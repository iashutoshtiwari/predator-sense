"""Unprivileged client/UI tests; no system bus and no backend in the GUI."""

import importlib.util
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

from support import BackendCase
from core.state import load_coolboost_state, save_coolboost_state
from service.client import ServiceClient, actionable_error
from service.protocol import ERROR_PREFIX
from service.telemetry_model import Availability, FIELDS, TelemetrySnapshot, observed
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PyQt6 import QtCore, QtWidgets
from ui.main_window import MainWindow


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.error = None
        self.responses = {
            "GetStatus": [True, "", ""],
            "GetFanState": ["firmware_auto", "auto", 50, 50],
            "GetCoolBoost": [0],
        }

    def call(self, member, args, callback):
        self.calls.append((member, args))
        if self.error:
            callback(None, *self.error)
        else:
            if member == "GetTelemetrySnapshot":
                ready, code, message = self.responses["GetStatus"]
                cpu, gpu, cp, gp = self.responses["GetFanState"]
                boost = self.responses["GetCoolBoost"][0]
                values = {
                    "cpu_mode": None if cpu == "unknown" else cpu,
                    "gpu_mode": None if gpu == "unknown" else gpu,
                    "cpu_manual_percent": None if cp < 0 else cp,
                    "gpu_manual_percent": None if gp < 0 else gp,
                    "coolboost": None if boost < 0 else bool(boost),
                }
                snapshot = TelemetrySnapshot(
                    time.time(), time.monotonic(), len(self.calls), "fake",
                    tuple(observed(n, values.get(n), "fake") for n in FIELDS), ready, code, message,
                )
                callback([snapshot.to_json()], "", "")
            else:
                callback(self.responses.get(member, []), "", "")


