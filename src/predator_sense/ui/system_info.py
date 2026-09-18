"""Unprivileged system hardware detection for CPU and GPU models.

Reads system attributes (/proc/cpuinfo, NVML, lspci) without hardware
control or privileged EC access.
"""

from __future__ import annotations

import re
import subprocess

_CACHED_CPU: str | None = None
_CACHED_GPU: str | None = None


def get_cpu_model() -> str:
    """Return a clean CPU model string from /proc/cpuinfo, or 'Processor' as fallback."""
    global _CACHED_CPU
    if _CACHED_CPU is not None:
        return _CACHED_CPU

    model = "Processor"
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("model name"):
                    raw = line.split(":", 1)[1].strip()
                    cleaned = re.sub(r"\s*(?:\(R\)|\(TM\)|CPU\b|@\s*[\d.]+\s*GHz)\s*", " ", raw)
                    cleaned = re.sub(r"\s+", " ", cleaned).strip()
                    if cleaned:
                        model = cleaned
                    break
    except Exception:
        pass

    _CACHED_CPU = model
    return _CACHED_CPU


def _detect_gpu_from_nvml() -> str | None:
    """Attempt detection via official NVML library."""
    try:
        import pynvml
        pynvml.nvmlInit()
        try:
            count = pynvml.nvmlDeviceGetCount()
            for i in range(count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                name = pynvml.nvmlDeviceGetName(handle)
                if isinstance(name, bytes):
                    name = name.decode("utf-8", errors="replace")
                name = name.strip()
                if name:
                    return name
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        pass
    return None


def _detect_gpu_from_lspci() -> str | None:
    """Attempt detection via lspci output."""
    try:
        out = subprocess.check_output(["lspci"], text=True, timeout=1.0)
        discrete_names = []
        fallback_names = []
        for line in out.strip().splitlines():
            line_upper = line.upper()
            if "VGA COMPATIBLE CONTROLLER" in line_upper or "3D CONTROLLER" in line_upper:
                match = re.search(r"\[([^\]]+)\]", line)
                if match:
                    dev_name = match.group(1).strip()
                    if "NVIDIA" in line_upper and not dev_name.upper().startswith("NVIDIA"):
                        dev_name = f"NVIDIA {dev_name}"
                else:
                    parts = line.split(":", 2)
                    dev_name = parts[2].strip() if len(parts) >= 3 else line.strip()

                if "NVIDIA" in line_upper or "AMD" in line_upper or "3D CONTROLLER" in line_upper:
                    discrete_names.append(dev_name)
                else:
                    fallback_names.append(dev_name)

        if discrete_names:
            return discrete_names[0]
        if fallback_names:
            return fallback_names[0]
    except Exception:
        pass
    return None


def get_gpu_model() -> str:
    """Return a clean GPU model string, preferring NVML then lspci, or 'Graphics'."""
    global _CACHED_GPU
    if _CACHED_GPU is not None:
        return _CACHED_GPU

    gpu = _detect_gpu_from_nvml()
    if not gpu:
        gpu = _detect_gpu_from_lspci()

    _CACHED_GPU = gpu or "Graphics"
    return _CACHED_GPU
