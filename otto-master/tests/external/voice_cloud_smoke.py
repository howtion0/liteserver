"""Explicit real-cloud smoke for the test0.9 voice providers.

Run manually from the repository root.  This module is intentionally not named
``test_*.py`` so normal pytest and CI never consume paid APIs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from time import monotonic
from typing import Any

from otto_master.audio.opus import OpusParameters, StreamingOpusEncoder
from otto_master.config import load_config
from otto_master.gateways.cloud import ChatMessage, DeepSeekClient, VolcAsrClient, VolcTtsClient


async def _paced_pcm(pcm: bytes, *, chunk_ms: int) -> Any:
    bytes_per_chunk = 16_000 * 2 * chunk_ms // 1_000
    for offset in range(0, len(pcm), bytes_per_chunk):
        yield pcm[offset : offset + bytes_per_chunk]
        await asyncio.sleep(chunk_ms / 1_000)


async def run(config_path: Path, phrase: str) -> dict[str, object]:
    config = load_config(config_path)
    if not all(
        (
            config.secrets.asr_api_key,
            config.secrets.tts_api_key,
            config.secrets.llm_api_key,
            config.cloud.llm.model,
        )
    ):
        raise RuntimeError("ASR, TTS and DeepSeek local credentials are required")
    assert config.secrets.asr_api_key is not None
    assert config.secrets.tts_api_key is not None
    assert config.secrets.llm_api_key is not None
    assert config.cloud.llm.model is not None

    tts = VolcTtsClient(
        base_url=config.cloud.tts.base_url,
        api_key=config.secrets.tts_api_key,
        timeout_seconds=config.cloud.request_timeout_seconds,
        resource_id=config.cloud.tts.resource_id or "seed-tts-2.0",
        speaker=config.cloud.tts.speaker or "zh_female_vv_uranus_bigtts",
    )
    started = monotonic()
    first_audio_at: float | None = None
    pcm_parts: list[bytes] = []
    async for chunk in tts.synthesize(phrase):
        if first_audio_at is None:
            first_audio_at = monotonic()
        pcm_parts.append(chunk)
    tts_finished = monotonic()
    pcm = b"".join(pcm_parts)

    encoder = StreamingOpusEncoder(OpusParameters(24_000))
    opus_frames = list(encoder.feed(pcm))
    opus_frames.extend(encoder.finish())

    asr = VolcAsrClient(
        base_url=config.cloud.asr.base_url,
        api_key=config.secrets.asr_api_key,
        timeout_seconds=config.cloud.request_timeout_seconds,
        resource_id=config.cloud.asr.resource_id or "volc.bigasr.sauc.duration",
    )
    asr_started = monotonic()
    asr_events = [
        result
        async for result in asr.transcribe(
            _paced_pcm(pcm, chunk_ms=config.cloud.asr.chunk_ms or 180),
            uid="otto-master-cloud-smoke",
        )
    ]
    asr_finished = monotonic()
    final = next((event for event in reversed(asr_events) if event.is_final), None)
    if final is None:
        raise RuntimeError("ASR smoke ended without a final result")

    llm = DeepSeekClient(
        base_url=config.cloud.llm.base_url,
        api_key=config.secrets.llm_api_key,
        model=config.cloud.llm.model,
        timeout_seconds=config.cloud.request_timeout_seconds,
        thinking=config.cloud.llm.thinking,
        max_tokens=config.cloud.llm.max_tokens,
    )
    llm_started = monotonic()
    llm_chunks = [
        chunk
        async for chunk in llm.stream_chat(
            [
                ChatMessage(
                    "system",
                    "只用一句简短中文回答，不要使用Markdown。",
                ),
                ChatMessage("user", "请回复：语音链路正常。"),
            ]
        )
    ]
    llm_finished = monotonic()

    return {
        "tts": {
            "pcm_bytes": len(pcm),
            "chunks": len(pcm_parts),
            "first_audio_ms": (
                round((first_audio_at - started) * 1_000, 3)
                if first_audio_at is not None
                else None
            ),
            "total_ms": round((tts_finished - started) * 1_000, 3),
            "opus_frames_60ms": len(opus_frames),
            "tail_padding_bytes": encoder.padding_bytes,
        },
        "asr": {
            "partial_events": sum(not event.is_final for event in asr_events),
            "final_text": final.text,
            "request_id": final.request_id,
            "total_ms": round((asr_finished - asr_started) * 1_000, 3),
        },
        "llm": {
            "chunks": len(llm_chunks),
            "text": "".join(llm_chunks),
            "total_ms": round((llm_finished - llm_started) * 1_000, 3),
        },
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument(
        "--phrase",
        default="你好，我是伊娃，这是流式语音问答链路测试。",
    )
    args = parser.parse_args()
    result = await run(args.config, args.phrase)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
