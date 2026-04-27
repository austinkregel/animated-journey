#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="${SCRIPT_DIR}/out"
LOG_DIR="${SCRIPT_DIR}/logs"

usage() {
    cat <<EOF
Usage: $0 <target> --port <port> [options]

Flash a scanner firmware to an ESP32 device.

Targets:
  scanner-s3    ESP32-S3 nodes
  scanner-c6    ESP32-C6 nodes
  scanner-p4    ESP32-P4-NANO nodes (your primary boards)

Required:
  --port <port>     Serial port (e.g. /dev/ttyACM0)

Options:
  --app-only        Flash only the app partition (skip bootloader/partition table)
  --erase-first     Run 'esptool erase-flash' before writing (recovers from partial flashes)
  --baud <n>        esptool serial baud rate (default: 460800; try 115200 for flaky USB-serial)
  --no-stub         Disable stub flasher; write via ROM (slower, more reliable on weak USB-serial)
  --no-compress     Send raw image bytes instead of compressed (lower CPU on chip = less brownout)
  --no-provision    Skip NVS provisioning prompt
  --no-monitor      Do not capture a boot log after flashing/provisioning
  --monitor-seconds <n>
                    Serial monitor timeout in seconds (default: 45)
  --monitor-log <path>
                    Boot log output path (default: logs/<target>-<timestamp>.log)
  --node-id <id>    Node identifier for NVS provisioning
  --mqtt-host <ip>  MQTT broker address for NVS provisioning
  --mqtt-port <n>   MQTT broker port (default: 1883)
  --mqtt-user <u>   MQTT username
  --mqtt-pass <p>   MQTT password
  --wifi-ssid <s>   WiFi SSID (S3/C6 only, not needed for P4 with PoE)
  --wifi-pass <p>   WiFi password

Examples:
  # Full flash of P4 node (first time):
  $0 scanner-p4 --port /dev/ttyACM0 \\
      --node-id living-room-01 --mqtt-host 192.168.1.100

  # App-only update (bootloader/partition table already flashed):
  $0 scanner-p4 --port /dev/ttyACM0 --app-only --no-provision

  # S3 node with WiFi credentials:
  $0 scanner-s3 --port /dev/ttyUSB0 \\
      --node-id office-01 --mqtt-host 192.168.1.100 \\
      --wifi-ssid MyNetwork --wifi-pass MyPassword
EOF
    exit 1
}

# Chip type from target name
chip_for_target() {
    case "$1" in
        scanner-s3) echo "esp32s3" ;;
        scanner-c6) echo "esp32c6" ;;
        scanner-p4) echo "esp32p4" ;;
        *) echo "auto" ;;
    esac
}

# Bootloader offset varies by chip
bootloader_offset() {
    case "$1" in
        esp32)   echo "0x1000" ;;
        esp32s3) echo "0x0" ;;
        esp32c6) echo "0x0" ;;
        esp32p4) echo "0x2000" ;;
        *)       echo "0x0" ;;
    esac
}

# Flash size varies by board target
flash_size_for_target() {
    case "$1" in
        scanner-p4) echo "16MB" ;;
        *)          echo "4MB" ;;
    esac
}

# OTA data partition offset varies by partition table
ota_data_offset() {
    case "$1" in
        scanner-p4) echo "0x810000" ;;
        *)          echo "0x3d0000" ;;
    esac
}

