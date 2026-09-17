# Working on Predator Sense

## Project scope

Predator Sense is a Python/PyQt6 desktop fan-control app for the Acer Predator
Helios 300 (2017), model G3-572-55UB. Arch Linux is the supported distribution;
CI runs static checks on Ubuntu with Python 3.12. This is a source-based desktop
app, not a web app or an installable Python package. Runtime dependencies are
PyQt6, Python, and polkit; see `requirements.txt` and `PKGBUILD`.

Features currently implemented: CPU/GPU Auto, Manual, and Turbo fan modes,
global Auto/Turbo controls, and persistent CoolBoost. Diagnostics can inspect
NVIDIA tools, but GPU overclocking and telemetry are not implemented app features.

## Where to work

| File | Responsibility |
| --- | --- |
| `src/main.py` | Entry point, environment/EC checks, QApplication, palette/styles, icon, fixed 635 × 465 window. |
| `src/frontend.py` | Widget construction, labels, geometry, and UI-only signal connections (`Ui_PredatorSense`). |
| `src/ui/main_window.py` | `MainWindow`, hardware-facing signal handlers, initial fan state, CoolBoost persistence. |
| `src/core/profiles.py` | Frozen `ModelProfile`, `PFS` enum, and G3-572 EC register/mode values. |
| `src/core/hardware.py` | EC byte reads/writes, EC access preparation, bounded subprocess helper. |
| `src/core/env_checks.py` | DMI model check; currently requires product name containing `G3-572`. |
| `src/core/logger.py` | Console logging and rotating file logs. |
| `src/font_config.py`, `fonts/` | Bundled Squares font registration and QFont helpers. |
| `background_service.py` | Independent loop that reapplies saved CoolBoost state every 15 seconds. |
| `packaging/`, `predator-sense.install` | Launchers, desktop entry, polkit policy, systemd unit, package lifecycle hooks. |
| `PKGBUILD`, `.SRCINFO` | Arch package recipe and metadata. |
| `scripts/smoke_test.py` | Required-file presence check only. |
| `scripts/collect_diagnostics.py` | Read-only system/EC diagnostics, written to a report file. |
| `.github/workflows/ci.yml`, `pyproject.toml` | Authoritative CI commands and Ruff configuration. |

Start with the relevant files rather than scanning bundled fonts, images, or
generated package contents. Check `git status --short` before editing and preserve
unrelated local work.

## Hardware and runtime constraints

- EC I/O is `/sys/kernel/debug/ec/ec0/io`. `ensure_ec_access()` may invoke
  `modprobe ec_sys write_support=1`; it is not a read-only environment probe.
- Keep hardware values in `ModelProfile`. Do not infer register addresses or
  extend supported models without model-specific evidence. Preserve the DMI gate.
- `ec_read()` returns an integer or `None`; `ec_write()` returns success/failure
  and skips unchanged bytes. Handle failures explicitly, including valid zero
  values. EC writes require byte-sized values.
- The manual sliders currently map levels 0–10 to EC values 0–100 using
  `level * 10`. Preserve this mapping unless the task explicitly changes it.
- Constructing `MainWindow` reads hardware and can write Auto mode when a register
  value is unknown. `QT_QPA_PLATFORM=offscreen` alone does not make it safe to run.
- For automated UI checks, stub `ec_read`/`ec_write` in `ui.main_window` before
  constructing the window and redirect its `STATE_FILE` to a temporary location.
  Use `PYTHONPATH=src` for imports from repository-root test scripts. Patch symbols
  where they are used: these modules import hardware functions directly.
- UI and service share `/var/lib/predator-sense/state.json`, with the JSON key
  `coolboost_enabled`. Keep both readers/writers compatible. Missing state or invalid
  JSON makes the service select CoolBoost off. The service has duplicated
  CoolBoost constants and does not call the GUI's DMI check; review it alongside
  profile or hardware-support changes.
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
python -m py_compile src/main.py src/frontend.py src/font_config.py src/core/*.py src/ui/*.py scripts/smoke_test.py
python scripts/smoke_test.py
test -f PKGBUILD
grep -q '^pkgname=predator-sense' PKGBUILD
```

When changing the service or diagnostics, also compile them; CI's explicit list
does not currently include them:

```bash
python -m py_compile background_service.py scripts/collect_diagnostics.py
```

For shell/packaging edits, syntax-check without executing the hooks:

```bash
for file in PKGBUILD predator-sense.install packaging/predator-sense packaging/predator-sense-root configure.sh; do
  bash -n "$file" || exit 1
done
```

There is no behavioral test suite or configured pytest dependency. The smoke test
does not verify fan control, UI interactions, or packaging correctness. For
behavioral changes, add focused checks with mocked hardware and temporary state;
report separately what was checked statically and what was exercised. Do not claim
hardware validation from a passing smoke test. If tooling is unavailable, state
which checks could not run.

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
- Radio-button `toggled` fires on both selection and deselection. Existing
  hardware handlers discard the boolean; review signal order and resulting EC
  writes when changing mode controls, including global controls and startup.
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
