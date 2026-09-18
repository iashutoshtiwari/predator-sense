# Predator Sense for Linux

Native fan control and real-time hardware telemetry for the **Acer Predator Helios 300 (2017)** on Linux.

The application features an unprivileged PyQt6 GUI designed for native Wayland and X11 sessions,
paired with a hardened background system service (`predator-sensed`) that securely manages Embedded
Controller (EC) registers.

---

## Screenshot

![Predator Sense native dashboard](docs/screenshots/dashboard.png)

*The native dashboard showing CPU and GPU thermal curves, candidate RPM graphs, fan modes, and CoolBoost.*

---

## Features

* **Dedicated Hardware Support:** Designed strictly for the Acer Predator G3-572 Embedded Controller.
* **Fan Control Modes:**
  * **Auto:** Dynamic firmware-managed fan curves.
  * **Manual:** Direct user-defined percentage control (0–100%) with debounced input.
  * **Turbo:** Maximum cooling fan speeds for heavy thermal workloads.
  * **Global Selectors:** Synchronized one-click Auto or Turbo across all fans.
* **CoolBoost Technology:** Extends the fan curve with elevated RPM under moderate thermal loads.
* **1 Hz Low-Overhead Telemetry:**
  * CPU temperature via `coretemp` sysfs (package-preferred).
  * GPU temperature via official NVIDIA NVML bindings (`pynvml`).
  * Non-blocking candidate fan RPM reads directly from EC tachometer words.
  * 60-second rolling history graphs rendered with anti-aliased QPainter sparklines.
* **Secure Privilege Separation:** The GUI runs strictly as an unprivileged user; all hardware mutations are authenticated via Polkit on the system D-Bus.
* **State Persistence & Sleep Recovery:** Remembers fan settings across reboots; gracefully handles logind suspend and resume without running unbounded polling loops.
* **Native Wayland & High-DPI Support:** Full scaling awareness on modern desktop environments (KDE Plasma, GNOME).

---

## Supported Hardware

* **Target Model:** Acer Predator Helios 300 (2017), model **`G3-572`** (Motherboard `CFL`).
* **Tested BIOS Version:** **`V1.22`**.
* **Strict Hardware Gate:** Hardware control is gated by the normalized DMI product string `Predator G3-572`. Systems returning any other string are refused access to prevent hardware misconfiguration.
* **Explicit Non-Features:** No RGB keyboard controls, battery charging thresholds, GPU overclocking, or generic fan curves for unsupported laptop lines (Nitro, Triton, newer Helios generations).

---

## Installation

### Arch Linux, CachyOS, and Arch-derived Distributions

The package builds cleanly as a native Arch package:

```bash
# Prepare reproducible release source tarball (when building from git):
python scripts/prepare_arch_source.py

# Build and install dependencies via makepkg:
makepkg -si

# Enable and start the system hardware service:
systemctl enable --now predator-sensed.service

# Verify installation health without writing fan values:
predator-sense-check

# Launch Predator Sense from your application menu or terminal:
predator-sense
```

Runtime dependencies include:
* `python` (>= 3.12)
* `python-pyqt6`, `qt6-wayland`, `qt6-svg`
* `python-dbus-next`
* `polkit`, `dbus`, `systemd`, `kmod`, `hicolor-icon-theme`
* `python-nvidia-ml-py` (optional, for discrete NVIDIA GPU temperature readings)

---

## Architecture

Predator Sense enforces a strict two-tier architecture:

