"""Real Qt/dbus-next wire tests on an isolated unprivileged bus and fake EC."""

import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import time
import unittest
import uuid

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PyQt6 import QtCore, QtDBus, QtWidgets

from service.client import QtBusTransport, ServiceClient
from service.protocol import BUS_NAME, ERROR_PREFIX, INTERFACE, OBJECT_PATH


@unittest.skipUnless(shutil.which("dbus-daemon"), "private D-Bus integration requires dbus-daemon")
class PrivateBusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.bus_process = subprocess.Popen(
            ["dbus-daemon", "--session", "--nofork", "--print-address=1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self.stop_process, self.bus_process)
        self.assertTrue(select.select([self.bus_process.stdout], [], [], 5)[0], "private bus did not start")
        self.address = self.bus_process.stdout.readline().strip()
        self.connection_name = "predator-test-" + uuid.uuid4().hex
        self.connection = QtDBus.QDBusConnection.connectToBus(self.address, self.connection_name)
        self.addCleanup(QtDBus.QDBusConnection.disconnectFromBus, self.connection_name)
        self.assertTrue(self.connection.isConnected())
        self.transport = QtBusTransport(connection=self.connection)
        self.client = ServiceClient(transport=self.transport)

    @staticmethod
    def stop_process(process):
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()

    def start_fixture(self, authorization="allow"):
        fixture = Path(__file__).with_name("dbus_fixture.py")
        process = subprocess.Popen(
            [sys.executable, str(fixture), self.address, authorization],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self.stop_process, process)
        self.assertTrue(select.select([process.stdout], [], [], 5)[0], "fixture did not start")
        line = process.stdout.readline().strip()
        if line != "READY":
            process.terminate()
            _, errors = process.communicate(timeout=5)
            self.fail(f"fixture failed: {line} {errors}")
        return process

    def wait_until(self, predicate):
        deadline = time.monotonic() + 6
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 20)
            time.sleep(0.005)
        self.assertTrue(predicate(), str(self.client.snapshot))

    def test_real_client_read_mutate_and_polkit_wire(self):
        self.start_fixture()
        self.client.refresh()
        self.wait_until(lambda: self.client.snapshot.get("ready"))
        self.assertEqual(self.client.snapshot["cpu_mode"], "firmware_auto")
        self.client.set_cpu_manual_speed(70)
        self.wait_until(lambda: self.client.snapshot.get("cpu_manual") == 70)
        self.assertEqual(self.client.snapshot["cpu_mode"], "manual")
        result = []
        self.client.get_temperatures(lambda *args: result.append(args))
        self.wait_until(lambda: bool(result))
        self.assertEqual(result[0], ([44000, -1], "", ""))
        self.client.set_coolboost(True)
        self.wait_until(lambda: self.client.snapshot.get("coolboost") == 1)

    def test_real_polkit_denial_preserves_state(self):
        self.start_fixture("deny")
        failures = []
        self.client.failed.connect(lambda *args: failures.append(args))
        self.client.set_global_turbo()
        self.wait_until(lambda: bool(failures) and self.client.snapshot.get("ready"))
        self.assertEqual(failures[0][0], "NotAuthorized")
        self.assertEqual(self.client.snapshot["cpu_mode"], "firmware_auto")

    def test_daemon_absence_and_restart(self):
        self.client.refresh()
        self.wait_until(lambda: not self.client.refreshing)
        self.assertEqual(self.client.snapshot["code"], "DaemonUnavailable")
        process = self.start_fixture()
        self.client.refresh()
        self.wait_until(lambda: self.client.snapshot.get("ready"))
        self.stop_process(process)
        self.client.refresh()
        self.wait_until(lambda: not self.client.refreshing)
        self.assertFalse(self.client.snapshot["ready"])

    def test_unknown_and_wrongly_typed_wire_calls_are_rejected(self):
        self.start_fixture()
        for member, args, expected in (
            ("WriteRegister", [16, 1], "UnknownMethod"),
            ("SetCoolBoost", [1], "InvalidArgs"),
        ):
            message = QtDBus.QDBusMessage.createMethodCall(BUS_NAME, OBJECT_PATH, INTERFACE, member)
            message.setArguments(args)
            # Fixture runs in another process, so a bounded blocking call is safe here.
            response = self.connection.call(message, QtDBus.QDBus.CallMode.Block, 3000)
            self.assertEqual(response.errorName(), ERROR_PREFIX + expected)
