import asyncio
import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from dbus_next import Message, MessageType

from support import BackendCase
from core.profiles import FanMode
from core.state import CoolingState, load_cooling_state, save_cooling_state
from service.controller import Controller
from service.lifecycle import LOGIN, MANAGER, PATH, SleepMonitor
from service.protocol import ServiceError
from service.telemetry_model import TelemetrySnapshot


class PersistenceTests(BackendCase):
    def setUp(self):
        super().setUp()
        self.path = self.root / "state.json"
        self.controller = Controller(self.backend, state_file=self.path)

    def test_fresh_startup_explicit_auto_and_off(self):
        self.controller.recover(startup=True)
        self.assertEqual(self.backend.get_cpu_fan_mode(), FanMode.AUTO)
        self.assertEqual(self.backend.get_gpu_fan_mode(), FanMode.AUTO)
        self.assertFalse(self.backend.get_coolboost())
        self.assertEqual(load_cooling_state(self.path), CoolingState())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_restore_restart_and_stop_preserves_desired(self):
        self.controller.recover(startup=True)
        self.controller.invoke("SetCpuManualSpeed", [70])
        self.controller.invoke("SetGpuFanMode", ["turbo"])
        self.controller.invoke("SetCoolBoost", [True])
        desired = load_cooling_state(self.path)
        self.assertEqual(desired.cpu_manual_percent, 70)
        self.assertEqual(self.controller.shutdown(), "")
        self.assertEqual(self.backend.get_cpu_fan_mode(), FanMode.AUTO)
        self.assertFalse(self.backend.get_coolboost())
        self.assertEqual(load_cooling_state(self.path), desired)
        restarted = Controller(self.backend, state_file=self.path)
        restarted.recover(startup=True)
        self.assertEqual(self.backend.get_cpu_fan_mode(), FanMode.MANUAL)
        self.assertEqual(self.backend.get_cpu_manual_speed(), 70)
        self.assertEqual(self.backend.get_gpu_fan_mode(), FanMode.TURBO)
        self.assertTrue(self.backend.get_coolboost())

    def test_corrupt_and_invalid_are_auto(self):
        for data in ("{", "[]", '{"version": true}', '{"coolboost_enabled": "yes"}'):
            self.path.write_text(data)
            self.controller.recover(startup=True)
            self.assertEqual(load_cooling_state(self.path), CoolingState())
            self.assertIn("Invalid saved state", self.controller.startup_warning)

    def test_legacy_migration(self):
        self.path.write_text('{"coolboost_enabled": true}')
        self.controller.recover(startup=True)
        self.assertEqual(load_cooling_state(self.path), CoolingState(coolboost_enabled=True))

    def test_strict_validation(self):
        for args in ({"cpu_mode": "unknown"}, {"cpu_mode": "manual", "cpu_manual_percent": True},
                     {"gpu_mode": "manual", "gpu_manual_percent": 101}, {"cpu_manual_percent": 50}):
            with self.assertRaises(ValueError):
                CoolingState(**args)
        save_cooling_state(CoolingState(), self.path)
        data = json.loads(self.path.read_text())
        del data["cpu_mode"]
        self.path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            load_cooling_state(self.path)

    def test_suspend_flush_no_writes_resume_restores(self):
        self.controller.recover(startup=True)
        self.controller.invoke("SetCpuManualSpeed", [30])
        self.ec.writes.clear()
        self.controller.lifecycle = "Suspended"
        self.controller.suspend()
        self.assertEqual(self.ec.writes, [])
        self.assertIsNone(self.controller.telemetry_snapshot().cpu_fan_rpm)
        with self.assertRaises(ServiceError):
            self.controller.invoke("SetGlobalTurbo", [])
        self.ec.data[0x22] = 0
        self.controller.lifecycle = "Recovering"
        self.controller.recover()
        self.assertEqual(self.backend.get_cpu_fan_mode(), FanMode.MANUAL)
        self.assertEqual(self.backend.get_cpu_manual_speed(), 30)

    def test_write_failure_degrades_and_falls_back(self):
        self.controller.recover(startup=True)
        self.ec.ignore_write = True
        with self.assertRaises(ServiceError):
            self.controller.invoke("SetCpuFanMode", ["turbo"])
        self.assertIn("fallback", self.controller.degraded)
        self.assertEqual(self.controller.invoke("GetStatus", [])[1], "Degraded")
        self.assertEqual(load_cooling_state(self.path), CoolingState())

    def test_pre_resume_samples_are_not_current(self):
        self.controller.recover(startup=True)
        self.controller.telemetry = type("Telemetry", (), {"latest": TelemetrySnapshot.empty()})()
        self.assertEqual(self.controller.telemetry_snapshot().code, "Recovering")

    def test_suspend_before_startup_preserves_saved_preferences(self):
        desired = CoolingState(cpu_mode="turbo")
        save_cooling_state(desired, self.path)
        self.controller.lifecycle = "Suspended"
        self.controller.suspend()
        self.assertEqual(load_cooling_state(self.path), desired)
        self.controller.lifecycle = "Recovering"
        self.controller.recover()
        self.assertEqual(self.backend.get_cpu_fan_mode(), FanMode.TURBO)

    def test_stop_prevents_late_mutation_and_recovery(self):
        self.controller.recover(startup=True)
        self.controller.shutdown()
        self.ec.writes.clear()
        with self.assertRaises(ServiceError):
            self.controller.invoke("SetGlobalTurbo", [])
        with self.assertRaises(ServiceError):
            self.controller.recover()
        self.assertEqual(self.ec.writes, [])

    def test_changed_identity_prevents_resume_writes(self):
        self.controller.recover(startup=True)
        self.ec.writes.clear()
        self.product.write_text("Other model")
        with self.assertRaises(Exception):
            self.controller.recover()
        self.assertEqual(self.ec.writes, [])


class SleepTests(IsolatedAsyncioTestCase):
    async def test_missing_ec_retries_then_recovers_without_duplicate_operations(self):
        controller = type("Controller", (), {
            "recover": AsyncMock(), "degraded": "", "lifecycle": "Recovering", "restore_failed": False,
        })()
        # to_thread expects a synchronous method.
        calls = []
        def recover():
            calls.append(1)
            if len(calls) == 1:
                raise OSError("EC missing")
        controller.recover = recover
        service = type("Service", (), {"controller": controller, "lock": asyncio.Lock()})()
        monitor = SleepMonitor(None, service)
        monitor.events.put_nowait(False)
        task = asyncio.create_task(monitor.run())
        await asyncio.sleep(1.15)
        self.assertEqual(len(calls), 2)
        self.assertEqual(controller.lifecycle, "")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_only_logind_owner_can_trigger_sleep(self):
        controller = type("Controller", (), {"lifecycle": ""})()
        service = type("Service", (), {"controller": controller})()
        monitor = SleepMonitor(None, service)
        monitor.owner = ":1.4"
        def signal(sender):
            return Message(message_type=MessageType.SIGNAL, sender=sender, path=PATH,
                           interface=MANAGER, member="PrepareForSleep", signature="b", body=[True])
        monitor.handle(signal(":1.8"))
        self.assertTrue(monitor.events.empty())
        monitor.handle(signal(":1.4"))
        self.assertEqual(controller.lifecycle, "Suspended")
        self.assertTrue(await monitor.events.get())
        self.assertEqual(LOGIN, "org.freedesktop.login1")
