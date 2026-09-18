from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator

import pytest

from otto_master.audio.opus import OpusParameters, StreamingOpusEncoder
from otto_master.gateways.cloud import CloudProviderError, SpeechRecognitionResult
from otto_master.message_bus import MessageBus
from otto_master.messages import Message, MessageKind
from otto_master.services.asr import AsrService


class FakeFrameReader:
    def __init__(self) -> None:
        self.frames: dict[str, bytes] = {}

    async def take_audio_frame(
        self,
        device_id: str,
        utterance_id: str,
        sequence: int,
        frame_ref: str,
    ) -> bytes | None:
        return self.frames.pop(frame_ref, None)


class FakeAsrProvider:
    def __init__(self, *, failure: CloudProviderError | None = None) -> None:
        self.calls = 0
        self.received: list[list[bytes]] = []
        self.failure = failure

    async def transcribe(
        self,
        pcm_chunks: AsyncIterable[bytes],
        *,
        uid: str,
        sample_rate: int = 16_000,
    ) -> AsyncIterator[SpeechRecognitionResult]:
        self.calls += 1
        chunks = [chunk async for chunk in pcm_chunks]
        self.received.append(chunks)
        if self.failure is not None:
            raise self.failure
        yield SpeechRecognitionResult("你好", False, "fake-request")
        yield SpeechRecognitionResult("你好。", True, "fake-request")


def _audio_message(topic: str, device: str, session: str, utterance: str) -> Message:
    return Message.create(
        topic=topic,
        kind=MessageKind.EVENT,
        source=f"device:{device}:websocket",
        target="service:asr",
        payload={
            "device_id": device,
            "session_id": session,
            "utterance_id": utterance,
            "codec": "opus",
            "sample_rate": 16_000,
            "channels": 1,
            "frame_duration_ms": 60,
        },
    )


def _frame_message(
    device: str,
    session: str,
    utterance: str,
    sequence: int,
    frame_ref: str,
) -> Message:
    message = _audio_message("audio.input.frame", device, session, utterance)
    return Message.create(
        topic=message.topic,
        kind=message.kind,
        source=message.source,
        target=message.target,
        payload={**message.payload, "sequence": sequence, "frame_ref": frame_ref},
    )


def _activity_message(
    device: str,
    session: str,
    utterance: str,
    speaking: bool,
) -> Message:
    message = _audio_message("audio.input.activity", device, session, utterance)
    return Message.create(
        topic=message.topic,
        kind=message.kind,
        source=message.source,
        target=message.target,
        payload={**message.payload, "speaking": speaking},
    )


async def _wait_topic(observed: list[Message], topic: str) -> Message:
    deadline = asyncio.get_running_loop().time() + 2
    while asyncio.get_running_loop().time() < deadline:
        for message in observed:
            if message.topic == topic:
                return message
        await asyncio.sleep(0.01)
    raise AssertionError(f"message topic was not published: {topic}")


@pytest.mark.asyncio
async def test_asr_is_closed_by_default_and_drops_unarmed_laughter_audio() -> None:
    bus = MessageBus()
    reader = FakeFrameReader()
    provider = FakeAsrProvider()
    service = AsrService(bus, reader, provider)
    packet = StreamingOpusEncoder(OpusParameters(16_000)).feed(b"\x00" * 1_920)[0]
    reader.frames["laugh-frame"] = packet

    await bus.start()
    await service.start()
    try:
        await bus.publish(_audio_message("audio.input.started", "eva000000001", "s1", "u1"))
        await bus.drain()
        await bus.publish(_frame_message("eva000000001", "s1", "u1", 0, "laugh-frame"))
        await bus.publish(_audio_message("audio.input.finished", "eva000000001", "s1", "u1"))
        await bus.drain()

        assert provider.calls == 0
        assert reader.frames == {}
        assert service.status()["dropped_frames"] == 1
        assert service.status()["active_utterances"] == 0
    finally:
        await service.shutdown()
        await bus.stop()


