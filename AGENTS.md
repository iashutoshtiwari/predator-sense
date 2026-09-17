# Working on Predator Sense

## Project scope

Predator Sense is a Python/PyQt6 desktop fan-control app for the Acer Predator
Helios 300 (2017), model G3-572-55UB. Arch Linux, CachyOS, and compatible Arch-derived distributions are supported;
CI runs static and hardware-free behavioral checks on Ubuntu with Python 3.12. This is a source-based desktop
app, not a web app or an installable Python package. Runtime dependencies are
PyQt6, Python, and polkit; see `requirements.txt` and `PKGBUILD`.

Features currently implemented: CPU/GPU Auto, Manual, and Turbo fan modes,
global Auto/Turbo controls, and persistent CoolBoost. Diagnostics can inspect
NVIDIA tools; the backend exposes candidate fan RPM reads, but there is no live
telemetry display or GPU overclocking feature.

Read [ARCHITECTURE.md](ARCHITECTURE.md) for the audited runtime and packaging,
G3-572 hardware evidence, Phase 1 backend contract, and remaining audit risks.
It distinguishes implemented safeguards from outstanding physical validation. Keep it current when changing the architecture or hardware contract.

## Where to work

| File | Responsibility |
| --- | --- |
| `src/main.py` | Entry point, environment/EC checks, QApplication, palette/styles, icon, fixed 635 × 465 window. |
| `src/frontend.py` | Widget construction, labels, and geometry (`Ui_PredatorSense`). |
| `src/ui/main_window.py` | `MainWindow`, hardware-facing signal handlers, initial fan state, CoolBoost persistence. |
| `src/core/profiles.py` | Single G3-572 constant map, `FanChannel`, `FanMode`, identity/status types. |
| `src/core/hardware.py` | `G3572EcBackend`, private EC transport, locking, verified semantic operations. |
| `src/core/env_checks.py` | Exact normalized DMI identity gate and explicit bounded startup EC preparation. |
| `src/core/errors.py`, `src/core/state.py` | Structured hardware failures and shared atomic CoolBoost persistence. |
| `src/core/logger.py` | Console logging and rotating file logs. |
| `src/font_config.py`, `fonts/` | Bundled Squares font registration and QFont helpers. |
| `background_service.py` | Independent loop that reapplies saved CoolBoost state every 15 seconds. |
| `packaging/`, `predator-sense.install` | Launchers, desktop entry, polkit policy, systemd unit, package lifecycle hooks. |
| `PKGBUILD`, `.SRCINFO` | Arch package recipe and metadata. |
| `tests/` | Hardware-free backend, controller, service, and diagnostics checks using unittest. |
| `scripts/smoke_test.py` | Required-file presence check only. |
| `scripts/collect_diagnostics.py` | Read-only system/EC diagnostics, written to a report file. |
| `.github/workflows/ci.yml`, `pyproject.toml` | Authoritative CI commands and Ruff configuration. |

Start with the relevant files rather than scanning bundled fonts, images, or
generated package contents. Check `git status --short` before editing and preserve
unrelated local work.

## Hardware and runtime constraints

- EC I/O is `/sys/kernel/debug/ec/ec0/io`. The backend never prepares the system
  or escalates privileges. `core.env_checks.ensure_ec_access()` is an explicit
  startup step that may run `modprobe ec_sys write_support=1` after the DMI gate.
- Keep hardware constants in `core/profiles.py`. Only normalized product name
  `Predator G3-572` is accepted. Preserve the gate on every backend transaction;
  do not expose a public raw-address writer or add other model support.
- Backend operations raise `HardwareError` with an `ErrorCode`; unknown modes
  return `FanMode.UNKNOWN`, unknown CoolBoost/manual control and implausible RPM
  return `None`. Zero is valid. Writes are allow-listed, skip unchanged bytes,
  and verify readback. Manual mode plus its control write share a transaction.
- Sliders map levels 0–10 to percentages 0–100 using `level * 10`. Preserve this
  mapping; the backend rejects non-integer inputs and clamps integer percentages.
- MainWindow startup reads state without writing. Automated UI checks must still
  inject a fake backend before constructing it and redirect `ui.main_window.STATE_FILE`;
  offscreen Qt does not prevent hardware I/O. `tests/support.py` redirects DMI,
  EC paths, and logging; use `PYTHONPATH=src` for repository-root tests.
- UI and service share `/var/lib/predator-sense/state.json` and the boolean key
  `coolboost_enabled`, through `core/state.py`. Missing/malformed state selects
  off. The service uses the same guarded backend and skips automatic reapplication
  when the CoolBoost read is unknown or fails. Keep these paths compatible.
