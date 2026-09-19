"""FastAPI control plane, event stream, static console and OTA endpoints."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import platform
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from ipaddress import ip_address
from pathlib import Path
from time import monotonic
from typing import Any, Literal, Protocol, cast
from urllib.parse import urlsplit
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import Response

from ..config import AppConfig
from ..dispatch.commands import DispatchRequestError
from ..message_bus import MessageBus
from ..messages import JsonValue, Message
from ..services.conversation_control import ConversationControlError
from ..storage.database import Database, sanitize_payload
from .mqtt_broker import EmbeddedMqttBroker, MqttBrokerError
from .zhihu import ZhihuGatewayError

ComponentStatusProvider = Callable[[], Mapping[str, Mapping[str, Any]]]


class DeviceReader(Protocol):
    async def list_devices(self) -> list[dict[str, Any]]: ...

    async def get_device(self, device_id: str) -> dict[str, Any] | None: ...

    async def get_actions(
        self,
        device_id: str,
    ) -> list[dict[str, JsonValue]] | None: ...


class DeviceVerificationReader(Protocol):
    async def verify(self, device_id: str) -> dict[str, Any] | None: ...


class CommandDispatchReader(Protocol):
    async def submit_action(
        self,
        *,
        device_id: str,
        action: str,
        parameters: Mapping[str, JsonValue] | None,
        confirmation: bool,
        source: str = "webui",
        command_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]: ...

    async def submit_stop(
        self,
        *,
        device_id: str,
        source: str = "webui",
        command_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]: ...

    async def stop_cluster(self, *, source: str = "webui") -> dict[str, Any]: ...

    async def get_command(self, command_id: str) -> dict[str, Any] | None: ...


class ConversationControlReader(Protocol):
    async def control(
        self,
        *,
        device_id: str,
        command: Literal["start", "stop"],
        source: str = "webui",
        correlation_id: str | None = None,
    ) -> dict[str, Any]: ...


class DeviceWebsocketHandler(Protocol):
    async def handle(self, websocket: WebSocket) -> None: ...


class ZhihuReader(Protocol):
    def status(self) -> dict[str, Any]: ...

    def tools(self) -> dict[str, Any]: ...

    async def persona(self) -> dict[str, str]: ...

    async def save_persona(self, values: dict[str, str]) -> dict[str, str]: ...

    async def probe(self) -> dict[str, Any]: ...

    async def query(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]: ...

    async def events(self, *, after: int = 0, limit: int = 64) -> dict[str, Any]: ...


class NarrationReader(Protocol):
    async def narrate(
        self,
        device_id: str,
        text: str,
        *,
        correlation_id: str | None = None,
    ) -> dict[str, Any]: ...


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _optional_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized[:limit] if normalized else None


def _is_loopback(host: str) -> bool:
    return host.lower() in {"127.0.0.1", "::1", "localhost"}


def _package_version() -> str:
    try:
        return version("otto-master")
    except PackageNotFoundError:
        return "0.0.0+unknown"


class ApiError(Exception):
    """Stable public API error without internal traceback details."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class DevicePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=80)
    enabled: bool | None = None


class ActionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=12, max_length=17)
    action: str = Field(min_length=1, max_length=80)
    parameters: dict[str, Any] = Field(default_factory=dict, max_length=32)
    confirmation: bool = False


class BatchActionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_ids: list[str] = Field(min_length=1, max_length=16)
    action: str = Field(min_length=1, max_length=80)
    parameters: dict[str, Any] = Field(default_factory=dict, max_length=32)
    confirmation: bool = False


class BatchStopCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_ids: list[str] = Field(min_length=1, max_length=16)
    confirmation: bool = False


class BatchConversationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_ids: list[str] = Field(min_length=1, max_length=16)
    command: Literal["start", "stop"]
    confirmation: bool = False


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logging_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] | None = None
    discovery_enabled: bool | None = None


class ProvisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mac: str = Field(min_length=12, max_length=17)
    name: str | None = Field(default=None, min_length=1, max_length=80)


class ZhihuQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1, max_length=40)
    arguments: dict[str, Any] = Field(default_factory=dict, max_length=16)


class ZhihuPersonaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="奶龙", max_length=80)
    persona: str = Field(default="", max_length=4_000)
    memory: str = Field(default="", max_length=4_000)
    interests: str = Field(default="", max_length=2_000)


class ZhihuNarrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(pattern=r"^[0-9a-f]{12}$")
    text: str = Field(min_length=1, max_length=4_000)


@dataclass(slots=True)
class EventSubscription:
    subscription_id: str
    initial_frame: dict[str, Any]
    queue: asyncio.Queue[dict[str, Any]]


@dataclass(slots=True)
class _ConversationLane:
    device_id: str
    session_id: str | None = None
    utterance_id: str | None = None
    state: str = "waiting"
    user_partial: str = ""
    user_text: str = ""
    assistant_text: str = ""
    tool_status: str | None = None
    tool_name: str | None = None
    action: str | None = None
    command_id: str | None = None
    error_code: str | None = None
    reason: str | None = None
    last_topic: str | None = None
    updated_at: str | None = None

    def reset_turn(self, *, session_id: str, utterance_id: str | None) -> None:
        self.session_id = session_id
        self.utterance_id = utterance_id
        self.user_partial = ""
        self.user_text = ""
        self.assistant_text = ""
        self.tool_status = None
        self.tool_name = None
        self.action = None
        self.command_id = None
        self.error_code = None
        self.reason = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "session_id": self.session_id,
            "utterance_id": self.utterance_id,
            "state": self.state,
            "user_partial": self.user_partial,
            "user_text": self.user_text,
            "assistant_text": self.assistant_text,
            "tool_status": self.tool_status,
            "tool_name": self.tool_name,
            "action": self.action,
            "command_id": self.command_id,
            "error_code": self.error_code,
            "reason": self.reason,
            "last_topic": self.last_topic,
            "updated_at": self.updated_at,
        }


