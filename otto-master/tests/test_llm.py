from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import pytest

from otto_master.gateways.cloud import (
    ChatMessage,
    ChatStreamChunk,
    ChatToolCallDelta,
    ChatToolDefinition,
)
from otto_master.services.llm import (
    LlmProtocolError,
    LlmService,
    LlmToolCall,
    SentenceSegmenter,
)


class FakeStreamingChat:
    def __init__(self, replies: list[list[str]]) -> None:
        self.replies = replies
        self.requests: list[tuple[ChatMessage, ...]] = []

    async def stream_chat_events(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ChatToolDefinition] = (),
        tool_choice: str = "auto",
    ) -> AsyncIterator[ChatStreamChunk]:
        self.requests.append(tuple(messages))
        for chunk in self.replies.pop(0):
            yield ChatStreamChunk(content=chunk)
        yield ChatStreamChunk(finish_reason="stop")


def test_sentence_segmenter_handles_cross_chunk_punctuation_and_tail() -> None:
    segmenter = SentenceSegmenter(max_chars=20)

    output = (
        segmenter.feed("你好，今")
        + segmenter.feed("天很开心！第二句还没")
        + segmenter.feed("结束")
        + segmenter.finish()
    )

    assert output == ("你好，今天很开心！", "第二句还没结束")


def test_sentence_segmenter_bounds_unpunctuated_output_at_soft_break() -> None:
    segmenter = SentenceSegmenter(max_chars=12)

    output = segmenter.feed("这是很长的一句话，需要提前切开继续播放") + segmenter.finish()

    assert output == ("这是很长的一句话，", "需要提前切开继续播放")
    assert "".join(output) == "这是很长的一句话，需要提前切开继续播放"


@pytest.mark.asyncio
async def test_llm_service_streams_sentences_and_keeps_bounded_device_history() -> None:
    provider = FakeStreamingChat(
        [
            ["你好", "！我是 EVA。"],
            ["第二", "轮回答"],
            ["第三轮。"],
        ]
    )
    service = LlmService(provider, history_turns=1)

    first = [sentence async for sentence in service.stream_sentences("eva1", "第一问")]
    second = [sentence async for sentence in service.stream_sentences("eva1", "第二问")]
    third = [sentence async for sentence in service.stream_sentences("eva2", "独立问题")]

    assert first == ["你好！", "我是 EVA。"]
    assert second == ["第二轮回答"]
    assert third == ["第三轮。"]
    assert [(item.role, item.content) for item in service.history("eva1")] == [
        ("user", "第二问"),
        ("assistant", "第二轮回答"),
    ]
    assert [(item.role, item.content) for item in service.history("eva2")] == [
        ("user", "独立问题"),
        ("assistant", "第三轮。"),
    ]
    second_request = provider.requests[1]
    assert [(item.role, item.content) for item in second_request[1:]] == [
        ("user", "第一问"),
        ("assistant", "你好！我是 EVA。"),
        ("user", "第二问"),
    ]


@pytest.mark.asyncio
async def test_llm_service_does_not_commit_cancelled_or_failed_reply() -> None:
    class FailingProvider:
        async def stream_chat_events(
            self,
            messages: Sequence[ChatMessage],
            *,
            tools: Sequence[ChatToolDefinition] = (),
            tool_choice: str = "auto",
        ) -> AsyncIterator[ChatStreamChunk]:
            yield ChatStreamChunk(content="半句")
            raise RuntimeError("provider failed")

    service = LlmService(FailingProvider())

    with pytest.raises(RuntimeError, match="provider failed"):
        _ = [sentence async for sentence in service.stream_sentences("eva1", "问题")]

    assert service.history("eva1") == ()


@pytest.mark.asyncio
async def test_llm_service_hard_limits_prompt_and_streamed_response() -> None:
    provider = FakeStreamingChat([["甲" * 12, "乙" * 12]])
    service = LlmService(
        provider,
        sentence_max_chars=8,
        prompt_max_chars=32,
        response_max_chars=16,
        sentence_queue_size=1,
    )

    output = [sentence async for sentence in service.stream_sentences("eva1", "问" * 40)]

    assert "".join(output) == "甲" * 12 + "乙" * 4
    assert provider.requests[0][-1] == ChatMessage("user", "问" * 32)
    assert service.history("eva1") == (
        ChatMessage("user", "问" * 32),
        ChatMessage("assistant", "甲" * 12 + "乙" * 4),
    )
    assert service.status()["prompt_truncations"] == 1
    assert service.status()["response_truncations"] == 1


def _walk_tool() -> ChatToolDefinition:
    return ChatToolDefinition(
        name="self_otto_walk_forward",
        description="让机器人向前或向后行走。",
        parameters={
            "type": "object",
            "properties": {
                "steps": {"type": "integer", "minimum": 1, "maximum": 10},
                "direction": {"type": "integer", "enum": [-1, 1]},
            },
            "additionalProperties": False,
        },
    )


