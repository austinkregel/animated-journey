import asyncio
import json
import logging
import os
import time
from pathlib import Path

from aiohttp import web

from addon import ha_api, mqtt_client
from addon.positioning.engine import PositioningEngine
from addon.analyzer import PatternDetector, DeviceClassifier, RouteAnalyzer, LLMQuery
from addon.utils.oui_lookup import lookup as oui_lookup

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
INGRESS_PATH = os.environ.get("INGRESS_PATH", "")
DATA_DIR = Path("/data")
CONFIG_FILE = DATA_DIR / "config.json"
FIRMWARE_DIR = DATA_DIR / "firmware"
SETTINGS_FILE = DATA_DIR / "settings.json"


def _ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FIRMWARE_DIR.mkdir(parents=True, exist_ok=True)


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {"nodes": [], "origin": {"lat": 42.98880, "lng": -84.18284}}


def _save_config(data: dict):
    CONFIG_FILE.write_text(json.dumps(data, indent=2))


def _load_settings() -> dict:
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text())
    return {
        "mqtt_topic_prefix": "burtooth",
        "scan_interval_ms": 2000,
        "position_update_ms": 1000,
        "active_scanning": False,
        "max_tracked_devices": 500,
    }


def _save_settings(data: dict):
    SETTINGS_FILE.write_text(json.dumps(data, indent=2))


async def handle_get_config(request: web.Request) -> web.Response:
    return web.json_response(_load_config())


async def handle_post_config(request: web.Request) -> web.Response:
    data = await request.json()
    _save_config(data)
    return web.json_response({"status": "ok"})


async def handle_status(request: web.Request) -> web.Response:
    app = request.app
    mqtt = app.get("mqtt")
    engine: PositioningEngine = app.get("engine")
    config = _load_config()

    tracked = 0
    if engine:
        try:
            positions = await engine.get_positions()
            tracked = len(positions)
        except Exception:
            pass

    return web.json_response({
        "mqtt_connected": mqtt.connected if mqtt else False,
        "node_count": len(config.get("nodes", [])),
        "tracked_devices": tracked,
        "ingress_path": INGRESS_PATH,
    })


async def handle_get_positions(request: web.Request) -> web.Response:
    engine: PositioningEngine = request.app.get("engine")
    if not engine:
        return web.json_response([])
    positions = await engine.get_positions()
    return web.json_response(positions)


async def handle_get_paths(request: web.Request) -> web.Response:
    engine: PositioningEngine = request.app.get("engine")
    if not engine:
        return web.json_response([])
    mac_hash = request.query.get("mac_hash")
    since = request.query.get("since")
    since_f = float(since) if since else None
    paths = await engine.get_paths(mac_hash=mac_hash, since=since_f)
    return web.json_response(paths)


async def handle_get_calibration_status(request: web.Request) -> web.Response:
    engine: PositioningEngine = request.app.get("engine")
    if not engine:
        return web.json_response({})
    return web.json_response(engine.get_calibration_status())


async def handle_post_calibration_point(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "message": "Manual calibration point recorded"})


async def handle_get_anchors(request: web.Request) -> web.Response:
    engine: PositioningEngine = request.app.get("engine")
    if not engine:
        return web.json_response([])
    return web.json_response(engine.get_anchors())


async def handle_get_settings(request: web.Request) -> web.Response:
    return web.json_response(_load_settings())


async def handle_post_settings(request: web.Request) -> web.Response:
    data = await request.json()
    current = _load_settings()
    current.update(data)
    _save_settings(current)
    return web.json_response({"status": "ok"})


async def handle_firmware_download(request: web.Request) -> web.Response:
    target = request.match_info["target"]
    firmware_path = FIRMWARE_DIR / f"{target}.bin"
    if not firmware_path.exists():
        raise web.HTTPNotFound(text=f"Firmware {target}.bin not found")
    return web.FileResponse(firmware_path)


async def handle_firmware_upload(request: web.Request) -> web.Response:
    reader = await request.multipart()
    field = await reader.next()
    if not field:
        raise web.HTTPBadRequest(text="No file uploaded")

    filename = field.filename or "firmware.bin"
    target = filename.replace(".bin", "")
    dest = FIRMWARE_DIR / filename
    with open(dest, "wb") as f:
        while True:
            chunk = await field.read_chunk()
            if not chunk:
                break
            f.write(chunk)

    return web.json_response({"status": "ok", "target": target, "size": dest.stat().st_size})


