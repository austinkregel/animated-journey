#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="${SCRIPT_DIR}/out/c6-coprocessor"
IDF_DIR="${SCRIPT_DIR}/idf-source"
DEFAULT_BUILD_DIR="${IDF_DIR}/builds/c6-coprocessor"
BUILD_DIR="$DEFAULT_BUILD_DIR"
COMPONENTS_DIR="${IDF_DIR}/components/c6-coprocessor"
CACHE_DIR="${IDF_DIR}/cache"
IMAGE_NAME="animated-journey-idf"
DEFAULT_SOURCE_DIR="${SCRIPT_DIR}/scanner-p4/managed_components/espressif__esp_hosted/slave"

usage() {
    cat <<EOF
Usage: $0 --port <port> [options]

Build and flash the ESP-Hosted slave firmware for the onboard ESP32-C6
co-processor used by scanner-p4 over SDIO.

Required:
  --port <port>       C6 serial port, not the P4 host serial port

Options:
  --build-dir <path>  Build tree containing flash_args (for --flash-only)
                      (default: idf-source/builds/c6-coprocessor)
  --source <path>     ESP-Hosted slave project path
                      (default: scanner-p4/managed_components/espressif__esp_hosted/slave)
  --build-only        Build artifacts but do not flash
  --flash-only        Flash existing build artifacts without rebuilding
  --baud <n>          Flash baud rate (default: 460800; try 115200 for flaky USB-serial)
  --erase-first       Run 'esptool erase-flash' before writing (recovers from partial flashes)
  --no-compress       Send raw image bytes (lower CPU on chip = avoids brownouts on weak USB-serial)
  --no-stub           Write via ROM bootloader (no stub) - lowest power, slower
                      (erase still uses stub: C6 ROM does not support full-chip erase)
                      WARNING: ESP32-C6FH4 (embedded flash) cannot be written via
                      ROM either - the spi_set_data_lines call fails because the
                      flash pins are internal. Use --no-stub only on C6 variants
                      with EXTERNAL flash. For C6FH4 brownouts, fix the 3V3 rail
                      (decoupling cap, external 3V3, better USB-serial adapter).
  --no-monitor        Do not capture a boot log after flashing
  --monitor-seconds <n>
                      Serial monitor timeout in seconds (default: 30)
  --monitor-log <path>
                      Boot log output path (default: out/c6-coprocessor/boot-<timestamp>.log)
  --release-prompt    Pause after flashing for the user to remove the IO9->GND
                      strap (BOOT pin) and reset the C6, so the chip can boot
                      its app firmware instead of looping in download mode.

Examples:
  $0 --port /dev/tty.usbmodemC6
  $0 --port /dev/tty.usbmodemC6 --monitor-seconds=20
  $0 --port /dev/tty.usbmodemC6 --flash-only --no-monitor

  # P4 + C6 USB both plugged in: auto-detect ports and flash both (see flash-p4-and-c6.sh)
  ./flash-p4-and-c6.sh

Notes:
  This flashes the P4 board's ESP32-C6 ESP-Hosted co-processor firmware.
  It is different from the standalone scanner-c6 firmware.
EOF
    exit 1
}

