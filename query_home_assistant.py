#!/usr/bin/env python3
"""
animated-journey Mesh - Home Assistant Discovery Script
 
This script queries your HA instance (read-only GET requests only) and saves
the results to local JSON files for review. No data is sent anywhere.

Usage:
    python3 ha_discovery.py <YOUR_LONG_LIVED_ACCESS_TOKEN>
    
    Or set the env var:
    export HA_TOKEN=<YOUR_LONG_LIVED_ACCESS_TOKEN>
    python3 ha_discovery.py

Output files (all saved to ./ha_discovery_output/):
    - config.json          : HA location (lat/lng), timezone, units
    - discovery_info.json  : HA version, install type
    - states_summary.json  : Processed summary of entities relevant to our project
    - states_raw.json      : Full raw entity dump (review before sharing)
"""

import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from collections import defaultdict

HA_URL = "http://homeassistant.local:8123"
OUTPUT_DIR = Path("./ha_discovery_output")

# ============================================================
# Integrations we care about and what attributes to extract
# ============================================================
INTERESTING_DOMAINS = {
    "device_tracker",  # UniFi, Life360, Mobile App
    "sensor",          # ESPHome, system sensors
    "binary_sensor",   # Motion sensors (Hue, Wyze)
    "light",           # Hue lights (Zigbee devices)
    "switch",          # Z-Wave, Wyze
    "camera",          # Wyze, UniFi Protect
    "media_player",    # Google Cast, Music Assistant
    "climate",         # Z-Wave thermostats
    "lock",            # Z-Wave locks
    "cover",           # Z-Wave covers
    "automation",      # skip
    "script",          # skip
    "scene",           # skip
    "zone",            # useful - geographic zones
    "person",          # useful - linked to device trackers
    "update",          # skip
    "number",          # skip
    "select",          # skip
    "button",          # skip
    "input_boolean",   # skip
    "input_number",    # skip
    "input_select",    # skip
    "input_text",      # skip
    "input_datetime",  # skip
    "timer",           # skip
    "counter",         # skip
    "todo",            # skip
    "conversation",    # skip
    "stt",             # skip
    "tts",             # skip
    "weather",         # skip
}

# Domains we always want full detail on
PRIORITY_DOMAINS = {
    "device_tracker",  # Life360, UniFi, Mobile App - GPS + RSSI
    "person",          # Linked trackers
    "zone",            # Geographic zones with lat/lng
}

# Attributes that might contain useful RF/location data
USEFUL_ATTRIBUTES = {
    "mac", "ip", "ip_address", "mac_address",
    "latitude", "longitude", "gps_accuracy",
    "source_type", "ssid", "bssid",
    "rssi", "signal_strength", "wifi_signal",
    "ap_mac", "essid",
    "device_class", "device_type",
    "manufacturer", "model", "sw_version",
    "friendly_name", "entity_id",
    "unique_id", "hostname",
    "is_connected", "connection_type",
    "battery_level", "battery",
}

SKIP_DOMAINS = {
    "automation", "script", "scene", "update", "number", "select",
    "button", "input_boolean", "input_number", "input_select",
    "input_text", "input_datetime", "timer", "counter", "todo",
    "conversation", "stt", "tts", "weather", "persistent_notification",
    "sun", "input_button", "schedule", "tag", "event",
}


def api_get(endpoint: str, token: str) -> dict | list | None:
    """Make a GET request to the HA REST API. Read-only."""
    url = f"{HA_URL}/api/{endpoint}"
    req = Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")

    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        print(f"  ERROR {e.code} on GET /api/{endpoint}: {e.reason}")
        return None
    except URLError as e:
        print(f"  ERROR connecting to {HA_URL}: {e.reason}")
        print("  Make sure homeassistant.local is reachable from this machine.")
        return None


