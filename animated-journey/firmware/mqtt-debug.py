#!/usr/bin/env python3
"""
MQTT debug subscriber for animated-journey scanner nodes.

Connects to the MQTT broker and subscribes to all animated-journey topics,
printing a live summary of what's flowing through the system.

Usage:
    python3 mqtt-debug.py                          # defaults: 192.168.3.52:1883
    python3 mqtt-debug.py --host 10.0.0.5 --port 1883 --user admin --password secret
"""

import argparse
import json
import signal
import sys
import time
from collections import defaultdict

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("Missing dependency: paho-mqtt")
    print("Install it with:  pip3 install paho-mqtt")
    sys.exit(1)


PREFIX = "animated-journey"

stats = {
    "connected": False,
    "connect_time": None,
    "msg_total": 0,
    "ble_adv": defaultdict(int),       # node_id -> count
    "ble_macs": set(),                  # unique BLE MACs seen
    "wifi_probe": defaultdict(int),
    "wifi_beacon": defaultdict(int),
    "node_status": {},                  # node_id -> last status dict
    "discovery": [],                    # discovery config topics
    "unknown_topics": defaultdict(int),
    "last_ble_payload": None,
    "last_status_payload": None,
}

CYAN    = "\033[36m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
RED     = "\033[31m"
DIM     = "\033[2m"
BOLD    = "\033[1m"
RESET   = "\033[0m"


def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        stats["connected"] = True
        stats["connect_time"] = time.time()
        print(f"{GREEN}Connected to MQTT broker{RESET}")
        client.subscribe(f"{PREFIX}/#", qos=0)
        client.subscribe("homeassistant/#", qos=0)
        print(f"{DIM}Subscribed to {PREFIX}/# and homeassistant/#{RESET}")
        print(f"{DIM}Waiting for messages...{RESET}\n")
    else:
        print(f"{RED}Connection failed: rc={rc}{RESET}")


def on_disconnect(client, userdata, rc, properties=None, reason_code=None):
    stats["connected"] = False
    print(f"\n{RED}Disconnected from broker (rc={rc}){RESET}")


