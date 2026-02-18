import asyncio
import hashlib
import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from addon.utils.geo import local_to_gps

logger = logging.getLogger(__name__)

PATHS_DIR = "/data/paths"


@dataclass
class PathPoint:
    lat: float
    lng: float
    x: float
    y: float
    vx: float
    vy: float
    timestamp: float
    signal_types: list[str] = field(default_factory=list)


@dataclass
class PathSession:
    mac: str
    mac_hash: str
    start_time: float
    points: list[PathPoint] = field(default_factory=list)
    closed: bool = False

    @property
    def end_time(self) -> float:
        return self.points[-1].timestamp if self.points else self.start_time

    @property
    def duration_s(self) -> float:
        return self.end_time - self.start_time

    @property
    def point_count(self) -> int:
        return len(self.points)

    @property
    def total_distance_m(self) -> float:
        if len(self.points) < 2:
            return 0.0
        total = 0.0
        for i in range(1, len(self.points)):
            dx = self.points[i].x - self.points[i - 1].x
            dy = self.points[i].y - self.points[i - 1].y
            total += math.sqrt(dx * dx + dy * dy)
        return total

    @property
    def avg_speed_mps(self) -> float:
        dur = self.duration_s
        return self.total_distance_m / dur if dur > 0 else 0.0

    @property
    def entry_bearing(self) -> Optional[float]:
        if len(self.points) < 2:
            return None
        p0, p1 = self.points[0], self.points[1]
        return math.degrees(math.atan2(p1.x - p0.x, p1.y - p0.y)) % 360

    @property
    def exit_bearing(self) -> Optional[float]:
        if len(self.points) < 2:
            return None
        p0, p1 = self.points[-2], self.points[-1]
        return math.degrees(math.atan2(p1.x - p0.x, p1.y - p0.y)) % 360

    @property
    def path_id(self) -> str:
        return f"{self.mac_hash}_{int(self.start_time)}"

    def to_summary(self) -> dict:
        return {
            "path_id": self.path_id,
            "mac_hash": self.mac_hash,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_s": round(self.duration_s, 1),
            "point_count": self.point_count,
            "distance_m": round(self.total_distance_m, 2),
            "avg_speed_mps": round(self.avg_speed_mps, 2),
            "entry_bearing": round(self.entry_bearing, 1) if self.entry_bearing is not None else None,
            "exit_bearing": round(self.exit_bearing, 1) if self.exit_bearing is not None else None,
        }

    def to_detail(self) -> dict:
        summary = self.to_summary()
        summary["points"] = [
            {
                "lat": p.lat,
                "lng": p.lng,
                "vx": round(p.vx, 3),
                "vy": round(p.vy, 3),
                "timestamp": p.timestamp,
                "signal_types": p.signal_types,
            }
            for p in self.points
        ]
        return summary


def _mac_hash(mac: str) -> str:
    return hashlib.sha256(mac.lower().encode()).hexdigest()[:12]


class PathRecorder:
    """Records and stores travel paths for tracked devices."""

    def __init__(self, origin_lat: float, origin_lng: float):
        self._active_paths: dict[str, PathSession] = {}
        self._origin = (origin_lat, origin_lng)

    async def start(self) -> None:
        os.makedirs(PATHS_DIR, exist_ok=True)
        logger.info("Path recorder started, storing to %s", PATHS_DIR)

    def record_point(
        self,
        mac: str,
        x: float,
        y: float,
        vx: float,
        vy: float,
        timestamp: float,
        signal_types: Optional[list[str]] = None,
    ) -> None:
        mac_lower = mac.lower()
        lat, lng = local_to_gps(x, y, self._origin[0], self._origin[1])

        point = PathPoint(
            lat=lat,
            lng=lng,
            x=x,
            y=y,
            vx=vx,
            vy=vy,
            timestamp=timestamp,
            signal_types=signal_types or [],
        )

        session = self._active_paths.get(mac_lower)
        if session is None:
            session = PathSession(
                mac=mac_lower,
                mac_hash=_mac_hash(mac_lower),
                start_time=timestamp,
            )
            self._active_paths[mac_lower] = session

        session.points.append(point)

    def close_stale_paths(self, max_gap_s: float = 120.0) -> list[PathSession]:
        """Close paths where the last point was recorded more than max_gap_s ago."""
        now = time.time()
        completed: list[PathSession] = []

        stale_macs = [
            mac for mac, session in self._active_paths.items()
            if session.points and (now - session.points[-1].timestamp) > max_gap_s
        ]

        for mac in stale_macs:
            session = self._active_paths.pop(mac)
            session.closed = True
            if session.point_count >= 2:
                completed.append(session)
            else:
                logger.debug("Discarding single-point path for %s", session.mac_hash)

        return completed

    async def save_path(self, path_session: PathSession) -> None:
        filename = f"{path_session.path_id}.json"
        filepath = os.path.join(PATHS_DIR, filename)
        try:
            data = path_session.to_detail()
            with open(filepath, "w") as f:
                json.dump(data, f)
            logger.info(
                "Saved path %s: %d points, %.1fm, %.0fs",
                path_session.path_id,
                path_session.point_count,
                path_session.total_distance_m,
                path_session.duration_s,
            )
        except Exception:
            logger.exception("Failed to save path %s", path_session.path_id)

    async def get_paths(
        self,
        mac_hash: Optional[str] = None,
        since: Optional[float] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Load saved path summaries from disk, optionally filtered."""
        results: list[dict] = []
        if not os.path.isdir(PATHS_DIR):
            return results

        try:
            files = sorted(os.listdir(PATHS_DIR), reverse=True)
        except OSError:
            logger.exception("Failed to list paths directory")
            return results

        for filename in files:
            if not filename.endswith(".json"):
                continue

            if mac_hash and not filename.startswith(mac_hash):
                continue

            filepath = os.path.join(PATHS_DIR, filename)
            try:
                with open(filepath, "r") as f:
                    data = json.load(f)

                if since and data.get("start_time", 0) < since:
                    continue

                # Return summary without full point data
                summary = {k: v for k, v in data.items() if k != "points"}
                summary["point_count"] = len(data.get("points", []))
                results.append(summary)

                if len(results) >= limit:
                    break
            except Exception:
                logger.debug("Skipping malformed path file %s", filename)

        return results

    async def get_path_detail(self, path_id: str) -> Optional[dict]:
        """Load full path detail including all GPS points."""
        filename = f"{path_id}.json"
        filepath = os.path.join(PATHS_DIR, filename)

        if not os.path.exists(filepath):
            return None

        try:
            with open(filepath, "r") as f:
                return json.load(f)
        except Exception:
            logger.exception("Failed to load path detail for %s", path_id)
            return None
