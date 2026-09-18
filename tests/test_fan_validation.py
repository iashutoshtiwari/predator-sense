"""RPM contract and read-only validation utility; fake hardware/bus only."""

import asyncio
from dataclasses import replace
import errno
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest

from dbus_next import MessageFlag, MessageType

from support import BackendCase
from predator_sense.core.errors import ErrorCode
from predator_sense.core.profiles import FanChannel, RPM_REGISTERS
from predator_sense.service.controller import Controller
from predator_sense.service.telemetry_model import FIELDS, TelemetrySnapshot, observed

spec = importlib.util.spec_from_file_location(
    "validate_fan_telemetry", Path(__file__).resolve().parent.parent / "scripts/validate_fan_telemetry.py"
)
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)


class RpmTests(BackendCase):
    def test_explicit_channel_bytes_and_telemetry_roundtrip(self):
        self.ec.data[0x13:0x15] = b"\x80\x0d"  # 3456, not 32781
        self.ec.data[0x15:0x17] = b"\xea\x17"  # 6122, not 59927
        self.assertEqual(self.backend.get_cpu_fan_rpm(), 3456)
        self.assertEqual(self.backend.get_gpu_fan_rpm(), 6122)
        controller = Controller(self.backend, state_file=self.root / "state.json")
        controller.starting = False
        ec = {r.name: r for r in controller.sample_ec().readings}
        snapshot = replace(TelemetrySnapshot.empty(), readings=tuple(
            ec.get(name, observed(name, None, "fake")) for name in FIELDS
        ))
        decoded = TelemetrySnapshot.from_json(snapshot.to_json())
        self.assertEqual(decoded.cpu_fan_rpm, 3456)
        self.assertEqual(decoded.gpu_fan_rpm, 6122)
        self.assertEqual(validation.row(decoded, "turbo")[4], 3456)
        self.assertEqual(validation.row(decoded, "turbo")[7], 6122)
        self.assertEqual(self.ec.writes, [])

    def test_short_and_disconnected_reads_on_both_channels(self):
        for channel in FanChannel:
            address = RPM_REGISTERS[channel]
            self.ec.read_override[address] = b"\x01"
            self.assert_code(ErrorCode.SHORT_READ, lambda: self.backend.get_fan_rpm(channel))
            del self.ec.read_override[address]
            self.ec.open_error = OSError(errno.ENODEV, "disconnected")
            self.assert_code(ErrorCode.EC_UNAVAILABLE, lambda: self.backend.get_fan_rpm(channel))
            self.ec.open_error = None
        self.assertEqual(self.ec.writes, [])

    def test_invalid_warning_is_rate_limited_per_channel_without_clamping(self):
        for channel in FanChannel:
            address = RPM_REGISTERS[channel]
            self.ec.data[address:address + 2] = b"\xff\xff"
        with patch("predator_sense.core.hardware.time.monotonic", return_value=100) as clock, \
                patch("predator_sense.core.hardware.logger.warning") as warning:
            for _ in range(60):
                for channel in FanChannel:
                    self.assertIsNone(self.backend.get_fan_rpm(channel))
            self.assertEqual(warning.call_count, 2)
            clock.return_value = 160
            self.assertIsNone(self.backend.get_cpu_fan_rpm())
            self.assertEqual(warning.call_count, 3)
        self.assertEqual(self.ec.writes, [])

    def test_rpm_registers_and_adjacent_bytes_cannot_be_written(self):
        with self.backend._transaction(write=True) as session:
            for address in (0x13, 0x14, 0x15, 0x16):
                self.assert_code(ErrorCode.WRITE_REFUSED,
                                 lambda: self.backend._write_verified(session, address, 0))
        self.assertEqual(self.ec.writes, [])


class UtilityTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def reply(signature, body):
        return SimpleNamespace(message_type=MessageType.METHOD_RETURN, signature=signature, body=body)

    async def test_only_read_calls_no_auto_activation_and_owner_pinned(self):
        snapshot = TelemetrySnapshot.empty()
        bus = SimpleNamespace(call=AsyncMock(side_effect=[
            self.reply("s", [":1.42"]), self.reply("ssbb", ["Predator G3-572", "V1.22", True, True]),
            self.reply("s", [snapshot.to_json()]), self.reply("s", [snapshot.to_json()]),
        ]))
        output = io.StringIO()
        with patch.object(asyncio, "sleep", new_callable=AsyncMock):
            await validation.sample(bus, SimpleNamespace(samples=2, interval=1, label="manual-50"),
                                    output=output, diagnostics=io.StringIO())
        messages = [c.args[0] for c in bus.call.call_args_list]
        self.assertEqual([m.member for m in messages],
                         ["GetNameOwner", "GetHardwareIdentity", "GetTelemetrySnapshot", "GetTelemetrySnapshot"])
        self.assertTrue(all(m.flags & MessageFlag.NO_AUTOSTART for m in messages))
        self.assertTrue(all(m.destination == ":1.42" for m in messages[1:]))
        self.assertIn("Unavailable", output.getvalue())
        self.assertIn("manual-50", output.getvalue())

    async def test_missing_daemon_stops_without_start_request(self):
        bus = SimpleNamespace(call=AsyncMock(return_value=SimpleNamespace(
            message_type=MessageType.ERROR, error_name="org.freedesktop.DBus.Error.NameHasNoOwner", body=["absent"],
        )))
        with self.assertRaises(RuntimeError):
            await validation.sample(bus, SimpleNamespace(samples=1, interval=1, label="auto"),
                                    output=io.StringIO(), diagnostics=io.StringIO())
        self.assertEqual(bus.call.await_count, 1)

    async def test_wrong_model_refused_before_sampling(self):
        bus = SimpleNamespace(call=AsyncMock(side_effect=[
            self.reply("s", [":1.42"]), self.reply("ssbb", ["Predator G3-573", "V1.22", False, True]),
        ]))
        with self.assertRaises(RuntimeError):
            await validation.sample(bus, SimpleNamespace(samples=1, interval=1, label="auto"))
        self.assertEqual(bus.call.await_count, 2)

    async def test_real_zero_unavailable_and_stale_output(self):
        snapshot = replace(TelemetrySnapshot.empty(), readings=tuple(
            observed(name, 0 if name == "cpu_fan_rpm" else None, "fake", now=10) for name in FIELDS
        ))
        row = validation.row(snapshot, "zero", now=11)
        self.assertEqual(row[4], 0)
        self.assertEqual(row[7], "Unavailable")
        self.assertEqual(validation.row(snapshot, "stale", now=14)[4:6], ["Unavailable", "stale"])

    async def test_invalid_interval_rejected_without_bus_access(self):
        with patch.object(validation, "run") as run, patch("sys.stderr", new=io.StringIO()):
            for interval in ("0", "0.1", "nan", "inf", "61"):
                with self.assertRaises(SystemExit):
                    validation.main(["--interval", interval])
            run.assert_not_called()
