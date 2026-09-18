"""Read-only installation verification. Never activates the daemon or writes EC."""
import asyncio
import importlib.util
from pathlib import Path
import subprocess
import time

from dbus_next import BusType, Message, MessageFlag, MessageType
from dbus_next.aio import MessageBus

from predator_sense.service.protocol import BUS_NAME, INTERFACE, OBJECT_PATH
from predator_sense.service.telemetry_model import TelemetrySnapshot


def command(*args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=5, check=False)
        return result.returncode == 0, result.stdout.strip() or result.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


async def cached_snapshot():
    bus = await asyncio.wait_for(MessageBus(bus_type=BusType.SYSTEM).connect(), 5)
    try:
        owner = await asyncio.wait_for(bus.call(Message(
            destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus", member="GetNameOwner",
            signature="s", body=[BUS_NAME], flags=MessageFlag.NO_AUTOSTART,
        )), 5)
        if owner.message_type == MessageType.ERROR or owner.signature != "s":
            raise RuntimeError("Daemon has no bus owner; enable/start predator-sensed.service")
        reply = await asyncio.wait_for(bus.call(Message(
            destination=owner.body[0], path=OBJECT_PATH, interface=INTERFACE,
            member="GetTelemetrySnapshot", flags=MessageFlag.NO_AUTOSTART,
        )), 5)
        if reply.message_type == MessageType.ERROR or reply.signature != "s":
            raise RuntimeError("Cannot read cached telemetry from existing daemon")
        return TelemetrySnapshot.from_json(reply.body[0]).aged(time.monotonic())
    finally:
        bus.disconnect()
        await bus.wait_for_disconnect()


def main():
    failures = []
    def report(name, ok, detail, *, optional=False):
        print(f"{'OK' if ok else 'WARN' if optional else 'FAIL'}  {name}: {detail}")
        if not ok and not optional:
            failures.append(name)

    try:
        product = " ".join(Path("/sys/class/dmi/id/product_name").read_text().split())
        report("Hardware", product == "Predator G3-572", product)
    except OSError as exc:
        report("Hardware", False, str(exc))
    report("Service installed", Path("/usr/lib/systemd/system/predator-sensed.service").is_file(),
           "/usr/lib/systemd/system/predator-sensed.service")
    for state in ("is-active", "is-enabled"):
        ok, detail = command("systemctl", state, "predator-sensed.service")
        report(f"Service {state}", ok, detail)
    for module in ("PyQt6.QtWidgets", "PyQt6.QtDBus", "PyQt6.QtSvg", "dbus_next"):
        try:
            found = importlib.util.find_spec(module) is not None
        except ImportError:
            found = False
        report("GUI dependency", found, module)
    report("NVIDIA binding", importlib.util.find_spec("pynvml") is not None,
           "python-nvidia-ml-py (optional); runtime availability shown below", optional=True)
    try:
        enabled = Path("/sys/module/ec_sys/parameters/write_support").read_text().strip()
        report("ec_sys", enabled.lower() in ("y", "1"), f"write_support={enabled}")
    except OSError as exc:
        report("ec_sys", False, str(exc))
    try:
        exists = Path("/sys/kernel/debug/ec/ec0/io").stat()
        report("EC path", bool(exists), "/sys/kernel/debug/ec/ec0/io")
    except PermissionError:
        report("EC path", False, "debugfs is root-only; verify via daemon telemetry below", optional=True)
    except OSError as exc:
        report("EC path", False, str(exc))
    try:
        snapshot = asyncio.run(cached_snapshot())
        report("D-Bus / daemon", snapshot.ready, snapshot.message or snapshot.code or "ready")
        for field in ("cpu_temp_c", "gpu_temp_c", "cpu_fan_rpm", "gpu_fan_rpm"):
            reading = snapshot.reading(field)
            report(field, snapshot.value(field) is not None,
                   f"{snapshot.value(field)}; {reading.status.value}; {reading.source}; {reading.error}",
                   optional=field == "gpu_temp_c")
    except Exception as exc:
        report("D-Bus", False, str(exc))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
