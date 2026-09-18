"""Unit tests for system hardware info discovery."""

import host_guard  # noqa: F401 -- install hardware isolation before application imports

import unittest
from unittest.mock import MagicMock, patch

from predator_sense.ui import system_info


class TestSystemInfo(unittest.TestCase):
    def setUp(self):
        # Reset cached values for clean testing
        system_info._CACHED_CPU = None
        system_info._CACHED_GPU = None

    def tearDown(self):
        system_info._CACHED_CPU = None
        system_info._CACHED_GPU = None

    def test_cpu_model_cleaning_and_fallback(self):
        sample_cpuinfo = """processor\t: 0
vendor_id\t: GenuineIntel
model name\t: Intel(R) Core(TM) i5-7300HQ CPU @ 2.50GHz
stepping\t: 9
"""
        with patch("builtins.open", unittest.mock.mock_open(read_data=sample_cpuinfo)):
            model = system_info.get_cpu_model()
            self.assertEqual(model, "Intel Core i5-7300HQ")

        # Verify caching
        with patch("builtins.open", side_effect=FileNotFoundError):
            self.assertEqual(system_info.get_cpu_model(), "Intel Core i5-7300HQ")

        # Test fallback when file missing
        system_info._CACHED_CPU = None
        with patch("builtins.open", side_effect=FileNotFoundError):
            self.assertEqual(system_info.get_cpu_model(), "Processor")

    def test_gpu_model_nvml_and_fallback(self):
        mock_pynvml = MagicMock()
        mock_pynvml.nvmlDeviceGetCount.return_value = 1
        mock_pynvml.nvmlDeviceGetHandleByIndex.return_value = "handle_0"
        mock_pynvml.nvmlDeviceGetName.return_value = b"NVIDIA GeForce GTX 1050 Ti"

        with patch.dict("sys.modules", {"pynvml": mock_pynvml}):
            gpu = system_info.get_gpu_model()
            self.assertEqual(gpu, "NVIDIA GeForce GTX 1050 Ti")

        # Test lspci fallback
        system_info._CACHED_GPU = None
        sample_lspci = """
00:02.0 VGA compatible controller: Intel Corporation Kaby Lake-H GT2 [HD Graphics 630] (rev 04)
01:00.0 VGA compatible controller: NVIDIA Corporation GP107M [GeForce GTX 1050 Ti Mobile] (rev a1)
"""
        with patch.dict("sys.modules", {"pynvml": None}), \
             patch("subprocess.check_output", return_value=sample_lspci):
            gpu = system_info.get_gpu_model()
            self.assertEqual(gpu, "NVIDIA GeForce GTX 1050 Ti Mobile")

        # Test ultimate fallback
        system_info._CACHED_GPU = None
        with patch.dict("sys.modules", {"pynvml": None}), \
             patch("subprocess.check_output", side_effect=FileNotFoundError):
            self.assertEqual(system_info.get_gpu_model(), "Graphics")




class ModelDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_window_appears_while_discovery_is_stalled(self):
        from threading import Event
        import time
        from PyQt6 import QtTest
        from dashboard_fixture import DashboardTransport, samples
        from predator_sense.service.client import ServiceClient
        from predator_sense.ui.main_window import MainWindow

        release, entered = Event(), Event()
        def discover():
            entered.set()
            release.wait(2)
            return "Simulated CPU", "Simulated GPU"
        discovery = system_info.ModelDiscovery(discover=discover)
        self.addCleanup(discovery.stop)
        self.addCleanup(release.set)
        before = time.monotonic()
        window = MainWindow(ServiceClient(transport=DashboardTransport(samples()[-1])), model_discovery=discovery)
        self.addCleanup(window.close)
        window.show()
        self.app.processEvents()
        self.assertLess(time.monotonic() - before, 0.5)
        self.assertTrue(entered.wait(1))
        self.assertTrue(window.isVisible())
        self.assertEqual(window.cards["gpu"].device_name_label.text(), "Graphics")
        release.set()
        QtTest.QTest.qWait(150)
        self.assertEqual(window.cards["gpu"].device_name_label.text(), "Simulated GPU")
        self.assertEqual(window.cards["cpu"].device_name_label.text(), "Simulated CPU")

    def test_discovery_is_shared_and_close_does_not_wait_for_worker(self):
        from threading import Event
        from unittest.mock import Mock
        release = Event()
        discover = Mock(side_effect=lambda: (release.wait(2), "Graphics"))
        self.addCleanup(release.set)
        with patch.object(system_info, "_NAMES_FUTURE", None), patch.object(system_info, "_model_names", discover):
            first, second = system_info.ModelDiscovery(), system_info.ModelDiscovery()
            first.start()
            second.start()
            self.assertIs(first.future, second.future)
            first.stop()
            second.stop()
            self.assertFalse(first.timer.isActive())
            self.assertFalse(second.timer.isActive())
            release.set()
            first.future.result(timeout=1)
            discover.assert_called_once()
