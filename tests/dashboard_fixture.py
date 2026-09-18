"""Explicitly simulated presentation data. Never imported by production code."""

from dataclasses import replace
import math
import time

from service.telemetry_model import FIELDS, TelemetrySnapshot, observed


def samples(count=60):
    now = time.monotonic()
    history = []
    for index in range(count):
        timestamp = now - count + 1 + index
        values = {
            "cpu_temp_c": 54 + 4 * math.sin(index / 6), "gpu_temp_c": 47 + 3 * math.sin(index / 9),
            "cpu_fan_rpm": int(2850 + 180 * math.sin(index / 8)),
            "gpu_fan_rpm": int(2500 + 120 * math.sin(index / 7)),
            "cpu_mode": "auto", "gpu_mode": "auto", "coolboost": True,
            "cpu_manual_percent": 50, "gpu_manual_percent": 50,
        }
        history.append(TelemetrySnapshot(
            time.time() - count + 1 + index, timestamp, index + 1, "simulated-ui-fixture",
            tuple(observed(name, values[name], "SIMULATED TEST DATA", now=timestamp) for name in FIELDS),
            True, "", "",
        ))
    return history


class DashboardTransport:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = []
        self.error = None

    def call(self, method, args, callback):
        self.calls.append((method, args))
        if self.error:
            callback(None, self.error, "Daemon unavailable. Start predator-sensed.service, then retry.")
        elif method == "GetHardwareIdentity":
            callback(["Predator G3-572", "V1.22", True, True], "", "")
        elif method == "GetTelemetrySnapshot":
            callback([self.snapshot.to_json()], "", "")
        else:
            callback([], "", "")


def missing(snapshot, *fields):
    return replace(snapshot, readings=tuple(
        observed(r.name, None, "SIMULATED unavailable sensor") if r.name in fields else r
        for r in snapshot.readings
    ))
