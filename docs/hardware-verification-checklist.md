# Predator Sense Physical-Hardware Verification Checklist

This manual test plan is intended for the maintainer on physical Acer Predator G3-572 hardware
(BIOS V1.22) before tagging or publishing a release candidate.

Never run competing EC software (such as NBFC, Acer-WMI scripts, or raw EC debuggers) during these tests.

---

## Pre-Requisites

1. Target machine is an Acer Predator G3-572.
2. Kernel module `ec_sys` is available with `write_support=1` (configured via `/etc/modprobe.d/predator-sense.conf`).
3. `predator-sensed.service` is installed and enabled via systemd.
4. Active desktop user session (KDE Plasma Wayland or X11) with a functioning Polkit agent.

---

## Step-by-Step Test Procedure

For each step below, verify:
* **GUI State:** Controls and indicators reflect the action accurately.
* **EC State:** Confirmed via `predator-sense-diagnostics` or debugfs read.
* **Fan Acoustic / Thermal Response:** Fans audibly spin up, slow down, or maintain curve.
* **Telemetry:** 1 Hz updates in GUI cards and sparklines without freezes or zero-substitutions.
* **Logs:** Clean output in `journalctl -u predator-sensed.service -b` and `~/.local/state/predator-sense/app.log`.

---

| Step # | Action | Expected GUI State | Expected EC State | Expected Physical Behavior |
| :--- | :--- | :--- | :--- | :--- |
| **1** | **Fresh Boot** | Saved modes/CoolBoost restore when observed modes are recognized; clean state selects Auto/Auto/Off. Unknown modes remain unknown pending explicit control. | Clean recognized state: `0x22=0x54`, `0x21=0x50`. Saved state: requested verified modes. | Observe response to the restored settings. |
| **2** | **CoolBoost On** | Click CoolBoost switch to ON. Prompts Polkit dialog once. Switch turns red/active. | `0x10=0x01`. | Fan RPM increases moderately (~300–500 RPM higher acoustic floor). |
| **3** | **CoolBoost Off** | Click CoolBoost switch to OFF. Polkit auth retained. Switch turns gray/inactive. | `0x10=0x00`. | Fan RPM settles back to normal baseline. |
| **4** | **CPU Manual (50%)** | Select CPU Manual, move slider to 50%, release mouse. | `0x22=0x5C`, `0x37=0x32` (50). | CPU fan speeds up to steady 50% sound level. GPU fan remains in Auto. |
| **5** | **GPU Manual (70%)** | Select GPU Manual, move slider to 70%, release mouse. | `0x21=0x70`, `0x3A=0x46` (70). | GPU fan speeds up to louder 70% level. |
| **6** | **Both Manual (80%)** | Set both sliders to 80%. | `0x22=0x5C, 0x37=0x50`, `0x21=0x70, 0x3A=0x50`. | Both fans operate at high manual velocity. |
| **7** | **Global Turbo** | Click Turbo in Global Controls panel. | `0x22=0x58`, `0x21=0x60`. | Both fans spin up to maximum rated RPM (~5500–6000 RPM). Distinct maximum acoustic profile. |
| **8** | **Return to Global Auto** | Click Auto in Global Controls panel. | `0x22=0x54`, `0x21=0x50`. | Fans decelerate smoothly to thermal curve speeds. |
| **9** | **Close & Reopen UI** | Close the window via Exit or titlebar. Re-launch `predator-sense`. | Exact previous state restored without extra EC writes. | Fans do not stutter, spin down, or jump during window lifecycle. |
| **10** | **Daemon Restart** | `sudo systemctl restart predator-sensed.service`. | GUI detects drop, displays reconnect banner, then restores active controls. | Saved cooling state re-applied by daemon upon probe. |
| **11** | **Suspend** | Close laptop lid or trigger system suspend (`systemctl suspend`). | Saved state flushed to `/var/lib/predator-sense/state.json`. | Laptop enters low-power sleep; fans stop completely. |
| **12** | **Resume** | Open laptop lid and wake system. Log into desktop session. | UI recovers automatically without manual restart; status label reports normal. | Daemon receives logind resume signal, re-probes EC, and restores configured fan modes. |
| **13** | **Logout / Login** | Log out of desktop session and log back in. | Application can be launched cleanly from desktop launcher. | Daemon remained active in background; fans uninterrupted. |
| **14** | **Reboot** | Reboot the system (`systemctl reboot`). | On next desktop login, saved state from `/var/lib/` was restored at boot by the daemon. | Fan state is maintained seamlessly across reboots. |

---

## Log Review Checklist

After completing the tests, run:
```bash
journalctl -u predator-sensed.service -b --no-pager
```
Verify:
- [ ] Daemon started and requested D-Bus name `io.github.iashutoshtiwari.PredatorSense`.
- [ ] Exact DMI match confirmed: `Predator G3-572`.
- [ ] No per-second polling logs at INFO level.
- [ ] Polkit checks logged only upon control mutations.
- [ ] Clean logind sleep/resume events captured without unhandled exceptions.


The 70%/80% and 300–500 RPM statements are retained for maintainer evidence review;
see [the hardware specification](g3-572-hardware.md#maintainer-evidence-review).
Mocks and offscreen rendering cannot establish these physical effects. Lifecycle
logging should be checked for failures; individual sleep/Polkit events are not all
currently logged as separate informational messages.
