"""All tests redirect logging and DMI/EC before any hardware-facing action."""

import atexit
from contextlib import contextmanager
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from predator_sense.core import logger as logger_config

_LOGS = tempfile.TemporaryDirectory(prefix="predator-sense-test-logs-")
logger_config.LOG_PATH = Path(_LOGS.name) / "app.log"
atexit.register(_LOGS.cleanup)

from predator_sense.core import env_checks, hardware  # noqa: E402 -- redirect logging before hardware imports
from predator_sense.core.hardware import G3572EcBackend  # noqa: E402
from predator_sense.core.profiles import (  # noqa: E402
    CONTROL_REGISTERS,
    FanChannel,
)


class FakeEC:
    def __init__(self):
        self.data = bytearray(256)
        for channel in FanChannel:
            self.data[CONTROL_REGISTERS[channel]] = 50
        self.writes = []
        self.events = []
        self.active = 0
        self.max_active = 0
        self.delay = 0
        self.read_override = {}
        self.write_error = None
        self.open_error = None
        self.ignore_write = False
        self.write_count = 1
        self.write_denied = False

    @contextmanager
    def transaction(self, write=False):
        if self.open_error:
            raise self.open_error
        if write and self.write_denied:
            raise PermissionError(13, "fake write access denied")
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            self.events.append((threading.get_ident(), "begin", write))
            if self.delay:
                time.sleep(self.delay)
            yield self
        finally:
            self.events.append((threading.get_ident(), "end", write))
            self.active -= 1

    def read(self, address, size):
        self.events.append((threading.get_ident(), "read", address))
        if address in self.read_override:
            result = self.read_override[address]
            if isinstance(result, Exception):
                raise result
            return result
        return bytes(self.data[address : address + size])

    def write(self, address, value):
        self.events.append((threading.get_ident(), "write", address))
        if self.write_error:
            raise self.write_error
        self.writes.append((address, value))
        if not self.ignore_write and self.write_count == 1:
            self.data[address] = value
        return self.write_count


class BackendCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="predator-sense-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.product = self.root / "product_name"
        self.bios = self.root / "bios_version"
        self.product.write_text("Predator G3-572\n")
        self.bios.write_text("V1.22\n")
        for name, value in (("DMI_PRODUCT_NAME_PATH", self.product), ("DMI_BIOS_VERSION_PATH", self.bios)):
            patcher = patch.object(env_checks, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for module in (hardware, env_checks):
            patcher = patch.object(module, "EC_IO_FILE", str(self.root / "unused-ec"))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.ec = FakeEC()
        self.backend = G3572EcBackend(_transport=self.ec)

    def assert_code(self, code, operation):
        from predator_sense.core.errors import HardwareError

        with self.assertRaises(HardwareError) as result:
            operation()
        self.assertEqual(result.exception.code, code)
        return result.exception
