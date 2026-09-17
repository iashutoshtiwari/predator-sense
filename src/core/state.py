"""Shared, atomically persisted CoolBoost preference (compatible JSON schema)."""

from __future__ import annotations

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