monitor_boot_log() {
    [[ "$MONITOR" == true ]] || return 0

    mkdir -p "$OUT_DIR"
    if [[ -z "$MONITOR_LOG" ]]; then
        MONITOR_LOG="${OUT_DIR}/boot-$(date +%Y%m%d-%H%M%S).log"
    fi

    echo ""
    echo "=== Capturing C6 co-processor boot log (${MONITOR_SECONDS}s max) ==="
    echo "  Port: ${PORT}"
    echo "  Log:  ${MONITOR_LOG}"
    echo "  Stops early on panic/assert/reboot markers."
    echo ""

    python3 - "$PORT" "$MONITOR_SECONDS" "$MONITOR_LOG" <<'PY'
import re
import sys
import time

try:
    import serial
except ImportError:
    print("ERROR: pyserial is required for monitoring. Install with: python3 -m pip install pyserial")
    sys.exit(2)

port = sys.argv[1]
timeout_s = float(sys.argv[2])
log_path = sys.argv[3]

stop_re = re.compile(
    r"(assert failed|Guru Meditation|panic'ed|Rebooting\.\.\.|SW_CPU_RESET|TG[01]WDT_SYS_RESET)",
    re.IGNORECASE,
)

deadline = time.monotonic() + timeout_s
stopped_reason = "timeout"

with serial.Serial(port, 115200, timeout=0.2) as ser, open(log_path, "w", encoding="utf-8", errors="replace") as log:
    while time.monotonic() < deadline:
        raw = ser.readline()
        if not raw:
            continue

        text = raw.decode("utf-8", errors="replace")
        print(text, end="")
        log.write(text)
        log.flush()

        match = stop_re.search(text)
        if match:
            stopped_reason = f"matched '{match.group(1)}'"
            break

print(f"\n=== Serial monitor stopped: {stopped_reason} ===")
PY
}

build_coprocessor() {
    if [[ ! -f "${SOURCE_DIR}/CMakeLists.txt" ]]; then
        echo "Error: ESP-Hosted slave project not found at:"
        echo "  ${SOURCE_DIR}"
        echo ""
        echo "Build scanner-p4 once first so ESP-IDF component manager checks out esp_hosted:"
        echo "  ./build.sh scanner-p4"
        exit 1
    fi

    mkdir -p "$OUT_DIR" "$BUILD_DIR" "$COMPONENTS_DIR" "$CACHE_DIR"

    echo "=== Preparing builder image ==="
    docker build -f "${SCRIPT_DIR}/Dockerfile.build" -t "$IMAGE_NAME" "$SCRIPT_DIR"

    echo ""
    echo "=== Building ESP32-C6 ESP-Hosted co-processor firmware ==="
    docker run --rm \
      --network=host \
      -e "IDF_TARGET=esp32c6" \
      -v "${SOURCE_DIR}:/src:ro" \
      -v "${BUILD_DIR}:/project/c6-coprocessor/build" \
      -v "${COMPONENTS_DIR}:/project/c6-coprocessor/managed_components" \
      -v "${CACHE_DIR}:/root/.cache" \
      "$IMAGE_NAME" \
      ". /opt/esp/idf/export.sh && \
       mkdir -p /project/c6-coprocessor && \
       cp -a /src/. /project/c6-coprocessor/ && \
       cd /project/c6-coprocessor && \
       idf.py set-target esp32c6 && \
       idf.py build"

    mkdir -p "$OUT_DIR"
    cp "${BUILD_DIR}/network_adapter.bin" "$OUT_DIR/" 2>/dev/null || true
    cp "${BUILD_DIR}/bootloader/bootloader.bin" "$OUT_DIR/" 2>/dev/null || true
    cp "${BUILD_DIR}/partition_table/partition-table.bin" "$OUT_DIR/" 2>/dev/null || true
    cp "${BUILD_DIR}/ota_data_initial.bin" "$OUT_DIR/" 2>/dev/null || true
    cp "${BUILD_DIR}/flasher_args.json" "$OUT_DIR/" 2>/dev/null || true
    cp "${BUILD_DIR}/flash_args" "$OUT_DIR/" 2>/dev/null || true

    echo "  -> C6 co-processor artifacts copied to out/c6-coprocessor/"
}

