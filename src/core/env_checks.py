from __future__ import annotations

import shutil

from core.logger import get_logger


logger = get_logger(__name__)
SUPPORTED_MODEL_SUBSTRING = "G3-572"
DMI_PRODUCT_NAME_PATH = "/sys/class/dmi/id/product_name"


def _read_dmi_product_name() -> str:
    try:
        with open(DMI_PRODUCT_NAME_PATH, "r", encoding="utf-8") as file_handle:
            return file_handle.read().strip()
    except OSError as exc:
        logger.error("Unable to read DMI product name from %s: %s", DMI_PRODUCT_NAME_PATH, exc)
        return "Unknown"


def run_env_checks() -> bool:
    pkexec_path = shutil.which("pkexec")
    sudo_path = shutil.which("sudo")

    if pkexec_path or sudo_path:
        logger.info("Privilege helper check passed (pkexec=%s, sudo=%s)", bool(pkexec_path), bool(sudo_path))
    else:
        logger.warning("Neither pkexec nor sudo was found in PATH")

    product_name = _read_dmi_product_name()
    logger.info("Detected DMI product name: %s", product_name)

    if SUPPORTED_MODEL_SUBSTRING not in product_name:
        logger.critical("Unsupported hardware detected: %s", product_name)
        return False

    logger.info("Hardware support check passed for model containing %s", SUPPORTED_MODEL_SUBSTRING)
    return True
