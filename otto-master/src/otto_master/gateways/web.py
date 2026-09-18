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
from pathlib import Path
from time import monotonic
from typing import Any, Literal
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
from ..message_bus import MessageBus
from ..messages import Message
from ..storage.database import Database, sanitize_payload
from .mqtt_broker import EmbeddedMqttBroker, MqttBrokerError

ComponentStatusProvider = Callable[[], Mapping[str, Mapping[str, Any]]]


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


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
    parameters: dict[str, Any] = Field(default_factory=dict)
    confirmation: bool = False


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logging_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] | None = None
    discovery_enabled: bool | None = None


class ProvisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mac: str = Field(min_length=12, max_length=17)
    name: str | None = Field(default=None, min_length=1, max_length=80)


@dataclass(slots=True)
class EventSubscription:
    subscription_id: str
    initial_frame: dict[str, Any]
    queue: asyncio.Queue[dict[str, Any]]


class EventHub:
    """Bounded in-memory event stream with process identity and cursor recovery."""

    def __init__(self, *, history_size: int = 256, subscriber_queue_size: int = 64) -> None:
        if history_size < 1 or subscriber_queue_size < 1:
            raise ValueError("event history and subscriber queues must be non-empty")
        self.stream_id = str(uuid4())
        self._history: deque[dict[str, Any]] = deque(maxlen=history_size)
        self._subscriber_queue_size = subscriber_queue_size
        self._subscribers: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._stalled_subscribers: set[str] = set()
        self._cursor = 0
        self._lock = asyncio.Lock()

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def publish(self, message: Message) -> None:
        event = message.to_dict()
        event["payload"] = sanitize_payload(message.payload)
        async with self._lock:
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

    async def read(
        self,
        *,
        after: int | None = None,
        stream_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        async with self._lock:
            frames, resumed, reason = self._select_frames(after, stream_id, limit)
            return {
                "stream_id": self.stream_id,
                "cursor": self._cursor,
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
    component_status: ComponentStatusProvider
    started_at: datetime
    started_monotonic: float


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
) -> bool:
    if origin is None or origin in allowed_origins:
        return True
    parsed = urlsplit(origin)
    return parsed.scheme in {"http", "https"} and parsed.netloc == host


def _require_console_access(request: Request, config: AppConfig) -> None:
    if not _validate_origin(
        request.headers.get("origin"),
        config.server.allowed_origins,
        request.headers.get("host"),
    ):
        raise ApiError(403, "origin_denied", "request origin is not allowed")
    expected = config.secrets.console_token
    if expected is None:
        if _is_loopback(config.server.host):
            return
        raise ApiError(
            503,
            "console_auth_not_configured",
            f"set {config.server.console_token_env} before enabling LAN mutations",
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
        items = await context.database.list_devices(limit=limit, offset=offset)
        return {"items": items, "limit": limit, "offset": offset}

    @app.get("/api/v1/devices/{device_id}")
    async def get_device(device_id: str) -> dict[str, Any]:
        device = await context.database.fetch_device(device_id)
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
        del device_id
        raise _component_not_ready("device action catalog")

    @app.post("/api/v1/devices/{device_id}/verify")
    async def verify_device(device_id: str, request: Request) -> dict[str, Any]:
        del device_id
        _require_console_access(request, context.config)
        raise _component_not_ready("device verification")

    @app.post("/api/v1/commands/action")
    async def command_action(payload: ActionCommand, request: Request) -> dict[str, Any]:
        del payload
        _require_console_access(request, context.config)
        raise _component_not_ready("dispatcher")

    @app.get("/api/v1/commands/{command_id}")
    async def get_command(command_id: str) -> dict[str, Any]:
        del command_id
        raise _component_not_ready("command repository")

    @app.post("/api/v1/devices/{device_id}/stop")
    async def stop_device(device_id: str, request: Request) -> dict[str, Any]:
        del device_id
        _require_console_access(request, context.config)
        raise _component_not_ready("dispatcher")

    @app.post("/api/v1/cluster/stop")
    async def stop_cluster(request: Request) -> dict[str, Any]:
        _require_console_access(request, context.config)
        raise _component_not_ready("dispatcher")

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
                "firmware": await firmware.status(),
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.websocket("/api/v1/events/stream")
    async def event_stream(websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin")
        if not _validate_origin(
            origin,
            context.config.server.allowed_origins,
            websocket.headers.get("host"),
        ):
            await websocket.close(code=4403, reason="origin denied")
            return
        expected = context.config.secrets.console_token
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
    app.mount("/assets", StaticFiles(directory=static_path), name="assets")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(static_path / "index.html", media_type="text/html")

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
