import asyncio
import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from dbus_next import Message, MessageType

from support import BackendCase
from predator_sense.core.profiles import FanMode
from predator_sense.core.state import CoolingState, load_cooling_state, save_cooling_state
from predator_sense.service.controller import Controller
from predator_sense.service.lifecycle import LOGIN, MANAGER, PATH, SleepMonitor
from predator_sense.service.protocol import ServiceError
from predator_sense.service.telemetry_model import TelemetrySnapshot


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


class UnknownModeTests(BackendCase):
    def test_unknown_modes_block_all_automatic_writes_and_preserve_file(self):
        for address in (0x21, 0x22):
            with self.subTest(address=address):
                self.ec.data[0x21] = 0
                self.ec.data[0x22] = 0
                self.ec.data[address] = 255
                self.ec.writes.clear()
                path = self.root / "unknown-state.json"
                desired = CoolingState(cpu_mode="turbo", gpu_mode="turbo", coolboost_enabled=True)
                save_cooling_state(desired, path)
                original = path.read_bytes()
                controller = Controller(self.backend, state_file=path)
                controller.recover(startup=True)
                self.assertTrue(controller.invoke("GetStatus", [])[0])  # Explicit controls remain available.
                self.assertIn("Unknown fan mode", controller.startup_warning)
                controller.suspend()
                controller.recover()
                controller.sample_ec()
                controller.shutdown()
                self.assertEqual(self.ec.writes, [])
                self.assertEqual(path.read_bytes(), original)

    def test_unknown_latch_requires_explicit_channel_action(self):
        self.ec.data[0x22] = 255
        self.ec.data[0x21] = 254
        path = self.root / "state.json"
        controller = Controller(self.backend, state_file=path)
        controller.recover(startup=True)
        self.assertFalse(path.exists())
        controller.invoke("SetCpuFanMode", ["turbo"])
        self.assertEqual(self.ec.data[0x21], 254)
        self.assertFalse(path.exists())
        self.ec.writes.clear()
        controller.recover()
        self.assertEqual(self.ec.writes, [])
        controller.invoke("SetGpuFanMode", ["auto"])
        self.assertFalse(controller.unknown_channels)
        self.assertEqual(load_cooling_state(path).cpu_mode, "turbo")
        self.assertEqual(load_cooling_state(path).gpu_mode, "auto")
        self.assertEqual(controller.startup_warning, "")


class PreparationRecoveryTests(BackendCase, IsolatedAsyncioTestCase):
    async def exercise(self, *, resume=False, unsupported=False, fail_write=False):
        from unittest.mock import Mock, patch
        from predator_sense.core.errors import ErrorCode, HardwareError
        controller = Controller(self.backend, state_file=self.root / "state.json")
        if resume:
            controller.recover(startup=True)
            controller.lifecycle = "Recovering"
        service = type("Service", (), {"controller": controller, "lock": asyncio.Lock()})()
        prepare = Mock()
        if unsupported:
            prepare.side_effect = HardwareError(ErrorCode.UNSUPPORTED_HARDWARE, "Other model")
        elif not fail_write:
            prepare.side_effect = [OSError("temporarily unavailable"), None]
        if fail_write:
            self.ec.ignore_write = True
        monitor = SleepMonitor(None, service, prepare=prepare)
        monitor.events.put_nowait(False)
        original = asyncio.wait_for
        delays = []
        async def short_wait(awaitable, timeout):
            delays.append(timeout)
            return await original(awaitable, 0.01)
        with patch("predator_sense.service.lifecycle.asyncio.wait_for", side_effect=short_wait):
            task = asyncio.create_task(monitor.run())
            try:
                for _ in range(100):
                    if not controller.starting and not controller.lifecycle:
                        break
                    await asyncio.sleep(0.01)
                self.assertFalse(controller.starting)
                self.assertEqual(controller.lifecycle, "")
                calls = prepare.call_count
                await asyncio.sleep(0.04)
                self.assertEqual(prepare.call_count, calls)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        if unsupported or fail_write:
            self.assertEqual(prepare.call_count, 1)
            self.assertTrue(controller.degraded)
        else:
            self.assertEqual(prepare.call_count, 2)
            self.assertEqual(delays, [1])
            self.assertFalse(controller.degraded)
            self.assertEqual(self.backend.get_cpu_fan_mode(), FanMode.AUTO)
        if unsupported:
            self.assertEqual(self.ec.writes, [])

    async def test_startup_retries_preparation_then_restores(self):
        await self.exercise()

    async def test_resume_reprepares_before_restoring(self):
        await self.exercise(resume=True)

    async def test_unsupported_stops_recovery_without_writes(self):
        await self.exercise(unsupported=True)

    async def test_failed_write_is_not_retried(self):
        await self.exercise(fail_write=True)

    async def test_backoff_caps_at_thirty_seconds_and_close_stops_retry(self):
        from unittest.mock import Mock, patch
        controller = Controller(self.backend, state_file=self.root / "state.json")
        prepare = Mock(side_effect=OSError("missing EC"))
        service = type("Service", (), {"controller": controller, "lock": asyncio.Lock()})()
        monitor = SleepMonitor(None, service, prepare=prepare)
        monitor.events.put_nowait(False)
        delays = []
        async def expire(awaitable, timeout):
            awaitable.close()
            delays.append(timeout)
            if len(delays) == 8:
                monitor.closed = True
            raise TimeoutError()
        with patch("predator_sense.service.lifecycle.asyncio.wait_for", side_effect=expire):
            await monitor.run()
        self.assertEqual(delays, [1, 2, 4, 8, 16, 30, 30, 30])
        self.assertEqual(self.ec.writes, [])

    async def test_suspended_or_stopping_never_prepares(self):
        from unittest.mock import Mock
        controller = Controller(self.backend, state_file=self.root / "state.json")
        service = type("Service", (), {"controller": controller})()
        prepare = Mock()
        monitor = SleepMonitor(None, service, prepare=prepare)
        for lifecycle in ("Suspended", "Stopping"):
            controller.lifecycle = lifecycle
            monitor._recover()
        prepare.assert_not_called()
        self.assertEqual(self.ec.writes, [])
