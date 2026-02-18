import logging
from collections import defaultdict
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class DeviceClassifier:
    """Classifies devices by type using OUI, BLE data, and behavior."""

    CATEGORIES: Dict[str, List[str]] = {
        "phone": ["Apple", "Samsung", "Google", "Motorola", "OnePlus", "Xiaomi", "Huawei", "LG"],
        "wearable": [],
        "laptop": ["Intel", "Dell", "HP", "Lenovo", "Framework", "ASUSTek"],
        "iot": ["Espressif", "Tuya", "Amazon", "Wyze"],
        "vehicle": [],
        "network": ["Ubiquiti", "Cisco", "TP-Link", "Netgear"],
        "tracker": ["Tile", "Apple"],
    }

    BLE_SERVICE_HINTS: Dict[int, str] = {
        0x180D: "wearable",   # Heart Rate Service
        0x180F: "wearable",   # Battery Service (common on wearables)
        0x1812: "wearable",   # HID (smartwatch)
        0xFE2C: "tracker",    # Tile
        0xFD6F: "phone",      # COVID exposure notification
    }

    AMAZON_MFG_ID: int = 0x0171

    def __init__(self, oui_lookup: Callable[[str], str]):
        """
        Args:
            oui_lookup: callable that maps a MAC address string to a vendor
                        name (e.g. ``oui_lookup.lookup``).
        """
        self._oui = oui_lookup
        self._device_cache: Dict[str, dict] = {}
        self._vendor_to_category: Dict[str, str] = {}
        self._build_vendor_index()

    def classify(
        self,
        mac: str,
        oui: Optional[str] = None,
        ble_service_uuids: Optional[List[int]] = None,
        manufacturer_data: Optional[Dict[int, bytes]] = None,
        avg_speed: Optional[float] = None,
        probe_ssids: Optional[List[str]] = None,
        ble_name: Optional[str] = None,
    ) -> dict:
        """Classify a device based on available data.

        Returns dict with keys: category, confidence, details.
        """
        if mac in self._device_cache:
            return self._device_cache[mac]

        category: Optional[str] = None
        confidence = 0.0
        details: List[str] = []

        # 1. BLE service UUIDs — most specific signal
        if ble_service_uuids:
            for uuid in ble_service_uuids:
                hint = self.BLE_SERVICE_HINTS.get(uuid)
                if hint:
                    category = hint
                    confidence = 0.9
                    details.append(f"BLE service 0x{uuid:04X} -> {hint}")
                    break

        # 2. BLE manufacturer data (Amazon Sidewalk detection)
        if category is None and manufacturer_data:
            if self.AMAZON_MFG_ID in manufacturer_data:
                category = "iot"
                confidence = 0.85
                details.append("Amazon Sidewalk manufacturer ID detected")

        # 3. BLE device name heuristics
        if category is None and ble_name:
            category, conf, detail = self._classify_by_name(ble_name)
            if category:
                confidence = conf
                details.append(detail)

        # 4. Speed-based classification
        if category is None and avg_speed is not None:
            if avg_speed > 5.0:
                category = "vehicle"
                confidence = 0.8
                details.append(f"avg speed {avg_speed:.1f} m/s suggests vehicle")
            elif avg_speed > 1.0:
                details.append(f"avg speed {avg_speed:.1f} m/s suggests pedestrian")

        # 5. Probe SSID count (many unique SSIDs = likely phone)
        if category is None and probe_ssids:
            if len(probe_ssids) >= 3:
                category = "phone"
                confidence = 0.6
                details.append(f"{len(probe_ssids)} probe SSIDs suggest phone")

        # 6. OUI vendor matching
        if category is None:
            vendor = oui or self._oui(mac)
            cat = self._vendor_to_category.get(vendor)
            if cat:
                category = cat
                confidence = max(confidence, 0.5)
                details.append(f"OUI vendor '{vendor}' -> {cat}")
            else:
                details.append(f"OUI vendor '{vendor}' has no category mapping")

        if category is None:
            category = "unknown"
            confidence = 0.0

        result = {
            "category": category,
            "confidence": round(confidence, 3),
            "details": details,
        }
        self._device_cache[mac] = result
        return result

    def classify_by_behavior(
        self,
        mac: str,
        avg_speed: float,
        visit_duration: float,
        appearance_count: int,
    ) -> dict:
        """Refine classification based on observed behavior.

        Heuristics:
            - High speed (>5 m/s), short duration -> vehicle
            - Walking speed (1–5 m/s), moderate duration -> pedestrian (phone)
            - Stationary (<1 m/s), long duration (>30 min) -> resident / worker
            - Very regular timing (high appearance count) -> commuter
        """
        category: Optional[str] = None
        confidence = 0.0
        details: List[str] = []

        if avg_speed > 5.0 and visit_duration < 120:
            category = "vehicle"
            confidence = 0.85
            details.append("high speed + short duration")
        elif 1.0 < avg_speed <= 5.0:
            category = "phone"
            confidence = 0.6
            details.append("walking speed suggests pedestrian with phone")
        elif avg_speed <= 1.0 and visit_duration > 1800:
            category = "phone"
            confidence = 0.55
            details.append("stationary + long duration suggests resident/worker")

        if appearance_count >= 10:
            confidence = min(confidence + 0.1, 1.0)
            details.append(f"{appearance_count} appearances suggest commuter")

        result = {
            "category": category or "unknown",
            "confidence": round(confidence, 3),
            "details": details,
        }

        existing = self._device_cache.get(mac)
        if existing is None or confidence > existing["confidence"]:
            self._device_cache[mac] = result

        return result

    def get_classification(self, mac: str) -> Optional[dict]:
        """Get cached classification for a device, or ``None``."""
        return self._device_cache.get(mac)

    def get_statistics(self) -> Dict[str, int]:
        """Get classification statistics: count per category."""
        counts: Dict[str, int] = defaultdict(int)
        for entry in self._device_cache.values():
            counts[entry["category"]] += 1
        return dict(counts)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_vendor_index(self) -> None:
        """Build a reverse map from vendor name to primary category."""
        for cat, vendors in self.CATEGORIES.items():
            for vendor in vendors:
                if vendor not in self._vendor_to_category:
                    self._vendor_to_category[vendor] = cat

        # Partial-match aliases for OUI strings that contain extra text
        self._vendor_aliases: Dict[str, str] = {
            "Chongqing Fugui": "phone",
            "Raspberry Pi": "iot",
            "Realtek": "laptop",
            "Synology": "network",
            "Microsoft": "laptop",
            "GIGA-BYTE": "laptop",
            "ASRock": "laptop",
        }

    def _classify_by_name(self, name: str) -> tuple:
        """Try to classify by BLE advertised name."""
        lower = name.lower()

        wearable_keywords = ["watch", "band", "fitbit", "garmin", "mi band", "galaxy fit"]
        for kw in wearable_keywords:
            if kw in lower:
                return "wearable", 0.8, f"BLE name '{name}' matches wearable keyword"

        tracker_keywords = ["airtag", "tile", "smarttag", "chipolo"]
        for kw in tracker_keywords:
            if kw in lower:
                return "tracker", 0.85, f"BLE name '{name}' matches tracker keyword"

        phone_keywords = ["iphone", "galaxy", "pixel", "oneplus", "moto"]
        for kw in phone_keywords:
            if kw in lower:
                return "phone", 0.7, f"BLE name '{name}' matches phone keyword"

        return None, 0.0, ""

    def _resolve_vendor(self, mac: str, oui: Optional[str] = None) -> Optional[str]:
        """Resolve vendor name, checking exact and alias matches."""
        vendor = oui or self._oui(mac)
        if vendor in self._vendor_to_category:
            return vendor
        for alias, cat in self._vendor_aliases.items():
            if alias in vendor:
                self._vendor_to_category[vendor] = cat
                return vendor
        return vendor
