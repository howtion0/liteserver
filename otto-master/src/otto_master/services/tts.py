"""Ordered sentence-level TTS synthesis and paced device playback."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from ..audio.opus import OpusParameters, StreamingOpusEncoder
from ..message_bus import MessageBus
from ..messages import JsonValue, Message, MessageKind


class StreamingTtsProvider(Protocol):
    def synthesize(
        self,
        text: str,
        *,
        sample_rate: int = 24_000,
    ) -> AsyncIterator[bytes]: ...


class TtsPlayback(Protocol):
    @property
    def device_id(self) -> str: ...

    @property
    def session_id(self) -> str: ...

    async def send_sentence(self, text: str) -> None: ...

    async def send_audio(self, packet: bytes) -> None: ...

    async def stop(self) -> None: ...


class DeviceTtsSink(Protocol):
    async def start_tts_playback(self, device_id: str) -> TtsPlayback: ...


@dataclass(frozen=True, slots=True)
class TtsPlaybackResult:
    playback_id: str
    device_id: str
    session_id: str
    sentence_count: int
    frame_count: int
    padding_bytes: int


@dataclass(frozen=True, slots=True)
class _PipelineFailure:
    error: Exception


class _PipelineDone:
    pass


@dataclass(frozen=True, slots=True)
class _SentenceStart:
    index: int
    text: str


@dataclass(frozen=True, slots=True)
class _PcmChunk:
    index: int
    payload: bytes


@dataclass(frozen=True, slots=True)
class _SentenceEnd:
    index: int
    pcm_bytes: int


@dataclass(frozen=True, slots=True)
class _OpusFrame:
    index: int
    payload: bytes


@dataclass(frozen=True, slots=True)
class _EncodedSentenceEnd:
    index: int
    pcm_bytes: int
    opus_frames: int


@dataclass(frozen=True, slots=True)
class _EncodedDone:
    padding_bytes: int


_PIPELINE_DONE = _PipelineDone()


class _FramePacer:
    def __init__(self, frame_duration_ms: int, *, enabled: bool) -> None:
        self._interval = frame_duration_ms / 1_000
        self._enabled = enabled
        self._next_deadline: float | None = None

    async def wait(self) -> None:
        if not self._enabled:
            return
        loop = asyncio.get_running_loop()
        now = loop.time()
        if self._next_deadline is None or now - self._next_deadline > self._interval:
            self._next_deadline = now
        delay = self._next_deadline - now
        if delay > 0:
            await asyncio.sleep(delay)
        self._next_deadline += self._interval


class TtsService:
    """Turn a streamed sentence sequence into one strict TTS playback session."""

    def __init__(
        self,
        message_bus: MessageBus,
        sink: DeviceTtsSink,
        provider: StreamingTtsProvider,
        *,
        sample_rate: int = 24_000,
        channels: int = 1,
        frame_duration_ms: int = 60,
        pace_audio: bool = True,
        sentence_queue_size: int = 4,
        pcm_queue_size: int = 16,
        opus_queue_size: int = 48,
    ) -> None:
        if min(sentence_queue_size, pcm_queue_size, opus_queue_size) < 1:
            raise ValueError("TTS pipeline queue sizes must be positive")
        self.message_bus = message_bus
        self.sink = sink
        self.provider = provider
        self.parameters = OpusParameters(
            sample_rate=sample_rate,
            channels=channels,
            frame_duration_ms=frame_duration_ms,
        )
        self.pace_audio = pace_audio
        self.sentence_queue_size = sentence_queue_size
        self.pcm_queue_size = pcm_queue_size
        self.opus_queue_size = opus_queue_size
        self._active: dict[str, asyncio.Task[Any]] = {}
        self._lock = asyncio.Lock()
        self._completed = 0
        self._failed = 0

    def status(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "healthy": True,
            "state": "running",
            "active_playbacks": len(self._active),
            "completed": self._completed,
            "failed": self._failed,
            "pipeline": {
                "sentence_queue_size": self.sentence_queue_size,
                "pcm_queue_size": self.pcm_queue_size,
                "opus_queue_size": self.opus_queue_size,
            },
        }

    async def play_sentences(
        self,
        device_id: str,
        sentences: AsyncIterable[str],
        *,
        correlation_id: str | None = None,
    ) -> TtsPlaybackResult:
        normalized_device = _required(device_id, "device_id")
        current_task = asyncio.current_task()
        if current_task is None:
            raise RuntimeError("TTS playback requires an asyncio task")
        async with self._lock:
            if normalized_device in self._active:
                raise RuntimeError("a TTS playback is already active for this device")
            self._active[normalized_device] = current_task
        playback_id = str(uuid4())
        playback: TtsPlayback | None = None
        primary_error: BaseException | None = None
        stopped = False
        sentence_count = 0
        frame_count = 0
        encoder = StreamingOpusEncoder(self.parameters)
        pacer = _FramePacer(
            self.parameters.frame_duration_ms,
            enabled=self.pace_audio,
        )
        iterator = sentences.__aiter__()
        pipeline_tasks: list[asyncio.Task[None]] = []
        try:
            try:
                first_sentence = _required(await anext(iterator), "TTS sentence")
            except StopAsyncIteration:
                return TtsPlaybackResult(
                    playback_id=playback_id,
                    device_id=normalized_device,
                    session_id="",
                    sentence_count=0,
                    frame_count=0,
                    padding_bytes=0,
                )
            playback = await self.sink.start_tts_playback(normalized_device)
            await self._publish(
                "tts.playback.started",
                MessageKind.EVENT,
                playback,
                playback_id,
                correlation_id,
                {},
            )
            sentence_queue: asyncio.Queue[str | _PipelineFailure | _PipelineDone] = (
                asyncio.Queue(maxsize=self.sentence_queue_size)
            )
            pcm_queue: asyncio.Queue[
                _SentenceStart | _PcmChunk | _SentenceEnd | _PipelineFailure | _PipelineDone
            ] = asyncio.Queue(maxsize=self.pcm_queue_size)
            opus_queue: asyncio.Queue[
                _SentenceStart
                | _OpusFrame
                | _EncodedSentenceEnd
                | _EncodedDone
                | _PipelineFailure
            ] = asyncio.Queue(maxsize=self.opus_queue_size)
            sentence_queue.put_nowait(first_sentence)
            pipeline_tasks = [
                asyncio.create_task(
                    self._produce_sentences(iterator, sentence_queue),
                    name=f"otto-tts-sentences-{normalized_device}",
                ),
                asyncio.create_task(
                    self._produce_pcm(sentence_queue, pcm_queue),
                    name=f"otto-tts-pcm-{normalized_device}",
                ),
                asyncio.create_task(
                    self._produce_opus(pcm_queue, opus_queue, encoder),
                    name=f"otto-tts-opus-{normalized_device}",
                ),
            ]
            sentence_count, frame_count = await self._consume_playback(
                playback,
                opus_queue,
                pacer,
                playback_id,
                correlation_id,
            )
        except BaseException as exc:
            primary_error = exc
            if isinstance(exc, Exception):
                self._failed += 1
                if playback is not None:
                    await self._publish(
                        "tts.playback.failed",
                        MessageKind.RESULT,
                        playback,
                        playback_id,
                        correlation_id,
                        {"error_code": _stable_error_code(exc)},
                    )
            raise
        finally:
            for task in pipeline_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pipeline_tasks, return_exceptions=True)
            if playback is not None:
                try:
                    await playback.stop()
                    stopped = True
                except Exception:
                    if primary_error is None:
                        self._failed += 1
                        raise
                finally:
                    if stopped:
                        await self._publish(
                            "tts.playback.finished",
                            MessageKind.RESULT,
                            playback,
                            playback_id,
                            correlation_id,
                            {
                                "sentence_count": sentence_count,
                                "frame_count": frame_count,
                                "padding_bytes": encoder.padding_bytes,
                            },
                        )
            async with self._lock:
                if self._active.get(normalized_device) is current_task:
                    self._active.pop(normalized_device, None)
        self._completed += 1
        if playback is None:
            raise RuntimeError("TTS playback did not start")
        return TtsPlaybackResult(
            playback_id=playback_id,
            device_id=normalized_device,
            session_id=playback.session_id,
            sentence_count=sentence_count,
            frame_count=frame_count,
            padding_bytes=encoder.padding_bytes,
        )

    async def cancel_device(self, device_id: str) -> bool:
        async with self._lock:
            task = self._active.get(device_id)
        if task is None:
            return False
        task.cancel()
        if task is not asyncio.current_task():
            await asyncio.gather(task, return_exceptions=True)
        return True

    async def _produce_sentences(
        self,
        iterator: AsyncIterator[str],
        output: asyncio.Queue[str | _PipelineFailure | _PipelineDone],
    ) -> None:
        try:
            async for sentence in iterator:
                await output.put(_required(sentence, "TTS sentence"))
            await output.put(_PIPELINE_DONE)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - iterator failures cross the queue boundary
            await output.put(_PipelineFailure(exc))

    async def _produce_pcm(
        self,
        sentences: asyncio.Queue[str | _PipelineFailure | _PipelineDone],
        output: asyncio.Queue[
            _SentenceStart | _PcmChunk | _SentenceEnd | _PipelineFailure | _PipelineDone
        ],
    ) -> None:
        index = 0
        try:
            while True:
                item = await sentences.get()
                if isinstance(item, _PipelineDone):
                    await output.put(_PIPELINE_DONE)
                    return
                if isinstance(item, _PipelineFailure):
                    raise item.error
                await output.put(_SentenceStart(index, item))
                pcm_bytes = 0
                async for pcm in self.provider.synthesize(
                    item,
                    sample_rate=self.parameters.sample_rate,
                ):
                    if not isinstance(pcm, bytes):
                        raise TypeError("TTS provider yielded a non-bytes PCM chunk")
                    if not pcm or len(pcm) > 1024 * 1024:
                        raise ValueError("TTS provider yielded an invalid PCM chunk")
                    pcm_bytes += len(pcm)
                    await output.put(_PcmChunk(index, pcm))
                await output.put(_SentenceEnd(index, pcm_bytes))
                index += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - provider failures cross the queue boundary
            await output.put(_PipelineFailure(exc))

    async def _produce_opus(
        self,
        pcm: asyncio.Queue[
            _SentenceStart | _PcmChunk | _SentenceEnd | _PipelineFailure | _PipelineDone
        ],
        output: asyncio.Queue[
            _SentenceStart
            | _OpusFrame
            | _EncodedSentenceEnd
            | _EncodedDone
            | _PipelineFailure
        ],
        encoder: StreamingOpusEncoder,
    ) -> None:
        frames: dict[int, int] = {}
        last_index = 0
        try:
            while True:
                item = await pcm.get()
                if isinstance(item, _PipelineDone):
                    for packet in encoder.finish():
                        await output.put(_OpusFrame(last_index, packet))
                    await output.put(_EncodedDone(encoder.padding_bytes))
                    return
                if isinstance(item, _PipelineFailure):
                    raise item.error
                if isinstance(item, _SentenceStart):
                    last_index = item.index
                    frames[item.index] = 0
                    await output.put(item)
                elif isinstance(item, _PcmChunk):
                    last_index = item.index
                    for packet in encoder.feed(item.payload):
                        frames[item.index] = frames.get(item.index, 0) + 1
                        await output.put(_OpusFrame(item.index, packet))
                elif isinstance(item, _SentenceEnd):
                    await output.put(
                        _EncodedSentenceEnd(
                            item.index,
                            item.pcm_bytes,
                            frames.get(item.index, 0),
                        )
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - encoder failures cross the queue boundary
            await output.put(_PipelineFailure(exc))

    async def _consume_playback(
        self,
        playback: TtsPlayback,
        queue: asyncio.Queue[
            _SentenceStart
            | _OpusFrame
            | _EncodedSentenceEnd
            | _EncodedDone
            | _PipelineFailure
        ],
        pacer: _FramePacer,
        playback_id: str,
        correlation_id: str | None,
    ) -> tuple[int, int]:
        sentence_count = 0
        frame_count = 0
        while True:
            item = await queue.get()
            if isinstance(item, _PipelineFailure):
                raise item.error
            if isinstance(item, _EncodedDone):
                return sentence_count, frame_count
            if isinstance(item, _SentenceStart):
                await playback.send_sentence(item.text)
                await self._publish(
                    "tts.synthesis.started",
                    MessageKind.EVENT,
                    playback,
                    playback_id,
                    correlation_id,
                    {"sentence_index": item.index, "text": item.text},
                )
            elif isinstance(item, _OpusFrame):
                await pacer.wait()
                await playback.send_audio(item.payload)
                frame_count += 1
            elif isinstance(item, _EncodedSentenceEnd):
                sentence_count += 1
                await self._publish(
                    "tts.synthesis.completed",
                    MessageKind.RESULT,
                    playback,
                    playback_id,
                    correlation_id,
                    {
                        "sentence_index": item.index,
                        "pcm_bytes": item.pcm_bytes,
                        "opus_frames": item.opus_frames,
                    },
                )

    async def _publish(
        self,
        topic: str,
        kind: MessageKind,
        playback: TtsPlayback,
        playback_id: str,
        correlation_id: str | None,
        extra: dict[str, JsonValue],
    ) -> None:
        await self.message_bus.publish(
            Message.create(
                topic=topic,
                kind=kind,
                source="service:tts",
                target=f"device:{playback.device_id}",
                correlation_id=correlation_id or playback_id,
                payload={
                    "device_id": playback.device_id,
                    "session_id": playback.session_id,
                    "playback_id": playback_id,
                    **extra,
                },
            )
        )


def _stable_error_code(error: Exception) -> str:
    code = getattr(error, "code", None)
    if isinstance(code, str) and code:
        return code[:128]
    message = str(error)
    if message in {
        "websocket_device_not_connected",
        "websocket_audio_write_failed",
        "websocket_control_write_failed",
        "mqtt_udp_session_not_connected",
        "mqtt_udp_device_address_unknown",
        "mqtt_udp_gateway_not_running",
        "mqtt_udp_audio_write_failed",
        "mqtt_udp_control_write_failed",
        "tts_playback_is_closed",
    }:
        return message
    return "tts_playback_failed"


def _required(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty")
    return value.strip()