@pytest.mark.asyncio
async def test_asr_armed_session_decodes_three_frames_and_publishes_final() -> None:
    bus = MessageBus()
    reader = FakeFrameReader()
    provider = FakeAsrProvider()
    service = AsrService(bus, reader, provider, chunk_frames=3)
    observed: list[Message] = []
    parameters = OpusParameters(16_000)
    encoder = StreamingOpusEncoder(parameters)
    packets = encoder.feed(b"\x00" * parameters.pcm_bytes_per_frame * 3)
    for sequence, packet in enumerate(packets):
        reader.frames[f"frame-{sequence}"] = packet

    await bus.start()
    observer = await bus.subscribe_observer(observed.append)
    await service.start()
    try:
        await service.arm_next_utterance("eva000000001", "s1")
        await bus.publish(_audio_message("audio.input.started", "eva000000001", "s1", "u1"))
        await bus.drain()
        for sequence in range(3):
            await bus.publish(
                _frame_message(
                    "eva000000001",
                    "s1",
                    "u1",
                    sequence,
                    f"frame-{sequence}",
                )
            )
            await bus.drain()
        await bus.publish(_audio_message("audio.input.finished", "eva000000001", "s1", "u1"))
        await bus.drain()
        completed = await _wait_topic(observed, "voice.transcription.completed")

        assert provider.calls == 1
        assert [len(chunk) for chunk in provider.received[0]] == [
            parameters.pcm_bytes_per_frame * 3
        ]
        assert completed.payload["text"] == "你好。"
        assert completed.payload["device_id"] == "eva000000001"
        assert completed.payload["utterance_id"] == "u1"
        assert any(message.topic == "voice.transcription.partial" for message in observed)
        assert service.status()["completed"] == 1
    finally:
        await service.shutdown()
        await bus.unsubscribe_observer(observer)
        await bus.stop()


@pytest.mark.asyncio
async def test_asr_sequence_error_fails_closed_and_does_not_consume_other_device() -> None:
    bus = MessageBus()
    reader = FakeFrameReader()
    provider = FakeAsrProvider()
    service = AsrService(bus, reader, provider)
    observed: list[Message] = []
    packet = StreamingOpusEncoder(OpusParameters(16_000)).feed(b"\x00" * 1_920)[0]
    reader.frames.update({"wrong-sequence": packet, "eva2-laugh": packet})

    await bus.start()
    observer = await bus.subscribe_observer(observed.append)
    await service.start()
    try:
        await service.arm_next_utterance("eva000000001", "s1")
        await bus.publish(_audio_message("audio.input.started", "eva000000001", "s1", "u1"))
        await bus.publish(_audio_message("audio.input.started", "eva000000002", "s2", "u2"))
        await bus.drain()
        await bus.publish(
            _frame_message("eva000000001", "s1", "u1", 1, "wrong-sequence")
        )
        await bus.publish(_frame_message("eva000000002", "s2", "u2", 0, "eva2-laugh"))
        await bus.drain()
        failed = await _wait_topic(observed, "voice.transcription.failed")

        assert failed.payload["device_id"] == "eva000000001"
        assert failed.payload["error_code"] == "audio_sequence_error"
        assert reader.frames == {}
        assert service.status()["active_utterances"] == 0
        assert service.status()["dropped_frames"] == 1
    finally:
        await service.shutdown()
        await bus.unsubscribe_observer(observer)
        await bus.stop()


@pytest.mark.asyncio
async def test_asr_provider_error_is_normalized_without_secret_text() -> None:
    bus = MessageBus()
    reader = FakeFrameReader()
    provider = FakeAsrProvider(
        failure=CloudProviderError(
            "volc_asr",
            "rate_limited",
            retryable=True,
            request_id="safe-request-id",
        )
    )
    service = AsrService(bus, reader, provider)
    observed: list[Message] = []
    packet = StreamingOpusEncoder(OpusParameters(16_000)).feed(b"\x00" * 1_920)[0]
    reader.frames["frame-0"] = packet

    await bus.start()
    observer = await bus.subscribe_observer(observed.append)
    await service.start()
    try:
        await service.arm_next_utterance("eva000000001", "s1")
        await bus.publish(_audio_message("audio.input.started", "eva000000001", "s1", "u1"))
        await bus.drain()
        await bus.publish(_frame_message("eva000000001", "s1", "u1", 0, "frame-0"))
        await bus.publish(_audio_message("audio.input.finished", "eva000000001", "s1", "u1"))
        await bus.drain()
        failed = await _wait_topic(observed, "voice.transcription.failed")

        assert failed.payload["error_code"] == "rate_limited"
        assert failed.payload["retryable"] is True
        assert failed.payload["provider_request_id"] == "safe-request-id"
    finally:
        await service.shutdown()
        await bus.unsubscribe_observer(observer)
        await bus.stop()


