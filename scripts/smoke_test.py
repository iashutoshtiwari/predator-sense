#!/usr/bin/env python3
import pathlib


def main():
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    required = [
        repo_root / "scripts" / "validate_fan_telemetry.py",
        repo_root / "src" / "main.py",
        repo_root / "src" / "daemon_main.py",
        repo_root / "src" / "frontend.py",
        repo_root / "src" / "font_config.py",
        repo_root / "src" / "core" / "hardware.py",
        repo_root / "src" / "core" / "logger.py",
        repo_root / "src" / "core" / "profiles.py",
        repo_root / "src" / "core" / "env_checks.py",
        repo_root / "src" / "core" / "errors.py",
        repo_root / "src" / "core" / "state.py",
        repo_root / "src" / "ui" / "main_window.py",
        repo_root / "src" / "service" / "daemon.py",
        repo_root / "src" / "service" / "client.py",
        repo_root / "src" / "service" / "protocol.py",
        repo_root / "src" / "service" / "controller.py",
        repo_root / "src" / "service" / "telemetry_model.py",
        repo_root / "src" / "service" / "sensors.py",
        repo_root / "src" / "service" / "telemetry.py",
        repo_root / "packaging" / "io.github.iashutoshtiwari.PredatorSense.conf",
        repo_root / "packaging" / "io.github.iashutoshtiwari.predatorsense.policy",
        repo_root / "PKGBUILD",
        repo_root / "packaging" / "predator-sense.desktop",
        repo_root / "packaging" / "predator-sensed.service",
        repo_root / "packaging" / "predator-sensed",
        repo_root / "packaging" / "io.github.iashutoshtiwari.PredatorSense.service",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit(f"Missing required files: {', '.join(missing)}")
    print("Smoke test passed.")


if __name__ == "__main__":
    main()