def extract_integration_hint(entity_id: str, attributes: dict) -> str:
    """Try to guess which integration an entity belongs to."""
    attr_str = json.dumps(attributes).lower()

    if "life360" in entity_id or "life360" in attr_str:
        return "life360"
    if "unifi" in entity_id or "ubiquiti" in attr_str or "unifi" in attr_str:
        return "unifi"
    if "hue" in entity_id or "philips" in attr_str or "hue" in attr_str:
        return "philips_hue"
    if "wyze" in entity_id or "wyze" in attr_str:
        return "wyze"
    if "esphome" in entity_id or "esphome" in attr_str:
        return "esphome"
    if "cast" in entity_id or "chromecast" in attr_str or "google_cast" in attr_str:
        return "google_cast"
    if "zwave" in entity_id or "z-wave" in attr_str or "z_wave" in attr_str:
        return "zwave"
    if "mobile_app" in entity_id or "mobile_app" in attr_str:
        return "mobile_app"
    if "ollama" in entity_id or "ollama" in attr_str:
        return "ollama"
    if "mqtt" in entity_id or "mqtt" in attr_str:
        return "mqtt"
    if "protect" in entity_id or "protect" in attr_str:
        return "unifi_protect"
    if "discord" in entity_id:
        return "discord"
    if "music_assistant" in entity_id:
        return "music_assistant"
    if "matter" in entity_id:
        return "matter"

    return "unknown"


def filter_useful_attributes(attributes: dict) -> dict:
    """Extract only attributes that are useful for our RF mapping project."""
    useful = {}
    for key, value in attributes.items():
        key_lower = key.lower()
        if key_lower in USEFUL_ATTRIBUTES:
            useful[key] = value
        elif any(term in key_lower for term in [
            "mac", "rssi", "signal", "ip_addr", "latitude", "longitude",
            "gps", "wifi", "ssid", "bssid", "channel", "frequency",
            "manufacturer", "model", "hostname", "ap_", "connection"
        ]):
            useful[key] = value
    return useful


def summarize_states(states: list) -> dict:
    """Process raw states into a useful summary grouped by integration."""
    by_integration = defaultdict(list)
    by_domain = defaultdict(int)
    priority_entities = []
    rf_relevant = []

    for entity in states:
        entity_id = entity.get("entity_id", "")
        domain = entity_id.split(".")[0] if "." in entity_id else "unknown"
        attributes = entity.get("attributes", {})
        state = entity.get("state", "")

        by_domain[domain] += 1

        # Skip boring domains
        if domain in SKIP_DOMAINS:
            continue

        integration = extract_integration_hint(entity_id, attributes)
        useful_attrs = filter_useful_attributes(attributes)

        entry = {
            "entity_id": entity_id,
            "state": state,
            "domain": domain,
            "integration_guess": integration,
            "friendly_name": attributes.get("friendly_name", ""),
        }

        # Priority entities get full attributes
        if domain in PRIORITY_DOMAINS:
            entry["all_attributes"] = attributes
            priority_entities.append(entry)
        elif useful_attrs:
            entry["useful_attributes"] = useful_attrs
            rf_relevant.append(entry)
        else:
            entry["attribute_keys"] = list(attributes.keys())

        by_integration[integration].append(entry)

    return {
        "_summary": {
            "total_entities": len(states),
            "by_domain": dict(sorted(by_domain.items(), key=lambda x: -x[1])),
            "by_integration": {k: len(v) for k, v in sorted(
                by_integration.items(), key=lambda x: -len(x[1])
            )},
        },
        "priority_entities": priority_entities,
        "rf_relevant_entities": rf_relevant,
        "by_integration": {k: v for k, v in sorted(
            by_integration.items(), key=lambda x: -len(x[1])
        )},
    }