@pytest.mark.asyncio
async def test_asr_partial_stability_closes_stream_without_device_stop() -> None:
    class FinalOnInputCloseProvider:
        def __init__(self) -> None:
            self.input_closed = asyncio.Event()
            self.received: list[bytes] = []

        async def transcribe(
            self,
            pcm_chunks: AsyncIterable[bytes],
            *,
            uid: str,
            sample_rate: int = 16_000,
        ) -> AsyncIterator[SpeechRecognitionResult]:
            chunks = pcm_chunks.__aiter__()
            self.received.append(await anext(chunks))
            yield SpeechRecognitionResult("你是谁呀", False, "stream-request")
            await asyncio.sleep(0.03)
            async for chunk in chunks:
                self.received.append(chunk)
            self.input_closed.set()
            yield SpeechRecognitionResult("你是谁呀？", True, "stream-request")

    bus = MessageBus()
    reader = FakeFrameReader()
    provider = FinalOnInputCloseProvider()
    service = AsrService(
        bus,
        reader,
        provider,
        chunk_frames=1,
        partial_stability_seconds=0.01,
    )
    observed: list[Message] = []
    parameters = OpusParameters(16_000)
    packets = StreamingOpusEncoder(parameters).feed(
        b"\x00" * parameters.pcm_bytes_per_frame * 3
    )
    for sequence, packet in enumerate(packets):
        reader.frames[f"frame-{sequence}"] = packet

    await bus.start()
    observer = await bus.subscribe_observer(observed.append)
    await service.start()
    try:
        await service.arm_next_utterance("eva000000001", "s1")
        await bus.publish(_audio_message("audio.input.started", "eva000000001", "s1", "u1"))
        for sequence in range(3):
            await bus.publish(
                _frame_message(
                    "eva000000001",
                    "s1",
                    "u1",
                    sequence,
                    f"frame-{sequence}",
                )
            )
        await bus.drain()

        completed = await _wait_topic(observed, "voice.transcription.completed")
        endpoint = await _wait_topic(observed, "voice.endpoint.detected")

        assert provider.input_closed.is_set()
        assert completed.payload["text"] == "你是谁呀？"
        assert endpoint.payload["method"] == "partial_stability"
        assert endpoint.payload["discarded_frames"] == 0
        assert endpoint.payload["preserved_frames"] == 2
        assert len(provider.received) == 3
        assert service.status()["endpointed"] == 1
        assert service.status()["endpoint_discarded_frames"] == 0
        assert service.status()["endpoint_preserved_frames"] == 2
        assert service.status()["completed"] == 1
    finally:
        await service.shutdown()
        await bus.unsubscribe_observer(observer)
        await bus.stop()


