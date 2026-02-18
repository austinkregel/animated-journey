import asyncio
import json
import logging
from typing import Any, Callable

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)

MAX_RECONNECT_DELAY = 60


class MQTTClient:
    def __init__(self):
        self._client: mqtt.Client | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._handlers: dict[str, list[Callable]] = {}
        self.connected = False
        self._config: dict = {}
        self._reconnect_delay = 1

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: dict, rc: int):
        if rc == 0:
            logger.info("MQTT connected")
            self.connected = True
            self._reconnect_delay = 1
            client.subscribe("burtooth/scan/#")
            client.subscribe("burtooth/nodes/#")
        else:
            logger.error("MQTT connection failed with code %d", rc)

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, rc: int):
        self.connected = False
        if rc != 0:
            logger.warning("MQTT disconnected unexpectedly (rc=%d), will reconnect", rc)

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage):
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = msg.payload

        for pattern, handlers in self._handlers.items():
            if mqtt.topic_matches_sub(pattern, topic):
                for handler in handlers:
                    if self._loop:
                        self._loop.call_soon_threadsafe(
                            asyncio.ensure_future,
                            handler(topic, payload),
                        )

    def register_handler(self, topic_pattern: str, handler: Callable):
        self._handlers.setdefault(topic_pattern, []).append(handler)

    async def connect(self, config: dict):
        self._config = config
        self._loop = asyncio.get_running_loop()

        while True:
            try:
                self._client = mqtt.Client()
                self._client.on_connect = self._on_connect
                self._client.on_disconnect = self._on_disconnect
                self._client.on_message = self._on_message

                if config.get("username"):
                    self._client.username_pw_set(config["username"], config.get("password", ""))

                self._client.connect(config.get("host", "core-mosquitto"), config.get("port", 1883))
                self._client.loop_start()

                while self.connected or not self._client:
                    await asyncio.sleep(1)
                    if self.connected:
                        self._reconnect_delay = 1
                        continue

                self._client.loop_stop()

            except Exception:
                logger.exception("MQTT connection error")

            logger.info("Reconnecting in %ds...", self._reconnect_delay)
            await asyncio.sleep(self._reconnect_delay)
            self._reconnect_delay = min(self._reconnect_delay * 2, MAX_RECONNECT_DELAY)

    async def disconnect(self):
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self.connected = False
            logger.info("MQTT disconnected")

    async def publish(self, topic: str, payload: str | dict, retain: bool = False):
        if not self._client or not self.connected:
            logger.warning("Cannot publish: MQTT not connected")
            return
        if isinstance(payload, dict):
            payload = json.dumps(payload)
        self._client.publish(topic, payload, retain=retain)

    async def publish_discovery(self, node_id: str, model: str, sensors: list[dict]):
        for sensor in sensors:
            sensor_id = sensor["id"]
            config_topic = f"homeassistant/sensor/burtooth_{node_id}/{sensor_id}/config"
            config_payload = {
                "name": f"Burtooth {node_id} {sensor['name']}",
                "unique_id": f"burtooth_{node_id}_{sensor_id}",
                "state_topic": f"burtooth/nodes/{node_id}/{sensor_id}",
                "device": {
                    "identifiers": [f"burtooth_{node_id}"],
                    "name": f"Burtooth {node_id}",
                    "model": model,
                    "manufacturer": "Burtooth Mesh",
                },
            }
            if "unit" in sensor:
                config_payload["unit_of_measurement"] = sensor["unit"]
            if "device_class" in sensor:
                config_payload["device_class"] = sensor["device_class"]

            await self.publish(config_topic, config_payload, retain=True)
            logger.info("Published discovery for %s/%s", node_id, sensor_id)
