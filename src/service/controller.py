"""Synchronous daemon operations; authorization is enforced by the bus adapter."""

from pathlib import Path
from threading import RLock

from core.errors import HardwareError
from core.profiles import FanChannel, FanMode, RPM_REGISTERS
from core.state import STATE_FILE, save_coolboost_state
from service.protocol import ServiceError
from service.sensors import failed
from service.telemetry import SampleResult
from service.telemetry_model import Availability, EC_FIELDS, TelemetrySnapshot, observed


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

    def restore_coolboost(self):
        """Restore only a valid existing preference once, not a 15-second loop."""
        import json

        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or type(data.get("coolboost_enabled")) is not bool:
            return
        current = self.backend.get_coolboost()
        if current is not None and current != data["coolboost_enabled"]:
            self.backend.set_coolboost(data["coolboost_enabled"])

    def telemetry_snapshot(self):
        return self.telemetry.latest if self.telemetry is not None else self.initial_snapshot

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
                    if self.starting:
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
            raise ServiceError(exc.code.value, str(exc)) from exc
        except OSError as exc:
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
            status = backend.probe()
            if status.error:
                return [False, status.error.code.value, str(status.error)]
            return [status.writable, "", self.startup_warning]
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
        if member == "SetCoolBoost":
            backend.set_coolboost(args[0])
            save_coolboost_state(args[0], self.state_file)
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
        return []