monitor_boot_log() {
    [[ "$MONITOR" == true ]] || return 0

    mkdir -p "$LOG_DIR"
    if [[ -z "$MONITOR_LOG" ]]; then
        MONITOR_LOG="${LOG_DIR}/${TARGET}-$(date +%Y%m%d-%H%M%S).log"
    fi

    echo ""
    echo "=== Capturing boot log (${MONITOR_SECONDS}s max) ==="
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

[[ $# -lt 1 ]] && usage

TARGET="$1"; shift
CHIP=$(chip_for_target "$TARGET")
FLASH_SIZE=$(flash_size_for_target "$TARGET")
PORT=""
APP_ONLY=false
ERASE_FIRST=false
BAUD=460800
USE_STUB=true
COMPRESS=true
NO_PROVISION=false
MONITOR=true
MONITOR_SECONDS=45
MONITOR_LOG=""
NODE_ID=""
MQTT_HOST=""
MQTT_PORT=1883
MQTT_USER=""
MQTT_PASS=""
WIFI_SSID=""
WIFI_PASS=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        "") shift ;; # tolerate empty args from trailing-space line continuations
        --port)         PORT="$2"; shift 2 ;;
        --port=*)       PORT="${1#*=}"; shift ;;
        --app-only)     APP_ONLY=true; shift ;;
        --erase-first)  ERASE_FIRST=true; shift ;;
        --baud)         BAUD="$2"; shift 2 ;;
        --baud=*)       BAUD="${1#*=}"; shift ;;
        --no-stub)      USE_STUB=false; shift ;;
        --no-compress)  COMPRESS=false; shift ;;
        --no-provision) NO_PROVISION=true; shift ;;
        --no-monitor)   MONITOR=false; shift ;;
        --monitor-seconds) MONITOR_SECONDS="$2"; shift 2 ;;
        --monitor-seconds=*) MONITOR_SECONDS="${1#*=}"; shift ;;
        --monitor-log)  MONITOR_LOG="$2"; shift 2 ;;
        --monitor-log=*) MONITOR_LOG="${1#*=}"; shift ;;
        --node-id)      NODE_ID="$2"; shift 2 ;;
        --node-id=*)    NODE_ID="${1#*=}"; shift ;;
        --mqtt-host)    MQTT_HOST="$2"; shift 2 ;;
        --mqtt-host=*)  MQTT_HOST="${1#*=}"; shift ;;
        --mqtt-port)    MQTT_PORT="$2"; shift 2 ;;
        --mqtt-port=*)  MQTT_PORT="${1#*=}"; shift ;;
        --mqtt-user)    MQTT_USER="$2"; shift 2 ;;
        --mqtt-user=*)  MQTT_USER="${1#*=}"; shift ;;
        --mqtt-pass)    MQTT_PASS="$2"; shift 2 ;;
        --mqtt-pass=*)  MQTT_PASS="${1#*=}"; shift ;;
        --wifi-ssid)    WIFI_SSID="$2"; shift 2 ;;
        --wifi-ssid=*)  WIFI_SSID="${1#*=}"; shift ;;
        --wifi-pass)    WIFI_PASS="$2"; shift 2 ;;
        --wifi-pass=*)  WIFI_PASS="${1#*=}"; shift ;;
        -h|--help)      usage ;;
        *)              echo "Unknown option: $1"; usage ;;
    esac
done

[[ -z "$PORT" ]] && { echo "Error: --port is required"; usage; }

ARTIFACT_DIR="${OUT_DIR}/${TARGET}"
APP_BIN="${ARTIFACT_DIR}/${TARGET}.bin"

if [[ ! -f "$APP_BIN" ]]; then
    echo "Error: ${APP_BIN} not found. Run ./build.sh ${TARGET} first."
    exit 1
fi

BOOT_OFFSET=$(bootloader_offset "$CHIP")

echo "=== Flashing ${TARGET} (${CHIP}) via ${PORT} ==="

ESPTOOL_COMMON=(--chip "$CHIP" --port "$PORT" --baud "$BAUD")
ESPTOOL_WRITE=("${ESPTOOL_COMMON[@]}")
[[ "$USE_STUB" == false ]] && ESPTOOL_WRITE+=(--no-stub)

WRITE_FLAGS=()
[[ "$COMPRESS" == false ]] && WRITE_FLAGS+=(--no-compress)

if [[ "$ERASE_FIRST" == true ]]; then
    # Full-chip erase requires the stub flasher (ROM bootloader only exposes erase-region).
    # Always use the stub here regardless of --no-stub for the write phase.
    echo "  Erasing flash first (--erase-first, using stub)"
    python3 -m esptool "${ESPTOOL_COMMON[@]}" erase-flash
