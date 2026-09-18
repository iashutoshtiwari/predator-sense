"""Native dashboard behavior against an injected transport, never a system bus."""

from dataclasses import replace
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6 import QtCore, QtGui, QtTest, QtWidgets

from dashboard_fixture import DashboardTransport, missing, samples
from service.client import ServiceClient
from service.telemetry_model import observed
from ui.main_window import MainWindow
from ui.theme import APP_ID, apply_theme


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        apply_theme(cls.app)

    def setUp(self):
        self.history = samples()
        self.transport = DashboardTransport(self.history[-1])
        self.client = ServiceClient(transport=self.transport)
        self.client.history.extend(self.history)
        self.window = MainWindow(self.client)
        self.window.show()
        self.client.refresh()
        self.client.stop()
        self.app.processEvents()
        self.addCleanup(self.window.close)

    def test_all_four_graphs_and_live_values_with_no_writes(self):
        for channel, card in self.window.cards.items():
            self.assertIn("°C", card.temperature.text())
            self.assertNotEqual(card.rpm.text(), "—")
            self.assertEqual(card.mode.text(), "AUTO")
            self.assertEqual(len(card.graphs), 2)
            self.assertTrue(all(1 <= len(graph.points) <= 60 for graph in card.graphs))
        self.assertEqual(self.window.bios_status.text(), "BIOS V1.22")
        self.assertFalse(self.window.notice.isVisible())
        self.assertTrue(all(method.startswith("Get") for method, _ in self.transport.calls))

    def test_unavailable_and_zero_are_distinct(self):
        snapshot = missing(self.transport.snapshot, "gpu_temp_c", "gpu_fan_rpm")
        snapshot = replace(snapshot, readings=tuple(
            observed(r.name, 0, "fake") if r.name in ("cpu_temp_c", "cpu_fan_rpm") else r
            for r in snapshot.readings
        ))
        self.transport.snapshot = snapshot
        self.client.refresh()
        cpu, gpu = self.window.cards.values()
        self.assertEqual(cpu.temperature.text(), "0°C")
        self.assertEqual(cpu.rpm.text(), "0")
        self.assertEqual(gpu.temperature.text(), "—")
        self.assertEqual(gpu.rpm.text(), "—")
        self.assertIn("unavailable", gpu.availability.text())

    def test_graphs_keep_real_gaps_and_use_sixty_seconds(self):
        graph = self.window.cards["cpu"].graphs[0]
        graph.points = ((1, 40), (2, None), (3, 42), (7, 45), (8, 0))
        self.assertEqual(graph.segments(), [[(1, 40)], [(3, 42)], [(7, 45), (8, 0)]])
        graph.set_history(samples(120), self.history[-1].monotonic_timestamp)
        self.assertLessEqual(len(graph.points), 61)

    def test_slider_drag_commits_once_and_keyboard_is_debounced(self):
        self.transport.snapshot = replace(self.transport.snapshot, readings=tuple(
            observed(r.name, "manual", "fake") if r.name == "cpu_mode" else r
            for r in self.transport.snapshot.readings
        ))
        self.client.refresh()
        slider = self.window.verticalSlider
        self.assertTrue(slider.isEnabled())
        slider.setSliderDown(True)
        for level in (6, 7, 8):
            slider.setValue(level)
        QtTest.QTest.qWait(280)
        self.assertFalse(any(m == "SetCpuManualSpeed" for m, _ in self.transport.calls))
        self.assertEqual(self.window.percent_labels["cpu"].text(), "80%")
        slider.setSliderDown(False)
        self.assertEqual([a for m, a in self.transport.calls if m == "SetCpuManualSpeed"], [[80]])
        slider.setFocus()
        QtTest.QTest.keyClick(slider, QtCore.Qt.Key.Key_Right)
        self.assertTrue(self.window.manual_timers["cpu"].isActive())

    def test_keyboard_controls_and_coolboost_semantics(self):
        self.window.global_turbo.setFocus()
        QtTest.QTest.keyClick(self.window.global_turbo, QtCore.Qt.Key.Key_Space)
        self.assertIn(("SetGlobalTurbo", []), self.transport.calls)
        self.transport.snapshot = replace(self.transport.snapshot, readings=tuple(
            observed(r.name, "turbo", "fake") if r.name.endswith("_mode") else r
            for r in self.transport.snapshot.readings
        ))
        self.client.refresh()
        self.assertFalse(self.window.verticalSlider.isEnabled())
        self.assertFalse(self.window.coolboost_checkbox.isEnabled())
        self.assertEqual(self.window.boost_state.text(), "ON")
        self.assertIn("Auto", self.window.boost_hint.text())

    def test_outage_and_authentication_keep_actionable_banner(self):
        self.client.busy = True
        self.client.busy_changed.emit(True)
        self.assertTrue(self.window.notice.isVisible())
        self.assertIn("authorization", self.window.status_label.text())
        self.client.busy = False
        self.transport.error = "org.freedesktop.DBus.Error.ServiceUnknown"
        self.client.refresh()
        self.assertEqual(self.window.connection_badge.text(), "OFFLINE")
        self.assertTrue(self.window.retry_button.isVisible())
        self.assertIn("predator-sensed.service", self.window.status_label.text())
        self.assertEqual(self.window.hardware_status.text(), "HARDWARE —")

    def test_identity_read_once_per_epoch_and_widget_tree_stable(self):
        count = len(self.window.findChildren(QtWidgets.QWidget))
        for _ in range(120):
            self.client.refresh()
        self.assertEqual(len(self.window.findChildren(QtWidgets.QWidget)), count)
        self.assertEqual(sum(m == "GetHardwareIdentity" for m, _ in self.transport.calls), 1)
        self.transport.snapshot = replace(self.transport.snapshot, epoch="restarted")
        self.client.refresh()
        self.assertEqual(sum(m == "GetHardwareIdentity" for m, _ in self.transport.calls), 2)

    def test_resizable_layout_and_packaged_identity(self):
        self.assertNotEqual(self.window.minimumSize(), self.window.maximumSize())
        self.window.resize(760, 480)
        self.app.processEvents()
        scroll = self.window.findChild(QtWidgets.QScrollArea)
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
        root = Path(__file__).resolve().parent.parent
        desktop = (root / "packaging/predator-sense.desktop").read_text()
        self.assertIn("Icon=" + APP_ID, desktop)
        recipe = (root / "PKGBUILD").read_text()
        self.assertIn(APP_ID + ".desktop", recipe)
        self.assertNotIn("install -m644 fonts/", recipe)
        self.assertTrue((root / "assets/predator-sense.svg").exists())

    def test_larger_fonts_keep_controls_reachable(self):
        self.window.resize(760, 480)
        for widget in self.window.findChildren(QtWidgets.QWidget):
            font = widget.font()
            font.setPointSizeF(font.pointSizeF() * 1.4)
            widget.setFont(font)
        self.app.processEvents()
        self.window._adapt_layout()
        self.app.processEvents()
        scroll = self.window.findChild(QtWidgets.QScrollArea)
        scroll.ensureWidgetVisible(self.window.exit_button)
        self.app.processEvents()
        self.assertFalse(self.window.exit_button.visibleRegion().isEmpty())
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)

    def test_fractional_and_double_scale_renders(self):
        renderer = Path(__file__).with_name("render_dashboard.py")
        for scale, expected_width in (("1.25", 1275), ("2", 2040)):
            with self.subTest(scale=scale), tempfile.TemporaryDirectory() as destination:
                result = subprocess.run(
                    [sys.executable, str(renderer), destination], capture_output=True, text=True, timeout=15,
                    env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_SCALE_FACTOR": scale},
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                image = QtGui.QImage(str(Path(destination) / "live.png"))
                self.assertEqual(image.width(), expected_width)
                self.assertFalse(image.isNull())

    def test_identity_waits_for_daemon_initialization(self):
        initial_calls = sum(m == "GetHardwareIdentity" for m, _ in self.transport.calls)
        self.transport.snapshot = replace(self.transport.snapshot, epoch="starting", ready=False, code="Starting")
        self.client.refresh()
        self.assertEqual(sum(m == "GetHardwareIdentity" for m, _ in self.transport.calls), initial_calls)
        self.transport.snapshot = replace(self.transport.snapshot, ready=True, code="")
        self.client.refresh()
        self.assertEqual(sum(m == "GetHardwareIdentity" for m, _ in self.transport.calls), initial_calls + 1)
