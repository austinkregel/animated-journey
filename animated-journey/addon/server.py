import asyncio
import json
import logging
import os
import time
from collections import deque
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


# Persistent add-on state lives at /data in the HA container, but that path is
# read-only (or absent) when running from a checkout for dev. Honor a DATA_DIR
# override, else use /data when it's writable, else a repo-local .dev-data dir.
def _resolve_data_dir() -> Path:
    env = os.environ.get("DATA_DIR")
    if env:
        return Path(env)
    container = Path("/data")
    if container.is_dir() and os.access(container, os.W_OK):
        return container
    return Path(__file__).resolve().parent.parent / ".dev-data"


DATA_DIR = _resolve_data_dir()


# Frontend assets live at /app/frontend in the container image, but when this
# server is run straight from a checkout (`python3 -m addon.server`) for a fast
# dev loop, fall back to the repo-relative frontend/ dir.
def _resolve_frontend_root() -> Path:
    env = os.environ.get("FRONTEND_DIR")
    if env:
        return Path(env)
    container = Path("/app/frontend")
    if container.is_dir():
        return container
    return Path(__file__).resolve().parent.parent / "frontend"


FRONTEND_ROOT = _resolve_frontend_root()

# Mark a node offline if we haven't heard a status from it within 3x the
# firmware's STATUS_REPORT_INTERVAL_MS (30s). 90s gives us tolerance for
# one missed report without flapping.
NODE_STALE_TIMEOUT_S = 90.0


def _is_node_online(status: dict) -> bool:
    last_seen = status.get("last_seen")
    if last_seen is None:
        return False
    return (time.time() - last_seen) < NODE_STALE_TIMEOUT_S


# esp_reset_reason_t -> human label, for the dev dashboard.
RESET_REASONS = {
    0: "unknown", 1: "power-on", 2: "external", 3: "software", 4: "panic",
    5: "int-wdt", 6: "task-wdt", 7: "other-wdt", 8: "deep-sleep", 9: "brownout",
    10: "sdio", 11: "usb", 12: "jtag", 13: "efuse", 14: "pwr-glitch",
    15: "cpu-lockup",
}


def _record_node_status(app, node_id: str, payload, now: float):
    """Store a node's latest status, keeping all diagnostic fields, and track
    when each distinct firmware git hash first appeared (for the rollout target).
    Module-level so it can be unit-tested without the full MQTT/HA stack."""
    statuses = app["node_statuses"]
    if isinstance(payload, dict):
        record = dict(payload)
        record["firmware_version"] = payload.get(
            "fw_version", payload.get("firmware_version", ""))
        record["ip"] = payload.get("ip", "")
        record["last_seen"] = now
        statuses[node_id] = record

        fw_git = payload.get("fw_git")
        if fw_git:
            first_seen = app.setdefault("fw_git_first_seen", {})
            first_seen.setdefault(fw_git, now)
    else:
        statuses.setdefault(node_id, {})["last_seen"] = now


def _current_target_git(app) -> str | None:
    """The most recently *introduced* firmware git hash across all nodes.

    Whichever distinct hash was first seen latest is treated as the rollout
    target; nodes still reporting a different hash are considered out of date.
    Held in memory, so it re-derives from live status reports after a restart.
    """
    first_seen = app.get("fw_git_first_seen", {})
    if not first_seen:
        return None
    return max(first_seen, key=first_seen.get)


# --- Live message feed (dev dashboard) -------------------------------------
FEED_MAX = 400          # ring-buffer depth
FEED_PAYLOAD_MAX = 400  # per-message payload preview length


def _feed_kind(topic: str) -> str:
    if "/scan/" in topic:
        return "scan"
    if topic.endswith("/status"):
        return "status"
    if "/cmd/" in topic:
        return "cmd"
    return "other"


