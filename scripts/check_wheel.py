#!/usr/bin/env python3
"""Validate built wheel contents/entry points without starting GUI or hardware."""
import configparser
from pathlib import Path
import sys
import zipfile


def check(path):
    with zipfile.ZipFile(path) as wheel:
        names = set(wheel.namelist())
        assert "predator_sense/assets/predator-sense.svg" in names
        assert "predator_sense/install_check.py" in names
        assert all(name.startswith("predator_sense/") or ".dist-info/" in name for name in names)
        assert not any(name.endswith((".ttf", ".otf", ".ico")) for name in names)
        root = Path(__file__).resolve().parent.parent / "src"
        expected = {str(p.relative_to(root)) for p in (root / "predator_sense").rglob("*.py")}
        assert expected <= names, f"Missing modules: {expected - names}"
        entry = next(name for name in names if name.endswith(".dist-info/entry_points.txt"))
        config = configparser.ConfigParser()
        config.read_string(wheel.read(entry).decode())
        assert dict(config["console_scripts"]) == {
            "predator-sense": "predator_sense.main:main",
            "predator-sensed": "predator_sense.daemon_main:main",
            "predator-sense-check": "predator_sense.install_check:main",
        }
        metadata = wheel.read(entry.replace("entry_points.txt", "METADATA")).decode()
        assert "License-Expression: GPL-3.0-only" in metadata
    print(f"Wheel verified: {path}")


if __name__ == "__main__":
    check(sys.argv[1])