async def handle_firmware_deploy(request: web.Request) -> web.Response:
    data = await request.json()
    node_id = data.get("node_id")
    target = data.get("target", "default")
    mqtt = request.app.get("mqtt")
    if not mqtt or not mqtt.connected:
        raise web.HTTPServiceUnavailable(text="MQTT not connected")

    ota_topic = f"burtooth/nodes/{node_id}/cmd/ota"
    firmware_url = data.get("firmware_url", f"http://homeassistant.local:8099/api/firmware/{target}.bin")
    await mqtt.publish(ota_topic, json.dumps({"url": firmware_url, "target": target}))
    return web.json_response({"status": "deploying", "node_id": node_id, "target": target})


async def handle_llm_query(request: web.Request) -> web.Response:
    data = await request.json()
    question = data.get("question", "")
    if not question:
        raise web.HTTPBadRequest(text="Missing 'question' field")

    llm: LLMQuery = request.app.get("llm")
    if not llm:
        return web.json_response({"answer": "LLM not available"})

    pattern_detector: PatternDetector = request.app.get("pattern_detector")
    context = {}
    if pattern_detector:
        context["recent_activity"] = pattern_detector.get_recent_activity()
        context["patterns"] = pattern_detector.detect_commuters()

    answer = await llm.query(question, context)
    return web.json_response({"answer": answer})


async def start_background_tasks(app: web.Application):
    mqtt_config = await ha_api.get_mqtt_config()
    mqtt = mqtt_client.MQTTClient()
    app["mqtt"] = mqtt

    config = _load_config()
    engine = PositioningEngine(ha_api=ha_api, config=config)
    app["engine"] = engine

    app["pattern_detector"] = PatternDetector()
    app["device_classifier"] = DeviceClassifier(oui_lookup)
    app["route_analyzer"] = RouteAnalyzer()
    app["llm"] = LLMQuery(ha_api)

    if mqtt_config:
        app["mqtt_task"] = asyncio.create_task(mqtt.connect(mqtt_config))
        await engine.start(mqtt)
    else:
        logger.warning("MQTT config unavailable; running without MQTT")


async def cleanup_background_tasks(app: web.Application):
    engine = app.get("engine")
    if engine:
        await engine.stop()
    mqtt = app.get("mqtt")
    if mqtt:
        await mqtt.disconnect()


async def handle_index(request: web.Request) -> web.FileResponse:
    return web.FileResponse("/app/frontend/index.html")


def create_app() -> web.Application:
    _ensure_dirs()
    app = web.Application()

    prefix = INGRESS_PATH

    app.router.add_get(f"{prefix}/", handle_index)
    app.router.add_get(f"{prefix}/api/config", handle_get_config)
    app.router.add_post(f"{prefix}/api/config", handle_post_config)
    app.router.add_get(f"{prefix}/api/status", handle_status)
    app.router.add_get(f"{prefix}/api/positions", handle_get_positions)
    app.router.add_get(f"{prefix}/api/paths", handle_get_paths)
    app.router.add_get(f"{prefix}/api/calibration/status", handle_get_calibration_status)
    app.router.add_post(f"{prefix}/api/calibration/point", handle_post_calibration_point)
    app.router.add_get(f"{prefix}/api/anchors", handle_get_anchors)
    app.router.add_get(f"{prefix}/api/settings", handle_get_settings)
    app.router.add_post(f"{prefix}/api/settings", handle_post_settings)
    app.router.add_get(f"{prefix}/api/firmware/{{target}}.bin", handle_firmware_download)
    app.router.add_post(f"{prefix}/api/firmware/upload", handle_firmware_upload)
    app.router.add_post(f"{prefix}/api/firmware/deploy", handle_firmware_deploy)
    app.router.add_post(f"{prefix}/api/llm/query", handle_llm_query)
    frontend_root = Path("/app/frontend")
    for subdir in ("css", "js", "lib"):
        full = frontend_root / subdir
        if full.is_dir():
            app.router.add_static(f"{prefix}/{subdir}", path=str(full), name=subdir)

    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)

    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8099)
