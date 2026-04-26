#!/usr/bin/env bash
# Discover USB serial ports (ESP32-P4 vs ESP32-C6 via esptool chip-id), then flash
# ESP-Hosted C6 coprocessor and scanner-p4 host. Requires both USB cables connected.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IDF_DIR="${SCRIPT_DIR}/idf-source"
DISCOVER="${SCRIPT_DIR}/tools/discover_p4_c6_usb.py"

BAUD=460800
ORDER="c6_first"
FLASH_SH_ARGS=()
P4_PORT_OVERRIDE=""
C6_PORT_OVERRIDE=""
SHARED_ERASE_FIRST=false
SHARED_NO_COMPRESS=false
SHARED_NO_STUB=false
C6_RELEASE_PROMPT=false
# Wipe the P4 before flashing the C6 so the (possibly old) P4 firmware
# stops driving the C6 reset GPIO / SDIO bus. Leaves the P4 in the ROM stub
# (--after no-reset) so it stays silent on GPIOs until the real flash runs.
# Default: enabled for c6_first, disabled for p4_first; toggle with the flags below.
PREP_ERASE_P4="auto"

usage() {
    cat <<EOF
Usage: $0 [options] [-- <flash.sh scanner-p4 args>]

With both the P4 and the C6 (PROG) USB ports connected, probes each serial device
using esptool chip-id, then flashes the C6 ESP-Hosted slave and the P4 application
from ./build.sh scanner-p4 output.

Options:
  --c6-first       Flash C6 then P4 (default; usual bring-up order)
  --p4-first       Flash P4 then C6 (P4 step uses --no-monitor; see on-screen hint for console)
  --p4-port <port> Skip P4 auto-discovery; use this port (e.g. /dev/tty.usbmodem...)
  --c6-port <port> Skip C6 auto-discovery; use this port (e.g. /dev/tty.usbserial-21140)
  --baud <n>       esptool baud (default: 460800)
  --erase-first    Apply esptool erase-flash to BOTH C6 and P4 before writing
  --no-compress    Apply esptool --no-compress to BOTH C6 and P4 (recovers brownouts)
  --no-stub        Write via ROM bootloader on BOTH C6 and P4 (lowest power, slower)
  --c6-release-prompt
                   After C6 flash, pause until the user removes the IO9->GND
                   strap and resets the C6, then continues with the P4 flash.
                   Use this when the C6 has BOOT permanently bridged to GND.
  --prep-p4-erase  Before flashing the C6, chip-erase the P4 and park it in
                   the ROM stub (--after no-reset). Prevents the running P4
                   firmware from resetting the C6 mid-flash. Default for
                   --c6-first; off for --p4-first.
  --no-prep-p4-erase
                   Skip the pre-erase even in --c6-first mode (use when the
                   P4 already has no firmware that talks to the C6).
  -h, --help

  Everything after '--' is passed to flash.sh for the P4 only (e.g. provisioning).
  Use the wrapper-level --erase-first / --no-compress to apply to BOTH chips
  (the C6 cannot receive args via '--').

Prerequisites:
  ./build.sh scanner-p4

C6 image is taken from, in order:
  idf-source/builds/scanner-p4/slave-c6/   (from integrated host build)
  idf-source/builds/c6-coprocessor/       (from flash-c6-coprocessor.sh)

Examples:
  $0
  $0 -- --app-only --no-provision
  $0 -- --node-id node-01 --mqtt-host 192.168.1.50
EOF
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        "")
            # Tolerate empty args from trailing spaces after line-continuation
            # backslashes in copy-pasted commands.
            shift
            ;;
        --)
            shift
            FLASH_SH_ARGS+=("$@")
            break
            ;;
        --p4-first) ORDER="p4_first"; shift ;;
        --c6-first) ORDER="c6_first"; shift ;;
        --p4-port) P4_PORT_OVERRIDE="$2"; shift 2 ;;
        --p4-port=*) P4_PORT_OVERRIDE="${1#*=}"; shift ;;
        --c6-port) C6_PORT_OVERRIDE="$2"; shift 2 ;;
        --c6-port=*) C6_PORT_OVERRIDE="${1#*=}"; shift ;;
        --baud) BAUD="$2"; shift 2 ;;
        --baud=*) BAUD="${1#*=}"; shift ;;
        --erase-first) SHARED_ERASE_FIRST=true; shift ;;
        --no-compress) SHARED_NO_COMPRESS=true; shift ;;
        --no-stub) SHARED_NO_STUB=true; shift ;;
        --c6-release-prompt) C6_RELEASE_PROMPT=true; shift ;;
        --prep-p4-erase) PREP_ERASE_P4=true; shift ;;
        --no-prep-p4-erase) PREP_ERASE_P4=false; shift ;;
        -h|--help) usage ;;
        *)
            echo "Unknown option: $1"
            echo "Use -- before arguments meant for flash.sh (scanner-p4)."
            usage
            ;;
    esac
done

if [[ -n "$P4_PORT_OVERRIDE" && -n "$C6_PORT_OVERRIDE" ]]; then
    P4_PORT="$P4_PORT_OVERRIDE"
    C6_PORT="$C6_PORT_OVERRIDE"
