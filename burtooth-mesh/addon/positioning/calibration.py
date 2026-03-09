import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from typing import Optional

import numpy as np

from addon.utils.geo import gps_to_local

logger = logging.getLogger(__name__)

CALIBRATION_FILE = "/data/calibration.json"

# GPS entity patterns to poll for ground-truth positions
_GPS_ENTITY_PREFIXES = (
    "device_tracker.motorola_razr",
    "device_tracker.life360_",
)


class AutoCalibrator:
    """Auto-calibration using GPS ground truth from mobile devices.

    Correlates known GPS positions with RSSI readings to fit per-node
    log-distance path loss parameters (tx_power and n).
    """

    def __init__(self, ha_api, origin_lat: float, origin_lng: float):
        self._ha_api = ha_api
        self._origin = (origin_lat, origin_lng)
        self._samples: dict[str, list[tuple[float, float]]] = defaultdict(list)
        self._calibration: dict[str, dict] = {}
        self._scan_cache: dict[str, list[dict]] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        await self.load()
        self._running = True
        self._task = asyncio.create_task(self._poll_gps_sources())
        logger.info("Auto-calibrator started with %d existing calibrations", len(self._calibration))

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Auto-calibrator stopped")

    async def load(self) -> None:
        if not os.path.exists(CALIBRATION_FILE):
            return
        try:
            with open(CALIBRATION_FILE, "r") as f:
                data = json.load(f)
            self._calibration = data.get("calibration", {})
            # Restore samples if saved
            raw_samples = data.get("samples", {})
            for node_id, pairs in raw_samples.items():
                self._samples[node_id] = [(d, r) for d, r in pairs]
            logger.info("Loaded calibration for %d nodes", len(self._calibration))
        except Exception:
            logger.exception("Failed to load calibration from %s", CALIBRATION_FILE)

    async def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(CALIBRATION_FILE), exist_ok=True)
            data = {
                "calibration": self._calibration,
                "samples": {
                    node_id: pairs[-200:]
                    for node_id, pairs in self._samples.items()
                },
                "last_updated": time.time(),
            }
            with open(CALIBRATION_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            logger.exception("Failed to save calibration to %s", CALIBRATION_FILE)

    def update_scan_cache(self, mac: str, node_id: str, rssi: float) -> None:
        """Called by the engine to feed recent scan data for GPS correlation."""
        mac = mac.lower()
        entry = {"node_id": node_id, "rssi": rssi, "timestamp": time.time()}
        if mac not in self._scan_cache:
            self._scan_cache[mac] = []
        self._scan_cache[mac].append(entry)
        # Keep only recent observations (last 30 seconds)
        cutoff = time.time() - 30.0
        self._scan_cache[mac] = [
            e for e in self._scan_cache[mac] if e["timestamp"] > cutoff
        ]

    async def _poll_gps_sources(self) -> None:
        """Periodically poll HA for GPS device positions and correlate with RSSI."""
        while self._running:
            try:
                await self._collect_gps_samples()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error in GPS polling cycle")
            await asyncio.sleep(10.0)

    async def _collect_gps_samples(self) -> None:
        states = await self._ha_api.get_states()
        if not states:
            return

        for state in states:
            entity_id = state.get("entity_id", "")
            if not any(entity_id.startswith(p) for p in _GPS_ENTITY_PREFIXES):
                continue

            attrs = state.get("attributes", {})
            lat = attrs.get("latitude")
            lng = attrs.get("longitude")
            gps_accuracy = attrs.get("gps_accuracy", 100)

            if lat is None or lng is None:
                continue
            if gps_accuracy > 50:
                continue

            gps_x, gps_y = gps_to_local(lat, lng, self._origin[0], self._origin[1])

            # Look for any MAC associated with this GPS device in the scan cache
            device_mac = self._find_device_mac(entity_id, attrs)
            if not device_mac or device_mac not in self._scan_cache:
                continue

            recent_scans = self._scan_cache.get(device_mac, [])
            cutoff = time.time() - 15.0
            relevant = [s for s in recent_scans if s["timestamp"] > cutoff]

            for scan in relevant:
                node_id = scan["node_id"]
                rssi = scan["rssi"]
                # We need the node's position to compute distance
                # Distance from GPS position to scanner node is what we're calibrating
                # The node positions come from config — we'll use the scan data to
                # record (distance, rssi) pairs once we know node positions
                self.add_sample(node_id, 0.0, rssi)

        # Periodically refit and save
        if any(len(s) >= 10 for s in self._samples.values()):
            self.fit_all()
            await self.save()

    def _find_device_mac(self, entity_id: str, attrs: dict) -> Optional[str]:
        """Extract a BLE/WiFi MAC from a GPS device's HA attributes."""
        for key in ("mac", "mac_address", "ble_mac", "wifi_mac"):
            mac = attrs.get(key, "")
            if mac:
                return mac.lower().replace("-", ":")
        return None

    def add_sample(self, node_id: str, distance_m: float, rssi: float) -> None:
        """Add a calibration sample. Keeps a rolling window of 200 per node."""
        if distance_m < 0.1:
            return
        self._samples[node_id].append((distance_m, rssi))
        if len(self._samples[node_id]) > 200:
            self._samples[node_id] = self._samples[node_id][-200:]

    def add_sample_with_node_pos(
        self,
        node_id: str,
        node_x: float,
        node_y: float,
        device_x: float,
        device_y: float,
        rssi: float,
        node_z: float = 0.0,
        device_z: float = 0.0,
    ) -> None:
        """Add a calibration sample computing 3D distance from known positions."""
        import math
        distance = math.sqrt(
            (device_x - node_x) ** 2
            + (device_y - node_y) ** 2
            + (device_z - node_z) ** 2
        )
        self.add_sample(node_id, distance, rssi)

    def fit_model(self, node_id: str) -> Optional[dict]:
        """Fit log-distance path loss model to samples for a single node.

        Model: RSSI = tx_power - 10 * n * log10(d)
        Linear regression on: RSSI = intercept + slope * 10*log10(d)
            where slope = -n, intercept = tx_power
        """
        samples = self._samples.get(node_id, [])
        if len(samples) < 10:
            return None

        distances = np.array([s[0] for s in samples])
        rssis = np.array([s[1] for s in samples])

        # Filter out zero/negative distances
        valid = distances > 0.1
        if np.sum(valid) < 10:
            return None
        distances = distances[valid]
        rssis = rssis[valid]

        # x = 10 * log10(distance)
        x = 10.0 * np.log10(distances)

        # Linear regression: RSSI = tx_power + (-n) * x
        A = np.vstack([x, np.ones(len(x))]).T
        try:
            result, residuals, _, _ = np.linalg.lstsq(A, rssis, rcond=None)
        except np.linalg.LinAlgError:
            logger.warning("Calibration fit failed for node %s", node_id)
            return None

        slope, intercept = float(result[0]), float(result[1])
        n = -slope
        tx_power = intercept

        # Sanity bounds
        if n < 1.0 or n > 6.0:
            logger.warning("Node %s calibration n=%.2f out of range, clamping", node_id, n)
            n = max(1.5, min(n, 5.0))
        if tx_power < -90 or tx_power > -20:
            logger.warning("Node %s calibration tx_power=%.1f out of range", node_id, tx_power)
            return None

        # R-squared
        ss_res = np.sum((rssis - (intercept + slope * x)) ** 2)
        ss_tot = np.sum((rssis - np.mean(rssis)) ** 2)
        r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

        cal = {
            "tx_power": round(tx_power, 1),
            "n": round(n, 3),
            "r_squared": round(r_squared, 4),
            "sample_count": len(distances),
            "last_update": time.time(),
        }
        self._calibration[node_id] = cal
        return cal

    def fit_all(self) -> dict[str, dict]:
        """Fit model for all nodes with sufficient samples."""
        results: dict[str, dict] = {}
        for node_id in list(self._samples.keys()):
            if len(self._samples[node_id]) >= 10:
                cal = self.fit_model(node_id)
                if cal:
                    results[node_id] = cal
        if results:
            logger.info("Calibrated %d nodes: %s", len(results), list(results.keys()))
        return results

    def get_calibration(self, node_id: Optional[str] = None) -> dict:
        if node_id:
            return self._calibration.get(node_id, {})
        return dict(self._calibration)

    def get_status(self) -> dict:
        return {
            "nodes": {
                node_id: {
                    **cal,
                    "samples_available": len(self._samples.get(node_id, [])),
                }
                for node_id, cal in self._calibration.items()
            },
            "total_samples": sum(len(s) for s in self._samples.values()),
            "nodes_with_samples": len(self._samples),
        }
