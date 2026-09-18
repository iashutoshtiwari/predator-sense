# Predator Sense architecture and hardware contract

Updated 2026-09-18 for Phase 5. The Phase 0 audit and Phase 1 backend are retained
as the hardware evidence and safety foundation. Phase 2 moves all production EC
access and saved-state ownership into a root daemon. Phase 3 adds cached 1 Hz
telemetry with isolated sensor workers and bounded history. Real-device validation is
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
| [`src/main.py`](src/main.py) | Normal-user GUI entry point, root refusal, QApplication, system fonts, centralized theme, consistent desktop ID/SVG icon; resizable window. |
| [`src/frontend.py`](src/frontend.py) | Hand-maintained Qt layout dashboard and segmented selectors; no hardware behavior. |
| [`src/ui/theme.py`](src/ui/theme.py), [`src/ui/instruments.py`](src/ui/instruments.py) | Theme tokens/desktop identity, passive telemetry cards, QPainter graphs and switch. |
| [`src/ui/main_window.py`](src/ui/main_window.py) | Client actions, debounced sliders, availability/busy/error rendering, observed state. |
| [`src/service/client.py`](src/service/client.py) | Nonblocking QtDBus calls, timeouts, error mapping, state refresh, one pending mutation. |
| [`src/service/protocol.py`](src/service/protocol.py) | Stable names, signatures, strict validation, generated introspection; no I/O. |
| [`src/service/telemetry_model.py`](src/service/telemetry_model.py) | Immutable snapshot/reading schema, availability, freshness, JSON validation. |
| [`src/service/sensors.py`](src/service/sensors.py) | Direct coretemp sysfs discovery and lazy NVML GPU reader; daemon only. |
| [`src/service/telemetry.py`](src/service/telemetry.py) | Three bounded workers, monotonic 1 Hz publication, 120-sample history. |
| [`src/daemon_main.py`](src/daemon_main.py), [`src/service/daemon.py`](src/service/daemon.py) | Root-only daemon entry, system bus, sender-based Polkit checks, bounded request queue, serialized dispatch. No Qt dependency in daemon code. |
| [`src/service/controller.py`](src/service/controller.py) | Hardware operations, validated cooling-state restoration/persistence, serialized EC sampling. |
| [`src/core/hardware.py`](src/core/hardware.py) | Guarded G3572EcBackend, private EC byte/word transport, locking and verified writes. |
| [`src/core/profiles.py`](src/core/profiles.py), [`src/core/errors.py`](src/core/errors.py) | Immutable one-machine map, enums/identity/status and structured hardware failures. |
| [`src/core/env_checks.py`](src/core/env_checks.py) | Authoritative exact DMI gate and bounded daemon-only ec_sys preparation. |
| [`src/core/state.py`](src/core/state.py) | Atomic versioned cooling-state persistence, called only by daemon in production. |
| [`src/core/logger.py`](src/core/logger.py) | GUI user-home logs; daemon sets LOG_PATH=None and uses journal-captured streams. |
| [`scripts/collect_diagnostics.py`](scripts/collect_diagnostics.py) | System inventory and read-only D-Bus cooling queries; never opens EC itself. |
| [`tests/`](tests/) | Fake hardware/transport tests and real Qt/dbus-next wire tests on a private bus with fake Polkit. |

## D-Bus and authorization contract

- Bus name: `io.github.iashutoshtiwari.PredatorSense`
- Object: `/io/github/iashutoshtiwari/PredatorSense`
- Interface: `io.github.iashutoshtiwari.PredatorSense.Control`
- Polkit action: `io.github.iashutoshtiwari.predatorsense.control`
- Domain error prefix: `io.github.iashutoshtiwari.PredatorSense.Error.`

All public methods use scalar D-Bus types. Legacy integer reads use `-1` for
unknown, preserving zero as a valid value. The telemetry JSON uses `null` instead. The interface is introspectable; there are no arbitrary
address/path/command methods, writable properties, or caller-supplied identities.

