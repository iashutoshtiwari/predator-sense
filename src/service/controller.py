"""Synchronous daemon operations; authorization is enforced by the bus adapter."""

from pathlib import Path

from core.errors import HardwareError
from core.profiles import FanChannel, FanMode
from core.state import STATE_FILE, save_coolboost_state
from service.protocol import ServiceError


def optional_int(value):
    return -1 if value is None else int(value)


def read_temperatures():
    """Read fixed sysfs sources only. No NVIDIA commands or caller-controlled paths."""
    cpu, gpu = [], []
    for hwmon in Path("/sys/class/hwmon").glob("hwmon*"):
        try:
            name = (hwmon / "name").read_text().strip()
        except OSError:
            continue
        target = cpu if name == "coretemp" else gpu if name in ("nvidia", "nouveau") else None
        if target is None:
            continue
        for source in hwmon.glob("temp*_input"):
            try:
                value = int(source.read_text().strip())
                if 0 <= value <= 125000:
                    target.append(value)
            except (OSError, ValueError):
                continue
    return [max(cpu, default=-1), max(gpu, default=-1)]


class Controller:
    def __init__(self, backend, *, state_file=STATE_FILE, temperatures=read_temperatures):
        self.backend = backend
        self.state_file = Path(state_file)
        self.temperatures = temperatures
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

    def invoke(self, member, args):
        try:
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
