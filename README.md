# Predator Sense™ for Helios 300 (2017)

Linux fan control application for Acer Predator Helios 300 (`G3-572-55UB`).

## Screenshot

![Predator Sense](demo.png)

## Requirements

- `python`
- `python-pyqt6`
- `polkit`

## Support Status

Currently, only Arch Linux is supported. A prebuilt binary tarball will be provided soon.

## Run From Source

```bash
python -m pip install -r requirements.txt
pkexec env DISPLAY=$DISPLAY XAUTHORITY=$XAUTHORITY python src/main.py
```

## Run From PKGBUILD

```bash
makepkg -si
predator-sense
```

## Disclaimer

This is a community project provided without any warranty. No contributor or maintainer is responsible for any damage to your device.

## Based On

This project is based on the implementation at https://github.com/mohsunb/PredatorSense.
