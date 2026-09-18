# Acer Predator Helios 300 (G3-572) Hardware & EC Register Specification

This document provides the authoritative hardware mapping for the Acer Predator Helios 300
(2017), model `G3-572` (tested with motherboard `CFL` and BIOS `V1.22`).

It strictly distinguishes between physical hardware observations on this machine,
independent third-party corroboration, and inferred/unvalidated properties.

---

## Hardware Identity Gate

* **DMI Product Name:** `/sys/class/dmi/id/product_name` must normalize exactly to `Predator G3-572`.
* **BIOS Version:** `/sys/class/dmi/id/bios_version` tested version is `V1.22`. Other versions are reported with a non-blocking warning.
* **EC Device Interface:** `/sys/kernel/debug/ec/ec0/io` via kernel module `ec_sys` with parameter `write_support=1`.

---

## EC Register Reference Map

| Register Offset | Size / Type | Read / Write | Function | Corroboration Level |
| :--- | :--- | :--- | :--- | :--- |
| **`0x10`** | 1 byte (uint8) | Read / Write | CoolBoost switch (`0x00` = Off, `0x01` = On) | **Experimentally observed on physical machine** |
| **`0x21`** | 1 byte (uint8) | Read / Write | GPU Fan Mode (`0x00` firmware auto, `0x50` auto, `0x60` turbo, `0x70` manual) | **Experimentally observed on physical machine** |
| **`0x22`** | 1 byte (uint8) | Read / Write | CPU Fan Mode (`0x00` firmware auto, `0x54` auto, `0x58` turbo, `0x5C` manual) | **Experimentally observed on physical machine** |
| **`0x37`** | 1 byte (uint8) | Read / Write | CPU Manual Fan Speed (`0–100` percentage) | **Experimentally observed on physical machine** |
| **`0x3A`** | 1 byte (uint8) | Read / Write | GPU Manual Fan Speed (`0–100` percentage) | **Experimentally observed on physical machine** |
| **`0x13`** | 2 bytes (uint16 LE) | Read-only | CPU Candidate Fan RPM word (`0–6122`) | **Corroborated by NBFC G3-572 profile** |
| **`0x15`** | 2 bytes (uint16 LE) | Read-only | GPU Candidate Fan RPM word (`0–6122`) | **Corroborated by NBFC G3-572 profile** |

---

## Detailed Classification of Evidence

### 1. Experimentally Observed on this Physical Machine
The following operations were directly tested and verified on physical Acer Predator G3-572 hardware running BIOS V1.22:
- **CoolBoost (`0x10`):** Writing `0x01` activates CoolBoost; writing `0x00` deactivates it. Observable through fan acoustics and EC readback. CoolBoost is only valid when at least one fan is in Auto mode.
- **Fan Modes (`0x22` CPU, `0x21` GPU):**
  - CPU: `0x54` sets Auto mode; `0x58` sets Turbo (max fan); `0x5C` sets Manual mode.
  - GPU: `0x50` sets Auto mode; `0x60` sets Turbo (max fan); `0x70` sets Manual mode.
  - `0x00` is the firmware default state at cold boot before any operating system fan management runs.
- **Manual Percentages (`0x37` CPU, `0x3A` GPU):** Integer percentages in the range `0–100`. Tested extensively at 50% (`0x32`), 70% (`0x46`), and 80% (`0x50`). Writes are accepted and verified via immediate readback.

### 2. Independently Corroborated by G3-572 NBFC (NoteBook FanControl)
- **Fan Tachometer Registers (`0x13`, `0x15`):**
  - Upstream NBFC Linux configuration (`share/nbfc/configs/Acer Predator G3-572.json`) defines 16-bit word reads at register offsets 19 (`0x13`) and 21 (`0x15`).
  - Decoding uses little-endian byte ordering (`le16toh`), reading two consecutive bytes (`0x13:0x14` for CPU, `0x15:0x16` for GPU).
  - Valid read range is `0–6122`. Values above 6122 are considered tachometer overflow or bus noise and rejected.

### 3. Inferred / Outstanding Physical Validation
- **RPM Unit Accuracy:** While NBFC and register telemetry report candidate fan speeds up to 6122, it remains unproven whether these raw word values translate directly 1:1 to physical rotor RPM or require a scaling coefficient. They are designated as "Candidate RPM".
- **Extreme Manual Boundaries:** Values below 30% or above 90% have not been validated across varied thermal loads.
- **Immediate Write Readback Latency:** EC controllers update registers asynchronously; atomic transactions must lock access during multi-register mutations.
- **Suspend/Resume Reset:** Hardware behavior across S3 sleep, modern standby (s2idle), and ACPI power transitions. The daemon re-applies desired state upon resume from logind.


## Maintainer evidence review

The existing statements above about extensive 70% and 80% manual testing are
preserved pending maintainer confirmation of their measurement records. The
README/checklist's 300–500 RPM CoolBoost difference likewise needs maintainer
review. The supplied reference contract explicitly records 50% (`0x32`). Software
fixtures at other percentages verify encoding and control flow, not physical fan
response. These review notes neither withdraw nor independently validate the
existing physical claims.

Unknown CPU/GPU mode bytes block automatic restoration and fallback writes until
an explicit authorized fan-mode action establishes known state. This is a
software safety rule; it does not expand the verified hardware map.
