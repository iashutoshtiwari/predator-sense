"""Private-bus integration fixture only; never load this from production entry points."""

import asyncio
import sys

from dbus_next import Message, MessageType
from dbus_next.aio import MessageBus

from support import BackendCase
from service.controller import Controller
from service.daemon import ControlService, PolkitAuthorizer
from service.protocol import BUS_NAME


async def main():
    case = BackendCase()
    case.setUp()
    bus = await MessageBus(bus_address=sys.argv[1]).connect()
    controller = Controller(case.backend, state_file=case.root / "state.json", temperatures=lambda: [44000, -1])
    controller.starting = False

    def authority(message):
        if message.message_type != MessageType.METHOD_CALL:
            return None
        if message.interface == "org.freedesktop.PolicyKit1.Authority" and message.member == "CheckAuthorization":
            return Message.new_method_return(message, "(bba{ss})", [[sys.argv[2] == "allow", False, {}]])
        return None

    bus.add_message_handler(authority)
    await bus.request_name("org.freedesktop.PolicyKit1")
    service = ControlService(bus, controller, PolkitAuthorizer(bus))
    bus.add_message_handler(service.handle_message)
    await bus.request_name(BUS_NAME)
    print("READY", flush=True)
    await asyncio.Event().wait()


asyncio.run(main())
