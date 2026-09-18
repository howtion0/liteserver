"""Provider-neutral streaming LLM orchestration and sentence segmentation."""

from __future__ import annotations

import asyncio
import json
import math
from collections import deque
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from ..gateways.cloud import ChatMessage, ChatStreamChunk, ChatToolChoice, ChatToolDefinition
from ..messages import JsonValue

_SENTENCE_ENDINGS = frozenset("。！？!?；;\n")
_SOFT_BREAKS = frozenset("，,、：:")
_CLOSING_MARKS = frozenset("”’\"'）)]】》」』")


class StreamingChatProvider(Protocol):
    def stream_chat_events(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ChatToolDefinition] = (),
        tool_choice: ChatToolChoice = "auto",
    ) -> AsyncIterator[ChatStreamChunk]: ...


class LlmProtocolError(RuntimeError):
    """Stable failure raised when a model violates the bounded turn contract."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class LlmSentence:
    text: str


@dataclass(frozen=True, slots=True)
class LlmToolCall:
    call_id: str
    name: str
    arguments: Mapping[str, JsonValue]


@dataclass(slots=True)
class _ToolCallBuffer:
    call_id: str | None = None
    name: str = ""
    arguments: str = ""


@dataclass(frozen=True, slots=True)
class _StreamFailure:
    error: Exception


class _StreamDone:
    pass


_STREAM_DONE = _StreamDone()


class SentenceSegmenter:
    """Incrementally split streamed text without losing an unfinished tail."""

    def __init__(self, *, max_chars: int = 96) -> None:
        if max_chars < 8:
            raise ValueError("max_chars must be at least 8")
        self.max_chars = max_chars
        self._buffer = ""

    @property
    def buffered_text(self) -> str:
        return self._buffer

    def feed(self, chunk: str) -> tuple[str, ...]:
        if not isinstance(chunk, str):
            raise TypeError("LLM chunks must be strings")
        if not chunk:
            return ()
        self._buffer += chunk
        sentences: list[str] = []
        while self._buffer:
            boundary = self._hard_boundary()
            if boundary is None and len(self._buffer) > self.max_chars:
                boundary = self._length_boundary()
            if boundary is None:
                break
            sentence = self._buffer[:boundary].strip()
            self._buffer = self._buffer[boundary:]
            if sentence:
                sentences.append(sentence)
        return tuple(sentences)

    def finish(self) -> tuple[str, ...]:
        tail = self._buffer.strip()
        self._buffer = ""
        return (tail,) if tail else ()

    def _hard_boundary(self) -> int | None:
        for index, character in enumerate(self._buffer):
            if character not in _SENTENCE_ENDINGS:
                continue
            boundary = index + 1
            while boundary < len(self._buffer) and self._buffer[boundary] in _CLOSING_MARKS:
                boundary += 1
            return boundary
        return None

    def _length_boundary(self) -> int:
        window = self._buffer[: self.max_chars + 1]
        for index in range(len(window) - 1, max(self.max_chars // 2, 1) - 1, -1):
            if window[index] in _SOFT_BREAKS or window[index].isspace():
                return index + 1
        return self.max_chars


class LlmService:
    """Maintain bounded per-device history and expose streamable spoken sentences."""

    def __init__(
        self,
        provider: StreamingChatProvider,
        *,
        system_prompt: str = (
            "你是机器人 EVA 的语音助手。只用一到两句自然、简短的中文口语回答，"
            "总共不超过八十个汉字；不要使用 Markdown、表格或冗长列表。"
        ),
        history_turns: int = 4,
        sentence_max_chars: int = 48,
        prompt_max_chars: int = 512,
        response_max_chars: int = 96,
        sentence_queue_size: int = 4,
    ) -> None:
        if not system_prompt.strip():
            raise ValueError("system_prompt must be non-empty")
        if history_turns < 0:
            raise ValueError("history_turns must be non-negative")
        if prompt_max_chars < 32:
            raise ValueError("prompt_max_chars must be at least 32")
        if response_max_chars < 16:
            raise ValueError("response_max_chars must be at least 16")
        if sentence_queue_size < 1:
            raise ValueError("sentence_queue_size must be at least 1")
        self.provider = provider
        self.system_prompt = system_prompt.strip()
        self.history_turns = history_turns
        self.sentence_max_chars = sentence_max_chars
        self.prompt_max_chars = prompt_max_chars
        self.response_max_chars = response_max_chars
        self.sentence_queue_size = sentence_queue_size
        self._history: dict[str, deque[ChatMessage]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._prompt_truncations = 0
        self._response_truncations = 0
        self._tool_calls = 0
        self._tool_calls_completed = 0
        self._tool_calls_failed = 0
        self._tool_preambles_discarded = 0
        self._tool_preambles_spoken = 0

    async def stream_sentences(
        self,
        device_id: str,
        prompt: str,
    ) -> AsyncIterator[str]:
        async for item in self.stream_turn(device_id, prompt):
            if isinstance(item, LlmToolCall):
                raise LlmProtocolError("unexpected_tool_call")
            yield item.text

    async def stream_turn(
        self,
        device_id: str,
        prompt: str,
        *,
        tools: Sequence[ChatToolDefinition] = (),
    ) -> AsyncIterator[LlmSentence | LlmToolCall]:
        normalized_device = _required(device_id, "device_id")
        normalized_prompt = _required(prompt, "prompt")
        if len(normalized_prompt) > self.prompt_max_chars:
            normalized_prompt = normalized_prompt[: self.prompt_max_chars].rstrip()
            self._prompt_truncations += 1
        normalized_tools = tuple(tools)
        tool_names = [tool.name for tool in normalized_tools]
        if len(tool_names) != len(set(tool_names)):
            raise ValueError("LLM tool names must be unique")
        lock = self._locks.setdefault(normalized_device, asyncio.Lock())
        async with lock:
            history = self._history.setdefault(normalized_device, deque())
            messages = [ChatMessage("system", self.system_prompt), *history]
            messages.append(ChatMessage("user", normalized_prompt))
            queue: asyncio.Queue[
                LlmSentence | LlmToolCall | _StreamFailure | _StreamDone
            ] = asyncio.Queue(maxsize=self.sentence_queue_size)
            response_chunks: list[str] = []
            producer = asyncio.create_task(
                self._produce_turn(
                    messages,
                    normalized_tools,
                    queue,
                    response_chunks,
                ),
                name=f"otto-llm-producer-{normalized_device}",
            )
            completed = False
            try:
                while True:
                    item = await queue.get()
                    if isinstance(item, _StreamDone):
                        completed = True
                        break
                    if isinstance(item, _StreamFailure):
                        raise item.error
                    yield item
            finally:
                if not producer.done():
                    producer.cancel()
                await asyncio.gather(producer, return_exceptions=True)
            if not completed:
                return
            complete = "".join(response_chunks).strip()
            if complete:
                history.append(ChatMessage("user", normalized_prompt))
                history.append(ChatMessage("assistant", complete))
                self._trim_history(history)

    async def record_tool_result(
        self,
        device_id: str,
        prompt: str,
        tool_call: LlmToolCall,
        *,
        success: bool,
        result_code: str,
    ) -> None:
        """Record outcome metrics without polluting plain-text chat history.

        A correct OpenAI-compatible tool turn needs structured assistant
        ``tool_calls`` and ``tool`` messages.  ``ChatMessage`` deliberately
        represents spoken text only, so synthesizing a textual success marker
        would teach the next turn to narrate an action instead of calling it.
        The message bus and command repository retain the actual audit trail.
        """

        _required(device_id, "device_id")
        _required(prompt, "prompt")
        _required(tool_call.call_id, "tool_call.call_id")
        _required(tool_call.name, "tool_call.name")
        _required(result_code, "result_code")
        if success:
            self._tool_calls_completed += 1
        else:
            self._tool_calls_failed += 1

    async def _produce_turn(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ChatToolDefinition],
        queue: asyncio.Queue[LlmSentence | LlmToolCall | _StreamFailure | _StreamDone],
        response_chunks: list[str],
    ) -> None:
        segmenter = SentenceSegmenter(max_chars=self.sentence_max_chars)
        remaining = self.response_max_chars
        mode: str | None = None
        text_committed = False
        finish_reason: str | None = None
        tool_buffers: dict[int, _ToolCallBuffer] = {}
        try:
            async for chunk in self.provider.stream_chat_events(
                messages,
                tools=tools,
                tool_choice="auto",
            ):
                if not isinstance(chunk, ChatStreamChunk):
                    raise TypeError("LLM provider yielded an invalid stream chunk")
                if chunk.finish_reason is not None:
                    finish_reason = chunk.finish_reason
                if chunk.content:
                    if mode == "tool":
                        raise LlmProtocolError("mixed_text_and_tool_call")
                    mode = "text"
                    accepted = chunk.content[:remaining]
                    if accepted:
                        response_chunks.append(accepted)
                        remaining -= len(accepted)
                        for sentence in segmenter.feed(accepted):
                            text_committed = True
                            await queue.put(LlmSentence(sentence))
                    if len(accepted) != len(chunk.content) or remaining == 0:
                        self._response_truncations += 1
                        break
                if chunk.tool_calls:
                    if mode == "text":
                        discarded = "".join(response_chunks).strip()
                        response_chunks.clear()
                        segmenter.finish()
                        if text_committed:
                            self._tool_preambles_spoken += 1
                        elif discarded:
                            self._tool_preambles_discarded += 1
                    mode = "tool"
                    for delta in chunk.tool_calls:
                        buffer = tool_buffers.setdefault(delta.index, _ToolCallBuffer())
                        if len(tool_buffers) > 1:
                            raise LlmProtocolError("multiple_tool_calls_not_allowed")
                        if delta.call_id is not None:
                            if buffer.call_id is None:
                                buffer.call_id = delta.call_id
                            elif buffer.call_id != delta.call_id:
                                raise LlmProtocolError("tool_call_id_changed")
                        if delta.name:
                            if delta.name != buffer.name:
                                buffer.name += delta.name
                            if len(buffer.name) > 128:
                                raise LlmProtocolError("tool_name_too_long")
                        if delta.arguments:
                            buffer.arguments += delta.arguments
                            if len(buffer.arguments.encode("utf-8")) > 4_096:
                                raise LlmProtocolError("tool_arguments_too_large")
            if mode == "tool":
                if finish_reason != "tool_calls" or len(tool_buffers) != 1:
                    raise LlmProtocolError("incomplete_tool_call")
                buffer = next(iter(tool_buffers.values()))
                tool_call = _finalize_tool_call(buffer)
                if tool_call.name not in {tool.name for tool in tools}:
                    raise LlmProtocolError("tool_not_offered")
                self._tool_calls += 1
                await queue.put(tool_call)
            elif mode == "text":
                for sentence in segmenter.finish():
                    await queue.put(LlmSentence(sentence))
            else:
                raise LlmProtocolError("empty_llm_response")
            await queue.put(_STREAM_DONE)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - provider failures cross the queue boundary
            await queue.put(_StreamFailure(exc))

    def status(self) -> dict[str, int]:
        return {
            "prompt_max_chars": self.prompt_max_chars,
            "response_max_chars": self.response_max_chars,
            "sentence_queue_size": self.sentence_queue_size,
            "prompt_truncations": self._prompt_truncations,
            "response_truncations": self._response_truncations,
            "tool_calls": self._tool_calls,
            "tool_calls_completed": self._tool_calls_completed,
            "tool_calls_failed": self._tool_calls_failed,
            "tool_preambles_discarded": self._tool_preambles_discarded,
            "tool_preambles_spoken": self._tool_preambles_spoken,
        }

    def clear_history(self, device_id: str) -> None:
        self._history.pop(device_id, None)

    def history(self, device_id: str) -> tuple[ChatMessage, ...]:
        return tuple(self._history.get(device_id, ()))

    def _trim_history(self, history: deque[ChatMessage]) -> None:
        maximum_messages = self.history_turns * 2
        while len(history) > maximum_messages:
            history.popleft()


def _finalize_tool_call(buffer: _ToolCallBuffer) -> LlmToolCall:
    if buffer.call_id is None or not buffer.call_id or len(buffer.call_id) > 128:
        raise LlmProtocolError("invalid_tool_call_id")
    if not buffer.name or len(buffer.name) > 128:
        raise LlmProtocolError("invalid_tool_name")
    try:
        parsed = json.loads(
            buffer.arguments or "{}",
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except LlmProtocolError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise LlmProtocolError("invalid_tool_arguments") from exc
    if not isinstance(parsed, dict):
        raise LlmProtocolError("tool_arguments_must_be_object")
    nodes = [0]
    arguments = _bounded_json(parsed, depth=0, nodes=nodes)
    if not isinstance(arguments, dict):
        raise LlmProtocolError("tool_arguments_must_be_object")
    return LlmToolCall(
        call_id=buffer.call_id,
        name=buffer.name,
        arguments=arguments,
    )


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise LlmProtocolError("duplicate_tool_argument")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise LlmProtocolError("non_finite_tool_argument")


def _bounded_json(value: object, *, depth: int, nodes: list[int]) -> JsonValue:
    nodes[0] += 1
    if depth > 8 or nodes[0] > 256:
        raise LlmProtocolError("tool_arguments_too_complex")
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and len(value) > 512:
            raise LlmProtocolError("tool_argument_string_too_long")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise LlmProtocolError("non_finite_tool_argument")
        return value
    if isinstance(value, list):
        if len(value) > 64:
            raise LlmProtocolError("tool_arguments_too_complex")
        return [_bounded_json(item, depth=depth + 1, nodes=nodes) for item in value]
    if isinstance(value, dict):
        if len(value) > 32:
            raise LlmProtocolError("tool_arguments_too_complex")
        return {
            str(key): _bounded_json(item, depth=depth + 1, nodes=nodes)
            for key, item in value.items()
        }
    raise LlmProtocolError("unsupported_tool_argument")


def _required(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty")
    return value.strip()