def on_message(client, userdata, msg):
    stats["msg_total"] += 1
    topic = msg.topic
    try:
        payload = json.loads(msg.payload.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = msg.payload.decode("utf-8", errors="replace")

    # --- HA discovery configs ---
    if topic.startswith("homeassistant/"):
        if stats["msg_total"] <= 5 or len(stats["discovery"]) < 20:
            stats["discovery"].append(topic)
            print(f"  {DIM}[discovery] {topic}{RESET}")
        return

    parts = topic.split("/")
    # animated-journey/scan/<type>/<node_id>
    if len(parts) == 4 and parts[1] == "scan":
        scan_type = parts[2]
        node_id = parts[3]

        if scan_type == "ble_adv":
            stats["ble_adv"][node_id] += 1
            if isinstance(payload, dict):
                mac = payload.get("mac", "?")
                stats["ble_macs"].add(mac)
                stats["last_ble_payload"] = payload
                rssi = payload.get("rssi", "?")
                name = payload.get("name", "")
                event = payload.get("event", "seen")
                seen_count = payload.get("seen_count", "?")
                tag = f"{GREEN}SEEN{RESET}" if event == "seen" else f"{RED}GONE{RESET}"
                total = stats["ble_adv"][node_id]
                if total <= 3 or total % 50 == 0:
                    print(f"  {CYAN}[ble_adv]{RESET} {tag} node={node_id} mac={mac} "
                          f"rssi={rssi} name=\"{name}\" x{seen_count} "
                          f"{DIM}(#{total}, {len(stats['ble_macs'])} unique MACs){RESET}")
        elif scan_type == "wifi_probe":
            stats["wifi_probe"][node_id] += 1
            total = stats["wifi_probe"][node_id]
            if total <= 3 or total % 50 == 0:
                ssid = payload.get("ssid", "?") if isinstance(payload, dict) else "?"
                print(f"  {YELLOW}[wifi_probe]{RESET} node={node_id} ssid=\"{ssid}\" "
                      f"{DIM}(#{total}){RESET}")
        elif scan_type == "wifi_beacon":
            stats["wifi_beacon"][node_id] += 1
            total = stats["wifi_beacon"][node_id]
            if total <= 3 or total % 50 == 0:
                ssid = payload.get("ssid", "?") if isinstance(payload, dict) else "?"
                print(f"  {YELLOW}[wifi_beacon]{RESET} node={node_id} ssid=\"{ssid}\" "
                      f"{DIM}(#{total}){RESET}")
        else:
            stats["unknown_topics"][topic] += 1
            print(f"  {DIM}[scan/{scan_type}] node={node_id}{RESET}")
        return

    # animated-journey/nodes/<node_id>/status
    if len(parts) == 4 and parts[1] == "nodes" and parts[3] == "status":
        node_id = parts[2]
        stats["node_status"][node_id] = payload
        stats["last_status_payload"] = payload
        if isinstance(payload, dict):
            fw = payload.get("fw_version", "?")
            uptime = payload.get("uptime", payload.get("uptime_s", "?"))
            heap = payload.get("free_heap", "?")
            ble_count = payload.get("ble", payload.get("ble_count", "?"))
            ble_active = payload.get("ble_active", "?")
            temp = payload.get("chip_temp", None)
            chip = payload.get("chip_model", "")
            cpu_mhz = payload.get("cpu_freq", "")
            psram = payload.get("psram_free", None)
            psram_t = payload.get("psram_total", None)
            extras = ""
            if temp is not None:
                extras += f" temp={temp}C"
            if chip:
                extras += f" chip={chip}"
            if cpu_mhz:
                extras += f" cpu={cpu_mhz}MHz"
            if psram is not None and psram_t:
                pct = (psram / psram_t * 100) if psram_t else 0
                extras += f" psram={psram}/{psram_t}({pct:.0f}%free)"
            print(f"  {GREEN}[status]{RESET} node={BOLD}{node_id}{RESET} "
                  f"fw={fw} uptime={uptime}s heap={heap} "
                  f"ble_total={ble_count} ble_active={ble_active}{extras}")
        else:
            print(f"  {GREEN}[status]{RESET} node={node_id} payload={payload}")
        return

    # animated-journey/position/<mac_hash>
    if len(parts) == 3 and parts[1] == "position":
        mac_hash = parts[2]
        stats["positions"] = stats.get("positions", defaultdict(int))
        stats["position_macs"] = stats.get("position_macs", set())
        stats["positions"]["total"] += 1
        stats["position_macs"].add(mac_hash)
        total = stats["positions"]["total"]
        if total <= 3 or total % 100 == 0:
            acc = payload.get("accuracy_m", "?") if isinstance(payload, dict) else "?"
            x = payload.get("x", "?") if isinstance(payload, dict) else "?"
            y = payload.get("y", "?") if isinstance(payload, dict) else "?"
            print(f"  {CYAN}[position]{RESET} mac_hash={mac_hash} "
                  f"pos=({x},{y}) acc={acc}m "
                  f"{DIM}(#{total}, {len(stats['position_macs'])} tracked){RESET}")
        return

    # animated-journey/nodes/<node_id>/cmd/...
    if len(parts) >= 4 and parts[1] == "nodes" and parts[3] == "cmd":
        cmd = "/".join(parts[4:]) if len(parts) > 4 else "?"
        print(f"  {YELLOW}[cmd]{RESET} node={parts[2]} cmd={cmd}")
        return

    stats["unknown_topics"][topic] += 1
    if stats["unknown_topics"][topic] <= 3:
        short = str(payload)[:120] if payload else ""
        print(f"  {DIM}[???] {topic} -> {short}{RESET}")


def print_summary():
    elapsed = time.time() - stats["connect_time"] if stats["connect_time"] else 0
    print(f"\n{'='*60}")
    print(f"{BOLD}Summary after {elapsed:.0f}s{RESET}")
    print(f"  Total messages:  {stats['msg_total']}")
    print(f"  Discovery msgs:  {len(stats['discovery'])}")

    if stats["ble_adv"]:
        print(f"\n  {CYAN}BLE Advertisements:{RESET}")
        for node, count in sorted(stats["ble_adv"].items()):
            print(f"    {node}: {count} messages")
        print(f"    Unique MACs seen: {len(stats['ble_macs'])}")
    else:
        print(f"\n  {RED}BLE Advertisements: NONE{RESET}")
        print(f"    -> No scan data is flowing. Check firmware logs.")

    if stats["wifi_probe"]:
        print(f"\n  {YELLOW}WiFi Probes:{RESET}")
        for node, count in sorted(stats["wifi_probe"].items()):
            print(f"    {node}: {count}")

    if stats["wifi_beacon"]:
        print(f"\n  {YELLOW}WiFi Beacons:{RESET}")
        for node, count in sorted(stats["wifi_beacon"].items()):
            print(f"    {node}: {count}")

    if stats["node_status"]:
        print(f"\n  {GREEN}Node Statuses:{RESET}")
        for node, st in sorted(stats["node_status"].items()):
            if isinstance(st, dict):
                temp = st.get('chip_temp')
                temp_str = f" temp={temp}C" if temp is not None else ""
                chip = st.get('chip_model', '')
                chip_str = f" chip={chip}" if chip else ""
                cpu = st.get('cpu_freq', '')
                cpu_str = f" cpu={cpu}MHz" if cpu else ""
                psf = st.get('psram_free')
                pst = st.get('psram_total')
                ps_str = f" psram={psf}/{pst}" if psf is not None else ""
                rst = st.get('reset_reason', '')
                rst_str = f" rst={rst}" if rst != '' else ""
                idf = st.get('idf_version', '')
                idf_str = f" idf={idf}" if idf else ""
                print(f"    {node}: fw={st.get('fw_version','?')} "
                      f"uptime={st.get('uptime', st.get('uptime_s','?'))}s "
                      f"ble={st.get('ble', st.get('ble_count','?'))}/{st.get('ble_active','?')} "
                      f"heap={st.get('free_heap','?')}"
                      f"{temp_str}{chip_str}{cpu_str}{ps_str}{rst_str}{idf_str}")
            else:
                print(f"    {node}: {st}")
    else:
        print(f"\n  {RED}Node Statuses: NONE{RESET}")
        print(f"    -> No status messages. Are any scanners online?")

    positions = stats.get("positions", {})
    position_macs = stats.get("position_macs", set())
    if positions:
        print(f"\n  {CYAN}Positions (addon output):{RESET}")
        print(f"    Total position updates: {positions.get('total', 0)}")
        print(f"    Unique devices tracked: {len(position_macs)}")
    else:
        print(f"\n  {RED}Positions: NONE{RESET}")
        print(f"    -> Addon is not publishing position estimates.")

    if stats["last_ble_payload"]:
        print(f"\n  {DIM}Last BLE payload:{RESET}")
        print(f"    {json.dumps(stats['last_ble_payload'], indent=2)}")

    if stats["unknown_topics"]:
        print(f"\n  Unrecognized topics: {dict(stats['unknown_topics'])}")

    print(f"{'='*60}\n")


def cleanup_stale_devices(client, stale_node_ids):
    """Remove retained HA discovery configs for old node IDs by publishing empty payloads."""
    suffixes = ["ble", "ble_active", "probes", "beacons", "uptime", "heap", "fw", "wifi_rssi"]
    count = 0
    for node_id in stale_node_ids:
        for suffix in suffixes:
            topic = f"homeassistant/sensor/animated-journey_{node_id}_{suffix}/config"
            client.publish(topic, "", qos=0, retain=True)
            count += 1
        topic = f"homeassistant/button/animated-journey_{node_id}_identify/config"
        client.publish(topic, "", qos=0, retain=True)
        count += 1
        status_topic = f"{PREFIX}/nodes/{node_id}/status"
        client.publish(status_topic, "", qos=0, retain=True)
        count += 1
    print(f"{GREEN}Published {count} empty retained messages to remove {len(stale_node_ids)} stale device(s){RESET}")
    print(f"Removed node IDs: {', '.join(stale_node_ids)}")
    print(f"Go to HA -> Settings -> Devices & Services -> MQTT -> delete the orphaned devices if they linger.")


def main():
    parser = argparse.ArgumentParser(description="MQTT debug subscriber for animated-journey")
    parser.add_argument("--host", default="192.168.3.52", help="MQTT broker host")
    parser.add_argument("--port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--user", default=None, help="MQTT username")
    parser.add_argument("--password", default=None, help="MQTT password")
    parser.add_argument("--duration", type=int, default=0,
                        help="Run for N seconds then print summary (0 = run forever, Ctrl+C for summary)")
    parser.add_argument("--cleanup", nargs="+", metavar="NODE_ID",
                        help="Remove stale HA devices by old node_id (e.g. --cleanup house-01 speaker-01)")
    args = parser.parse_args()

    print(f"{BOLD}animated-journey MQTT debug subscriber{RESET}")
    print(f"Broker: {args.host}:{args.port}")
    print(f"Topics: {PREFIX}/# + homeassistant/#")
    print()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="aj-debug-sub")
    if args.user:
        client.username_pw_set(args.user, args.password)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    def handle_signal(sig, frame):
        print_summary()
        client.disconnect()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)

    try:
        client.connect(args.host, args.port, keepalive=60)
    except Exception as e:
        print(f"{RED}Failed to connect: {e}{RESET}")
        sys.exit(1)

    if args.cleanup:
        client.loop_start()
        time.sleep(1)
        cleanup_stale_devices(client, args.cleanup)
        time.sleep(1)
        client.disconnect()
        client.loop_stop()
    elif args.duration > 0:
        client.loop_start()
        time.sleep(args.duration)
        print_summary()
        client.disconnect()
        client.loop_stop()
    else:
        print(f"{DIM}Press Ctrl+C to stop and print summary{RESET}\n")
        client.loop_forever()


if __name__ == "__main__":
    main()
