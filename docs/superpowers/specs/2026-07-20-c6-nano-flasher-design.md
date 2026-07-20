# C6-NANO-Flasher — Design Spec

**Date:** 2026-07-20
**Status:** Approved (design), pending implementation plan
**Author:** Austin Kregel (with Claude Code)

## Purpose

A dedicated, single-purpose board for flashing the **ESP32-C6 co-processor that
lives on the Waveshare ESP32-P4-NANO dev board**. Today, reflashing that C6
(via `animated-journey/firmware/flash-c6-coprocessor.sh`) requires a generic
USB-serial adapter with flying leads to the C6's UART pins, a **manual IO9→GND
boot strap**, and a **manual reset** — because the C6's reset (EN) is not wired
for auto-reset. This board makes flashing the C6 a single-cable, hands-free
operation.

This is the **first of two planned companion boards**; a sensor companion HAT
for the P4 is a separate, later design.

## Key hardware findings (from `docs/files/` schematic + pinout)

The design depends on facts verified against `ESP32-P4-NANO-schematic.pdf` and
the `ESP32-P4-NANO-details-inter.jpg` pinout:

1. **The C6's reset (`C6_CHIP_PU` / EN, module pin 8) is NOT on any header.** It
   is driven by the P4's `GPIO54` through R54 (0 Ω) and pulled up by R13 (10 kΩ).
   → An external programmer cannot toggle the C6's reset, so esptool's classic
   DTR/RTS→EN auto-reset is impossible over the exposed pins.
2. **`C6_IO12` (module pin 17) and `C6_IO13` (module pin 18) reach header P2 with
   a clean, resistor-free path.** (Contrast the P4↔C6 SDIO/GPIO lines
   GPIO14–19, which each pass through 51 kΩ isolation resistors R15/R16/R21/…
   and are unusable for USB.)
3. On the ESP32-C6, **`GPIO12` = USB D-, `GPIO13` = USB D+** (fixed-function
   native USB-Serial-JTAG). Therefore `C6_IO12`/`C6_IO13` are the C6's native
   USB pins, exposed cleanly on P2.
4. The C6's USB-Serial-JTAG resets the chip into download mode **through the USB
   peripheral itself — no EN pin required** — and provides the D+ 1.5 kΩ
   pull-up **internally** (no external pull-up needed).
5. Also exposed on P2 for a fallback path: `C6_U0RXD`, `C6_U0TXD`, `C6_IO9`
   (boot strap).

**Conclusion:** native-USB flashing over `C6_IO12`/`C6_IO13` is the correct
approach — it sidesteps the missing EN pin entirely.

## Approach

**Native-USB passthrough.** The host computer is the USB host; the on-board C6
is the USB device. The board carries the USB differential pair from a USB-C
receptacle to the C6's native-USB pins on the P4's P2 header, with ESD
protection in between. The board adds **no active logic** to the flash path.

### Consequences / constraints
- **Data + GND only.** The C6 is powered by the P4's 3V3 rail, so the **P4 must
  be independently powered (its own USB-C or PoE) while flashing the C6.** An
  optional, default-open jumper (JP1) can inject VBUS→header-5V to power the P4
  from the same cable.
- **Documented limitation:** if C6 firmware ever disables USB-Serial-JTAG or
  repurposes IO12/IO13, native-USB recovery is impossible (EN/IO9 are not
  reachable for a full auto-recovery). The 1×4 UART fallback header mitigates
  this for the recoverable cases.
- **P4 must not fight the flash:** while flashing the C6, the P4 firmware must
  not drive the C6 reset (GPIO54). This is a procedural/firmware concern (the
  existing `flash-p4-and-c6.sh` already handles the analogous case by parking
  the P4), not a board concern.

## Signal flow

```
[PC] --USB-A/C cable--> [J2 USB-C] --D+/D---> [D1 ESD] --> IO13/IO12 (C6 native USB) via J1 (2x13 socket on P2)
                                        \--GND---------------------------------> GND
[P4-NANO powered separately] --> supplies C6 3V3
Fallback: C6_U0RXD / C6_U0TXD / C6_IO9 --> J3 (1x4 header) for UART recovery
```

## Bill of materials (net-level)

| Ref | Part | Purpose |
|-----|------|---------|
| **J1** | 2×13 female socket, 2.54 mm | Mates P4 header **P2** (keyed). Connected pins: `C6_IO12`, `C6_IO13`, `GND`, `C6_U0RXD`, `C6_U0TXD`, `C6_IO9`. All other P2 pins = no-connect. |
| **J2** | USB-C receptacle, USB 2.0 (16-pin) | Host connection. |
| **D1** | USBLC6-2SC6 | Low-capacitance ESD clamp on D+/D-. |
| **R1, R2** | 5.1 kΩ | CC1/CC2 pulldowns (Rd) so a C-to-C cable enumerates. |
| **R3** | 1 kΩ | Power LED current limit. |
| **D2** | LED | Power indicator off header **3V3** (lit = P4/C6 powered). |
| **J3** | 1×4 header, 2.54 mm | UART fallback: `C6_U0RXD`, `C6_U0TXD`, `C6_IO9`, `GND`. |
| **JP1** | Solder jumper, normally **open** | Optional VBUS→header-5V to power the P4 from this cable. |

### Wiring
- `USB D+ (J2) → C6_IO13`
- `USB D- (J2) → C6_IO12`
- `GND (J2) ↔ header GND ↔ J3 GND`
- `C6_U0RXD, C6_U0TXD, C6_IO9 → J3`
- D± routed as a short, roughly length-matched pair through D1. Full-speed
  (12 Mbps) tolerances — no impedance-controlled stackup required.

## Mechanical & manufacturing
- **~25 × 18 mm**, 2-layer, 1.6 mm PCB, hanging off the P4's P2 edge — J1 is the
  only connector that must align to the P4.
- **JLCPCB** 2-layer. USB-C receptacle and D1 are the only fine-pitch parts;
  everything else is hand-solderable through-hole/large SMD.
- Silkscreen: J3 pin labels; a JP1 warning ("5V inject — leave open unless P4
  unpowered"); a "P2 / pin-1" orientation mark so J1 cannot be mis-seated.

## Open items to resolve during schematic capture
- **Exact P2 pin numbers** for `C6_IO12`, `C6_IO13`, `GND`, `C6_U0RXD`,
  `C6_U0TXD`, `C6_IO9`. Column assignments are known from the pinout photo;
  precise pin positions/orientation will be locked from the Waveshare P2 header
  numbering during capture.

## Implementation notes
- All KiCAD work goes through **Konnect** (the repo's KiCAD MCP server, wired via
  `bin/konnect` in `.mcp.json`): symbols/footprints → schematic capture → PCB
  layout → review → fab outputs.
- Reuse JLC-stocked / common symbols and footprints where they exist; only
  create custom library parts if a needed part is missing.

## Explicitly out of scope (YAGNI)
- The sensor companion HAT (separate design).
- Any onboard C6 module (the target C6 lives on the P4).
- Powering/flashing the P4 itself (this board targets the C6 only; JP1 5V-inject
  is the sole, optional exception).
- A production multi-board flashing fixture (pogo/ZIF).