_CONVERSATION_TOPICS = frozenset(
    {
        "audio.input.started",
        "voice.session.state.changed",
        "voice.session.closed",
        "voice.transcription.partial",
        "voice.transcription.completed",
        "voice.transcription.displayed",
        "voice.transcription.failed",
        "tts.synthesis.started",
        "tts.playback.failed",
        "voice.tool.requested",
        "voice.tool.completed",
        "voice.tool.failed",
    }
)


class ConversationProjection:
    """Retain a bounded, redacted current conversation lane per device."""

    def __init__(self, *, max_devices: int = 128, text_limit: int = 512) -> None:
        if max_devices < 1 or text_limit < 16:
            raise ValueError("conversation projection limits must be positive")
        self.max_devices = max_devices
        self.text_limit = text_limit
        self._lanes: dict[str, _ConversationLane] = {}

    def apply(self, message: Message, payload: Mapping[str, Any]) -> None:
        if message.topic not in _CONVERSATION_TOPICS:
            return
        device_id = payload.get("device_id")
        if not isinstance(device_id, str) or not device_id:
            return
        lane = self._lanes.get(device_id)
        if lane is None:
            if len(self._lanes) >= self.max_devices:
                oldest = min(
                    self._lanes.values(),
                    key=lambda item: item.updated_at or "",
                )
                self._lanes.pop(oldest.device_id, None)
            lane = _ConversationLane(device_id=device_id)
            self._lanes[device_id] = lane

        session_id = _optional_text(payload.get("session_id"), 128)
        utterance_id = _optional_text(payload.get("utterance_id"), 128)
        if message.topic == "audio.input.started" and session_id is not None:
            if lane.session_id != session_id:
                lane.reset_turn(session_id=session_id, utterance_id=utterance_id)
            elif lane.utterance_id != utterance_id:
                lane.utterance_id = utterance_id
                lane.user_partial = ""
                lane.error_code = None
                lane.reason = None
            lane.state = "starting"
        elif session_id is not None:
            if lane.session_id is None:
                lane.reset_turn(session_id=session_id, utterance_id=utterance_id)
            elif lane.session_id != session_id:
                return
            elif utterance_id is not None:
                if lane.utterance_id not in {None, utterance_id}:
                    return
                lane.utterance_id = utterance_id

        topic = message.topic
        if topic == "voice.session.state.changed":
            state = _optional_text(payload.get("state"), 64)
            if state is not None:
                lane.state = state
            lane.error_code = _optional_text(payload.get("error_code"), 128)
            lane.reason = _optional_text(payload.get("reason"), 128)
        elif topic == "voice.session.closed":
            lane.state = "waiting"
            lane.reason = _optional_text(payload.get("reason"), 128)
        elif topic == "voice.transcription.partial":
            text = _optional_text(payload.get("text"), self.text_limit)
            if text is not None:
                if not lane.user_partial:
                    lane.user_text = ""
                    lane.assistant_text = ""
                    lane.tool_status = None
                    lane.tool_name = None
                    lane.action = None
                    lane.command_id = None
                lane.user_partial = text
        elif topic in {"voice.transcription.completed", "voice.transcription.displayed"}:
            text = _optional_text(payload.get("text"), self.text_limit)
            if text is not None:
                lane.user_text = text
                lane.user_partial = ""
                lane.assistant_text = ""
                lane.tool_status = None
                lane.tool_name = None
                lane.action = None
                lane.command_id = None
                lane.error_code = None
        elif topic == "voice.transcription.failed":
            lane.error_code = _optional_text(payload.get("error_code"), 128)
        elif topic == "tts.synthesis.started":
            text = _optional_text(payload.get("text"), self.text_limit)
            if text is not None:
                combined = f"{lane.assistant_text}{text}"
                lane.assistant_text = combined[-self.text_limit :]
        elif topic == "tts.playback.failed":
            lane.error_code = _optional_text(payload.get("error_code"), 128)
        elif topic.startswith("voice.tool."):
            lane.tool_status = topic.rsplit(".", 1)[-1]
            lane.tool_name = _optional_text(payload.get("tool_name"), 128)
            lane.action = _optional_text(payload.get("action"), 80)
            lane.command_id = _optional_text(payload.get("command_id"), 128)
            lane.error_code = _optional_text(payload.get("error_code"), 128)

        lane.last_topic = topic
        lane.updated_at = message.created_at.isoformat().replace("+00:00", "Z")

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            lane.to_dict()
            for lane in sorted(
                self._lanes.values(),
                key=lambda item: (item.updated_at or "", item.device_id),
                reverse=True,
            )
        ]


