"""Official read-only Zhihu Open Platform gateway."""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..config import ZhihuConfig


class ZhihuGatewayError(RuntimeError):
    """Stable provider failure safe to expose through the control plane."""

    def __init__(self, code: str, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class ZhihuToolSpec:
    title: str
    path: str
    parameters: dict[str, str]


TOOL_SPECS: dict[str, ZhihuToolSpec] = {
    "search_zhihu": ZhihuToolSpec(
        "搜索知乎",
        "/api/v1/content/zhihu_search",
        {"query": "Query", "limit": "Count"},
    ),
    "search_global": ZhihuToolSpec(
        "搜索全网",
        "/api/v1/content/global_search",
        {"query": "Query", "limit": "Count"},
    ),
    "hot": ZhihuToolSpec("知乎热榜", "/api/v1/content/hot_list", {"limit": "Limit"}),
    "recommend": ZhihuToolSpec(
        "问题发现",
        "/api/v1/user/question_recommendations",
        {"query": "Query", "limit": "Count"},
    ),
    "answers": ZhihuToolSpec(
        "问题回答摘要",
        "/api/v1/content/question_answers",
        {"url": "QuestionUrl", "limit": "Limit", "offset": "Offset"},
    ),
    "contents": ZhihuToolSpec(
        "我的帖子",
        "/api/v1/user/contents",
        {"content_type": "ContentType", "limit": "Limit", "offset": "Offset"},
    ),
    "detail": ZhihuToolSpec(
        "本人创作全文",
        "/api/v1/user/content_detail",
        {"url": "ContentUrl"},
    ),
    "comments": ZhihuToolSpec(
        "本人帖子的评论",
        "/api/v1/user/content_comments",
        {"url": "ContentUrl", "limit": "Limit", "offset": "Offset"},
    ),
    "stats": ZhihuToolSpec(
        "账号创作数据",
        "/api/v1/user/creator_account_stats",
        {"content_type": "ContentType"},
    ),
    "content_stats": ZhihuToolSpec(
        "单篇创作数据",
        "/api/v1/user/creator_content_stats",
        {"url": "ContentUrl"},
    ),
    "followees": ZhihuToolSpec(
        "我的关注",
        "/api/v1/user/followees",
        {"limit": "Limit", "offset": "Offset"},
    ),
    "favorites": ZhihuToolSpec(
        "我的近期收藏",
        "/api/v1/user/collections",
        {"limit": "Limit"},
    ),
    "favlists": ZhihuToolSpec(
        "我的收藏夹",
        "/api/v1/user/favlists",
        {"limit": "Limit"},
    ),
    "favlist_items": ZhihuToolSpec(
        "收藏夹内容",
        "/api/v1/user/favlist_contents",
        {"folder_id": "FavlistUrlToken", "limit": "Limit", "offset": "Offset"},
    ),
}

TOOL_TITLES = {name: spec.title for name, spec in TOOL_SPECS.items()} | {
    "answer": "知乎直答"
}
QUERY_TOOLS = {"search_zhihu", "search_global", "answer"}
URL_TOOLS = {"answers", "detail", "comments", "content_stats"}

CAPABILITY_GROUPS: dict[str, tuple[str, list[str]]] = {
    "global_search": ("全网搜索", ["搜索全网"]),
    "zhihu_search": ("知乎搜索", ["搜索知乎"]),
    "hot_list": ("知乎热榜", ["知乎热榜"]),
    "question_answers": ("问题回答", ["问题回答摘要"]),
    "user_data": ("知乎用户数据", ["我的帖子", "我的关注", "近期收藏", "收藏夹"]),
    "creator": ("创作能力", ["问题发现", "本人全文", "本人评论", "账号数据", "单篇数据"]),
    "zhida_openai": ("知乎直答", ["知乎直答"]),
    "knowledge": ("知识库", ["知识库列表", "文件上传", "RAG 检索"]),
    "tools": ("小工具", ["开放平台小工具"]),
}


def _invalid(message: str) -> ZhihuGatewayError:
    return ZhihuGatewayError("invalid_input", message, status_code=422)


def normalize_content_url(value: Any, *, question: bool = False) -> str:
    """Accept only canonical public Zhihu content URLs."""

    if not isinstance(value, str):
        raise _invalid("内容链接必须是文本")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise _invalid("无效的内容链接") from None
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
    ):
        raise _invalid("请提供知乎 HTTPS 内容链接")
    pattern = r"/question/\d+/?" if question else (
        r"/(?:answer/\d+|question/\d+/answer/\d+|pin/\d+|zvideo/\d+)/?"
    )
    valid = (
        parsed.hostname == "www.zhihu.com"
        and re.fullmatch(pattern, parsed.path) is not None
    )
    if not question:
        valid = valid or (
            parsed.hostname == "zhuanlan.zhihu.com"
            and re.fullmatch(r"/p/\d+/?", parsed.path) is not None
        )
    if not valid:
        raise _invalid("请选择对应的知乎问题或本人内容链接")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def prepare_tool_arguments(name: str, arguments: Any) -> dict[str, str | int]:
    """Validate one page of official API arguments and reject unknown fields."""

    if name not in TOOL_TITLES:
        raise ZhihuGatewayError(
            "unknown_tool",
            "不支持此工具；这里只提供查询，不提供发布",
            status_code=404,
        )
    if not isinstance(arguments, dict) or not all(
        isinstance(key, str) for key in arguments
    ):
        raise _invalid("参数必须是对象")
    allowed = {"query"} if name == "answer" else set(TOOL_SPECS[name].parameters)
    if set(arguments) - allowed:
        raise _invalid("包含不支持的参数")
    values: dict[str, str | int] = dict(arguments)

    if name in QUERY_TOOLS or (name == "recommend" and "query" in values):
        query = values.get("query", "")
        if not isinstance(query, str) or not query.strip() or len(query) > 1_000:
            raise _invalid("请填写搜索主题，或先设置兴趣爱好")
        values["query"] = query.strip()

    if name in URL_TOOLS:
        values["url"] = normalize_content_url(
            values.get("url", ""),
            question=name == "answers",
        )

    if "limit" in allowed:
        default_limit = 5
        limit = values.get("limit", default_limit)
        maximum = (
            10
            if name == "search_zhihu"
            else 20
            if name in {"search_global", "recommend"}
            else 30
            if name == "hot"
            else 50
        )
        if type(limit) is not int or not 1 <= limit <= maximum:
            raise _invalid("查询条数超出允许范围")
        values["limit"] = limit

    if "offset" in allowed:
        offset = str(values.get("offset", "0"))
        if not re.fullmatch(r"\d{1,19}", offset) or int(offset) > 2**63 - 1:
            raise _invalid("分页游标无效")
        values["offset"] = offset

    if "folder_id" in allowed:
        folder_id = str(values.get("folder_id", ""))
        if not re.fullmatch(r"[1-9]\d{0,18}", folder_id) or int(folder_id) > 2**63 - 1:
            raise _invalid("请先从收藏夹列表选择收藏夹")
        values["folder_id"] = folder_id

    if "content_type" in allowed:
        valid_types = {"all", "answer", "article", "pin", "zvideo"}
        if name == "contents":
            valid_types.add("question")
        content_type = values.get("content_type", "all")
        if not isinstance(content_type, str) or content_type not in valid_types:
            raise _invalid("无效的创作类型")
        values["content_type"] = content_type

    return values


