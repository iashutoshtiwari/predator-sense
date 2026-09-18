"""Daemon-only temperature sources. No subprocesses, widgets, or sampling timers."""

from dataclasses import replace
import importlib
from pathlib import Path
import time

from service.telemetry_model import Availability, Reading, observed


def failed(name, source, error, *, status=None):
    if status is None:
        status = (Availability.PERMISSION_DENIED if isinstance(error, PermissionError) else
                  Availability.UNAVAILABLE if isinstance(error, (FileNotFoundError, ImportError)) else
                  Availability.SENSOR_FAILED)
    return Reading(name, status=status, source=source, sampled_at=time.monotonic(), error=str(error)[:240])


class CoretempSensor:
    def __init__(self, root=Path("/sys/class/hwmon")):
        self.root = Path(root)

    def read(self):
        """Prefer Package id 0; otherwise the hottest readable coretemp input.

        Rediscovery handles hwmon renumbering after resume/module reload. Missing
        labels are allowed in the fallback. A broken input cannot hide a good one.
        """
        preferred, fallback, errors = [], [], []
        try:
            devices = sorted(self.root.iterdir())
        except OSError as exc:
            return failed("cpu_temp_c", "coretemp", exc)
        for device in devices:
            if not device.name.startswith("hwmon"):
                continue
            try:
                if (device / "name").read_text().strip() != "coretemp":
                    continue
            except OSError as exc:
                errors.append(exc)
                continue
            for path in sorted(device.glob("temp*_input")):
                try:
                    value = int(path.read_text().strip()) / 1000
                    if not 0 <= value <= 125:
                        raise ValueError("Implausible coretemp temperature")
                    try:
                        label = path.with_name(path.name.replace("_input", "_label")).read_text().strip()
                    except OSError:
                        label = ""
                    reading = observed("cpu_temp_c", value, f"coretemp:{path}:{label or 'unlabelled'}")
                    (preferred if label == "Package id 0" else fallback).append(reading)
                except (OSError, ValueError, OverflowError) as exc:
                    errors.append(exc)
        if preferred or fallback:
            return max(preferred or fallback, key=lambda r: r.value)
        error = next((e for e in errors if isinstance(e, PermissionError)), None)
        error = error or (errors[0] if errors else FileNotFoundError("No coretemp input"))
        return failed("cpu_temp_c", "coretemp", error)


class NvmlSensor:
    """Official nvidia-ml-py binding, initialized lazily inside its dedicated worker.

    One handle, one initialization, no nvidia-smi fallback. Failures invalidate the
    handle; retry after five seconds without logging each failed sample.
    """
    def __init__(self, *, loader=importlib.import_module, clock=time.monotonic):
        self.loader, self.clock = loader, clock
        self.module = None
        self.initialized = False
        self.handle = None
        self.retry_at = 0.0
        self.last_failure = None

    def close(self):
        if self.initialized:
            self.initialized = False
            try:
                self.module.nvmlShutdown()
            except Exception:
                pass
        self.handle = None

    def read(self):
        if self.clock() < self.retry_at:
            return replace(self.last_failure, sampled_at=time.monotonic())
        try:
            if self.module is None:
                self.module = self.loader("pynvml")
            nvml = self.module
            if not self.initialized:
                nvml.nvmlInit()
                self.initialized = True
            if self.handle is None:
                for index in range(nvml.nvmlDeviceGetCount()):
                    handle = nvml.nvmlDeviceGetHandleByIndex(index)
                    name = nvml.nvmlDeviceGetName(handle)
                    if isinstance(name, bytes):
                        name = name.decode("utf-8", errors="replace")
                    if name in ("GeForce GTX 1050 Ti", "NVIDIA GeForce GTX 1050 Ti"):
                        self.handle = handle
                        break
                if self.handle is None:
                    raise FileNotFoundError("GTX 1050 Ti is unavailable")
            value = nvml.nvmlDeviceGetTemperature(self.handle, nvml.NVML_TEMPERATURE_GPU)
            return observed("gpu_temp_c", value, "NVML:GeForce GTX 1050 Ti")
        except Exception as exc:
            status = None
            code = getattr(exc, "value", None)
            if self.module is not None and code is not None:
                if code == getattr(self.module, "NVML_ERROR_NO_PERMISSION", object()):
                    status = Availability.PERMISSION_DENIED
                elif any(code == getattr(self.module, name, object()) for name in (
                    "NVML_ERROR_UNINITIALIZED", "NVML_ERROR_NOT_SUPPORTED", "NVML_ERROR_NOT_FOUND",
                    "NVML_ERROR_DRIVER_NOT_LOADED", "NVML_ERROR_LIBRARY_NOT_FOUND", "NVML_ERROR_GPU_IS_LOST",
                    "NVML_ERROR_NOT_READY", "NVML_ERROR_GPU_NOT_FOUND", "NVML_ERROR_NO_DATA",
                )):
                    status = Availability.UNAVAILABLE
            self.last_failure = failed("gpu_temp_c", "NVML:GeForce GTX 1050 Ti", exc, status=status)
            self.retry_at = self.clock() + 5.0
            self.close()
            return self.last_failure
