import logging
from typing import Any

from addon import ha_api
from addon.utils.oui_lookup import lookup

logger = logging.getLogger(__name__)


async def scan_entities() -> list[dict[str, Any]]:
    states = await ha_api.get_states()
    if not states:
        logger.warning("No states returned from HA API")
        return []

    anchors: list[dict[str, Any]] = []

    for entity in states:
        entity_id = entity.get("entity_id", "")
        if not entity_id.startswith("device_tracker."):
            continue

        attrs = entity.get("attributes", {})
        source_type = attrs.get("source_type", "")
        state = entity.get("state", "")

        if source_type != "router" or state != "home":
            continue

        mac = attrs.get("mac", "")
        oui = lookup(mac) if mac else "Unknown"

        anchors.append({
            "mac": mac,
            "friendly_name": attrs.get("friendly_name", entity_id),
            "oui": oui,
            "entity_id": entity_id,
            "ip": attrs.get("ip", ""),
            "host_name": attrs.get("host_name", ""),
            "ap_mac": attrs.get("ap_mac", ""),
            "anchor_type": "router",
            "confidence": 0.8,
        })

    logger.info("Found %d router-tracked home devices", len(anchors))
    return anchors
