#!/usr/bin/env python3
import pathlib


def main():
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    required = [
        repo_root / "main.py",
        repo_root / "frontend.py",
        repo_root / "ecwrite.py",
        repo_root / "font_config.py",
        repo_root / "PKGBUILD",
        repo_root / "packaging" / "predator-sense.desktop",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit(f"Missing required files: {', '.join(missing)}")
    print("Smoke test passed.")


if __name__ == "__main__":
    main()