fi

if [[ "$APP_ONLY" == true ]]; then
    echo "  App-only flash to 0x10000"
    python3 -m esptool "${ESPTOOL_WRITE[@]}" \
        write-flash ${WRITE_FLAGS[@]+"${WRITE_FLAGS[@]}"} 0x10000 "$APP_BIN"
else
    BOOTLOADER="${ARTIFACT_DIR}/bootloader.bin"
    PART_TABLE="${ARTIFACT_DIR}/partition-table.bin"
    OTA_DATA="${ARTIFACT_DIR}/ota_data_initial.bin"

    if [[ ! -f "$BOOTLOADER" ]]; then
        echo "Error: bootloader.bin not found in ${ARTIFACT_DIR}"
        exit 1
    fi
    if [[ ! -f "$PART_TABLE" ]]; then
        echo "Error: partition-table.bin not found in ${ARTIFACT_DIR}"
        exit 1
    fi

    echo "  Bootloader    -> ${BOOT_OFFSET}"
    echo "  Partition table -> 0x8000"
    echo "  App binary    -> 0x10000"
    OTA_DATA_OFFSET=$(ota_data_offset "$TARGET")
    if [[ -f "$OTA_DATA" ]]; then
        echo "  OTA data      -> ${OTA_DATA_OFFSET}"
    else
        echo "  OTA data      -> skipped (ota_data_initial.bin not found)"
    fi

    FLASH_ARGS=(
        "${ESPTOOL_WRITE[@]}"
        --before default-reset
        --after hard-reset
        write-flash
        ${WRITE_FLAGS[@]+"${WRITE_FLAGS[@]}"}
        --flash-mode dio
        --flash-size "$FLASH_SIZE"
        --flash-freq 80m
        "$BOOT_OFFSET" "$BOOTLOADER"
        0x8000 "$PART_TABLE"
        0x10000 "$APP_BIN"
    )

    if [[ -f "$OTA_DATA" ]]; then
        FLASH_ARGS+=("$OTA_DATA_OFFSET" "$OTA_DATA")
    fi

    python3 -m esptool "${FLASH_ARGS[@]}"
fi

echo ""
echo "=== Firmware flashed ==="

# NVS provisioning
if [[ "$NO_PROVISION" == true ]]; then
    echo "Skipping NVS provisioning (--no-provision)."
    monitor_boot_log
    exit 0
fi

if [[ -z "$NODE_ID" || -z "$MQTT_HOST" ]]; then
    echo ""
    echo "NVS not provisioned. To set node config, re-run with --node-id and --mqtt-host,"
    echo "or run the provisioning tool directly:"
    echo ""
    echo "  python3 tools/provision.py --port ${PORT} --chip ${CHIP} \\"
    echo "      --node-id <name> --mqtt-host <ip>"
    monitor_boot_log
    exit 0
fi

echo ""
echo "=== Provisioning NVS ==="

PROVISION_ARGS=(
    --port "$PORT"
    --chip "$CHIP"
    --node-id "$NODE_ID"
    --mqtt-host "$MQTT_HOST"
    --mqtt-port "$MQTT_PORT"
)
[[ -n "$MQTT_USER" ]]  && PROVISION_ARGS+=(--mqtt-user "$MQTT_USER")
[[ -n "$MQTT_PASS" ]]  && PROVISION_ARGS+=(--mqtt-pass "$MQTT_PASS")
[[ -n "$WIFI_SSID" ]]  && PROVISION_ARGS+=(--wifi-ssid "$WIFI_SSID")
[[ -n "$WIFI_PASS" ]]  && PROVISION_ARGS+=(--wifi-pass "$WIFI_PASS")

python3 "${SCRIPT_DIR}/tools/provision.py" "${PROVISION_ARGS[@]}"

echo ""
echo "=== Done! Node '${NODE_ID}' is ready. ==="
monitor_boot_log