```text
┌─────────────────────────────────────────────────────────────────┐
│ User Session: predator-sense (PyQt6 GUI)                        │
│ • Unprivileged desktop process (never run with sudo)            │
│ • Wayland / X11 native, system UI fonts, high-DPI scaling       │
│ • Passive D-Bus client; zero direct EC or sysfs writes          │
└───────────────────────────────┬─────────────────────────────────┘
                                │ System D-Bus
                                │ Interface: io.github.iashutoshtiwari.PredatorSense
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│ System Daemon: predator-sensed (Root Service)                   │
│ • Managed by systemd (predator-sensed.service)                 │
│ • DMI Gate: checks /sys/class/dmi/id/product_name               │
│ • Gated kernel module preparation: modprobe ec_sys              │
│ • Hardware lock & serialized EC I/O (/sys/kernel/debug/ec/ec0)  │
│ • 1 Hz monotonic sensor engine (coretemp sysfs + NVML worker)   │
│ • Polkit authorization gate on every mutation                   │
│ • Atomic state persistence in /var/lib/predator-sense/          │
│ • logind sleep monitor for clean suspend / resume transitions   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Controls

* **Auto Mode:** Returns fan speed management to the laptop's internal EC thermal table.
* **Manual Mode:** Enables percentage sliders (0–100%, stepped in 10% increments). Dragging the slider commits the percentage upon mouse release; keyboard navigation debounces changes by 250 ms before issuing an EC write.
* **Turbo Mode:** Overrides EC thermal management to command maximum fan speeds for peak compute loads.
* **CoolBoost:** An Acer-specific register setting (`0x10`) that boosts fan curves by approximately 300–500 RPM. Can be enabled when at least one fan is set to Auto.
* **Signal Blocking:** The GUI blocks widget event signals when updating its visual state from telemetry snapshots, preventing feedback loops or unexpected writes.

---

## Telemetry

* **Cached 1-Second Snapshots:** The daemon samples coretemp, NVML, and EC tachometers independently in isolated daemon threads and publishes a unified snapshot at 1 Hz.
* **Freshness & Stale Semantics:** If a sensor lane stalls or errors, existing readings are dated and clearly marked as stale or unavailable rather than substituted with misleading zeroes.
* **Candidate RPM:** Tachometer register readings (CPU `0x13`, GPU `0x15`) are displayed with a note indicating candidate RPM, preserving the uncalibrated word values reported by the Embedded Controller.

---

## Wayland

Predator Sense is fully Wayland-native:
* Does **not** export `QT_QPA_PLATFORM=xcb`.
* Uses Qt's logical coordinates and layout managers for fractional scaling (100% to 200%).
* Follows the `desktopFileName` specification for seamless icon and taskbar association under Wayland compositors (KWin / Plasma, Mutter / GNOME).

---

## Safety

* **Allow-Listed Registers Only:** The daemon only ever reads or writes documented G3-572 registers (`0x10`, `0x21`, `0x22`, `0x37`, `0x3A`). Arbitrary address reading or writing is impossible over D-Bus.
* **Value Clamping:** Mode writes are strictly restricted to verified constants (`0x50`, `0x54`, `0x58`, `0x5C`, `0x60`, `0x70`); manual percentages are clamped to `0–100`.
* **Readback Verification:** Every register write verifies that the register took the requested value.
* **Non-Persistent Fallback:** If a hardware error occurs, the daemon attempts a best-effort Auto fallback to keep fans running safely.
* **Root GUI Refusal:** The GUI process actively refuses execution under EUID 0 to protect desktop session configuration files and IPC boundaries.

---

## Troubleshooting

1. **Verify service status:**
   ```bash
   systemctl status predator-sensed.service
   journalctl -u predator-sensed.service -b
   ```
2. **Run non-mutating installation check:**
   ```bash
   predator-sense-check
   ```
3. **Inspect Polkit authentication:**
   If toggling fan modes fails, ensure an active desktop Polkit agent (e.g. `polkit-kde-agent` or `polkit-gnome`) is running in your desktop session.

---

## Diagnostics

Collect a comprehensive, privacy-redacted diagnostics report:

```bash
# Print report to terminal:
predator-sense-diagnostics --stdout

# Or save to a file:
predator-sense-diagnostics --output predator-diagnostics.txt
```

The report inspects kernel, DMI, service status, EC file accessibility, kernel module parameters, graphic controllers, and live telemetry sources without capturing hostnames, user paths, or process lists.

---

## Development

Run unit tests and linting from the repository root:

```bash
# Run complete hardware-free test suite:
PYTHONPATH=src QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v

# Run smoke test:
python scripts/smoke_test.py
```

All behavioral tests run without root, physical EC access, or live display servers.

---

## Credits / Upstream Projects

* Original reverse-engineering concept by [mohsunb/PredatorSense](https://github.com/mohsunb/PredatorSense).
* Additional Linux implementation ideas by [kphanipavan/PredatorNonSense](https://github.com/kphanipavan/PredatorNonSense).
* Hardware register corroboration from [NBFC Linux](https://github.com/nbfc-linux/nbfc-linux) (G3-572 profile).

---

## License

This project is licensed under the **GNU General Public License v3.0 (GPLv3)**. See [`LICENSE`](LICENSE) for details.
