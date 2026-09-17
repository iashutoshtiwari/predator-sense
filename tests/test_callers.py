import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import patch

from support import BackendCase
from core.errors import ErrorCode
from core.profiles import CONTROL_REGISTERS, COOLBOOST_REGISTER, MODE_REGISTERS, FanChannel, FanMode
from core.state import load_coolboost_state, save_coolboost_state

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6 import QtWidgets
from ui import main_window


def load_script(name, relative_path):
    path = Path(__file__).resolve().parent.parent / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


service = load_script("background_service", "background_service.py")
diagnostics = load_script("collect_diagnostics", "scripts/collect_diagnostics.py")


class WindowTests(BackendCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        super().setUp()
        patcher = patch.object(main_window, "STATE_FILE", str(self.root / "state.json"))
        patcher.start()
        self.addCleanup(patcher.stop)
        warning = patch.object(QtWidgets.QMessageBox, "warning")
        self.warning = warning.start()
        self.addCleanup(warning.stop)

    def window(self):
        window = main_window.MainWindow(self.backend)
        self.addCleanup(window.close)
        return window

    def test_startup_reads_without_writes_and_shows_manual_speed(self):
        self.ec.data[MODE_REGISTERS[FanChannel.CPU]] = 0x5C
        self.ec.data[CONTROL_REGISTERS[FanChannel.CPU]] = 70
        window = self.window()
        self.assertTrue(window.cpu_manual.isChecked())
        self.assertEqual(window.verticalSlider.value(), 7)
        self.assertTrue(window.verticalSlider.isEnabled())
        self.assertFalse(window.global_auto.isChecked())
        self.assertEqual(self.ec.writes, [])

    def test_unknown_and_failed_startup_never_force_auto(self):
        self.ec.data[MODE_REGISTERS[FanChannel.CPU]] = 0x5D
        self.ec.read_override[MODE_REGISTERS[FanChannel.GPU]] = b""
        self.ec.data[COOLBOOST_REGISTER] = 2
        window = self.window()
        self.assertEqual(window.cpuFanMode, FanMode.UNKNOWN)
        self.assertEqual(window.gpuFanMode, FanMode.UNKNOWN)
        for button in (window.cpu_auto, window.cpu_manual, window.cpu_turbo, window.global_auto):
            self.assertFalse(button.isChecked())
        self.assertIsNone(window.cb)
        self.assertEqual(self.ec.writes, [])

    def test_mode_click_does_not_write_deselected_auto(self):
        window = self.window()
        window.cpu_turbo.click()
        self.assertEqual(self.ec.writes, [(MODE_REGISTERS[FanChannel.CPU], 0x58)])
        self.assertTrue(window.cpu_turbo.isChecked())
        self.assertFalse(window.global_auto.isChecked())
        window.cpu_auto.click()
        self.assertEqual(self.ec.writes[-1], (MODE_REGISTERS[FanChannel.CPU], 0x54))

    def test_global_controls_and_individual_recovery(self):
        window = self.window()
        window.global_turbo.click()
        self.assertEqual(
            self.ec.writes, [(MODE_REGISTERS[FanChannel.CPU], 0x58), (MODE_REGISTERS[FanChannel.GPU], 0x60)]
        )
        self.assertTrue(window.global_turbo.isChecked())
        window.cpu_auto.click()
        self.assertFalse(window.global_turbo.isChecked())
        window.global_auto.click()
        self.assertTrue(window.cpu_auto.isChecked())
        self.assertTrue(window.gpu_auto.isChecked())
        self.assertTrue(window.global_auto.isChecked())

    def test_manual_selection_and_slider_mapping(self):
        window = self.window()
        for manual, slider, channel in (
            (window.cpu_manual, window.verticalSlider, FanChannel.CPU),
            (window.gpu_manual, window.verticalSlider_2, FanChannel.GPU),
        ):
            manual.click()
            self.assertTrue(slider.isEnabled())
            slider.setValue(7)
            self.assertEqual(self.backend.get_manual_speed(channel), 70)
            self.assertEqual(self.backend.get_fan_mode(channel), FanMode.MANUAL)
        self.assertEqual(len(self.ec.writes), 4)

    def test_failed_write_restores_observed_selection_and_reports(self):
        window = self.window()
        self.ec.ignore_write = True
        window.cpu_turbo.click()
        self.assertTrue(window.cpu_auto.isChecked())
        self.assertFalse(window.cpu_turbo.isChecked())
        self.warning.assert_called_once()

    def test_coolboost_persists_only_verified_success(self):
        window = self.window()
        self.ec.ignore_write = True
        window.coolboost_checkbox.click()
        self.assertFalse((self.root / "state.json").exists())
        self.assertFalse(window.coolboost_checkbox.isChecked())
        self.ec.ignore_write = False
        window.coolboost_checkbox.click()
        self.assertEqual(json.loads((self.root / "state.json").read_text()), {"coolboost_enabled": True})

    def test_partial_global_failure_shows_actual_channel_states(self):
        window = self.window()
        self.ec.read_override[MODE_REGISTERS[FanChannel.GPU]] = b"\x00"
        window.global_turbo.click()
        self.assertTrue(window.cpu_turbo.isChecked())
        self.assertTrue(window.gpu_auto.isChecked())
        self.assertFalse(window.global_turbo.isChecked())
        self.warning.assert_called_once()


class ServiceStateTests(BackendCase):
    def test_service_uses_backend_and_skips_unchanged(self):
        service.apply_coolboost(self.backend, True)
        service.apply_coolboost(self.backend, True)
        self.assertEqual(self.ec.writes, [(COOLBOOST_REGISTER, 1)])

    def test_service_refuses_unknown_failed_and_unsupported_reads(self):
        self.ec.data[COOLBOOST_REGISTER] = 2
        self.assert_code(ErrorCode.MALFORMED_READ, lambda: service.apply_coolboost(self.backend, True))
        self.ec.read_override[COOLBOOST_REGISTER] = b""
        self.assert_code(ErrorCode.SHORT_READ, lambda: service.apply_coolboost(self.backend, True))
        self.product.write_text("Other")
        self.assert_code(ErrorCode.UNSUPPORTED_HARDWARE, lambda: service.apply_coolboost(self.backend, True))
        self.assertEqual(self.ec.writes, [])

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

    def test_failed_replace_retains_existing_state_and_cleans_temporary_file(self):
        state = self.root / "state.json"
        save_coolboost_state(True, state)
        with patch("core.state.os.replace", side_effect=OSError("simulated failure")):
            with self.assertRaises(OSError):
                save_coolboost_state(False, state)
        self.assertTrue(load_coolboost_state(state))
        self.assertEqual(
            sorted(path.name for path in self.root.iterdir()), ["bios_version", "product_name", "state.json"]
        )

    def test_diagnostics_uses_backend_reads_only(self):
        report = []
        with patch.object(diagnostics, "G3572EcBackend", return_value=self.backend):
            diagnostics.append_ec_registers(report, diagnostics.DEFAULT_EC_ADDRESSES)
        self.assertIn("0x13: 0", report)
        self.assertIn("0x21: firmware_auto", report)
        self.assertEqual(self.ec.writes, [])
        with self.assertRaises(SystemExit):
            diagnostics.parse_ec_addresses(["0xFF"])
        self.assertEqual(diagnostics.parse_ec_addresses(["0x13", "0x10"]), (0x13, 0x10))

    def test_diagnostics_reports_hardware_failure(self):
        self.ec.open_error = PermissionError(13, "fake failure")
        report = []
        with patch.object(diagnostics, "G3572EcBackend", return_value=self.backend):
            diagnostics.append_ec_registers(report, diagnostics.DEFAULT_EC_ADDRESSES)
        self.assertTrue(any("permission_denied" in line for line in report))
        self.assertEqual(self.ec.writes, [])
