# Predator Sense architecture and hardware contract

Updated 2026-09-18 for the Phase 1 backend work, based on the Phase 0 audit of
`master` at `aeab951`. Changes remain in the worktree. This document distinguishes
implemented behavior, supplied hardware observations, and outstanding validation.

## Assessment and product boundary

One `G3572EcBackend` now owns production EC operations for the GUI, CoolBoost
service, and EC diagnostics. It checks exact normalized DMI identity, validates
operations, serializes access, and verifies writes. The prior public raw writer,
GUI startup fallback writes, and service-specific register constants are removed.
The presentation/controller/core split is retained.

The only supported machine is the Acer Predator G3-572 (reported unit:
G3-572-55UB), with DMI product name `Predator G3-572`. The supplied hardware
observations use BIOS `V1.22`, Intel Kaby Lake-H / HD Graphics 630, and NVIDIA
GeForce GTX 1050 Ti. These are not measurements performed during development.
Other BIOS versions are exposed and logged as unvalidated, not automatically
rejected when the product matches.

Supported distributions are Arch Linux, CachyOS, and compatible Arch derivatives.
Ubuntu is a CI environment, not a promised desktop runtime. No generic model
framework or other Acer laptop support is provided. There is no D-Bus daemon,
GPU overclocking, RGB, battery control, or live telemetry display. The backend
and diagnostics provide candidate fan RPM reads for subsequent work.

The supplied machine context reports `acer_wmi` and `ec_sys`, without usable
G3-572 fan/PWM hwmon controls in `acer_wmi`. No Predator-v4 capability override,
other-model WMI quirk, or custom kernel module is used.

## Components and privilege boundary

| Component | Responsibility |
| --- | --- |
| [`src/main.py`](src/main.py) | Environment validation, explicit EC preparation, backend probe, QApplication, theme/fonts/icon, fixed 635 × 465 window. |
| [`src/frontend.py`](src/frontend.py) | Widget construction, labels, geometry, fonts. No EC operations or mode-control side effects. Its referenced `dialog.ui` remains untracked. |
| [`src/ui/main_window.py`](src/ui/main_window.py) | Semantic backend calls, observed-state rendering, actions, visible errors, verified CoolBoost persistence. Backend is injected. |
| [`src/core/profiles.py`](src/core/profiles.py) | One immutable G3-572 register/value map, `FanChannel`, `FanMode`, `HardwareIdentity`, `HardwareStatus`. |
| [`src/core/hardware.py`](src/core/hardware.py) | `G3572EcBackend`, private byte/word transport, locks, allow-list and readback verification. No subprocesses or escalation. |
| [`src/core/env_checks.py`](src/core/env_checks.py) | Authoritative normalized DMI identity and separate, explicit startup preparation. |
| [`src/core/errors.py`](src/core/errors.py) | `HardwareError` and stable `ErrorCode` categories. |
| [`src/core/state.py`](src/core/state.py) | Shared compatible CoolBoost JSON schema and atomic replacement. |
| [`background_service.py`](background_service.py) | Every 15 seconds, reads saved CoolBoost preference and reapplies it through the backend if the observed state is known and differs. |
| [`scripts/collect_diagnostics.py`](scripts/collect_diagnostics.py) | Read-only system inventory and semantic EC reads through the backend; no arbitrary EC address access. |
| [`src/core/logger.py`](src/core/logger.py) | Console and rotating file logs under the executing user's home. |
| [`src/font_config.py`](src/font_config.py), `fonts/` | Bundled Squares registration and QFont helpers; source/installed/PyInstaller lookup. |
| [`tests/`](tests/) | Hardware-free unittest cases with fake EC I/O, temporary DMI/state/logs, and offscreen Qt. |

```text
desktop entry → /usr/bin/predator-sense → pkexec
  → /usr/bin/predator-sense-root → src/main.py
  → exact DMI check → explicit EC preparation → backend probe → MainWindow

systemd (root) → background_service.py
  → same startup checks → saved CoolBoost preference → backend

diagnostics → backend read operations only

all backend transactions → exact DMI gate → process/thread locks → EC I/O
```

The installed root wrapper defaults Qt to `xcb`. Launcher paths and the polkit
`exec.path` agree. Polkit authorizes the GUI executable, not individual operations.
The existing service runs as root without sandbox directives. Privilege separation
into a dedicated daemon remains future work.

