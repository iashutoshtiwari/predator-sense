"""Unit tests for system hardware info discovery."""

from pathlib import Path
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
        sample_lspci = """00:02.0 VGA compatible controller: Intel Corporation Kaby Lake-H GT2 [HD Graphics 630] (rev 04)
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


if __name__ == "__main__":
    unittest.main()
