"""End-to-end integration tests for UI and ServiceClient with a simulated backend.

Validates all 9 Phase 8 scenarios:
1. Auto mode
2. Manual fan speed
3. Turbo mode
4. CoolBoost toggle
5. Temperature changes
6. Fan RPM changes
7. Daemon disconnect
8. Daemon reconnect
9. Authorization failure
"""

import os
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PyQt6 import QtCore, QtWidgets

from support import BackendCase
from dashboard_fixture import FakeModelDiscovery
from predator_sense.service.client import ServiceClient
from predator_sense.service.protocol import ERROR_PREFIX
from predator_sense.service.telemetry_model import FIELDS, TelemetrySnapshot, observed
from predator_sense.ui.main_window import MainWindow


class SimTransport:
    def __init__(self):
        self.calls = []
        self.error = None
        self.coolboost = 0
        self.cpu_mode = "auto"
        self.gpu_mode = "auto"
        self.cpu_manual = 50
        self.gpu_manual = 50
        self.cpu_temp = 45.0
        self.gpu_temp = 55.0
        self.cpu_rpm = 2400
        self.gpu_rpm = 2600
        self.ready = True
        self.code = ""
        self.message = ""

    def call(self, member, args, callback):
        self.calls.append((member, args))
        if self.error:
            callback(None, *self.error)
            return

        if member == "GetHardwareIdentity":
            callback(["Predator G3-572", "V1.22", True, True], "", "")
        elif member == "GetStatus":
            callback([self.ready, self.code, self.message], "", "")
        elif member == "GetFanState":
            callback([self.cpu_mode, self.gpu_mode, self.cpu_manual, self.gpu_manual], "", "")
        elif member == "GetCoolBoost":
            callback([self.coolboost], "", "")
        elif member == "GetTelemetrySnapshot":
            values = {
                "cpu_temp_c": self.cpu_temp,
                "gpu_temp_c": self.gpu_temp,
                "cpu_fan_rpm": self.cpu_rpm,
                "gpu_fan_rpm": self.gpu_rpm,
                "cpu_mode": self.cpu_mode,
                "gpu_mode": self.gpu_mode,
                "cpu_manual_percent": self.cpu_manual if self.cpu_mode == "manual" else None,
                "gpu_manual_percent": self.gpu_manual if self.gpu_mode == "manual" else None,
                "coolboost": bool(self.coolboost),
            }
            snapshot = TelemetrySnapshot(
                time.time(),
                time.monotonic(),
                len(self.calls),
                "sim-epoch-1",
                tuple(observed(n, values.get(n), "sim") for n in FIELDS),
                self.ready,
                self.code,
                self.message,
            )
            callback([snapshot.to_json()], "", "")
        elif member == "SetCpuFanMode":
            self.cpu_mode = args[0]
            callback([], "", "")
        elif member == "SetGpuFanMode":
            self.gpu_mode = args[0]
            callback([], "", "")
        elif member == "SetCpuManualSpeed":
            self.cpu_mode = "manual"
            self.cpu_manual = args[0]
            callback([], "", "")
        elif member == "SetGpuManualSpeed":
            self.gpu_mode = "manual"
            self.gpu_manual = args[0]
            callback([], "", "")
        elif member == "SetCoolBoost":
            self.coolboost = 1 if args[0] else 0
            callback([], "", "")
        elif member == "SetGlobalAuto":
            self.cpu_mode = "auto"
            self.gpu_mode = "auto"
            callback([], "", "")
        elif member == "SetGlobalTurbo":
            self.cpu_mode = "turbo"
            self.gpu_mode = "turbo"
            callback([], "", "")
        else:
            callback([], "", "")