@pytest.mark.asyncio
async def test_asr_stable_vad_silence_closes_stream_when_provider_has_no_partial() -> None:
    class FinalOnInputCloseProvider:
        def __init__(self) -> None:
            self.input_closed = asyncio.Event()
            self.received: list[bytes] = []

        async def transcribe(
            self,
            pcm_chunks: AsyncIterable[bytes],
            *,
            uid: str,
            sample_rate: int = 16_000,
        ) -> AsyncIterator[SpeechRecognitionResult]:
            async for chunk in pcm_chunks:
                self.received.append(chunk)
            self.input_closed.set()
            yield SpeechRecognitionResult("奶龙在呢！", True, "stream-request")

    bus = MessageBus()
    reader = FakeFrameReader()
    provider = FinalOnInputCloseProvider()
    service = AsrService(
        bus,
        reader,
        provider,
        chunk_frames=1,
        partial_stability_seconds=0.02,
    )
    observed: list[Message] = []
    parameters = OpusParameters(16_000)
    packets = StreamingOpusEncoder(parameters).feed(
        b"\x00" * parameters.pcm_bytes_per_frame * 3
    )
    for sequence, packet in enumerate(packets):
        reader.frames[f"frame-{sequence}"] = packet

    await bus.start()
    observer = await bus.subscribe_observer(observed.append)
    await service.start()
    try:
        await service.arm_next_utterance("eva000000001", "s1")
        await bus.publish(_audio_message("audio.input.started", "eva000000001", "s1", "u1"))
        await bus.publish(_activity_message("eva000000001", "s1", "u1", False))
        await asyncio.sleep(0.03)
        assert service.status()["active_utterances"] == 1

        await bus.publish(_activity_message("eva000000001", "s1", "u1", True))
        for sequence in range(2):
            await bus.publish(
                _frame_message(
                    "eva000000001",
                    "s1",
                    "u1",
                    sequence,
                    f"frame-{sequence}",
                )
            )
        await bus.publish(_activity_message("eva000000001", "s1", "u1", False))
        await asyncio.sleep(0.01)
        await bus.publish(_activity_message("eva000000001", "s1", "u1", True))
        await asyncio.sleep(0.03)
        assert not any(message.topic == "voice.endpoint.detected" for message in observed)

        await bus.publish(
            _frame_message("eva000000001", "s1", "u1", 2, "frame-2")
        )
        await bus.publish(_activity_message("eva000000001", "s1", "u1", False))
        await bus.drain()

        completed = await _wait_topic(observed, "voice.transcription.completed")
        endpoint = await _wait_topic(observed, "voice.endpoint.detected")

        assert provider.input_closed.is_set()
        assert completed.payload["text"] == "奶龙在呢！"
        assert endpoint.payload["method"] == "vad_silence"
        assert endpoint.payload["discarded_frames"] == 0
        assert len(provider.received) == 3
        assert service.status()["vad_endpointed"] == 1
        assert service.status()["partial_endpointed"] == 0
        assert service.status()["completed"] == 1
    finally:
        await service.shutdown()
        await bus.unsubscribe_observer(observer)
        await bus.stop()


@pytest.mark.asyncio
async def test_asr_partial_endpoint_survives_vad_chatter() -> None:
    class PartialThenFinalProvider:
        def __init__(self) -> None:
            self.received: list[bytes] = []

        async def transcribe(
            self,
            pcm_chunks: AsyncIterable[bytes],
            *,
            uid: str,
            sample_rate: int = 16_000,
        ) -> AsyncIterator[SpeechRecognitionResult]:
            chunks = pcm_chunks.__aiter__()
            self.received.append(await anext(chunks))
            yield SpeechRecognitionResult("你好奶龙", False, "stream-request")
            async for chunk in chunks:
                self.received.append(chunk)
            yield SpeechRecognitionResult("你好奶龙。", True, "stream-request")

    bus = MessageBus()
    reader = FakeFrameReader()
    provider = PartialThenFinalProvider()
    service = AsrService(
        bus,
        reader,
        provider,
        chunk_frames=1,
        partial_stability_seconds=0.04,
        max_utterance_seconds=1,
    )
    observed: list[Message] = []
    packet = StreamingOpusEncoder(OpusParameters(16_000)).feed(b"\x00" * 1_920)[0]
    reader.frames["frame-0"] = packet

    await bus.start()
    observer = await bus.subscribe_observer(observed.append)
    await service.start()
    try:
        await service.arm_next_utterance("eva000000001", "s1")
        await bus.publish(_audio_message("audio.input.started", "eva000000001", "s1", "u1"))
        await bus.publish(_frame_message("eva000000001", "s1", "u1", 0, "frame-0"))
        await bus.drain()
        await _wait_topic(observed, "voice.transcription.partial")

        for _ in range(6):
            await bus.publish(_activity_message("eva000000001", "s1", "u1", False))
            await bus.drain()
            await asyncio.sleep(0.005)
            await bus.publish(_activity_message("eva000000001", "s1", "u1", True))
            await bus.drain()
            await asyncio.sleep(0.005)

        completed = await _wait_topic(observed, "voice.transcription.completed")
        endpoint = await _wait_topic(observed, "voice.endpoint.detected")

        assert completed.payload["text"] == "你好奶龙。"
        assert endpoint.payload["method"] == "partial_stability"
        assert service.status()["partial_endpointed"] == 1
        assert service.status()["vad_endpointed"] == 0
    finally:
        await service.shutdown()
        await bus.unsubscribe_observer(observer)
        await bus.stop()


