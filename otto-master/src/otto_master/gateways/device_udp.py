"""Encrypted UDP Opus data plane negotiated over authenticated device MQTT."""

from __future__ import annotations

import asyncio
import logging
import secrets
import struct
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from enum import Enum
from time import monotonic
from typing import Any, cast
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ..config import AudioConfig, DeviceUdpConfig
from ..message_bus import MessageBus
from ..messages import JsonValue, Message, MessageKind
from .device_mqtt import DeviceControlSender
from .device_protocol import (
    DeviceProtocolError,
    heartbeat_message,
    required_string,
    voice_session_closed_message,
)

_HEADER_BYTES = 16
_PACKET_TYPE_OPUS = 1


class DeviceUdpState(str, Enum):
    DISABLED = "disabled"
    CREATED = "created"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class DeviceUdpError(RuntimeError):
    """Raised when the encrypted UDP data plane cannot run."""


class DeviceUdpPlaybackError(RuntimeError):
    """Raised when ordered MQTT-control/UDP-audio playback cannot continue."""


@dataclass(frozen=True, slots=True)
class _AudioFrame:
    device_id: str
    session_id: str
    utterance_id: str
    sequence: int
    payload: bytes


@dataclass(slots=True)
class _UdpSession:
    device_id: str
    session_id: str
    key: bytes
    nonce: bytes
    marker: bytes
    input_sample_rate: int
    input_channels: int
    input_frame_duration_ms: int
    address: tuple[str, int] | None = None
    utterance_id: str | None = None
    next_audio_sequence: int = 0
    remote_wire_sequence: int = 0
    local_wire_sequence: int = 0
    output_timestamp: int = 0
    last_activity: float = field(default_factory=monotonic)
    receive_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    playback_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: asyncio.Event = field(default_factory=asyncio.Event)


class _UdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, gateway: DeviceUdpGateway) -> None:
        self.gateway = gateway

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.gateway._transport = cast(asyncio.DatagramTransport, transport)

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.gateway._spawn_datagram(data, addr)

    def error_received(self, exc: Exception) -> None:
        self.gateway._last_error = f"{type(exc).__name__}: {exc}"

    def connection_lost(self, exc: Exception | None) -> None:
        if exc is not None:
            self.gateway._last_error = f"{type(exc).__name__}: {exc}"


class DeviceUdpPlayback:
    """One exclusive TTS control/audio sequence for an MQTT+UDP session."""

    def __init__(self, gateway: DeviceUdpGateway, session: _UdpSession) -> None:
        self._gateway = gateway
        self._session = session
        self._closed = False

    @property
    def device_id(self) -> str:
        return self._session.device_id

    @property
    def session_id(self) -> str:
        return self._session.session_id

    async def send_sentence(self, text: str) -> None:
        normalized = text.strip()
        if not normalized or len(normalized) > 2_000:
            raise ValueError("TTS sentence must contain 1..2000 characters")
        await self._send_control(
            {
                "session_id": self.session_id,
                "type": "tts",
                "state": "sentence_start",
                "text": normalized,
            }
        )

    async def send_audio(self, packet: bytes) -> None:
        self._ensure_open()
        if not isinstance(packet, bytes):
            raise TypeError("TTS packet must be bytes")
        if not packet or len(packet) > self._gateway.config.max_datagram_bytes - _HEADER_BYTES:
            raise ValueError("TTS Opus packet size is invalid")
        if not await self._gateway._is_current(self._session):
            raise DeviceUdpPlaybackError("mqtt_udp_session_not_connected")
        address = self._session.address
        transport = self._gateway._transport
        if address is None:
            raise DeviceUdpPlaybackError("mqtt_udp_device_address_unknown")
        if transport is None or self._gateway._state is not DeviceUdpState.RUNNING:
            raise DeviceUdpPlaybackError("mqtt_udp_gateway_not_running")

        async with self._session.send_lock:
            self._session.local_wire_sequence += 1
            if self._session.output_timestamp == 0:
                self._session.output_timestamp = int(monotonic() * 1_000) & 0xFFFFFFFF
            else:
                self._session.output_timestamp = (
                    self._session.output_timestamp
                    + self._gateway.audio.frame_duration_ms
                ) & 0xFFFFFFFF
            header = bytearray(self._session.nonce)
            struct.pack_into("!H", header, 2, len(packet))
            struct.pack_into("!I", header, 8, self._session.output_timestamp)
            struct.pack_into("!I", header, 12, self._session.local_wire_sequence)
            encrypted = _aes_ctr(self._session.key, bytes(header), packet)
            try:
                transport.sendto(bytes(header) + encrypted, address)
            except Exception as exc:
                raise DeviceUdpPlaybackError("mqtt_udp_audio_write_failed") from exc
            self._session.last_activity = monotonic()
            self._gateway._audio_frames_sent += 1

    async def stop(self) -> None:
        if self._closed:
            return
        try:
            if await self._gateway._is_current(self._session):
                await self._send_control(
                    {
                        "session_id": self.session_id,
                        "type": "tts",
                        "state": "stop",
                    }
                )
        finally:
            self._closed = True
            if self._session.playback_lock.locked():
                self._session.playback_lock.release()

    async def _send_control(self, payload: dict[str, JsonValue]) -> None:
        self._ensure_open()
        if not await self._gateway._is_current(self._session):
            raise DeviceUdpPlaybackError("mqtt_udp_session_not_connected")
        try:
            await self._gateway._send_control(self.device_id, payload)
        except Exception as exc:
            raise DeviceUdpPlaybackError("mqtt_udp_control_write_failed") from exc
        self._session.last_activity = monotonic()

    def _ensure_open(self) -> None:
        if self._closed:
            raise DeviceUdpPlaybackError("tts_playback_is_closed")


