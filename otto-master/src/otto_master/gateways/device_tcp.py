"""Authenticated newline-JSON fallback gateway for ``otto-master/1`` devices."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol
from uuid import uuid4

from ..config import TcpConfig
from ..message_bus import MessageBus
from ..messages import Message, MessageKind
from .device_protocol import (
    OUTBOUND_COMMAND_TOPICS,
    DeviceProtocolError,
    decode_device_json,
    disconnected_message,
    encode_otto_command,
    heartbeat_message,
    required_string,
    translate_otto_value,
)
from .mqtt_broker import normalize_device_id


class DeviceTokenStore(Protocol):
    def authenticate_device_token(self, value: str, token: str | None) -> bool: ...

    def expected_device_client_id(self, value: str) -> str | None: ...


class DeviceTcpError(RuntimeError):
    """Raised when the TCP gateway cannot establish its listener."""


class DeviceTcpState(str, Enum):
    DISABLED = "disabled"
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass(slots=True)
class _TcpPeer:
    device_id: str
    client_id: str
    session_id: str
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class DeviceTcpGateway:
    """Expose the legacy diagnostic transport without creating a second domain model."""

    def __init__(
        self,
        config: TcpConfig,
        credentials: DeviceTokenStore,
        message_bus: MessageBus,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.credentials = credentials
        self.message_bus = message_bus
        self._state = DeviceTcpState.CREATED if config.enabled else DeviceTcpState.DISABLED
        self._server: asyncio.Server | None = None
        self._connections: dict[str, _TcpPeer] = {}
        self._connection_lock = asyncio.Lock()
        self._client_tasks: set[asyncio.Task[Any]] = set()
        self._subscription_ids: list[str] = []
        self._stopping = False
        self._last_error: str | None = None
        self._connections_accepted = 0
        self._connections_rejected = 0
        self._messages_received = 0
        self._messages_rejected = 0
        self._messages_published = 0
        self._publish_failures = 0
        self._logger = logger or logging.getLogger("otto_master.device_tcp")

    @property
    def running(self) -> bool:
        return self._state is DeviceTcpState.RUNNING

    async def start(self) -> None:
        if self._state is DeviceTcpState.DISABLED or self.running:
            return
        if self._state not in {
            DeviceTcpState.CREATED,
            DeviceTcpState.STOPPED,
            DeviceTcpState.ERROR,
        }:
            raise DeviceTcpError(f"TCP gateway cannot start from {self._state.value}")
        self._state = DeviceTcpState.STARTING
        self._stopping = False
        self._last_error = None
        try:
            self._server = await asyncio.start_server(
                self._handle_client,
                self.config.host,
                self.config.port,
                limit=self.config.max_frame_bytes + 2,
            )
            for topic in OUTBOUND_COMMAND_TOPICS:
                self._subscription_ids.append(
                    await self.message_bus.subscribe(topic, self._publish_command)
                )
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._state = DeviceTcpState.ERROR
            if self._server is not None:
                self._server.close()
                await self._server.wait_closed()
                self._server = None
            raise DeviceTcpError(
                f"failed to start TCP gateway on {self.config.host}:{self.config.port}"
            ) from exc
        self._state = DeviceTcpState.RUNNING
        self._logger.info(
            "device_tcp_started",
            extra={
                "event": "device_tcp_started",
                "host": self.config.host,
                "port": self.config.port,
            },
        )

    async def shutdown(self) -> None:
        if self._state in {DeviceTcpState.DISABLED, DeviceTcpState.STOPPED}:
            return
        self._stopping = True
        self._state = DeviceTcpState.STOPPING
        for subscription_id in self._subscription_ids:
            await self.message_bus.unsubscribe(subscription_id)
        self._subscription_ids.clear()
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            await server.wait_closed()
        async with self._connection_lock:
            peers = tuple(self._connections.values())
            self._connections.clear()
        for peer in peers:
            peer.writer.close()
        for peer in peers:
            await self._wait_writer_closed(peer.writer)
        current = asyncio.current_task()
        tasks = tuple(task for task in self._client_tasks if task is not current)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._client_tasks.clear()
        await self._publish_transport_unavailable("tcp_gateway_stopped")
        self._state = DeviceTcpState.STOPPED
        self._logger.info("device_tcp_stopped", extra={"event": "device_tcp_stopped"})

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "healthy": self.running,
            "state": self._state.value,
            "host": self.config.host,
            "port": self.config.port,
            "protocol": "otto-master/1",
            "authenticated": True,
            "connected_devices": len(self._connections),
            "connections_accepted": self._connections_accepted,
            "connections_rejected": self._connections_rejected,
            "messages_received": self._messages_received,
            "messages_rejected": self._messages_rejected,
            "messages_published": self._messages_published,
            "publish_failures": self._publish_failures,
            "last_error": self._last_error,
            "checked_at": _now(),
        }

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._client_tasks.add(task)
        peer: _TcpPeer | None = None
        try:
            if self._stopping or not self.running:
                return
            first = await asyncio.wait_for(
                self._read_frame(reader),
                timeout=self.config.hello_timeout_seconds,
            )
            value = decode_device_json(first, maximum_bytes=self.config.max_frame_bytes)
            if value.get("type") != "hello":
                raise DeviceProtocolError("first TCP frame must be hello")
            if required_string(value, "protocol", maximum=32) != "otto-master/1":
                raise DeviceProtocolError("unsupported TCP protocol")
            mac = required_string(value, "mac", maximum=32)
            try:
                device_id = normalize_device_id(mac)
            except ValueError as exc:
                raise DeviceProtocolError("hello contains an invalid MAC") from exc
            token = required_string(value, "token", maximum=256)
            client_id = required_string(value, "client_id", maximum=128)
            expected_client_id = self.credentials.expected_device_client_id(device_id)
            if expected_client_id is None or client_id != expected_client_id:
                raise DeviceProtocolError("TCP client identity is not provisioned")
            if not self.credentials.authenticate_device_token(device_id, token):
                raise DeviceProtocolError("TCP device authentication failed")
            peer_name = writer.get_extra_info("peername")
            if isinstance(peer_name, tuple) and peer_name and isinstance(peer_name[0], str):
                value["ip_address"] = peer_name[0]
            translated = translate_otto_value(
                device_id,
                value,
                transport="tcp",
                hello_protocol="otto-master/1",
            )
            candidate = _TcpPeer(
                device_id=device_id,
                client_id=client_id,
                session_id=str(uuid4()),
                reader=reader,
                writer=writer,
            )
            replaced = await self._register(candidate)
            peer = candidate
            if replaced is not None:
                replaced.writer.close()
                await self._wait_writer_closed(replaced.writer)
            await self._send_json(
                peer,
                {
                    "type": "hello",
                    "protocol": "otto-master/1",
                    "session_id": peer.session_id,
                },
            )
            for message in (*translated, heartbeat_message(device_id, "tcp")):
                await self.message_bus.publish(message)
            self._connections_accepted += 1
            while not self._stopping and await self._is_current(peer):
                frame = await self._read_frame(reader)
                self._messages_received += 1
                value = decode_device_json(frame, maximum_bytes=self.config.max_frame_bytes)
                if value.get("type") == "hello":
                    raise DeviceProtocolError("TCP hello may only appear as the first frame")
                messages = translate_otto_value(device_id, value, transport="tcp")
                if not await self._is_current(peer):
                    break
                for message in messages:
                    await self.message_bus.publish(message)
        except (TimeoutError, EOFError, ConnectionError, asyncio.IncompleteReadError):
            pass
        except (DeviceProtocolError, ValueError) as exc:
            self._connections_rejected += int(peer is None)
            self._messages_rejected += int(peer is not None)
            self._last_error = f"{type(exc).__name__}: {exc}"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._logger.exception(
                "device_tcp_client_failed",
                extra={"event": "device_tcp_client_failed"},
            )
        finally:
            if (
                peer is not None
                and await self._unregister(peer)
                and self.message_bus.running
                and not self._stopping
            ):
                await self.message_bus.publish(
                    disconnected_message(peer.device_id, "tcp", "tcp_connection_closed")
                )
            writer.close()
            await self._wait_writer_closed(writer)
            if task is not None:
                self._client_tasks.discard(task)

    async def _read_frame(self, reader: asyncio.StreamReader) -> bytes:
        try:
            line = await reader.readline()
        except ValueError as exc:
            raise DeviceProtocolError("TCP frame exceeds configured limit") from exc
        if not line:
            raise EOFError("TCP peer closed")
        if len(line) > self.config.max_frame_bytes + 1:
            raise DeviceProtocolError("TCP frame exceeds configured limit")
        if not line.endswith(b"\n"):
            raise DeviceProtocolError("TCP frame must end with LF")
        payload = line[:-1]
        if payload.endswith(b"\r"):
            payload = payload[:-1]
        return payload

    async def _register(self, peer: _TcpPeer) -> _TcpPeer | None:
        async with self._connection_lock:
            replaced = self._connections.get(peer.device_id)
            if replaced is None and len(self._connections) >= self.config.max_connections:
                raise DeviceProtocolError("TCP device connection limit reached")
            self._connections[peer.device_id] = peer
            return replaced

    async def _unregister(self, peer: _TcpPeer) -> bool:
        async with self._connection_lock:
            if self._connections.get(peer.device_id) is not peer:
                return False
            self._connections.pop(peer.device_id, None)
            return True

    async def _is_current(self, peer: _TcpPeer) -> bool:
        async with self._connection_lock:
            return self._connections.get(peer.device_id) is peer

    async def _publish_command(self, message: Message) -> None:
        if message.payload.get("transport") != "tcp":
            return
        try:
            command = encode_otto_command(message, transport="tcp")
        except DeviceProtocolError as exc:
            await self._publish_command_failure(message, str(exc))
            return
        async with self._connection_lock:
            peer = self._connections.get(command.device_id)
        if peer is None or not await self._is_current(peer):
            await self._publish_command_failure(message, "tcp_device_not_connected")
            return
        try:
            async with peer.send_lock:
                peer.writer.write(command.payload + b"\n")
                await asyncio.wait_for(
                    peer.writer.drain(),
                    timeout=self.config.write_timeout_seconds,
                )
        except Exception:  # noqa: BLE001 - stream failures vary by platform
            await self._publish_command_failure(message, "tcp_write_failed")
            return
        self._messages_published += 1
        await self.message_bus.publish(
            Message.create(
                topic="device.command.published",
                kind=MessageKind.RESULT,
                source="device_tcp",
                target=f"device:{command.device_id}",
                correlation_id=command.external_id,
                payload={
                    "device_id": command.device_id,
                    "transport": "tcp",
                    "command_topic": message.topic,
                    "outbound_message_id": message.message_id,
                    "framing": "ndjson",
                },
            )
        )

    async def _publish_command_failure(self, message: Message, reason: str) -> None:
        self._publish_failures += 1
        device_id = message.payload.get("device_id")
        normalized = device_id if isinstance(device_id, str) else "unknown"
        target = message.target if message.target.startswith("device:") else "service:dispatcher"
        await self.message_bus.publish(
            Message.create(
                topic="device.command.failed",
                kind=MessageKind.RESULT,
                source="device_tcp",
                target=target,
                correlation_id=message.correlation_id or message.message_id,
                payload={
                    "device_id": normalized,
                    "transport": "tcp",
                    "command_topic": message.topic,
                    "reason": reason[:512],
                },
            )
        )

    async def _publish_transport_unavailable(self, reason: str) -> None:
        if not self.message_bus.running:
            return
        await self.message_bus.publish(
            Message.create(
                topic="device.transport.unavailable",
                kind=MessageKind.EVENT,
                source="device_tcp",
                target="service:device_manager",
                payload={"transport": "tcp", "reason": reason},
            )
        )

    async def _send_json(self, peer: _TcpPeer, value: dict[str, Any]) -> None:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(payload) > self.config.max_frame_bytes:
            raise DeviceProtocolError("TCP outbound frame exceeds configured limit")
        async with peer.send_lock:
            peer.writer.write(payload + b"\n")
            await asyncio.wait_for(peer.writer.drain(), timeout=self.config.write_timeout_seconds)

    @staticmethod
    async def _wait_writer_closed(writer: asyncio.StreamWriter) -> None:
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass
