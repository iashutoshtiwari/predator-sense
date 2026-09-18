"""Private-bus integration fixture only; never load this from production entry points."""

import asyncio
import sys
import time

from dbus_next import Message, MessageType
from dbus_next.aio import MessageBus

from support import BackendCase
from predator_sense.service.controller import Controller
from predator_sense.service.daemon import ControlService, PolkitAuthorizer
from predator_sense.service.protocol import BUS_NAME
from predator_sense.service.telemetry import TelemetryEngine
from predator_sense.service.telemetry_model import observed


async def main():
    case = BackendCase()
    case.setUp()
    bus = await MessageBus(bus_address=sys.argv[1]).connect()
    controller = Controller(case.backend, state_file=case.root / "state.json", temperatures=lambda: [44000, -1])
    controller.starting = False
    cpu = type("CPU", (), {"read": lambda self: observed("cpu_temp_c", 44.0, "fake coretemp")})()
    class FakeGpu:
        count = 0

        def read(self):
            self.count += 1
            if "stress" in sys.argv[3:]:
                if self.count % 17 == 0:
                    time.sleep(3.2)
                if self.count % 11 == 0:
                    raise OSError("simulated GPU read failure")
                return observed("gpu_temp_c", 48, "fake NVML")
            return observed("gpu_temp_c", None, "fake NVML")

    gpu = FakeGpu()
    controller.telemetry = TelemetryEngine(cpu, gpu, controller.sample_ec)
    sampling = asyncio.create_task(controller.telemetry.run())
    # READY means the initial cached sample is available, not just bus ownership.
    while not controller.telemetry.history:
        await asyncio.sleep(0.01)

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
    try:
        await asyncio.Event().wait()
    finally:
        sampling.cancel()
        await asyncio.gather(sampling, return_exceptions=True)


asyncio.run(main())
