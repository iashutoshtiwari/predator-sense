"""Stable failure categories for callers; no raw OS exceptions cross the backend."""

from enum import Enum


class ErrorCode(Enum):
    IDENTITY_UNAVAILABLE = "identity_unavailable"
    UNSUPPORTED_HARDWARE = "unsupported_hardware"
    MODULE_MISSING = "module_missing"
    DEBUGFS_UNAVAILABLE = "debugfs_unavailable"
    EC_UNAVAILABLE = "ec_unavailable"
    PERMISSION_DENIED = "permission_denied"
    SHORT_READ = "short_read"
    MALFORMED_READ = "malformed_read"
    IO_ERROR = "io_error"
    INVALID_VALUE = "invalid_value"
    WRITE_REFUSED = "write_refused"
    VERIFICATION_FAILED = "verification_failed"
    LOCK_TIMEOUT = "lock_timeout"


class HardwareError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        register: int | None = None,
        expected: int | None = None,
        observed: int | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.register = register
        self.expected = expected
        self.observed = observed
