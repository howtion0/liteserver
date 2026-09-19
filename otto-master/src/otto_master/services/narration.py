"""Explicit device-targeted text narration through the existing TTS pipeline."""

from __future__ import annotations

from collections.abc import AsyncIterable
from typing import Any, Protocol

from .tts import TtsPlaybackResult


class SentencePlayer(Protocol):
    async def play_sentences(
        self,
        device_id: str,
        sentences: AsyncIterable[str],
        *,
        correlation_id: str | None = None,
    ) -> TtsPlaybackResult: ...


class DeviceNarrationService:
    """Narrate one bounded text on one stable device identity."""

    def __init__(self, player: SentencePlayer, *, max_chars: int = 600) -> None:
        if max_chars < 32:
            raise ValueError("narration max_chars must be at least 32")
        self.player = player
        self.max_chars = max_chars

    async def narrate(
        self,
        device_id: str,
        text: str,
        *,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_device = device_id.strip().lower()
        if not _device_id_valid(normalized_device):
            raise ValueError("device_id must be a lowercase 12-character hexadecimal ID")
        normalized_text = " ".join(text.split())
        if not normalized_text:
            raise ValueError("narration text must not be empty")
        if len(normalized_text) > self.max_chars:
            normalized_text = normalized_text[: self.max_chars].rstrip() + "…"

        async def sentence_source() -> AsyncIterable[str]:
            yield normalized_text

        result = await self.player.play_sentences(
            normalized_device,
            sentence_source(),
            correlation_id=correlation_id,
        )
        return {
            "playback_id": result.playback_id,
            "device_id": result.device_id,
            "session_id": result.session_id,
            "sentence_count": result.sentence_count,
            "frame_count": result.frame_count,
            "truncated": len(" ".join(text.split())) > self.max_chars,
        }


def _device_id_valid(value: str) -> bool:
    return len(value) == 12 and all(character in "0123456789abcdef" for character in value)