| Method | Input signature | Output and meaning |
| --- | --- | --- |
| GetTelemetrySnapshot | empty | `s`: cached version-1 JSON snapshot, including availability and timestamps; no hardware I/O. |
| GetHardwareIdentity | empty | `ssbb`: product, BIOS (empty if unavailable), supported, tested BIOS. |
| GetStatus | empty | `bss`: control-ready, error code, explanation/startup warning. |
| GetFanState | empty | `ssii`: CPU mode, GPU mode, CPU control %, GPU control %. |
| GetCoolBoost | empty | `i`: -1 unknown, 0 off, 1 on. |
| GetTemperatures | empty | `ii`: CPU/GPU millidegrees C or -1, from the cached telemetry snapshot. |
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
calls per sender and 32 overall. An asyncio lock serializes live controller operations;
blocking hardware work runs off the event loop. Cached telemetry/temperature reads
bypass the operation lock. EC sampling and controls share a thread lock so a sample
does not interleave with a mutation. Backend locks remain in place.
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

The client polls one cached snapshot each second, including while awaiting Polkit.
Widgets contain no polling or hardware reads. Slider changes coalesce over 250 ms;
refresh does not overwrite an active slider choice. The GUI stays open on service
absence/startup, authorization failures, timeout, unsupported hardware, and EC
errors. Controls are disabled while unavailable, stale, or busy. Reads initiated
before a new mutation are discarded; subsequent ticks observe the applied state.
A timed-out mutation may already have applied, so the client does not assume rollback.

## Phase 3 telemetry contract

