"""Authenticated Xiaozhi-compatible WebSocket v1 device gateway."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict, deque
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol, cast
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect

from ..config import AudioConfig, DeviceWebsocketConfig
from ..message_bus import MessageBus
from ..messages import JsonValue, Message, MessageKind
from .device_protocol import (
    OUTBOUND_COMMAND_TOPICS,
    DeviceProtocolError,
    decode_device_json,
    disconnected_message,
    encode_otto_command,
    heartbeat_message,
    required_string,
    translate_otto_value,
    validate_payload_identity,
    voice_session_closed_message,
)
from .mqtt_broker import normalize_device_id


class DeviceTokenStore(Protocol):
    def authenticate_device_token(self, value: str, token: str | None) -> bool: ...

    def expected_device_client_id(self, value: str) -> str | None: ...


class DeviceWebsocketState(str, Enum):
    DISABLED = "disabled"
    CREATED = "created"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"


@dataclass(slots=True)
class _WsPeer:
    device_id: str
    client_id: str
    session_id: str
    websocket: WebSocket
    input_sample_rate: int
    input_channels: int
    input_frame_duration_ms: int
    utterance_id: str | None = None
    next_audio_sequence: int = 0
    close_reason: str = "websocket_disconnected"
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    playback_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass(frozen=True, slots=True)
class _AudioFrame:
    device_id: str
    session_id: str
    utterance_id: str
    sequence: int
    payload: bytes


class DevicePlaybackError(RuntimeError):
    """Raised when ordered audio output to a device cannot continue."""


class DeviceTtsPlayback:
    """One exclusive TTS control/audio sequence for a connected WebSocket peer."""

    def __init__(self, gateway: DeviceWebsocketGateway, peer: _WsPeer) -> None:
        self._gateway = gateway
        self._peer = peer
        self._closed = False

    @property
    def device_id(self) -> str:
        return self._peer.device_id

    @property
    def session_id(self) -> str:
        return self._peer.session_id

    async def send_sentence(self, text: str) -> None:
        normalized = text.strip()
        if not normalized or len(normalized) > 2_000:
            raise ValueError("TTS sentence must contain 1..2000 characters")
        await self._send_json(
            {
                "session_id": self._peer.session_id,
                "type": "tts",
                "state": "sentence_start",
                "text": normalized,
            }
        )

    async def send_audio(self, packet: bytes) -> None:
        if not isinstance(packet, bytes):
            raise TypeError("TTS packet must be bytes")
        if not packet or len(packet) > self._gateway.config.max_frame_bytes:
            raise ValueError("TTS Opus packet size is invalid")
        self._ensure_open()
        if not await self._gateway._is_current(self._peer):
            raise DevicePlaybackError("websocket_device_not_connected")
        try:
            async with self._peer.send_lock:
                await self._peer.websocket.send_bytes(packet)
        except Exception as exc:
            raise DevicePlaybackError("websocket_audio_write_failed") from exc
        self._gateway._audio_frames_sent += 1

    async def stop(self) -> None:
        if self._closed:
            return
        try:
            if await self._gateway._is_current(self._peer):
                await self._send_json(
                    {
                        "session_id": self._peer.session_id,
                        "type": "tts",
                        "state": "stop",
                    }
                )
        finally:
            self._closed = True
            if self._peer.playback_lock.locked():
                self._peer.playback_lock.release()

    async def _send_json(self, payload: dict[str, JsonValue]) -> None:
        self._ensure_open()
        if not await self._gateway._is_current(self._peer):
            raise DevicePlaybackError("websocket_device_not_connected")
        try:
            async with self._peer.send_lock:
                await self._peer.websocket.send_json(payload)
        except Exception as exc:
            raise DevicePlaybackError("websocket_control_write_failed") from exc

    def _ensure_open(self) -> None:
        if self._closed:
            raise DevicePlaybackError("tts_playback_is_closed")


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class DeviceWebsocketGateway:
    """Translate Xiaozhi v1 control/audio frames at the FastAPI boundary."""

    def __init__(
        self,
        config: DeviceWebsocketConfig,
        audio: AudioConfig,
        credentials: DeviceTokenStore,
        message_bus: MessageBus,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.audio = audio
        self.credentials = credentials
        self.message_bus = message_bus
        self._state = (
            DeviceWebsocketState.CREATED
            if config.enabled
            else DeviceWebsocketState.DISABLED
        )
        self._connections: dict[str, _WsPeer] = {}
        self._connection_lock = asyncio.Lock()
        self._subscription_ids: list[str] = []
        self._audio_frames: OrderedDict[str, _AudioFrame] = OrderedDict()
        self._audio_ids: dict[str, deque[str]] = {}
        self._audio_lock = asyncio.Lock()
        self._last_error: str | None = None
        self._connections_accepted = 0
        self._connections_rejected = 0
        self._messages_received = 0
        self._messages_rejected = 0
        self._messages_published = 0
        self._publish_failures = 0
        self._audio_frames_received = 0
        self._audio_frames_sent = 0
        self._transcriptions_sent = 0
        self._prelisten_audio_frames_dropped = 0
        self._logger = logger or logging.getLogger("otto_master.device_ws")

    @property
    def running(self) -> bool:
        return self._state is DeviceWebsocketState.RUNNING

    async def start(self) -> None:
        if self._state is DeviceWebsocketState.DISABLED or self.running:
            return
        for topic in OUTBOUND_COMMAND_TOPICS:
            self._subscription_ids.append(
                await self.message_bus.subscribe(topic, self._publish_command)
            )
        self._state = DeviceWebsocketState.RUNNING

    async def shutdown(self) -> None:
        if self._state in {DeviceWebsocketState.DISABLED, DeviceWebsocketState.STOPPED}:
            return
        if self._state is DeviceWebsocketState.CREATED:
            self._state = DeviceWebsocketState.STOPPED
            return
        self._state = DeviceWebsocketState.STOPPING
        for subscription_id in self._subscription_ids:
            await self.message_bus.unsubscribe(subscription_id)
        self._subscription_ids.clear()
        async with self._connection_lock:
            peers = tuple(self._connections.values())
            self._connections.clear()
        for peer in peers:
            await self._safe_close(peer.websocket, 1001, "server shutdown")
        async with self._audio_lock:
            self._audio_frames.clear()
            self._audio_ids.clear()
        await self._publish_transport_unavailable("websocket_gateway_stopped")
        self._state = DeviceWebsocketState.STOPPED

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "healthy": self.running,
            "state": self._state.value,
            "protocol": "xiaozhi-websocket-v1",
            "authenticated": True,
            "connected_devices": len(self._connections),
            "connections_accepted": self._connections_accepted,
            "connections_rejected": self._connections_rejected,
            "messages_received": self._messages_received,
            "messages_rejected": self._messages_rejected,
            "messages_published": self._messages_published,
            "publish_failures": self._publish_failures,
            "audio_frames_received": self._audio_frames_received,
            "audio_frames_sent": self._audio_frames_sent,
            "transcriptions_sent": self._transcriptions_sent,
            "prelisten_audio_frames_dropped": self._prelisten_audio_frames_dropped,
            "audio_frames_buffered": len(self._audio_frames),
            "last_error": self._last_error,
            "checked_at": _now(),
        }

    async def handle(self, websocket: WebSocket) -> None:
        """Own one ASGI WebSocket from authenticated headers through disconnect."""

        if not self.running:
            await self._safe_close(websocket, 1013, "device websocket unavailable")
            return
        peer: _WsPeer | None = None
        accepted = False
        try:
            device_id, client_id = self._authenticate_headers(websocket)
            async with self._connection_lock:
                existing = self._connections.get(device_id)
                if existing is None and len(self._connections) >= self.config.max_connections:
                    raise DeviceProtocolError("device websocket connection limit reached")
            await websocket.accept()
            accepted = True
            first = await asyncio.wait_for(
                websocket.receive(),
                timeout=self.config.hello_timeout_seconds,
            )
            if first.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect(int(first.get("code", 1000)))
            text = first.get("text")
            if not isinstance(text, str):
                raise DeviceProtocolError("first WebSocket frame must be a JSON hello")
            value = decode_device_json(
                text.encode("utf-8"),
                maximum_bytes=self.config.max_frame_bytes,
            )
            (
                connected,
                input_sample_rate,
                input_channels,
                input_frame_duration_ms,
            ) = self._hello_message(device_id, value)
            candidate = _WsPeer(
                device_id=device_id,
                client_id=client_id,
                session_id=str(uuid4()),
                websocket=websocket,
                input_sample_rate=input_sample_rate,
                input_channels=input_channels,
                input_frame_duration_ms=input_frame_duration_ms,
            )
            replaced = await self._register(candidate)
            peer = candidate
            if replaced is not None:
                await self._safe_close(replaced.websocket, 4000, "connection replaced")
            await websocket.send_json(
                {
                    "type": "hello",
                    "transport": "websocket",
                    "session_id": peer.session_id,
                    "audio_params": {
                        "format": self.audio.format,
                        "sample_rate": self.audio.input_sample_rate,
                        "channels": self.audio.channels,
                        "frame_duration": self.audio.frame_duration_ms,
                    },
                }
            )
            await self.message_bus.publish(connected)
            await self.message_bus.publish(heartbeat_message(device_id, "websocket"))
            self._connections_accepted += 1
            await self._receive_loop(peer)
        except WebSocketDisconnect:
            pass
        except (TimeoutError, DeviceProtocolError, ValueError) as exc:
            self._connections_rejected += int(peer is None)
            self._messages_rejected += int(peer is not None)
            self._last_error = f"{type(exc).__name__}: {exc}"
            if accepted:
                await self._safe_close(websocket, 4400, "invalid device frame")
            else:
                await self._safe_close(websocket, 4401, "device authentication failed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._logger.exception(
                "device_websocket_failed",
                extra={"event": "device_websocket_failed"},
            )
            await self._safe_close(websocket, 1011, "device websocket failed")
        finally:
            if peer is not None:
                if (
                    peer.utterance_id is not None
                    and self.message_bus.running
                    and self.running
                ):
                    await self._finish_utterance(
                        peer,
                        reason="websocket_disconnected",
                    )
                current = await self._unregister(peer)
                await self._clear_audio(peer.device_id, peer.session_id)
                if current and self.message_bus.running and self.running:
                    await self.message_bus.publish(
                        voice_session_closed_message(
                            peer.device_id,
                            "websocket",
                            peer.session_id,
                            peer.close_reason,
                        )
                    )
                    await self.message_bus.publish(
                        disconnected_message(
                            peer.device_id,
                            "websocket",
                            "websocket_connection_closed",
                        )
                    )

    def _authenticate_headers(self, websocket: WebSocket) -> tuple[str, str]:
        raw_device_id = websocket.headers.get("device-id")
        if raw_device_id is None:
            raise DeviceProtocolError("Device-Id header is required")
        try:
            device_id = normalize_device_id(raw_device_id)
        except ValueError as exc:
            raise DeviceProtocolError("Device-Id header is invalid") from exc
        if websocket.headers.get("protocol-version") != "1":
            raise DeviceProtocolError("only Protocol-Version 1 is supported")
        client_id = websocket.headers.get("client-id", "").strip()
        if not client_id or len(client_id) > 128:
            raise DeviceProtocolError("Client-Id header is invalid")
        expected_client_id = self.credentials.expected_device_client_id(device_id)
        if expected_client_id is None or client_id != expected_client_id:
            raise DeviceProtocolError("WebSocket client identity is not provisioned")
        authorization = websocket.headers.get("authorization", "")
        token = (
            authorization[7:].strip()
            if authorization.lower().startswith("bearer ")
            else None
        )
        if not self.credentials.authenticate_device_token(device_id, token):
            raise DeviceProtocolError("WebSocket device authentication failed")
        return device_id, client_id

    def _hello_message(
        self,
        device_id: str,
        value: dict[str, Any],
    ) -> tuple[Message, int, int, int]:
        if value.get("type") != "hello":
            raise DeviceProtocolError("first WebSocket message must be hello")
        if value.get("version") != 1 or value.get("transport") != "websocket":
            raise DeviceProtocolError("unsupported Xiaozhi WebSocket hello")
        validate_payload_identity(device_id, value)
        audio_params = value.get("audio_params")
        if not isinstance(audio_params, dict):
            raise DeviceProtocolError("hello audio_params must be an object")
        if audio_params.get("format") != "opus":
            raise DeviceProtocolError("WebSocket audio format must be opus")
        sample_rate = audio_params.get("sample_rate")
        channels = audio_params.get("channels")
        frame_duration = audio_params.get("frame_duration")
        if sample_rate not in {8000, 12000, 16000, 24000, 48000}:
            raise DeviceProtocolError("unsupported WebSocket audio sample rate")
        if channels != 1 or frame_duration not in {20, 40, 60}:
            raise DeviceProtocolError("unsupported WebSocket audio layout")
        features = value.get("features", {})
        if not isinstance(features, dict):
            raise DeviceProtocolError("hello features must be an object")
        name_value = value.get("name", device_id)
        firmware_value = value.get("firmware_version", "unknown")
        if not isinstance(name_value, str) or not name_value.strip() or len(name_value) > 80:
            raise DeviceProtocolError("hello name is invalid")
        if (
            not isinstance(firmware_value, str)
            or not firmware_value.strip()
            or len(firmware_value) > 80
        ):
            raise DeviceProtocolError("hello firmware_version is invalid")
        capabilities: dict[str, JsonValue] = {
            "voice": True,
            "binary_opus": True,
            "mcp": features.get("mcp") is True,
            "state": features.get("otto_state") is True,
            "actions": features.get("otto_actions") is True,
            "stop": features.get("otto_stop") is True,
        }
        return (
            Message.create(
                topic="device.connected",
                kind=MessageKind.EVENT,
                source=f"device:{device_id}:websocket",
                target=f"device:{device_id}",
                payload={
                    "device_id": device_id,
                    "mac": device_id,
                    "transport": "websocket",
                    "protocol": "xiaozhi-websocket/1",
                    "name": name_value.strip(),
                    "device_name": name_value.strip(),
                    "firmware_version": firmware_value.strip(),
                    "capabilities": capabilities,
                    "audio_params": cast(dict[str, JsonValue], dict(audio_params)),
                },
            ),
            cast(int, sample_rate),
            cast(int, channels),
            cast(int, frame_duration),
        )

    async def _receive_loop(self, peer: _WsPeer) -> None:
        receive_task: asyncio.Task[MutableMapping[str, Any]] | None = None
        try:
            receive_task = asyncio.create_task(peer.websocket.receive())
            while self.running and await self._is_current(peer):
                done, _ = await asyncio.wait(
                    {receive_task},
                    timeout=self.config.heartbeat_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    await self.message_bus.publish(
                        heartbeat_message(peer.device_id, "websocket")
                    )
                    continue
                frame = receive_task.result()
                if frame.get("type") == "websocket.disconnect":
                    raise WebSocketDisconnect(int(frame.get("code", 1000)))
                self._messages_received += 1
                raw_bytes = frame.get("bytes")
                raw_text = frame.get("text")
                if isinstance(raw_bytes, bytes):
                    await self._handle_audio(peer, raw_bytes)
                elif isinstance(raw_text, str):
                    await self._handle_text(peer, raw_text)
                else:
                    raise DeviceProtocolError("WebSocket frame has no text or bytes payload")
                receive_task = asyncio.create_task(peer.websocket.receive())
        finally:
            if receive_task is not None and not receive_task.done():
                receive_task.cancel()
                await asyncio.gather(receive_task, return_exceptions=True)

    async def _handle_text(self, peer: _WsPeer, text: str) -> None:
        value = decode_device_json(
            text.encode("utf-8"),
            maximum_bytes=self.config.max_frame_bytes,
        )
        session_id = value.get("session_id")
        if session_id is not None and session_id != peer.session_id:
            raise DeviceProtocolError("WebSocket session_id does not match the connection")
        message_type = required_string(value, "type", maximum=64)
        if message_type == "listen":
            await self._publish_listen(peer, value)
        elif message_type == "abort":
            await self._finish_utterance(peer, reason="abort")
        elif message_type in {
            "heartbeat",
            "otto_state",
            "otto_actions",
            "otto_action_ack",
            "otto_stop_ack",
            "error",
        }:
            for message in translate_otto_value(
                peer.device_id,
                value,
                transport="websocket",
            ):
                await self.message_bus.publish(message)
        elif message_type == "goodbye":
            peer.close_reason = "device_goodbye"
            if peer.utterance_id is not None:
                await self._finish_utterance(peer, reason="goodbye")
            await self._safe_close(peer.websocket, 1000, "device goodbye")
        elif message_type == "mcp":
            return
        else:
            raise DeviceProtocolError(f"unsupported WebSocket message type: {message_type}")
        await self.message_bus.publish(heartbeat_message(peer.device_id, "websocket"))

    async def _publish_listen(self, peer: _WsPeer, value: dict[str, Any]) -> None:
        state = required_string(value, "state", maximum=16)
        if state == "start":
            mode = value.get("mode", "auto")
            if mode not in {"auto", "manual", "realtime"}:
                raise DeviceProtocolError("listen mode is invalid")
            if peer.utterance_id is not None:
                # Xiaozhi firmware re-enters listening after a TTS stop without
                # first emitting a listen/stop for the pre-TTS utterance. Close
                # that bounded input before opening the post-TTS question so
                # wake-gate playback cannot turn a valid restart into a
                # protocol disconnect.
                await self._finish_utterance(peer, reason="listen_restarted")
            peer.utterance_id = str(uuid4())
            peer.next_audio_sequence = 0
            await self.message_bus.publish(
                self._audio_lifecycle_message(
                    peer,
                    "audio.input.started",
                    {
                        "utterance_id": peer.utterance_id,
                        "mode": mode,
                        "codec": "opus",
                        "sample_rate": peer.input_sample_rate,
                        "channels": peer.input_channels,
                        "frame_duration_ms": peer.input_frame_duration_ms,
                    },
                )
            )
            return
        if state == "stop":
            await self._finish_utterance(peer, reason="listen_stop")
            return
        if state == "detect":
            text = value.get("text", "")
            if not isinstance(text, str) or len(text) > 512:
                raise DeviceProtocolError("wake candidate text is invalid")
            await self.message_bus.publish(
                Message.create(
                    topic="voice.wake.candidate.received",
                    kind=MessageKind.EVENT,
                    source=f"device:{peer.device_id}:websocket",
                    target=f"device:{peer.device_id}",
                    payload={
                        "device_id": peer.device_id,
                        "transport": "websocket",
                        "session_id": peer.session_id,
                        "text": text,
                    },
                )
            )
            return
        if state == "vad":
            speaking = value.get("speaking")
            if not isinstance(speaking, bool):
                raise DeviceProtocolError("listen VAD speaking must be a boolean")
            if peer.utterance_id is None:
                return
            await self.message_bus.publish(
                self._audio_lifecycle_message(
                    peer,
                    "audio.input.activity",
                    {
                        "utterance_id": peer.utterance_id,
                        "speaking": speaking,
                    },
                )
            )
            return
        raise DeviceProtocolError("listen state is invalid")

    async def _finish_utterance(self, peer: _WsPeer, *, reason: str) -> None:
        utterance_id = peer.utterance_id
        if utterance_id is None:
            raise DeviceProtocolError("audio input finished without an active utterance")
        frame_count = peer.next_audio_sequence
        peer.utterance_id = None
        peer.next_audio_sequence = 0
        await self.message_bus.publish(
            self._audio_lifecycle_message(
                peer,
                "audio.input.finished",
                {
                    "utterance_id": utterance_id,
                    "reason": reason,
                    "frame_count": frame_count,
                },
            )
        )

    def _audio_lifecycle_message(
        self,
        peer: _WsPeer,
        topic: str,
        extra: dict[str, JsonValue],
    ) -> Message:
        return Message.create(
            topic=topic,
            kind=MessageKind.EVENT,
            source=f"device:{peer.device_id}:websocket",
            target="service:asr",
            payload={
                "device_id": peer.device_id,
                "transport": "websocket",
                "session_id": peer.session_id,
                **extra,
            },
        )

    async def _handle_audio(self, peer: _WsPeer, payload: bytes) -> None:
        if not payload or len(payload) > self.config.max_frame_bytes:
            raise DeviceProtocolError("WebSocket audio frame size is invalid")
        utterance_id = peer.utterance_id
        if utterance_id is None:
            # Xiaozhi sends bounded wake-word pre-roll after hello but before
            # listen/start. It predates the post-laughter user utterance, so
            # fail closed by dropping it without disconnecting the device.
            self._prelisten_audio_frames_dropped += 1
            return
        frame_ref = str(uuid4())
        sequence = peer.next_audio_sequence
        async with self._audio_lock:
            ids = self._audio_ids.setdefault(peer.device_id, deque())
            while len(ids) >= self.config.audio_buffer_frames_per_device:
                expired = ids.popleft()
                self._audio_frames.pop(expired, None)
            ids.append(frame_ref)
            self._audio_frames[frame_ref] = _AudioFrame(
                device_id=peer.device_id,
                session_id=peer.session_id,
                utterance_id=utterance_id,
                sequence=sequence,
                payload=bytes(payload),
            )
        peer.next_audio_sequence += 1
        self._audio_frames_received += 1
        await self.message_bus.publish(
            Message.create(
                topic="audio.input.frame",
                kind=MessageKind.EVENT,
                source=f"device:{peer.device_id}:websocket",
                target="service:asr",
                payload={
                    "device_id": peer.device_id,
                    "transport": "websocket",
                    "session_id": peer.session_id,
                    "utterance_id": utterance_id,
                    "frame_ref": frame_ref,
                    "sequence": sequence,
                    "byte_length": len(payload),
                    "codec": "opus",
                    "sample_rate": peer.input_sample_rate,
                    "channels": peer.input_channels,
                    "frame_duration_ms": peer.input_frame_duration_ms,
                    "protocol_version": 1,
                },
            )
        )
        await self.message_bus.publish(heartbeat_message(peer.device_id, "websocket"))

    async def take_audio_frame(
        self,
        device_id: str,
        utterance_id: str,
        sequence: int,
        frame_ref: str,
    ) -> bytes | None:
        """Remove one in-memory frame by opaque reference for the future audio service."""

        async with self._audio_lock:
            frame = self._audio_frames.get(frame_ref)
            if (
                frame is None
                or frame.device_id != device_id
                or frame.utterance_id != utterance_id
                or frame.sequence != sequence
            ):
                return None
            self._audio_frames.pop(frame_ref, None)
            ids = self._audio_ids.get(device_id)
            if ids is not None:
                try:
                    ids.remove(frame_ref)
                except ValueError:
                    pass
                if not ids:
                    self._audio_ids.pop(device_id, None)
            return frame.payload

    async def start_tts_playback(self, device_id: str) -> DeviceTtsPlayback:
        """Send TTS start and reserve one playback slot for this device."""

        async with self._connection_lock:
            peer = self._connections.get(device_id)
        if peer is None or not await self._is_current(peer):
            raise DevicePlaybackError("websocket_device_not_connected")
        await peer.playback_lock.acquire()
        playback = DeviceTtsPlayback(self, peer)
        try:
            await playback._send_json(
                {
                    "session_id": peer.session_id,
                    "type": "tts",
                    "state": "start",
                }
            )
        except BaseException:
            try:
                await playback.stop()
            except DevicePlaybackError:
                pass
            raise
        return playback

    async def send_transcription(
        self,
        device_id: str,
        session_id: str,
        text: str,
    ) -> None:
        """Send final ASR text to exactly one authenticated WebSocket session."""

        normalized = _transcription_text(text)
        async with self._connection_lock:
            peer = self._connections.get(device_id)
        if peer is None or not await self._is_current(peer):
            raise DevicePlaybackError("websocket_device_not_connected")
        if peer.session_id != session_id:
            raise DevicePlaybackError("device_session_changed")
        try:
            async with peer.send_lock:
                await peer.websocket.send_json(
                    {
                        "session_id": peer.session_id,
                        "type": "stt",
                        "text": normalized,
                    }
                )
        except Exception as exc:
            raise DevicePlaybackError("websocket_control_write_failed") from exc
        self._transcriptions_sent += 1

    async def close_audio_session(self, device_id: str, session_id: str) -> bool:
        """Close a completed WebSocket voice turn so firmware returns to idle."""

        async with self._connection_lock:
            peer = self._connections.get(device_id)
        if peer is None:
            return False
        if peer.session_id != session_id:
            raise DevicePlaybackError("device_session_changed")
        peer.close_reason = "server_goodbye"
        await self._safe_close(peer.websocket, 1000, "voice turn complete")
        return True

    async def _clear_audio(self, device_id: str, session_id: str) -> None:
        async with self._audio_lock:
            ids = self._audio_ids.get(device_id)
            if ids is None:
                return
            retained: deque[str] = deque()
            for frame_id in ids:
                frame = self._audio_frames.get(frame_id)
                if frame is not None and frame.session_id == session_id:
                    self._audio_frames.pop(frame_id, None)
                else:
                    retained.append(frame_id)
            if retained:
                self._audio_ids[device_id] = retained
            else:
                self._audio_ids.pop(device_id, None)

    async def _register(self, peer: _WsPeer) -> _WsPeer | None:
        async with self._connection_lock:
            replaced = self._connections.get(peer.device_id)
            if replaced is None and len(self._connections) >= self.config.max_connections:
                raise DeviceProtocolError("device websocket connection limit reached")
            self._connections[peer.device_id] = peer
            return replaced

    async def _unregister(self, peer: _WsPeer) -> bool:
        async with self._connection_lock:
            if self._connections.get(peer.device_id) is not peer:
                return False
            self._connections.pop(peer.device_id, None)
            return True

    async def _is_current(self, peer: _WsPeer) -> bool:
        async with self._connection_lock:
            return self._connections.get(peer.device_id) is peer

    async def _publish_command(self, message: Message) -> None:
        if message.payload.get("transport") != "websocket":
            return
        try:
            command = encode_otto_command(message, transport="websocket")
        except DeviceProtocolError as exc:
            await self._publish_command_failure(message, str(exc))
            return
        async with self._connection_lock:
            peer = self._connections.get(command.device_id)
        if peer is None or not await self._is_current(peer):
            await self._publish_command_failure(message, "websocket_device_not_connected")
            return
        try:
            async with peer.send_lock:
                await peer.websocket.send_text(command.payload.decode("utf-8"))
        except Exception:  # noqa: BLE001 - ASGI send failures vary by server
            await self._publish_command_failure(message, "websocket_write_failed")
            return
        self._messages_published += 1
        await self.message_bus.publish(
            Message.create(
                topic="device.command.published",
                kind=MessageKind.RESULT,
                source="device_websocket",
                target=f"device:{command.device_id}",
                correlation_id=command.external_id,
                payload={
                    "device_id": command.device_id,
                    "transport": "websocket",
                    "command_topic": message.topic,
                    "outbound_message_id": message.message_id,
                    "framing": "text-json",
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
                source="device_websocket",
                target=target,
                correlation_id=message.correlation_id or message.message_id,
                payload={
                    "device_id": normalized,
                    "transport": "websocket",
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
                source="device_websocket",
                target="service:device_manager",
                payload={"transport": "websocket", "reason": reason},
            )
        )

    @staticmethod
    async def _safe_close(websocket: WebSocket, code: int, reason: str) -> None:
        try:
            await websocket.close(code=code, reason=reason)
        except (RuntimeError, WebSocketDisconnect):
            pass


def _transcription_text(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("transcription text must be a string")
    normalized = text.strip()
    if not normalized or len(normalized) > 512:
        raise ValueError("transcription text must contain 1..512 characters")
    return normalized
