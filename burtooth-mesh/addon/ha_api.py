import logging
import os

import aiohttp

logger = logging.getLogger(__name__)

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
SUPERVISOR_URL = "http://supervisor"

_HEADERS = {
    "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
    "Content-Type": "application/json",
}


async def _get(url: str) -> dict | list | None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=_HEADERS) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("data", data) if isinstance(data, dict) else data
                logger.error("GET %s returned %d: %s", url, resp.status, await resp.text())
    except Exception:
        logger.exception("Failed to reach %s", url)
    return None


async def get_mqtt_config() -> dict | None:
    data = await _get(f"{SUPERVISOR_URL}/services/mqtt")
    if data and isinstance(data, dict):
        return {
            "host": data.get("host", "core-mosquitto"),
            "port": int(data.get("port", 1883)),
            "username": data.get("username", ""),
            "password": data.get("password", ""),
        }
    return None


async def get_states() -> list | None:
    return await _get(f"{SUPERVISOR_URL}/core/api/states")


async def get_config() -> dict | None:
    return await _get(f"{SUPERVISOR_URL}/core/api/config")


async def get_entity_state(entity_id: str) -> dict | None:
    return await _get(f"{SUPERVISOR_URL}/core/api/states/{entity_id}")
