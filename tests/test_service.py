import asyncio
import json
import unittest
from unittest.mock import AsyncMock

from dbus_next import Message, MessageType, Variant

from support import BackendCase
from core.profiles import COOLBOOST_REGISTER, FanMode
from service.controller import Controller
from service.daemon import ControlService, PolkitAuthorizer
from service.telemetry_model import TelemetrySnapshot
from service.protocol import ACTION_ID, CONTROL_METHODS, ERROR_PREFIX, INTERFACE, OBJECT_PATH, ServiceError


class ServiceTests(BackendCase, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.controller = Controller(
            self.backend, state_file=self.root / "state.json", temperatures=lambda: [45000, -1]
        )
        self.controller.starting = False
        self.auth = type("Auth", (), {"authorize": AsyncMock(), "caller_exists": AsyncMock(return_value=True)})()
        self.bus = type("Bus", (), {"send": AsyncMock()})()
        self.service = ControlService(self.bus, self.controller, self.auth)

    async def call(self, member, signature="", body=None):
        return await self.service.dispatch(member, signature, body or [], ":1.42")

    async def test_reads_never_authorize(self):
        self.assertEqual(await self.call("GetHardwareIdentity"), ["Predator G3-572", "V1.22", True, True])
        self.assertEqual(await self.call("GetStatus"), [True, "", ""])
        self.assertEqual(await self.call("GetFanState"), ["firmware_auto", "firmware_auto", 50, 50])
        self.assertEqual(await self.call("GetCoolBoost"), [0])
        self.assertEqual(await self.call("GetFanSpeeds"), [0, 0])
        self.assertEqual(await self.call("GetTemperatures"), [45000, -1])
        self.auth.authorize.assert_not_awaited()
        self.assertEqual(self.ec.writes, [])

    async def test_cached_snapshot_bypasses_busy_hardware_and_never_authorizes(self):
        async with self.service.lock:
            result = await asyncio.wait_for(self.call("GetTelemetrySnapshot"), 0.1)
        snapshot = TelemetrySnapshot.from_json(result[0])
        self.assertIsNone(snapshot.cpu_temp_c)
        self.assertEqual(self.ec.writes, [])
        self.auth.authorize.assert_not_awaited()

    async def test_every_mutation_is_authorized(self):
        for name, (signature, _output) in CONTROL_METHODS.items():
            args = ["turbo"] if signature == "s" else [60] if signature == "i" else [True] if signature == "b" else []
            await self.call(name, signature, args)
        self.assertEqual(self.auth.authorize.await_count, len(CONTROL_METHODS))
        self.auth.authorize.assert_awaited_with(":1.42")
        self.assertEqual(json.loads((self.root / "state.json").read_text()), {"coolboost_enabled": True})

    async def test_all_unauthorized_mutations_have_zero_effects(self):
        self.auth.authorize.side_effect = ServiceError("NotAuthorized", "denied")
        for name, (signature, _) in CONTROL_METHODS.items():
            body = ["auto"] if signature == "s" else [50] if signature == "i" else [True] if signature == "b" else []
            with self.assertRaises(ServiceError) as result:
                await self.call(name, signature, body)
            self.assertEqual(result.exception.code, "NotAuthorized")
        self.assertEqual(self.ec.writes, [])
        self.assertFalse((self.root / "state.json").exists())

    async def test_malformed_requests_rejected_before_auth(self):
        for member, signature, args in (
            ("Write", "ii", [0x10, 1]),
            ("SetCpuFanMode", "s", ["firmware_auto"]),
            ("SetCpuFanMode", "s", ["auto; rm -rf /"]),
            ("SetCpuManualSpeed", "i", [101]),
            ("SetCpuManualSpeed", "i", [-1]),
            ("SetCpuManualSpeed", "i", [True]),
            ("SetCoolBoost", "b", [1]),
            ("GetStatus", "s", ["file"]),
            ("SetCpuFanMode", "ss", ["auto", "path"]),
        ):
            with self.subTest(member=member, args=args), self.assertRaises(ServiceError):
                await self.call(member, signature, args)
        self.auth.authorize.assert_not_awaited()
        self.assertEqual(self.ec.writes, [])

    async def test_unsupported_hardware_fails_even_after_authorization(self):
        self.product.write_text("Other")
        self.assertEqual((await self.call("GetStatus"))[1], "unsupported_hardware")
        with self.assertRaises(ServiceError) as error:
            await self.call("SetCoolBoost", "b", [True])
        self.assertEqual(error.exception.code, "unsupported_hardware")
        self.assertEqual(self.ec.writes, [])

    async def test_backend_error_and_disconnected_sender(self):
        self.ec.ignore_write = True
        with self.assertRaises(ServiceError) as result:
            await self.call("SetCoolBoost", "b", [True])
        self.assertEqual(result.exception.code, "verification_failed")
        self.assertFalse((self.root / "state.json").exists())
        self.ec.writes.clear()
        self.auth.caller_exists.return_value = False
        with self.assertRaises(ServiceError):
            await self.call("SetCpuFanMode", "s", ["turbo"])
        self.assertEqual(self.ec.writes, [])

    async def test_starting_responds_without_waiting_for_backend_lock(self):
        self.controller.starting = True
        async with self.service.lock:
            result = await asyncio.wait_for(self.call("GetStatus"), 0.2)
        self.assertEqual(result[1], "Starting")
        with self.assertRaises(ServiceError):
            await self.call("SetGlobalTurbo")
        self.auth.authorize.assert_not_awaited()

    async def test_missing_sender_refused(self):
        with self.assertRaises(ServiceError):
            await self.service.dispatch("SetGlobalTurbo", "", [], "pretend-root")
        self.auth.authorize.assert_not_awaited()

    async def test_concurrent_calls_serialize(self):
        self.ec.delay = 0.005
        await asyncio.gather(*(self.call("SetCpuManualSpeed", "i", [n]) for n in range(10, 20)))
        self.assertEqual(self.ec.max_active, 1)

    async def test_global_partial_failure_returns_error_without_false_success(self):
        self.ec.read_override[0x21] = b"\x00"
        with self.assertRaises(ServiceError):
            await self.call("SetGlobalTurbo")
        self.assertEqual(self.backend.get_cpu_fan_mode(), FanMode.TURBO)

    async def test_restore_existing_preference_once_and_skip_unknown(self):
        state = self.root / "state.json"
        state.write_text('{"coolboost_enabled": true}')
        self.controller.restore_coolboost()
        self.controller.restore_coolboost()
        self.assertEqual(self.ec.writes, [(COOLBOOST_REGISTER, 1)])
        self.ec.data[COOLBOOST_REGISTER] = 2
        self.controller.restore_coolboost()
        self.assertEqual(len(self.ec.writes), 1)
        for data in ("[]", "{", '{"coolboost_enabled": "false"}'):
            state.write_text(data)
            self.controller.restore_coolboost()
        self.assertEqual(len(self.ec.writes), 1)

    async def test_wire_errors_and_backpressure(self):
        message = Message(
            path=OBJECT_PATH,
            interface=INTERFACE,
            member="SetCoolBoost",
            signature="b",
            body=[True],
            sender=":1.42",
            serial=1,
        )
        self.service.pending[":1.42"] = 4
        response = self.service.handle_message(message)
        self.assertEqual(response.error_name, ERROR_PREFIX + "Busy")
        self.service.pending.clear()
        self.auth.authorize.side_effect = ServiceError("NotAuthorized", "denied")
        self.assertTrue(self.service.handle_message(message))
        await asyncio.gather(*self.service.tasks)
        self.assertEqual(self.bus.send.call_args.args[0].error_name, ERROR_PREFIX + "NotAuthorized")
        self.assertEqual(self.ec.writes, [])


class PolkitTests(unittest.IsolatedAsyncioTestCase):
    async def test_authorization_uses_bus_name_not_caller_supplied_uid(self):
        reply = Message(
            message_type=MessageType.METHOD_RETURN, reply_serial=1, signature="(bba{ss})", body=[[True, False, {}]]
        )
        bus = type("Bus", (), {"call": AsyncMock(return_value=reply)})()
        await PolkitAuthorizer(bus).authorize(":1.42")
        request = bus.call.call_args.args[0]
        self.assertEqual(request.signature, "(sa{sv})sa{ss}us")
        self.assertEqual(request.body[:4], [["system-bus-name", {"name": Variant("s", ":1.42")}], ACTION_ID, {}, 1])

    async def test_cancelled_denied_and_failed_authority(self):
        for details, code in (({}, "NotAuthorized"), ({"polkit.dismissed": "true"}, "AuthorizationCancelled")):
            reply = Message(
                message_type=MessageType.METHOD_RETURN,
                reply_serial=1,
                signature="(bba{ss})",
                body=[[False, False, details]],
            )
            bus = type("Bus", (), {"call": AsyncMock(return_value=reply)})()
            with self.assertRaises(ServiceError) as result:
                await PolkitAuthorizer(bus).authorize(":1.42")
            self.assertEqual(result.exception.code, code)
        bus.call.side_effect = OSError("offline")
        with self.assertRaises(ServiceError) as result:
            await PolkitAuthorizer(bus).authorize(":1.42")
        self.assertEqual(result.exception.code, "AuthorizationUnavailable")

    async def test_authorization_timeout_cancels_polkit_check(self):
        bus = type("Bus", (), {"call": AsyncMock(side_effect=TimeoutError()), "send": AsyncMock()})()
        with self.assertRaises(ServiceError) as result:
            await PolkitAuthorizer(bus).authorize(":1.42")
        self.assertEqual(result.exception.code, "AuthorizationTimeout")
        cancellation = bus.send.call_args.args[0]
        request = bus.call.call_args.args[0]
        self.assertEqual(cancellation.member, "CancelCheckAuthorization")
        self.assertEqual(cancellation.body, [request.body[-1]])

    async def test_malformed_polkit_response_fails_closed(self):
        reply = Message(message_type=MessageType.METHOD_RETURN, reply_serial=1, signature="b", body=[True])
        bus = type("Bus", (), {"call": AsyncMock(return_value=reply)})()
        with self.assertRaises(ServiceError) as result:
            await PolkitAuthorizer(bus).authorize(":1.42")
        self.assertEqual(result.exception.code, "AuthorizationUnavailable")
