from __future__ import annotations

import asyncio
import base64
import gzip
import json
import struct
from collections.abc import AsyncIterator
from typing import Any, cast

import httpx
import pytest
from websockets.asyncio.server import ServerConnection, serve

from otto_master.gateways.cloud import (
    ChatMessage,
    ChatToolCallDelta,
    ChatToolDefinition,
    CloudProviderError,
    DeepSeekClient,
    VolcAsrClient,
    VolcTtsClient,
    build_asr_audio_request,
    build_asr_full_request,
    parse_asr_response,
)


def _asr_server_packet(
    payload: dict[str, Any],
    *,
    sequence: int,
    final: bool = False,
) -> bytes:
    compressed = gzip.compress(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    )
    flags = 0x03 if final else 0x01
    return b"".join(
        (
            bytes((0x11, (0x9 << 4) | flags, 0x11, 0x00)),
            struct.pack(">iI", sequence, len(compressed)),
            compressed,
        )
    )


def test_volc_asr_wire_builders_and_parser_follow_current_sequence_protocol() -> None:
    full = build_asr_full_request(1, {"audio": {"rate": 16_000}})
    assert full[:4] == bytes((0x11, 0x11, 0x11, 0x00))
    assert struct.unpack(">i", full[4:8]) == (1,)
    full_size = struct.unpack(">I", full[8:12])[0]
    assert full_size == len(full[12:])
    assert json.loads(gzip.decompress(full[12:])) == {"audio": {"rate": 16_000}}

    audio = build_asr_audio_request(2, b"\x01\x00", is_final=False)
    final = build_asr_audio_request(3, b"", is_final=True)
    assert audio[:4] == bytes((0x11, 0x21, 0x01, 0x00))
    assert struct.unpack(">i", audio[4:8]) == (2,)
    assert final[:4] == bytes((0x11, 0x23, 0x01, 0x00))
    assert struct.unpack(">i", final[4:8]) == (-3,)

    parsed = parse_asr_response(
        _asr_server_packet(
            {"code": 1000, "result": {"text": "你好。"}},
            sequence=-4,
            final=True,
        )
    )
    assert parsed.code == 0
    assert parsed.sequence == -4
    assert parsed.is_final is True
    assert parsed.payload == {"code": 1000, "result": {"text": "你好。"}}


@pytest.mark.parametrize(
    "packet",
    [
        b"",
        b"\x11\x90\x10\x00",
        b"\x11\x90\x10\x00\x00\x00\x00\x05x",
        b"\x11\x90\x12\x00\x00\x00\x00\x00",
    ],
)
def test_volc_asr_parser_rejects_truncated_or_unsupported_frames(packet: bytes) -> None:
    with pytest.raises(ValueError):
        parse_asr_response(packet)


@pytest.mark.asyncio
async def test_volc_asr_client_streams_partial_and_final_without_exposing_key() -> None:
    received: list[bytes] = []
    observed_headers: dict[str, str] = {}

    async def handler(websocket: ServerConnection) -> None:
        observed_headers.update(
            {
                "key": websocket.request.headers["X-Api-Key"],
                "resource": websocket.request.headers["X-Api-Resource-Id"],
            }
        )
        first = await websocket.recv()
        assert isinstance(first, bytes)
        received.append(first)
        await websocket.send(_asr_server_packet({"code": 1000}, sequence=1))
        sent_partial = False
        while True:
            frame = await websocket.recv()
            assert isinstance(frame, bytes)
            received.append(frame)
            flags = frame[1] & 0x0F
            if not sent_partial and flags == 0x01:
                await websocket.send(
                    _asr_server_packet(
                        {"code": 1000, "result": {"text": "你好"}},
                        sequence=2,
                    )
                )
                sent_partial = True
            if flags == 0x03:
                await websocket.send(
                    _asr_server_packet(
                        {"code": 1000, "result": {"text": "你好。"}},
                        sequence=-4,
                        final=True,
                    )
                )
                return

    async def chunks() -> AsyncIterator[bytes]:
        yield b"\x01\x00" * 960
        await asyncio.sleep(0)
        yield b"\x02\x00" * 960

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = VolcAsrClient(
            base_url=f"ws://127.0.0.1:{port}",
            api_key="asr-test-secret",
            timeout_seconds=2,
        )
        results = [result async for result in client.transcribe(chunks(), uid="device-1")]

    assert [(result.text, result.is_final) for result in results] == [
        ("你好", False),
        ("你好。", True),
    ]
    assert observed_headers == {
        "key": "asr-test-secret",
        "resource": "volc.bigasr.sauc.duration",
    }
    assert [frame[1] & 0x0F for frame in received] == [0x01, 0x01, 0x01, 0x03]
    assert "asr-test-secret" not in repr(results)


