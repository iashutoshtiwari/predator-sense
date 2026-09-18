"""Daemon cooling preferences, strict validation and durable atomic replacement."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import tempfile

STATE_FILE = "/var/lib/predator-sense/state.json"


def load_coolboost_state(path: str | Path = STATE_FILE) -> bool:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return isinstance(data, dict) and data.get("coolboost_enabled") is True
    except (OSError, ValueError):
        return False


def save_coolboost_state(enabled: bool, path: str | Path = STATE_FILE) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump({"coolboost_enabled": enabled}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class CoolingState:
    cpu_mode: str = "auto"
    gpu_mode: str = "auto"
    cpu_manual_percent: int | None = None
    gpu_manual_percent: int | None = None
    coolboost_enabled: bool = False

    def __post_init__(self):
        if type(self.coolboost_enabled) is not bool:
            raise ValueError("CoolBoost must be boolean")
        for channel in ("cpu", "gpu"):
            mode = getattr(self, f"{channel}_mode")
            percent = getattr(self, f"{channel}_manual_percent")
            if mode not in ("auto", "manual", "turbo"):
                raise ValueError("Invalid requested mode")
            if mode == "manual":
                if type(percent) is not int or not 0 <= percent <= 100:
                    raise ValueError("Manual mode needs an integer percentage")
            elif percent is not None:
                raise ValueError("Non-manual modes must not contain a manual setting")


def load_cooling_state(path=STATE_FILE):
    """Legacy CoolBoost-only files migrate to explicit Auto; invalid files raise."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Cooling state must be an object")
    if set(data) == {"coolboost_enabled"}:
        return CoolingState(coolboost_enabled=data["coolboost_enabled"])
    if type(data.get("version")) is not int or data.pop("version") != 1:
        raise ValueError("Unsupported cooling-state version")
    if set(data) != set(CoolingState.__dataclass_fields__):
        raise ValueError("Incomplete or unknown cooling settings")
    return CoolingState(**data)


def save_cooling_state(state, path=STATE_FILE):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump({"version": 1, **asdict(state)}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
