from __future__ import annotations

import pytest

from otto_master.audio.opus import (
    OpusCodecError,
    OpusFrameDecoder,
    OpusParameters,
    StreamingOpusEncoder,
    encode_pcm_frame,
)


@pytest.mark.parametrize("sample_rate", [16_000, 24_000])
def test_fixed_60_ms_frame_round_trip(sample_rate: int) -> None:
    parameters = OpusParameters(sample_rate=sample_rate)
    pcm = b"\x00\x00" * parameters.samples_per_frame

    packet = encode_pcm_frame(pcm, parameters)
    decoded = OpusFrameDecoder(parameters).decode(packet)

    assert 0 < len(packet) <= 1_275
    assert len(decoded) == parameters.pcm_bytes_per_frame


def test_streaming_encoder_preserves_cross_chunk_pcm_and_pads_only_tail() -> None:
    parameters = OpusParameters(sample_rate=24_000)
    frame_bytes = parameters.pcm_bytes_per_frame
    pcm = b"\x01\x00" * (parameters.samples_per_frame * 2 + 7)
    encoder = StreamingOpusEncoder(parameters)

    packets = (
        encoder.feed(pcm[:111])
        + encoder.feed(pcm[111 : frame_bytes + 19])
        + encoder.feed(pcm[frame_bytes + 19 :])
    )
    tail = encoder.finish()

    assert len(packets) == 2
    assert len(tail) == 1
    assert encoder.frames_encoded == 3
    assert encoder.buffered_pcm_bytes == 0
    assert encoder.padding_bytes == frame_bytes - 14
    assert encoder.finish() == ()
    decoder = OpusFrameDecoder(parameters)
    assert all(len(decoder.decode(packet)) == frame_bytes for packet in packets + tail)


def test_streaming_encoder_rejects_half_sample_and_feed_after_finish() -> None:
    encoder = StreamingOpusEncoder(OpusParameters(sample_rate=16_000))
    assert encoder.feed(b"\x01") == ()

    with pytest.raises(ValueError, match="S16LE sample"):
        encoder.finish()
    with pytest.raises(RuntimeError, match="finished"):
        encoder.feed(b"\x00\x00")


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"sample_rate": 44_100}, "sample_rate"),
        ({"sample_rate": 16_000, "channels": 3}, "channels"),
        ({"sample_rate": 16_000, "frame_duration_ms": 30}, "frame_duration"),
    ],
)
def test_opus_parameters_reject_unsupported_layouts(
    kwargs: dict[str, int],
    error: str,
) -> None:
    with pytest.raises(ValueError, match=error):
        OpusParameters(**kwargs)


def test_decoder_rejects_invalid_or_wrong_duration_packet() -> None:
    parameters = OpusParameters(sample_rate=16_000)
    decoder = OpusFrameDecoder(parameters)

    with pytest.raises(ValueError, match="1..1275"):
        decoder.decode(b"")
    with pytest.raises((OpusCodecError, ValueError)):
        decoder.decode(b"not-an-opus-packet")


def test_encode_pcm_frame_requires_exactly_one_frame() -> None:
    parameters = OpusParameters(sample_rate=16_000)

    with pytest.raises(ValueError, match="exactly one"):
        encode_pcm_frame(b"\x00\x00", parameters)