class EventHub:
    """Bounded in-memory event stream with process identity and cursor recovery."""

    def __init__(
        self,
        *,
        history_size: int = 256,
        subscriber_queue_size: int = 64,
        conversation_device_limit: int = 128,
    ) -> None:
        if history_size < 1 or subscriber_queue_size < 1:
            raise ValueError("event history and subscriber queues must be non-empty")
        self.stream_id = str(uuid4())
        self._history: deque[dict[str, Any]] = deque(maxlen=history_size)
        self._subscriber_queue_size = subscriber_queue_size
        self._subscribers: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._stalled_subscribers: set[str] = set()
        self._cursor = 0
        self._lock = asyncio.Lock()
        self._conversations = ConversationProjection(max_devices=conversation_device_limit)

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def publish(self, message: Message) -> None:
        event = message.to_dict()
        sanitized_payload = sanitize_payload(message.payload)
        event["payload"] = sanitized_payload
        async with self._lock:
            if isinstance(sanitized_payload, Mapping):
                self._conversations.apply(message, sanitized_payload)
            self._cursor += 1
            frame = {
                "type": "event",
                "stream_id": self.stream_id,
                "cursor": self._cursor,
                "event": event,
            }
            self._history.append(frame)
            for subscription_id, queue in self._subscribers.items():
                if subscription_id in self._stalled_subscribers:
                    continue
                try:
                    queue.put_nowait(frame)
                except asyncio.QueueFull:
                    while not queue.empty():
                        queue.get_nowait()
                    queue.put_nowait(
                        {
                            "type": "resync_required",
                            "stream_id": self.stream_id,
                            "cursor": self._cursor,
                            "reason": "subscriber_backpressure",
                        }
                    )
                    self._stalled_subscribers.add(subscription_id)

    async def conversation_snapshot(self) -> list[dict[str, Any]]:
        async with self._lock:
            return self._conversations.snapshot()

    async def read(
        self,
        *,
        after: int | None = None,
        stream_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        async with self._lock:
            frames, resumed, reason = self._select_frames(after, stream_id, limit)
            response_cursor = (
                int(frames[-1]["cursor"])
                if frames
                else (after if resumed and after is not None else self._cursor)
            )
            return {
                "stream_id": self.stream_id,
                "cursor": response_cursor,
                "latest_cursor": self._cursor,
                "has_more": response_cursor < self._cursor,
                "resumed": resumed,
                "resync_reason": reason,
                "items": frames,
            }

    async def subscribe(
        self,
        *,
        after: int | None = None,
        stream_id: str | None = None,
    ) -> EventSubscription:
        async with self._lock:
            subscription_id = str(uuid4())
            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
                maxsize=self._subscriber_queue_size
            )
            self._subscribers[subscription_id] = queue
            frames, resumed, reason = self._select_frames(
                after,
                stream_id,
                len(self._history) or 1,
            )
            initial = {
                "type": "snapshot",
                "stream_id": self.stream_id,
                "cursor": self._cursor,
                "resumed": resumed,
                "resync_reason": reason,
                "events": frames,
            }
            return EventSubscription(subscription_id, initial, queue)

    async def unsubscribe(self, subscription_id: str) -> None:
        async with self._lock:
            self._subscribers.pop(subscription_id, None)
            self._stalled_subscribers.discard(subscription_id)

    def _select_frames(
        self,
        after: int | None,
        stream_id: str | None,
        limit: int,
    ) -> tuple[list[dict[str, Any]], bool, str | None]:
        history = list(self._history)
        if after is None or stream_id != self.stream_id:
            reason = None if after is None else "stream_changed"
            return history[-limit:], False, reason
        if after < 0 or after > self._cursor:
            return history[-limit:], False, "invalid_cursor"
        oldest = int(history[0]["cursor"]) if history else self._cursor + 1
        if after < oldest - 1:
            return history[-limit:], False, "cursor_expired"
        frames = [frame for frame in history if int(frame["cursor"]) > after]
        return frames[:limit], True, None


class FirmwareService:
    def __init__(self, config: AppConfig) -> None:
        self.enabled = config.ota.enabled
        self.path = config.resolve_path(config.ota.firmware_path)
        self.version = config.ota.firmware_version
        self.target_hardware = config.ota.target_hardware

    async def status(self) -> dict[str, Any]:
        if not self.enabled:
            return self._missing("disabled")
        if not self.path.is_file():
            return self._missing("file_missing")
        return await asyncio.to_thread(self._inspect)

    def _missing(self, reason: str) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "available": False,
            "reason": reason,
            "version": self.version,
            "target_hardware": self.target_hardware,
            "size": None,
            "sha256": None,
            "download_url": None,
        }

    def _inspect(self) -> dict[str, Any]:
        digest = hashlib.sha256()
        with self.path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        stat = self.path.stat()
        return {
            "enabled": True,
            "available": True,
            "reason": None,
            "version": self.version,
            "target_hardware": self.target_hardware,
            "size": stat.st_size,
            "sha256": digest.hexdigest(),
            "last_modified": datetime.fromtimestamp(stat.st_mtime, UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "download_url": "/api/v1/firmware/download",
        }


@dataclass(slots=True)
class WebContext:
    config: AppConfig
    database: Database
    message_bus: MessageBus
    mqtt_broker: EmbeddedMqttBroker
    events: EventHub
    devices: DeviceReader | None
    verifier: DeviceVerificationReader | None
    dispatcher: CommandDispatchReader | None
    component_status: ComponentStatusProvider
    started_at: datetime
    started_monotonic: float
    device_websocket: DeviceWebsocketHandler | None = None
    conversation_control: ConversationControlReader | None = None
    zhihu: ZhihuReader | None = None
    narrator: NarrationReader | None = None


def _correlation_id(request: Request) -> str:
    value = getattr(request.state, "correlation_id", None)
    return value if isinstance(value, str) else str(uuid4())


def _error_payload(code: str, message: str, correlation_id: str) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "correlation_id": correlation_id,
        }
    }