class DeviceUdpGateway:
    """Pair MQTT session controls with encrypted UDP Opus datagrams."""

    def __init__(
        self,
        config: DeviceUdpConfig,
        audio: AudioConfig,
        message_bus: MessageBus,
        control_sender: DeviceControlSender,
        *,
        mqtt_enabled: bool = True,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.audio = audio
        self.message_bus = message_bus
        self._send_control = control_sender
        self._enabled = config.enabled and mqtt_enabled
        self._state = DeviceUdpState.CREATED if self._enabled else DeviceUdpState.DISABLED
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: _UdpProtocol | None = None
        self._sessions: dict[str, _UdpSession] = {}
        self._markers: dict[bytes, _UdpSession] = {}
        self._session_lock = asyncio.Lock()
        self._audio_frames: OrderedDict[str, _AudioFrame] = OrderedDict()
        self._audio_ids: dict[str, deque[str]] = {}
        self._audio_lock = asyncio.Lock()
        self._datagram_tasks: set[asyncio.Task[None]] = set()
        self._cleanup_task: asyncio.Task[None] | None = None
        self._logger = logger or logging.getLogger("otto_master.device_udp")
        self._last_error: str | None = None
        self._sessions_opened = 0
        self._sessions_closed = 0
        self._datagrams_received = 0
        self._datagrams_rejected = 0
        self._audio_frames_received = 0
        self._audio_frames_sent = 0
        self._transcriptions_sent = 0
        self._prelisten_audio_frames_dropped = 0

    @property
    def running(self) -> bool:
        return self._state is DeviceUdpState.RUNNING

    async def start(self) -> None:
        if self._state is DeviceUdpState.DISABLED or self.running:
            return
        if self._state not in {
            DeviceUdpState.CREATED,
            DeviceUdpState.STOPPED,
            DeviceUdpState.ERROR,
        }:
            raise DeviceUdpError(f"UDP gateway cannot start from {self._state.value}")
        loop = asyncio.get_running_loop()
        try:
            transport, protocol = await loop.create_datagram_endpoint(
                lambda: _UdpProtocol(self),
                local_addr=(self.config.host, self.config.port),
            )
        except Exception as exc:
            self._state = DeviceUdpState.ERROR
            self._last_error = f"{type(exc).__name__}: {exc}"
            raise DeviceUdpError("failed to bind encrypted UDP audio gateway") from exc
        self._transport = transport
        self._protocol = protocol
        self._state = DeviceUdpState.RUNNING
        self._last_error = None
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(),
            name="otto-device-udp-cleanup",
        )

    async def shutdown(self) -> None:
        if self._state in {DeviceUdpState.DISABLED, DeviceUdpState.STOPPED}:
            return
        if self._state is DeviceUdpState.CREATED:
            self._state = DeviceUdpState.STOPPED
            return
        self._state = DeviceUdpState.STOPPING
        cleanup = self._cleanup_task
        self._cleanup_task = None
        if cleanup is not None:
            cleanup.cancel()
            await asyncio.gather(cleanup, return_exceptions=True)
        transport = self._transport
        self._transport = None
        self._protocol = None
        if transport is not None:
            transport.close()
        tasks = tuple(self._datagram_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._datagram_tasks.clear()
        await self.close_all("udp_gateway_stopped")
        async with self._audio_lock:
            self._audio_frames.clear()
            self._audio_ids.clear()
        self._state = DeviceUdpState.STOPPED

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "healthy": self.running if self._enabled else True,
            "state": self._state.value,
            "host": self.config.host if self._enabled else None,
            "port": self.config.port if self._enabled else None,
            "advertise_host": self.config.advertise_host if self._enabled else None,
            "encryption": "aes-128-ctr" if self._enabled else None,
            "sessions": len(self._sessions),
            "sessions_opened": self._sessions_opened,
            "sessions_closed": self._sessions_closed,
            "datagrams_received": self._datagrams_received,
            "datagrams_rejected": self._datagrams_rejected,
            "audio_frames_received": self._audio_frames_received,
            "audio_frames_sent": self._audio_frames_sent,
            "transcriptions_sent": self._transcriptions_sent,
            "prelisten_audio_frames_dropped": self._prelisten_audio_frames_dropped,
            "audio_frames_buffered": len(self._audio_frames),
            "last_error": self._last_error,
        }

    async def handle_mqtt_value(self, device_id: str, value: dict[str, Any]) -> bool:
        """Consume voice-session MQTT controls; return false for fleet controls."""

        message_type = required_string(value, "type", maximum=64)
        if message_type == "hello" and value.get("transport") == "udp":
            await self._open_session(device_id, value)
            return True
        if message_type not in {"listen", "abort", "goodbye", "mcp"}:
            return False

        session = await self._session_for_control(device_id, value)
        session.last_activity = monotonic()
        if message_type == "listen":
            await self._handle_listen(session, value)
        elif message_type == "abort":
            await self._finish_utterance(session, reason="abort", required=False)
        elif message_type == "goodbye":
            await self._finish_utterance(session, reason="goodbye", required=False)
            await self._close_session(session, "device_goodbye")
        await self.message_bus.publish(heartbeat_message(device_id, "mqtt"))
        return True

    async def close_all(self, reason: str) -> None:
        async with self._session_lock:
            sessions = tuple(self._sessions.values())
        for session in sessions:
            await self._finish_utterance(session, reason=reason, required=False)
            await self._close_session(session, reason)

    async def has_session(self, device_id: str) -> bool:
        async with self._session_lock:
            return device_id in self._sessions

    async def take_audio_frame(
        self,
        device_id: str,
        utterance_id: str,
        sequence: int,
        frame_ref: str,
    ) -> bytes | None:
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

    async def start_tts_playback(self, device_id: str) -> DeviceUdpPlayback:
        async with self._session_lock:
            session = self._sessions.get(device_id)
        if session is None or not await self._is_current(session):
            raise DeviceUdpPlaybackError("mqtt_udp_session_not_connected")
        await session.playback_lock.acquire()
        playback = DeviceUdpPlayback(self, session)
        try:
            await playback._send_control(
                {
                    "session_id": session.session_id,
                    "type": "tts",
                    "state": "start",
                }
            )
        except BaseException:
            try:
                await playback.stop()
            except DeviceUdpPlaybackError:
                pass
            raise
        return playback

    async def send_transcription(
        self,
        device_id: str,
        session_id: str,
        text: str,
    ) -> None:
        """Send final ASR text to exactly one negotiated MQTT/UDP session."""

        normalized = _transcription_text(text)
        async with self._session_lock:
            session = self._sessions.get(device_id)
        if session is None or not await self._is_current(session):
            raise DeviceUdpPlaybackError("mqtt_udp_session_not_connected")
        if session.session_id != session_id:
            raise DeviceUdpPlaybackError("device_session_changed")
        try:
            await self._send_control(
                device_id,
                {
                    "session_id": session.session_id,
                    "type": "stt",
                    "text": normalized,
                },
            )
        except Exception as exc:
            raise DeviceUdpPlaybackError("mqtt_udp_control_write_failed") from exc
        session.last_activity = monotonic()
        self._transcriptions_sent += 1

    async def close_audio_session(self, device_id: str, session_id: str) -> bool:
        """Request an orderly device close and bound the acknowledgement wait."""

        async with self._session_lock:
            session = self._sessions.get(device_id)
        if session is None:
            return False
        if session.session_id != session_id:
            raise DeviceUdpPlaybackError("device_session_changed")
        await self._finish_utterance(
            session,
            reason="server_goodbye",
            required=False,
        )
        try:
            await self._send_control(
                device_id,
                {
                    "session_id": session.session_id,
                    "type": "goodbye",
                },
            )
        except Exception as exc:
            await self._close_session(session, "server_goodbye_publish_failed")
            raise DeviceUdpPlaybackError("mqtt_udp_control_write_failed") from exc
        try:
            await asyncio.wait_for(session.closed.wait(), timeout=1.0)
        except TimeoutError:
            await self._close_session(session, "server_goodbye_timeout")
        return True

    async def _open_session(self, device_id: str, value: dict[str, Any]) -> None:
        if not self.running:
            raise DeviceProtocolError("encrypted UDP gateway is unavailable")
        version = value.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version != 3:
            raise DeviceProtocolError("unsupported MQTT UDP protocol version")
        params = value.get("audio_params")
        if not isinstance(params, dict):
            raise DeviceProtocolError("UDP hello audio_params must be an object")
        if params.get("format") != "opus":
            raise DeviceProtocolError("UDP hello requires Opus audio")
        sample_rate = params.get("sample_rate")
        channels = params.get("channels")
        frame_duration = params.get("frame_duration")
        if sample_rate != self.audio.input_sample_rate:
            raise DeviceProtocolError("UDP input sample rate is unsupported")
        if channels != self.audio.channels:
            raise DeviceProtocolError("UDP input channel count is unsupported")
        if frame_duration != self.audio.frame_duration_ms:
            raise DeviceProtocolError("UDP input frame duration is unsupported")

        previous: _UdpSession | None
        async with self._session_lock:
            previous = self._sessions.get(device_id)
        if previous is not None:
            await self._finish_utterance(previous, reason="session_replaced", required=False)
            await self._close_session(previous, "session_replaced")

        key = secrets.token_bytes(16)
        while True:
            nonce = bytearray(secrets.token_bytes(16))
            nonce[0] = _PACKET_TYPE_OPUS
            nonce[1] = 0
            nonce[2:4] = b"\x00\x00"
            nonce[8:16] = b"\x00" * 8
            marker = bytes(nonce[:2] + nonce[4:8])
            async with self._session_lock:
                if marker not in self._markers:
                    break
        session = _UdpSession(
            device_id=device_id,
            session_id=str(uuid4()),
            key=key,
            nonce=bytes(nonce),
            marker=marker,
            input_sample_rate=sample_rate,
            input_channels=channels,
            input_frame_duration_ms=frame_duration,
        )
        async with self._session_lock:
            self._sessions[device_id] = session
            self._markers[marker] = session
        try:
            await self._send_control(
                device_id,
                {
                    "type": "hello",
                    "transport": "udp",
                    "session_id": session.session_id,
                    "audio_params": {
                        "format": self.audio.format,
                        "sample_rate": self.audio.output_sample_rate,
                        "channels": self.audio.channels,
                        "frame_duration": self.audio.frame_duration_ms,
                    },
                    "udp": {
                        "server": self.config.advertise_host,
                        "port": self.config.port,
                        "encryption": "aes-128-ctr",
                        "key": key.hex(),
                        "nonce": bytes(nonce).hex(),
                    },
                },
            )
        except Exception:
            await self._close_session(session, "server_hello_publish_failed")
            raise
        self._sessions_opened += 1

    async def _session_for_control(
        self,
        device_id: str,
        value: dict[str, Any],
    ) -> _UdpSession:
        async with self._session_lock:
            session = self._sessions.get(device_id)
        if session is None:
            raise DeviceProtocolError("MQTT voice control has no UDP session")
        session_id = required_string(value, "session_id", maximum=128)
        if session_id != session.session_id:
            raise DeviceProtocolError("MQTT voice control session_id does not match")
        return session

    async def _handle_listen(self, session: _UdpSession, value: dict[str, Any]) -> None:
        state = required_string(value, "state", maximum=16)
        if state == "start":
            mode = value.get("mode", "auto")
            if mode not in {"auto", "manual", "realtime"}:
                raise DeviceProtocolError("listen mode is invalid")
            if session.utterance_id is not None:
                await self._finish_utterance(
                    session,
                    reason="listen_restarted",
                    required=True,
                )
            session.utterance_id = str(uuid4())
            session.next_audio_sequence = 0
            await self.message_bus.publish(
                self._audio_lifecycle_message(
                    session,
                    "audio.input.started",
                    {
                        "utterance_id": session.utterance_id,
                        "mode": mode,
                        "codec": "opus",
                        "sample_rate": session.input_sample_rate,
                        "channels": session.input_channels,
                        "frame_duration_ms": session.input_frame_duration_ms,
                        "protocol_version": 3,
                    },
                )
            )
            return
        if state == "stop":
            await self._finish_utterance(session, reason="listen_stop", required=True)
            return
        if state == "detect":
            text = value.get("text", "")
            if not isinstance(text, str) or len(text) > 512:
                raise DeviceProtocolError("wake candidate text is invalid")
            await self.message_bus.publish(
                Message.create(
                    topic="voice.wake.candidate.received",
                    kind=MessageKind.EVENT,
                    source=f"device:{session.device_id}:mqtt",
                    target=f"device:{session.device_id}",
                    payload={
                        "device_id": session.device_id,
                        "transport": "mqtt",
                        "session_id": session.session_id,
                        "text": text,
                    },
                )
            )
            return
        if state == "vad":
            speaking = value.get("speaking")
            if not isinstance(speaking, bool):
                raise DeviceProtocolError("listen VAD speaking must be a boolean")
            if session.utterance_id is None:
                return
            await self.message_bus.publish(
                self._audio_lifecycle_message(
                    session,
                    "audio.input.activity",
                    {
                        "utterance_id": session.utterance_id,
                        "speaking": speaking,
                    },
                )
            )
            return
        raise DeviceProtocolError("listen state is invalid")

    async def _finish_utterance(
        self,
        session: _UdpSession,
        *,
        reason: str,
        required: bool,
    ) -> None:
        utterance_id = session.utterance_id
        if utterance_id is None:
            if required:
                raise DeviceProtocolError("audio input finished without an active utterance")
            return
        frame_count = session.next_audio_sequence
        session.utterance_id = None
        session.next_audio_sequence = 0
        if self.message_bus.running:
            await self.message_bus.publish(
                self._audio_lifecycle_message(
                    session,
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
        session: _UdpSession,
        topic: str,
        extra: dict[str, JsonValue],
    ) -> Message:
        return Message.create(
            topic=topic,
            kind=MessageKind.EVENT,
            source=f"device:{session.device_id}:mqtt",
            target="service:asr",
            payload={
                "device_id": session.device_id,
                "transport": "mqtt",
                "session_id": session.session_id,
                **extra,
            },
        )

    def _spawn_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        if not self.running:
            return
        task = asyncio.create_task(
            self._handle_datagram(bytes(data), addr),
            name="otto-device-udp-datagram",
        )
        self._datagram_tasks.add(task)
        task.add_done_callback(self._datagram_done)

    def _datagram_done(self, task: asyncio.Task[None]) -> None:
        self._datagram_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self._last_error = f"{type(error).__name__}: {error}"
            self._logger.warning(
                "device_udp_datagram_failed",
                exc_info=error,
                extra={"event": "device_udp_datagram_failed"},
            )

    async def _handle_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        self._datagrams_received += 1
        if len(data) <= _HEADER_BYTES or len(data) > self.config.max_datagram_bytes:
            self._datagrams_rejected += 1
            return
        header = data[:_HEADER_BYTES]
        if header[0] != _PACKET_TYPE_OPUS:
            self._datagrams_rejected += 1
            return
        marker = header[:2] + header[4:8]
        async with self._session_lock:
            session = self._markers.get(marker)
        if session is None:
            self._datagrams_rejected += 1
            return

        async with session.receive_lock:
            if not await self._is_current(session):
                self._datagrams_rejected += 1
                return
            payload_length = struct.unpack_from("!H", header, 2)[0]
            if payload_length != len(data) - _HEADER_BYTES:
                self._datagrams_rejected += 1
                return
            if session.address is None:
                session.address = addr
            elif session.address != addr:
                self._datagrams_rejected += 1
                return
            wire_sequence = struct.unpack_from("!I", header, 12)[0]
            if wire_sequence <= session.remote_wire_sequence:
                self._datagrams_rejected += 1
                return
            if wire_sequence != session.remote_wire_sequence + 1:
                self._logger.warning(
                    "device_udp_sequence_gap",
                    extra={
                        "event": "device_udp_sequence_gap",
                        "device_id": session.device_id,
                        "expected": session.remote_wire_sequence + 1,
                        "received": wire_sequence,
                    },
                )
            session.remote_wire_sequence = wire_sequence
            session.last_activity = monotonic()
            payload = _aes_ctr(session.key, header, data[_HEADER_BYTES:])
            utterance_id = session.utterance_id
            if utterance_id is None:
                self._prelisten_audio_frames_dropped += 1
                return
            sequence = session.next_audio_sequence
            frame_ref = f"udp:{uuid4()}"
            async with self._audio_lock:
                ids = self._audio_ids.setdefault(session.device_id, deque())
                while len(ids) >= self.config.audio_buffer_frames_per_device:
                    expired = ids.popleft()
                    self._audio_frames.pop(expired, None)
                ids.append(frame_ref)
                self._audio_frames[frame_ref] = _AudioFrame(
                    device_id=session.device_id,
                    session_id=session.session_id,
                    utterance_id=utterance_id,
                    sequence=sequence,
                    payload=payload,
                )
            session.next_audio_sequence += 1
            self._audio_frames_received += 1
            timestamp = struct.unpack_from("!I", header, 8)[0]
            await self.message_bus.publish(
                Message.create(
                    topic="audio.input.frame",
                    kind=MessageKind.EVENT,
                    source=f"device:{session.device_id}:mqtt",
                    target="service:asr",
                    payload={
                        "device_id": session.device_id,
                        "transport": "mqtt",
                        "session_id": session.session_id,
                        "utterance_id": utterance_id,
                        "frame_ref": frame_ref,
                        "sequence": sequence,
                        "wire_sequence": wire_sequence,
                        "timestamp": timestamp,
                        "byte_length": len(payload),
                        "codec": "opus",
                        "sample_rate": session.input_sample_rate,
                        "channels": session.input_channels,
                        "frame_duration_ms": session.input_frame_duration_ms,
                        "protocol_version": 3,
                    },
                )
            )
            await self.message_bus.publish(heartbeat_message(session.device_id, "mqtt"))

    async def _close_session(self, session: _UdpSession, reason: str) -> None:
        async with self._session_lock:
            if self._sessions.get(session.device_id) is not session:
                session.closed.set()
                return
            self._sessions.pop(session.device_id, None)
            self._markers.pop(session.marker, None)
        if session.playback_lock.locked():
            session.playback_lock.release()
        await self._clear_audio(session.device_id, session.session_id)
        self._sessions_closed += 1
        session.closed.set()
        if self.message_bus.running:
            await self.message_bus.publish(
                voice_session_closed_message(
                    session.device_id,
                    "mqtt",
                    session.session_id,
                    reason,
                )
            )

    async def _clear_audio(self, device_id: str, session_id: str) -> None:
        async with self._audio_lock:
            ids = self._audio_ids.get(device_id)
            if ids is None:
                return
            retained: deque[str] = deque()
            for frame_ref in ids:
                frame = self._audio_frames.get(frame_ref)
                if frame is not None and frame.session_id == session_id:
                    self._audio_frames.pop(frame_ref, None)
                else:
                    retained.append(frame_ref)
            if retained:
                self._audio_ids[device_id] = retained
            else:
                self._audio_ids.pop(device_id, None)

    async def _is_current(self, session: _UdpSession) -> bool:
        async with self._session_lock:
            return self._sessions.get(session.device_id) is session

    async def _cleanup_loop(self) -> None:
        interval = min(max(self.config.session_timeout_seconds / 4, 1), 30)
        while True:
            await asyncio.sleep(interval)
            cutoff = monotonic() - self.config.session_timeout_seconds
            async with self._session_lock:
                stale = tuple(
                    session
                    for session in self._sessions.values()
                    if session.last_activity < cutoff
                )
            for session in stale:
                await self._finish_utterance(
                    session,
                    reason="session_timeout",
                    required=False,
                )
                await self._close_session(session, "session_timeout")


def _aes_ctr(key: bytes, nonce: bytes, payload: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.CTR(nonce))
    cryptor = cipher.encryptor()
    return cryptor.update(payload) + cryptor.finalize()


def _transcription_text(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("transcription text must be a string")
    normalized = text.strip()
    if not normalized or len(normalized) > 512:
        raise ValueError("transcription text must contain 1..512 characters")
    return normalized
