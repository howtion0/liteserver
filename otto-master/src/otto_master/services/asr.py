"""Per-device streaming ASR service with an explicit fail-closed admission gate."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

from ..audio.opus import OpusCodecError, OpusFrameDecoder, OpusParameters
from ..gateways.cloud import CloudProviderError, SpeechRecognitionResult
from ..message_bus import MessageBus
from ..messages import JsonValue, Message, MessageKind


class AudioFrameReader(Protocol):
    async def take_audio_frame(
        self,
        device_id: str,
        utterance_id: str,
        sequence: int,
        frame_ref: str,
    ) -> bytes | None: ...


class StreamingAsrProvider(Protocol):
    def transcribe(
        self,
        pcm_chunks: AsyncIterable[bytes],
        *,
        uid: str,
        sample_rate: int = 16_000,
    ) -> AsyncIterator[SpeechRecognitionResult]: ...


@dataclass(slots=True)
class _Utterance:
    device_id: str
    session_id: str
    utterance_id: str
    decoder: OpusFrameDecoder
    queue: asyncio.Queue[bytes | None]
    expected_sequence: int = 0
    frame_count: int = 0
    input_finished: bool = False
    speech_observed: bool = False
    task: asyncio.Task[None] | None = None
    endpoint_task: asyncio.Task[None] | None = None
    endpoint_method: str | None = None


class AsrService:
    """Decode admitted device utterances and publish normalized transcription facts.

    Input is closed by default.  A caller must arm one exact device audio session;
    only the next utterance from that session can start a cloud ASR request.
    All other frames are consumed from the short-lived frame store and dropped.
    """

    def __init__(
        self,
        message_bus: MessageBus,
        frame_reader: AudioFrameReader,
        provider: StreamingAsrProvider,
        *,
        chunk_frames: int = 3,
        queue_frames: int = 32,
        partial_stability_seconds: float = 1.2,
        logger: logging.Logger | None = None,
    ) -> None:
        if chunk_frames < 1:
            raise ValueError("chunk_frames must be at least 1")
        if queue_frames < chunk_frames:
            raise ValueError("queue_frames must be at least chunk_frames")
        if partial_stability_seconds <= 0:
            raise ValueError("partial_stability_seconds must be positive")
        self.message_bus = message_bus
        self.frame_reader = frame_reader
        self.provider = provider
        self.chunk_frames = chunk_frames
        self.queue_frames = queue_frames
        self.partial_stability_seconds = partial_stability_seconds
        self._logger = logger or logging.getLogger("otto_master.asr")
        self._armed_sessions: dict[str, str] = {}
        self._ignored_utterances: dict[str, tuple[str, str]] = {}
        self._active: dict[str, _Utterance] = {}
        self._subscriptions: list[str] = []
        self._lock = asyncio.Lock()
        self._running = False
        self._started_count = 0
        self._completed_count = 0
        self._failed_count = 0
        self._dropped_frames = 0
        self._endpointed_count = 0
        self._vad_endpointed_count = 0
        self._partial_endpointed_count = 0
        self._endpoint_discarded_frames = 0
        self._endpoint_preserved_frames = 0

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._subscriptions = [
            await self.message_bus.subscribe("audio.input.started", self._handle_started),
            await self.message_bus.subscribe("audio.input.frame", self._handle_frame),
            await self.message_bus.subscribe("audio.input.activity", self._handle_activity),
            await self.message_bus.subscribe("audio.input.finished", self._handle_finished),
        ]
        self._running = True

    async def shutdown(self) -> None:
        if not self._running and not self._subscriptions:
            return
        self._running = False
        for subscription in self._subscriptions:
            await self.message_bus.unsubscribe(subscription)
        self._subscriptions.clear()
        async with self._lock:
            tasks = tuple(
                utterance.task
                for utterance in self._active.values()
                if utterance.task is not None
            )
            self._active.clear()
            self._armed_sessions.clear()
            self._ignored_utterances.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def arm_next_utterance(self, device_id: str, session_id: str) -> None:
        """Allow exactly one future utterance for one current device session."""

        normalized_device = _required(device_id, "device_id")
        normalized_session = _required(session_id, "session_id")
        async with self._lock:
            if normalized_device in self._active:
                raise RuntimeError("cannot arm ASR while an utterance is active")
            self._armed_sessions[normalized_device] = normalized_session

    async def close_device_input(self, device_id: str, *, cancel_active: bool = True) -> None:
        """Fail closed for future audio and optionally cancel the current utterance."""

        task: asyncio.Task[None] | None = None
        async with self._lock:
            self._armed_sessions.pop(device_id, None)
            utterance = self._active.get(device_id)
            if cancel_active and utterance is not None:
                task = utterance.task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "healthy": self._running,
            "state": "running" if self._running else "stopped",
            "armed_devices": len(self._armed_sessions),
            "active_utterances": len(self._active),
            "started": self._started_count,
            "completed": self._completed_count,
            "failed": self._failed_count,
            "dropped_frames": self._dropped_frames,
            "queue_frames": self.queue_frames,
            "chunk_frames": self.chunk_frames,
            "partial_stability_seconds": self.partial_stability_seconds,
            "endpointed": self._endpointed_count,
            "vad_endpointed": self._vad_endpointed_count,
            "partial_endpointed": self._partial_endpointed_count,
            "endpoint_discarded_frames": self._endpoint_discarded_frames,
            "endpoint_preserved_frames": self._endpoint_preserved_frames,
        }

    async def _handle_started(self, message: Message) -> None:
        fields = _audio_identity(message)
        if fields is None:
            return
        device_id, session_id, utterance_id = fields
        publish_started = False
        async with self._lock:
            if device_id in self._active:
                await self._fail_locked(
                    self._active[device_id],
                    "overlapping_utterance",
                )
                return
            armed_session = self._armed_sessions.get(device_id)
            if armed_session != session_id:
                self._ignored_utterances[device_id] = (session_id, utterance_id)
                return
            self._armed_sessions.pop(device_id, None)
            try:
                parameters = _opus_parameters(message)
                decoder = OpusFrameDecoder(parameters)
            except (TypeError, ValueError, OpusCodecError):
                await self._publish_failed(
                    device_id,
                    session_id,
                    utterance_id,
                    "unsupported_audio_format",
                )
                self._failed_count += 1
                return
            utterance = _Utterance(
                device_id=device_id,
                session_id=session_id,
                utterance_id=utterance_id,
                decoder=decoder,
                queue=asyncio.Queue(maxsize=self.queue_frames),
            )
            self._active[device_id] = utterance
            utterance.task = asyncio.create_task(
                self._run_utterance(utterance),
                name=f"otto-asr-{device_id}-{utterance_id}",
            )
            self._started_count += 1
            publish_started = True
        if publish_started:
            await self.message_bus.publish(
                _transcription_message(
                    "voice.transcription.started",
                    MessageKind.EVENT,
                    device_id,
                    session_id,
                    utterance_id,
                    {},
                )
            )

    async def _handle_frame(self, message: Message) -> None:
        fields = _frame_identity(message)
        if fields is None:
            return
        device_id, session_id, utterance_id, sequence, frame_ref = fields
        packet = await self.frame_reader.take_audio_frame(
            device_id,
            utterance_id,
            sequence,
            frame_ref,
        )
        async with self._lock:
            utterance = self._active.get(device_id)
            if (
                utterance is None
                or utterance.session_id != session_id
                or utterance.utterance_id != utterance_id
                or utterance.input_finished
            ):
                self._dropped_frames += 1
                return
            if packet is None:
                await self._fail_locked(utterance, "audio_frame_expired")
                return
            if sequence != utterance.expected_sequence:
                await self._fail_locked(utterance, "audio_sequence_error")
                return
            try:
                pcm = utterance.decoder.decode(packet)
            except (TypeError, ValueError, OpusCodecError):
                await self._fail_locked(utterance, "opus_decode_failed")
                return
            try:
                utterance.queue.put_nowait(pcm)
            except asyncio.QueueFull:
                await self._fail_locked(utterance, "audio_backpressure")
                return
            utterance.expected_sequence += 1
            utterance.frame_count += 1

    async def _handle_activity(self, message: Message) -> None:
        fields = _audio_identity(message)
        speaking = message.payload.get("speaking")
        if fields is None or not isinstance(speaking, bool):
            return
        device_id, session_id, utterance_id = fields
        cancelled: asyncio.Task[None] | None = None
        async with self._lock:
            utterance = self._active.get(device_id)
            if (
                utterance is None
                or utterance.session_id != session_id
                or utterance.utterance_id != utterance_id
                or utterance.input_finished
            ):
                return
            if speaking:
                utterance.speech_observed = True
                cancelled = self._cancel_endpoint_locked(utterance)
            elif utterance.speech_observed:
                self._schedule_endpoint_locked(utterance, "vad_silence")
        if cancelled is not None:
            await asyncio.gather(cancelled, return_exceptions=True)

    async def _handle_finished(self, message: Message) -> None:
        fields = _audio_identity(message)
        if fields is None:
            return
        device_id, session_id, utterance_id = fields
        async with self._lock:
            ignored = self._ignored_utterances.get(device_id)
            if ignored == (session_id, utterance_id):
                self._ignored_utterances.pop(device_id, None)
                return
            utterance = self._active.get(device_id)
            if (
                utterance is None
                or utterance.session_id != session_id
                or utterance.utterance_id != utterance_id
                or utterance.input_finished
            ):
                return
            utterance.input_finished = True
            endpoint_task = utterance.endpoint_task
            if endpoint_task is not None:
                endpoint_task.cancel()
                utterance.endpoint_task = None
                utterance.endpoint_method = None
            try:
                utterance.queue.put_nowait(None)
            except asyncio.QueueFull:
                await self._fail_locked(utterance, "audio_backpressure")

    async def _run_utterance(self, utterance: _Utterance) -> None:
        final_seen = False
        try:
            async for result in self.provider.transcribe(
                self._pcm_chunks(utterance),
                uid=utterance.device_id,
                sample_rate=utterance.decoder.parameters.sample_rate,
            ):
                topic = (
                    "voice.transcription.completed"
                    if result.is_final
                    else "voice.transcription.partial"
                )
                await self.message_bus.publish(
                    _transcription_message(
                        topic,
                        MessageKind.RESULT if result.is_final else MessageKind.EVENT,
                        utterance.device_id,
                        utterance.session_id,
                        utterance.utterance_id,
                        {
                            "text": result.text,
                            "is_final": result.is_final,
                            "provider": result.provider,
                            "provider_request_id": result.request_id,
                        },
                    )
                )
                if result.is_final:
                    final_seen = True
                    self._completed_count += 1
                    break
                self._schedule_endpoint_locked(utterance, "partial_stability")
            if not final_seen:
                raise CloudProviderError(
                    "asr",
                    "stream_ended_without_final",
                    retryable=True,
                    request_id=utterance.utterance_id,
                )
        except asyncio.CancelledError:
            raise
        except CloudProviderError as exc:
            self._failed_count += 1
            await self._publish_failed(
                utterance.device_id,
                utterance.session_id,
                utterance.utterance_id,
                exc.code,
                retryable=exc.retryable,
                provider_request_id=exc.request_id,
            )
        except Exception:
            self._failed_count += 1
            self._logger.exception(
                "asr_utterance_failed",
                extra={
                    "event": "asr_utterance_failed",
                    "device_id": utterance.device_id,
                    "utterance_id": utterance.utterance_id,
                },
            )
            await self._publish_failed(
                utterance.device_id,
                utterance.session_id,
                utterance.utterance_id,
                "asr_processing_failed",
            )
        finally:
            endpoint_task = utterance.endpoint_task
            if endpoint_task is not None and endpoint_task is not asyncio.current_task():
                endpoint_task.cancel()
                await asyncio.gather(endpoint_task, return_exceptions=True)
            async with self._lock:
                if self._active.get(utterance.device_id) is utterance:
                    self._active.pop(utterance.device_id, None)

    def _schedule_endpoint_locked(self, utterance: _Utterance, method: str) -> None:
        previous = utterance.endpoint_task
        if previous is not None and not previous.done():
            previous.cancel()
        utterance.endpoint_method = method
        utterance.endpoint_task = asyncio.create_task(
            self._finish_after_endpoint_stability(utterance, method),
            name=f"otto-asr-endpoint-{utterance.device_id}-{utterance.utterance_id}",
        )

    def _cancel_endpoint_locked(self, utterance: _Utterance) -> asyncio.Task[None] | None:
        previous = utterance.endpoint_task
        utterance.endpoint_task = None
        utterance.endpoint_method = None
        if previous is not None and not previous.done():
            previous.cancel()
            return previous
        return None

    async def _finish_after_endpoint_stability(
        self,
        utterance: _Utterance,
        method: str,
    ) -> None:
        await asyncio.sleep(self.partial_stability_seconds)
        preserved = 0
        current = asyncio.current_task()
        async with self._lock:
            if (
                self._active.get(utterance.device_id) is not utterance
                or utterance.input_finished
                or utterance.endpoint_task is not current
                or utterance.endpoint_method != method
            ):
                return
            utterance.input_finished = True
            preserved = utterance.queue.qsize()
            self._endpointed_count += 1
            if method == "vad_silence":
                self._vad_endpointed_count += 1
            else:
                self._partial_endpointed_count += 1
            self._endpoint_preserved_frames += preserved
        # No new frames can enter after input_finished is set. Put the sentinel
        # behind every frame already accepted so the cloud stream receives the
        # complete buffered tail instead of truncating the user's last words.
        await utterance.queue.put(None)
        await self.message_bus.publish(
            _transcription_message(
                "voice.endpoint.detected",
                MessageKind.EVENT,
                utterance.device_id,
                utterance.session_id,
                utterance.utterance_id,
                {
                    "method": method,
                    "grace_seconds": self.partial_stability_seconds,
                    "discarded_frames": 0,
                    "preserved_frames": preserved,
                },
            )
        )

    async def _pcm_chunks(self, utterance: _Utterance) -> AsyncIterator[bytes]:
        pending = bytearray()
        frame_bytes = utterance.decoder.parameters.pcm_bytes_per_frame
        target_bytes = frame_bytes * self.chunk_frames
        while True:
            frame = await utterance.queue.get()
            if frame is None:
                if pending:
                    yield bytes(pending)
                return
            pending.extend(frame)
            while len(pending) >= target_bytes:
                yield bytes(pending[:target_bytes])
                del pending[:target_bytes]

    async def _fail_locked(self, utterance: _Utterance, code: str) -> None:
        if self._active.get(utterance.device_id) is utterance:
            self._active.pop(utterance.device_id, None)
        task = utterance.task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        self._failed_count += 1
        await self._publish_failed(
            utterance.device_id,
            utterance.session_id,
            utterance.utterance_id,
            code,
        )

    async def _publish_failed(
        self,
        device_id: str,
        session_id: str,
        utterance_id: str,
        code: str,
        *,
        retryable: bool = False,
        provider_request_id: str | None = None,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "error_code": code[:128],
            "retryable": retryable,
        }
        if provider_request_id is not None:
            payload["provider_request_id"] = provider_request_id
        await self.message_bus.publish(
            _transcription_message(
                "voice.transcription.failed",
                MessageKind.RESULT,
                device_id,
                session_id,
                utterance_id,
                payload,
            )
        )


def _audio_identity(message: Message) -> tuple[str, str, str] | None:
    device_id = message.payload.get("device_id")
    session_id = message.payload.get("session_id")
    utterance_id = message.payload.get("utterance_id")
    if not all(isinstance(value, str) and value for value in (device_id, session_id, utterance_id)):
        return None
    return str(device_id), str(session_id), str(utterance_id)


def _frame_identity(message: Message) -> tuple[str, str, str, int, str] | None:
    identity = _audio_identity(message)
    sequence = message.payload.get("sequence")
    frame_ref = message.payload.get("frame_ref")
    if (
        identity is None
        or not isinstance(sequence, int)
        or isinstance(sequence, bool)
        or sequence < 0
        or not isinstance(frame_ref, str)
        or not frame_ref
    ):
        return None
    return (*identity, sequence, frame_ref)


def _opus_parameters(message: Message) -> OpusParameters:
    if message.payload.get("codec") != "opus":
        raise ValueError("audio codec must be opus")
    sample_rate = message.payload.get("sample_rate")
    channels = message.payload.get("channels")
    frame_duration = message.payload.get("frame_duration_ms")
    if (
        not isinstance(sample_rate, int)
        or isinstance(sample_rate, bool)
        or sample_rate != 16_000
        or not isinstance(channels, int)
        or isinstance(channels, bool)
        or channels != 1
        or not isinstance(frame_duration, int)
        or isinstance(frame_duration, bool)
        or frame_duration != 60
    ):
        raise ValueError("ASR requires 16000 Hz mono 60 ms Opus")
    return OpusParameters(
        sample_rate=sample_rate,
        channels=channels,
        frame_duration_ms=frame_duration,
    )


def _transcription_message(
    topic: str,
    kind: MessageKind,
    device_id: str,
    session_id: str,
    utterance_id: str,
    extra: dict[str, JsonValue],
) -> Message:
    return Message.create(
        topic=topic,
        kind=kind,
        source="service:asr",
        target=f"device:{device_id}",
        correlation_id=utterance_id,
        payload={
            "device_id": device_id,
            "session_id": session_id,
            "utterance_id": utterance_id,
            **extra,
        },
    )


def _required(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty")
    return value.strip()