@pytest.mark.asyncio
async def test_llm_service_assembles_one_fragmented_tool_call_without_spoken_text() -> None:
    class ToolProvider:
        async def stream_chat_events(
            self,
            messages: Sequence[ChatMessage],
            *,
            tools: Sequence[ChatToolDefinition] = (),
            tool_choice: str = "auto",
        ) -> AsyncIterator[ChatStreamChunk]:
            assert [tool.name for tool in tools] == ["self_otto_walk_forward"]
            assert tool_choice == "auto"
            yield ChatStreamChunk(
                tool_calls=(
                    ChatToolCallDelta(
                        index=0,
                        call_id="call-walk",
                        name="self_otto_walk_forward",
                        arguments='{"steps":',
                    ),
                )
            )
            yield ChatStreamChunk(
                tool_calls=(ChatToolCallDelta(index=0, arguments='4,"direction":-1}'),)
            )
            yield ChatStreamChunk(finish_reason="tool_calls")

    service = LlmService(ToolProvider())
    output = [
        item
        async for item in service.stream_turn("eva1", "后退四步", tools=[_walk_tool()])
    ]

    assert output == [
        LlmToolCall(
            call_id="call-walk",
            name="self_otto_walk_forward",
            arguments={"steps": 4, "direction": -1},
        )
    ]
    assert service.history("eva1") == ()
    await service.record_tool_result(
        "eva1",
        "后退四步",
        output[0],
        success=True,
        result_code="completed",
    )
    assert service.history("eva1") == ()
    assert service.status()["tool_calls"] == 1
    assert service.status()["tool_calls_completed"] == 1


@pytest.mark.asyncio
async def test_tool_result_does_not_turn_the_next_action_into_plain_text_history() -> None:
    class TwoToolTurnsProvider:
        def __init__(self) -> None:
            self.requests: list[tuple[ChatMessage, ...]] = []

        async def stream_chat_events(
            self,
            messages: Sequence[ChatMessage],
            *,
            tools: Sequence[ChatToolDefinition] = (),
            tool_choice: str = "auto",
        ) -> AsyncIterator[ChatStreamChunk]:
            self.requests.append(tuple(messages))
            direction = 1 if len(self.requests) == 1 else -1
            yield ChatStreamChunk(
                tool_calls=(
                    ChatToolCallDelta(
                        0,
                        f"call-{len(self.requests)}",
                        "self_otto_walk_forward",
                        f'{{"steps":1,"direction":{direction}}}',
                    ),
                ),
                finish_reason="tool_calls",
            )

    provider = TwoToolTurnsProvider()
    service = LlmService(provider)
    first = [
        item
        async for item in service.stream_turn("eva1", "前进一步", tools=[_walk_tool()])
    ]
    assert isinstance(first[0], LlmToolCall)
    await service.record_tool_result(
        "eva1",
        "前进一步",
        first[0],
        success=True,
        result_code="completed",
    )
    second = [
        item
        async for item in service.stream_turn("eva1", "后退一步", tools=[_walk_tool()])
    ]

    assert second == [
        LlmToolCall(
            call_id="call-2",
            name="self_otto_walk_forward",
            arguments={"steps": 1, "direction": -1},
        )
    ]
    assert provider.requests[1][1:] == (ChatMessage("user", "后退一步"),)
    assert service.history("eva1") == ()


@pytest.mark.asyncio
async def test_llm_service_rejects_mixed_text_and_tool_call() -> None:
    class MixedProvider:
        async def stream_chat_events(
            self,
            messages: Sequence[ChatMessage],
            *,
            tools: Sequence[ChatToolDefinition] = (),
            tool_choice: str = "auto",
        ) -> AsyncIterator[ChatStreamChunk]:
            yield ChatStreamChunk(content="好的")
            yield ChatStreamChunk(
                tool_calls=(
                    ChatToolCallDelta(
                        index=0,
                        call_id="call-walk",
                        name="self_otto_walk_forward",
                        arguments="{}",
                    ),
                ),
                finish_reason="tool_calls",
            )

    service = LlmService(MixedProvider())
    with pytest.raises(LlmProtocolError, match="mixed_text_and_tool_call"):
        _ = [
            item
            async for item in service.stream_turn("eva1", "走", tools=[_walk_tool()])
        ]

    assert service.history("eva1") == ()


@pytest.mark.asyncio
async def test_llm_service_rejects_multiple_or_duplicate_argument_calls() -> None:
    class MultipleProvider:
        async def stream_chat_events(
            self,
            messages: Sequence[ChatMessage],
            *,
            tools: Sequence[ChatToolDefinition] = (),
            tool_choice: str = "auto",
        ) -> AsyncIterator[ChatStreamChunk]:
            yield ChatStreamChunk(
                tool_calls=(
                    ChatToolCallDelta(0, "call-1", "self_otto_walk_forward", "{}"),
                    ChatToolCallDelta(1, "call-2", "self_otto_walk_forward", "{}"),
                ),
                finish_reason="tool_calls",
            )

    service = LlmService(MultipleProvider())
    with pytest.raises(LlmProtocolError, match="multiple_tool_calls_not_allowed"):
        _ = [
            item
            async for item in service.stream_turn("eva1", "前进再后退", tools=[_walk_tool()])
        ]

    class DuplicateArgumentProvider:
        async def stream_chat_events(
            self,
            messages: Sequence[ChatMessage],
            *,
            tools: Sequence[ChatToolDefinition] = (),
            tool_choice: str = "auto",
        ) -> AsyncIterator[ChatStreamChunk]:
            yield ChatStreamChunk(
                tool_calls=(
                    ChatToolCallDelta(
                        0,
                        "call-3",
                        "self_otto_walk_forward",
                        '{"steps":2,"steps":3}',
                    ),
                ),
                finish_reason="tool_calls",
            )

    duplicate_service = LlmService(DuplicateArgumentProvider())
    with pytest.raises(LlmProtocolError, match="duplicate_tool_argument"):
        _ = [
            item
            async for item in duplicate_service.stream_turn(
                "eva1", "前进", tools=[_walk_tool()]
            )
        ]
