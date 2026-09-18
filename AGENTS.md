# Working on Predator Sense

## Project scope

Predator Sense is a Python/PyQt6 desktop fan-control app for the Acer Predator
Helios 300 (2017), model G3-572-55UB. Arch Linux, CachyOS, and compatible Arch-derived distributions are supported;
CI runs static and hardware-free behavioral checks on Ubuntu with Python 3.12. This is a source-based desktop
app, not a web app or an installable Python package. Runtime dependencies include
PyQt6, Python, dbus-next, D-Bus, polkit, Qt Wayland, and Qt SVG; see `requirements.txt` and `PKGBUILD`.

Features currently implemented: CPU/GPU Auto, Manual, and Turbo fan modes,
global Auto/Turbo controls, persistent CoolBoost, and daemon-side 1 Hz telemetry. Diagnostics can inspect
NVIDIA tools. The native dashboard shows CPU/GPU temperatures, candidate RPM,
four 60-second graphs, modes, CoolBoost, and compact hardware status. GPU
overclocking is not implemented.

Read [ARCHITECTURE.md](ARCHITECTURE.md) for the audited runtime and packaging,
G3-572 hardware evidence, Phase 2 daemon/client contract, Phase 3 telemetry, Phase 4 RPM validation, Phase 5 UI, and remaining audit risks.
It distinguishes implemented safeguards from outstanding physical validation. Keep it current when changing the architecture or hardware contract.

## Where to work

| File | Responsibility |
| --- | --- |
| `src/main.py` | Unprivileged GUI entry point, root refusal, QApplication, desktop identity, theme/icon, resizable window. |
| `src/frontend.py` | Layout-managed dashboard construction and labels (`Ui_PredatorSense`). |
| `src/ui/main_window.py` | `MainWindow`, asynchronous client actions, service status, and observed-state rendering. |
| `src/core/profiles.py` | Single G3-572 constant map, `FanChannel`, `FanMode`, identity/status types. |
| `src/core/hardware.py` | `G3572EcBackend`, private EC transport, locking, verified semantic operations. |
| `src/core/env_checks.py` | Exact normalized DMI identity gate and daemon-only bounded startup EC preparation. |
| `src/core/errors.py`, `src/core/state.py` | Structured hardware failures and atomic cooling-state persistence. |
| `src/core/logger.py` | Console logging and rotating file logs. |
| `src/font_config.py` | System UI and numeric font helpers; no bundled font registration. |
| `src/ui/theme.py`, `src/ui/instruments.py` | Central theme/desktop identity, passive telemetry cards, QPainter graphs and CoolBoost switch. |
| `src/daemon_main.py`, `src/service/daemon.py` | Root entry point, system bus ownership, Polkit checks, serialized requests. |
| `src/service/lifecycle.py` | Trusted logind sleep signals, serialized recovery and bounded health retry rate. |
| `src/service/controller.py` | Daemon operations, validated cooling-state restore, serialized EC sampling. |
| `src/service/protocol.py`, `src/service/client.py` | Stable wire contract and asynchronous unprivileged Qt client. |
| `src/service/telemetry_model.py` | Immutable snapshots, per-sensor status/freshness, versioned JSON wire schema. |
| `src/service/sensors.py`, `src/service/telemetry.py` | Daemon-only coretemp/NVML sources, isolated bounded workers, monotonic 1 Hz sampling. |
| `packaging/`, `predator-sense.install` | Launchers, desktop entry, polkit policy, systemd unit, package lifecycle hooks. |
| `PKGBUILD`, `.SRCINFO` | Arch package recipe and metadata. |
| `tests/` | Hardware-free backend, controller, service, and diagnostics checks using unittest. |
| `scripts/smoke_test.py` | Required-file presence check only. |
| `scripts/validate_fan_telemetry.py` | Read-only cached RPM validation; no daemon activation, modes changed separately in GUI. |
| `scripts/collect_diagnostics.py` | Read-only system/EC diagnostics, written to a report file. |
| `.github/workflows/ci.yml`, `pyproject.toml` | Authoritative CI commands and Ruff configuration. |

