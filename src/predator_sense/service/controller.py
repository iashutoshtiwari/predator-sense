"""Synchronous daemon operations; authorization is enforced by the bus adapter."""

from dataclasses import replace
from pathlib import Path
from threading import RLock
import time

from predator_sense.core.errors import HardwareError
from predator_sense.core.profiles import FanChannel, FanMode, RPM_REGISTERS
from predator_sense.core.state import STATE_FILE, CoolingState, load_cooling_state, save_cooling_state
from predator_sense.service.protocol import ServiceError
from predator_sense.service.sensors import failed
from predator_sense.service.telemetry import SampleResult
from predator_sense.service.telemetry_model import Availability, EC_FIELDS, TelemetrySnapshot, observed


def optional_int(value):
    return -1 if value is None else int(value)


class Controller:
    def __init__(self, backend, *, state_file=STATE_FILE, temperatures=None):
        self.backend = backend
        self.state_file = Path(state_file)
        self.temperatures = temperatures
        self.telemetry = None
        self.operation_lock = RLock()
        self.initial_snapshot = TelemetrySnapshot.empty()
        self.starting = True
        self.startup_warning = ""
        self.desired = CoolingState()
        self.lifecycle = ""
        self.degraded = ""
        self.recovered_at = 0.0
        self.state_loaded = False
        self.restore_failed = False

    def _apply(self, state):
        for channel in FanChannel:
            mode = getattr(state, f"{channel.value}_mode")
            if mode == "manual":
                self.backend.set_manual_speed(channel, getattr(state, f"{channel.value}_manual_percent"))
            else:
                self.backend.set_fan_mode(channel, FanMode(mode))
        self.backend.set_coolboost(state.coolboost_enabled)

    def safe_fallback(self):
        """Independent best-effort channels; never overwrite the desired settings."""
        errors = []
        for channel in FanChannel:
            try:
                self.backend.set_fan_mode(channel, FanMode.AUTO)
            except Exception as exc:
                errors.append(str(exc))
        try:
            self.backend.set_coolboost(False)
        except Exception as exc:
            errors.append(str(exc))
        return "; ".join(errors)

    def recover(self, *, startup=False):
        with self.operation_lock:
            self.restore_failed = False
            if self.lifecycle in ("Stopping", "Suspended"):
                raise ServiceError(self.lifecycle, "Recovery deferred")
            status = self.backend.probe()
            if status.error:
                raise status.error
            if not status.writable:
                raise ServiceError("BackendUnavailable", "EC is not writable")
            # Read all control registers before restoring anything.
            self.backend.get_cpu_fan_mode()
            self.backend.get_gpu_fan_mode()
            self.backend.get_cpu_manual_speed()
            self.backend.get_gpu_manual_speed()
            self.backend.get_coolboost()
            if startup or not self.state_loaded:
                try:
                    self.desired = load_cooling_state(self.state_file)
                except FileNotFoundError:
                    self.desired = CoolingState()
                except (OSError, ValueError, TypeError) as exc:
                    self.desired = CoolingState()
                    self.startup_warning = f"Invalid saved state; using Auto and CoolBoost Off: {exc}"
                self.state_loaded = True
            try:
                self._apply(self.desired)
                save_cooling_state(self.desired, self.state_file)
                self.degraded = ""
                self.recovered_at = time.monotonic()
            except Exception as exc:
                self.restore_failed = True
                self.degraded = f"Cooling restore failed: {exc}; Auto fallback: {self.safe_fallback() or 'verified'}"
                raise
            finally:
                self.starting = False

    def suspend(self):
        with self.operation_lock:
            if self.state_loaded:
                save_cooling_state(self.desired, self.state_file)

    def shutdown(self):
        with self.operation_lock:
            self.lifecycle = "Stopping"
            return self.safe_fallback()

    def _requested_state(self, member, args):
        state = self.desired
        if member == "SetCoolBoost":
            return replace(state, coolboost_enabled=args[0])
        if member in ("SetGlobalAuto", "SetGlobalTurbo"):
            mode = "auto" if member == "SetGlobalAuto" else "turbo"
            return replace(state, cpu_mode=mode, gpu_mode=mode, cpu_manual_percent=None, gpu_manual_percent=None)
        channel = "cpu" if member.startswith("SetCpu") else "gpu"
        mode = "manual" if member.endswith("ManualSpeed") else args[0]
        percent = (args[0] if member.endswith("ManualSpeed") else
                   getattr(self.backend, f"get_{channel}_manual_speed")() if mode == "manual" else None)
        return replace(state, **{f"{channel}_mode": mode, f"{channel}_manual_percent": percent})

    def telemetry_snapshot(self):
        snapshot = self.telemetry.latest if self.telemetry is not None else self.initial_snapshot
        if self.lifecycle or self.starting:
            return replace(TelemetrySnapshot.empty(), code=self.lifecycle or "Starting",
                           message="Cooling service is recovering or suspended.")
        if self.degraded:
            return replace(snapshot, ready=False, code="Degraded", message=self.degraded)
        if self.recovered_at:
            old_ec = any(r.name in EC_FIELDS and (r.sampled_at is None or r.sampled_at < self.recovered_at)
                         for r in snapshot.readings)
            readings = tuple(
                replace(r, value=None, status=Availability.UNAVAILABLE, error="Waiting for a post-recovery sample")
                if r.sampled_at is None or r.sampled_at < self.recovered_at else r
                for r in snapshot.readings
            )
            snapshot = replace(snapshot, readings=readings)
            if old_ec:
                snapshot = replace(snapshot, ready=False, code="Recovering", message="Waiting for a fresh EC sample.")
        return snapshot

    def sample_ec(self):
        # A complete EC sample cannot interleave with a semantic mutation.
        with self.operation_lock:
            ready, code, message = self._invoke("GetStatus", [])
            readings = []
            methods = (
                "get_cpu_fan_rpm", "get_gpu_fan_rpm", "get_cpu_fan_mode", "get_gpu_fan_mode",
                "get_coolboost", "get_cpu_manual_speed", "get_gpu_manual_speed",
            )
            for name, method in zip(EC_FIELDS, methods):
                source = "G3-572 EC"
                if name.endswith("fan_rpm"):
                    channel = FanChannel(name.split("_")[0])
                    source = f"G3-572 EC:0x{RPM_REGISTERS[channel]:02X} little-endian word (candidate RPM)"
                try:
                    if self.starting or self.lifecycle:
                        reading = observed(name, None, source)
                    else:
                        value = getattr(self.backend, method)()
                        if isinstance(value, FanMode):
                            value = None if value == FanMode.UNKNOWN else value.value
                        reading = observed(name, value, source)
                except HardwareError as exc:
                    status = (Availability.PERMISSION_DENIED if exc.code.value == "permission_denied" else
                              Availability.UNAVAILABLE if exc.code.value in (
                                  "ec_unavailable", "identity_unavailable", "unsupported_hardware",
                                  "module_missing", "debugfs_unavailable",
                              ) else Availability.SENSOR_FAILED)
                    reading = failed(name, source, exc, status=status)
                except Exception as exc:
                    reading = failed(name, source, exc)
                readings.append(reading)
            return SampleResult(tuple(readings), ready, code, message)

    def invoke(self, member, args):
        # Cached reads must remain available during EC operations or authorization.
        if member == "GetTelemetrySnapshot":
            return [self.telemetry_snapshot().to_json()]
        if member == "GetTemperatures" and self.temperatures is None:
            snapshot = self.telemetry_snapshot()
            return [optional_int(None if value is None else round(value * 1000))
                    for value in (snapshot.cpu_temp_c, snapshot.gpu_temp_c)]
        try:
            with self.operation_lock:
                return self._invoke(member, args)
        except HardwareError as exc:
            if member.startswith("Set"):
                with self.operation_lock:
                    self.degraded = f"{exc}; Auto fallback: {self.safe_fallback() or 'verified'}"
            raise ServiceError(exc.code.value, str(exc)) from exc
        except OSError as exc:
            self.degraded = f"State persistence failed: {exc}"
            raise ServiceError(
                "PersistenceFailed", f"State could not be saved; hardware may have changed: {exc}"
            ) from exc

    def _invoke(self, member, args):
        backend = self.backend
        if member == "GetHardwareIdentity":
            identity = backend.get_identity()
            return [identity.product_name, identity.bios_version or "", identity.supported, identity.tested_bios]
        if member == "GetStatus":
            if self.starting:
                return [False, "Starting", "Hardware service is starting; waiting for EC preparation."]
            if self.lifecycle:
                return [False, self.lifecycle, "Hardware controls are temporarily unavailable."]
            if self.degraded:
                return [False, "Degraded", self.degraded]
            status = backend.probe()
            if status.error:
                return [False, status.error.code.value, str(status.error)]
            return [status.writable, "", self.startup_warning]
        if self.lifecycle:
            raise ServiceError(self.lifecycle, "Hardware controls are temporarily unavailable")
        if self.starting:
            raise ServiceError("Starting", "Hardware service is still starting; retry shortly")
        if member == "GetFanState":
            return [
                backend.get_cpu_fan_mode().value,
                backend.get_gpu_fan_mode().value,
                optional_int(backend.get_cpu_manual_speed()),
                optional_int(backend.get_gpu_manual_speed()),
            ]
        if member == "GetCoolBoost":
            return [optional_int(backend.get_coolboost())]
        if member == "GetFanSpeeds":
            return [optional_int(backend.get_cpu_fan_rpm()), optional_int(backend.get_gpu_fan_rpm())]
        if member == "GetTemperatures":
            return self.temperatures()
        requested = self._requested_state(member, args)
        if member == "SetCoolBoost":
            backend.set_coolboost(args[0])
        elif member in ("SetGlobalAuto", "SetGlobalTurbo"):
            mode = FanMode.AUTO if member == "SetGlobalAuto" else FanMode.TURBO
            backend.set_fan_mode(FanChannel.CPU, mode)
            backend.set_fan_mode(FanChannel.GPU, mode)
        elif member in ("SetCpuFanMode", "SetGpuFanMode"):
            channel = FanChannel.CPU if member == "SetCpuFanMode" else FanChannel.GPU
            backend.set_fan_mode(channel, FanMode(args[0]))
        elif member in ("SetCpuManualSpeed", "SetGpuManualSpeed"):
            channel = FanChannel.CPU if member == "SetCpuManualSpeed" else FanChannel.GPU
            backend.set_manual_speed(channel, args[0])
        else:
            raise ServiceError("UnknownMethod", "Unknown control method")
        save_cooling_state(requested, self.state_file)
        self.desired = requested
        self.state_loaded = True
        self.degraded = ""
        return []
