#!/usr/bin/env python3
"""Root system service entry point; no Qt or user-session environment required."""

import asyncio
import logging
import os

from core import logger as logger_config


def main():
    if os.geteuid() != 0:
        raise SystemExit("predator-sensed must be started as a root system service")
    logger_config.LOG_PATH = None  # systemd captures stream logs in the journal
    logging.basicConfig(level=logging.INFO, format=logger_config.LOG_FORMAT)
    from service.daemon import run_daemon

    asyncio.run(run_daemon())


if __name__ == "__main__":
    main()
