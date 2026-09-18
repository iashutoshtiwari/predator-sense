#!/usr/bin/env python3
import pathlib


def main():
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    required = [
        repo_root / "src" / "predator_sense" / "ui" / "theme.py",
        repo_root / "src" / "predator_sense" / "ui" / "instruments.py",
        repo_root / "assets" / "predator-sense.svg",
        repo_root / "src" / "predator_sense" / "assets" / "fonts" / "TurretRoad-Regular.ttf",
        repo_root / "src" / "predator_sense" / "assets" / "fonts" / "TurretRoad-Medium.ttf",
        repo_root / "src" / "predator_sense" / "assets" / "fonts" / "TurretRoad-Bold.ttf",
        repo_root / "src" / "predator_sense" / "assets" / "fonts" / "OFL.txt",
        repo_root / "scripts" / "validate_fan_telemetry.py",
        repo_root / "src" / "predator_sense" / "main.py",
        repo_root / "src" / "predator_sense" / "daemon_main.py",
        repo_root / "src" / "predator_sense" / "frontend.py",
        repo_root / "src" / "predator_sense" / "font_config.py",
        repo_root / "src" / "predator_sense" / "core" / "hardware.py",
        repo_root / "src" / "predator_sense" / "core" / "logger.py",
        repo_root / "src" / "predator_sense" / "core" / "profiles.py",
        repo_root / "src" / "predator_sense" / "core" / "env_checks.py",
        repo_root / "src" / "predator_sense" / "core" / "errors.py",
        repo_root / "src" / "predator_sense" / "core" / "state.py",
        repo_root / "src" / "predator_sense" / "ui" / "main_window.py",
        repo_root / "src" / "predator_sense" / "service" / "daemon.py",
        repo_root / "src" / "predator_sense" / "service" / "client.py",
        repo_root / "src" / "predator_sense" / "service" / "protocol.py",
        repo_root / "src" / "predator_sense" / "service" / "controller.py",
        repo_root / "src" / "predator_sense" / "service" / "telemetry_model.py",
        repo_root / "src" / "predator_sense" / "service" / "sensors.py",
        repo_root / "src" / "predator_sense" / "service" / "telemetry.py",
        repo_root / "packaging" / "io.github.iashutoshtiwari.PredatorSense.conf",
        repo_root / "packaging" / "io.github.iashutoshtiwari.predatorsense.policy",
        repo_root / "PKGBUILD",
        repo_root / "packaging" / "predator-sense.desktop",
        repo_root / "packaging" / "predator-sensed.service",
        repo_root / "src" / "predator_sense" / "install_check.py",
        repo_root / "src" / "predator_sense" / "diagnostics.py",
        repo_root / "packaging" / "io.github.iashutoshtiwari.PredatorSense.service",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit(f"Missing required files: {', '.join(missing)}")
    print("Smoke test passed.")


if __name__ == "__main__":
    main()
