#!/usr/bin/env python3
"""Reapply the saved CoolBoost preference through the guarded hardware backend."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from core.env_checks import ensure_ec_access, run_env_checks
from core.errors import ErrorCode, HardwareError
from core.hardware import G3572EcBackend
from core.logger import get_logger
from core.state import STATE_FILE, load_coolboost_state

logger = get_logger(__name__)
SLEEP_SECONDS = 15


def read_state():
    return load_coolboost_state(STATE_FILE)


def apply_coolboost(backend: G3572EcBackend, enabled: bool):
    current = backend.get_coolboost()
    if current is None:
        raise HardwareError(ErrorCode.MALFORMED_READ, "CoolBoost state unknown; automatic reapplication skipped")
    if current != enabled:
        backend.set_coolboost(enabled)


def main():
    if not run_env_checks() or not ensure_ec_access():
        raise SystemExit(1)
    backend = G3572EcBackend()
    status = backend.probe()
    if not status.writable:
        logger.error("CoolBoost backend unavailable: %s", status.error)
        raise SystemExit(1)
    while True:
        try:
            apply_coolboost(backend, read_state())
        except HardwareError as exc:
            logger.error("CoolBoost reapplication failed [%s]: %s", exc.code.value, exc)
        time.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    main()
