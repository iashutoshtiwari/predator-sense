"""Telemetry sources, timing, isolation, and client recovery without real sensors."""

from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import tempfile
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from support import BackendCase
from core.errors import ErrorCode, HardwareError
from service.controller import Controller
from service.sensors import CoretempSensor, NvmlSensor
from service.telemetry import SampleResult, SensorWorker, TelemetryEngine, next_deadline
from service.telemetry_model import Availability, EC_FIELDS, FIELDS, TelemetrySnapshot, observed


class CoretempTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sensor = CoretempSensor(self.root)

    def device(self, index, name, inputs):
        path = self.root / f"hwmon{index}"
        path.mkdir()
        (path / "name").write_text(name)
        for number, (label, value) in enumerate(inputs, 1):
            (path / f"temp{number}_input").write_text(str(value))
            if label is not None:
                (path / f"temp{number}_label").write_text(label)
        return path

    def test_variable_indices_and_package_preferred_over_hotter_core(self):
        for index in (0, 4, 19):
            with self.subTest(index=index):
                path = self.device(index, "coretemp", [("Core 0", 71000), ("Package id 0", 44000)])
                reading = self.sensor.read()
                self.assertEqual(reading.value, 44.0)
                self.assertIn("Package id 0", reading.source)
                path.rename(self.root / "removed")
                # Removed devices outside hwmon* must not be discovered.
                for file in (self.root / "removed").iterdir():
                    file.unlink()
                (self.root / "removed").rmdir()

    def test_fallback_hottest_readable_core_and_missing_labels(self):
        self.device(8, "coretemp", [("Package id 0", "broken"), ("Core 0", 41000), (None, 53500)])
        self.assertEqual(self.sensor.read().value, 53.5)

    def test_missing_coretemp_does_not_use_unrelated_temperature(self):
        self.device(2, "acpitz", [(None, 60000)])
        reading = self.sensor.read()
        self.assertIsNone(reading.value)
        self.assertEqual(reading.status, Availability.UNAVAILABLE)

    def test_permission_failure_and_invalid_temperature(self):
        with patch.object(Path, "iterdir", side_effect=PermissionError("denied")):
            self.assertEqual(self.sensor.read().status, Availability.PERMISSION_DENIED)
        self.device(3, "coretemp", [("Package id 0", 200000)])
        self.assertEqual(self.sensor.read().status, Availability.SENSOR_FAILED)

    def test_zero_is_valid_and_renumbering_is_rediscovered(self):
        path = self.device(1, "coretemp", [("Package id 0", 0)])
        self.assertEqual(self.sensor.read().value, 0.0)
        path.rename(self.root / "hwmon12")
        self.assertIn("hwmon12", self.sensor.read().source)


class NvmlError(Exception):
    def __init__(self, value):
        self.value = value
        super().__init__(f"fake NVML error {value}")


class NvmlTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.nvml = SimpleNamespace(
            nvmlInit=Mock(), nvmlShutdown=Mock(), nvmlDeviceGetCount=Mock(return_value=1),
            nvmlDeviceGetHandleByIndex=Mock(return_value="handle"),
            nvmlDeviceGetName=Mock(return_value=b"NVIDIA GeForce GTX 1050 Ti"),
            nvmlDeviceGetTemperature=Mock(return_value=48), NVML_TEMPERATURE_GPU=0,
            NVML_ERROR_NO_PERMISSION=4, NVML_ERROR_GPU_IS_LOST=15, NVML_ERROR_LIBRARY_NOT_FOUND=12,
        )
        self.loader = Mock(return_value=self.nvml)
        self.sensor = NvmlSensor(loader=self.loader, clock=lambda: self.now)
        self.addCleanup(self.sensor.close)

    def test_binding_missing_and_driver_library_missing(self):
        self.loader.side_effect = ModuleNotFoundError("pynvml")
        reading = self.sensor.read()
        self.assertIsNone(reading.value)
        self.assertEqual(reading.status, Availability.UNAVAILABLE)
        self.sensor.read()
        self.loader.assert_called_once_with("pynvml")
        self.now += 5
        self.loader.side_effect = None
        self.nvml.nvmlInit.side_effect = NvmlError(12)
        self.assertEqual(self.sensor.read().status, Availability.UNAVAILABLE)

    def test_initializes_once_and_uses_cached_handle(self):
        for _ in range(10):
            self.assertEqual(self.sensor.read().value, 48)
        self.nvml.nvmlInit.assert_called_once()
        self.nvml.nvmlDeviceGetCount.assert_called_once()
        self.sensor.close()
        self.nvml.nvmlShutdown.assert_called_once()

    def test_disappearing_gpu_retries_and_recovers(self):
        self.assertEqual(self.sensor.read().value, 48)
        self.nvml.nvmlDeviceGetTemperature.side_effect = NvmlError(15)
        self.assertEqual(self.sensor.read().status, Availability.UNAVAILABLE)
        self.assertIsNone(self.sensor.handle)
        self.nvml.nvmlDeviceGetTemperature.side_effect = None
        self.assertIsNone(self.sensor.read().value)
        self.now += 5
        self.assertEqual(self.sensor.read().value, 48)
        self.assertEqual(self.nvml.nvmlInit.call_count, 2)

    def test_permission_and_other_failures_are_distinct(self):
        for code, expected in ((4, Availability.PERMISSION_DENIED), (999, Availability.SENSOR_FAILED)):
            self.now += 5
            self.nvml.nvmlDeviceGetTemperature.side_effect = NvmlError(code)
            self.assertEqual(self.sensor.read().status, expected)

    def test_wrong_gpu_is_not_used_and_zero_is_not_missing(self):
        self.nvml.nvmlDeviceGetName.return_value = "NVIDIA GeForce RTX 4090"
        self.assertIsNone(self.sensor.read().value)
        self.nvml.nvmlDeviceGetTemperature.assert_not_called()
        self.now += 5
        self.nvml.nvmlDeviceGetName.return_value = "GeForce GTX 1050 Ti"
        self.nvml.nvmlDeviceGetTemperature.return_value = 0
        self.assertEqual(self.sensor.read().value, 0)


class SnapshotTests(unittest.TestCase):
    def test_immutable_roundtrip_and_missing_values_are_null(self):
        snapshot = TelemetrySnapshot.empty()
        self.assertEqual(TelemetrySnapshot.from_json(snapshot.to_json()), snapshot)
        self.assertTrue(all(r.value is None for r in snapshot.readings))
        self.assertIsNone(snapshot.cpu_temp_c)
        with self.assertRaises(FrozenInstanceError):
            snapshot.sequence = 5
        with self.assertRaises(FrozenInstanceError):
            snapshot.readings[0].value = 5
        self.assertIn('"value":null', snapshot.to_json())

    def test_stale_retains_dated_reading_but_no_fresh_value(self):
        snapshot = TelemetrySnapshot.empty()
        readings = tuple(observed(n, 40.0 if n == "cpu_temp_c" else None, "fake", now=10) for n in FIELDS)
        snapshot = replace(snapshot, readings=readings, ready=True).aged(13)
        self.assertIsNone(snapshot.cpu_temp_c)
        self.assertEqual(snapshot.reading("cpu_temp_c").value, 40)
        self.assertEqual(snapshot.reading("cpu_temp_c").status, Availability.STALE)
        self.assertFalse(snapshot.ready)

    def test_malformed_wire_data_is_rejected(self):
        data = json.loads(TelemetrySnapshot.empty().to_json())
        for payload in ("null", "[]", "{}", "x", "x" * 32769, json.dumps({**data, "version": 2})):
            with self.subTest(payload=payload[:20]), self.assertRaises(ValueError):
                TelemetrySnapshot.from_json(payload)
        for name, value in (("coolboost", 0), ("cpu_temp_c", float("nan")), ("cpu_fan_rpm", -1),
                            ("cpu_temp_c", 10 ** 1000)):
            with self.assertRaises(ValueError):
                observed(name, value, "fake")


