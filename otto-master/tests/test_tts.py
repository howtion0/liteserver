from __future__ import annotations

import asyncio
import json
import struct
from collections.abc import AsyncIterator

import pytest

from otto_master.audio.opus import OpusFrameDecoder, OpusParameters, StreamingOpusEncoder
from otto_master.gateways.cloud import CloudProviderError
from otto_master.message_bus import MessageBus
from otto_master.messages import Message
from otto_master.services.tts import TtsService, apply_pcm_gain


class FakePlayback:
    def __init__(self, device_id: str, events: list[tuple[str, object]]) -> None:
        self._device_id = device_id
        self._events = events
        self._stopped = False

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def session_id(self) -> str:
        return "ws-session-1"

    async def send_sentence(self, text: str) -> None:
        self._events.append(("sentence", text))

    async def send_audio(self, packet: bytes) -> None:
        self._events.append(("audio", packet))

    async def stop(self) -> None:
        if not self._stopped:
            self._events.append(("stop", None))
            self._stopped = True


class FakeSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    async def start_tts_playback(self, device_id: str) -> FakePlayback:
        self.events.append(("start", device_id))
        return FakePlayback(device_id, self.events)


class FakeTtsProvider:
    def __init__(
        self,
        chunks: dict[str, list[bytes]],
        *,
        fail_on: str | None = None,
    ) -> None:
        self.chunks = chunks
        self.fail_on = fail_on
        self.requests: list[str] = []

    async def synthesize(
        self,
        text: str,
        *,
        sample_rate: int = 24_000,
    ) -> AsyncIterator[bytes]:
        assert sample_rate == 24_000
        self.requests.append(text)
        for chunk in self.chunks.get(text, []):
            yield chunk
        if text == self.fail_on:
            raise CloudProviderError(
                "volc_tts",
                "rate_limited",
                retryable=True,
                request_id="tts-request",
            )


async def _sentences(*values: str) -> AsyncIterator[str]:
    for value in values:
        yield value


def test_pcm_gain_scales_s16le_and_saturates_without_wrapping() -> None:
    pcm = struct.pack("<hhhhhhh", 0, 1, -1, 10_000, -10_000, 30_000, -30_000)

    amplified = apply_pcm_gain(pcm, 1.5)

    assert struct.unpack("<hhhhhhh", amplified) == (
        0,
        2,
        -2,
        15_000,
        -15_000,
        32_767,
        -32_768,
    )
    assert apply_pcm_gain(pcm, 1.0) is pcm


@pytest.mark.parametrize("gain", [0.0, 4.1, float("nan"), float("inf")])
def test_pcm_gain_rejects_invalid_gain(gain: float) -> None:
    with pytest.raises(ValueError, match="PCM gain"):
        apply_pcm_gain(b"\x00\x00", gain)


def test_pcm_gain_rejects_incomplete_sample() -> None:
    with pytest.raises(ValueError, match="complete S16LE"):
        apply_pcm_gain(b"\x00", 1.5)


@pytest.mark.asyncio
async def test_tts_streams_multiple_sentences_in_one_start_stop_and_pads_tail() -> None:
    parameters = OpusParameters(24_000)
    pcm = b"\x01\x00" * (parameters.samples_per_frame * 2 + 7)
    provider = FakeTtsProvider(
        {
            "第一句。": [pcm[:101], pcm[101:2_000]],
            "第二句。": [pcm[2_000:]],
        }
    )
    sink = FakeSink()
    bus = MessageBus()
    observed: list[Message] = []
    service = TtsService(bus, sink, provider, pace_audio=False)

    await bus.start()
    observer = await bus.subscribe_observer(observed.append)
    try:
        result = await service.play_sentences(
            "eva000000001",
            _sentences("第一句。", "第二句。"),
            correlation_id="question-1",
        )
        await bus.drain()
    finally:
        await bus.unsubscribe_observer(observer)
        await bus.stop()

    assert provider.requests == ["第一句。", "第二句。"]
    assert [kind for kind, _ in sink.events].count("start") == 1
    assert [kind for kind, _ in sink.events].count("stop") == 1
    assert [value for kind, value in sink.events if kind == "sentence"] == [
        "第一句。",
        "第二句。",
    ]
    packets = [value for kind, value in sink.events if kind == "audio"]
    assert len(packets) == 3
    decoder = OpusFrameDecoder(parameters)
    assert all(
        isinstance(packet, bytes)
        and len(decoder.decode(packet)) == parameters.pcm_bytes_per_frame
        for packet in packets
    )
    assert result.sentence_count == 2
    assert result.frame_count == 3
    assert result.padding_bytes == parameters.pcm_bytes_per_frame - 14
    topics = [message.topic for message in observed]
    assert topics.count("tts.playback.started") == 1
    assert topics.count("tts.synthesis.started") == 2
    assert topics.count("tts.synthesis.completed") == 2
    assert topics.count("tts.playback.finished") == 1
    serialized = json.dumps([message.to_dict() for message in observed])
    assert "base64" not in serialized.lower()
    assert '"data"' not in serialized.lower()
    assert '"audio"' not in serialized.lower()