flash_coprocessor() {
    if [[ ! -f "${BUILD_DIR}/flash_args" ]]; then
        echo "Error: ${BUILD_DIR}/flash_args not found."
        echo "Run without --flash-only to build the C6 co-processor firmware first."
        exit 1
    fi

    if [[ "$ERASE_FIRST" == true ]]; then
        echo ""
        echo "=== Erasing C6 flash (--erase-first) ==="
        python3 -m esptool --chip esp32c6 --port "$PORT" --baud "$BAUD" erase-flash
    fi

    local pre_flags=(--chip esp32c6 --port "$PORT" --baud "$BAUD")
    [[ "$USE_STUB" == false ]] && pre_flags+=(--no-stub)

    local write_flags=()
    [[ "$COMPRESS" == false ]] && write_flags+=(--no-compress)

    echo ""
    echo "=== Flashing ESP32-C6 co-processor via ${PORT} ==="
    (
        cd "$BUILD_DIR"
        python3 -m esptool \
            "${pre_flags[@]}" \
            --before default-reset \
            --after hard-reset \
            write-flash \
            ${write_flags[@]+"${write_flags[@]}"} \
            @flash_args
    )
}

PORT=""
SOURCE_DIR="$DEFAULT_SOURCE_DIR"
BUILD_ONLY=false
FLASH_ONLY=false
MONITOR=true
MONITOR_SECONDS=30
MONITOR_LOG=""
BAUD=460800
ERASE_FIRST=false
COMPRESS=true
USE_STUB=true
RELEASE_PROMPT=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        "") shift ;; # tolerate empty args from trailing-space line continuations
        --port) PORT="$2"; shift 2 ;;
        --port=*) PORT="${1#*=}"; shift ;;
        --build-dir) BUILD_DIR="$2"; shift 2 ;;
        --build-dir=*) BUILD_DIR="${1#*=}"; shift ;;
        --source) SOURCE_DIR="$2"; shift 2 ;;
        --source=*) SOURCE_DIR="${1#*=}"; shift ;;
        --build-only) BUILD_ONLY=true; shift ;;
        --flash-only) FLASH_ONLY=true; shift ;;
        --baud) BAUD="$2"; shift 2 ;;
        --baud=*) BAUD="${1#*=}"; shift ;;
        --erase-first) ERASE_FIRST=true; shift ;;
        --no-compress) COMPRESS=false; shift ;;
        --no-stub) USE_STUB=false; shift ;;
        --release-prompt) RELEASE_PROMPT=true; shift ;;
        --no-monitor) MONITOR=false; shift ;;
        --monitor-seconds) MONITOR_SECONDS="$2"; shift 2 ;;
        --monitor-seconds=*) MONITOR_SECONDS="${1#*=}"; shift ;;
        --monitor-log) MONITOR_LOG="$2"; shift 2 ;;
        --monitor-log=*) MONITOR_LOG="${1#*=}"; shift ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

if [[ "$BUILD_ONLY" == true && "$FLASH_ONLY" == true ]]; then
    echo "Error: --build-only and --flash-only cannot be used together."
    exit 1
fi

if [[ "$BUILD_ONLY" != true && -z "$PORT" ]]; then
    echo "Error: --port is required unless --build-only is used."
    usage
fi

if [[ "$FLASH_ONLY" != true ]]; then
    build_coprocessor
fi

if [[ "$BUILD_ONLY" == true ]]; then
    echo ""
    echo "=== Build complete ==="
    exit 0
fi

flash_coprocessor

echo ""
echo "=== C6 co-processor flashed ==="

if [[ "$RELEASE_PROMPT" == true ]]; then
    echo ""
    echo "=== ACTION REQUIRED ==="
    echo "  The C6 is still strapped in download mode (IO9 tied to GND)."
    echo "  To boot the slave firmware:"
    echo "    1. Remove the IO9 -> GND bridge."
    echo "    2. Tap EN to GND (or power-cycle the C6) to reset it."
    echo "  Then press Enter to continue."
    # Read from controlling terminal (works even when stdin is redirected by a parent script).
    if [[ -t 0 ]]; then
        read -r _
    elif [[ -r /dev/tty ]]; then
        read -r _ </dev/tty
    else
        echo "  (no tty available; sleeping 10s instead)"
        sleep 10
    fi
fi

monitor_boot_log
