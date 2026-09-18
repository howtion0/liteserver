"""Strict Opus/PCM helpers for the device audio data plane.

The helpers deliberately operate on byte strings only.  They do not retain or
persist audio outside the lifetime of their instances and they do not depend on
NumPy or FFmpeg.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, cast

import opuslib_next  # type: ignore[import-untyped]

SUPPORTED_SAMPLE_RATES: Final = frozenset({8_000, 12_000, 16_000, 24_000, 48_000})
SUPPORTED_FRAME_DURATIONS_MS: Final = frozenset({10, 20, 40, 60})
MAX_OPUS_PACKET_BYTES: Final = 1_275
PCM_SAMPLE_BYTES: Final = 2


class OpusCodecError(RuntimeError):
    """Raised when negotiated audio or codec output violates the contract."""


@dataclass(frozen=True, slots=True)
class OpusParameters:
    """Validated mono/stereo S16LE parameters for one Opus stream."""

    sample_rate: int
    channels: int = 1
    frame_duration_ms: int = 60

    def __post_init__(self) -> None:
        if self.sample_rate not in SUPPORTED_SAMPLE_RATES:
            raise ValueError(
                "sample_rate must be one of 8000, 12000, 16000, 24000 or 48000"
            )
        if self.channels not in {1, 2}:
            raise ValueError("channels must be 1 or 2")
        if self.frame_duration_ms not in SUPPORTED_FRAME_DURATIONS_MS:
            raise ValueError("frame_duration_ms must be one of 10, 20, 40 or 60")

    @property
    def samples_per_frame(self) -> int:
        return self.sample_rate * self.frame_duration_ms // 1_000

    @property
    def pcm_bytes_per_frame(self) -> int:
        return self.samples_per_frame * self.channels * PCM_SAMPLE_BYTES


class OpusFrameDecoder:
    """Stateful decoder for one negotiated Opus utterance."""

    def __init__(self, parameters: OpusParameters) -> None:
        self.parameters = parameters
        try:
            self._decoder = opuslib_next.Decoder(
                parameters.sample_rate,
                parameters.channels,
            )
        except Exception as exc:  # pragma: no cover - platform loader details vary
            raise OpusCodecError("opus decoder initialization failed") from exc

    def decode(self, packet: bytes) -> bytes:
        """Decode exactly one negotiated-duration Opus packet to S16LE PCM."""

        if not isinstance(packet, bytes):
            raise TypeError("packet must be bytes")
        if not packet or len(packet) > MAX_OPUS_PACKET_BYTES:
            raise ValueError(f"Opus packet must contain 1..{MAX_OPUS_PACKET_BYTES} bytes")
        try:
            decoded = self._decoder.decode(
                packet,
                self.parameters.samples_per_frame,
                decode_fec=False,
            )
        except Exception as exc:
            raise OpusCodecError("opus frame decode failed") from exc
        pcm = cast(bytes, decoded)
        if not isinstance(pcm, bytes):
            raise OpusCodecError("opus decoder returned a non-bytes payload")
        if len(pcm) != self.parameters.pcm_bytes_per_frame:
            raise OpusCodecError(
                "decoded Opus duration does not match the negotiated frame duration"
            )
        return pcm


class StreamingOpusEncoder:
    """Encode arbitrarily chunked S16LE PCM into fixed-duration Opus packets."""

    def __init__(
        self,
        parameters: OpusParameters,
        *,
        application: str = "audio",
    ) -> None:
        self.parameters = parameters
        self._buffer = bytearray()
        self._finished = False
        self._frames_encoded = 0
        self._padding_bytes = 0
        try:
            self._encoder = opuslib_next.Encoder(
                parameters.sample_rate,
                parameters.channels,
                application,
            )
        except Exception as exc:  # pragma: no cover - platform loader details vary
            raise OpusCodecError("opus encoder initialization failed") from exc

    @property
    def buffered_pcm_bytes(self) -> int:
        return len(self._buffer)

    @property
    def frames_encoded(self) -> int:
        return self._frames_encoded

    @property
    def padding_bytes(self) -> int:
        return self._padding_bytes

    def feed(self, pcm: bytes) -> tuple[bytes, ...]:
        """Append one PCM chunk and return every newly completed Opus packet."""

        if self._finished:
            raise RuntimeError("cannot feed a finished Opus encoder")
        if not isinstance(pcm, bytes):
            raise TypeError("pcm must be bytes")
        if not pcm:
            return ()
        self._buffer.extend(pcm)
        frame_bytes = self.parameters.pcm_bytes_per_frame
        packets: list[bytes] = []
        while len(self._buffer) >= frame_bytes:
            frame = bytes(self._buffer[:frame_bytes])
            del self._buffer[:frame_bytes]
            packets.append(self._encode_frame(frame))
        return tuple(packets)

    def finish(self) -> tuple[bytes, ...]:
        """Pad and encode the final partial frame, if present, exactly once."""

        if self._finished:
            return ()
        self._finished = True
        if not self._buffer:
            return ()
        sample_width = self.parameters.channels * PCM_SAMPLE_BYTES
        if len(self._buffer) % sample_width:
            self._buffer.clear()
            raise ValueError("PCM stream ends inside an S16LE sample")
        frame_bytes = self.parameters.pcm_bytes_per_frame
        self._padding_bytes = frame_bytes - len(self._buffer)
        self._buffer.extend(b"\x00" * self._padding_bytes)
        frame = bytes(self._buffer)
        self._buffer.clear()
        return (self._encode_frame(frame),)

    def _encode_frame(self, pcm: bytes) -> bytes:
        if len(pcm) != self.parameters.pcm_bytes_per_frame:
            raise ValueError("PCM frame size does not match the negotiated duration")
        try:
            packet = self._encoder.encode(pcm, self.parameters.samples_per_frame)
        except Exception as exc:
            raise OpusCodecError("PCM frame encode failed") from exc
        if not isinstance(packet, bytes) or not packet:
            raise OpusCodecError("opus encoder returned an empty or invalid packet")
        if len(packet) > MAX_OPUS_PACKET_BYTES:
            raise OpusCodecError("opus encoder returned an oversized packet")
        self._frames_encoded += 1
        return packet


def encode_pcm_frame(pcm: bytes, parameters: OpusParameters) -> bytes:
    """Encode one exact PCM frame without retaining an encoder between calls."""

    encoder = StreamingOpusEncoder(parameters)
    packets = encoder.feed(pcm)
    if encoder.buffered_pcm_bytes or len(packets) != 1:
        raise ValueError("pcm must contain exactly one complete frame")
    return packets[0]
