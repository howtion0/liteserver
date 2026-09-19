from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from otto_master.config import load_config
from otto_master.gateways.zhihu import (
    ZhihuGateway,
    ZhihuGatewayError,
    normalize_content_url,
    prepare_tool_arguments,
    safe_json_numbers,
    tool_definitions,
)
from otto_master.services.zhihu import ZhihuService
from otto_master.storage.database import Database


def _zhihu_config(**changes: Any) -> Any:
    loaded = load_config(environment={"ZHIHU_ACCESS_SECRET": "unit-test-override"})
    return replace(loaded.zhihu, **changes)


def test_tool_contract_rejects_publish_unknown_fields_and_unsafe_urls() -> None:
    assert prepare_tool_arguments("hot", {}) == {"limit": 5}
    assert prepare_tool_arguments("search_zhihu", {"query": " 机器人 "}) == {
        "query": "机器人",
        "limit": 5,
    }
    assert normalize_content_url(
        "https://www.zhihu.com/question/123?utm_source=test#fragment",
        question=True,
    ) == "https://www.zhihu.com/question/123"
    assert normalize_content_url(
        "https://zhuanlan.zhihu.com/p/456?utm_source=test"
    ) == "https://zhuanlan.zhihu.com/p/456"

    with pytest.raises(ZhihuGatewayError, match="不提供发布"):
        prepare_tool_arguments("publish", {})
    with pytest.raises(ZhihuGatewayError, match="不支持的参数"):
        prepare_tool_arguments("hot", {"limit": 1, "cookie": "no"})
    with pytest.raises(ZhihuGatewayError, match="HTTPS"):
        normalize_content_url("http://www.zhihu.com/question/123", question=True)
    with pytest.raises(ZhihuGatewayError, match="请选择"):
        normalize_content_url("https://example.com/question/123", question=True)
    with pytest.raises(ZhihuGatewayError, match="超出"):
        prepare_tool_arguments("hot", {"limit": 31})


def test_tool_metadata_is_read_only_and_json_numbers_are_browser_safe() -> None:
    definitions = tool_definitions()
    assert definitions
    assert all("只读" in item["description"] for item in definitions)
    assert all("publish" not in item["name"] for item in definitions)
    assert safe_json_numbers({"id": 2**53, "small": 7}) == {
        "id": str(2**53),
        "small": 7,
    }


async def test_gateway_calls_official_contract_without_exposing_secret() -> None:
    credential = "unit-test-access-secret"
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["authorization"] == f"Bearer {credential}"
        assert request.headers["x-request-timestamp"].isdigit()
        assert request.url.host == "developer.zhihu.com"
        return httpx.Response(
            200,
            json={
                "Code": 0,
                "Data": {"items": [{"id": 2**53, "title": "测试热榜"}]},
            },
        )

    gateway = ZhihuGateway(
        _zhihu_config(),
        credential,
        transport=httpx.MockTransport(handler),
    )
    await gateway.start()
    try:
        result = await gateway.call("hot", {"limit": 1})
    finally:
        await gateway.shutdown()

    assert len(seen) == 1
    assert seen[0].url.path == "/api/v1/content/hot_list"
    assert seen[0].url.params["Limit"] == "1"
    assert result["data"]["items"][0]["id"] == str(2**53)
    assert credential not in json.dumps(result, ensure_ascii=False)
    assert credential not in repr(gateway.status())


async def test_quota_maps_capabilities_and_never_returns_credential() -> None:
    credential = "unit-test-quota-secret"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/quota"
        return httpx.Response(
            200,
            json={
                "Code": 0,
                "Data": [
                    {
                        "APIID": "hot_list",
                        "APIName": "热榜",
                        "TotalQuota": 100,
                        "TotalUsed": 2,
                        "RemainingQuota": 98,
                    }
                ],
            },
        )

    gateway = ZhihuGateway(
        _zhihu_config(),
        credential,
        transport=httpx.MockTransport(handler),
    )
    await gateway.start()
    try:
        result = await gateway.quota()
    finally:
        await gateway.shutdown()

    hot = next(item for item in result["capabilities"] if item["id"] == "hot_list")
    assert hot["available"] is True
    assert hot["remaining"] == 98
    assert any(item["id"] == "zhida_openai" for item in result["capabilities"])
    assert credential not in json.dumps(result, ensure_ascii=False)