class ImmediateWorker:
    """Deterministic worker seam: test schedule/history without sleeping for minutes."""
    def __init__(self, name, read, close=None):
        self.read = read
        self.pending = False
        self.stalled = False
        self.closed = False
        self.error = None

    def submit(self):
        self.pending = True

    def take(self):
        if not self.pending or self.stalled:
            return None
        self.pending = False
        if self.error:
            raise self.error
        return self.read()

    def close(self):
        self.closed = True


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        cpu = SimpleNamespace(read=lambda: observed("cpu_temp_c", 45.0, "fake", now=self.now))
        gpu = SimpleNamespace(read=lambda: observed("gpu_temp_c", 49, "fake", now=self.now))
        def ec():
            return SampleResult(tuple(observed(n, None, "fake", now=self.now) for n in EC_FIELDS), True)
        self.engine = TelemetryEngine(cpu, gpu, ec, clock=lambda: self.now, worker_factory=ImmediateWorker)
        self.addCleanup(self.engine.close)
        self.engine.start_samples()

    def test_five_minutes_of_history_is_bounded(self):
        for second in range(1, 301):
            self.now = float(second)
            snapshot = self.engine.tick()
            self.assertEqual(snapshot.sequence, second)
            self.assertEqual(snapshot.monotonic_timestamp, second)
            self.assertEqual(snapshot.cpu_temp_c, 45)
            self.assertLessEqual(len(self.engine.history), 120)
        self.assertEqual(len(self.engine.history), 120)
        self.assertEqual(self.engine.history[0].sequence, 181)

    def test_slow_gpu_and_failed_cpu_do_not_erase_fan_state(self):
        self.engine.tick()
        self.engine.workers["gpu"].stalled = True
        self.engine.workers["cpu"].error = PermissionError("denied")
        self.now = 4
        snapshot = self.engine.tick()
        self.assertEqual(snapshot.reading("gpu_temp_c").status, Availability.STALE)
        self.assertIsNone(snapshot.gpu_temp_c)
        self.assertEqual(snapshot.reading("cpu_temp_c").status, Availability.PERMISSION_DENIED)
        self.assertTrue(snapshot.ready)
        self.engine.workers["gpu"].stalled = False
        self.assertEqual(self.engine.tick().gpu_temp_c, 49)

    def test_stalled_initial_read_becomes_stale(self):
        self.engine.workers["gpu"].stalled = True
        self.now = 4
        self.assertEqual(self.engine.tick().reading("gpu_temp_c").status, Availability.STALE)

    def test_monotonic_deadlines_skip_delays_without_drift_or_bursts(self):
        deadline = 1.0
        for _ in range(300):
            deadline = next_deadline(deadline, deadline + 0.04)
        self.assertEqual(deadline, 301.0)
        self.assertEqual(next_deadline(10, 14.2), 15.0)
        self.assertEqual(next_deadline(10, 11), 12.0)
        self.assertEqual(next_deadline(10, 9.99), 11.0)

    def test_worker_does_not_overlap_or_queue_retries(self):
        entered, release = Event(), Event()
        calls = []
        def read():
            calls.append(1)
            entered.set()
            release.wait(2)
            return "sample"
        cleanup = Mock()
        worker = SensorWorker("blocked-test", read, cleanup)
        self.addCleanup(worker.close)
        self.addCleanup(release.set)
        worker.submit()
        self.assertTrue(entered.wait(1))
        for _ in range(100):
            worker.submit()
            self.assertIsNone(worker.take())
        self.assertEqual(len(calls), 1)
        self.assertEqual(worker.queue.qsize(), 0)
        before = time.monotonic()
        worker.close()
        self.assertLess(time.monotonic() - before, 0.1)
        release.set()
        worker.thread.join(1)
        self.assertFalse(worker.thread.is_alive())
        cleanup.assert_called_once()


class EcSamplingTests(BackendCase):
    def test_partial_ec_failure_preserves_successes_without_writes(self):
        controller = Controller(self.backend, state_file=self.root / "state.json")
        controller.starting = False
        with patch.object(self.backend, "get_cpu_fan_rpm", side_effect=HardwareError(ErrorCode.SHORT_READ, "short")):
            result = controller.sample_ec()
        readings = {r.name: r for r in result.readings}
        self.assertEqual(readings["cpu_fan_rpm"].status, Availability.SENSOR_FAILED)
        self.assertIsNone(readings["cpu_fan_rpm"].value)
        self.assertEqual(readings["gpu_fan_rpm"].value, 0)
        self.assertEqual(readings["cpu_mode"].value, "firmware_auto")
        self.assertIs(readings["coolboost"].value, False)
        self.assertEqual(self.ec.writes, [])

    def test_unsupported_identity_and_permission_denied(self):
        controller = Controller(self.backend, state_file=self.root / "state.json")
        controller.starting = False
        error = HardwareError(ErrorCode.PERMISSION_DENIED, "no")
        with patch.object(self.backend, "get_gpu_fan_mode", side_effect=error):
            result = controller.sample_ec()
        reading = next(r for r in result.readings if r.name == "gpu_mode")
        self.assertEqual(reading.status, Availability.PERMISSION_DENIED)
        self.product.write_text("Predator G3-573")
        result = controller.sample_ec()
        self.assertFalse(result.ready)
        self.assertTrue(all(r.value is None for r in result.readings))