@pytest.mark.asyncio
async def test_asr_max_duration_closes_stream_despite_continuous_vad_chatter() -> None:
    class FinalOnInputCloseProvider:
        async def transcribe(
            self,
            pcm_chunks: AsyncIterable[bytes],
            *,
            uid: str,
            sample_rate: int = 16_000,
        ) -> AsyncIterator[SpeechRecognitionResult]:
            async for _chunk in pcm_chunks:
                pass
            yield SpeechRecognitionResult("最多十二秒。", True, "stream-request")

    bus = MessageBus()
    reader = FakeFrameReader()
    service = AsrService(
        bus,
        reader,
        FinalOnInputCloseProvider(),
        chunk_frames=1,
        partial_stability_seconds=0.03,
        max_utterance_seconds=0.08,
    )
    observed: list[Message] = []
    packet = StreamingOpusEncoder(OpusParameters(16_000)).feed(b"\x00" * 1_920)[0]
    reader.frames["frame-0"] = packet

    await bus.start()
    observer = await bus.subscribe_observer(observed.append)
    await service.start()
    chatter_task: asyncio.Task[None] | None = None
    try:
        await service.arm_next_utterance("eva000000001", "s1")
        await bus.publish(_audio_message("audio.input.started", "eva000000001", "s1", "u1"))
        await bus.publish(_frame_message("eva000000001", "s1", "u1", 0, "frame-0"))
        await bus.drain()

        async def chatter() -> None:
            while not any(message.topic == "voice.endpoint.detected" for message in observed):
                await bus.publish(_activity_message("eva000000001", "s1", "u1", False))
                await bus.drain()
                await asyncio.sleep(0.005)
                await bus.publish(_activity_message("eva000000001", "s1", "u1", True))
                await bus.drain()
                await asyncio.sleep(0.005)

        chatter_task = asyncio.create_task(chatter())
        endpoint = await _wait_topic(observed, "voice.endpoint.detected")
        completed = await _wait_topic(observed, "voice.transcription.completed")

        assert endpoint.payload["method"] == "max_duration"
        assert completed.payload["text"] == "最多十二秒。"
        assert service.status()["max_duration_endpointed"] == 1
    finally:
        if chatter_task is not None:
            chatter_task.cancel()
            await asyncio.gather(chatter_task, return_exceptions=True)
        await service.shutdown()
        await bus.unsubscribe_observer(observer)
        await bus.stop()


@pytest.mark.asyncio
async def test_asr_close_detaches_active_utterance_before_task_cancellation() -> None:
    class BlockingProvider:
        def __init__(self) -> None:
            self.started = asyncio.Event()

        async def transcribe(
            self,
            pcm_chunks: AsyncIterable[bytes],
            *,
            uid: str,
            sample_rate: int = 16_000,
        ) -> AsyncIterator[SpeechRecognitionResult]:
            async for _chunk in pcm_chunks:
                self.started.set()
                await asyncio.Event().wait()
            if False:  # pragma: no cover - establishes the async-generator contract
                yield SpeechRecognitionResult("", True, "never")

    bus = MessageBus()
    reader = FakeFrameReader()
    provider = BlockingProvider()
    service = AsrService(bus, reader, provider, chunk_frames=1)
    packet = StreamingOpusEncoder(OpusParameters(16_000)).feed(b"\x00" * 1_920)[0]
    reader.frames["frame-0"] = packet

    await bus.start()
    await service.start()
    try:
        await service.arm_next_utterance("eva000000001", "s1")
        await bus.publish(_audio_message("audio.input.started", "eva000000001", "s1", "u1"))
        await bus.publish(_frame_message("eva000000001", "s1", "u1", 0, "frame-0"))
        await bus.drain()
        await asyncio.wait_for(provider.started.wait(), timeout=1)

        await service.close_device_input("eva000000001")

        assert service.status()["active_utterances"] == 0
        await service.arm_next_utterance("eva000000001", "s2")
        assert service.status()["armed_devices"] == 1
    finally:
        await service.shutdown()
        await bus.stop()
