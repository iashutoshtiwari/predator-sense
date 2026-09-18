"""Immutable, versioned telemetry contract shared by daemon and unprivileged clients."""

from dataclasses import asdict, dataclass, replace
from enum import StrEnum
import json
import math
import time

from core.profiles import FAN_RPM_MAX

HISTORY_LIMIT = 120
INTERVAL = 1.0
STALE_AFTER = 2.5
FIELDS = (
    "cpu_temp_c", "gpu_temp_c", "cpu_fan_rpm", "gpu_fan_rpm", "cpu_mode", "gpu_mode",
    "coolboost", "cpu_manual_percent", "gpu_manual_percent",
)
EC_FIELDS = FIELDS[2:]


class Availability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    PERMISSION_DENIED = "permission_denied"
    BACKEND_DISCONNECTED = "backend_disconnected"
    SENSOR_FAILED = "sensor_failed"
    STALE = "stale"


@dataclass(frozen=True)
class Reading:
    name: str
    value: float | int | bool | str | None = None
    status: Availability = Availability.UNAVAILABLE
    source: str = ""
    sampled_at: float | None = None  # monotonic seconds, same Linux host as the client
    error: str = ""

    def __post_init__(self):
        if self.name not in FIELDS or not isinstance(self.status, Availability):
            raise ValueError("Invalid sensor name/status")
        if not isinstance(self.source, str) or not isinstance(self.error, str):
            raise ValueError("Invalid sensor metadata")
        if self.sampled_at is not None and not finite_number(self.sampled_at):
            raise ValueError("Invalid sensor timestamp")
        value = self.value
        if value is None:
            if self.status == Availability.AVAILABLE:
                raise ValueError("Available sensors must have a value")
            return
        if self.status not in (Availability.AVAILABLE, Availability.STALE) or self.sampled_at is None:
            raise ValueError("Only available/stale readings may retain a dated value")
        if self.name.endswith("temp_c"):
            valid = finite_number(value) and 0 <= value <= 125
        elif self.name.endswith("rpm"):
            valid = type(value) is int and 0 <= value <= FAN_RPM_MAX
        elif self.name.endswith("percent"):
            valid = type(value) is int and 0 <= value <= 100
        elif self.name.endswith("mode"):
            valid = type(value) is str and value in ("firmware_auto", "auto", "manual", "turbo")
        else:
            valid = type(value) is bool
        if not valid:
            raise ValueError("Invalid sensor value")

    def aged(self, now):
        if self.sampled_at is not None and now - self.sampled_at > STALE_AFTER:
            return replace(self, status=Availability.STALE, error="No recent sensor sample")
        return self


def finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def observed(name, value, source, *, now=None):
    return Reading(
        name, value, Availability.AVAILABLE if value is not None else Availability.UNAVAILABLE,
        source, time.monotonic() if now is None else now,
        "" if value is not None else "Sensor value is unavailable or unrecognized",
    )


@dataclass(frozen=True)
class TelemetrySnapshot:
    timestamp: float  # Unix seconds, for display
    monotonic_timestamp: float  # sample publication time, for freshness/scheduling
    sequence: int
    epoch: str  # changes on daemon restart; sequence is only unique within an epoch
    readings: tuple[Reading, ...]
    ready: bool = False
    code: str = "Starting"
    message: str = "Waiting for the first telemetry sample"

    def __post_init__(self):
        if not finite_number(self.timestamp) or not finite_number(self.monotonic_timestamp):
            raise ValueError("Invalid snapshot timestamp")
        if type(self.sequence) is not int or self.sequence < 0 or not isinstance(self.epoch, str):
            raise ValueError("Invalid snapshot identity")
        if type(self.ready) is not bool or not isinstance(self.code, str) or not isinstance(self.message, str):
            raise ValueError("Invalid service status")
        if (type(self.readings) is not tuple or not all(isinstance(r, Reading) for r in self.readings)
                or tuple(r.name for r in self.readings) != FIELDS):
            raise ValueError("Snapshot must contain each sensor exactly once in contract order")

    def reading(self, name):
        return self.readings[FIELDS.index(name)]

    def value(self, name):
        """Only fresh values: stale history remains explicitly accessible in reading()."""
        reading = self.reading(name)
        return reading.value if reading.status == Availability.AVAILABLE else None

    cpu_temp_c = property(lambda self: self.value("cpu_temp_c"))
    gpu_temp_c = property(lambda self: self.value("gpu_temp_c"))
    cpu_fan_rpm = property(lambda self: self.value("cpu_fan_rpm"))
    gpu_fan_rpm = property(lambda self: self.value("gpu_fan_rpm"))
    cpu_mode = property(lambda self: self.value("cpu_mode"))
    gpu_mode = property(lambda self: self.value("gpu_mode"))
    coolboost = property(lambda self: self.value("coolboost"))
    cpu_manual_percent = property(lambda self: self.value("cpu_manual_percent"))
    gpu_manual_percent = property(lambda self: self.value("gpu_manual_percent"))

    def aged(self, now):
        readings = tuple(r.aged(now) for r in self.readings)
        stale_ec = any(r.status == Availability.STALE for r in readings if r.name in EC_FIELDS)
        return replace(
            self, readings=readings, ready=self.ready and not stale_ec,
            code="Stale" if stale_ec else self.code,
            message="Waiting for fresh fan state" if stale_ec else self.message,
        )

    def to_json(self):
        return json.dumps({"version": 1, **asdict(self)}, separators=(",", ":"), allow_nan=False)

    @classmethod
    def from_json(cls, payload):
        if not isinstance(payload, str) or len(payload) > 32768:
            raise ValueError("Invalid telemetry payload")
        try:
            data = json.loads(payload)
            version = data.pop("version")
            if type(version) is not int or version != 1:
                raise ValueError("Unsupported telemetry version")
            data["readings"] = tuple(
                Reading(**{**r, "status": Availability(r["status"])}) for r in data["readings"]
            )
            return cls(**data)
        except (TypeError, KeyError, AttributeError) as exc:
            raise ValueError("Malformed telemetry snapshot") from exc

    @classmethod
    def empty(cls, *, status=Availability.UNAVAILABLE, code="Starting", message="Waiting for telemetry", epoch=""):
        now = time.monotonic()
        return cls(time.time(), now, 0, epoch, tuple(Reading(n, status=status) for n in FIELDS), False, code, message)