Start with the relevant files rather than scanning bundled fonts, images, or
generated package contents. Check `git status --short` before editing and preserve
unrelated local work.

## Hardware and runtime constraints

- EC I/O is `/sys/kernel/debug/ec/ec0/io`. The backend never prepares the system
  or escalates privileges. `core.env_checks.ensure_ec_access()` is an explicit
  daemon startup step that may run `modprobe ec_sys write_support=1` after the DMI gate.
- Keep hardware constants in `core/profiles.py`. Only normalized product name
  `Predator G3-572` is accepted. Preserve the gate on every backend transaction;
  do not expose a public raw-address writer or add other model support.
- Backend operations raise `HardwareError` with an `ErrorCode`; unknown modes
  return `FanMode.UNKNOWN`, unknown CoolBoost/manual control and implausible RPM
  return `None`. Zero is valid. Writes are allow-listed, skip unchanged bytes,
  and verify readback. Manual mode plus its control write share a transaction.
- RPM words at CPU `0x13` and GPU `0x15` are read-only, little-endian,
  unsmoothed candidate RPM. Reject values above 6122, preserving legitimate zero.
  Implausible reads warn at most once per channel per backend instance per 60
  seconds. The validation script pins an existing D-Bus owner and uses
  NO_AUTOSTART; do not let validation activate startup CoolBoost restoration.
- Sliders map levels 0–10 to percentages 0–100 using `level * 10`. Preserve this
  mapping; the backend rejects non-integer inputs and clamps integer percentages.
- MainWindow receives `ServiceClient`, never a hardware backend. GUI modules must
  not import `core.hardware`, `core.env_checks`, or `core.state`. Only the daemon
  owns production EC access. Diagnostics use D-Bus for EC data too.
- GUI tests inject a fake client/transport. Backend tests redirect DMI, EC paths,
  state and logging through `tests/support.py`. Offscreen Qt alone is not hardware
  isolation. Use `PYTHONPATH=src` for repository-root tests.
- Telemetry uses one cached `GetTelemetrySnapshot()` call per client poll. Keep
  sampling in the daemon and the timer/history in `ServiceClient`, never widgets.
  CPU/GPU/EC workers each allow one pending read. Preserve monotonic deadlines,
  120-snapshot history bounds, per-sensor errors, and 2.5-second freshness checks.
  Never substitute zero for unavailable readings or write telemetry to disk.
- CPU discovery matches hwmon `name=coretemp`, prefers `Package id 0`, then the
  hottest readable coretemp input. GPU uses optional `nvidia-ml-py`/`pynvml` with
  five-second failure retry; no per-second `sensors` or `nvidia-smi` subprocess.
  Tests inject NVML and sysfs sources; never initialize real NVML for validation.
- Only the daemon writes `/var/lib/predator-sense/state.json`, preserving the
  `coolboost_enabled` boolean key in the versioned cooling-state schema. Restore
  validated modes/manual percentages/CoolBoost after a healthy probe at startup
  and logind resume. Missing/invalid state selects explicit Auto/Auto/Off.
  Stop fallback must not overwrite desired preferences. Recovery health checks
  back off to 30 seconds; failed writes get one best-effort Auto fallback.
  See ARCHITECTURE.md for durability, suspend and cleanup limitations.
- Every D-Bus mutation must check Polkit against the bus-provided unique sender.
  Never accept a caller-supplied identity, arbitrary address, command, or path.
  Reads are unauthenticated; method routing is explicitly allow-listed in bus
  policy. Keep the protocol, introspection, policy, and packaging consistent.
- Leave Qt platform selection to the user's session. Do not force xcb/Wayland or
  launch the GUI as root. Mouse slider drags commit on release; keyboard changes
  debounce for 250 ms. Preserve signal blocking during observed-state updates.