def safe_json_numbers(value: Any) -> Any:
    """Convert integers unsafe for JavaScript JSON parsing into decimal strings."""

    if type(value) is int and abs(value) > 2**53 - 1:
        return str(value)
    if isinstance(value, list):
        return [safe_json_numbers(item) for item in value]
    if isinstance(value, dict):
        return {str(key): safe_json_numbers(item) for key, item in value.items()}
    return value


def tool_definitions() -> list[dict[str, Any]]:
    """Return public read-only tool metadata without credential state."""

    definitions: list[dict[str, Any]] = []
    for name, title in TOOL_TITLES.items():
        keys = {"query"} if name == "answer" else set(TOOL_SPECS[name].parameters)
        properties: dict[str, dict[str, str]] = {
            key: {"type": "integer" if key == "limit" else "string"}
            for key in sorted(keys)
        }
        required = (
            ["query"]
            if name in QUERY_TOOLS
            else ["url"]
            if name in URL_TOOLS
            else ["folder_id"]
            if name == "favlist_items"
            else []
        )
        description = f"{title}。只读查询，一次取一页，不自动遍历。"
        if name in {"detail", "comments", "stats", "content_stats"}:
            description += "仅限Access Secret所属账号本人，不能代查其他用户。"
        if name == "recommend":
            description += "用户指定主题则填query，否则不传，使用本人画像。"
        definitions.append(
            {
                "name": name,
                "title": title,
                "description": description,
                "input_schema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            }
        )
    return definitions


class ZhihuGateway:
    """Bounded official API client with no retries or persistent provider state."""

    def __init__(
        self,
        config: ZhihuConfig,
        access_secret: str | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(config.base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("zhihu.base_url must be an HTTPS origin")
        self.config = config
        self._base_url = config.base_url.rstrip("/")
        self._access_secret = access_secret
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._semaphore = asyncio.Semaphore(config.max_concurrency)
        self._request_count = 0
        self._failure_count = 0

    @property
    def configured(self) -> bool:
        return bool(self._access_secret)

    @property
    def running(self) -> bool:
        return self._client is not None

    async def start(self) -> None:
        if not self.config.enabled or self._client is not None:
            return
        self._client = httpx.AsyncClient(
            follow_redirects=False,
            transport=self._transport,
        )

    async def shutdown(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()

    def status(self) -> dict[str, Any]:
        enabled = self.config.enabled
        return {
            "enabled": enabled,
            "healthy": not enabled or self.running,
            "state": "disabled" if not enabled else ("running" if self.running else "stopped"),
            "configured": self.configured,
            "max_concurrency": self.config.max_concurrency,
            "requests": self._request_count,
            "failures": self._failure_count,
        }

    async def call(self, name: str, arguments: Any) -> dict[str, Any]:
        values = prepare_tool_arguments(name, arguments)
        if name == "answer":
            payload = {
                "model": "zhida-fast-1p5",
                "messages": [{"role": "user", "content": values["query"]}],
                "stream": False,
            }
            data = await self._request_json(
                "POST",
                "/v1/chat/completions",
                json_body=payload,
                timeout=self.config.answer_timeout_seconds,
            )
            try:
                result: Any = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                raise ZhihuGatewayError(
                    "protocol_error",
                    "知乎直答未返回有效答案",
                ) from None
            if not isinstance(result, str) or not result.strip():
                raise ZhihuGatewayError("protocol_error", "知乎直答未返回有效答案")
            normalized_data: Any = {"Text": result.strip()}
        else:
            spec = TOOL_SPECS[name]
            parameters = {spec.parameters[key]: value for key, value in values.items()}
            data = await self._request_json("GET", spec.path, params=parameters)
            self._raise_provider_code(data)
            normalized_data = data.get("Data", {})

        return {
            "tool": name,
            "title": TOOL_TITLES[name],
            "args": values,
            "data": safe_json_numbers(normalized_data),
            "retrieved_at": int(time.time()),
            "source": "知乎官方 API",
            "identity": "Access Secret 所属账号",
            "summary_only": name
            in {
                "search_zhihu",
                "search_global",
                "answers",
                "contents",
                "favorites",
                "favlist_items",
            },
        }

    async def quota(self) -> dict[str, Any]:
        """Verify credentials and read capability groups without business quota use."""

        data = await self._request_json("GET", "/api/v1/quota")
        self._raise_provider_code(data, quota=True)
        rows = data.get("Data", [])
        if not isinstance(rows, list):
            raise ZhihuGatewayError("protocol_error", "知乎额度响应格式异常")
        capabilities: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            api_id = row.get("APIID")
            if not isinstance(api_id, str) or api_id not in CAPABILITY_GROUPS:
                continue
            seen.add(api_id)
            fallback_name, components = CAPABILITY_GROUPS[api_id]
            total = _nonnegative_int(row.get("TotalQuota"))
            used = _nonnegative_int(row.get("TotalUsed"))
            remaining = _nonnegative_int(row.get("RemainingQuota"))
            capabilities.append(
                {
                    "id": api_id,
                    "name": str(row.get("APIName") or fallback_name),
                    "components": components,
                    "total": total,
                    "used": used,
                    "remaining": remaining,
                    "available": total > 0,
                    "exhausted": total > 0 and remaining <= 0,
                }
            )
        for api_id, (name, components) in CAPABILITY_GROUPS.items():
            if api_id not in seen:
                capabilities.append(
                    {
                        "id": api_id,
                        "name": name,
                        "components": components,
                        "total": 0,
                        "used": 0,
                        "remaining": 0,
                        "available": False,
                        "exhausted": False,
                    }
                )
        return {
            "verified": True,
            "checked_at": int(time.time()),
            "capabilities": safe_json_numbers(capabilities),
        }

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if not self.config.enabled:
            raise ZhihuGatewayError(
                "service_disabled",
                "知乎服务未启用",
                status_code=503,
            )
        if not self._access_secret:
            raise ZhihuGatewayError(
                "credential_not_configured",
                f"请在Server环境变量 {self.config.access_secret_env} 中配置知乎凭据",
                status_code=503,
            )
        client = self._client
        if client is None:
            raise ZhihuGatewayError(
                "service_unavailable",
                "知乎服务尚未启动",
                status_code=503,
            )
        headers = {
            "Authorization": f"Bearer {self._access_secret}",
            "X-Request-Timestamp": str(int(time.time())),
            "Content-Type": "application/json",
        }
        self._request_count += 1
        try:
            async with self._semaphore, client.stream(
                method,
                self._base_url + path,
                params=params,
                json=json_body,
                headers=headers,
                timeout=timeout or self.config.request_timeout_seconds,
            ) as response:
                if response.status_code != 200:
                    raise _http_error(response.status_code)
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > self.config.max_response_bytes:
                        raise ZhihuGatewayError(
                            "response_too_large",
                            "知乎响应过大，请减小查询范围",
                            status_code=502,
                        )
            decoded = json.loads(raw)
        except ZhihuGatewayError:
            self._failure_count += 1
            raise
        except (httpx.HTTPError, UnicodeDecodeError, json.JSONDecodeError):
            self._failure_count += 1
            raise ZhihuGatewayError(
                "network_error",
                "知乎请求超时、网络不可用或响应格式异常；未自动重试",
                status_code=502,
            ) from None
        if not isinstance(decoded, dict):
            self._failure_count += 1
            raise ZhihuGatewayError("protocol_error", "知乎响应格式异常")
        return decoded

    @staticmethod
    def _raise_provider_code(data: dict[str, Any], *, quota: bool = False) -> None:
        if data.get("Code") == 0:
            return
        code = str(data.get("Code", "UNKNOWN"))
        meanings = {
            "20001": "Access Secret无效或未授权",
            "10001": "参数错误、非本人内容或内容不可用",
            "30001": "频率或额度限制",
            "30002": "额度已用完",
            "30003": "风控拒绝",
            "90001": "服务异常",
        }
        status_code = 403 if code == "20001" else 429 if code.startswith("300") else 502
        prefix = "知乎鉴权失败" if quota and code == "20001" else "知乎请求失败"
        raise ZhihuGatewayError(
            f"provider_{code.lower()}",
            f"{prefix}：{meanings.get(code, '服务异常')}（{code}）",
            status_code=status_code,
        )


def _nonnegative_int(value: Any) -> int:
    try:
        result = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(result, 0)


def _http_error(status_code: int) -> ZhihuGatewayError:
    public_status = 429 if status_code == 429 else 403 if status_code in {401, 403} else 502
    message = (
        "知乎请求频率受限，请稍后由用户手动重试"
        if status_code == 429
        else "知乎凭据无效或当前能力未授权"
        if status_code in {401, 403}
        else f"知乎返回HTTP {status_code}，请检查权限或稍后再试"
    )
    return ZhihuGatewayError(
        "upstream_http",
        message,
        status_code=public_status,
    )