def _provided_bearer(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return request.headers.get("x-otto-token")


def _validate_origin(
    origin: str | None,
    allowed_origins: tuple[str, ...],
    host: str | None,
    *,
    allow_same_host: bool,
) -> bool:
    if origin is None or origin in allowed_origins:
        return True
    parsed = urlsplit(origin)
    if not (
        allow_same_host
        and parsed.scheme in {"http", "https"}
        and parsed.netloc == host
    ):
        return False
    hostname = parsed.hostname
    if hostname is None:
        return False
    normalized = hostname.lower().rstrip(".")
    if _is_loopback(normalized) or normalized.endswith(".local"):
        return True
    try:
        address = ip_address(normalized)
    except ValueError:
        allowed_hosts = {
            candidate.hostname.lower().rstrip(".")
            for value in allowed_origins
            if (candidate := urlsplit(value)).hostname is not None
        }
        return normalized in allowed_hosts
    return address.is_private or address.is_loopback or address.is_link_local


def _require_console_access(request: Request, config: AppConfig) -> None:
    if not _validate_origin(
        request.headers.get("origin"),
        config.server.allowed_origins,
        request.headers.get("host"),
        allow_same_host=True,
    ):
        raise ApiError(403, "origin_denied", "request origin is not allowed")
    if not config.server.console_auth_required:
        return
    expected = config.secrets.console_token
    if expected is None:
        raise ApiError(
            503,
            "console_auth_not_configured",
            f"set {config.server.console_token_env} when console authentication is required",
        )
    provided = _provided_bearer(request)
    if provided is None or not hmac.compare_digest(provided, expected):
        raise ApiError(401, "authentication_required", "a valid console token is required")


def _require_provisioning_access(request: Request, config: AppConfig) -> None:
    expected = config.secrets.provisioning_token
    if expected is None:
        if _is_loopback(config.server.host):
            return
        raise ApiError(
            503,
            "provisioning_disabled",
            f"set {config.ota.provisioning_token_env} before provisioning over the LAN",
        )
    provided = request.headers.get("x-otto-provisioning-token")
    if provided is None or not hmac.compare_digest(provided, expected):
        raise ApiError(401, "provisioning_auth_required", "a valid provisioning token is required")


def _component_not_ready(name: str) -> ApiError:
    return ApiError(503, "component_not_ready", f"{name} is scheduled for a later phase")


def _dispatch_api_error(exc: DispatchRequestError) -> ApiError:
    if exc.code == "device_not_found":
        status_code = 404
    elif exc.code in {
        "invalid_action",
        "invalid_confirmation",
        "invalid_command",
        "invalid_device_id",
        "invalid_parameters",
        "target_mismatch",
        "action_not_supported",
        "confirmation_required",
    }:
        status_code = 422
    elif exc.code in {
        "capability_missing",
        "command_conflict",
        "device_disabled",
        "device_not_online",
        "device_queue_full",
        "device_state_unsafe",
        "duplicate_submission",
        "transport_unavailable",
    }:
        status_code = 409
    else:
        status_code = 503
    return ApiError(status_code, exc.code, exc.message)


def _zhihu_api_error(exc: ZhihuGatewayError) -> ApiError:
    return ApiError(exc.status_code, f"zhihu_{exc.code}", exc.message)


def _batch_device_ids(values: list[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        if (
            not isinstance(value, str)
            or len(value) != 12
            or value != value.lower()
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ApiError(
                422,
                "invalid_device_ids",
                "batch targets must be lowercase 12-character device IDs",
            )
        normalized.append(value)
    if len(normalized) != len(set(normalized)):
        raise ApiError(422, "duplicate_device_ids", "batch targets must be unique")
    return tuple(normalized)


async def _submit_batch_action(
    dispatcher: CommandDispatchReader,
    device_id: str,
    payload: BatchActionCommand,
    correlation_id: str,
) -> dict[str, Any]:
    try:
        command = await dispatcher.submit_action(
            device_id=device_id,
            action=payload.action,
            parameters=cast(Mapping[str, JsonValue], payload.parameters),
            confirmation=True,
            source="webui:batch",
            correlation_id=correlation_id,
        )
    except DispatchRequestError as exc:
        return {
            "device_id": device_id,
            "accepted": False,
            "error": {"code": exc.code, "message": exc.message},
        }
    except Exception:  # noqa: BLE001 - do not leak implementation details in batch results
        return {
            "device_id": device_id,
            "accepted": False,
            "error": {"code": "dispatch_failed", "message": "action dispatch failed"},
        }
    return {
        "device_id": device_id,
        "accepted": True,
        "command_id": command.get("command_id"),
        "status": command.get("status"),
    }


async def _submit_batch_stop(
    dispatcher: CommandDispatchReader,
    device_id: str,
    correlation_id: str,
) -> dict[str, Any]:
    try:
        command = await dispatcher.submit_stop(
            device_id=device_id,
            source="webui:batch",
            correlation_id=correlation_id,
        )
    except DispatchRequestError as exc:
        return {
            "device_id": device_id,
            "accepted": False,
            "error": {"code": exc.code, "message": exc.message},
        }
    except Exception:  # noqa: BLE001 - do not leak implementation details in batch results
        return {
            "device_id": device_id,
            "accepted": False,
            "error": {"code": "dispatch_failed", "message": "stop dispatch failed"},
        }
    return {
        "device_id": device_id,
        "accepted": True,
        "command_id": command.get("command_id"),
        "status": command.get("status"),
    }


async def _submit_batch_conversation(
    controller: ConversationControlReader,
    device_id: str,
    command: Literal["start", "stop"],
    correlation_id: str,
) -> dict[str, Any]:
    try:
        result = await controller.control(
            device_id=device_id,
            command=command,
            source="webui:batch",
            correlation_id=correlation_id,
        )
    except ConversationControlError as exc:
        return {
            "device_id": device_id,
            "accepted": False,
            "error": {"code": exc.code, "message": exc.message},
        }
    except Exception:  # noqa: BLE001 - batch API must not leak implementation details
        return {
            "device_id": device_id,
            "accepted": False,
            "error": {
                "code": "conversation_control_failed",
                "message": "conversation control failed",
            },
        }
    return {
        "device_id": device_id,
        "accepted": True,
        "command_id": result.get("command_id"),
        "status": result.get("status"),
        "transport": result.get("transport"),
    }


def create_app(context: WebContext) -> FastAPI:
    """Create the control-plane ASGI app without starting a network listener."""

    app = FastAPI(
        title="Otto Master",
        version=_package_version(),
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/v1/openapi.json",
        debug=False,
    )
    firmware = FirmwareService(context.config)

    @app.middleware("http")
    async def correlation_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        requested = request.headers.get("x-correlation-id", "").strip()
        request.state.correlation_id = requested[:128] if requested else str(uuid4())
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = request.state.correlation_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; connect-src 'self' ws: wss:; "
            "img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'",
        )
        return response

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(exc.code, exc.message, _correlation_id(request)),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        fields = [".".join(str(part) for part in error["loc"]) for error in exc.errors()]
        message = "request validation failed"
        if fields:
            message = f"request validation failed for: {', '.join(fields)}"
        return JSONResponse(
            status_code=422,
            content=_error_payload("validation_error", message, _correlation_id(request)),
        )

    @app.get("/api/v1/health")
    async def health() -> dict[str, Any]:
        components = {name: dict(value) for name, value in context.component_status().items()}
        unhealthy = [
            name
            for name, value in components.items()
            if value.get("enabled", True) and not value.get("healthy", False)
        ]
        return {
            "status": "healthy" if not unhealthy else "degraded",
            "version": _package_version(),
            "checked_at": _now(),
            "unhealthy_components": unhealthy,
            "components": components,
        }

    @app.get("/api/v1/system/status")
    async def system_status() -> dict[str, Any]:
        advertised_host = context.config.discovery.hostname.rstrip(".")
        web_host = (
            "127.0.0.1" if context.config.server.host in {"0.0.0.0", "::"} else context.config.server.host
        )
        return {
            "state": "running",
            "project": context.config.project.name,
            "version": _package_version(),
            "started_at": context.started_at.isoformat().replace("+00:00", "Z"),
            "uptime_seconds": round(monotonic() - context.started_monotonic, 3),
            "current_time": _now(),
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "python": platform.python_version(),
            },
            "web_url": f"http://{web_host}:{context.config.server.port}",
            "discovery_hostname": advertised_host,
            "http_port": context.config.server.port,
            "mqtt_port": context.config.mqtt.port,
            "console_auth_required": context.config.server.console_auth_required,
            "console_auth_configured": context.config.secrets.console_token is not None,
        }

    @app.get("/api/v1/mqtt/status")
    async def mqtt_status() -> dict[str, Any]:
        return context.mqtt_broker.status()

    @app.get("/api/v1/devices")
    async def list_devices(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        if context.devices is None:
            items = await context.database.list_devices(limit=limit, offset=offset)
            return {"items": items, "limit": limit, "offset": offset}
        all_items = await context.devices.list_devices()
        return {
            "items": all_items[offset : offset + limit],
            "limit": limit,
            "offset": offset,
            "total": len(all_items),
        }

    @app.get("/api/v1/devices/{device_id}")
    async def get_device(device_id: str) -> dict[str, Any]:
        device = (
            await context.devices.get_device(device_id)
            if context.devices is not None
            else await context.database.fetch_device(device_id)
        )
        if device is None:
            raise ApiError(404, "device_not_found", "device does not exist")
        return device

    @app.patch("/api/v1/devices/{device_id}")
    async def patch_device(
        device_id: str,
        payload: DevicePatch,
        request: Request,
    ) -> dict[str, Any]:
        del device_id, payload
        _require_console_access(request, context.config)
        raise _component_not_ready("device manager")

    @app.get("/api/v1/devices/{device_id}/actions")
    async def device_actions(device_id: str) -> dict[str, Any]:
        if context.devices is not None:
            actions = await context.devices.get_actions(device_id)
            if actions is None:
                raise ApiError(404, "device_not_found", "device does not exist")
        else:
            device = await context.database.fetch_device(device_id)
            if device is None:
                raise ApiError(404, "device_not_found", "device does not exist")
            actions = await context.database.fetch_device_actions(device_id)
        return {"device_id": device_id, "items": actions, "count": len(actions)}

    @app.get("/api/v1/conversations")
    async def list_conversations(request: Request) -> dict[str, Any]:
        # Transcripts are not credentials, but they are private user content.
        # Keep them behind the same console boundary as the live event stream.
        _require_console_access(request, context.config)
        projected = {
            item["device_id"]: item for item in await context.events.conversation_snapshot()
        }
        if context.devices is not None:
            devices = await context.devices.list_devices()
        else:
            devices = await context.database.list_devices(limit=500, offset=0)
        components = context.component_status()
        voice = components.get("voice_mvp", {})
        runtime_sessions = voice.get("sessions", {})
        if not isinstance(runtime_sessions, Mapping):
            runtime_sessions = {}

        items: list[dict[str, Any]] = []
        all_device_ids = {str(item.get("device_id")) for item in devices if item.get("device_id")}
        all_device_ids.update(projected)
        all_device_ids.update(
            str(device_id) for device_id in runtime_sessions if isinstance(device_id, str)
        )
        devices_by_id = {
            str(item.get("device_id")): item for item in devices if item.get("device_id")
        }
        for device_id in all_device_ids:
            device = devices_by_id.get(device_id, {})
            lane: dict[str, Any] = {
                "device_id": device_id,
                "session_id": None,
                "utterance_id": None,
                "state": "waiting",
                "user_partial": "",
                "user_text": "",
                "assistant_text": "",
                "tool_status": None,
                "tool_name": None,
                "action": None,
                "command_id": None,
                "error_code": None,
                "reason": None,
                "last_topic": None,
                "updated_at": None,
            }
            lane.update(projected.get(device_id, {}))
            runtime_session = runtime_sessions.get(device_id)
            if isinstance(runtime_session, Mapping):
                runtime_state = _optional_text(runtime_session.get("state"), 64)
                if runtime_state is not None:
                    lane["state"] = runtime_state
                runtime_session_id = _optional_text(runtime_session.get("session_id"), 128)
                if runtime_session_id is not None:
                    lane["session_id"] = runtime_session_id
                failure_code = _optional_text(runtime_session.get("failure_code"), 128)
                if failure_code is not None:
                    lane["error_code"] = failure_code
                exit_reason = _optional_text(runtime_session.get("exit_reason"), 128)
                if exit_reason is not None:
                    lane["reason"] = exit_reason
                lane["round_id"] = runtime_session.get("round_id")
                lane["turn_count"] = runtime_session.get("turn_count", 0)
                lane["speech_detected"] = runtime_session.get("speech_detected", False)
            lane.update(
                {
                    "name": device.get("name") or device_id,
                    "device_status": device.get("status", "unknown"),
                    "transport": device.get("transport"),
                    "action_state": device.get("action_state", "unknown"),
                }
            )
            items.append(lane)
        items.sort(key=lambda item: (str(item.get("name", "")), item["device_id"]))
        active_states = {"laughing", "listening", "recognizing", "answering"}
        return {
            "items": items,
            "count": len(items),
            "active_count": sum(item["state"] in active_states for item in items),
            "stream_id": context.events.stream_id,
            "cursor": context.events.cursor,
        }

    @app.post("/api/v1/devices/{device_id}/verify")
    async def verify_device(device_id: str, request: Request) -> dict[str, Any]:
        _require_console_access(request, context.config)
        if context.verifier is None:
            raise _component_not_ready("device verification")
        report = await context.verifier.verify(device_id)
        if report is None:
            raise ApiError(404, "device_not_found", "device does not exist")
        return report

    @app.post("/api/v1/commands/action")
    async def command_action(payload: ActionCommand, request: Request) -> JSONResponse:
        _require_console_access(request, context.config)
        if context.dispatcher is None:
            raise _component_not_ready("dispatcher")
        try:
            command = await context.dispatcher.submit_action(
                device_id=payload.device_id,
                action=payload.action,
                parameters=cast(Mapping[str, JsonValue], payload.parameters),
                confirmation=payload.confirmation,
                correlation_id=_correlation_id(request),
            )
        except DispatchRequestError as exc:
            raise _dispatch_api_error(exc) from exc
        return JSONResponse(status_code=202, content=command)

    @app.post("/api/v1/commands/actions/batch")
    async def command_actions_batch(
        payload: BatchActionCommand,
        request: Request,
    ) -> JSONResponse:
        _require_console_access(request, context.config)
        if context.dispatcher is None:
            raise _component_not_ready("dispatcher")
        if payload.confirmation is not True:
            raise ApiError(422, "confirmation_required", "batch action requires confirmation")
        device_ids = _batch_device_ids(payload.device_ids)
        batch_id = _correlation_id(request)
        items = await asyncio.gather(
            *(
                _submit_batch_action(context.dispatcher, device_id, payload, batch_id)
                for device_id in device_ids
            )
        )
        accepted = sum(item["accepted"] is True for item in items)
        return JSONResponse(
            status_code=202,
            content={
                "batch_id": batch_id,
                "requested": len(items),
                "accepted": accepted,
                "failed": len(items) - accepted,
                "items": items,
            },
        )

    @app.get("/api/v1/commands/{command_id}")
    async def get_command(command_id: str) -> dict[str, Any]:
        if context.dispatcher is None:
            raise _component_not_ready("command repository")
        command = await context.dispatcher.get_command(command_id)
        if command is None:
            raise ApiError(404, "command_not_found", "command does not exist")
        return command

    @app.post("/api/v1/devices/{device_id}/stop")
    async def stop_device(device_id: str, request: Request) -> JSONResponse:
        _require_console_access(request, context.config)
        if context.dispatcher is None:
            raise _component_not_ready("dispatcher")
        try:
            command = await context.dispatcher.submit_stop(
                device_id=device_id,
                correlation_id=_correlation_id(request),
            )
        except DispatchRequestError as exc:
            raise _dispatch_api_error(exc) from exc
        return JSONResponse(status_code=202, content=command)

    @app.post("/api/v1/commands/stops/batch")
    async def command_stops_batch(
        payload: BatchStopCommand,
        request: Request,
    ) -> JSONResponse:
        _require_console_access(request, context.config)
        if context.dispatcher is None:
            raise _component_not_ready("dispatcher")
        if payload.confirmation is not True:
            raise ApiError(422, "confirmation_required", "batch stop requires confirmation")
        device_ids = _batch_device_ids(payload.device_ids)
        batch_id = _correlation_id(request)
        items = await asyncio.gather(
            *(
                _submit_batch_stop(context.dispatcher, device_id, batch_id)
                for device_id in device_ids
            )
        )
        accepted = sum(item["accepted"] is True for item in items)
        return JSONResponse(
            status_code=202,
            content={
                "batch_id": batch_id,
                "requested": len(items),
                "accepted": accepted,
                "failed": len(items) - accepted,
                "items": items,
            },
        )

    @app.post("/api/v1/commands/conversations/batch")
    async def command_conversations_batch(
        payload: BatchConversationCommand,
        request: Request,
    ) -> JSONResponse:
        _require_console_access(request, context.config)
        if context.conversation_control is None:
            raise _component_not_ready("conversation control")
        if payload.confirmation is not True:
            raise ApiError(
                422,
                "confirmation_required",
                "batch conversation control requires confirmation",
            )
        device_ids = _batch_device_ids(payload.device_ids)
        batch_id = _correlation_id(request)
        items = await asyncio.gather(
            *(
                _submit_batch_conversation(
                    context.conversation_control,
                    device_id,
                    payload.command,
                    batch_id,
                )
                for device_id in device_ids
            )
        )
        accepted = sum(item["accepted"] is True for item in items)
        return JSONResponse(
            status_code=202,
            content={
                "batch_id": batch_id,
                "command": payload.command,
                "requested": len(items),
                "accepted": accepted,
                "failed": len(items) - accepted,
                "items": items,
            },
        )

    @app.post("/api/v1/cluster/stop")
    async def stop_cluster(request: Request) -> JSONResponse:
        _require_console_access(request, context.config)
        if context.dispatcher is None:
            raise _component_not_ready("dispatcher")
        try:
            result = await context.dispatcher.stop_cluster()
        except DispatchRequestError as exc:
            raise _dispatch_api_error(exc) from exc
        return JSONResponse(status_code=202, content=result)

    @app.get("/api/v1/events")
    async def list_events(
        after: int | None = Query(default=None, ge=0),
        stream_id: str | None = None,
        limit: int = Query(default=100, ge=1, le=256),
    ) -> dict[str, Any]:
        return await context.events.read(after=after, stream_id=stream_id, limit=limit)

    @app.get("/api/v1/settings")
    async def get_settings() -> dict[str, Any]:
        stored_settings = await context.database.load_settings()
        safe_override_keys = {"console.discovery_enabled", "console.logging_level"}
        stored = {
            key: value for key, value in stored_settings.items() if key in safe_override_keys
        }
        return {
            "server": {
                "host": context.config.server.host,
                "port": context.config.server.port,
                "console_auth_required": context.config.server.console_auth_required,
                "console_auth_configured": context.config.secrets.console_token is not None,
            },
            "mqtt": {
                "enabled": context.config.mqtt.enabled,
                "host": context.config.mqtt.host,
                "port": context.config.mqtt.port,
                "authentication": "required",
                "acl": "per-device",
            },
            "discovery": {
                "enabled": context.config.discovery.enabled,
                "hostname": context.config.discovery.hostname,
                "service_type": context.config.discovery.service_type,
            },
            "logging": {"level": context.config.logging.level},
            "stored_overrides": stored,
        }

    @app.put("/api/v1/settings")
    async def put_settings(payload: SettingsUpdate, request: Request) -> dict[str, Any]:
        _require_console_access(request, context.config)
        updates = payload.model_dump(exclude_none=True)
        if not updates:
            raise ApiError(400, "empty_update", "at least one setting must be supplied")
        for key, value in updates.items():
            await context.database.save_setting(f"console.{key}", value)
        return {
            "saved": updates,
            "applied": False,
            "effect": "server_restart_required",
            "saved_at": _now(),
        }

    @app.get("/api/v1/zhihu/status")
    async def zhihu_status(request: Request) -> dict[str, Any]:
        _require_console_access(request, context.config)
        if context.zhihu is None:
            raise _component_not_ready("zhihu")
        return context.zhihu.status()

    @app.get("/api/v1/zhihu/tools")
    async def zhihu_tools(request: Request) -> dict[str, Any]:
        _require_console_access(request, context.config)
        if context.zhihu is None:
            raise _component_not_ready("zhihu")
        return context.zhihu.tools()

    @app.get("/api/v1/zhihu/persona")
    async def zhihu_persona(request: Request) -> dict[str, str]:
        _require_console_access(request, context.config)
        if context.zhihu is None:
            raise _component_not_ready("zhihu")
        return await context.zhihu.persona()

    @app.put("/api/v1/zhihu/persona")
    async def put_zhihu_persona(
        payload: ZhihuPersonaUpdate,
        request: Request,
    ) -> dict[str, str]:
        _require_console_access(request, context.config)
        if context.zhihu is None:
            raise _component_not_ready("zhihu")
        return await context.zhihu.save_persona(payload.model_dump())

    @app.post("/api/v1/zhihu/probe")
    async def probe_zhihu(request: Request) -> dict[str, Any]:
        _require_console_access(request, context.config)
        if context.zhihu is None:
            raise _component_not_ready("zhihu")
        try:
            return await context.zhihu.probe()
        except ZhihuGatewayError as exc:
            raise _zhihu_api_error(exc) from exc

    @app.post("/api/v1/zhihu/query")
    async def query_zhihu(
        payload: ZhihuQueryRequest,
        request: Request,
    ) -> dict[str, Any]:
        _require_console_access(request, context.config)
        if context.zhihu is None:
            raise _component_not_ready("zhihu")
        try:
            return await context.zhihu.query(payload.tool, payload.arguments)
        except ZhihuGatewayError as exc:
            raise _zhihu_api_error(exc) from exc

    @app.get("/api/v1/zhihu/events")
    async def zhihu_events(
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=64, ge=1, le=128),
    ) -> dict[str, Any]:
        _require_console_access(request, context.config)
        if context.zhihu is None:
            raise _component_not_ready("zhihu")
        return await context.zhihu.events(after=after, limit=limit)

    @app.post("/api/v1/zhihu/narrate")
    async def narrate_zhihu(
        payload: ZhihuNarrationRequest,
        request: Request,
    ) -> dict[str, Any]:
        _require_console_access(request, context.config)
        if context.narrator is None:
            raise _component_not_ready("voice narration")
        try:
            return await context.narrator.narrate(
                payload.device_id,
                payload.text,
                correlation_id=_correlation_id(request),
            )
        except ValueError as exc:
            raise ApiError(422, "invalid_narration", str(exc)) from exc
        except RuntimeError as exc:
            raise ApiError(
                409,
                "narration_unavailable",
                "device narration is unavailable or already busy",
            ) from exc

    @app.get("/api/v1/firmware")
    async def firmware_status() -> dict[str, Any]:
        return await firmware.status()

    @app.get("/api/v1/firmware/download", response_class=FileResponse)
    async def firmware_download() -> FileResponse:
        status = await firmware.status()
        if not status["available"]:
            raise ApiError(404, "firmware_not_found", "firmware file is not available")
        return FileResponse(
            firmware.path,
            media_type="application/octet-stream",
            filename=firmware.path.name,
        )

    @app.get("/api/v1/ota/manifest")
    async def ota_manifest() -> dict[str, Any]:
        return {
            "protocol": "otto-ota/1",
            "firmware": await firmware.status(),
            "mqtt": {
                "endpoint": context.mqtt_broker.status()["public_endpoint"],
                "protocol": "mqtt-3.1.1",
                "authentication": "per-device",
                "publish_topic_template": "otto/v1/devices/{device_id}/up",
                "subscribe_topic_template": "otto/v1/devices/{device_id}/down",
            },
            "tcp": {
                "enabled": context.config.tcp.enabled,
                "endpoint": f"tcp://{context.config.discovery.hostname}:{context.config.tcp.port}",
                "protocol": "otto-master/1",
                "authentication": "per-device-token",
            },
            "websocket": {
                "enabled": context.config.device_websocket.enabled,
                "endpoint": (
                    f"ws://{context.config.discovery.hostname}:{context.config.server.port}"
                    f"{context.config.server.websocket_path}"
                ),
                "protocol": "xiaozhi-websocket-v1",
                "authentication": "bearer-device-token",
            },
        }

    @app.post("/api/v1/ota/provision")
    async def ota_provision(payload: ProvisionRequest, request: Request) -> JSONResponse:
        _require_provisioning_access(request, context.config)
        try:
            provisioned = await context.mqtt_broker.provision_device(payload.mac)
        except ValueError as exc:
            raise ApiError(422, "invalid_device_identity", str(exc)) from exc
        except MqttBrokerError as exc:
            raise ApiError(503, "mqtt_unavailable", str(exc)) from exc
        response = JSONResponse(
            {
                "protocol": "otto-provisioning/1",
                "device_id": provisioned.device_id,
                "mqtt": {
                    "endpoint": provisioned.endpoint,
                    "client_id": provisioned.client_id,
                    "username": provisioned.username,
                    "password": provisioned.password,
                    "publish_topic": provisioned.publish_topic,
                    "subscribe_topic": provisioned.subscribe_topic,
                },
                "tcp": {
                    "enabled": context.config.tcp.enabled,
                    "endpoint": (
                        f"tcp://{context.config.discovery.hostname}:{context.config.tcp.port}"
                    ),
                    "protocol": "otto-master/1",
                    "client_id": provisioned.client_id,
                    "token": provisioned.password,
                },
                "websocket": {
                    "enabled": context.config.device_websocket.enabled,
                    "endpoint": (
                        f"ws://{context.config.discovery.hostname}:{context.config.server.port}"
                        f"{context.config.server.websocket_path}"
                    ),
                    "protocol_version": 1,
                    "client_id": provisioned.client_id,
                    "token": provisioned.password,
                },
                "firmware": await firmware.status(),
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.websocket(context.config.server.websocket_path)
    async def device_websocket(websocket: WebSocket) -> None:
        if context.device_websocket is None:
            await websocket.close(code=1013, reason="device websocket unavailable")
            return
        await context.device_websocket.handle(websocket)

    @app.websocket("/api/v1/events/stream")
    async def event_stream(websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin")
        expected = context.config.secrets.console_token
        if not _validate_origin(
            origin,
            context.config.server.allowed_origins,
            websocket.headers.get("host"),
            allow_same_host=expected is not None,
        ):
            await websocket.close(code=4403, reason="origin denied")
            return
        protocols = [
            item.strip()
            for item in websocket.headers.get("sec-websocket-protocol", "").split(",")
            if item.strip()
        ]
        protocol_token = protocols[1] if len(protocols) > 1 and protocols[0] == "otto-console" else None
        authorization = websocket.headers.get("authorization", "")
        bearer = authorization[7:].strip() if authorization.lower().startswith("bearer ") else None
        provided = protocol_token or bearer
        if expected is None and not _is_loopback(context.config.server.host):
            await websocket.close(code=4403, reason="console authentication is not configured")
            return
        if expected is not None and (
            provided is None or not hmac.compare_digest(provided, expected)
        ):
            await websocket.close(code=4401, reason="authentication required")
            return
        accepted_protocol = "otto-console" if protocols and protocols[0] == "otto-console" else None
        await websocket.accept(subprotocol=accepted_protocol)
        after_value = websocket.query_params.get("after")
        try:
            after = int(after_value) if after_value is not None else None
        except ValueError:
            after = None
        subscription = await context.events.subscribe(
            after=after,
            stream_id=websocket.query_params.get("stream_id"),
        )
        receive_task: asyncio.Task[MutableMapping[str, Any]] | None = None
        try:
            await websocket.send_json(subscription.initial_frame)
            receive_task = asyncio.create_task(websocket.receive())
            while True:
                event_task = asyncio.create_task(subscription.queue.get())
                done, _ = await asyncio.wait(
                    {receive_task, event_task},
                    timeout=20,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    event_task.cancel()
                    await asyncio.gather(event_task, return_exceptions=True)
                    await websocket.send_json(
                        {
                            "type": "heartbeat",
                            "stream_id": context.events.stream_id,
                            "cursor": context.events.cursor,
                            "sent_at": _now(),
                        }
                    )
                    continue
                if receive_task in done:
                    received = receive_task.result()
                    if received["type"] == "websocket.disconnect":
                        event_task.cancel()
                        await asyncio.gather(event_task, return_exceptions=True)
                        return
                    receive_task = asyncio.create_task(websocket.receive())
                if event_task in done:
                    frame = event_task.result()
                    await websocket.send_json(frame)
                    if frame.get("type") == "resync_required":
                        await websocket.close(code=4409, reason="event resync required")
                        return
                else:
                    event_task.cancel()
                    await asyncio.gather(event_task, return_exceptions=True)
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            return
        finally:
            if receive_task is not None:
                receive_task.cancel()
                await asyncio.gather(receive_task, return_exceptions=True)
            await context.events.unsubscribe(subscription.subscription_id)

    static_path = Path(__file__).resolve().parents[1] / "web"
    app.mount(
        "/",
        StaticFiles(directory=static_path, html=True),
        name="webui",
    )

    return app


class WebGateway:
    """Own the ASGI app, MessageBus observer and uvicorn lifecycle."""

    def __init__(self, context: WebContext) -> None:
        self.context = context
        self.app = create_app(context)
        self._server: uvicorn.Server | None = None
        self._serve_task: asyncio.Task[None] | None = None
        self._observer_id: str | None = None
        self._running = False
        self._last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if not self.context.config.server.enabled or self._running:
            return
        self._observer_id = await self.context.message_bus.subscribe_observer(
            self.context.events.publish
        )
        uvicorn_config = uvicorn.Config(
            self.app,
            host=self.context.config.server.host,
            port=self.context.config.server.port,
            log_config=None,
            access_log=False,
            lifespan="off",
            timeout_graceful_shutdown=3,
        )
        server = uvicorn.Server(uvicorn_config)
        task = asyncio.create_task(server.serve(), name="otto-web-server")
        self._server = server
        self._serve_task = task
        try:
            for _ in range(500):
                if server.started:
                    self._running = True
                    return
                if task.done():
                    exception = task.exception()
                    if exception is not None:
                        raise exception
                    raise RuntimeError("web server exited before startup")
                await asyncio.sleep(0.01)
            raise TimeoutError("web server startup timed out")
        except (Exception, SystemExit) as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            await self.shutdown()
            if isinstance(exc, SystemExit):
                raise OSError("web server failed to bind its configured address") from exc
            raise

    async def shutdown(self) -> None:
        server = self._server
        task = self._serve_task
        self._server = None
        self._serve_task = None
        if server is not None:
            server.should_exit = True
        if task is not None:
            try:
                await asyncio.gather(task, return_exceptions=True)
            except asyncio.CancelledError:
                if server is not None:
                    server.force_exit = True
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
        if self._observer_id is not None:
            await self.context.message_bus.unsubscribe_observer(self._observer_id)
            self._observer_id = None
        self._running = False

    def status(self) -> dict[str, Any]:
        enabled = self.context.config.server.enabled
        return {
            "enabled": enabled,
            "healthy": self._running if enabled else True,
            "state": "running" if self._running else ("disabled" if not enabled else "stopped"),
            "host": self.context.config.server.host,
            "port": self.context.config.server.port,
            "event_subscribers": self.context.events.subscriber_count,
            "last_error": self._last_error,
        }
