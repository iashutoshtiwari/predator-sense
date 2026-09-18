"""Diagnostics failures and report content using only injected host boundaries."""

import asyncio
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from dbus_next import MessageFlag, MessageType

from predator_sense import diagnostics
from predator_sense.service.telemetry_model import TelemetrySnapshot


class DiagnosticsTests(unittest.TestCase):
    def test_file_and_command_failures(self):
        for error in (FileNotFoundError("missing"), PermissionError("denied")):
            with patch.object(Path, "read_text", side_effect=error):
                self.assertIn("unavailable", diagnostics.read_file_safe(Path("fake")))
            with patch.object(diagnostics.subprocess, "run", side_effect=error):
                code, output = diagnostics.run_cmd(["fake-command"])
                self.assertNotEqual(code, 0)
                self.assertTrue(output)
        with patch.object(diagnostics.subprocess, "run", side_effect=subprocess.TimeoutExpired("fake", 8)):
            self.assertEqual(diagnostics.run_cmd(["fake-command"])[0], 124)

    def test_os_release(self):
        with patch.object(Path, "read_text", return_value='NAME="Test Linux"\n'):
            self.assertEqual(diagnostics.get_os_release(), "Test Linux")
        with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            self.assertEqual(diagnostics.get_os_release(), "Linux")

    def test_report_survives_missing_or_inaccessible_ec_without_opening_it(self):
        for error in (PermissionError("debugfs denied"), FileNotFoundError("EC missing")):
            ec = Mock()
            ec.stat.side_effect = error
            with (patch.object(diagnostics, "EC_IO_PATH", ec),
                  patch.object(diagnostics, "read_file_safe", return_value="fixture"),
                  patch.object(diagnostics, "get_os_release", return_value="Test Linux"),
                  patch.object(Path, "is_file", return_value=False),
                  patch.object(diagnostics, "run_cmd", return_value=(127, "unavailable")),
                  patch.object(diagnostics, "query_daemon_telemetry", new=AsyncMock(
                      return_value=({}, None, "unavailable: no system bus"))),
                  patch("builtins.open", side_effect=AssertionError("Diagnostics must not open EC"))):
                joined = "\n".join(diagnostics.collect_diagnostics_report())
            for expected in ("app_version: 1.0.0", "ec_io_exists: unavailable", "daemon_query: unavailable",
                             "## EC Register Observations", "## Graphics Devices"):
                self.assertIn(expected, joined)
            for private in ("uid:", "euid:", "cwd:", "Overclock"):
                self.assertNotIn(private, joined)

    def test_main_stdout(self):
        with (patch.object(diagnostics, "collect_diagnostics_report", return_value=["test diagnostic line"]),
              patch("sys.argv", ["predator-sense-diagnostics", "--stdout"])):
            self.assertEqual(diagnostics.main(), 0)


class DiagnosticsBusTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def reply(signature, body, kind=MessageType.METHOD_RETURN):
        return SimpleNamespace(signature=signature, body=body, message_type=kind, error_name="Unavailable")

    async def query(self, replies=(), *, connect_error=None):
        bus = SimpleNamespace(call=AsyncMock(side_effect=replies), disconnect=Mock(),
                              wait_for_disconnect=AsyncMock())
        bus.connect = AsyncMock(return_value=bus, side_effect=connect_error)
        with patch.object(diagnostics, "MessageBus", return_value=bus):
            result = await diagnostics.query_daemon_telemetry()
        bus.disconnect.assert_called_once()
        return result, bus

    async def test_connection_failures_are_unavailable(self):
        for error in (FileNotFoundError("missing"), ConnectionRefusedError("refused"),
                      PermissionError("denied"), TimeoutError()):
            result, _ = await self.query(connect_error=error)
            self.assertIn("unavailable", result[2])
        with patch.object(diagnostics, "MessageBus", side_effect=OSError("cannot construct bus")):
            self.assertIn("unavailable", (await diagnostics.query_daemon_telemetry())[2])

    async def test_connection_is_bounded(self):
        async def stalled():
            await asyncio.Event().wait()
        original = asyncio.wait_for
        async def short_wait(awaitable, timeout):
            self.assertEqual(timeout, 4)
            return await original(awaitable, 0.01)
        bus = SimpleNamespace(connect=stalled, disconnect=Mock(), wait_for_disconnect=AsyncMock())
        with (patch.object(diagnostics, "MessageBus", return_value=bus),
              patch.object(diagnostics.asyncio, "wait_for", side_effect=short_wait)):
            self.assertIn("TimeoutError", (await diagnostics.query_daemon_telemetry())[2])

    async def test_absent_owner_and_malformed_telemetry(self):
        result, _ = await self.query([self.reply("", [], MessageType.ERROR)])
        self.assertIn("not running", result[2])
        for reply in (self.reply("s", []), self.reply("s", ["not-json"]), self.reply("i", [1])):
            result, _ = await self.query([self.reply("s", [":1.42"]), reply])
            self.assertIsNone(result[1])
            self.assertTrue(result[2])

    async def test_read_only_owner_pinned_and_snapshot_aged(self):
        result, bus = await self.query([
            self.reply("s", [":1.42"]), self.reply("s", [TelemetrySnapshot.empty().to_json()]),
            self.reply("ssii", ["auto", "manual", 50, 70]), self.reply("i", [1]), self.reply("ii", [0, -1]),
        ])
        self.assertIsNone(result[2])
        self.assertEqual(result[0][0x13], "0")
        self.assertEqual(result[0][0x15], "unavailable")
        messages = [c.args[0] for c in bus.call.call_args_list]
        self.assertTrue(all(m.flags & MessageFlag.NO_AUTOSTART for m in messages))
        self.assertTrue(all(m.destination == ":1.42" for m in messages[1:]))
        self.assertTrue(all(m.member.startswith("Get") for m in messages))

    async def test_bad_register_reply_is_reported(self):
        result, _ = await self.query([
            self.reply("s", [":1.42"]), self.reply("s", [TelemetrySnapshot.empty().to_json()]),
            self.reply("i", [0]), self.reply("", [], MessageType.ERROR), self.reply("ii", [0, 0]),
        ])
        self.assertIn("malformed", result[0][0x22])
        self.assertIn("unavailable", result[0][0x10])
