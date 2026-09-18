# Predator Sense architecture and hardware contract

Updated 2026-09-18 for Phase 2. The Phase 0 audit and Phase 1 backend are retained
as the hardware evidence and safety foundation. Phase 2 moves all production EC
access and saved-state ownership into a root daemon. Real-device validation is
separate from the mocked and private-bus verification below.

## Product and process boundary

Only normalized DMI product `Predator G3-572` is supported (reported unit
G3-572-55UB, BIOS V1.22, Intel Kaby Lake-H / HD Graphics 630 and GTX 1050 Ti).
Other BIOS versions are reported as unvalidated. Arch Linux, CachyOS, and
compatible Arch derivatives are the target; Ubuntu hosts hardware-free CI.
No other Acer models, generic model framework, GPU OC, RGB, battery control,
Predator-v4 overrides, or custom kernel modules are included.

```text
normal desktop user
  predator-sense → Python/PyQt6 GUI → asynchronous QtDBus client
                                          │ system D-Bus
                                          ▼
root systemd service: predator-sensed
  narrow method dispatcher → Polkit for mutations → serialized controller
                                                      │
                         G3572EcBackend ← exact DMI gate
                               │
                  /sys/kernel/debug/ec/ec0/io
```

The GUI refuses execution as root and imports neither the backend, environment
preparation, nor the state writer. It preserves the desktop user's display/theme
and accessibility environment. No launcher sets `QT_QPA_PLATFORM`; the package
supplies `qt6-wayland`, allowing Qt's session-based Wayland/X11 selection. Source
launches require the matching system service/policies to be installed separately.

## Components

| Component | Responsibility |
| --- | --- |
| [`src/main.py`](src/main.py) | Normal-user GUI entry point, root refusal, QApplication, styling/fonts/icon, fixed 635 × 465 window. |
| [`src/frontend.py`](src/frontend.py) | Presentation and service-status label; no hardware behavior. Its referenced `dialog.ui` remains untracked. |
| [`src/ui/main_window.py`](src/ui/main_window.py) | Client actions, debounced sliders, availability/busy/error rendering, observed state. |
| [`src/service/client.py`](src/service/client.py) | Nonblocking QtDBus calls, timeouts, error mapping, state refresh, one pending mutation. |
| [`src/service/protocol.py`](src/service/protocol.py) | Stable names, signatures, strict validation, generated introspection; no I/O. |
| [`src/daemon_main.py`](src/daemon_main.py), [`src/service/daemon.py`](src/service/daemon.py) | Root-only daemon entry, system bus, sender-based Polkit checks, bounded request queue, serialized dispatch. No Qt dependency in daemon code. |
| [`src/service/controller.py`](src/service/controller.py) | Hardware operations, saved CoolBoost restoration/persistence, fixed sysfs temperature reads. |
| [`src/core/hardware.py`](src/core/hardware.py) | Guarded G3572EcBackend, private EC byte/word transport, locking and verified writes. |
| [`src/core/profiles.py`](src/core/profiles.py), [`src/core/errors.py`](src/core/errors.py) | Immutable one-machine map, enums/identity/status and structured hardware failures. |
| [`src/core/env_checks.py`](src/core/env_checks.py) | Authoritative exact DMI gate and bounded daemon-only ec_sys preparation. |
| [`src/core/state.py`](src/core/state.py) | Compatible atomic CoolBoost persistence, called only by daemon in production. |
| [`src/core/logger.py`](src/core/logger.py) | GUI user-home logs; daemon sets LOG_PATH=None and uses journal-captured streams. |
| [`scripts/collect_diagnostics.py`](scripts/collect_diagnostics.py) | System inventory and read-only D-Bus cooling queries; never opens EC itself. |
| [`tests/`](tests/) | Fake hardware/transport tests and real Qt/dbus-next wire tests on a private bus with fake Polkit. |

## D-Bus and authorization contract