def _record_feed(app, topic: str, payload, now: float):
    """Append one MQTT message to the in-memory live feed."""
    feed = app.get("feed")
    if feed is None:
        return
    if isinstance(payload, (dict, list)):
        text = json.dumps(payload, separators=(",", ":"))
    elif isinstance(payload, (bytes, bytearray)):
        text = payload.decode("utf-8", errors="replace")
    else:
        text = str(payload)
    if len(text) > FEED_PAYLOAD_MAX:
        text = text[:FEED_PAYLOAD_MAX] + "…"
    seq = app.get("feed_seq", 0) + 1
    app["feed_seq"] = seq
    feed.append({"seq": seq, "ts": now, "topic": topic,
                 "kind": _feed_kind(topic), "payload": text})
CONFIG_FILE = DATA_DIR / "config.json"
FIRMWARE_DIR = DATA_DIR / "firmware"
OVERLAY_DIR = DATA_DIR / "overlays"
SETTINGS_FILE = DATA_DIR / "settings.json"
ADDON_OPTIONS_FILE = DATA_DIR / "options.json"


def _load_addon_options() -> dict:
    if ADDON_OPTIONS_FILE.exists():
        try:
            return json.loads(ADDON_OPTIONS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            logger.exception("Failed to read addon options.json")
    return {}

MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def _ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FIRMWARE_DIR.mkdir(parents=True, exist_ok=True)
    OVERLAY_DIR.mkdir(parents=True, exist_ok=True)


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {"nodes": [], "origin": None}


def _save_config(data: dict):
    CONFIG_FILE.write_text(json.dumps(data, indent=2))


def _load_settings() -> dict:
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text())
    return {
        "mqtt_topic_prefix": "animated-journey",
        "scan_interval_ms": 2000,
        "position_update_ms": 1000,
        "active_scanning": False,
        "max_tracked_devices": 500,
        "mqtt_host": "",
        "mqtt_port": 1883,
        "mqtt_user": "",
        "mqtt_pass": "",
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

    node_statuses = app.get("node_statuses", {})
    enriched_statuses = {
        nid: {**status, "online": _is_node_online(status)}
        for nid, status in node_statuses.items()
    }

    all_nodes = config.get("nodes", [])
    return web.json_response({
        "mqtt_connected": mqtt.connected if mqtt else False,
        "node_count": len(all_nodes),
        "tracked_devices": tracked,
        "ingress_path": INGRESS_PATH,
        "node_statuses": enriched_statuses,
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

    ota_topic = f"animated-journey/nodes/{node_id}/cmd/ota"
    firmware_url = data.get("firmware_url", f"http://homeassistant.local:8099/api/firmware/{target}.bin")
    await mqtt.publish(ota_topic, json.dumps({"url": firmware_url, "target": target}))

    app = request.app
    ota_jobs = app.setdefault("ota_jobs", {})
    ota_jobs[node_id] = {"status": "deploying", "progress": 0}

    return web.json_response({"status": "deploying", "node_id": node_id, "target": target})


async def handle_get_nodes(request: web.Request) -> web.Response:
    config = _load_config()
    node_statuses = request.app.get("node_statuses", {})

    nodes = []
    for n in config.get("nodes", []):
        nid = n.get("node_id") or n.get("id", "")
        status = node_statuses.get(nid, {})
        nodes.append({
            "node_id": nid,
            "type": n.get("type", "unknown"),
            "x": n.get("x", 0),
            "y": n.get("y", 0),
            "z": n.get("z", 0),
            "online": _is_node_online(status),
            "firmware_version": status.get("firmware_version", ""),
            "ip": status.get("ip", ""),
            "uptime": status.get("uptime"),
            "last_seen": status.get("last_seen"),
            "auto_discovered": n.get("auto_discovered", False),
        })

    return web.json_response({"nodes": nodes})


async def handle_dev_nodes(request: web.Request) -> web.Response:
    """Full-fidelity node health for the dev dashboard.

    Unlike /api/nodes (which joins against placed nodes in config.json), this
    reports every node we've heard a status from, with all firmware/diagnostic
    fields and a computed up-to-date flag against the rollout target hash.
    """
    app = request.app
    statuses = app.get("node_statuses", {})
    target = _current_target_git(app)
    now = time.time()

    nodes = []
    for nid, s in statuses.items():
        fw_git = s.get("fw_git", "") or ""
        last_seen = s.get("last_seen")
        reset_reason = s.get("reset_reason")
        nodes.append({
            "node_id": nid,
            "online": _is_node_online(s),
            "last_seen": last_seen,
            "age_s": (now - last_seen) if last_seen else None,
            "fw_version": s.get("firmware_version", ""),
            "fw_git": fw_git,
            "dirty": fw_git.endswith("-dirty"),
            "up_to_date": bool(fw_git) and target is not None and fw_git == target,
            "chip_model": s.get("chip_model", ""),
            "chip_temp": s.get("chip_temp"),
            "uptime": s.get("uptime"),
            "free_heap": s.get("free_heap"),
            "min_free_heap": s.get("min_free_heap"),
            "psram_free": s.get("psram_free"),
            "psram_total": s.get("psram_total"),
            "wifi_rssi": s.get("wifi_rssi"),
            "reset_reason": reset_reason,
            "reset_reason_name": RESET_REASONS.get(reset_reason),
            "cpu_freq": s.get("cpu_freq"),
            "cpu_count": s.get("cpu_count"),
            "idf_version": s.get("idf_version", ""),
            # Coprocessor (C6) diagnostics -- present only on P4 nodes.
            "c6_fw": s.get("c6_fw", ""),
            "c6_chip": s.get("c6_chip", ""),
            "c6_rpc": s.get("c6_rpc", ""),
            "c6_reset": s.get("c6_reset"),
            "c6_reset_name": RESET_REASONS.get(s.get("c6_reset")),
            "c6_link": s.get("c6_link"),
            "c6_reboots": s.get("c6_reboots"),
            "ble": s.get("ble"),
            "ble_active": s.get("ble_active"),
            "beacons": s.get("beacons"),
            "probes": s.get("probes"),
        })

    nodes.sort(key=lambda n: n["node_id"])
    return web.json_response({
        "target_git": target,
        "server_time": now,
        "node_count": len(nodes),
        "online_count": sum(1 for n in nodes if n["online"]),
        "stale_timeout_s": NODE_STALE_TIMEOUT_S,
        "nodes": nodes,
    })


async def handle_dev_feed(request: web.Request) -> web.Response:
    """Incremental live feed of MQTT messages the nodes post.

    Pass ?since=<cursor> to get only messages newer than a prior poll; omit it
    (or -1) to get the whole current buffer. Entries are ascending by seq.
    """
    app = request.app
    feed = app.get("feed")
    try:
        since = int(request.query.get("since", "-1"))
    except ValueError:
        since = -1
    entries = list(feed) if feed else []
    if since >= 0:
        entries = [e for e in entries if e["seq"] > since]
    return web.json_response({
        "cursor": app.get("feed_seq", 0),
        "server_time": time.time(),
        "count": len(entries),
        "entries": entries,
    })


async def handle_dev_dashboard(request: web.Request) -> web.Response:
    html_path = FRONTEND_ROOT / "dev-dashboard.html"
    if not html_path.exists():
        raise web.HTTPNotFound(text=f"dev-dashboard.html not found under {FRONTEND_ROOT}")
    html = html_path.read_text().replace("{{INGRESS_PATH}}", INGRESS_PATH)
    return web.Response(text=html, content_type="text/html")


async def handle_ota_update(request: web.Request) -> web.Response:
    data = await request.json()
    node_id = data.get("node_id")
    if not node_id:
        raise web.HTTPBadRequest(text="Missing node_id")

    mqtt = request.app.get("mqtt")
    if not mqtt or not mqtt.connected:
        raise web.HTTPServiceUnavailable(text="MQTT not connected")

    ota_topic = f"animated-journey/nodes/{node_id}/cmd/ota"
    firmware_url = f"http://homeassistant.local:8099{INGRESS_PATH}/api/firmware/default.bin"
    await mqtt.publish(ota_topic, json.dumps({"url": firmware_url}))

    ota_jobs = request.app.setdefault("ota_jobs", {})
    ota_jobs[node_id] = {"status": "deploying", "progress": 0}

    return web.json_response({"status": "ok", "node_id": node_id})


async def handle_ota_status(request: web.Request) -> web.Response:
    node_id = request.match_info["node_id"]
    ota_jobs = request.app.get("ota_jobs", {})
    job = ota_jobs.get(node_id, {"status": "unknown", "progress": 0})
    return web.json_response(job)


async def handle_node_restart(request: web.Request) -> web.Response:
    node_id = request.match_info["node_id"]
    mqtt = request.app.get("mqtt")
    if not mqtt or not mqtt.connected:
        raise web.HTTPServiceUnavailable(text="MQTT not connected")

    restart_topic = f"animated-journey/nodes/{node_id}/cmd/restart"
    await mqtt.publish(restart_topic, json.dumps({"action": "restart"}))
    return web.json_response({"status": "ok", "node_id": node_id})


async def handle_overlay_upload(request: web.Request) -> web.Response:
    reader = await request.multipart()
    field = await reader.next()
    if not field:
        raise web.HTTPBadRequest(text="No file uploaded")

    filename = field.filename or "overlay.png"
    suffix = Path(filename).suffix.lower()
    if suffix not in MIME_TYPES:
        raise web.HTTPBadRequest(text=f"Unsupported image type: {suffix}")

    dest = OVERLAY_DIR / f"overlay{suffix}"

    # Remove any existing overlay files
    for old in OVERLAY_DIR.glob("overlay.*"):
        old.unlink()

    with open(dest, "wb") as f:
        while True:
            chunk = await field.read_chunk()
            if not chunk:
                break
            f.write(chunk)

    return web.json_response({
        "status": "ok",
        "filename": dest.name,
        "size": dest.stat().st_size,
    })


async def handle_overlay_image(request: web.Request) -> web.Response:
    for f in OVERLAY_DIR.glob("overlay.*"):
        suffix = f.suffix.lower()
        ct = MIME_TYPES.get(suffix, "application/octet-stream")
        return web.FileResponse(f, headers={"Content-Type": ct})
    raise web.HTTPNotFound(text="No overlay image uploaded")


async def handle_overlay_delete(request: web.Request) -> web.Response:
    for f in OVERLAY_DIR.glob("overlay.*"):
        f.unlink()

    settings = _load_settings()
    settings.pop("overlay_bounds", None)
    _save_settings(settings)
    return web.json_response({"status": "ok"})


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
    mqtt_config = None

    # Env override wins, so a local dev run is just:
    #   MQTT_HOST=192.168.3.52 python3 -m addon.server
    env_host = os.environ.get("MQTT_HOST")
    if env_host:
        mqtt_config = {
            "host": env_host,
            "port": int(os.environ.get("MQTT_PORT", 1883)),
            "username": os.environ.get("MQTT_USER", ""),
            "password": os.environ.get("MQTT_PASS", ""),
        }
        logger.info("Using MQTT config from environment (host=%s)", env_host)

    addon_options = _load_addon_options()
    if not mqtt_config and addon_options.get("mqtt_host"):
        mqtt_config = {
            "host": addon_options["mqtt_host"],
            "port": int(addon_options.get("mqtt_port", 1883)),
            "username": addon_options.get("mqtt_user", ""),
            "password": addon_options.get("mqtt_pass", ""),
        }
        logger.info("Using MQTT config from addon options (host=%s)", mqtt_config["host"])

    if not mqtt_config:
        mqtt_config = await ha_api.get_mqtt_config()
        if mqtt_config:
            logger.info("Using MQTT config from Supervisor (host=%s)", mqtt_config["host"])

    if not mqtt_config:
        settings = _load_settings()
        if settings.get("mqtt_host"):
            mqtt_config = {
                "host": settings["mqtt_host"],
                "port": int(settings.get("mqtt_port", 1883)),
                "username": settings.get("mqtt_user", ""),
                "password": settings.get("mqtt_pass", ""),
            }
            logger.info("Using MQTT config from settings (host=%s)", mqtt_config["host"])

    mqtt = mqtt_client.MQTTClient()
    app["mqtt"] = mqtt
    app["node_statuses"] = {}
    app["feed"] = deque(maxlen=FEED_MAX)
    app["feed_seq"] = 0

    config = _load_config()
    engine = PositioningEngine(ha_api=ha_api, config=config)
    app["engine"] = engine

    app["pattern_detector"] = PatternDetector()
    app["device_classifier"] = DeviceClassifier(oui_lookup)
    app["route_analyzer"] = RouteAnalyzer()
    app["llm"] = LLMQuery(ha_api)

    pending_config_save = {"dirty": False}

    def _auto_discover_node(node_id: str, payload: dict):
        config = _load_config()
        nodes = config.get("nodes", [])
        known_ids = {n.get("node_id") or n.get("id") for n in nodes}
        if node_id in known_ids:
            return
        new_node = {
            "node_id": node_id,
            "type": "scanner",
            "x": 0,
            "y": 0,
            "z": 0,
            "auto_discovered": True,
        }
        if isinstance(payload, dict):
            if payload.get("fw_version"):
                new_node["firmware_version"] = payload["fw_version"]
            if payload.get("model"):
                new_node["model"] = payload["model"]
        nodes.append(new_node)
        config["nodes"] = nodes
        _save_config(config)
        logger.info("Auto-discovered new node: %s", node_id)

    async def _handle_node_status(topic: str, payload):
        parts = topic.split("/")
        if len(parts) < 4 or parts[3] != "status":
            return
        node_id = parts[2]
        now = time.time()
        # Keep the full diagnostic payload (chip_temp, heap, psram, fw_git,
        # reset_reason, ...) so the dev dashboard can surface it, and track the
        # firmware git hash for rollout-target detection.
        _record_node_status(app, node_id, payload, now)

        _auto_discover_node(node_id, payload if isinstance(payload, dict) else {})

        ota_progress = None
        if isinstance(payload, dict):
            ota_progress = payload.get("ota_progress")
        if ota_progress is not None:
            ota_jobs = app.setdefault("ota_jobs", {})
            job = ota_jobs.setdefault(node_id, {})
            job["progress"] = ota_progress
            job["status"] = payload.get("ota_status", "deploying")

    async def _handle_feed(topic, payload):
        _record_feed(app, topic, payload, time.time())

    if mqtt_config:
        mqtt.register_handler("animated-journey/nodes/#", _handle_node_status)
        # Records every animated-journey message (scan + status) into the live feed.
        mqtt.register_handler("animated-journey/#", _handle_feed)
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


async def handle_index(request: web.Request) -> web.Response:
    html = Path("/app/frontend/index.html").read_text()
    meta_tag = f'<meta name="ingress-path" content="{INGRESS_PATH}">'
    html = html.replace("</head>", f"  {meta_tag}\n</head>", 1)
    return web.Response(text=html, content_type="text/html")


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
    app.router.add_get(f"{prefix}/api/nodes", handle_get_nodes)
    app.router.add_get(f"{prefix}/dev-dashboard", handle_dev_dashboard)
    app.router.add_get(f"{prefix}/api/dev/nodes", handle_dev_nodes)
    app.router.add_get(f"{prefix}/api/dev/feed", handle_dev_feed)
    app.router.add_post(f"{prefix}/api/nodes/{{node_id}}/restart", handle_node_restart)
    app.router.add_get(f"{prefix}/api/firmware/{{target}}.bin", handle_firmware_download)
    app.router.add_post(f"{prefix}/api/firmware/upload", handle_firmware_upload)
    app.router.add_post(f"{prefix}/api/firmware/deploy", handle_firmware_deploy)
    app.router.add_post(f"{prefix}/api/ota/update", handle_ota_update)
    app.router.add_get(f"{prefix}/api/ota/status/{{node_id}}", handle_ota_status)
    app.router.add_post(f"{prefix}/api/ota/upload", handle_firmware_upload)
    app.router.add_post(f"{prefix}/api/overlay/upload", handle_overlay_upload)
    app.router.add_get(f"{prefix}/api/overlay/image", handle_overlay_image)
    app.router.add_delete(f"{prefix}/api/overlay", handle_overlay_delete)
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