def main():
    # Get token from arg or env (never saved to disk)
    token = None
    if len(sys.argv) > 1:
        token = sys.argv[1]
    else:
        token = os.environ.get("HA_TOKEN")

    if not token:
        print("Usage: python3 ha_discovery.py <HA_TOKEN>")
        print("   or: export HA_TOKEN=<token> && python3 ha_discovery.py")
        sys.exit(1)

    OUTPUT_DIR.mkdir(exist_ok=True)

    print(f"Querying {HA_URL} ...")
    print(f"Output will be saved to {OUTPUT_DIR.resolve()}/")
    print()

    # 1. Discovery info
    print("[1/4] GET /api/discovery_info")
    discovery = api_get("discovery_info", token)
    if discovery:
        (OUTPUT_DIR / "discovery_info.json").write_text(
            json.dumps(discovery, indent=2)
        )
        print(f"  HA version: {discovery.get('version', '?')}")
        print(f"  Install type: {discovery.get('installation_type', '?')}")

    # 2. Config
    print("[2/4] GET /api/config")
    config = api_get("config", token)
    if config:
        # Redact some fields but keep location
        safe_config = {
            "latitude": config.get("latitude"),
            "longitude": config.get("longitude"),
            "elevation": config.get("elevation"),
            "unit_system": config.get("unit_system"),
            "time_zone": config.get("time_zone"),
            "version": config.get("version"),
            "components": sorted(config.get("components", [])),
        }
        (OUTPUT_DIR / "config.json").write_text(
            json.dumps(safe_config, indent=2)
        )
        lat = config.get("latitude", "?")
        lng = config.get("longitude", "?")
        print(f"  Location: {lat}, {lng}")
        print(f"  Timezone: {config.get('time_zone', '?')}")
        print(f"  Components loaded: {len(config.get('components', []))}")

    # 3. Services (just a count + list of domains)
    print("[3/4] GET /api/services")
    services = api_get("services", token)
    if services:
        service_domains = [s.get("domain", "?") for s in services]
        (OUTPUT_DIR / "services_domains.json").write_text(
            json.dumps(sorted(service_domains), indent=2)
        )
        print(f"  Service domains: {len(service_domains)}")

    # 4. States (the big one)
    print("[4/4] GET /api/states")
    states = api_get("states", token)
    if states:
        # Save raw (for user review before sharing)
        (OUTPUT_DIR / "states_raw.json").write_text(
            json.dumps(states, indent=2)
        )
        print(f"  Total entities: {len(states)}")

        # Save processed summary
        summary = summarize_states(states)
        (OUTPUT_DIR / "states_summary.json").write_text(
            json.dumps(summary, indent=2)
        )

        # Print highlights
        s = summary["_summary"]
        print(f"\n{'='*60}")
        print(f"DISCOVERY SUMMARY")
        print(f"{'='*60}")
        print(f"\nEntities by domain:")
        for domain, count in list(s["by_domain"].items())[:15]:
            print(f"  {domain}: {count}")

        print(f"\nEntities by integration (guessed):")
        for integration, count in s["by_integration"].items():
            print(f"  {integration}: {count}")

        print(f"\nPriority entities (device_tracker, person, zone):")
        for e in summary["priority_entities"]:
            attrs = e.get("all_attributes", {})
            mac = attrs.get("mac", attrs.get("mac_address", ""))
            lat = attrs.get("latitude", "")
            lng = attrs.get("longitude", "")
            src = attrs.get("source_type", "")
            extra = []
            if mac:
                extra.append(f"MAC={mac}")
            if lat and lng:
                extra.append(f"GPS={lat},{lng}")
            if src:
                extra.append(f"src={src}")
            extra_str = f" [{', '.join(extra)}]" if extra else ""
            print(f"  {e['entity_id']}: {e['state']}{extra_str}")

        print(f"\nRF-relevant entities (have MAC, RSSI, signal, GPS attrs):")
        for e in summary["rf_relevant_entities"][:30]:
            ua = e.get("useful_attributes", {})
            print(f"  {e['entity_id']}: {json.dumps(ua)}")

    print(f"\n{'='*60}")
    print(f"Files saved to: {OUTPUT_DIR.resolve()}/")
    print(f"  - discovery_info.json  (HA version)")
    print(f"  - config.json          (location, timezone, components)")
    print(f"  - services_domains.json(service domain list)")
    print(f"  - states_raw.json      (FULL entity dump - review before sharing!)")
    print(f"  - states_summary.json  (processed summary - safe to share)")
    print(f"\nReview states_raw.json for any sensitive data before sharing.")
    print(f"The states_summary.json is pre-filtered and safe to share.")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()