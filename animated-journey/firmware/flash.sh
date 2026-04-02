#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="${SCRIPT_DIR}/out"

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
  --no-provision    Skip NVS provisioning prompt
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

[[ $# -lt 1 ]] && usage

TARGET="$1"; shift
CHIP=$(chip_for_target "$TARGET")
PORT=""
APP_ONLY=false
NO_PROVISION=false
NODE_ID=""
MQTT_HOST=""
MQTT_PORT=1883
MQTT_USER=""
MQTT_PASS=""
WIFI_SSID=""
WIFI_PASS=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)         PORT="$2"; shift 2 ;;
        --app-only)     APP_ONLY=true; shift ;;
        --no-provision) NO_PROVISION=true; shift ;;
        --node-id)      NODE_ID="$2"; shift 2 ;;
        --mqtt-host)    MQTT_HOST="$2"; shift 2 ;;
        --mqtt-port)    MQTT_PORT="$2"; shift 2 ;;
        --mqtt-user)    MQTT_USER="$2"; shift 2 ;;
        --mqtt-pass)    MQTT_PASS="$2"; shift 2 ;;
        --wifi-ssid)    WIFI_SSID="$2"; shift 2 ;;
        --wifi-pass)    WIFI_PASS="$2"; shift 2 ;;
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

if [[ "$APP_ONLY" == true ]]; then
    echo "  App-only flash to 0x10000"
    python3 -m esptool --chip "$CHIP" --port "$PORT" --baud 460800 \
        write_flash 0x10000 "$APP_BIN"
else
    BOOTLOADER="${ARTIFACT_DIR}/bootloader.bin"
    PART_TABLE="${ARTIFACT_DIR}/partition-table.bin"

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

    python3 -m esptool --chip "$CHIP" --port "$PORT" --baud 460800 \
        write_flash \
        "$BOOT_OFFSET" "$BOOTLOADER" \
        0x8000 "$PART_TABLE" \
        0x10000 "$APP_BIN"
fi

echo ""
echo "=== Firmware flashed ==="

# NVS provisioning
if [[ "$NO_PROVISION" == true ]]; then
    echo "Skipping NVS provisioning (--no-provision)."
    exit 0
fi

if [[ -z "$NODE_ID" || -z "$MQTT_HOST" ]]; then
    echo ""
    echo "NVS not provisioned. To set node config, re-run with --node-id and --mqtt-host,"
    echo "or run the provisioning tool directly:"
    echo ""
    echo "  python3 tools/provision.py --port ${PORT} --chip ${CHIP} \\"
    echo "      --node-id <name> --mqtt-host <ip>"
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
echo "Monitor with: python3 -m serial.tools.miniterm ${PORT} 115200  (pip install pyserial)"