@pytest.mark.asyncio
async def test_volc_tts_decodes_chunked_json_audio_and_requires_terminal_event() -> None:
    pcm_parts = [b"\x01\x00" * 4, b"\x02\x00" * 3]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Api-Key"] == "tts-test-secret"
        body = json.loads(request.content)
        assert body["req_params"]["audio_params"] == {
            "format": "pcm",
            "sample_rate": 24_000,
        }
        lines = [
            json.dumps({"code": 0, "data": base64.b64encode(part).decode()})
            for part in pcm_parts
        ]
        lines.append(json.dumps({"code": 20_000_000, "message": "OK"}))
        return httpx.Response(200, content=("\n".join(lines) + "\n").encode())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = VolcTtsClient(
            base_url="https://tts.invalid/api",
            api_key="tts-test-secret",
            timeout_seconds=2,
            client=http,
        )
        actual = [chunk async for chunk in client.synthesize("你好")]

    assert actual == pcm_parts


@pytest.mark.asyncio
async def test_volc_tts_maps_rate_limit_to_stable_error_without_response_body() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                429,
                text="tts-test-secret and provider internals must stay hidden",
            )
        )
    ) as http:
        client = VolcTtsClient(
            base_url="https://tts.invalid/api",
            api_key="tts-test-secret",
            timeout_seconds=2,
            client=http,
        )
        with pytest.raises(CloudProviderError) as captured:
            _ = [chunk async for chunk in client.synthesize("你好")]

    assert captured.value.code == "rate_limited"
    assert captured.value.retryable is True
    assert "tts-test-secret" not in str(captured.value)
    assert "provider internals" not in str(captured.value)


@pytest.mark.asyncio
async def test_deepseek_stream_chat_yields_ordered_content_and_disables_thinking() -> None:
    observed_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer llm-test-secret"
        observed_body.update(cast(dict[str, Any], json.loads(request.content)))
        events = [
            {"choices": [{"delta": {"content": "你"}}]},
            {"choices": [{"delta": {"content": "好！"}}]},
        ]
        content = "".join(f"data: {json.dumps(event, ensure_ascii=False)}\n\n" for event in events)
        content += "data: [DONE]\n\n"
        return httpx.Response(200, text=content)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = DeepSeekClient(
            base_url="https://api.deepseek.invalid",
            api_key="llm-test-secret",
            model="deepseek-flash",
            timeout_seconds=2,
            max_tokens=96,
            client=http,
        )
        chunks = [
            chunk
            async for chunk in client.stream_chat(
                [ChatMessage(role="user", content="打个招呼")]
            )
        ]

    assert chunks == ["你", "好！"]
    assert observed_body["model"] == "deepseek-flash"
    assert observed_body["stream"] is True
    assert observed_body["thinking"] == {"type": "disabled"}
    assert observed_body["max_tokens"] == 96
    assert observed_body["messages"] == [{"role": "user", "content": "打个招呼"}]


@pytest.mark.asyncio
async def test_deepseek_streams_tool_call_fragments_and_sends_bounded_schema() -> None:
    observed_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed_body.update(cast(dict[str, Any], json.loads(request.content)))
        events = [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "self_otto_walk_forward",
                                        "arguments": '{"steps":',
                                    },
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {"arguments": "4}"},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            },
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        content = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        return httpx.Response(200, text=content + "data: [DONE]\n\n")

    tool = ChatToolDefinition(
        name="self_otto_walk_forward",
        description="让机器人行走。",
        parameters={
            "type": "object",
            "properties": {"steps": {"type": "integer", "maximum": 10}},
            "additionalProperties": False,
        },
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = DeepSeekClient(
            base_url="https://api.deepseek.invalid",
            api_key="llm-test-secret",
            model="deepseek-flash",
            timeout_seconds=2,
            client=http,
        )
        chunks = [
            chunk
            async for chunk in client.stream_chat_events(
                [ChatMessage("user", "前进四步")],
                tools=[tool],
            )
        ]

    assert observed_body["tool_choice"] == "auto"
    assert observed_body["tools"] == [tool.to_dict()]
    assert chunks[0].tool_calls == (
        ChatToolCallDelta(
            index=0,
            call_id="call-1",
            name="self_otto_walk_forward",
            arguments='{"steps":',
        ),
    )
    assert chunks[1].tool_calls == (ChatToolCallDelta(index=0, arguments="4}"),)
    assert chunks[2].finish_reason == "tool_calls"


@pytest.mark.asyncio
async def test_deepseek_rejects_stream_without_done_event() -> None:
    response = httpx.Response(
        200,
        text='data: {"choices":[{"delta":{"content":"半句"}}]}\n\n',
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: response)
    ) as http:
        client = DeepSeekClient(
            base_url="https://api.deepseek.invalid",
            api_key="llm-test-secret",
            model="deepseek-flash",
            timeout_seconds=2,
            client=http,
        )
        with pytest.raises(CloudProviderError) as captured:
            _ = [
                chunk
                async for chunk in client.stream_chat(
                    [ChatMessage(role="user", content="继续")]
                )
            ]

    assert captured.value.code == "stream_ended_without_final"
