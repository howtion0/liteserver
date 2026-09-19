from __future__ import annotations

from collections.abc import AsyncIterable

import pytest

from otto_master.services.narration import DeviceNarrationService
from otto_master.services.tts import TtsPlaybackResult


class FakePlayer:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def play_sentences(
        self,
        device_id: str,
        sentences: AsyncIterable[str],
        *,
        correlation_id: str | None = None,
    ) -> TtsPlaybackResult:
        values = [sentence async for sentence in sentences]
        self.calls.append(
            {
                "device_id": device_id,
                "sentences": values,
                "correlation_id": correlation_id,
            }
        )
        return TtsPlaybackResult(
            playback_id="playback-1",
            device_id=device_id,
            session_id="session-1",
            sentence_count=len(values),
            frame_count=3,
            padding_bytes=0,
        )


async def test_narration_targets_one_device_and_bounds_text() -> None:
    player = FakePlayer()
    service = DeviceNarrationService(player, max_chars=32)

    result = await service.narrate(
        "aabbccddee01",
        " 这是一段需要压缩空白并截断的知乎朗读文字。" * 3,
        correlation_id="web-request-1",
    )

    assert result["device_id"] == "aabbccddee01"
    assert result["truncated"] is True
    assert player.calls[0]["correlation_id"] == "web-request-1"
    sentences = player.calls[0]["sentences"]
    assert isinstance(sentences, list)
    assert len(sentences) == 1
    assert str(sentences[0]).endswith("…")
    assert len(str(sentences[0])) <= 33


@pytest.mark.parametrize("device_id", ["", "EVA1", "aabbccddee0g", "aabbccddee0100"])
async def test_narration_rejects_mutable_or_invalid_device_identity(device_id: str) -> None:
    service = DeviceNarrationService(FakePlayer())

    with pytest.raises(ValueError, match="device_id"):
        await service.narrate(device_id, "测试")


async def test_narration_rejects_empty_text_without_calling_tts() -> None:
    player = FakePlayer()
    service = DeviceNarrationService(player)

    with pytest.raises(ValueError, match="must not be empty"):
        await service.narrate("aabbccddee01", " \n ")

    assert player.calls == []