`core.env_checks.ensure_ec_access()` preserves existing startup behavior: after
checking DMI it tries a read-only open, and if the EC path is missing may run
`modprobe ec_sys write_support=1` with an eight-second timeout. It does not mount
debugfs or invoke sudo/pkexec. Only GUI/service startup calls this preparation.
Constructing/probing the backend and running diagnostics do not prepare the
system or write EC registers.

## Authoritative G3-572 hardware map

The byte mode/control values below are observations supplied with the Phase 1
request. They are centralized in `core/profiles.py`; constants are not duplicated
in the service or GUI.

| Definition | Address | Values / evidence limits |
| --- | --- | --- |
| `COOLBOOST_REGISTER` | `0x10` | `0x00` off, `0x01` on. |
| `GPU_FAN_MODE_REGISTER` | `0x21` | `0x00` firmware Auto, `0x50` explicit Auto, `0x60` Turbo, `0x70` Manual. |
| `CPU_FAN_MODE_REGISTER` | `0x22` | `0x00` firmware Auto, `0x54` explicit Auto, `0x58` Turbo, `0x5C` Manual. |
| `CPU_FAN_CONTROL_REGISTER` | `0x37` | Control domain 0–100; supplied physical confirmation is 50 (`0x32`). |
| `GPU_FAN_CONTROL_REGISTER` | `0x3A` | Control domain 0–100; supplied physical confirmation is 50 (`0x32`). |
| `CPU_FAN_RPM_REGISTER` | `0x13` | Candidate little-endian word read, plausible range 0–6122. Read-only. |
| `GPU_FAN_RPM_REGISTER` | `0x15` | Candidate little-endian word read, plausible range 0–6122. Read-only. |