class UiIntegrationTests(BackendCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        super().setUp()
        self.transport = SimTransport()
        self.client = ServiceClient(transport=self.transport)
        self.window = MainWindow(self.client, model_discovery=FakeModelDiscovery())
        self.client.refresh()
        self.addCleanup(self.window.close)
        self.addCleanup(self.client.stop)

    def test_scenario_1_auto_mode(self):
        # Start in manual, then click auto
        self.transport.cpu_mode = "manual"
        self.client.refresh()
        self.assertTrue(self.window.cpu_manual.isChecked())

        # Click Auto
        self.window.cpu_auto.click()
        self.assertIn(("SetCpuFanMode", ["auto"]), self.transport.calls)
        self.client.refresh()
        self.assertTrue(self.window.cpu_auto.isChecked())
        self.assertFalse(self.window.verticalSlider.isEnabled())

    def test_scenario_2_manual_mode(self):
        # Click Manual mode
        self.window.cpu_manual.click()
        self.assertIn(("SetCpuFanMode", ["manual"]), self.transport.calls)
        self.client.refresh()
        self.assertTrue(self.window.cpu_manual.isChecked())
        self.assertTrue(self.window.verticalSlider.isEnabled())

        # Adjust slider to level 8 (80%)
        self.window.verticalSlider.setValue(8)
        self.window.manual_timers["cpu"].stop()
        self.window._send_manual("cpu")
        self.assertIn(("SetCpuManualSpeed", [80]), self.transport.calls)
        self.client.refresh()
        self.assertEqual(self.window.verticalSlider.value(), 8)

    def test_scenario_3_turbo_mode(self):
        # Click Turbo
        self.window.cpu_turbo.click()
        self.assertIn(("SetCpuFanMode", ["turbo"]), self.transport.calls)
        self.client.refresh()
        self.assertTrue(self.window.cpu_turbo.isChecked())
        self.assertFalse(self.window.verticalSlider.isEnabled())

    def test_scenario_4_coolboost_toggle(self):
        # CoolBoost requires at least one fan in Auto
        self.transport.cpu_mode = "auto"
        self.transport.coolboost = 0
        self.client.refresh()

        self.window.coolboost_checkbox.click()
        self.assertIn(("SetCoolBoost", [True]), self.transport.calls)
        self.client.refresh()
        self.assertEqual(self.window.coolboost_checkbox.checkState(), QtCore.Qt.CheckState.Checked)

    def test_scenario_5_temperature_changes(self):
        # Simulate temp rise from 45C to 82C
        self.transport.cpu_temp = 82.0
        self.transport.gpu_temp = 74.0
        self.client.refresh()

        # Telemetry card should render the new reading
        self.assertEqual(self.client.telemetry.cpu_temp_c, 82.0)
        self.assertEqual(self.client.telemetry.gpu_temp_c, 74.0)
        self.assertIn("82", self.window.cards["cpu"].temperature.text())
        self.assertIn("74", self.window.cards["gpu"].temperature.text())

    def test_scenario_6_fan_rpm_changes(self):
        # Simulate fan spin-up
        self.transport.cpu_rpm = 4500
        self.transport.gpu_rpm = 4800
        self.client.refresh()

        self.assertEqual(self.client.telemetry.cpu_fan_rpm, 4500)
        self.assertEqual(self.client.telemetry.gpu_fan_rpm, 4800)
        self.assertIn("4,500", self.window.cards["cpu"].rpm.text())
        self.assertIn("4,800", self.window.cards["gpu"].rpm.text())

    def test_scenario_7_daemon_disconnect(self):
        # Daemon disappears
        self.transport.error = ("org.freedesktop.DBus.Error.ServiceUnknown", "daemon terminated")
        self.client.refresh()

        self.assertFalse(self.window.cpu_auto.isEnabled())
        self.assertFalse(self.window.cpu_turbo.isEnabled())
        self.assertFalse(self.window.coolboost_checkbox.isEnabled())
        self.assertIn("predator-sensed.service", self.window.status_label.text())

    def test_scenario_8_daemon_reconnect(self):
        # Disconnect then reconnect
        self.transport.error = ("org.freedesktop.DBus.Error.ServiceUnknown", "daemon terminated")
        self.client.refresh()
        self.assertFalse(self.window.cpu_auto.isEnabled())

        # Recover
        self.transport.error = None
        self.client.refresh()
        self.assertTrue(self.window.cpu_auto.isEnabled())
        self.assertNotIn("predator-sensed.service", self.window.status_label.text())

    def test_scenario_9_authorization_failure(self):
        # Polkit denial must show actionable message without modifying hardware or crashing
        self.transport.error = (ERROR_PREFIX + "NotAuthorized", "Authentication failed")
        self.window.cpu_turbo.click()

        self.assertIn("Authorization denied", self.window.status_label.text())
        self.assertFalse(self.client.busy)
        # Previous state preserved
        self.transport.error = None
        self.client.refresh()
        self.assertTrue(self.window.cpu_auto.isChecked())
