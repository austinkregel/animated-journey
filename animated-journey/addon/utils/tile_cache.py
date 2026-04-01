import asyncio
import hashlib
import logging
import os
from pathlib import Path

import aiohttp

logger = logging.getLogger(__name__)

TILE_DIR = Path("/data/tiles")
ESRI_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
MAX_CONCURRENT = 4


class TileCache:
    """Caches Esri satellite tiles to local disk for offline use."""

    def __init__(self, cache_dir: Path = TILE_DIR):
        self._dir = cache_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    def _tile_path(self, z: int, y: int, x: int) -> Path:
        return self._dir / str(z) / str(y) / f"{x}.jpg"

    def has_tile(self, z: int, y: int, x: int) -> bool:
        return self._tile_path(z, y, x).exists()

    def get_tile(self, z: int, y: int, x: int) -> bytes | None:
        path = self._tile_path(z, y, x)
        if path.exists():
            return path.read_bytes()
        return None

    async def fetch_and_cache(self, z: int, y: int, x: int) -> bytes | None:
        """Fetch a tile from Esri and cache it locally."""
        path = self._tile_path(z, y, x)
        if path.exists():
            return path.read_bytes()

        url = ESRI_URL.format(z=z, y=y, x=x)
        async with self._semaphore:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status != 200:
                            logger.warning("Tile fetch failed %d/%d/%d: HTTP %d", z, y, x, resp.status)
                            return None
                        data = await resp.read()
            except Exception:
                logger.exception("Tile fetch error %d/%d/%d", z, y, x)
                return None

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return data

    async def precache_area(self, lat: float, lng: float, zoom_levels: range = range(17, 21)) -> int:
        """Pre-cache tiles around a GPS coordinate for multiple zoom levels."""
        import math

        cached = 0
        for z in zoom_levels:
            n = 2 ** z
            x_center = int((lng + 180) / 360 * n)
            lat_rad = math.radians(lat)
            y_center = int((1 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2 * n)

            radius = 3 if z < 19 else 5
            tasks = []
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    tx, ty = x_center + dx, y_center + dy
                    if not self.has_tile(z, ty, tx):
                        tasks.append(self.fetch_and_cache(z, ty, tx))

            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                cached += sum(1 for r in results if isinstance(r, bytes))

        logger.info("Pre-cached %d tiles around %.5f, %.5f", cached, lat, lng)
        return cached

    def cache_stats(self) -> dict:
        """Return cache statistics."""
        total_files = 0
        total_bytes = 0
        for root, _, files in os.walk(self._dir):
            for f in files:
                total_files += 1
                total_bytes += os.path.getsize(os.path.join(root, f))
        return {
            "tile_count": total_files,
            "size_mb": round(total_bytes / (1024 * 1024), 2),
            "cache_dir": str(self._dir),
        }
