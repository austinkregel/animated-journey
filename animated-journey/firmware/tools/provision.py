#!/usr/bin/env python3
"""
NVS provisioning tool for animated-journey scanner nodes.

Generates an NVS partition binary from the given configuration and
flashes it to the device via esptool.

Usage:
    python provision.py --port /dev/ttyACM0 --node-id scanner-01 \
        --mqtt-host 192.168.1.100

Requirements:
    pip install esptool
"""

import argparse
import csv
import io
import os
import struct
import subprocess
import sys
import tempfile

NVS_PARTITION_OFFSET = 0x9000
NVS_PARTITION_SIZE = 0x6000
NVS_NAMESPACE = "aj-node-cfg"


def generate_nvs_csv(args):
    """Build the NVS CSV content from CLI arguments."""
    rows = [
        ["key", "type", "encoding", "value"],
        [NVS_NAMESPACE, "namespace", "", ""],
        ["node_id", "data", "string", args.node_id],
        ["mqtt_host", "data", "string", args.mqtt_host],
        ["mqtt_port", "data", "u16", str(args.mqtt_port)],
    ]

    if args.mqtt_user:
        rows.append(["mqtt_user", "data", "string", args.mqtt_user])
    if args.mqtt_pass:
        rows.append(["mqtt_pass", "data", "string", args.mqtt_pass])
    if args.wifi_ssid:
        rows.append(["wifi_ssid", "data", "string", args.wifi_ssid])
    if args.wifi_pass:
        rows.append(["wifi_pass", "data", "string", args.wifi_pass])

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerows(rows)
    return buf.getvalue()


def find_nvs_partition_gen():
    """Locate nvs_partition_gen.py from ESP-IDF or pip."""
    idf_path = os.environ.get("IDF_PATH")
    if idf_path:
        candidate = os.path.join(
            idf_path, "components", "nvs_flash", "nvs_partition_generator",
            "nvs_partition_gen.py"
        )
        if os.path.isfile(candidate):
            return candidate

    try:
        result = subprocess.run(
            [sys.executable, "-m", "esp_idf_nvs_partition_gen", "--help"],
            capture_output=True
        )
        if result.returncode == 0:
            return None  # use module invocation
    except Exception:
        pass

    print("ERROR: Cannot find nvs_partition_gen.py.")
    print("  Set IDF_PATH or install: pip install esp-idf-nvs-partition-gen")
    sys.exit(1)


def generate_nvs_binary(csv_content, output_path, size):
    """Generate NVS partition binary from CSV."""
    gen_script = find_nvs_partition_gen()

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False
    ) as f:
        f.write(csv_content)
        csv_path = f.name

    try:
        if gen_script:
            cmd = [
                sys.executable, gen_script, "generate",
                csv_path, output_path, str(hex(size))
            ]
        else:
            cmd = [
                sys.executable, "-m", "esp_idf_nvs_partition_gen", "generate",
                csv_path, output_path, str(hex(size))
            ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"nvs_partition_gen failed:")
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(result.stderr)
            sys.exit(1)
    finally:
        os.unlink(csv_path)


def flash_partition(port, chip, bin_path, offset):
    """Flash the NVS binary to the device."""
    cmd = [
        sys.executable, "-m", "esptool",
        "--chip", chip,
        "--port", port,
        "write_flash", hex(offset), bin_path
    ]
    print(f"Flashing NVS to {hex(offset)}...")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("Flash failed!")
        sys.exit(1)
    print("NVS partition flashed successfully.")


def main():
    parser = argparse.ArgumentParser(
        description="Provision an animated-journey scanner node"
    )
    parser.add_argument("--port", required=True, help="Serial port (e.g. /dev/ttyACM0)")
    parser.add_argument("--chip", default="esp32p4", help="Chip type (default: esp32p4)")
    parser.add_argument("--node-id", required=True, help="Unique node identifier")
    parser.add_argument("--mqtt-host", required=True, help="MQTT broker hostname/IP")
    parser.add_argument("--mqtt-port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--mqtt-user", default="", help="MQTT username")
    parser.add_argument("--mqtt-pass", default="", help="MQTT password")
    parser.add_argument("--wifi-ssid", default="", help="WiFi SSID (optional, for C6/S3 nodes)")
    parser.add_argument("--wifi-pass", default="", help="WiFi password (optional)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Generate NVS binary without flashing")

    args = parser.parse_args()

    csv_content = generate_nvs_csv(args)
    print(f"NVS config for node '{args.node_id}':")
    print(csv_content)

    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        nvs_bin_path = f.name

    try:
        generate_nvs_binary(csv_content, nvs_bin_path, NVS_PARTITION_SIZE)
        print(f"NVS binary generated: {nvs_bin_path} "
              f"({os.path.getsize(nvs_bin_path)} bytes)")

        if args.dry_run:
            print("Dry run -- skipping flash.")
            return

        flash_partition(args.port, args.chip, nvs_bin_path, NVS_PARTITION_OFFSET)

    finally:
        if os.path.exists(nvs_bin_path):
            os.unlink(nvs_bin_path)


if __name__ == "__main__":
    main()