@pytest.mark.asyncio
async def test_tts_provider_failure_always_sends_stop_and_publishes_stable_error() -> None:
    provider = FakeTtsProvider({"失败句。": [b"\x00\x00" * 10]}, fail_on="失败句。")
    sink = FakeSink()
    bus = MessageBus()
    observed: list[Message] = []
    service = TtsService(bus, sink, provider, pace_audio=False)

    await bus.start()
    observer = await bus.subscribe_observer(observed.append)
    try:
        with pytest.raises(CloudProviderError) as captured:
            await service.play_sentences("eva000000001", _sentences("失败句。"))
        await bus.drain()
    finally:
        await bus.unsubscribe_observer(observer)
        await bus.stop()

    assert captured.value.code == "rate_limited"
    assert [kind for kind, _ in sink.events] == ["start", "sentence", "stop"]
    failed = next(message for message in observed if message.topic == "tts.playback.failed")
    assert failed.payload["error_code"] == "rate_limited"
    assert any(message.topic == "tts.playback.finished" for message in observed)
    assert service.status()["active_playbacks"] == 0


@pytest.mark.asyncio
async def test_tts_empty_sentence_stream_does_not_open_device_playback() -> None:
    sink = FakeSink()
    bus = MessageBus()
    service = TtsService(bus, sink, FakeTtsProvider({}), pace_audio=False)

    await bus.start()
    try:
        result = await service.play_sentences("eva000000001", _sentences())
    finally:
        await bus.stop()

    assert result.sentence_count == 0
    assert sink.events == []


@pytest.mark.asyncio
async def test_tts_rejects_parallel_playback_for_same_device() -> None:
    release = asyncio.Event()

    class BlockingProvider(FakeTtsProvider):
        async def synthesize(
            self,
            text: str,
            *,
            sample_rate: int = 24_000,
        ) -> AsyncIterator[bytes]:
            await release.wait()
            yield b"\x00\x00" * 10

    sink = FakeSink()
    bus = MessageBus()
    service = TtsService(bus, sink, BlockingProvider({}), pace_audio=False)

    await bus.start()
    first = asyncio.create_task(
        service.play_sentences("eva000000001", _sentences("第一句。"))
    )
    try:
        while service.status()["active_playbacks"] == 0:
            await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="already active"):
            await service.play_sentences("eva000000001", _sentences("第二句。"))
        release.set()
        await first
    finally:
        if not first.done():
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
        await bus.stop()


@pytest.mark.asyncio
async def test_tts_sentence_producer_runs_while_pcm_provider_is_blocked() -> None:
    synthesis_started = asyncio.Event()
    release_synthesis = asyncio.Event()
    second_sentence_produced = asyncio.Event()

    class BlockingProvider(FakeTtsProvider):
        async def synthesize(
            self,
            text: str,
            *,
            sample_rate: int = 24_000,
        ) -> AsyncIterator[bytes]:
            synthesis_started.set()
            await release_synthesis.wait()
            yield b"\x00\x00" * 10

    async def streamed_sentences() -> AsyncIterator[str]:
        yield "第一句。"
        second_sentence_produced.set()
        yield "第二句。"

    sink = FakeSink()
    bus = MessageBus()
    provider = BlockingProvider({})
    service = TtsService(
        bus,
        sink,
        provider,
        pace_audio=False,
        sentence_queue_size=1,
        pcm_queue_size=1,
        opus_queue_size=1,
    )

    await bus.start()
    playback = asyncio.create_task(
        service.play_sentences("eva000000001", streamed_sentences())
    )
    try:
        await asyncio.wait_for(synthesis_started.wait(), timeout=1)
        await asyncio.wait_for(second_sentence_produced.wait(), timeout=1)
        assert not playback.done()
        release_synthesis.set()
        result = await playback
    finally:
        if not playback.done():
            playback.cancel()
            await asyncio.gather(playback, return_exceptions=True)
        await bus.stop()

    assert provider.requests == []
    assert result.sentence_count == 2
    assert result.frame_count == 1
    assert service.status()["pipeline"] == {
        "sentence_queue_size": 1,
        "pcm_queue_size": 1,
        "opus_queue_size": 1,
    }


@pytest.mark.asyncio
async def test_tts_gain_preserves_samples_split_across_provider_chunks() -> None:
    pcm = struct.pack("<hhhh", 1_000, -1_000, 30_000, -30_000)
    provider = FakeTtsProvider({"放大。": [pcm[:3], pcm[3:5], pcm[5:]]})
    sink = FakeSink()
    bus = MessageBus()
    service = TtsService(bus, sink, provider, pcm_gain=1.5, pace_audio=False)

    await bus.start()
    try:
        result = await service.play_sentences("eva000000001", _sentences("放大。"))
    finally:
        await bus.stop()

    packet = next(value for kind, value in sink.events if kind == "audio")
    expected_encoder = StreamingOpusEncoder(OpusParameters(24_000))
    expected_packets = (
        *expected_encoder.feed(apply_pcm_gain(pcm, 1.5)),
        *expected_encoder.finish(),
    )
    assert packet == expected_packets[0]
    assert result.frame_count == 1
    assert service.status()["pcm_gain"] == 1.5


@pytest.mark.asyncio
async def test_tts_rejects_provider_with_incomplete_final_sample_and_stops() -> None:
    provider = FakeTtsProvider({"坏数据。": [b"\x01"]})
    sink = FakeSink()
    bus = MessageBus()
    service = TtsService(bus, sink, provider, pcm_gain=1.5, pace_audio=False)

    await bus.start()
    try:
        with pytest.raises(ValueError, match="incomplete S16LE"):
            await service.play_sentences("eva000000001", _sentences("坏数据。"))
    finally:
        await bus.stop()

    assert [kind for kind, _ in sink.events] == ["start", "sentence", "stop"]
