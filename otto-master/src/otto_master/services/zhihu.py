"""Application service for bounded, read-only Zhihu queries."""

from __future__ import annotations

import asyncio
from collections import deque
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, cast

from ..gateways.zhihu import ZhihuGateway, tool_definitions
from ..messages import JsonValue
from ..storage.database import Database

_DEFAULT_PERSONA = {
    "name": "奶龙",
    "persona": "活泼、呆萌、爱笑的机器人伙伴",
    "memory": "",
    "interests": "机器人、科技与有趣的新知识",
}


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class ZhihuService:
    """Own non-secret radio state while the gateway owns provider protocol details."""

    def __init__(
        self,
        gateway: ZhihuGateway,
        database: Database,
        *,
        history_size: int,
    ) -> None:
        if history_size < 1:
            raise ValueError("Zhihu event history must be positive")
        self.gateway = gateway
        self.database = database
        self._events: deque[dict[str, Any]] = deque(maxlen=history_size)
        self._cursor = 0
        self._event_lock = asyncio.Lock()
        self._last_probe: dict[str, Any] | None = None

    def status(self) -> dict[str, Any]:
        gateway_status = self.gateway.status()
        probe = self._last_probe
        return {
            **gateway_status,
            "provider": "zhihu_official",
            "read_only": True,
            "last_probe_at": probe.get("checked_at") if probe is not None else None,
            "capabilities": (
                deepcopy(probe.get("capabilities", [])) if probe is not None else []
            ),
            "event_cursor": self._cursor,
        }

    def tools(self) -> dict[str, Any]:
        return {
            "items": tool_definitions(),
            "read_only": True,
            "pagination": "single_page",
        }

    async def persona(self) -> dict[str, str]:
        settings = await self.database.load_settings()
        stored = settings.get("zhihu.persona")
        if not isinstance(stored, dict):
            return dict(_DEFAULT_PERSONA)
        result = dict(_DEFAULT_PERSONA)
        for key in result:
            value = stored.get(key)
            if isinstance(value, str):
                result[key] = value
        return result

    async def save_persona(self, values: dict[str, str]) -> dict[str, str]:
        normalized = {key: values.get(key, "").strip() for key in _DEFAULT_PERSONA}
        if not normalized["name"]:
            normalized["name"] = _DEFAULT_PERSONA["name"]
        await self.database.save_setting(
            "zhihu.persona",
            cast(JsonValue, normalized),
        )
        await self._record("persona.updated", {"name": normalized["name"]})
        return normalized

    async def probe(self) -> dict[str, Any]:
        result = await self.gateway.quota()
        self._last_probe = deepcopy(result)
        capabilities = result.get("capabilities", [])
        await self._record(
            "zhihu.probed",
            {
                "verified": result.get("verified") is True,
                "capability_count": len(capabilities) if isinstance(capabilities, list) else 0,
            },
        )
        return result

    async def query(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self.gateway.call(tool, arguments)
        await self._record(
            "zhihu.query.completed",
            {
                "tool": result.get("tool"),
                "title": result.get("title"),
                "retrieved_at": result.get("retrieved_at"),
            },
        )
        return result

    async def events(self, *, after: int = 0, limit: int = 64) -> dict[str, Any]:
        async with self._event_lock:
            items = [deepcopy(item) for item in self._events if item["cursor"] > after]
            return {
                "items": items[:limit],
                "cursor": self._cursor,
                "history_limited": bool(self._events and after < self._events[0]["cursor"] - 1),
            }

    async def _record(self, event_type: str, data: dict[str, Any]) -> None:
        async with self._event_lock:
            self._cursor += 1
            self._events.append(
                {
                    "cursor": self._cursor,
                    "type": event_type,
                    "created_at": _now(),
                    "data": deepcopy(data),
                }
            )
