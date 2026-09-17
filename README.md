# Predator Sense™ for Helios 300 (2017)

Linux fan control application for Acer Predator Helios 300 (`G3-572-55UB`).

## Screenshot

![Predator Sense](demo.png)

## Requirements

- `python`
- `python-pyqt6`
- `polkit`

## Support Status

Arch Linux, CachyOS, and compatible Arch-derived distributions are supported.
Hardware access requires the exact normalized DMI product name `Predator G3-572`.
BIOS `V1.22` is the tested version; other BIOS versions are reported as unvalidated.

## Run From Source

```bash
python -m pip install -r requirements.txt
sudo python src/main.py
```

## Run From PKGBUILD

```bash
makepkg -si
predator-sense
```

## Hardware backend and development

The GUI and CoolBoost service share a guarded G3-572 backend with verified writes,
explicit unknown states, and candidate fan RPM reads. The GUI does not display
telemetry. See [ARCHITECTURE.md](ARCHITECTURE.md) for the API, evidence limits, and
remaining physical validation. Startup may prepare `ec_sys` after verifying DMI;
the backend itself never escalates privileges or loads modules.

Hardware-free checks (Python 3.12, dependencies from `requirements-dev.txt`):

```bash
ruff check .
PYTHONPATH=src QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
python scripts/smoke_test.py
```

## Disclaimer

This is a community project provided without any warranty. No contributor or maintainer is responsible for any damage to your device.

## Based On

This project is based on the implementation at https://github.com/mohsunb/PredatorSense.
