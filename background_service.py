#!/usr/bin/env python3
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from core.hardware import ec_read, ec_write, ensure_ec_access

STATE_FILE = "/var/lib/predator-sense/state.json"
COOL_BOOST_CONTROL = 0x10
COOL_BOOST_ON = 0x01
COOL_BOOST_OFF = 0x00
SLEEP_SECONDS = 15


def read_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return bool(data.get("coolboost_enabled", False))
    except (OSError, json.JSONDecodeError):
        return False


def apply_coolboost(enabled):
    target = COOL_BOOST_ON if enabled else COOL_BOOST_OFF
    current = ec_read(COOL_BOOST_CONTROL)
    if current != target:
        ec_write(COOL_BOOST_CONTROL, target)


def main():
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    if not ensure_ec_access():
        raise SystemExit(1)

    while True:
        try:
            apply_coolboost(read_state())
        except OSError as exc:
            print("CoolBoost service I/O error:", exc)
        time.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    main()