- Importing modules that initialize a logger creates log directories/files under
  `Path.home() / ".local/state/predator-sense/app.log"`. Privileged launches may
  therefore log under root's home. In isolated tests, configure the logger's
  `LOG_PATH` to a temporary path before importing those modules.
- Ordinary linting, compilation, and smoke checks do not need root. Do not launch
  the live GUI/service, load kernel modules, write EC registers, install packages,
  or operate system services just to validate a code change. Use hardware mocks;
  perform live hardware actions only when included in the user's authorized task.

## Development and validation

Run commands from the repository root. Use Python 3.12 for CI parity. For an
isolated development environment, for example:

```bash
python3.12 -m venv /tmp/predator-sense-venv
source /tmp/predator-sense-venv/bin/activate
python -m pip install -r requirements-dev.txt
```

The existing CI checks are:

```bash
ruff check .
python -m py_compile src/main.py src/frontend.py src/font_config.py src/core/*.py src/ui/*.py scripts/smoke_test.py background_service.py scripts/collect_diagnostics.py
PYTHONPATH=src QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
python scripts/smoke_test.py
test -f PKGBUILD
grep -q '^pkgname=predator-sense' PKGBUILD
```

For shell/packaging edits, syntax-check without executing the hooks:

```bash
for file in PKGBUILD predator-sense.install packaging/predator-sense packaging/predator-sense-root configure.sh; do
  bash -n "$file" || exit 1
done
```

Behavioral tests use standard-library unittest, fake EC transports/temporary files,
normalized DMI fixtures, temporary state/logs, and offscreen PyQt6. No pytest or root
is needed. The smoke test checks file presence only. Report static checks and
mocked behavior separately; neither proves physical hardware behavior.

## Editing conventions

- Follow the surrounding Python style: four spaces, explicit imports, type hints
  where practical, and the shared logger. Ruff targets Python 3.12, enables `E`
  and `F`, and limits lines to 120 characters. Only `src/frontend.py` ignores
  `E501`; avoid unrelated formatting changes.
- Keep widget presentation in `frontend.py`, controller behavior in
  `ui/main_window.py`, EC access in `core/hardware.py`, and model values in
  `core/profiles.py`.
- `frontend.py` has a generated-file warning, but its referenced `dialog.ui` is
  not tracked. Edit the checked-in Python carefully; do not assume it can be
  regenerated or overwrite its custom font helpers and signal wiring.
- Radio-button `toggled` fires on both selection and deselection. The controller
  uses `clicked` for hardware actions and blocks signals while refreshing observed
  state. Preserve zero-write startup and test global/individual transitions.
- Use `font_config` helpers for new widgets. Preserve asset lookup for source,
  installed, and PyInstaller layouts. Check geometry against the fixed window
  size when changing UI layout.
- Keep subprocess calls bounded, handle missing commands, and use argument lists
  in hardware code. Diagnostics should remain read-only with respect to hardware
  and system configuration.

## Packaging and known repository details

- `PKGBUILD` explicitly installs each Python module under
  `/usr/share/predator-sense`; adding or moving modules requires updating that
  list. Fonts are installed both with the app and under `/usr/share/fonts/TTSquares`.
- The installed launch chain is desktop entry → `/usr/bin/predator-sense` →
  `pkexec /usr/bin/predator-sense-root` → `/usr/bin/python` with the installed
  `src/main.py`. The root wrapper defaults Qt to `xcb`. Keep launcher paths and
  the polkit `exec.path` annotation aligned.
- Install/upgrade hooks enable and immediately start `predator-sense.service`;
  removal disables/stops it. `makepkg -si` therefore has persistent system and
  hardware effects. To validate a package build on Arch without installing it,
  use `makepkg -f` as a normal user when build validation is needed.
- Regenerate `.SRCINFO` with `makepkg --printsrcinfo > .SRCINFO` when changing
  package metadata. Do not hand-edit generated package trees or commit archives,
  caches, local environments, or diagnostics reports.
- `configure.sh` is deprecated and deliberately exits with status 1.
  `main.spec` is a PyInstaller recipe, but PyInstaller is not in the declared
  dependencies and that build path is not exercised by CI.
- Existing licensing metadata conflicts: `LICENSE` contains GPLv3 while
  `PKGBUILD` and `.SRCINFO` declare MIT. Do not silently resolve this discrepancy
  as part of an unrelated change.