async def test_failures_are_bounded_and_not_retried() -> None:
    calls = 0

    async def rate_limited(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"error": "too many"})

    gateway = ZhihuGateway(
        _zhihu_config(),
        "unit-test-secret",
        transport=httpx.MockTransport(rate_limited),
    )
    await gateway.start()
    try:
        with pytest.raises(ZhihuGatewayError) as captured:
            await gateway.call("answer", {"query": "测试"})
    finally:
        await gateway.shutdown()

    assert calls == 1
    assert captured.value.status_code == 429
    assert captured.value.code == "upstream_http"


async def test_response_limit_and_missing_credential_fail_closed() -> None:
    calls = 0

    async def oversized(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b'{' + b'x' * 2_000 + b'}')

    gateway = ZhihuGateway(
        _zhihu_config(max_response_bytes=1_024),
        "unit-test-secret",
        transport=httpx.MockTransport(oversized),
    )
    await gateway.start()
    try:
        with pytest.raises(ZhihuGatewayError, match="响应过大"):
            await gateway.call("hot", {})
    finally:
        await gateway.shutdown()
    assert calls == 1

    unconfigured = ZhihuGateway(
        _zhihu_config(),
        None,
        transport=httpx.MockTransport(oversized),
    )
    await unconfigured.start()
    try:
        with pytest.raises(ZhihuGatewayError) as captured:
            await unconfigured.quota()
    finally:
        await unconfigured.shutdown()
    assert captured.value.code == "credential_not_configured"
    assert calls == 1


async def test_gateway_enforces_configured_concurrency_limit() -> None:
    active = 0
    maximum_active = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json={"Code": 0, "Data": {"Items": []}})

    gateway = ZhihuGateway(
        _zhihu_config(max_concurrency=2),
        "unit-test-secret",
        transport=httpx.MockTransport(handler),
    )
    await gateway.start()
    try:
        await asyncio.gather(*(gateway.call("hot", {"limit": 1}) for _ in range(6)))
    finally:
        await gateway.shutdown()

    assert maximum_active == 2

async def test_service_persists_only_persona_and_keeps_results_in_memory(
    tmp_path: Path,
) -> None:
    credential = "unit-test-service-secret"

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/quota":
            return httpx.Response(200, json={"Code": 0, "Data": []})
        return httpx.Response(
            200,
            json={"Code": 0, "Data": {"items": [{"title": "短期结果"}]}},
        )

    database = await Database.open(tmp_path / "otto.db")
    gateway = ZhihuGateway(
        _zhihu_config(event_history_size=2),
        credential,
        transport=httpx.MockTransport(handler),
    )
    service = ZhihuService(gateway, database, history_size=2)
    await gateway.start()
    try:
        saved = await service.save_persona(
            {
                "name": "奶龙",
                "persona": "轻快",
                "memory": "记住机器人",
                "interests": "科技",
            }
        )
        assert saved["name"] == "奶龙"
        assert await service.persona() == saved
        assert (await service.probe())["verified"] is True
        assert (await service.query("hot", {"limit": 1}))["tool"] == "hot"
        events = await service.events()
        assert len(events["items"]) == 2
        assert events["items"][-1]["type"] == "zhihu.query.completed"

        settings = await database.load_settings()
        encoded = json.dumps(settings, ensure_ascii=False)
        assert credential not in encoded
        assert "短期结果" not in encoded
        assert set(settings) == {"zhihu.persona"}
    finally:
        await gateway.shutdown()
        await database.close()