The [NBFC-Linux G3-572 configuration](https://github.com/nbfc-linux/nbfc-linux/blob/main/share/nbfc/configs/Acer%20Predator%20G3-572.json)
corroborates word reads at 19/21, control registers 55/58, control range 0–100,
and independent read calibration 0–6122. Its mode initialization/reset bytes
are different; those write sequences are not imported here.

NBFC's [Linux ec_sys implementation](https://github.com/nbfc-linux/nbfc-linux/blob/main/src/ec_sys_linux.c)
uses a two-byte positional read followed by `le16toh` in
`EC_SysLinux_ReadWord`. The backend follows that little-endian convention.
This establishes software semantics, not physical accuracy or atomicity of a
firmware-updated two-byte sample. Values above 6122 return unavailable (`None`);
zero remains a valid reading. No synthetic RPM conversion is applied.

The former profile accepted CPU `0x5D` as Manual without evidence in the supplied
contract. It now decodes as UNKNOWN. Only `0x5C` is a verified CPU Manual byte.
The complete manual range has not been physically validated in this project.

## Backend API and safety semantics

The channel-based API uses `FanChannel.CPU` / `FanChannel.GPU`:

```python
backend = G3572EcBackend()
identity = backend.get_identity()
status = backend.probe()  # no module loading, trial writes, or state changes
mode = backend.get_fan_mode(FanChannel.CPU)
backend.set_fan_mode(FanChannel.CPU, FanMode.AUTO)
backend.set_fan_mode(FanChannel.GPU, FanMode.MANUAL, manual_percent=50)
backend.set_manual_speed(FanChannel.CPU, 50)
```

The example setters are hardware actions, not a smoke test. CPU/GPU convenience
methods are also available for fan mode, manual speed, and RPM. CoolBoost uses
`get_coolboost()` / `set_coolboost(enabled)`.

- **Identity:** every transaction re-reads normalized DMI before opening EC.
  Whitespace is collapsed; matching is exact and case-sensitive. Missing/invalid
  DMI fails closed. Neither a prior successful probe nor a caller-supplied model
  can grant write access. Unsupported devices are refused for reads as well.
- **Probe:** returns identity, readable/writable flags, and an optional structured
  failure. It reads control bytes and checks opening with read/write access.
  Writability is not a trial-write guarantee; kernel/firmware can still refuse
  an operation. Actual writes are always checked and verified.
- **States:** `FanMode` distinguishes FIRMWARE_AUTO, AUTO, MANUAL, TURBO, UNKNOWN.
  Unknown well-formed values return UNKNOWN/None; transport failures raise
  `HardwareError`. No decoding, startup, or probe writes a fallback state.
- **Writes:** only the five verified control addresses and their permitted values
  are accepted by the private writer. There is no public raw-address write API.
  UNKNOWN and FIRMWARE_AUTO are not writable modes. A deliberate request for a
  verified mode may replace an unknown byte; a read alone never does so.
- **Verification:** read the old byte, skip if unchanged, otherwise write one byte,
  check the transfer size, immediately re-read, and compare exactly. Verification
  errors carry register, expected, and observed fields. No blind retries occur.
- **Manual control:** reject non-integers (including booleans) and clamp integers
  to 0–100. Read control before changing mode; switch to Manual and verify, then
  apply and verify the requested percentage under the same lock. A Manual request
  without a percentage preserves an existing valid control value. The UI retains
  its 0–10 slider mapped through `level * 10`.
- **Partial failures:** EC operations are not rollback transactions. If mode
  succeeds and speed fails, Manual can remain selected. A global action can
  succeed on CPU and fail on GPU. Callers report the error and re-read actual
  state instead of guessing rollback writes or claiming full success.
- **Errors:** categories distinguish identity unavailable, unsupported hardware,
  missing module, unavailable debugfs/device, permissions, short/malformed reads,
  I/O failure, invalid input, refused writes, verification mismatch, and lock
  timeout. Missing-interface classification checks module presence and mount
  availability. No hardware errors are silently converted into successful writes.
- **Concurrency:** one process-wide RLock covers complete operations across
  backend instances; a bounded advisory flock on the EC descriptor coordinates
  cooperating GUI/service processes. Positional unbuffered I/O avoids shared seek
  offsets and stale readback buffers. Locks cannot serialize firmware or unrelated
  software that ignores them. Kernel/debugfs flock behavior requires device testing.
- **Logging:** verified changes use INFO; ordinary reads are silent and implausible
  RPM samples use DEBUG. Callers log actionable failures. Logs are not telemetry
  storage and may be under root's home for privileged launches.

## Controller, state, and service

MainWindow accepts a backend instance and reads observed modes, manual controls,
and CoolBoost before connecting action signals. Refresh blocks signals; unknown
modes leave the relevant radio group unselected and unknown CoolBoost uses an
indeterminate checkbox. Firmware and explicit Auto share the visible Auto choice.
Manual sliders initialize from observed control values, rounded to the existing
10-point UI steps; the tooltip retains the observed percentage.

Radio actions use `clicked`, so deselection cannot issue a second mode write.
Global controls invoke semantic operations for each channel. Individual controls
remain usable after a global action; the global selection reflects observed
agreement. Failed actions display an error and refresh actual state. Only a
verified CoolBoost write is followed by saving its preference.

UI and service share `/var/lib/predator-sense/state.json` and boolean key
`coolboost_enabled`. Saving writes and flushes a temporary file in the same
folder, then atomically replaces the old file. Invalid/missing JSON, non-object
JSON, or a non-boolean enabled value selects off, preserving the original missing
state default while rejecting truthy strings. Persistence errors are reported.

The service uses the same DMI guard and backend, checks observed CoolBoost, skips
unchanged values, and refuses automatic reapplication after an unknown/failed
read. It does not expose a generic writer or duplicate register constants.
The preference read, EC action, and preference save are not a cross-process atomic
unit: concurrent GUI/service intent can briefly differ until the next service
iteration. A future daemon should own this coordination.

## Diagnostics, packaging, and unresolved audit findings

Diagnostics now use the backend for mode/CoolBoost/manual/RPM reads and restrict
`--ec-addresses` to the seven known read locations. The EC section contains decoded
values rather than the previous raw-byte dump. System commands remain read-only.
Explicit `acer_wmi` status, daemon status, and narrower privacy filtering from the
Phase 0 proposal are still pending. Existing reports include cwd, UID/EUID,
hostname via `uname -a`, and full NVIDIA output that can include process details.
These are unnecessary for a minimal hardware report. Existing ignored reports
are not reproduced here. The existing shell pipelines and zero-duration NVIDIA
sampling behavior also remain audit follow-up items.

[`PKGBUILD`](PKGBUILD) installs each Python module explicitly, including the new
errors/state modules. Version/dependency metadata is unchanged, so `.SRCINFO`
needs no regeneration for this module-list change. The recipe still builds from
the local checkout with empty source/checksum lists and `url="local"`; publishing
a reproducible source recipe is separate work. Diagnostics and license documents
are not installed. Fonts are installed with the application and system-wide.

Package install/upgrade hooks enable and immediately start the root service;
removal disables/stops it. Hook errors are suppressed. Do not install a package or
run these hooks as a validation step. The existing polkit/launcher setup still
runs the GUI as root. PyInstaller is undeclared and its build is untested;
`configure.sh` remains deprecated. Frontend geometry and custom fonts are retained;
no visual redesign was made.

### License and asset provenance (unresolved)

[`LICENSE`](LICENSE) is GPLv3, while `PKGBUILD` and `.SRCINFO` declare MIT. Local
history starts with MIT at `676c909`, deletes it at `5999abd`, later adds GPLv3,
and removes duplicate `LICENSE.md` at `3f60208`. This does not settle project
licensing intent or inherited notice requirements.

The configured upstream is
[`kphanipavan/PredatorNonSense`](https://github.com/kphanipavan/PredatorNonSense),
whose [license](https://github.com/kphanipavan/PredatorNonSense/blob/master/LICENSE)
is MIT with a 2021 Phani Pavan Kambhampati copyright notice. The README credits
[`mohsunb/PredatorSense`](https://github.com/mohsunb/PredatorSense), which returned
404 during the Phase 0 audit. Maintainer review must resolve reused-code lineage
and appropriate notices before reconciling metadata.

All ten bundled OTF name tables identify TypeType copyright (2014), designers
Ivan Gladkikh and Olexa Volochay, and reserved rights. No embedded license text or
URL was found, and no tracked font license document is present. Icon/screenshot
provenance is also undocumented. This does not establish redistribution rights;
font/asset grants and packaging of notices remain maintainer decisions. Phase 1
does not change licensing declarations.

## Verification and remaining physical validation

CI installs `requirements-dev.txt` on Python 3.12, runs Ruff, compiles GUI/core,
service and diagnostics, executes unittest with `PYTHONPATH=src` and offscreen
Qt, runs the required-file smoke check, and checks the package name. Tests do not
need root, real EC access, or a pytest dependency.

Local Phase 1 verification passed on **Python 3.12.14**: all **47 unittest
cases**, `ruff check .`, compilation of GUI/core/service/diagnostics, required-file
smoke check, package-name sanity check, shell syntax checks, and `git diff --check`.
The initial 45-case suite also passed on Python 3.14.7. Python/dependency runtimes
were isolated under `/tmp`; no system packages or services were changed.

The suite exercises exact/normalized/missing/mismatched DMI, BIOS reporting,
all fan states, unknown bytes, CoolBoost, percentage conversion and clamping,
manual transition order, unchanged-write avoidance, RPM endianness/plausibility,
short/malformed reads, invalid inputs and allow-lists, missing module/debugfs/EC,
permissions and I/O errors, verification failures and partial transitions,
thread contention, descriptor lock contention, temporary-file transport, mocked
UI interactions, service behavior, diagnostics, and atomic saved-state handling.

Test setup redirects logger output before importing hardware modules and redirects
DMI and EC paths. MainWindow tests inject fake backend I/O and temporary state.
No real GUI/service, live diagnostics, module loading, EC access, package
installation, or system-service operation is used for validation. Passing static
checks and fake-hardware tests does not establish physical hardware correctness.

Real G3-572 validation remains necessary for RPM plausibility/stability,
immediate readback timing, descriptor locking on debugfs, firmware resets and
suspend/resume, mode transitions, and CoolBoost reapplication. The manual range
beyond the supplied 50% observation and BIOS versions other than V1.22 remain
unvalidated. Do not restore `0x5D` decoding or add trial register writes without
model-specific evidence. No commit, push, release, or publication is part of this
work.

## Phase 1 changed files

- Backend: `src/core/profiles.py`, `src/core/hardware.py`,
  `src/core/env_checks.py`, new `src/core/errors.py`, new `src/core/state.py`.
- Callers: `src/main.py`, `src/frontend.py`, `src/ui/main_window.py`,
  `background_service.py`, `scripts/collect_diagnostics.py`.
- Validation and packaging: new `tests/support.py`, new `tests/test_backend.py`,
  new `tests/test_callers.py`, `.github/workflows/ci.yml`,
  `scripts/smoke_test.py`, `PKGBUILD`.
- Documentation: `ARCHITECTURE.md` (updates the existing uncommitted audit),
  `AGENTS.md`, `README.md`.