- Bus name: `io.github.iashutoshtiwari.PredatorSense`
- Object: `/io/github/iashutoshtiwari/PredatorSense`
- Interface: `io.github.iashutoshtiwari.PredatorSense.Control`
- Polkit action: `io.github.iashutoshtiwari.predatorsense.control`
- Domain error prefix: `io.github.iashutoshtiwari.PredatorSense.Error.`

All public methods use scalar D-Bus types. Unknown integers are `-1`, preserving
zero as a valid value. The interface is introspectable; there are no arbitrary
address/path/command methods, writable properties, or caller-supplied identities.

| Method | Input signature | Output and meaning |
| --- | --- | --- |
| GetHardwareIdentity | empty | `ssbb`: product, BIOS (empty if unavailable), supported, tested BIOS. |
| GetStatus | empty | `bss`: control-ready, error code, explanation/startup warning. |
| GetFanState | empty | `ssii`: CPU mode, GPU mode, CPU control %, GPU control %. |
| GetCoolBoost | empty | `i`: -1 unknown, 0 off, 1 on. |
| GetTemperatures | empty | `ii`: CPU/GPU millidegrees C or -1; hottest valid coretemp/nvidia/nouveau hwmon input. |
| GetFanSpeeds | empty | `ii`: candidate CPU/GPU RPM or -1. |
| SetCpuFanMode / SetGpuFanMode | `s` | Empty result after verification; auto, turbo, or manual only. Manual preserves existing valid control value. |
| SetCpuManualSpeed / SetGpuManualSpeed | `i` | Empty result; strictly 0–100, enters Manual then applies/verifies speed. |
| SetCoolBoost | `b` | Empty result after verification and atomic persistence. |
| SetGlobalAuto / SetGlobalTurbo | empty | Empty result after sequential CPU/GPU verification. Partial failure is reported. |

Read methods do not authenticate. Each mutation validates its signature, enum,
and range before checking Polkit. Unlike the local backend's integer clamping,
the D-Bus boundary rejects percentages outside 0–100. The dispatcher uses the
unique sender supplied by the bus as a `system-bus-name` Polkit subject, never a
PID/UID claimed in arguments. It rechecks that sender's connection after waiting
for authorization and the operation lock. If authorization fails, times out,
is dismissed, or the sender disappears, no control operation is invoked.

