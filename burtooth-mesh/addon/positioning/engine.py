import asyncio
import hashlib
import json
import logging
import time
from collections import defaultdict
from typing import Any, Optional

from .trilateration import Trilateration
from .kalman import KalmanTracker
from .anchor_manager import AnchorManager
from .path_recorder import PathRecorder
from .calibration import AutoCalibrator

logger = logging.getLogger(__name__)

OBSERVATION_WINDOW_MS = 100
PRUNE_INTERVAL_S = 60
PATH_CLOSE_INTERVAL_S = 30
STABILITY_ANALYSIS_INTERVAL_S = 3600


class PositioningEngine:
    """Central positioning coordinator.

    Subscribes to burtooth/scan/# MQTT topics, groups observations by MAC
    within time windows, runs the 3D trilateration + Kalman pipeline, and
    publishes results.

    Operates in relative (x, y, z) meter coordinates by default.  GPS-based
    mode is still supported when an ``origin`` with lat/lng is provided.
    """

    def __init__(self, ha_api=None, config: Optional[dict] = None):
        config = config or {}
        self._ha_api = ha_api

        origin = config.get("origin") or {}
        self._origin_lat = origin.get("lat")
        self._origin_lng = origin.get("lng")
        self._relative_mode = self._origin_lat is None or self._origin_lng is None

        # node_id -> (x, y, z) in local meters
        self._node_positions: dict[str, tuple[float, float, float]] = {}
        self._load_node_positions(config)

        self._trilateration = Trilateration()
        self._kalman = KalmanTracker()
        self._anchor_manager = AnchorManager()
        self._path_recorder = PathRecorder()
        self._calibrator = AutoCalibrator(
            ha_api,
            self._origin_lat or 0.0,
            self._origin_lng or 0.0,
        )

        # Observation buffer: mac -> list of observations
        self._observations: dict[str, list[dict]] = defaultdict(list)

        self._mqtt = None
        self._running = False
        self._tasks: list[asyncio.Task] = []

    def _load_node_positions(self, config: dict) -> None:
        """Extract scanner node positions from config.

        Accepts nodes with explicit (x, y, z) coordinates first.  Falls back
        to (lat, lng) -> local conversion when an origin is configured.
        """
        for node in config.get("nodes", []):
            node_id = node.get("id") or node.get("node_id")
            if not node_id:
                continue

            # Prefer explicit local coordinates
            if node.get("x") is not None and node.get("y") is not None:
                z = node.get("z", 0.0)
                self._node_positions[node_id] = (
                    float(node["x"]),
                    float(node["y"]),
                    float(z),
                )
                continue

            # Fall back to GPS conversion when origin is available
            lat = node.get("lat")
            lng = node.get("lng")
            if (
                lat is not None
                and lng is not None
                and not self._relative_mode
            ):
                from addon.utils.geo import gps_to_local
                x, y = gps_to_local(lat, lng, self._origin_lat, self._origin_lng)
                z = node.get("z", 0.0)
                self._node_positions[node_id] = (x, y, float(z))

    async def start(self, mqtt_client) -> None:
        self._mqtt = mqtt_client
        self._running = True

        mqtt_client.register_handler("burtooth/scan/#", self._handle_scan)

        await self._anchor_manager.load()
        if self._ha_api and not self._relative_mode:
            await self._anchor_manager.seed_from_ha(
                self._ha_api, self._origin_lat, self._origin_lng
            )

        await self._path_recorder.start()
        await self._calibrator.start()

        self._apply_calibration()

        self._tasks = [
            asyncio.create_task(self._process_loop()),
            asyncio.create_task(self._maintenance_loop()),
        ]

        logger.info(
            "Positioning engine started (relative_mode=%s, %d nodes configured)",
            self._relative_mode, len(self._node_positions),
        )

    async def stop(self) -> None:
        self._running = False
        await self._calibrator.stop()

        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()

        completed = self._path_recorder.close_stale_paths(max_gap_s=0)
        for path in completed:
            await self._path_recorder.save_path(path)

        await self._anchor_manager.save()
        logger.info("Positioning engine stopped")

    async def _handle_scan(self, topic: str, payload: Any) -> None:
        if not self._running:
            return

        try:
            data = payload if isinstance(payload, dict) else json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return

        mac = data.get("mac", "").lower()
        if not mac:
            return

        node_id = data.get("node_id") or _node_id_from_topic(topic)
        rssi = data.get("rssi")
        if node_id is None or rssi is None:
            return

        observation = {
            "node_id": node_id,
            "rssi": float(rssi),
            "timestamp": data.get("timestamp", time.time()),
            "type": data.get("type", "ble"),
            "name": data.get("name"),
            "tx_power": data.get("tx_power"),
        }

        self._observations[mac].append(observation)

        self._calibrator.update_scan_cache(mac, node_id, float(rssi))
        self._anchor_manager.record_observation(mac, node_id, float(rssi))

    async def _process_loop(self) -> None:
        """Process mature observation windows every 100ms."""
        while self._running:
            try:
                now = time.time()
                window_cutoff = now - (OBSERVATION_WINDOW_MS / 1000.0)

                mature_macs = []
                for mac, obs_list in self._observations.items():
                    if obs_list and obs_list[0]["timestamp"] <= window_cutoff:
                        mature_macs.append(mac)

                for mac in mature_macs:
                    obs_list = self._observations.pop(mac, [])
                    if not obs_list:
                        continue
                    await self._process_observations(mac, obs_list)

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error in process loop")

            await asyncio.sleep(OBSERVATION_WINDOW_MS / 1000.0)

    async def _process_observations(self, mac: str, observations: list[dict]) -> None:
        """Run the full 3D position pipeline on a group of observations."""
        by_node: dict[str, dict] = {}
        for obs in observations:
            nid = obs["node_id"]
            if nid not in by_node or obs["rssi"] > by_node[nid]["rssi"]:
                by_node[nid] = obs

        unique_obs = list(by_node.values())
        signal_types = list({o.get("type", "ble") for o in unique_obs})

        tri_input = [(o["node_id"], o["rssi"]) for o in unique_obs]

        known_nodes = {nid for nid, _ in tri_input if nid in self._node_positions}
        if len(known_nodes) < 2:
            return

        # Use advertised TX power if consistently reported
        adv_tx = None
        tx_powers = [o["tx_power"] for o in unique_obs if o.get("tx_power") is not None]
        if tx_powers:
            adv_tx = sum(tx_powers) / len(tx_powers)

        position = self._trilateration.estimate_position(
            tri_input, self._node_positions, adv_tx_power=adv_tx,
        )
        if position is None:
            return

        x, y, z, accuracy = position
        timestamp = max(o["timestamp"] for o in unique_obs)

        sx, sy, sz, svx, svy, svz = self._kalman.update(mac, x, y, z, accuracy, timestamp)

        self._path_recorder.record_point(mac, sx, sy, sz, svx, svy, svz, timestamp, signal_types)

        await self._publish_position(
            mac, sx, sy, sz, svx, svy, svz, accuracy, timestamp, signal_types,
        )

    async def _publish_position(
        self,
        mac: str,
        x: float,
        y: float,
        z: float,
        vx: float,
        vy: float,
        vz: float,
        accuracy: float,
        timestamp: float,
        signal_types: list[str],
    ) -> None:
        if not self._mqtt or not self._mqtt.connected:
            return

        mac_hash = hashlib.sha256(mac.encode()).hexdigest()[:12]

        payload: dict[str, Any] = {
            "mac_hash": mac_hash,
            "x": round(x, 3),
            "y": round(y, 3),
            "z": round(z, 3),
            "vx": round(vx, 3),
            "vy": round(vy, 3),
            "vz": round(vz, 3),
            "accuracy_m": round(accuracy, 2),
            "timestamp": timestamp,
            "signal_types": signal_types,
        }

        if not self._relative_mode:
            from addon.utils.geo import local_to_gps
            lat, lng = local_to_gps(x, y, self._origin_lat, self._origin_lng)
            payload["lat"] = round(lat, 7)
            payload["lng"] = round(lng, 7)

        topic = f"burtooth/position/{mac_hash}"
        await self._mqtt.publish(topic, payload)

    async def _maintenance_loop(self) -> None:
        """Periodic maintenance: prune stale trackers, close paths, run stability analysis."""
        last_prune = time.time()
        last_path_close = time.time()
        last_stability = time.time()
        last_calibration_apply = time.time()

        while self._running:
            try:
                now = time.time()

                if now - last_prune >= PRUNE_INTERVAL_S:
                    self._kalman.prune_stale()
                    last_prune = now

                if now - last_path_close >= PATH_CLOSE_INTERVAL_S:
                    completed = self._path_recorder.close_stale_paths()
                    for path in completed:
                        await self._path_recorder.save_path(path)
                    last_path_close = now

                if now - last_stability >= STABILITY_ANALYSIS_INTERVAL_S:
                    new_anchors = self._anchor_manager.analyze_stability()
                    if new_anchors:
                        await self._anchor_manager.save()
                    last_stability = now

                if now - last_calibration_apply >= 300:
                    self._apply_calibration()
                    last_calibration_apply = now

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error in maintenance loop")

            await asyncio.sleep(5.0)

    def _apply_calibration(self) -> None:
        """Push calibration data from the auto-calibrator into trilateration."""
        cal_data = self._calibrator.get_calibration()
        for node_id, params in cal_data.items():
            tx_power = params.get("tx_power")
            n = params.get("n")
            if tx_power is not None and n is not None:
                self._trilateration.set_calibration(node_id, tx_power, n)

    async def get_positions(self) -> list[dict]:
        """Returns current tracked device positions for the API."""
        states = self._kalman.get_all_states()
        positions = []
        for mac, state in states.items():
            if state is None:
                continue
            mac_hash = hashlib.sha256(mac.encode()).hexdigest()[:12]
            entry: dict[str, Any] = {
                "mac_hash": mac_hash,
                "x": round(state["x"], 3),
                "y": round(state["y"], 3),
                "z": round(state["z"], 3),
                "vx": round(state["vx"], 3),
                "vy": round(state["vy"], 3),
                "vz": round(state["vz"], 3),
                "last_seen": state["last_seen"],
            }

            if not self._relative_mode:
                from addon.utils.geo import local_to_gps
                lat, lng = local_to_gps(
                    state["x"], state["y"],
                    self._origin_lat, self._origin_lng,
                )
                entry["lat"] = round(lat, 7)
                entry["lng"] = round(lng, 7)

            positions.append(entry)
        return positions

    async def get_paths(
        self,
        mac_hash: Optional[str] = None,
        since: Optional[float] = None,
    ) -> list[dict]:
        return await self._path_recorder.get_paths(mac_hash=mac_hash, since=since)

    def get_calibration_status(self) -> dict:
        return self._calibrator.get_status()

    def get_anchors(self) -> list[dict]:
        return self._anchor_manager.get_all_anchors()


def _node_id_from_topic(topic: str) -> Optional[str]:
    """Extract node_id from topic like burtooth/scan/{node_id}."""
    parts = topic.split("/")
    return parts[2] if len(parts) >= 3 else None