- Importing modules that initialize a logger creates log directories/files under
  `Path.home() / ".local/state/predator-sense/app.log"`. Privileged launches may
  use journal-only logging via `LOG_PATH = None`. In isolated tests, configure the logger's
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
python -m py_compile src/main.py src/frontend.py src/font_config.py src/core/*.py src/ui/*.py src/service/*.py src/daemon_main.py scripts/smoke_test.py scripts/collect_diagnostics.py scripts/validate_fan_telemetry.py
PYTHONPATH=src QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
python scripts/smoke_test.py
test -f PKGBUILD
grep -q '^pkgname=predator-sense' PKGBUILD
```

For shell/packaging edits, syntax-check without executing the hooks:

```bash
for file in PKGBUILD predator-sense.install packaging/predator-sense packaging/predator-sensed configure.sh; do
  bash -n "$file" || exit 1
done
```

Behavioral tests use standard-library unittest, fake EC transports/temporary files,
normalized DMI fixtures, temporary state/logs, and offscreen PyQt6. No pytest or root
is needed. The smoke test checks file presence only. Report static checks and
mocked behavior separately; neither proves physical hardware behavior. Private-bus
tests start only an isolated dbus-daemon with fake EC and fake Polkit, never the
production daemon/system bus. Real Plasma/Polkit-agent behavior remains a device check.
`tests/telemetry_soak.py` is an opt-in three-minute Qt/private-bus run with fake
sensors, including stalled/failing GPU reads; it never uses the production bus.

## Editing conventions

- Follow the surrounding Python style: four spaces, explicit imports, type hints
  where practical, and the shared logger. Ruff targets Python 3.12, enables `E`
  and `F`, and limits lines to 120 characters. Only `src/frontend.py` ignores
  `E501`; avoid unrelated formatting changes.
- Keep widget presentation in `frontend.py`, controller behavior in
  `ui/main_window.py`, EC access in `core/hardware.py`, and model values in
  `core/profiles.py`.
- `frontend.py` is now hand-maintained and uses Qt layouts. Keep it presentation-only;
  graphs/cards consume snapshots/history, never poll or import hardware.
- Use `ui/theme.py` for colors and desktop identity. UI fonts come from the system;
  the old bundled Squares files remain unlicensed audit material and are not used
  or installed. Do not reactivate them without a confirmed grant.
- Radio-button `toggled` fires on both selection and deselection. The controller
  uses `clicked` for hardware actions and blocks signals while refreshing observed
  state. Preserve zero-write startup and test global/individual transitions.
- Use `font_config` helpers for new widgets. Preserve asset lookup for source,
  installed, and PyInstaller layouts. Check compact layouts, large fonts, and
  100–200% scaling. `tests/render_dashboard.py` renders explicitly simulated data.
- Keep subprocess calls bounded, handle missing commands, and use argument lists
  in hardware code. Diagnostics should remain read-only with respect to hardware
  and system configuration.

## Packaging and known repository details

- `PKGBUILD` explicitly installs each Python module under
  `/usr/share/predator-sense`; adding or moving modules requires updating that
  list. Install the original SVG under app assets and hicolor/scalable/apps; keep
  the desktop filename/icon and Qt desktopFileName aligned with `APP_ID`.
- The desktop launcher executes Python as the normal user. `predator-sensed.service`
  runs the root daemon as Type=dbus. Polkit gates control methods, not GUI launch.
  D-Bus service, object, interface, and action names live in `service/protocol.py`.
- Upgrade hooks stop/disable legacy `predator-sense.service`, preserve state, and
  start/restart `predator-sensed.service`. `makepkg -si` has persistent hardware
  effects. For non-installing build validation, use `makepkg -f` as a normal user.
- Keep systemd hardening compatible with writable debugfs and ec_sys preparation;
  do not add ProtectKernelTunables/ProtectKernelModules blindly. Never execute
  package lifecycle hooks merely to test syntax.
- Regenerate `.SRCINFO` with `makepkg --printsrcinfo > .SRCINFO` when changing
  package metadata. Do not hand-edit generated package trees or commit archives,
  caches, local environments, or diagnostics reports.
- `configure.sh` is deprecated and deliberately exits with status 1.
  `main.spec` is a PyInstaller recipe, but PyInstaller is not in the declared
  dependencies and that build path is not exercised by CI.
- Existing licensing metadata conflicts: `LICENSE` contains GPLv3 while
  `PKGBUILD` and `.SRCINFO` declare MIT. Do not silently resolve this discrepancy
  as part of an unrelated change.
