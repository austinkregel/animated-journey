import logging
from collections import defaultdict
from typing import Dict, List, Tuple

import numpy as np

from addon.utils.geo import local_to_gps

logger = logging.getLogger(__name__)

_COMPASS_DIRECTIONS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


class RouteAnalyzer:
    """Identifies common travel routes across the property."""

    def __init__(self, grid_resolution_m: float = 2.0):
        self._grid_res = grid_resolution_m
        self._route_counts: Dict[Tuple[int, int], int] = defaultdict(int)
        self._entry_exit: Dict[Tuple[str, str], int] = defaultdict(int)

    def record_path(self, points: List[Tuple[float, float]]) -> None:
        """Record a travel path as a list of (x, y) local-meter coordinates.

        Each coordinate pair is discretised to a grid cell and the
        corresponding heatmap counter is incremented.
        """
        if not points:
            return

        visited: set = set()
        for x, y in points:
            cell = (int(round(x / self._grid_res)), int(round(y / self._grid_res)))
            if cell not in visited:
                self._route_counts[cell] += 1
                visited.add(cell)

    def record_entry_exit(self, entry_bearing: float, exit_bearing: float) -> None:
        """Record entry and exit bearings (degrees) for a single path.

        Bearings are binned to the eight principal compass directions before
        being stored.
        """
        entry_dir = self.bearing_to_compass(entry_bearing)
        exit_dir = self.bearing_to_compass(exit_bearing)
        self._entry_exit[(entry_dir, exit_dir)] += 1

    def get_heatmap(self, origin_lat: float, origin_lng: float) -> List[dict]:
        """Return a heatmap of most-traveled grid cells.

        Each entry contains: lat, lng, count, intensity (0.0–1.0).
        """
        if not self._route_counts:
            return []

        max_count = max(self._route_counts.values())
        heatmap: List[dict] = []

        for (cx, cy), count in self._route_counts.items():
            x_m = cx * self._grid_res
            y_m = cy * self._grid_res
            lat, lng = local_to_gps(x_m, y_m, origin_lat, origin_lng)
            heatmap.append({
                "lat": round(lat, 7),
                "lng": round(lng, 7),
                "count": count,
                "intensity": round(count / max_count, 3) if max_count else 0.0,
            })

        heatmap.sort(key=lambda h: h["count"], reverse=True)
        return heatmap

    def get_common_routes(self, min_count: int = 3) -> List[dict]:
        """Identify the most common entry-to-exit patterns.

        Only routes with at least *min_count* occurrences are returned.
        Each result contains: entry_direction, exit_direction, count, percentage.
        """
        total = sum(self._entry_exit.values())
        if total == 0:
            return []

        routes: List[dict] = []
        for (entry_dir, exit_dir), count in self._entry_exit.items():
            if count < min_count:
                continue
            routes.append({
                "entry_direction": entry_dir,
                "exit_direction": exit_dir,
                "count": count,
                "percentage": round(100.0 * count / total, 1),
            })

        routes.sort(key=lambda r: r["count"], reverse=True)
        return routes

    def get_hotspots(self, top_n: int = 10) -> List[dict]:
        """Return the *top_n* most visited grid cells.

        Each result contains: grid_x, grid_y, count.
        """
        sorted_cells = sorted(self._route_counts.items(), key=lambda c: c[1], reverse=True)
        return [
            {"grid_x": cell[0], "grid_y": cell[1], "count": count}
            for cell, count in sorted_cells[:top_n]
        ]

    @staticmethod
    def bearing_to_compass(bearing: float) -> str:
        """Convert a bearing in degrees to a compass direction.

        Directions: N, NE, E, SE, S, SW, W, NW.
        """
        idx = round(bearing / 45) % 8
        return _COMPASS_DIRECTIONS[idx]