**Sources.** CPU discovery reads `/sys/class/hwmon/hwmon*/name` on every sample and
selects `coretemp`, independent of hwmon indices. A readable `Package id 0` label
wins over hotter core readings. If it is missing or unreadable, use the hottest
valid coretemp input, including unlabelled inputs. Values are read directly from
`temp*_input` in millidegrees and converted to Celsius, accepting 0–125 °C.
The [kernel coretemp documentation](https://www.kernel.org/doc/html/latest/hwmon/coretemp.html)
describes these inputs and package/core labels. Rediscovery permits renumbering
after driver reload; other hwmon devices are never substituted for the CPU.

GPU temperature uses [NVIDIA's nvidia-ml-py binding](https://pypi.org/project/nvidia-ml-py/)
(import name `pynvml`). NVML initializes lazily in the GPU worker, selects the
GTX 1050 Ti by device name, caches its handle, and calls `nvmlDeviceGetTemperature`.
There is no subprocess fallback. Missing bindings, driver/library, device loss,
and unavailable temperature support produce unavailable readings. Permission
errors remain distinct. A failure invalidates the handle and shuts down the
session; retries occur at most once every five seconds, without repetitive logs.
Normal shutdown calls NVML cleanup from its owning worker.

The [Arch python-nvidia-ml-py package](https://archlinux.org/packages/extra/any/python-nvidia-ml-py/)
is an optional runtime dependency because it brings an NVIDIA userspace dependency;
CPU/EC telemetry must still run without NVIDIA installed. `requirements.txt` includes
the Python binding for source environments. The service uses system Python, so a
binding installed only in the GUI's virtual environment is insufficient.

RPM, mode, CoolBoost, and manual settings come from the existing guarded semantic
G3-572 backend, with its existing little-endian RPM map and plausibility checks.
Each EC getter has independent failure handling. A failed CPU RPM read does not
clear a successful GPU RPM or fan-mode read. Sampling never writes EC or state.
These remain candidate RPM readings until physically correlated on the laptop.

**Scheduling and isolation.** The asyncio daemon publishes on anchored monotonic
one-second deadlines, skips missed slots, and never schedules catch-up bursts.
CPU, GPU, and EC each own one persistent daemon worker thread and at most one
outstanding read. Publication consumes completed results without waiting, then
requests the next read. Fast results can be about one sampling interval old when
published. A slow GPU cannot delay CPU/EC samples, the bus dispatcher, or Qt. A
stalled lane is not resubmitted or replaced with additional threads. Shutdown
signals workers without waiting on a hung foreign driver call; cleanup runs when
that read returns. Python cannot forcibly interrupt NVML: a permanently hung call
leaves that lane stale until service restart. Unexpected sampler termination exits
the service so systemd can restart it. None of these paths spawn sensor processes.

**Model and freshness.** `TelemetrySnapshot` and each `Reading` are frozen dataclasses;
readings are a fixed tuple. The snapshot exposes `cpu_temp_c`, `gpu_temp_c`,
`cpu_fan_rpm`, `gpu_fan_rpm`, `cpu_mode`, `gpu_mode`, `coolboost`, and the two
`*_manual_percent` properties. Missing values are `None`, never fabricated zeroes.
`reading(name)` exposes value, source, status, error, and monotonic sample time.
Availability is one of `available`, `unavailable`, `permission_denied`,
`backend_disconnected`, `sensor_failed`, or `stale`. Once older than 2.5 seconds,
a reading is stale; its dated value is retained in metadata/history, but its
snapshot convenience property returns `None`. Stale EC state disables controls.
Explicit read failures clear only the failed field. The wall-clock timestamp is
for display; freshness uses monotonic time, independent of wall-clock changes.

`GetTelemetrySnapshot() -> s` returns version-1 JSON with `timestamp`,
`monotonic_timestamp`, `sequence`, `epoch`, `readings`, `ready`, `code`, and `message`.
Every reading has `name`, `value`, `status`, `source`, `sampled_at`, and `error`.
Units are Celsius, RPM, percent, and seconds; missing values are JSON `null`.
The epoch changes on daemon restart. Decode validates schema, types, known fields,
finite values and ranges, and a 32 KiB payload limit. The string signature keeps
QtDBus and CLI access straightforward without custom Qt metatype registration.
The protocol introspection and explicit routing policy include the new read method;
all mutation authorization remains unchanged.

**Client and history.** `ServiceClient.start()/stop()` owns an anchored 1000 ms
precise Qt timer; each poll requests one snapshot asynchronously. One outstanding
read is allowed, independently of one outstanding mutation. Pending reads age the
last sample locally; bus failures publish missing values with disconnected/error
status. Polling automatically recovers when a daemon reappears, including a new
epoch. `telemetry_updated(snapshot)` drives the dashboard cards and graphs. The existing
`snapshot_changed` signal projects the same data onto the current fan controls.
Both engine and client retain at most 120 snapshots in memory. The client avoids
duplicating identical daemon sequence/epoch samples in history. There is no
telemetry persistence, database, or per-sample logging. Phase 5 adds the presentation layer described below.

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
  Implausible RPM words produce rate-limited WARNING messages (one per channel
  per backend instance per 60 monotonic seconds). Callers log actionable failures. Logs are not telemetry
  storage and may be under root's home for privileged launches.

## State migration and retired paths

The daemon owns `/var/lib/predator-sense/state.json`: version 1 stores CPU/GPU
requested mode, manual percent only for Manual, and the compatible boolean
`coolboost_enabled`. Strict validation rejects incomplete/unknown fields, unknown
modes, bool-as-percent, and out-of-range values. Legacy CoolBoost-only state
migrates to explicit Auto for both fans. Fresh or invalid state uses explicit
CPU/GPU Auto and CoolBoost Off: firmware retains autonomous thermal control
without silently selecting Manual or Turbo. Invalid state is reported in status.

Every successful control request saves verified desired settings with a 0600
temporary file, file fsync, atomic same-directory replace, and directory fsync.
systemd owns the 0700 state directory. Persistence failures report degraded
status; an applied hardware change cannot be rolled back atomically with disk.
Telemetry is never persisted.

Startup probes the exact gated backend, reads all control registers, validates
state, applies semantic operations with readback verification, and then becomes
ready. Failed application attempts independent CPU/GPU Auto and CoolBoost Off
fallback once and reports degraded status. It does not retry failed writes in
a loop. Saved desired preferences survive fallback and intentional shutdown.

`service/lifecycle.py` subscribes to authenticated logind-owner
[PrepareForSleep signals](https://www.freedesktop.org/software/systemd/man/latest/org.freedesktop.login1.html).
Suspend blocks controls and flushes state without EC writes. Each mutation is
already fsynced, so durability does not depend on winning the suspend notification
race; no sleep inhibitor is held. Resume probes and rereads controls before
restoration, revalidating DMI on every backend transaction. Missing EC retries
health checks after 1, 2, 4, 8, 16, then 30 seconds, without repeated writes or
per-attempt logs. EC descriptors are opened per transaction, so reacquisition
never reuses a pre-suspend descriptor. Control and lifecycle operations share
the daemon and backend locks. Logind owner changes also trigger recovery.

During suspend/recovery cached readings are unavailable. Pre-recovery EC samples
are withheld until a new sample arrives; normal telemetry expires after 2.5s.
GUI initialization/reconnect only hydrates observed state with blocked signals;
GUI close stops client polling and never changes fans or stops the daemon.

SIGTERM/SIGINT stop admission, drain operations, and attempt explicit Auto on both
fans plus CoolBoost Off, retaining desired state for restart. Cleanup is best
effort: SIGKILL, power loss, kernel panic and failed hardware cannot guarantee it.
The service restarts on failure after 5 seconds, limited to five starts per 300
seconds; installation hooks enable it for multi-user boot. No second fan writer
or GUI process is needed. Real reboot, logind suspend/resume, EC readback and
shutdown behavior still require G3-572 V1.22 device validation.

The old `background_service.py`, root GUI wrapper, GUI-exec Polkit action, and
`predator-sense.service` are removed. Upgrade hooks stop/disable that legacy unit,
preserve saved state, then enable/start/restart `predator-sensed.service`. The
new unit also declares a conflict with the old unit. Existing running legacy GUI
processes should be closed during upgrade; their removed files do not terminate
already-running processes automatically.

## Diagnostics, packaging, and unresolved audit findings

Diagnostics request cooling data through D-Bus and restrict their location selector
to the seven known read locations; it is no longer a raw EC reader. Temperature
queries use the coretemp/NVML cache described above. Missing GPU support returns
unavailable without running a telemetry subprocess. The diagnostics script still
has its separate, explicitly requested NVIDIA command sampling. Phase 5 now displays the shared telemetry snapshot and history.

Remaining diagnostics audit work includes explicit acer_wmi/service inventory
and narrower privacy filtering. The existing report includes cwd, UID/EUID,
hostname through uname, and NVIDIA process details. Existing shell pipelines and
zero-duration GPU sampling behavior are unchanged.

PKGBUILD explicitly includes all daemon/client modules, the unprivileged launcher,
daemon launcher, systemd unit, D-Bus activation/routing policy, and new Polkit
action. Dependencies include python-dbus-next, dbus, and qt6-wayland; the NVML
binding is optional. Phase 3 adds all three telemetry modules, bumps pkgrel to 3,
and regenerates `.SRCINFO` with makepkg. The recipe still uses local source
and `url="local"`; publishing a reproducible source recipe is separate work.
Phase 5 stops installing unconfirmed font assets; system fonts are used. Diagnostics/license
files are not installed, and the PyInstaller recipe remains outside CI.

Installation/upgrade has persistent system and potential hardware effects. Do not
execute hooks or install the package as a test. Package verification must use
syntax checks or a non-installing normal-user build.

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

Local Phase 3 results: **98 tests passed on Python 3.12.14**. The three-minute
hardware-free Qt/private-bus soak delivered **181 updates**, with a **0.998-second
mean interval** and a **33 ms maximum Qt heartbeat gap**. History capped at **120**
samples; the fake daemon stayed at **four threads** (main plus three workers).
Daemon RSS went from 25,636 to 26,912 KiB as the bounded history filled. GPU status
included 26 stale and 14 failed updates while CPU and fan data remained available.
Ruff, Python compilation, smoke checks, shell syntax, `.SRCINFO` consistency, and
diff whitespace checks passed. A non-installing `makepkg --nodeps -f` build of
0.2.0-3 succeeded in `/tmp`; archive inspection confirmed the telemetry modules,
updated bus policy, and optional NVML dependency. No installation hooks were run.
This is simulated-source validation, not measured laptop sensor performance.

Phase 3 adds hardware-free tests for variable hwmon indices/package preference,
fallback and permission errors, absent NVML/driver, GPU loss/reinitialization,
partial EC failures, immutable wire snapshots, stale/no-fake-zero semantics,
bounded five-minute simulated history, drift-free deadlines, nonoverlapping
workers, one-call Qt polling, and automatic daemon restart recovery. The optional
`tests/telemetry_soak.py` runs the real Qt client/window and private D-Bus fixture
for three minutes with fake GPU stalls/failures, measuring update intervals,
Qt heartbeat gaps, bounded history, worker count, and daemon memory.

Real-device Phase 3 validation still needs coretemp package-label comparison,
GTX 1050 Ti/NVML compatibility under the installed driver and service sandbox,
GPU sleep/disappearance/resume recovery, RPM correlation, and a multi-minute
native Plasma Wayland session while using fan controls. Mock/private-bus results
do not establish those hardware properties.


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


## Phase 4: RPM evidence and read-only validation

Rechecked 2026-09-18 against the model-specific upstream sources:

- [G3-572 configuration](https://github.com/nbfc-linux/nbfc-linux/blob/main/share/nbfc/configs/Acer%20Predator%20G3-572.json):
  word reads at CPU 19 (`0x13`) and GPU 21 (`0x15`), independent read range
  0–6122, with control locations 55/58 (`0x37`/`0x3A`).
- [ec_sys_linux.c](https://github.com/nbfc-linux/nbfc-linux/blob/main/src/ec_sys_linux.c):
  `EC_SysLinux_ReadWord` reads two bytes at the register offset and applies
  `le16toh`. The existing backend's little-endian decoding matches this behavior.
- [fan.c](https://github.com/nbfc-linux/nbfc-linux/blob/main/src/fan.c):
  `Fan_ECReadValue` selects word reads; `Fan_UpdateCurrentSpeed` converts the
  configured range to a percentage. Consequently NBFC establishes a fan-speed
  read range, not independently calibrated RPM units. No source investigated
  proves these words are only a different scalar, either. The existing `*_fan_rpm`
  API remains a working hypothesis and source metadata explicitly says candidate RPM.

Only read semantics are adopted. NBFC's write/reset configuration, retry/clamping
behavior, and other Acer-model registers are not imported. The backend performs
one two-byte read per channel, accepts 0–6122 inclusive, and returns `None` for
higher words. Short/disconnected reads retain structured errors. RPM locations
and their adjacent high bytes remain outside the write allow-list. Invalid raw
words are included in rate-limited warnings, without clamping or fabricated RPM.
The shared hardware maximum also validates the telemetry wire model.

Production integration uses the existing 1 Hz daemon EC worker and immutable
snapshot. EC operations remain serialized with controls. Raw accepted words pass
through unchanged: no conversion, rolling average, or smoothing. Failed or stale
fields are unavailable, while valid zero stays zero. Phase 5 now shows candidate RPM in both telemetry cards and graphs, preserving
the source warning; the validation utility also exposes the same readings.

[`scripts/validate_fan_telemetry.py`](scripts/validate_fan_telemetry.py) reads cached
snapshots from an already-running service at 1 Hz by default (configurable 1–60
seconds, 1–3600 samples). It resolves and pins the unique bus owner, marks calls
NO_AUTOSTART, validates exact G3-572 identity, and reports BIOS. This prevents the
tool from activating the daemon and indirectly triggering its saved-state restore.
It does not load modules, prepare EC, open hardware, scan addresses, invoke
subprocesses, or send mutations. Service disappearance stops the run; rerun it
explicitly after an independently managed restart. The daemon and GUI may perform
authorized actions independently while observation is running.

CSV includes the sample timestamp/epoch/sequence, annotation, candidate CPU/GPU
RPM and error/status, modes, manual percentages, CoolBoost, and temperatures.
`--label` only annotates the output. The utility applies the same freshness rule
as the GUI and prints `Unavailable` for absent/stale values. Duplicate sequences
are visible as repeated cached samples. No file is written unless the maintainer
explicitly redirects stdout.

Physical validation remains outstanding: on G3-572 BIOS V1.22, compare settled
Auto, Auto + CoolBoost, Manual ~30%/50%/~70%, and Turbo observations using the GUI
separately. Record workload and temperature alongside a trustworthy independent
RPM reference (historical PredatorSense observations or external measurement).
Confirm CPU/GPU channel assignment, absolute units, byte ordering against actual
words, plausible maxima, actual stopped-fan zero if observable, and recovery after
suspend. Never run competing EC writers to gather simultaneous reference data.
Software fixtures establish decoding/isolation, not physical calibration.

Phase 4 verification: **108 tests passed on Python 3.12.14**, including explicit
CPU/GPU byte fixtures, zero/normal/6122/high-word boundaries, short/disconnected
reads, write exclusion, warning rate limits, snapshot roundtrip, missing/stale
output, and real CLI execution against an isolated private bus. The CLI's absent-
daemon test verifies it exits instead of activating a service. Ruff, compilation,
smoke, CLI help, and diff-whitespace checks passed. Existing Phase 3 changes were
preserved. No live EC/NVML access, service changes, package installation, or commit
was performed for this phase.


## Phase 5: native thermal dashboard

The daemon, service client, wire protocol, and hardware code are unchanged in this
phase. `MainWindow` consumes `telemetry_updated` for instruments/history and
`snapshot_changed` for controls. It makes one asynchronous hardware-identity read
per daemon epoch after initialization, not one per tick; Retry can refresh identity after an error.
No widget reads hardware, spawns commands, or owns a telemetry polling timer.

`frontend.py` is a hand-maintained layout rather than generated absolute geometry.
The preferred 1020 × 800 window is resizable with a 760 × 480 minimum; startup
fits it to the available screen in logical coordinates, including 200% scaling. CPU/GPU panels
sit side by side when space permits, stack when font/layout minimums require it,
and remain reachable through a scroll area. Each card presents temperature first,
then candidate RPM, then mode, with separate temperature and RPM histories for the
last 60 seconds. Fixed graph scales (0–125 °C and 0–6122 candidate RPM) prevent
misleading auto-scaling. Graphs preserve missing samples and time gaps, and apply
no smoothing. Empty charts say Awaiting readings; missing numbers show a dash.
Historical values remain historical, never substituted for a current missing value.

The cooling panel contains global Auto/Turbo, per-fan Auto/Manual/Turbo segments,
horizontal sliders with percentages, and a CoolBoost switch. Manual values retain
the existing 0–10 to 0–100 mapping. Mouse dragging commits once on release; keyboard
changes debounce for 250 ms. Programmatic rendering blocks signals. Manual sliders
require a known Manual state, and CoolBoost edits require known state plus at least
one Auto fan. Its observed ON/OFF state stays visible when edits are unavailable;
no new hardware semantics or firmware mode writes are introduced.

The header has connection status; the footer reports daemon, detected model, EC,
and BIOS. Missing GPU/RPM readings have local explanations/tooltips. Daemon, EC,
unsupported-hardware, and authorization failures use a compact actionable banner.
A retry button refreshes read-only state. Controls keep native Qt radio/checkbox/
slider semantics, accessible names, focus indication, and a logical keyboard order.
No transitions, timer animations, widget-tree rebuilds, plotting frameworks,
webviews, QML, or unrelated features are introduced.

Colors, stylesheet, spacing, and desktop ID are centralized in `ui/theme.py`.
`font_config.py` loads the open-source geometric typeface Turret Road (SIL Open
Font License 1.1) bundled under `assets/fonts/` with graceful fallback to system
UI/monospace fonts. The new SVG is an original geometric thermal-control
mark, not a copied Acer logo.

Qt application name/desktopFileName, installed desktop filename, StartupWMClass,
and hicolor SVG name use `io.github.iashutoshtiwari.PredatorSense`; visible title is
Predator Sense. Source and installed asset resolution is retained. `qt6-svg`
is an explicit Arch runtime dependency for the icon (it is optional in Arch's
[python-pyqt6 package](https://archlinux.org/packages/extra/x86_64/python-pyqt6/)).
PKGBUILD installs both UI modules and SVG, and uses pkgrel 4 with regenerated `.SRCINFO`.

Qt retains session platform selection and native scaling. The desktop identifier
follows [Qt's desktopFileName contract](https://doc.qt.io/qt-6/qguiapplication.html#desktopFileName-prop),
and drawing uses logical coordinates with DPI-aware Qt rasterization as described
in [Qt high-DPI support](https://doc.qt.io/qt-6/highdpi.html). No XCB/Wayland override
or privileged GUI launch is introduced.

`tests/dashboard_fixture.py` contains explicit simulated data; production never
imports it. `tests/render_dashboard.py` exports live-looking, offline, missing-GPU,
authentication, and compact states marked SIMULATED TEST DATA. Representative
screenshots are in `docs/screenshots/`. These are offscreen renders, not evidence
of a live G3-572 or a Plasma compositor session. Native Wayland/X11 integration,
fractional scaling across physical monitors, actual Polkit-agent dialogs, and
physical telemetry validation still require testing on the target device.

Phase 5 validation: **119 tests passed on Python 3.12.14**. New cases cover live
values/graphs, legitimate zero versus missing values, gaps and history bounds,
mouse release/keyboard debouncing, keyboard modes, CoolBoost availability,
connection/authentication banners, identity refresh across epochs and startup,
widget-tree stability, larger fonts, compact layout, and offscreen rendering at
125% and 200%. Additional screenshots were inspected at 100%, 125%, 150%, and 200%.

A three-minute Qt/private-bus soak with the production theme and simulated GPU
stalls/failures delivered **181 updates**, averaging **0.999 seconds**, with a
**22 ms maximum Qt heartbeat gap**. History stayed capped at 120. The fixture
remained bounded at five daemon threads: main, three sensor workers, and one
reusable executor worker for the dashboard's identity request. Its RSS rose from
25,316 to 26,860 KiB while filling history. These measurements use simulated
hardware, not the physical laptop or a compositor performance benchmark.

Ruff, compilation, smoke checks, desktop-file validation, shell syntax, `.SRCINFO`
consistency, and diff-whitespace checks passed. A non-installing `makepkg --nodeps
-f` build succeeded; the archive contains all modules, matching desktop/SVG assets,
and qt6-svg dependency, with no bundled fonts or ICO. No installed services,
package lifecycle hooks, kernel modules, live hardware, or commits were touched.


## Phase 7: Arch/CachyOS packaging and "just works" installation

Phase 7 delivers a standards-compliant Arch Linux package (`PKGBUILD`, `predator-sense.install`,
and associated systemd, D-Bus, Polkit, and desktop files) targeting Arch Linux, CachyOS, and
compatible Arch-derived distributions.

### Packaging structure and Python wheel build

Rather than loose files copied to `/usr/share/`, the application is structured as a standard
Python package (`predator_sense`) built via `pyproject.toml` using `setuptools.build_meta`:
- `predator-sense`: unprivileged GUI console script entry point (`predator_sense.main:main`).
- `predator-sensed`: root hardware service daemon entry point (`predator_sense.daemon_main:main`).
- `predator-sense-check`: non-mutating installation health check (`predator_sense.install_check:main`).

`PKGBUILD` invokes standard packaging tooling (`python -m build --wheel --no-isolation` and
`python -m installer --destdir="$pkgdir" --prefix=/usr dist/*.whl`), ensuring correct file
ownership, permissions, and bytecode compilation.

### ec_sys configuration and debugfs access

- `/etc/modprobe.d/predator-sense.conf` configures `options ec_sys write_support=1`.
- The daemon conditionally loads `ec_sys` via `ensure_ec_access()` only after strictly
  verifying the normalized DMI product name (`Predator G3-572`). Unconditional boot loading via
  `/usr/lib/modules-load.d/` is deliberately avoided to prevent enabling EC write support on
  incompatible hardware if the package is installed across multiple machines.
- The daemon detects `/sys/kernel/debug/ec/ec0/io` and never attempts dangerous debugfs
  remounts. The unprivileged GUI never invokes `modprobe` or requests sudo.

### Systemd, D-Bus, and Polkit integration

- Systemd unit is installed to `/usr/lib/systemd/system/predator-sensed.service` (`Type=dbus`,
  `BusName=io.github.iashutoshtiwari.PredatorSense`, `Conflicts=predator-sense.service`).
- In accordance with Arch Linux packaging guidelines, `.install` scriptlets never enable or
  start systemd services automatically. `post_install()` informs the administrator to execute
  `systemctl enable --now predator-sensed.service`.
- D-Bus policy at `/usr/share/dbus-1/system.d/io.github.iashutoshtiwari.PredatorSense.conf`
  enforces strict destination and method routing.
- Polkit action at `/usr/share/polkit-1/actions/io.github.iashutoshtiwari.predatorsense.policy`
  gates hardware mutations with `auth_admin_keep` for active sessions.

### Desktop identity, Wayland, and font policy

- Standards-compliant `.desktop` file installed to `/usr/share/applications/` with `Exec=predator-sense`,
  `StartupWMClass=io.github.iashutoshtiwari.PredatorSense`, and `Categories=System;Settings;`.
- Scalable vector icon installed to `/usr/share/icons/hicolor/scalable/apps/`.
- No forced XCB platform (`QT_QPA_PLATFORM=xcb` is never exported); native Wayland sessions are preserved.
- The UI bundles the open-source Turret Road font (SIL OFL 1.1) under `assets/fonts/` with system fallback.

### Installation verification and state lifecycle

- `predator-sense-check` executes read-only validation: DMI product identity, systemd service installation
  and active state, D-Bus responsiveness without activating an inactive daemon, EC file and module parameter
  presence, GUI dependencies, and live telemetry sources (CPU, GPU, fan RPMs).
- Atomic cooling state under `/var/lib/predator-sense/state.json` is preserved across upgrades and package
  removal. `post_remove()` documents its retention and manual purge procedure.
