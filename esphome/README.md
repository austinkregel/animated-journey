# ESPHome BLE Proxy Configs

These configs add BLE advertisement scanning to existing ESPHome devices,
publishing scan data to the Burtooth Mesh MQTT topics.

## Setup

1. Copy `ble-proxy-base.yaml` to your ESPHome config directory
2. Copy the device-specific YAML files for your devices
3. Create/update your `secrets.yaml` with WiFi and API credentials
4. Flash via ESPHome dashboard

## Devices

| File | MAC | Description |
|------|-----|-------------|
| `esphome-web-54bafc.yaml` | `f0:9e:9e:54:ba:fc` | Generic ESP32 dev board |
| `office-speaker.yaml` | `80:b5:4e:f1:e7:c4` | ESP32-S3 audio board |

## How It Works

The `ble-proxy-base.yaml` package adds:
- `esp32_ble_tracker` in passive scan mode (200ms interval, 160ms window)
- MQTT publishing of BLE advertisements to `burtooth/scan/ble/adv/{device_name}`
- SNTP time sync for accurate timestamps

The JSON payload matches the Burtooth Mesh scan format so the positioning
engine processes these just like native scanner nodes.
