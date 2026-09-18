import asyncio
from contextlib import redirect_stdout
import io
import tempfile
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from dbus_next import Message, MessageFlag, MessageType

from support import BackendCase
from predator_sense.core import env_checks
from predator_sense import install_check
from predator_sense.service.telemetry_model import TelemetrySnapshot


class EcPreparationTests(BackendCase):
    def test_loaded_read_only_ec_is_not_reloaded(self):
        ec_file = self.root / "ec"
        ec_file.write_bytes(bytes(256))
        parameter = self.root / "write_support"
        parameter.write_text("N")
        with (patch.object(env_checks, "EC_IO_FILE", ec_file),
              patch.object(env_checks, "EC_WRITE_SUPPORT_PATH", parameter),
              patch.object(env_checks.subprocess, "run") as run):
            self.assertFalse(env_checks.ensure_ec_access())
            run.assert_not_called()
            parameter.write_text("Y")
            self.assertTrue(env_checks.ensure_ec_access())
            run.assert_not_called()

    def test_missing_ec_uses_only_guarded_bounded_modprobe(self):
        parameter = self.root / "write_support"
        parameter.write_text("Y")
        ec_file = self.root / "ec"
        def loaded(*args, **kwargs):
            ec_file.write_bytes(bytes(256))
            return type("Result", (), {"returncode": 0})()
        with (patch.object(env_checks, "EC_IO_FILE", ec_file),
              patch.object(env_checks, "EC_WRITE_SUPPORT_PATH", parameter),
              patch.object(env_checks.subprocess, "run", side_effect=loaded) as run):
            self.assertTrue(env_checks.ensure_ec_access())
            self.assertEqual(run.call_args.args[0], ["modprobe", "ec_sys", "write_support=1"])
            self.assertEqual(run.call_args.kwargs["timeout"], 8)


class InstallCheckerTests(unittest.IsolatedAsyncioTestCase):
    async def test_checker_cannot_activate_daemon_and_pins_owner(self):
        responses = [
            Message(message_type=MessageType.METHOD_RETURN, reply_serial=1, signature="s", body=[":1.42"]),
            Message(message_type=MessageType.METHOD_RETURN, reply_serial=2, signature="s",
                    body=[TelemetrySnapshot.empty().to_json()]),
        ]
        bus = type("Bus", (), {"call": AsyncMock(side_effect=responses),
                              "wait_for_disconnect": AsyncMock(), "disconnect": lambda self: None})()
        connector = type("Connector", (), {"connect": AsyncMock(return_value=bus)})()
        with patch.object(install_check, "MessageBus", return_value=connector):
            await install_check.cached_snapshot()
        messages = [call.args[0] for call in bus.call.call_args_list]
        self.assertTrue(all(m.flags & MessageFlag.NO_AUTOSTART for m in messages))
        self.assertEqual([m.member for m in messages], ["GetNameOwner", "GetTelemetrySnapshot"])
        self.assertEqual(messages[-1].destination, ":1.42")

    async def test_absent_daemon_is_reported_without_activation(self):
        response = Message(message_type=MessageType.ERROR, reply_serial=1,
                           error_name="org.freedesktop.DBus.Error.NameHasNoOwner")
        bus = type("Bus", (), {"call": AsyncMock(return_value=response),
                              "wait_for_disconnect": AsyncMock(), "disconnect": lambda self: None})()
        connector = type("Connector", (), {"connect": AsyncMock(return_value=bus)})()
        with patch.object(install_check, "MessageBus", return_value=connector), self.assertRaises(RuntimeError):
            await install_check.cached_snapshot()
        self.assertEqual(bus.call.await_count, 1)


class SourceArchiveTests(unittest.TestCase):
    def test_source_is_reproducible_and_excludes_builds_fonts_and_state(self):
        import importlib.util
        import tarfile
        root = Path(__file__).resolve().parent.parent
        spec = importlib.util.spec_from_file_location("prepare_source", root / "scripts/prepare_arch_source.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            tree = Path(directory)
            (tree / "pyproject.toml").write_text('[project]\nversion="0.3.0"\n')
            for name in ("README.md", "LICENSE"):
                (tree / name).write_text(name)
            (tree / "PKGBUILD").write_text("sha256sums=('placeholder')\n")
            (tree / "src/predator_sense/assets").mkdir(parents=True)
            (tree / "src/predator_sense/__init__.py").write_text("")
            (tree / "packaging").mkdir()
            (tree / "src/predator_sense/uncertain.ttf").write_text("never redistribute")
            with redirect_stdout(io.StringIO()):
                archive = module.prepare(tree)
                first = archive.read_bytes()
                module.prepare(tree)
            self.assertEqual(first, archive.read_bytes())
            with tarfile.open(archive) as tar:
                self.assertFalse(any(name.endswith(".ttf") for name in tar.getnames()))
        self.assertFalse(asyncio.iscoroutinefunction(module.prepare))
