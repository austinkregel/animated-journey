import json
import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

_OLLAMA_ENTITY = "ai_task.ollama_ai_task"
_OLLAMA_DIRECT_URL = "http://homeassistant.local:11434/api/generate"
_OLLAMA_MODEL = "llama3"

_SYSTEM_INSTRUCTIONS = (
    "You are an analytical assistant for a Bluetooth/WiFi mesh sensor network. "
    "The user will ask questions about device traffic patterns, common routes, "
    "and device classification data collected by the sensor array. "
    "Answer concisely using only the data provided in the context. "
    "If the data is insufficient, say so."
)


class LLMQuery:
    """Natural language queries on pattern data via Ollama."""

    def __init__(self, ha_api=None, supervisor_url: str = "http://supervisor", supervisor_token: str = ""):
        import os
        self._ha_api = ha_api
        self._supervisor_url = supervisor_url
        self._token = supervisor_token or os.environ.get("SUPERVISOR_TOKEN", "")

    async def query(self, question: str, context: dict) -> str:
        """Ask a question about RF scan / pattern data.

        Args:
            question: Natural language question from the user.
            context: Dict with relevant data to include as context:
                - recent_devices: list of recent device summaries
                - patterns: detected commuter patterns
                - statistics: overall stats
                - routes: common route info

        Returns:
            Natural language answer string.
        """
        prompt = self._build_prompt(question, context)

        answer = await self._query_via_ha(prompt)
        if answer is not None:
            return answer

        answer = await self._query_direct(prompt)
        if answer is not None:
            return answer

        return "LLM query failed: neither Home Assistant nor direct Ollama endpoint responded."

    def _build_prompt(self, question: str, context: dict) -> str:
        """Build a structured prompt with data context."""
        sections: list[str] = [_SYSTEM_INSTRUCTIONS, ""]

        if context.get("statistics"):
            sections.append("## Overall Statistics")
            sections.append(json.dumps(context["statistics"], indent=2))

        if context.get("recent_devices"):
            sections.append("## Recent Devices")
            for dev in context["recent_devices"][:20]:
                sections.append(f"- {json.dumps(dev)}")

        if context.get("patterns"):
            sections.append("## Detected Commuter Patterns")
            for pat in context["patterns"][:15]:
                sections.append(f"- {json.dumps(pat)}")

        if context.get("routes"):
            sections.append("## Common Routes")
            for route in context["routes"][:10]:
                sections.append(f"- {json.dumps(route)}")

        sections.append("")
        sections.append(f"## Question\n{question}")

        return "\n".join(sections)

    async def _query_via_ha(self, prompt: str) -> Optional[str]:
        """Query via Home Assistant ``ai_task.generate`` service."""
        if not self._token:
            logger.debug("No supervisor token; skipping HA ai_task route")
            return None

        url = f"{self._supervisor_url}/core/api/services/ai_task/generate"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        body = {
            "entity_id": _OLLAMA_ENTITY,
            "task_text": prompt,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        text = data.get("response", data.get("text", ""))
                        if text:
                            return str(text)
                        logger.warning("HA ai_task returned 200 but no usable text: %s", data)
                    else:
                        logger.warning("HA ai_task returned %d: %s", resp.status, await resp.text())
        except Exception:
            logger.debug("HA ai_task call failed", exc_info=True)

        return None

    async def _query_direct(self, prompt: str) -> Optional[str]:
        """Query Ollama directly via its HTTP API."""
        body = {
            "model": _OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(_OLLAMA_DIRECT_URL, json=body, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("response", "")
                    logger.warning("Ollama direct returned %d: %s", resp.status, await resp.text())
        except Exception:
            logger.debug("Direct Ollama call failed", exc_info=True)

        return None
