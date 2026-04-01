import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from addon.utils.geo import gps_to_local

logger = logging.getLogger(__name__)

ANCHORS_FILE = "/data/anchors.json"

# Known fixed-location devices to seed from Home Assistant
_KNOWN_FIXED_DEVICES: list[dict] = [
    {"mac": "ec:b5:fa:98:f8:a5", "name": "Hue Bridge", "type": "wifi"},
    {"mac": "20:f8:3b:09:83:37", "name": "HA Voice 1", "type": "ble"},
    {"mac": "20:f8:3b:09:a2:bc", "name": "HA Voice 2", "type": "ble"},
    {"mac": "04:d4:c4:55:d3:20", "name": "HA Server", "type": "wifi"},
]


@dataclass
class AnchorInfo:
    mac: str
    name: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    confidence: str = "low"
    discovered_via: str = "observation"
    signal_type: str = "ble"
    last_seen: float = 0.0
    avg_rssi: float = 0.0

    def to_dict(self) -> dict:
        return {
            "mac": self.mac,
            "name": self.name,
            "lat": self.lat,
            "lng": self.lng,
            "confidence": self.confidence,
            "discovered_via": self.discovered_via,
            "signal_type": self.signal_type,
            "last_seen": self.last_seen,
            "avg_rssi": self.avg_rssi,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AnchorInfo":
        return cls(
            mac=data["mac"],
            name=data.get("name", data["mac"]),
            lat=data.get("lat"),
            lng=data.get("lng"),
            confidence=data.get("confidence", "low"),
            discovered_via=data.get("discovered_via", "observation"),
            signal_type=data.get("signal_type", "ble"),
            last_seen=data.get("last_seen", 0.0),
            avg_rssi=data.get("avg_rssi", 0.0),
        )


class AnchorManager:
    """Manages fixed-location reference anchors for positioning calibration."""

    def __init__(self):
        self.anchors: dict[str, AnchorInfo] = {}
        self._rssi_history: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self._history_timestamps: dict[str, float] = {}

    async def load(self) -> None:
        if not os.path.exists(ANCHORS_FILE):
            logger.info("No anchors file found at %s", ANCHORS_FILE)
            return
        try:
            with open(ANCHORS_FILE, "r") as f:
                data = json.load(f)
            for item in data:
                anchor = AnchorInfo.from_dict(item)
                self.anchors[anchor.mac] = anchor
            logger.info("Loaded %d anchors from %s", len(self.anchors), ANCHORS_FILE)
        except Exception:
            logger.exception("Failed to load anchors from %s", ANCHORS_FILE)

    async def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(ANCHORS_FILE), exist_ok=True)
            data = [anchor.to_dict() for anchor in self.anchors.values()]
            with open(ANCHORS_FILE, "w") as f:
                json.dump(data, f, indent=2)
            logger.debug("Saved %d anchors to %s", len(self.anchors), ANCHORS_FILE)
        except Exception:
            logger.exception("Failed to save anchors to %s", ANCHORS_FILE)

    async def seed_from_ha(self, ha_api, origin_lat: float, origin_lng: float) -> None:
        """Seed anchor positions from known fixed devices in Home Assistant."""
        try:
            states = await ha_api.get_states()
            if not states:
                logger.warning("Could not fetch HA states for anchor seeding")
                return

            entity_macs: dict[str, dict] = {}
            for state in states:
                entity_id = state.get("entity_id", "")
                attrs = state.get("attributes", {})

                mac = attrs.get("mac", "").lower().replace("-", ":")
                if not mac:
                    mac = attrs.get("mac_address", "").lower().replace("-", ":")
                if not mac:
                    continue
                entity_macs[mac] = {
                    "entity_id": entity_id,
                    "lat": attrs.get("latitude"),
                    "lng": attrs.get("longitude"),
                    "friendly_name": attrs.get("friendly_name", entity_id),
                }

            for device in _KNOWN_FIXED_DEVICES:
                mac = device["mac"].lower()
                if mac in self.anchors and self.anchors[mac].confidence == "high":
                    continue

                anchor = AnchorInfo(
                    mac=mac,
                    name=device["name"],
                    confidence="high",
                    discovered_via="ha_entity",
                    signal_type=device.get("type", "ble"),
                    last_seen=time.time(),
                )

                ha_info = entity_macs.get(mac)
                if ha_info and ha_info.get("lat") and ha_info.get("lng"):
                    anchor.lat = ha_info["lat"]
                    anchor.lng = ha_info["lng"]

                self.anchors[mac] = anchor
                logger.info("Seeded anchor: %s (%s)", device["name"], mac)

            await self.save()
        except Exception:
            logger.exception("Error seeding anchors from HA")

    def record_observation(self, mac: str, node_id: str, rssi: float) -> None:
        mac = mac.lower()
        history = self._rssi_history[mac][node_id]
        history.append(rssi)
        # Keep a rolling window of 1000 observations per node
        if len(history) > 1000:
            self._rssi_history[mac][node_id] = history[-1000:]

        if mac not in self._history_timestamps:
            self._history_timestamps[mac] = time.time()

    def analyze_stability(
        self,
        min_hours: float = 24.0,
        max_rssi_stddev: float = 3.0,
        min_samples: int = 100,
    ) -> list[AnchorInfo]:
        """Identify MACs with stable RSSI patterns that likely represent fixed devices."""
        now = time.time()
        new_anchors: list[AnchorInfo] = []

        for mac, node_rssi in self._rssi_history.items():
            if mac in self.anchors:
                continue

            first_seen = self._history_timestamps.get(mac, now)
            hours_observed = (now - first_seen) / 3600.0
            if hours_observed < min_hours:
                continue

            # Check if any single node shows very stable RSSI
            for node_id, rssi_list in node_rssi.items():
                if len(rssi_list) < min_samples:
                    continue

                arr = np.array(rssi_list)
                stddev = float(np.std(arr))
                if stddev <= max_rssi_stddev:
                    avg_rssi = float(np.mean(arr))
                    anchor = AnchorInfo(
                        mac=mac,
                        name=f"auto_{mac.replace(':', '')}",
                        confidence="medium",
                        discovered_via="stability_analysis",
                        avg_rssi=avg_rssi,
                        last_seen=now,
                    )
                    self.anchors[mac] = anchor
                    new_anchors.append(anchor)
                    logger.info(
                        "Discovered stable anchor %s via node %s (stddev=%.2f, n=%d)",
                        mac, node_id, stddev, len(rssi_list),
                    )
                    break

        return new_anchors

    def get_anchor(self, mac: str) -> Optional[AnchorInfo]:
        return self.anchors.get(mac.lower())

    def get_all_anchors(self) -> list[dict]:
        return [a.to_dict() for a in self.anchors.values()]

    def get_anchor_positions(
        self,
        origin_lat: float,
        origin_lng: float,
    ) -> dict[str, tuple[float, float]]:
        """Returns mac -> (x_local, y_local) for anchors that have GPS coordinates."""
        result: dict[str, tuple[float, float]] = {}
        for mac, anchor in self.anchors.items():
            if anchor.lat is not None and anchor.lng is not None:
                x, y = gps_to_local(anchor.lat, anchor.lng, origin_lat, origin_lng)
                result[mac] = (x, y)
        return result