The installed bus policy permits root alone to own the name and lists every
routable method explicitly. Routing permission is not mutation authorization;
the dispatcher always calls Polkit. Inactive/remote sessions are denied by the
default action policy, while an active local session uses `auth_admin_keep`.
Temporary retention is managed by Polkit (not a permanent app authorization),
and calls share one action without caller-controlled authorization details.
See the [Polkit authorization API](https://polkit.pages.freedesktop.org/polkit/eggdbus-interface-org.freedesktop.PolicyKit1.Authority.html)
and [retained-authorization guidance](https://polkit.pages.freedesktop.org/polkit/polkit.8.html).

Authorization has a 110-second deadline and cancellation request; GUI mutations
have a 120-second deadline, reads four seconds. There are at most four pending
calls per sender and 32 overall. An asyncio lock serializes controller operations;
blocking hardware work runs off the event loop. Backend locks remain in place.
The protocol/client cannot import the hardware through their dependency chain.

## Daemon startup, lifecycle, and session behavior

`predator-sensed.service` uses Type=dbus, the stable BusName, root identity,
Restart=on-failure, and a private state directory. D-Bus activation can start it
on demand. It claims its name before initialization so GetStatus can report
Starting while it validates DMI and prepares EC. Unsupported/unavailable hardware
is reported through the bus rather than causing the GUI to exit.

Only daemon startup calls `ensure_ec_access()`: after exact DMI validation it
may run the fixed `modprobe ec_sys write_support=1` command with an eight-second
limit. It never mounts debugfs or runs shell commands from D-Bus inputs. The
backend itself still performs no escalation or preparation.

The service uses NoNewPrivileges, PrivateTmp, ProtectHome, ProtectSystem=full,
ProtectControlGroups, RestrictSUIDSGID, LockPersonality, and AF_UNIX restriction.
It deliberately avoids ProtectKernelTunables/ProtectKernelModules/PrivateDevices,
which could block writable debugfs or module preparation. These settings require
real-device validation; the service is not installed or launched by unit tests.

The GUI polls read-only state every two seconds, suspends refresh while editing a
slider or awaiting a mutation, and coalesces slider changes over 250 ms. It stays
open on absence/startup, denied/cancelled authorization, timeout, unsupported
hardware, and EC errors. Controls are disabled while unavailable or busy; status
text and tooltips describe the action needed. Stale replies from reads started
before a mutation are discarded. A timed-out request may already have applied,
so observed state is refreshed instead of assuming a rollback.

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

The daemon-internal channel-based API uses `FanChannel.CPU` / `FanChannel.GPU`:

```python
backend = G3572EcBackend()
identity = backend.get_identity()
status = backend.probe()  # no module loading, trial writes, or state changes
mode = backend.get_fan_mode(FanChannel.CPU)
backend.set_fan_mode(FanChannel.CPU, FanMode.AUTO)
backend.set_fan_mode(FanChannel.GPU, FanMode.MANUAL, manual_percent=50)
backend.set_manual_speed(FanChannel.CPU, 50)
```

These setters are daemon-only hardware actions, not GUI calls or a smoke test. CPU/GPU convenience
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
  cooperating backend processes (only the daemon owns production EC access). Positional unbuffered I/O avoids shared seek
  offsets and stale readback buffers. Locks cannot serialize firmware or unrelated
  software that ignores them. Kernel/debugfs flock behavior requires device testing.
- **Logging:** verified changes use INFO; ordinary reads are silent and implausible
  RPM samples use DEBUG. Callers log actionable failures. Logs are not telemetry
  storage and may be under root's home for privileged launches.

## State migration and retired paths

The daemon alone owns `/var/lib/predator-sense/state.json`, retaining the boolean
`coolboost_enabled` schema and atomic same-directory replacement. A verified
SetCoolBoost request persists its result. A persistence error can follow an
applied hardware change and is reported honestly.

At daemon startup only a valid existing boolean preference is restored, once,
and only after a known CoolBoost read. Missing/malformed JSON or unknown EC does
not force off. The old 15-second loop is removed. This avoids competing writers
but does not continually override firmware; resume/firmware resets can require
a new explicit user request. A future resume policy needs device evidence.

The old `background_service.py`, root GUI wrapper, GUI-exec Polkit action, and
`predator-sense.service` are removed. Upgrade hooks stop/disable that legacy unit,
preserve saved state, then enable/start/restart `predator-sensed.service`. The
new unit also declares a conflict with the old unit. Existing running legacy GUI
processes should be closed during upgrade; their removed files do not terminate
already-running processes automatically.

## Diagnostics, packaging, and unresolved audit findings

Diagnostics request cooling data through D-Bus and restrict their location selector
to the seven known read locations; it is no longer a raw EC reader. Temperature
sources are read-only sysfs data. Missing NVIDIA hwmon temperatures return
unavailable instead of executing a privileged external tool. GUI telemetry display
is not expanded in this phase.

Remaining diagnostics audit work includes explicit acer_wmi/service inventory
and narrower privacy filtering. The existing report includes cwd, UID/EUID,
hostname through uname, and NVIDIA process details. Existing shell pipelines and
zero-duration GPU sampling behavior are unchanged.

PKGBUILD explicitly includes all daemon/client modules, the unprivileged launcher,
daemon launcher, systemd unit, D-Bus activation/routing policy, and new Polkit
action. Dependencies include python-dbus-next, dbus, and qt6-wayland; pkgrel is 2
and `.SRCINFO` was regenerated with makepkg. The recipe still uses local source
and `url="local"`; publishing a reproducible source recipe is separate work.
Fonts remain installed both with the app and system-wide. Diagnostics/license
files are not installed, and the PyInstaller recipe remains outside CI.

Installation/upgrade has persistent system and potential hardware effects. Do not
execute hooks or install the package as a test. Package verification must use
syntax checks or a non-installing normal-user build. `configure.sh` is deprecated.

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
font/asset grants and packaging of notices remain maintainer decisions. Phase 2
does not change licensing declarations.

## Verification and remaining physical validation

Local Phase 2 results: **72 tests passed on Python 3.12.14**, including four real
Qt/dbus-next private-bus integration cases. Ruff, compilation, smoke checks,
shell syntax, documentation links, `.SRCINFO` consistency, and diff whitespace
checks passed. A non-installing `makepkg --nodeps -f` build succeeded in `/tmp`;
archive inspection confirmed the new daemon/bus/Polkit assets and absence of
legacy launchers. `--nodeps` skips host dependency checks, so it is not an installed
runtime test. The systemd unit passed `systemd-analyze verify` against a staged
executable path; the uninstalled `/usr/bin/predator-sensed` path cannot be checked
as an installed command yet. A mocked offscreen window was rendered and inspected,
including readable daemon-unavailable status within the existing geometry.

Python 3.12 tests cover the retained backend contract, D-Bus signature/enum/range
validation, all mutation authorization paths, caller disconnection, backend and
persistence errors, startup status, queue bounds, concurrent operations, state
migration, and daemon-absent GUI behavior. Private-bus integration starts an
unprivileged dbus-daemon and fake-hardware/fake-Polkit fixture, then uses the real
QtDBus client and dbus-next dispatcher. This verifies wire signatures, reads,
mutations, denied authorization, and disappearance/reconnection without touching
the system bus. CI requires dbus-daemon to avoid silently skipping that coverage.

Session tests check that startup preserves Qt platform selection under simulated
Wayland and X11 session environments, and refuses root before QApplication. They
do not run a Plasma compositor or a real Polkit agent. The mocked offscreen window
is checked separately for layout. Hardware fixtures redirect DMI, EC paths,
logs, and state before operations. No live EC access, module loading, package
installation, service changes, or system-bus control is performed for validation.

Real G3-572 follow-up remains necessary for native Plasma Wayland/X11 windows,
actual session Polkit dialogs and retained authorization, installed service
activation/hardening, package upgrade from the old unit, RPM plausibility,
immediate readback timing, descriptor locking on debugfs, and suspend/firmware
reset behavior. Manual values beyond the supplied 50% observation and BIOS
versions other than V1.22 remain unvalidated. The license/provenance findings
above remain open. No commit or release is part of this work.

## Phase 2 file inventory

- New daemon/client: `src/daemon_main.py`, `src/service/__init__.py`,
  `src/service/protocol.py`, `src/service/controller.py`, `src/service/daemon.py`,
  `src/service/client.py`.
- Updated callers/core: `src/main.py`, `src/frontend.py`, `src/ui/main_window.py`,
  `src/core/logger.py`, `src/core/env_checks.py`, `scripts/collect_diagnostics.py`.
- New packaging: `packaging/predator-sensed`, `packaging/predator-sensed.service`,
  `packaging/io.github.iashutoshtiwari.PredatorSense.conf`,
  `packaging/io.github.iashutoshtiwari.PredatorSense.service`,
  `packaging/io.github.iashutoshtiwari.predatorsense.policy`.
- Updated packaging/dependencies: `packaging/predator-sense`, `predator-sense.install`,
  `PKGBUILD`, `.SRCINFO`, `requirements.txt`.
- Removed: `background_service.py`, `packaging/predator-sense-root`,
  `packaging/predator-sense.service`, `packaging/org.predatorsense.policy`.
- Tests/CI: `tests/test_callers.py`, `tests/test_service.py`,
  `tests/test_dbus_integration.py`, `tests/dbus_fixture.py`, `tests/test_packaging.py`,
  `.github/workflows/ci.yml`, `scripts/smoke_test.py`.
- Documentation: `README.md`, `ARCHITECTURE.md`, `AGENTS.md`.
