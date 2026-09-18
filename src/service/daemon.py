"""System D-Bus adapter. Sender identity comes exclusively from bus messages."""

import asyncio
from collections import Counter
import logging
import signal
import uuid

from dbus_next import BusType, Message, MessageType, NameFlag, RequestNameReply, Variant
from dbus_next.aio import MessageBus

from service.protocol import (
    ACTION_ID,
    BUS_NAME,
    CONTROL_METHODS,
    ERROR_PREFIX,
    INTERFACE,
    METHODS,
    OBJECT_PATH,
    ServiceError,
    introspection_xml,
    validate_call,
)

logger = logging.getLogger(__name__)


class PolkitAuthorizer:
    def __init__(self, bus):
        self.bus = bus

    async def authorize(self, sender):
        cancellation_id = uuid.uuid4().hex
        request = Message(
            destination="org.freedesktop.PolicyKit1",
            path="/org/freedesktop/PolicyKit1/Authority",
            interface="org.freedesktop.PolicyKit1.Authority",
            member="CheckAuthorization",
            signature="(sa{sv})sa{ss}us",
            body=[["system-bus-name", {"name": Variant("s", sender)}], ACTION_ID, {}, 1, cancellation_id],
        )
        try:
            reply = await asyncio.wait_for(self.bus.call(request), 110)
        except (TimeoutError, asyncio.CancelledError):
            # A timed-out GUI must not leave an authentication prompt outstanding.
            await self.bus.send(
                Message(
                    destination=request.destination,
                    path=request.path,
                    interface=request.interface,
                    member="CancelCheckAuthorization",
                    signature="s",
                    body=[cancellation_id],
                )
            )
            raise ServiceError("AuthorizationTimeout", "Authorization timed out; retry the action")
        except Exception as exc:
            raise ServiceError("AuthorizationUnavailable", "Polkit is unavailable; check the system service") from exc
        if reply.message_type == MessageType.ERROR:
            raise ServiceError(
                "AuthorizationUnavailable", "Polkit could not check authorization; check the session agent"
            )
        if reply.signature != "(bba{ss})":
            raise ServiceError("AuthorizationUnavailable", "Invalid Polkit response")
        allowed, _challenge, details = reply.body[0]
        if not allowed:
            code = "AuthorizationCancelled" if details.get("polkit.dismissed") else "NotAuthorized"
            raise ServiceError(
                code, "Authorization cancelled" if code == "AuthorizationCancelled" else "Authorization denied"
            )

    async def caller_exists(self, sender):
        reply = await asyncio.wait_for(
            self.bus.call(
                Message(
                    destination="org.freedesktop.DBus",
                    path="/org/freedesktop/DBus",
                    interface="org.freedesktop.DBus",
                    member="NameHasOwner",
                    signature="s",
                    body=[sender],
                )
            ),
            3,
        )
        return reply.message_type == MessageType.METHOD_RETURN and reply.signature == "b" and reply.body == [True]


