import errno
import fcntl
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from support import BackendCase
from predator_sense.core import env_checks, hardware
from predator_sense.core.errors import ErrorCode
from predator_sense.core.hardware import G3572EcBackend
from predator_sense.core.profiles import (
    CONTROL_REGISTERS,
    COOLBOOST_REGISTER,
    MODE_REGISTERS,
    MODE_VALUES,
    RPM_REGISTERS,
    FanChannel,
    FanMode,
)


class IdentityTests(BackendCase):
    def test_normalized_product_and_bios(self):
        self.product.write_text(" \tPredator   G3-572\n")
        identity = self.backend.get_identity()
        self.assertTrue(identity.supported)
        self.assertTrue(identity.tested_bios)
        self.assertEqual(identity.product_name, "Predator G3-572")
        self.bios.write_text("V1.99")
        self.assertFalse(self.backend.get_identity().tested_bios)
        self.backend.set_coolboost(True)
        self.assertEqual(self.ec.writes, [(COOLBOOST_REGISTER, 1)])

    def test_mismatched_product_blocks_every_write(self):
        operations = [
            lambda: self.backend.set_cpu_fan_mode(FanMode.AUTO),
            lambda: self.backend.set_gpu_fan_mode(FanMode.TURBO),
            lambda: self.backend.set_cpu_manual_speed(50),
            lambda: self.backend.set_gpu_manual_speed(50),
            lambda: self.backend.set_coolboost(True),
        ]
        for product in ("G3-572", "Predator", "Predator G3-572-55UB", "Predator G3-572 extra", "Nitro AN515"):
            self.product.write_text(product)
            for operation in operations:
                with self.subTest(product=product, operation=operation):
                    self.assert_code(ErrorCode.UNSUPPORTED_HARDWARE, operation)
            self.assertFalse(self.backend.probe().writable)
        self.assertEqual(self.ec.events, [])

    def test_missing_empty_and_malformed_identity(self):
        for data in (b"", b"\xff"):
            self.product.write_bytes(data)
            self.assert_code(ErrorCode.IDENTITY_UNAVAILABLE, lambda: self.backend.set_coolboost(True))
        self.product.unlink()
        self.assert_code(ErrorCode.IDENTITY_UNAVAILABLE, self.backend.get_identity)
        self.assertEqual(self.ec.writes, [])

    def test_identity_rechecked_after_probe(self):
        self.assertTrue(self.backend.probe().writable)
        self.product.write_text("Other laptop")
        self.assert_code(ErrorCode.UNSUPPORTED_HARDWARE, lambda: self.backend.set_coolboost(True))
        self.assertEqual(self.ec.writes, [])

    def test_bios_unavailable_does_not_relax_product_gate(self):
        self.bios.unlink()
        self.assertIsNone(self.backend.get_identity().bios_version)
        self.assertTrue(self.backend.probe().writable)
        self.product.write_text("Other")
        self.assert_code(ErrorCode.UNSUPPORTED_HARDWARE, lambda: self.backend.set_coolboost(True))

    def test_preparation_refuses_unsupported_device_before_modprobe(self):
        self.product.write_text("Other")
        with patch.object(env_checks.subprocess, "run") as run:
            self.assertFalse(env_checks.ensure_ec_access())
        run.assert_not_called()


