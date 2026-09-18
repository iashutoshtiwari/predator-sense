"""DMI identity and explicit application startup preparation (not backend probing)."""

from __future__ import annotations

from pathlib import Path
import subprocess

from core.errors import ErrorCode, HardwareError
from core.logger import get_logger
from core.profiles import EC_IO_FILE, HardwareIdentity, SUPPORTED_PRODUCT, TESTED_BIOS

logger = get_logger(__name__)
DMI_PRODUCT_NAME_PATH = Path("/sys/class/dmi/id/product_name")
DMI_BIOS_VERSION_PATH = Path("/sys/class/dmi/id/bios_version")


def get_hardware_identity() -> HardwareIdentity:
    try:
        product = " ".join(DMI_PRODUCT_NAME_PATH.read_text(encoding="utf-8").split())
    except (OSError, UnicodeError) as exc:
        raise HardwareError(ErrorCode.IDENTITY_UNAVAILABLE, f"Cannot read DMI product name: {exc}") from exc
    if not product:
        raise HardwareError(ErrorCode.IDENTITY_UNAVAILABLE, "DMI product name is empty")
    try:
        bios = " ".join(DMI_BIOS_VERSION_PATH.read_text(encoding="utf-8").split()) or None
    except (OSError, UnicodeError):
        bios = None
    return HardwareIdentity(product, bios)


def require_supported_identity() -> HardwareIdentity:
    identity = get_hardware_identity()
    if not identity.supported:
        raise HardwareError(
            ErrorCode.UNSUPPORTED_HARDWARE,
            f"Unsupported product {identity.product_name!r}; requires {SUPPORTED_PRODUCT!r}",
        )
    return identity


def run_env_checks() -> bool:
    try:
        identity = require_supported_identity()
    except HardwareError as exc:
        logger.error("Hardware identity check failed [%s]: %s", exc.code.value, exc)
        return False
    logger.info("Hardware: %s; BIOS: %s", identity.product_name, identity.bios_version or "unavailable")
    if not identity.tested_bios:
        logger.warning("This BIOS has not been validated; tested BIOS is %s", TESTED_BIOS)
    return True


def ensure_ec_access() -> bool:
    """Explicit privileged daemon startup preparation.

    Never called by G3572EcBackend. No sudo/pkexec and no EC writes. Only the root daemon owns this preparation.
    """
    try:
        require_supported_identity()
        try:
            with open(EC_IO_FILE, "rb"):
                return True
        except FileNotFoundError:
            logger.info("EC interface missing; preparing ec_sys with write support")
        result = subprocess.run(
            ["modprobe", "ec_sys", "write_support=1"],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
        if result.returncode:
            logger.error("ec_sys preparation failed: %s", result.stderr.strip())
            return False
        with open(EC_IO_FILE, "rb"):
            return True
    except (HardwareError, OSError, subprocess.TimeoutExpired) as exc:
        logger.error("Cannot prepare EC access: %s", exc)
        return False
