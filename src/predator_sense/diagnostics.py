"""Read-only system diagnostics for Predator Sense feature parity and bug reporting.

Collects non-sensitive system identity, service state, EC access status,
and sensor sources. Avoids sensitive hostnames, user paths, or process tables.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import platform
import shutil
import subprocess
import sys

from dbus_next import BusType, Message, MessageFlag, MessageType
from dbus_next.aio import MessageBus

from predator_sense import __version__
from predator_sense.core.profiles import (
    BYTE_READ_REGISTERS,
    CONTROL_REGISTERS,
    COOLBOOST_REGISTER,
    EC_IO_FILE,
    EC_MODULE_PATH,
    MODE_REGISTERS,
    RPM_REGISTERS,
    WORD_READ_REGISTERS,
    FanChannel,
)
from predator_sense.service.protocol import BUS_NAME, INTERFACE, OBJECT_PATH
from predator_sense.service.telemetry_model import TelemetrySnapshot

EC_IO_PATH = Path(EC_IO_FILE)
DEFAULT_EC_REGISTERS = tuple(sorted(BYTE_READ_REGISTERS | WORD_READ_REGISTERS))


def run_cmd(cmd: list[str], timeout: int = 8) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        output = proc.stdout.strip() or proc.stderr.strip()
        return proc.returncode, output
    except FileNotFoundError:
        return 127, f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"command timed out after {timeout}s"


def read_file_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as exc:
        return f"<unavailable: {exc}>"


def get_os_release() -> str:
    path = Path("/etc/os-release")
    if not path.exists():
        return "Unknown Linux"
    lines = read_file_safe(path).splitlines()
    data = {}
    for line in lines:
        if "=" in line:
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip().strip('"')
    return data.get("PRETTY_NAME") or data.get("NAME", "Linux")


async def query_daemon_telemetry() -> tuple[dict[int, str], TelemetrySnapshot | None, str | None]:
    """Query the system daemon over D-Bus with NO_AUTOSTART."""
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    registers: dict[int, str] = {}
    snapshot: TelemetrySnapshot | None = None
    error: str | None = None

    try:
        # Check owner first without auto-activating
        owner_msg = Message(
            destination="org.freedesktop.DBus",
            path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus",
            member="GetNameOwner",
            signature="s",
            body=[BUS_NAME],
            flags=MessageFlag.NO_AUTOSTART,
        )
        owner_reply = await asyncio.wait_for(bus.call(owner_msg), 4)
        if owner_reply.message_type == MessageType.ERROR:
            return registers, None, "Daemon not running (no D-Bus owner)"

        # Read snapshot
        snap_msg = Message(
            destination=BUS_NAME,
            path=OBJECT_PATH,
            interface=INTERFACE,
            member="GetTelemetrySnapshot",
            flags=MessageFlag.NO_AUTOSTART,
        )
        snap_reply = await asyncio.wait_for(bus.call(snap_msg), 4)
        if snap_reply.message_type == MessageType.METHOD_RETURN and snap_reply.signature == "s":
            snapshot = TelemetrySnapshot.from_json(snap_reply.body[0])

        # Read registers via status methods
        for method, reg_list in (
            ("GetFanState", [MODE_REGISTERS[FanChannel.CPU], MODE_REGISTERS[FanChannel.GPU],
                             CONTROL_REGISTERS[FanChannel.CPU], CONTROL_REGISTERS[FanChannel.GPU]]),
            ("GetCoolBoost", [COOLBOOST_REGISTER]),
            ("GetFanSpeeds", [RPM_REGISTERS[FanChannel.CPU], RPM_REGISTERS[FanChannel.GPU]]),
        ):
            msg = Message(
                destination=BUS_NAME,
                path=OBJECT_PATH,
                interface=INTERFACE,
                member=method,
                flags=MessageFlag.NO_AUTOSTART,
            )
            reply = await asyncio.wait_for(bus.call(msg), 4)
            if reply.message_type == MessageType.ERROR:
                for r in reg_list:
                    registers[r] = f"unavailable [{reply.error_name}]"
            else:
                for r, val in zip(reg_list, reply.body):
                    registers[r] = "unavailable" if val == -1 else str(val)

    except Exception as exc:
        error = str(exc)
    finally:
        bus.disconnect()
        await bus.wait_for_disconnect()

    return registers, snapshot, error


def collect_diagnostics_report() -> list[str]:
    lines: list[str] = []

    def section(title: str):
        lines.append("")
        lines.append(f"## {title}")

    lines.append("# Predator Sense Diagnostics Report")
    lines.append(f"timestamp: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"app_version: {__version__}")

    section("System and Hardware")
    lines.append(f"distro: {get_os_release()}")
    lines.append(f"kernel: {platform.system()} {platform.release()} ({platform.machine()})")
    lines.append(f"product_name: {read_file_safe(Path('/sys/class/dmi/id/product_name'))}")
    lines.append(f"bios_version: {read_file_safe(Path('/sys/class/dmi/id/bios_version'))}")

    section("Service Status")
    for check in ("is-installed", "is-active", "is-enabled"):
        if check == "is-installed":
            installed = Path("/usr/lib/systemd/system/predator-sensed.service").is_file()
            lines.append(f"service_installed: {installed}")
        else:
            code, out = run_cmd(["systemctl", check, "predator-sensed.service"])
            lines.append(f"service_{check}: {out if code == 0 else f'no ({out})'}")

    section("EC and Kernel Modules")
    lines.append(f"ec_io_path: {EC_IO_PATH}")
    lines.append(f"ec_io_exists: {EC_IO_PATH.exists()}")
    try:
        with open(EC_IO_PATH, "rb") as _:
            lines.append("ec_io_accessible: true")
    except OSError as exc:
        lines.append(f"ec_io_accessible: false ({exc})")

    param_path = Path(EC_MODULE_PATH) / "parameters" / "write_support"
    lines.append(f"ec_sys_write_support: {read_file_safe(param_path)}")

    code, out = run_cmd(["sh", "-c", "lsmod | grep ec_sys || true"])
    lines.append(f"lsmod_ec_sys: {out or 'not loaded'}")

    section("Graphics Devices")
    code, out = run_cmd(["sh", "-c", "lspci -nn | grep -Ei 'vga|3d|display' || true"])
    lines.append(out or "None found")

    section("Daemon Telemetry & Sensors")
    registers, snapshot, daemon_err = asyncio.run(query_daemon_telemetry())
    if daemon_err:
        lines.append(f"daemon_query: {daemon_err}")
    elif snapshot is not None:
        lines.append(f"daemon_ready: {snapshot.ready}")
        lines.append(f"daemon_epoch: {snapshot.epoch}")
        lines.append(f"daemon_status_message: {snapshot.message or snapshot.code or 'OK'}")

        for field in ("cpu_temp_c", "gpu_temp_c", "cpu_fan_rpm", "gpu_fan_rpm"):
            r = snapshot.reading(field)
            lines.append(f"sensor_{field}: value={snapshot.value(field)}; status={r.status.value}; source={r.source}; error={r.error or 'none'}")
    else:
        lines.append("daemon_query: No snapshot received")

    section("EC Register Observations")
    if registers:
        for reg in DEFAULT_EC_REGISTERS:
            val = registers.get(reg, "not queried")
            lines.append(f"0x{reg:02X}: {val}")
    else:
        lines.append("Registers unavailable (daemon offline or inaccessible)")

    return lines


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="predator-sense-diagnostics",
        description="Collect non-sensitive system and hardware diagnostics for Predator Sense.",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="",
        help="Path to save the report (default: predator-diagnostics-YYYYMMDD-HHMMSS.txt)",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print the report to stdout instead of writing to a file",
    )
    args = parser.parse_args()

    report_lines = collect_diagnostics_report()
    content = "\n".join(report_lines) + "\n"

    if args.stdout or args.output == "-":
        sys.stdout.write(content)
        return 0

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = Path(args.output or f"predator-diagnostics-{timestamp}.txt").resolve()
    try:
        out_path.write_text(content, encoding="utf-8")
        print(f"Diagnostics written to: {out_path}")
        return 0
    except OSError as exc:
        print(f"Error writing diagnostics file {out_path}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
