from __future__ import annotations

import subprocess

from core.logger import get_logger


logger = get_logger(__name__)
EC_IO_FILE = "/sys/kernel/debug/ec/ec0/io"


def run_command(cmd: list[str], timeout: int = 8) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        logger.warning("Command not found: %s", cmd[0])
        return 127, "", f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        logger.warning("Command timed out after %ss: %s", timeout, " ".join(cmd))
        return 124, "", f"command timed out after {timeout}s"
    except OSError as exc:
        logger.error("OS error while running command %s: %s", " ".join(cmd), exc)
        return 1, "", str(exc)


def ensure_ec_access() -> bool:
    try:
        with open(EC_IO_FILE, "rb"):
            logger.info("EC I/O path is available: %s", EC_IO_FILE)
            return True
    except PermissionError as exc:
        logger.error("Permission denied for %s: %s", EC_IO_FILE, exc)
        return False
    except FileNotFoundError:
        logger.warning("%s not found. Trying to load ec_sys module.", EC_IO_FILE)
        code, _stdout, stderr = run_command(["modprobe", "ec_sys", "write_support=1"], timeout=8)
        if code != 0:
            logger.error("Failed to load ec_sys via modprobe: %s", stderr.strip())
            return False
        try:
            with open(EC_IO_FILE, "rb"):
                logger.info("ec_sys module loaded and EC I/O path is now available")
                return True
        except OSError as exc:
            logger.error("EC I/O path still unavailable after modprobe: %s", exc)
            return False
    except OSError as exc:
        logger.error("Unable to access EC I/O path %s: %s", EC_IO_FILE, exc)
        return False


def ec_write(address: int, value: int) -> bool:
    try:
        with open(EC_IO_FILE, "rb+") as file_handle:
            file_handle.seek(address)
            old_raw = file_handle.read(1)
            if not old_raw:
                logger.error("Failed to read old EC value at address 0x%02X", address)
                return False

            old_value = old_raw[0]
            if value != old_value:
                file_handle.seek(address)
                file_handle.write(bytearray([value]))
                logger.info(
                    "EC write at 0x%02X: %d -> %d",
                    address,
                    old_value,
                    value,
                )
            else:
                logger.debug("EC write skipped at 0x%02X: value unchanged (%d)", address, value)
            return True
    except (OSError, PermissionError) as exc:
        logger.error("EC write failed at address 0x%02X with value %d: %s", address, value, exc)
        return False


def ec_read(address: int) -> int | None:
    try:
        with open(EC_IO_FILE, "rb") as file_handle:
            file_handle.seek(address)
            raw = file_handle.read(1)
            if not raw:
                logger.error("EC read returned no data at address 0x%02X", address)
                return None
            return raw[0]
    except (OSError, PermissionError) as exc:
        logger.error("EC read failed at address 0x%02X: %s", address, exc)
        return None