class ControlService:
    def __init__(self, bus, controller, authorizer):
        self.bus, self.controller, self.authorizer = bus, controller, authorizer
        self.lock = asyncio.Lock()
        self.pending = Counter()
        self.tasks = set()

    async def dispatch(self, member, signature, body, sender):
        validate_call(member, signature, body)
        if not sender or not sender.startswith(":"):
            raise ServiceError("NotAuthorized", "Missing unique D-Bus caller identity")
        if member in ("GetTelemetrySnapshot", "GetTemperatures"):
            return self.controller.invoke(member, body)
        if member == "GetStatus" and self.controller.starting:
            return self.controller.invoke(member, body)
        if member in CONTROL_METHODS:
            if self.controller.starting:
                raise ServiceError("Starting", "Hardware service is starting; retry shortly")
            await self.authorizer.authorize(sender)
        async with self.lock:
            # Recheck after waiting for both authentication and the hardware lock.
            if member in CONTROL_METHODS and not await self.authorizer.caller_exists(sender):
                raise ServiceError("NotAuthorized", "Requesting application disconnected; action cancelled")
            return await asyncio.to_thread(self.controller.invoke, member, body)

    def handle_message(self, message):
        if message.message_type != MessageType.METHOD_CALL or message.path != OBJECT_PATH:
            return None
        if message.interface == "org.freedesktop.DBus.Introspectable" and message.member == "Introspect":
            if message.signature:
                return Message.new_error(message, "org.freedesktop.DBus.Error.InvalidArgs", "No arguments expected")
            return Message.new_method_return(message, "s", [introspection_xml()])
        if message.interface == "org.freedesktop.DBus.Peer" and message.member == "Ping" and not message.signature:
            return Message.new_method_return(message)
        if message.interface != INTERFACE:
            return Message.new_error(message, "org.freedesktop.DBus.Error.UnknownInterface", "Unknown interface")
        try:
            validate_call(message.member, message.signature, message.body)
        except ServiceError as exc:
            return Message.new_error(message, ERROR_PREFIX + exc.code, exc.message)
        sender = message.sender
        if len(self.tasks) >= 32 or self.pending[sender] >= 4:
            return Message.new_error(message, ERROR_PREFIX + "Busy", "Too many pending requests; retry later")
        self.pending[sender] += 1
        task = asyncio.create_task(self._respond(message))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return True

    async def _respond(self, message):
        try:
            result = await self.dispatch(message.member, message.signature, message.body, message.sender)
            reply = Message.new_method_return(message, METHODS[message.member][1], result)
        except ServiceError as exc:
            reply = Message.new_error(message, ERROR_PREFIX + exc.code, exc.message)
        except Exception:
            logger.exception("D-Bus operation failed: %s", message.member)
            reply = Message.new_error(message, ERROR_PREFIX + "Internal", "Service operation failed; check daemon logs")
        finally:
            self.pending[message.sender] -= 1
            if not self.pending[message.sender]:
                del self.pending[message.sender]
        try:
            await self.bus.send(reply)
        except Exception:
            logger.warning("Unable to reply to disconnected D-Bus client")


async def run_daemon():
    # Kept here so importing the bus protocol/client cannot import hardware code.
    from core.env_checks import ensure_ec_access, run_env_checks
    from core.hardware import G3572EcBackend
    from service.controller import Controller
    from service.lifecycle import SleepMonitor
    from service.sensors import CoretempSensor, NvmlSensor
    from service.telemetry import TelemetryEngine

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    controller = Controller(G3572EcBackend())
    service = ControlService(bus, controller, PolkitAuthorizer(bus))
    bus.add_message_handler(service.handle_message)
    result = await bus.request_name(BUS_NAME, NameFlag.DO_NOT_QUEUE)
    if result != RequestNameReply.PRIMARY_OWNER:
        bus.disconnect()
        raise RuntimeError("Another Predator Sense daemon already owns the bus name")

    async def initialize():
        async with service.lock:
            try:
                valid = await asyncio.to_thread(run_env_checks)
                if valid and await asyncio.to_thread(ensure_ec_access):
                    await asyncio.to_thread(controller.recover, startup=True)
                else:
                    controller.degraded = "Hardware identity or EC preparation failed."
            except Exception as exc:
                logger.exception("Initial cooling restoration failed")
                controller.degraded = f"Saved cooling settings could not be restored: {exc}"
            finally:
                controller.starting = False

    monitor = SleepMonitor(bus, service)
    await monitor.start()
    recovery = asyncio.create_task(monitor.run())

    controller.telemetry = TelemetryEngine(CoretempSensor(), NvmlSensor(), controller.sample_ec)
    sampling = asyncio.create_task(controller.telemetry.run())
    startup = asyncio.create_task(initialize())
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    disconnected = asyncio.create_task(bus.wait_for_disconnect())
    stopping = asyncio.create_task(stop.wait())
    await asyncio.wait((disconnected, stopping, sampling, recovery), return_when=asyncio.FIRST_COMPLETED)
    stopping.cancel()
    controller.lifecycle = "Stopping"
    monitor.close()
    bus.remove_message_handler(service.handle_message)
    sampling_failed = sampling.done() and not sampling.cancelled()
    sampling.cancel()
    await asyncio.gather(sampling, return_exceptions=True)
    await startup
    for task in list(service.tasks):
        task.cancel()
    await asyncio.gather(*service.tasks, return_exceptions=True)
    recovery.cancel()
    await asyncio.gather(recovery, return_exceptions=True)
    # The controller lock also drains any already-running to_thread operation.
    fallback_error = await asyncio.to_thread(controller.shutdown)
    if fallback_error:
        logger.error("Best-effort stop fallback incomplete: %s", fallback_error)
    bus.disconnect()
    await disconnected
    if not stop.is_set():
        reason = ("Telemetry sampler stopped unexpectedly" if sampling_failed
                  else "System D-Bus disconnected unexpectedly")
        raise RuntimeError(reason)