class FanTests(BackendCase):
    def test_all_modes_and_unknown_decode_without_writes(self):
        for channel in FanChannel:
            for mode, raw in MODE_VALUES[channel].items():
                self.ec.data[MODE_REGISTERS[channel]] = raw
                self.assertEqual(self.backend.get_fan_mode(channel), mode)
            for raw in (0x5D, 0xFF):
                self.ec.data[MODE_REGISTERS[channel]] = raw
                self.assertEqual(self.backend.get_fan_mode(channel), FanMode.UNKNOWN)
        self.assertEqual(self.ec.writes, [])

    def test_explicit_auto_turbo_and_no_redundant_writes(self):
        for channel in FanChannel:
            for mode in (FanMode.AUTO, FanMode.TURBO):
                self.backend.set_fan_mode(channel, mode)
                self.assertEqual(self.backend.get_fan_mode(channel), mode)
                count = len(self.ec.writes)
                self.backend.set_fan_mode(channel, mode)
                self.assertEqual(len(self.ec.writes), count)
        self.assertEqual(len(self.ec.writes), 4)

    def test_manual_orders_mode_before_control_and_preserves_other_bytes(self):
        for channel in FanChannel:
            self.ec.writes.clear()
            original = self.ec.data[:]
            self.backend.set_manual_speed(channel, 60)
            self.assertEqual(
                self.ec.writes,
                [
                    (MODE_REGISTERS[channel], MODE_VALUES[channel][FanMode.MANUAL]),
                    (CONTROL_REGISTERS[channel], 60),
                ],
            )
            expected = original[:]
            for address, value in self.ec.writes:
                expected[address] = value
            self.assertEqual(self.ec.data, expected)
            self.assertEqual(self.backend.get_manual_speed(channel), 60)

    def test_manual_without_percent_uses_observed_control(self):
        self.backend.set_cpu_fan_mode(FanMode.MANUAL)
        self.assertEqual(self.backend.get_cpu_manual_speed(), 50)
        self.assertEqual(self.ec.writes, [(MODE_REGISTERS[FanChannel.CPU], 0x5C)])

    def test_manual_preflight_failure_does_not_change_mode(self):
        self.ec.read_override[CONTROL_REGISTERS[FanChannel.CPU]] = b""
        self.assert_code(ErrorCode.SHORT_READ, lambda: self.backend.set_cpu_manual_speed(60))
        self.assertEqual(self.ec.writes, [])

    def test_failed_mode_verification_prevents_control_write(self):
        self.ec.ignore_write = True
        self.assert_code(ErrorCode.VERIFICATION_FAILED, lambda: self.backend.set_gpu_manual_speed(60))
        self.assertEqual(self.ec.writes, [(MODE_REGISTERS[FanChannel.GPU], 0x70)])

    def test_failed_control_verification_reports_partial_manual_transition(self):
        channel = FanChannel.CPU
        self.ec.read_override[CONTROL_REGISTERS[channel]] = bytes((50,))
        exc = self.assert_code(ErrorCode.VERIFICATION_FAILED, lambda: self.backend.set_cpu_manual_speed(70))
        self.assertEqual(exc.register, CONTROL_REGISTERS[channel])
        self.assertEqual(self.backend.get_cpu_fan_mode(), FanMode.MANUAL)
        self.assertEqual(self.ec.writes, [(MODE_REGISTERS[channel], 0x5C), (CONTROL_REGISTERS[channel], 70)])

    def test_explicit_verified_mode_can_replace_unknown_only_on_request(self):
        self.ec.data[MODE_REGISTERS[FanChannel.CPU]] = 0xFF
        self.assertEqual(self.backend.get_cpu_fan_mode(), FanMode.UNKNOWN)
        self.assertTrue(self.backend.probe().writable)
        self.assertEqual(self.ec.writes, [])
        self.backend.set_cpu_fan_mode(FanMode.AUTO)
        self.assertEqual(self.backend.get_cpu_fan_mode(), FanMode.AUTO)

    def test_percent_clamping_and_invalid_types(self):
        for channel in FanChannel:
            for requested, expected in ((-10, 0), (0, 0), (50, 50), (100, 100), (200, 100)):
                self.backend.set_manual_speed(channel, requested)
                self.assertEqual(self.backend.get_manual_speed(channel), expected)
            for invalid in (True, None, 1.5, "50"):
                self.assert_code(ErrorCode.INVALID_VALUE, lambda: self.backend.set_manual_speed(channel, invalid))

    def test_invalid_modes_and_channels(self):
        for invalid in (FanMode.UNKNOWN, FanMode.FIRMWARE_AUTO, None, "auto", 1):
            self.assert_code(ErrorCode.INVALID_VALUE, lambda: self.backend.set_cpu_fan_mode(invalid))
        self.assert_code(ErrorCode.INVALID_VALUE, lambda: self.backend.get_fan_mode("cpu"))
        self.assert_code(
            ErrorCode.INVALID_VALUE,
            lambda: self.backend.set_cpu_fan_mode(FanMode.AUTO, manual_percent=50),
        )
        self.assertEqual(self.ec.writes, [])

    def test_coolboost_boolean_contract(self):
        self.assertIs(self.backend.get_coolboost(), False)
        for enabled in (True, False):
            self.backend.set_coolboost(enabled)
            self.assertIs(self.backend.get_coolboost(), enabled)
        self.ec.data[COOLBOOST_REGISTER] = 2
        self.assertIsNone(self.backend.get_coolboost())
        for invalid in (1, 0, "false", None):
            self.assert_code(ErrorCode.INVALID_VALUE, lambda: self.backend.set_coolboost(invalid))

    def test_word_reads_little_endian_and_plausibility(self):
        for channel in FanChannel:
            address = RPM_REGISTERS[channel]
            for value in (0, 256, 3456, 6122, 6123, 65535):
                self.ec.data[address : address + 2] = value.to_bytes(2, "little")
                self.assertEqual(self.backend.get_fan_rpm(channel), value if value <= 6122 else None)
        self.assertEqual(self.ec.writes, [])

    def test_cpu_gpu_api_wrappers(self):
        self.backend.set_cpu_fan_mode(FanMode.AUTO)
        self.backend.set_gpu_fan_mode(FanMode.TURBO)
        self.assertEqual(self.backend.get_cpu_fan_mode(), FanMode.AUTO)
        self.assertEqual(self.backend.get_gpu_fan_mode(), FanMode.TURBO)
        self.backend.set_cpu_manual_speed(40)
        self.backend.set_gpu_manual_speed(70)
        self.assertEqual(self.backend.get_cpu_manual_speed(), 40)
        self.assertEqual(self.backend.get_gpu_manual_speed(), 70)
        self.assertEqual(self.backend.get_cpu_fan_rpm(), 0)
        self.assertEqual(self.backend.get_gpu_fan_rpm(), 0)

    def test_unknown_manual_read(self):
        self.ec.data[CONTROL_REGISTERS[FanChannel.CPU]] = 255
        self.assertIsNone(self.backend.get_cpu_manual_speed())
        self.assert_code(ErrorCode.INVALID_VALUE, lambda: self.backend.set_cpu_fan_mode(FanMode.MANUAL))
        self.assertEqual(self.ec.writes, [])


