#!/usr/bin/env python3
import pathlib


def main():
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    required = [
        repo_root / "src" / "main.py",
        repo_root / "background_service.py",
        repo_root / "src" / "frontend.py",
        repo_root / "src" / "font_config.py",
        repo_root / "src" / "core" / "hardware.py",
        repo_root / "src" / "core" / "logger.py",
        repo_root / "src" / "ui" / "main_window.py",
        repo_root / "PKGBUILD",
        repo_root / "packaging" / "predator-sense.desktop",
        repo_root / "packaging" / "predator-sense.service",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit(f"Missing required files: {', '.join(missing)}")
    print("Smoke test passed.")


if __name__ == "__main__":
    main()