class ClientWindowTests(BackendCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        super().setUp()
        self.transport = FakeTransport()
        self.client = ServiceClient(transport=self.transport)
        self.addCleanup(self.client.stop)

    def window(self):
        window = MainWindow(self.client)
        self.addCleanup(window.close)
        self.client.refresh()
        return window

    def test_read_only_startup_and_client_api(self):
        window = self.window()
        self.assertTrue(window.cpu_auto.isChecked())
        self.assertEqual(self.transport.calls, [("GetTelemetrySnapshot", [])])
        for function, args, method in (
            (self.client.set_cpu_mode, ["turbo"], "SetCpuFanMode"),
            (self.client.set_gpu_mode, ["auto"], "SetGpuFanMode"),
            (self.client.set_cpu_manual_speed, [50], "SetCpuManualSpeed"),
            (self.client.set_gpu_manual_speed, [70], "SetGpuManualSpeed"),
            (self.client.set_coolboost, [True], "SetCoolBoost"),
            (self.client.set_global_auto, [], "SetGlobalAuto"),
            (self.client.set_global_turbo, [], "SetGlobalTurbo"),
        ):
            function(*args)
            self.assertIn((method, args), self.transport.calls)
        self.assertEqual(self.ec.writes, [])

    def test_daemon_absent_window_remains_open_and_can_recover(self):
        self.transport.error = ("org.freedesktop.DBus.Error.ServiceUnknown", "missing")
        window = self.window()
        self.assertFalse(window.cpu_auto.isEnabled())
        self.assertTrue(window.exit_button.isEnabled())
        self.assertIn("predator-sensed.service", window.status_label.text())
        self.transport.error = None
        self.client.refresh()
        self.assertTrue(window.cpu_auto.isEnabled())

    def test_starting_ec_unavailable_and_unsupported_states_disable_controls(self):
        window = self.window()
        for code in ("Starting", "unsupported_hardware", "ec_unavailable", "permission_denied"):
            self.transport.responses["GetStatus"] = [False, code, code]
            self.client.refresh()
            self.assertFalse(window.cpu_turbo.isEnabled())
            self.assertIn(code, window.status_label.text())

    def test_authorization_cancelled_is_actionable_without_crash(self):
        window = self.window()
        self.transport.error = (ERROR_PREFIX + "AuthorizationCancelled", "cancelled")
        window.cpu_turbo.click()
        self.assertIn("Authorization cancelled", window.status_label.text())
        self.assertFalse(self.client.busy)

    def test_failure_does_not_persist_user_state(self):
        window = self.window()
        self.transport.error = (ERROR_PREFIX + "verification_failed", "hardware rejected change")
        window.coolboost_checkbox.click()
        self.assertFalse((self.root / "state.json").exists())
        self.assertIn("hardware rejected", window.status_label.text())

    def test_slider_changes_are_coalesced(self):
        self.transport.responses["GetFanState"] = ["manual", "auto", 50, 50]
        window = self.window()
        for level in (6, 7, 8):
            window.verticalSlider.setValue(level)
        self.assertFalse(any(name == "SetCpuManualSpeed" for name, _ in self.transport.calls))
        self.assertTrue(window.manual_timers["cpu"].isActive())
        window.manual_timers["cpu"].stop()
        window._send_manual("cpu")
        self.assertEqual([args for name, args in self.transport.calls if name == "SetCpuManualSpeed"], [[80]])

    def test_unknown_state_does_not_select_auto_or_write(self):
        self.transport.responses["GetFanState"] = ["unknown", "unknown", -1, -1]
        self.transport.responses["GetCoolBoost"] = [-1]
        window = self.window()
        self.assertFalse(window.cpu_auto.isChecked())
        self.assertEqual(window.coolboost_checkbox.checkState(), QtCore.Qt.CheckState.PartiallyChecked)
        self.assertTrue(all(name.startswith("Get") for name, _ in self.transport.calls))

    def test_error_mapping(self):
        for name, expected in (
            ("org.freedesktop.DBus.Error.NoReply", "Timeout"),
            (ERROR_PREFIX + "NotAuthorized", "NotAuthorized"),
            (ERROR_PREFIX + "ec_unavailable", "ec_unavailable"),
        ):
            self.assertEqual(actionable_error(name, "detail")[0], expected)

    def test_refresh_started_before_mutation_cannot_publish_stale_data(self):
        pending = []
        self.transport.call = lambda member, args, callback: pending.append((member, callback))
        self.client.refresh()
        self.client.set_cpu_mode("turbo")
        self.assertTrue(self.client.busy)
        pending[0][1]([True, "", ""], "", "")
        self.assertFalse(self.client.snapshot["ready"])
        pending[1][1]([], "", "")
        self.assertEqual(pending[2][0], "GetTelemetrySnapshot")

    def test_one_snapshot_call_per_poll_and_bounded_client_history(self):
        updates = []
        self.client.telemetry_updated.connect(updates.append)
        for _ in range(300):
            self.client.refresh()
        self.assertEqual(len(updates), 300)
        self.assertEqual(len(self.client.history), 120)
        self.assertEqual(len(self.transport.calls), 300)
        self.assertTrue(all(name == "GetTelemetrySnapshot" for name, _ in self.transport.calls))
        self.assertIsNone(updates[-1].gpu_temp_c)

    def test_pending_read_is_not_duplicated_and_old_values_expire(self):
        self.client.refresh()
        pending = []
        self.transport.call = lambda member, args, callback: pending.append(callback)
        self.client.refresh()
        with patch("service.client.time.monotonic", return_value=time.monotonic() + 4):
            for _ in range(10):
                self.client.refresh()
        self.assertEqual(len(pending), 1)
        self.assertFalse(self.client.snapshot["ready"])
        self.assertEqual(self.client.telemetry.reading("cpu_mode").status, Availability.STALE)
        self.assertIsNone(self.client.telemetry.cpu_mode)

    def test_disconnected_values_are_none_and_polling_continues_during_authorization(self):
        self.transport.error = ("org.freedesktop.DBus.Error.ServiceUnknown", "missing")
        self.client.refresh()
        self.assertTrue(all(r.value is None for r in self.client.telemetry.readings))
        self.assertEqual(self.client.telemetry.reading("cpu_temp_c").status, Availability.BACKEND_DISCONNECTED)
        self.transport.error = None
        self.client.busy = True
        self.client.refresh()
        self.assertTrue(self.client.snapshot["ready"])
        self.assertTrue(self.client.busy)

    def test_malformed_snapshot_is_reported_without_crashing(self):
        self.transport.call = lambda member, args, callback: callback(["null"], "", "")
        self.client.refresh()
        self.assertEqual(self.client.snapshot["code"], "InvalidReply")
        self.assertFalse(self.client.refreshing)
        self.assertIsNone(self.client.telemetry.cpu_fan_rpm)

    def test_refresh_does_not_reset_pending_slider_choice(self):
        self.transport.responses["GetFanState"] = ["manual", "auto", 50, 50]
        window = self.window()
        window.verticalSlider.setValue(8)
        self.client.refresh()
        self.assertEqual(window.verticalSlider.value(), 8)
        self.assertTrue(window.manual_timers["cpu"].isActive())


class StateDiagnosticsTests(BackendCase):
    def test_state_schema_and_atomic_replacement(self):
        state = self.root / "saved" / "state.json"
        self.assertFalse(load_coolboost_state(state))
        save_coolboost_state(True, state)
        self.assertTrue(load_coolboost_state(state))
        save_coolboost_state(False, state)
        self.assertFalse(load_coolboost_state(state))
        for value in ("{", "[]", "null", '"yes"', '{"coolboost_enabled": "false"}', '{"coolboost_enabled": 1}'):
            state.write_text(value)
            self.assertFalse(load_coolboost_state(state))
        self.assertEqual(list(state.parent.iterdir()), [state])

    def test_failed_replace_retains_existing_state(self):
        state = self.root / "state.json"
        save_coolboost_state(True, state)
        with patch("core.state.os.replace", side_effect=OSError("simulated failure")):
            with self.assertRaises(OSError):
                save_coolboost_state(False, state)
        self.assertTrue(load_coolboost_state(state))

    def test_diagnostics_only_requests_daemon_data(self):
        path = Path(__file__).resolve().parent.parent / "scripts/collect_diagnostics.py"
        spec = importlib.util.spec_from_file_location("diagnostics", path)
        diagnostics = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(diagnostics)
        report = []
        with patch.object(diagnostics, "read_daemon_ec", AsyncMock(return_value={0x13: 2000})):
            diagnostics.append_ec_registers(report, (0x13,))
        self.assertIn("0x13: 2000", report)
        self.assertEqual(self.ec.writes, [])