class FailureTests(BackendCase):
    def test_private_allow_list_refuses_arbitrary_registers_and_values(self):
        for address, value in ((0x11, 1), (0x13, 50), (-1, 0), (256, 0), (0x10, 2), (0x22, 0), (0x37, 101)):
            self.assert_code(ErrorCode.WRITE_REFUSED, lambda: self.backend._write_verified(self.ec, address, value))
        for value in (-1, 256, True, "1"):
            self.assert_code(ErrorCode.WRITE_REFUSED, lambda: self.backend._write_verified(self.ec, 0x10, value))
        self.assertEqual(self.ec.events, [])
        self.assertFalse(hasattr(self.backend, "write"))
        self.assertFalse(hasattr(hardware, "ec_write"))

    def test_invalid_reads(self):
        for address, size in ((-1, 1), (256, 1), (0x10, 2), (0x13, 1), (0x13, 3)):
            self.assert_code(ErrorCode.INVALID_VALUE, lambda: self.backend._read(self.ec, address, size))

    def test_short_and_malformed_reads_never_write(self):
        for raw, code in (
            (b"", ErrorCode.SHORT_READ),
            (b"ab", ErrorCode.MALFORMED_READ),
            (None, ErrorCode.MALFORMED_READ),
        ):
            self.ec.read_override[COOLBOOST_REGISTER] = raw
            self.assert_code(code, lambda: self.backend.set_coolboost(True))
        self.ec.read_override[RPM_REGISTERS[FanChannel.CPU]] = b"\x00"
        self.assert_code(ErrorCode.SHORT_READ, self.backend.get_cpu_fan_rpm)
        self.assertEqual(self.ec.writes, [])

    def test_verification_error_details(self):
        self.ec.ignore_write = True
        exc = self.assert_code(ErrorCode.VERIFICATION_FAILED, lambda: self.backend.set_coolboost(True))
        self.assertEqual((exc.register, exc.expected, exc.observed), (COOLBOOST_REGISTER, 1, 0))

    def test_incomplete_write(self):
        self.ec.write_count = 0
        self.assert_code(ErrorCode.IO_ERROR, lambda: self.backend.set_coolboost(True))

    def test_permission_and_io_failures(self):
        for error, code in (
            (PermissionError(errno.EACCES, "denied"), ErrorCode.PERMISSION_DENIED),
            (OSError(errno.EIO, "failed"), ErrorCode.IO_ERROR),
            (OSError(errno.ENODEV, "gone"), ErrorCode.EC_UNAVAILABLE),
        ):
            self.ec.open_error = error
            self.assert_code(code, self.backend.get_coolboost)
        self.ec.open_error = None
        self.ec.write_error = PermissionError(errno.EPERM, "denied")
        self.assert_code(ErrorCode.PERMISSION_DENIED, lambda: self.backend.set_coolboost(True))

    def test_probe_is_read_only_and_reports_read_only_access(self):
        with patch.object(env_checks.subprocess, "run") as run:
            self.assertTrue(self.backend.probe().writable)
        run.assert_not_called()
        self.assertEqual(self.ec.writes, [])
        self.ec.write_denied = True
        status = self.backend.probe()
        self.assertTrue(status.readable)
        self.assertFalse(status.writable)
        self.assertEqual(status.error.code, ErrorCode.PERMISSION_DENIED)

    def test_missing_module_debugfs_and_device(self):
        self.ec.open_error = FileNotFoundError(errno.ENOENT, "missing")
        module = self.root / "module"
        with patch.object(hardware, "EC_MODULE_PATH", str(module)):
            self.assert_code(ErrorCode.MODULE_MISSING, self.backend.get_coolboost)
            module.mkdir()
            with patch.object(hardware.os.path, "ismount", return_value=False):
                self.assert_code(ErrorCode.DEBUGFS_UNAVAILABLE, self.backend.get_coolboost)
            with patch.object(hardware.os.path, "ismount", return_value=True):
                self.assert_code(ErrorCode.EC_UNAVAILABLE, self.backend.get_coolboost)

    def test_threads_and_backend_instances_share_transaction_lock(self):
        second = G3572EcBackend(_transport=self.ec)
        self.ec.delay = 0.001

        def action(index):
            if index % 3 == 0:
                self.backend.set_cpu_manual_speed(index % 101)
            elif index % 3 == 1:
                second.set_gpu_manual_speed(index % 101)
            else:
                second.get_cpu_fan_rpm()

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(action, range(60)))
        self.assertEqual(self.ec.max_active, 1)

    def test_real_file_transport_on_temporary_file(self):
        path = self.root / "fake-ec"
        original = bytes(self.ec.data)
        path.write_bytes(original)
        with patch.object(hardware, "EC_IO_FILE", str(path)):
            backend = G3572EcBackend()
            backend.set_cpu_manual_speed(70)
            backend.set_coolboost(True)
            self.assertEqual(backend.get_cpu_manual_speed(), 70)
        expected = bytearray(original)
        expected[MODE_REGISTERS[FanChannel.CPU]] = 0x5C
        expected[CONTROL_REGISTERS[FanChannel.CPU]] = 70
        expected[COOLBOOST_REGISTER] = 1
        self.assertEqual(path.read_bytes(), expected)

    def test_file_lock_contention_fails_closed(self):
        path = self.root / "fake-ec"
        path.write_bytes(self.ec.data)
        with path.open("rb") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            with patch.object(hardware, "EC_IO_FILE", str(path)), patch.object(hardware, "_LOCK_TIMEOUT", 0):
                self.assert_code(ErrorCode.LOCK_TIMEOUT, lambda: G3572EcBackend().set_coolboost(True))
        self.assertEqual(path.read_bytes(), self.ec.data)

    def test_actual_file_open_permission_exception(self):
        with patch.object(hardware.os, "open", side_effect=PermissionError(errno.EACCES, "denied")):
            self.assert_code(ErrorCode.PERMISSION_DENIED, lambda: G3572EcBackend().set_coolboost(True))

    def test_unsupported_product_never_opens_real_transport(self):
        self.product.write_text("Other laptop")
        with patch.object(hardware.os, "open") as opened:
            self.assert_code(ErrorCode.UNSUPPORTED_HARDWARE, lambda: G3572EcBackend().set_coolboost(True))
        opened.assert_not_called()