else
    if [[ ! -f "$DISCOVER" ]]; then
        echo "Missing ${DISCOVER}"
        exit 1
    fi
    # Run discovery for whichever ports weren't overridden.
    eval "$(python3 "$DISCOVER" || true)"
    [[ -n "$P4_PORT_OVERRIDE" ]] && P4_PORT="$P4_PORT_OVERRIDE"
    [[ -n "$C6_PORT_OVERRIDE" ]] && C6_PORT="$C6_PORT_OVERRIDE"
    if [[ -z "${P4_PORT:-}" || -z "${C6_PORT:-}" ]]; then
        echo "Could not determine both ports."
        echo "  P4=${P4_PORT:-<unset>}  C6=${C6_PORT:-<unset>}"
        echo "Pass --p4-port and/or --c6-port to skip auto-discovery."
        echo "Note: a C6 already running app firmware may not respond to chip-id"
        echo "      unless DTR/RTS are wired to EN/IO9 on the USB-serial adapter."
        exit 1
    fi
fi

echo "=== Ports: P4=${P4_PORT}  C6=${C6_PORT} ==="

C6_BUILD=""
if [[ -f "${IDF_DIR}/builds/scanner-p4/slave-c6/flash_args" ]]; then
    C6_BUILD="${IDF_DIR}/builds/scanner-p4/slave-c6"
elif [[ -f "${IDF_DIR}/builds/c6-coprocessor/flash_args" ]]; then
    C6_BUILD="${IDF_DIR}/builds/c6-coprocessor"
else
    echo "Error: No C6 flash_args found."
    echo "  Tried: ${IDF_DIR}/builds/scanner-p4/slave-c6/"
    echo "         ${IDF_DIR}/builds/c6-coprocessor/"
    echo "Run: ./build.sh scanner-p4   (or ./flash-c6-coprocessor.sh --build-only)"
    exit 1
fi

APP_BIN="${SCRIPT_DIR}/out/scanner-p4/scanner-p4.bin"
if [[ ! -f "$APP_BIN" ]]; then
    echo "Error: ${APP_BIN} not found. Run: ./build.sh scanner-p4"
    exit 1
fi

SHARED_FLAGS=()
[[ "$SHARED_ERASE_FIRST" == true ]] && SHARED_FLAGS+=(--erase-first)
[[ "$SHARED_NO_COMPRESS" == true ]] && SHARED_FLAGS+=(--no-compress)
[[ "$SHARED_NO_STUB" == true ]] && SHARED_FLAGS+=(--no-stub)

if [[ "$PREP_ERASE_P4" == "auto" ]]; then
    if [[ "$ORDER" == "c6_first" ]]; then
        PREP_ERASE_P4=true
    else
        PREP_ERASE_P4=false
    fi
fi

prep_erase_p4_here() {
    echo ""
    echo "=== [prep] Chip-erasing P4 (${P4_PORT}) so it can't drive C6 reset/SDIO ==="
    echo "    Leaving P4 parked in ROM stub (--after no-reset)."
    python3 -m esptool \
        --chip esp32p4 \
        --port "$P4_PORT" \
        --baud "$BAUD" \
        --before default-reset \
        --after no-reset \
        erase-flash
}

flash_c6_here() {
    local extra=()
    [[ "$C6_RELEASE_PROMPT" == true ]] && extra+=(--release-prompt)
    "$SCRIPT_DIR/flash-c6-coprocessor.sh" \
        --port "$C6_PORT" \
        --build-dir "$C6_BUILD" \
        --flash-only \
        --baud "$BAUD" \
        --no-monitor \
        ${SHARED_FLAGS[@]+"${SHARED_FLAGS[@]}"} \
        ${extra[@]+"${extra[@]}"}
}

flash_p4_here() {
    local extra=("$@")
    "$SCRIPT_DIR/flash.sh" scanner-p4 \
        --port "$P4_PORT" \
        --baud "$BAUD" \
        ${SHARED_FLAGS[@]+"${SHARED_FLAGS[@]}"} \
        ${extra[@]+"${extra[@]}"}
}

if [[ "$ORDER" == "c6_first" ]]; then
    if [[ "$PREP_ERASE_P4" == true ]]; then
        prep_erase_p4_here
    fi
    echo ""
    echo "=== [1/2] Flashing C6 co-processor (${C6_BUILD}) ==="
    flash_c6_here
    echo ""
    echo "=== [2/2] Flashing P4 host ==="
    flash_p4_here ${FLASH_SH_ARGS[@]+"${FLASH_SH_ARGS[@]}"}
else
    echo ""
    echo "=== [1/2] Flashing P4 host (--no-monitor; C6 not updated yet) ==="
    flash_p4_here --no-monitor ${FLASH_SH_ARGS[@]+"${FLASH_SH_ARGS[@]}"}
    echo ""
    echo "=== [2/2] Flashing C6 co-processor (${C6_BUILD}) ==="
    flash_c6_here
    echo ""
    echo "P4 console: ${P4_PORT} (115200). Example: screen ${P4_PORT} 115200"
fi

echo ""
echo "=== P4 + C6 flash complete ==="
