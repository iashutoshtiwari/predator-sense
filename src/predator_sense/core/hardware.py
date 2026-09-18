"""Guarded G3-572 operations. This module never escalates or prepares the system."""

from __future__ import annotations

from contextlib import contextmanager
import errno
import fcntl
import os
from pathlib import Path
import threading
import time

from predator_sense.core.env_checks import get_hardware_identity, require_supported_identity
from predator_sense.core.errors import ErrorCode, HardwareError
from predator_sense.core.logger import get_logger
from predator_sense.core.profiles import (
    BYTE_READ_REGISTERS,
    CONTROL_REGISTERS,
    COOLBOOST_OFF,
    COOLBOOST_ON,
    COOLBOOST_REGISTER,
    DEBUGFS_PATH,
    EC_IO_FILE,
    EC_MODULE_PATH,
    FAN_RPM_MAX,
    MODE_REGISTERS,
    MODE_VALUES,
    RPM_REGISTERS,
    WORD_READ_REGISTERS,
    WRITABLE_VALUES,
    FanChannel,
    FanMode,
    HardwareIdentity,
    HardwareStatus,
)

logger = get_logger(__name__)
# Shared across backend instances as well as channels. The descriptor flock also
# coordinates this application's GUI/service processes, not firmware/other tools.
_EC_LOCK = threading.RLock()
_LOCK_TIMEOUT = 2.0
_RPM_WARNING_INTERVAL = 60.0


def _os_error(exc: OSError) -> HardwareError:
    if exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
        code = ErrorCode.PERMISSION_DENIED
        message = "EC access denied; check process privileges and ec_sys write_support"
    elif exc.errno == errno.ENOENT:
        if not Path(EC_MODULE_PATH).exists():
            code = ErrorCode.MODULE_MISSING
            message = "ec_sys is not loaded; system preparation is required"
        elif not os.path.ismount(DEBUGFS_PATH):
            code = ErrorCode.DEBUGFS_UNAVAILABLE
            message = "debugfs is not mounted; system preparation is required"
        else:
            code = ErrorCode.EC_UNAVAILABLE
            message = f"EC device is unavailable at {EC_IO_FILE}"
    elif exc.errno in (errno.ENODEV, errno.ENXIO):
        code = ErrorCode.EC_UNAVAILABLE
        message = "EC device disappeared or is unavailable"
    else:
        code = ErrorCode.IO_ERROR
        message = "EC I/O failed"
    return HardwareError(code, f"{message}: {exc}")


class _EcFileSession:
    def __init__(self, fd: int):
        self._fd = fd

    def read(self, address: int, size: int) -> bytes:
        # Positional I/O has no shared seek offset and bypasses Python buffering.
        return os.pread(self._fd, size, address)

    def write(self, address: int, value: int) -> int:
        return os.pwrite(self._fd, bytes((value,)), address)


class _EcFileTransport:
    @contextmanager
    def transaction(self, write: bool = False):
        fd = os.open(EC_IO_FILE, (os.O_RDWR if write else os.O_RDONLY) | os.O_CLOEXEC)
        try:
            deadline = time.monotonic() + _LOCK_TIMEOUT
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise HardwareError(
                            ErrorCode.LOCK_TIMEOUT, "EC is busy in another process; retry later"
                        ) from exc
                    time.sleep(0.01)
            yield _EcFileSession(fd)
        finally:
            # Closing releases flock even when a read/write/verification fails.
            os.close(fd)


