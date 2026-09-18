#!/usr/bin/env python3
"""Observe cached G3-572 fan telemetry; never activate a daemon or change hardware."""

import argparse
import asyncio
import csv
from datetime import datetime, timezone
import math
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dbus_next import BusType, Message, MessageFlag, MessageType
from dbus_next.aio import MessageBus

from service.protocol import BUS_NAME, INTERFACE, OBJECT_PATH
from service.telemetry_model import TelemetrySnapshot


async def call(bus, destination, member, *, identity=False):
    if identity:
        message = Message(
            destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus", member="GetNameOwner", signature="s", body=[BUS_NAME],
            flags=MessageFlag.NO_AUTOSTART,
        )
    else:
        if member not in ("GetHardwareIdentity", "GetTelemetrySnapshot") or not destination.startswith(":"):
            raise ValueError("Only cached/identity reads to an existing daemon are permitted")
        message = Message(
            destination=destination, path=OBJECT_PATH, interface=INTERFACE, member=member,
            flags=MessageFlag.NO_AUTOSTART,
        )
    reply = await asyncio.wait_for(bus.call(message), 4)
    if reply.message_type == MessageType.ERROR:
        raise RuntimeError(f"{reply.error_name}: {reply.body}")
    expected = "s" if identity or member == "GetTelemetrySnapshot" else "ssbb"
    if reply.signature != expected:
        raise ValueError("Incompatible daemon response; update the matching daemon package")
    return reply.body


def row(snapshot, label, *, now=None):
    snapshot = snapshot.aged(time.monotonic() if now is None else now)
    values = [datetime.fromtimestamp(snapshot.timestamp, timezone.utc).isoformat(), snapshot.epoch,
              snapshot.sequence, label]
    for name in ("cpu_fan_rpm", "gpu_fan_rpm"):
        reading = snapshot.reading(name)
        value = snapshot.value(name)
        values.extend(("Unavailable" if value is None else value, reading.status.value, reading.error))
    for name in ("cpu_mode", "gpu_mode", "coolboost", "cpu_manual_percent", "gpu_manual_percent",
                 "cpu_temp_c", "gpu_temp_c"):
        value = snapshot.value(name)
        values.append("Unavailable" if value is None else value)
    return values


async def sample(bus, args, *, output=sys.stdout, diagnostics=sys.stderr):
    # Resolve once and pin the unique owner. A stopped/restarted service cannot
    # trigger activation (and thus cannot restore saved CoolBoost through us).
    owner = (await call(bus, "", "GetNameOwner", identity=True))[0]
    product, bios, supported, _tested = await call(bus, owner, "GetHardwareIdentity")
    if product != "Predator G3-572" or supported is not True:
        raise RuntimeError("Validation is restricted to Predator G3-572")
    print(f"{product}, BIOS {bios}; tested reference V1.22. Candidate RPM, physically unverified.", file=diagnostics)
    print("CPU 0x13 / GPU 0x15: little-endian words; no smoothing. Change modes separately in the GUI.",
          file=diagnostics)
    writer = csv.writer(output)
    writer.writerow(("sample_timestamp_utc", "daemon_epoch", "sequence", "label",
                     "cpu_candidate_rpm_0x13", "cpu_status", "cpu_error",
                     "gpu_candidate_rpm_0x15", "gpu_status", "gpu_error",
                     "cpu_mode", "gpu_mode", "coolboost", "cpu_manual_percent", "gpu_manual_percent",
                     "cpu_temp_c", "gpu_temp_c"))
    deadline = time.monotonic()
    for index in range(args.samples):
        payload = (await call(bus, owner, "GetTelemetrySnapshot"))[0]
        snapshot = TelemetrySnapshot.from_json(payload)
        writer.writerow(row(snapshot, args.label))
        output.flush()
        if index + 1 < args.samples:
            now = time.monotonic()
            deadline += max(1, math.floor((now - deadline) / args.interval) + 1) * args.interval
            await asyncio.sleep(max(0, deadline - time.monotonic()))


def build_parser():
    parser = argparse.ArgumentParser(
        description="Read-only G3-572 fan validation via an already-running daemon; never activates it.",
        epilog="Compare separate GUI settings: Auto, Auto + CoolBoost, Manual ~30%, 50%, ~70%, Turbo. "
               "Allow each setting to settle; --label annotates output only and never applies a setting.",
    )
    parser.add_argument("--samples", type=int, default=60, help="Number of samples, 1..3600 (default 60)")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds per sample, 1..60 (default 1 Hz)")
    parser.add_argument("--label", default="observation", help="CSV annotation, e.g. manual-50; no hardware effect")
    return parser


async def run(args):
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        await sample(bus, args)
    finally:
        bus.disconnect()
        await bus.wait_for_disconnect()


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not 1 <= args.samples <= 3600 or not math.isfinite(args.interval) or not 1 <= args.interval <= 60:
        parser.error("samples must be 1..3600 and interval must be 1..60 seconds")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except (OSError, TimeoutError, RuntimeError, ValueError) as exc:
        print(f"Validation stopped: {exc}. Requires an already-running matching daemon; no activation attempted.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
