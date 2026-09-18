"""logind-driven recovery, serialized with D-Bus control operations."""
import asyncio

from dbus_next import Message, MessageType

from predator_sense.core.errors import ErrorCode, HardwareError

LOGIN = "org.freedesktop.login1"
PATH = "/org/freedesktop/login1"
MANAGER = LOGIN + ".Manager"


class SleepMonitor:
    def __init__(self, bus, service, *, prepare=None):
        self.bus = bus
        self.service = service
        self.controller = service.controller
        self.owner = None
        self.events = asyncio.Queue()
        self.closed = False
        self.prepare = prepare

    async def start(self):
        for rule in (
            f"type='signal',sender='{LOGIN}',path='{PATH}',interface='{MANAGER}',member='PrepareForSleep'",
            "type='signal',sender='org.freedesktop.DBus',interface='org.freedesktop.DBus',"
            f"member='NameOwnerChanged',arg0='{LOGIN}'",
        ):
            reply = await asyncio.wait_for(self.bus.call(Message(
                destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus", member="AddMatch", signature="s", body=[rule],
            )), 5)
            if reply.message_type == MessageType.ERROR:
                raise RuntimeError("Cannot subscribe to logind sleep notifications")
        self.bus.add_message_handler(self.handle)
        reply = await asyncio.wait_for(self.bus.call(Message(
            destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus", member="GetNameOwner", signature="s", body=[LOGIN],
        )), 5)
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError("logind is unavailable")
        self.owner = reply.body[0]

    def handle(self, message):
        if self.closed or message.message_type != MessageType.SIGNAL:
            return
        if (message.sender == "org.freedesktop.DBus" and message.interface == "org.freedesktop.DBus"
                and message.member == "NameOwnerChanged" and message.signature == "sss"
                and message.body[0] == LOGIN):
            self.owner = message.body[2] or None
            # A restarted logind may have missed a sleep transition.
            self.controller.lifecycle = "Recovering"
            self.events.put_nowait(False)
        elif (self.owner and message.sender == self.owner and message.path == PATH
              and message.interface == MANAGER and message.member == "PrepareForSleep"
              and message.signature == "b"):
            self.controller.lifecycle = "Suspended" if message.body[0] else "Recovering"
            self.events.put_nowait(message.body[0])

    def _recover(self):
        if self.closed or self.controller.lifecycle in ("Suspended", "Stopping"):
            return
        self.controller.restore_failed = False
        if self.prepare is not None:
            self.prepare()
        self.controller.recover()

    async def run(self):
        while not self.closed:
            sleeping = await self.events.get()
            if sleeping:
                async with self.service.lock:
                    try:
                        await asyncio.to_thread(self.controller.suspend)
                    except OSError as exc:
                        self.controller.degraded = f"State flush failed: {exc}"
                continue
            delay = 1
            while not self.closed and self.events.empty():
                try:
                    async with self.service.lock:
                        await asyncio.to_thread(self._recover)
                    if self.controller.startup_warning.startswith("Waiting for EC preparation:"):
                        self.controller.startup_warning = ""
                    if not self.closed and self.events.empty():
                        self.controller.lifecycle = ""
                    break
                except Exception as exc:
                    unsupported = isinstance(exc, HardwareError) and exc.code == ErrorCode.UNSUPPORTED_HARDWARE
                    if self.controller.restore_failed or unsupported:
                        self.controller.starting = False
                        if unsupported:
                            self.controller.degraded = str(exc)
                        # An actual write failed: fallback was attempted, never replay it in a loop.
                        if not self.closed and self.events.empty():
                            self.controller.lifecycle = ""
                        break
                    self.controller.startup_warning = f"Waiting for EC preparation: {exc}"
                try:
                    sleeping = await asyncio.wait_for(self.events.get(), delay)
                except TimeoutError:
                    delay = min(delay * 2, 30)
                else:
                    self.events.put_nowait(sleeping)
                    break

    def close(self):
        self.closed = True
        self.bus.remove_message_handler(self.handle)