class G3572EcBackend:
    """Semantic operations only; raw addresses never cross into the controller.

    `_transport` is an internal test seam, not a configurable real-device path or
    a hardware-identity override. Every transaction rechecks authoritative DMI.
    Errors raise HardwareError. Valid but unknown register values decode to
    UNKNOWN/None. No automatic recovery writes or retry writes are performed.
    """

    def __init__(self, *, _transport=None):
        self._transport = _transport if _transport is not None else _EcFileTransport()
        self._rpm_warning_at = {}

    def get_identity(self) -> HardwareIdentity:
        return get_hardware_identity()

    @contextmanager
    def _transaction(self, *, write: bool = False):
        with _EC_LOCK:
            require_supported_identity()
            try:
                with self._transport.transaction(write=write) as session:
                    yield session
            except OSError as exc:
                raise _os_error(exc) from exc

    def probe(self) -> HardwareStatus:
        """Inspect identity and access without preparing the system or writing EC.

        Writability describes successful read/write opening, not a trial write.
        Kernel/firmware may still reject a later operation, which is verified.
        """
        identity = None
        readable = False
        try:
            identity = self.get_identity()
            with self._transaction() as session:
                for address in sorted(BYTE_READ_REGISTERS):
                    self._read(session, address)
            readable = True
            with self._transaction(write=True):
                pass
            return HardwareStatus(identity, True, True)
        except HardwareError as exc:
            return HardwareStatus(identity, readable, False, exc)

    @staticmethod
    def _channel(channel: FanChannel) -> FanChannel:
        if not isinstance(channel, FanChannel):
            raise HardwareError(ErrorCode.INVALID_VALUE, "Fan channel must be CPU or GPU")
        return channel

    @staticmethod
    def _percent(percent: int) -> int:
        if type(percent) is not int:
            raise HardwareError(ErrorCode.INVALID_VALUE, "Manual speed must be an integer percentage")
        return max(0, min(100, percent))

    @staticmethod
    def _read(session, address: int, size: int = 1) -> int:
        allowed = BYTE_READ_REGISTERS if size == 1 else WORD_READ_REGISTERS
        if type(address) is not int or type(size) is not int or size not in (1, 2) or address not in allowed:
            raise HardwareError(ErrorCode.INVALID_VALUE, "Read outside the G3-572 register contract")
        raw = session.read(address, size)
        if not isinstance(raw, bytes):
            raise HardwareError(ErrorCode.MALFORMED_READ, "EC returned non-byte data", register=address)
        if len(raw) != size:
            code = ErrorCode.SHORT_READ if len(raw) < size else ErrorCode.MALFORMED_READ
            raise HardwareError(
                code, f"EC read at 0x{address:02X}: expected {size} bytes, got {len(raw)}", register=address
            )
        # NBFC ec_sys_linux.c EC_SysLinux_ReadWord uses pread(2) + le16toh.
        return int.from_bytes(raw, "little")

    def _write_verified(self, session, address: int, value: int) -> None:
        if type(address) is not int or type(value) is not int or value not in WRITABLE_VALUES.get(address, ()):
            raise HardwareError(ErrorCode.WRITE_REFUSED, "Write outside the verified G3-572 contract", register=address)
        old = self._read(session, address)
        if old == value:
            return
        count = session.write(address, value)
        if count != 1:
            raise HardwareError(ErrorCode.IO_ERROR, f"Incomplete EC write at 0x{address:02X}", register=address)
        observed = self._read(session, address)
        if observed != value:
            raise HardwareError(
                ErrorCode.VERIFICATION_FAILED,
                f"EC verification failed at 0x{address:02X}: requested 0x{value:02X}, observed 0x{observed:02X}",
                register=address,
                expected=value,
                observed=observed,
            )
        logger.info("Verified EC state change register=0x%02X old=%d new=%d", address, old, value)

    def get_fan_mode(self, channel: FanChannel) -> FanMode:
        channel = self._channel(channel)
        with self._transaction() as session:
            value = self._read(session, MODE_REGISTERS[channel])
        return next((mode for mode, raw in MODE_VALUES[channel].items() if raw == value), FanMode.UNKNOWN)

    def set_fan_mode(self, channel: FanChannel, mode: FanMode, *, manual_percent: int | None = None) -> None:
        channel = self._channel(channel)
        if not isinstance(mode, FanMode) or mode not in (FanMode.AUTO, FanMode.TURBO, FanMode.MANUAL):
            raise HardwareError(ErrorCode.INVALID_VALUE, "Only explicit Auto, Turbo, or Manual may be requested")
        if manual_percent is not None:
            if mode != FanMode.MANUAL:
                raise HardwareError(ErrorCode.INVALID_VALUE, "A manual percentage requires Manual mode")
            manual_percent = self._percent(manual_percent)
        with self._transaction(write=True) as session:
            if mode == FanMode.MANUAL:
                # Read both registers before any writes, even with an explicit
                # percentage. A failed control read must not leave a changed mode.
                existing = self._read(session, CONTROL_REGISTERS[channel])
                if manual_percent is None:
                    if existing > 100:
                        raise HardwareError(ErrorCode.INVALID_VALUE, "Existing manual percentage is unavailable")
                    manual_percent = existing
            self._write_verified(session, MODE_REGISTERS[channel], MODE_VALUES[channel][mode])
            if mode == FanMode.MANUAL:
                # A failure here may leave Manual selected. Propagate it and let
                # callers re-read; never guess rollback values or retry blindly.
                self._write_verified(session, CONTROL_REGISTERS[channel], manual_percent)

    def get_manual_speed(self, channel: FanChannel) -> int | None:
        channel = self._channel(channel)
        with self._transaction() as session:
            value = self._read(session, CONTROL_REGISTERS[channel])
        return value if value <= 100 else None

    def set_manual_speed(self, channel: FanChannel, percent: int) -> None:
        self.set_fan_mode(channel, FanMode.MANUAL, manual_percent=self._percent(percent))

    def get_coolboost(self) -> bool | None:
        with self._transaction() as session:
            value = self._read(session, COOLBOOST_REGISTER)
        if value not in (COOLBOOST_OFF, COOLBOOST_ON):
            return None
        return value == COOLBOOST_ON

    def set_coolboost(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise HardwareError(ErrorCode.INVALID_VALUE, "CoolBoost state must be a boolean")
        with self._transaction(write=True) as session:
            self._write_verified(session, COOLBOOST_REGISTER, COOLBOOST_ON if enabled else COOLBOOST_OFF)

    def get_fan_rpm(self, channel: FanChannel) -> int | None:
        channel = self._channel(channel)
        with self._transaction() as session:
            value = self._read(session, RPM_REGISTERS[channel], 2)
        if value > FAN_RPM_MAX:
            # Bound warnings per channel, including intermittent failures. Never
            # clamp a corrupt sample or retry it in a tight polling loop.
            with _EC_LOCK:
                now = time.monotonic()
                if now >= self._rpm_warning_at.get(channel, float("-inf")):
                    self._rpm_warning_at[channel] = now + _RPM_WARNING_INTERVAL
                    logger.warning(
                        "Unavailable %s candidate RPM: raw word %d at 0x%02X outside 0..%d",
                        channel.value, value, RPM_REGISTERS[channel], FAN_RPM_MAX,
                    )
            return None
        return value

    def get_cpu_fan_mode(self) -> FanMode:
        return self.get_fan_mode(FanChannel.CPU)

    def get_gpu_fan_mode(self) -> FanMode:
        return self.get_fan_mode(FanChannel.GPU)

    def set_cpu_fan_mode(self, mode: FanMode, *, manual_percent: int | None = None) -> None:
        self.set_fan_mode(FanChannel.CPU, mode, manual_percent=manual_percent)

    def set_gpu_fan_mode(self, mode: FanMode, *, manual_percent: int | None = None) -> None:
        self.set_fan_mode(FanChannel.GPU, mode, manual_percent=manual_percent)

    def get_cpu_manual_speed(self) -> int | None:
        return self.get_manual_speed(FanChannel.CPU)

    def get_gpu_manual_speed(self) -> int | None:
        return self.get_manual_speed(FanChannel.GPU)

    def set_cpu_manual_speed(self, percent: int) -> None:
        self.set_manual_speed(FanChannel.CPU, percent)

    def set_gpu_manual_speed(self, percent: int) -> None:
        self.set_manual_speed(FanChannel.GPU, percent)

    def get_cpu_fan_rpm(self) -> int | None:
        return self.get_fan_rpm(FanChannel.CPU)

    def get_gpu_fan_rpm(self) -> int | None:
        return self.get_fan_rpm(FanChannel.GPU)
