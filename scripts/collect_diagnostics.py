#!/usr/bin/env python3
"""Collect system diagnostics for Predator Sense feature development.

This script performs read-only checks and writes a timestamped report file.
It does not modify EC values, fan modes, clocks, or power settings.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import pathlib
import shutil
import subprocess
import sys
import time
from typing import Iterable

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from dbus_next import BusType, Message, MessageType
from dbus_next.aio import MessageBus

from service.protocol import BUS_NAME, INTERFACE, OBJECT_PATH
from core.profiles import (
    BYTE_READ_REGISTERS, CONTROL_REGISTERS, COOLBOOST_REGISTER, EC_IO_FILE,
    MODE_REGISTERS, RPM_REGISTERS, WORD_READ_REGISTERS, FanChannel,
)

EC_IO_PATH = pathlib.Path(EC_IO_FILE)
DEFAULT_EC_ADDRESSES = tuple(sorted(BYTE_READ_REGISTERS | WORD_READ_REGISTERS))


def shell_join(parts: Iterable[str]) -> str:
    return " ".join(parts)


def run_command(cmd: list[str], timeout: int = 12) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"command not found: {cmd[0]}\n"
    except subprocess.TimeoutExpired:
        return 124, "", f"command timed out after {timeout}s\n"


def write_section(output: list[str], title: str) -> None:
    output.append("")
    output.append(f"## {title}")


def append_command(output: list[str], title: str, cmd: list[str], timeout: int = 12) -> None:
    write_section(output, title)
    output.append(f"$ {shell_join(cmd)}")
    code, stdout, stderr = run_command(cmd, timeout=timeout)
    output.append(f"exit_code: {code}")
    if stdout.strip():
        output.append("stdout:")
        output.append(stdout.rstrip())
    if stderr.strip():
        output.append("stderr:")
        output.append(stderr.rstrip())


def append_file(output: list[str], title: str, path: pathlib.Path) -> None:
    write_section(output, title)
    output.append(str(path))
    try:
        output.append(path.read_text(encoding="utf-8", errors="replace").rstrip())
    except OSError as exc:
        output.append(f"error: {exc}")


def append_ec_registers(output: list[str], addresses: tuple[int, ...]) -> None:
    write_section(output, "EC State (decoded values; RPM uses word reads)")
    output.append(f"ec_io_path: {EC_IO_PATH}")

    try:
        values = asyncio.run(read_daemon_ec())
        for address in addresses:
            output.append(f"0x{address:02X}: {values[address]}")
    except (OSError, TimeoutError, RuntimeError) as exc:
        output.append(f"Daemon unavailable: {exc}; install/start predator-sensed.service")


async def read_daemon_ec():
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    values = {}
    try:
        for method, registers in (
            ("GetFanState", [MODE_REGISTERS[FanChannel.CPU], MODE_REGISTERS[FanChannel.GPU],
                             CONTROL_REGISTERS[FanChannel.CPU], CONTROL_REGISTERS[FanChannel.GPU]]),
            ("GetCoolBoost", [COOLBOOST_REGISTER]),
            ("GetFanSpeeds", [RPM_REGISTERS[FanChannel.CPU], RPM_REGISTERS[FanChannel.GPU]]),
        ):
            reply = await asyncio.wait_for(bus.call(Message(
                destination=BUS_NAME, path=OBJECT_PATH, interface=INTERFACE, member=method,
            )), 4)
            if reply.message_type == MessageType.ERROR:
                for register in registers:
                    values[register] = f"unavailable [{reply.error_name}]: {reply.body[0]}"
            else:
                for register, value in zip(registers, reply.body):
                    values[register] = "unavailable" if value == -1 else value
        return values
    finally:
        bus.disconnect()


def append_hwmon_inventory(output: list[str]) -> None:
    write_section(output, "hwmon Inventory")
    hwmon_dir = pathlib.Path("/sys/class/hwmon")
    if not hwmon_dir.exists():
        output.append("/sys/class/hwmon not found")
        return

    for hw in sorted(hwmon_dir.glob("hwmon*")):
        output.append(f"[{hw.name}]")
        name_path = hw / "name"
        try:
            name = name_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            name = "<unknown>"
        output.append(f"name={name}")

        interesting = sorted(hw.glob("temp*_input"))
        interesting += sorted(hw.glob("fan*_input"))
        interesting += sorted(hw.glob("pwm*"))
        interesting += sorted(hw.glob("temp*_label"))

        if not interesting:
            output.append("(no temp/fan/pwm files)")
            continue

        for path in interesting:
            try:
                value = path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError as exc:
                value = f"<error: {exc}>"
            output.append(f"{path.name}={value}")


def append_nvidia_live_sample(output: list[str], seconds: int) -> None:
    write_section(output, f"NVIDIA Live Sample ({seconds}s)")

    if shutil.which("nvidia-smi") is None:
        output.append("nvidia-smi not found")
        return

    interval = 2
    loops = max(1, seconds // interval)
    output.append(
        "columns: timestamp,name,temperature.gpu,utilization.gpu,"
        "clocks.gr,clocks.mem,power.draw"
    )

    for index in range(loops):
        cmd = [
            "nvidia-smi",
            "--query-gpu=timestamp,name,temperature.gpu,utilization.gpu,clocks.gr,clocks.mem,power.draw",
            "--format=csv,noheader,nounits",
        ]
        code, stdout, stderr = run_command(cmd, timeout=6)
        if code != 0:
            output.append(f"error (exit {code}): {stderr.strip() or 'unknown error'}")
            break
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        output.extend(lines)
        if index < loops - 1:
            time.sleep(interval)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect Linux diagnostics for Predator Sense feature parity work."
    )
    parser.add_argument(
        "--output",
        default="",
        help="Report output file path (default: diagnostics-YYYYmmdd-HHMMSS.txt)",
    )
    parser.add_argument(
        "--gpu-sample-seconds",
        type=int,
        default=20,
        help="Duration for repeated nvidia-smi sampling (default: 20)",
    )
    parser.add_argument(
        "--ec-addresses",
        nargs="*",
        default=[f"0x{x:02X}" for x in DEFAULT_EC_ADDRESSES],
        help="EC addresses to read (example: 0x10 0x21 0x22 0x37 0x3A)",
    )
    return parser


def parse_ec_addresses(raw: list[str]) -> tuple[int, ...]:
    values: list[int] = []
    for item in raw:
        try:
            value = int(item, 0)
            if value not in BYTE_READ_REGISTERS | WORD_READ_REGISTERS:
                raise ValueError("outside the supported G3-572 read registers")
            values.append(value)
        except ValueError as exc:
            raise SystemExit(f"Invalid EC address: {item}") from exc
    return tuple(values)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    output_path = pathlib.Path(args.output or f"predator-diagnostics-{timestamp}.txt").resolve()
    ec_addresses = parse_ec_addresses(args.ec_addresses)

    report: list[str] = []
    report.append("# Predator Sense Diagnostics Report")
    report.append(f"generated_at: {dt.datetime.now().isoformat()}")
    report.append(f"cwd: {pathlib.Path.cwd()}")
    report.append(f"python: {sys.version.splitlines()[0]}")
    report.append(f"uid: {os.getuid()} euid: {os.geteuid()}")

    append_command(report, "Kernel", ["uname", "-a"])
    append_file(report, "OS Release", pathlib.Path("/etc/os-release"))
    append_file(report, "DMI Product Name", pathlib.Path("/sys/class/dmi/id/product_name"))
    append_file(report, "DMI BIOS Version", pathlib.Path("/sys/class/dmi/id/bios_version"))

    append_command(
        report,
        "PCI Display Devices",
        ["sh", "-lc", "lspci -nn | grep -Ei 'vga|3d|display' || true"],
    )
    append_command(report, "lsmod (ec_sys filter)", ["sh", "-lc", "lsmod | grep ec_sys || true"])
    append_command(report, "EC I/O path details", ["ls", "-l", str(EC_IO_PATH)])

    append_command(report, "nvidia-smi", ["nvidia-smi"])
    append_command(
        report,
        "nvidia-settings clock/coolbits query",
        ["sh", "-lc", "nvidia-settings -q all | grep -Ei 'clock|offset|coolbits|fan' || true"],
    )
    if shutil.which("nvidia-settings") is None:
        write_section(report, "nvidia-settings hint")
        report.append("nvidia-settings was not found in PATH.")
        report.append("Install package: nvidia-settings")
        report.append("Overclock mode detection requires this tool.")
    append_command(report, "sensors", ["sensors"])

    append_hwmon_inventory(report)
    append_ec_registers(report, ec_addresses)
    append_nvidia_live_sample(report, max(0, args.gpu_sample_seconds))

    output_path.write_text("\n".join(report).rstrip() + "\n", encoding="utf-8")
    print(f"Wrote diagnostics report: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
