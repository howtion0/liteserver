"""Cloud protocol adapters for Volcengine speech and DeepSeek chat.

Provider payloads and authentication stay at this boundary.  Callers receive
only normalized text/audio values and stable errors that are safe to log.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import gzip
import json
import re
import struct
from collections.abc import AsyncIterable, AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal, cast
from uuid import uuid4

import httpx
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import InvalidStatus, WebSocketException

VOLC_ASR_RESOURCE_ID: Final = "volc.bigasr.sauc.duration"
VOLC_TTS_RESOURCE_ID: Final = "seed-tts-2.0"
VOLC_TTS_TERMINAL_CODE: Final = 20_000_000
DEFAULT_TTS_SPEAKER: Final = "zh_female_vv_uranus_bigtts"

_ASR_CLIENT_FULL_REQUEST = 0x1
_ASR_CLIENT_AUDIO_REQUEST = 0x2
_ASR_SERVER_FULL_RESPONSE = 0x9
_ASR_SERVER_ERROR_RESPONSE = 0xF
_ASR_FLAG_POSITIVE_SEQUENCE = 0x1
_ASR_FLAG_NEGATIVE_WITH_SEQUENCE = 0x3
_ASR_SERIALIZATION_NONE = 0x0
_ASR_SERIALIZATION_JSON = 0x1
_ASR_COMPRESSION_NONE = 0x0
_ASR_COMPRESSION_GZIP = 0x1

ChatRole = Literal["system", "user", "assistant"]
ChatToolChoice = Literal["auto", "none", "required"]
_CHAT_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class CloudProviderError(RuntimeError):
    """A stable provider failure that never embeds response bodies or secrets."""

    def __init__(
        self,
        provider: str,
        code: str,
        *,
        retryable: bool,
        request_id: str,
    ) -> None:
        super().__init__(f"{provider}:{code} request_id={request_id}")
        self.provider = provider
        self.code = code
        self.retryable = retryable
        self.request_id = request_id


@dataclass(frozen=True, slots=True)
class SpeechRecognitionResult:
    text: str
    is_final: bool
    request_id: str
    provider: str = "volcengine"


@dataclass(frozen=True, slots=True)
class AsrWireResponse:
    code: int
    event: int
    is_final: bool
    sequence: int | None
    payload: Mapping[str, Any] | None


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: ChatRole
    content: str

    def __post_init__(self) -> None:
        if self.role not in {"system", "user", "assistant"}:
            raise ValueError("unsupported chat role")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("chat message content must be non-empty")

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True, slots=True)
class ChatToolDefinition:
    """One bounded OpenAI-compatible function exposed to a chat provider."""

    name: str
    description: str
    parameters: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _CHAT_TOOL_NAME.fullmatch(self.name):
            raise ValueError("chat tool name is invalid")
        if (
            not isinstance(self.description, str)
            or not self.description.strip()
            or len(self.description) > 1_024
        ):
            raise ValueError("chat tool description must be between 1 and 1024 characters")
        if not isinstance(self.parameters, Mapping):
            raise TypeError("chat tool parameters must be an object")
        schema = dict(self.parameters)
        if schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
            raise ValueError("chat tool parameters must be an object JSON schema")
        object.__setattr__(self, "description", self.description.strip())
        object.__setattr__(self, "parameters", schema)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": dict(self.parameters),
            },
        }


@dataclass(frozen=True, slots=True)
class ChatToolCallDelta:
    """One indexed fragment of a streamed function call."""

    index: int
    call_id: str | None = None
    name: str | None = None
    arguments: str = ""


@dataclass(frozen=True, slots=True)
class ChatStreamChunk:
    """Provider-neutral text and tool-call fields from one SSE delta."""

    content: str | None = None
    tool_calls: tuple[ChatToolCallDelta, ...] = ()
    finish_reason: str | None = None


def _asr_header(
    message_type: int,
    flags: int,
    *,
    serialization: int,
    compression: int,
) -> bytes:
    return bytes(
        (
            0x11,
            (message_type << 4) | flags,
            (serialization << 4) | compression,
            0x00,
        )
    )


def build_asr_full_request(sequence: int, payload: Mapping[str, Any]) -> bytes:
    """Build the current Volcengine v1 full-client request packet."""

    if sequence <= 0:
        raise ValueError("ASR sequence must be positive")
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    compressed = gzip.compress(serialized)
    return b"".join(
        (
            _asr_header(
                _ASR_CLIENT_FULL_REQUEST,
                _ASR_FLAG_POSITIVE_SEQUENCE,
                serialization=_ASR_SERIALIZATION_JSON,
                compression=_ASR_COMPRESSION_GZIP,
            ),
            struct.pack(">iI", sequence, len(compressed)),
            compressed,
        )
    )


def build_asr_audio_request(sequence: int, pcm: bytes, *, is_final: bool) -> bytes:
    """Build one current Volcengine v1 audio packet."""

    if sequence <= 0:
        raise ValueError("ASR sequence must be positive")
    if not isinstance(pcm, bytes):
        raise TypeError("pcm must be bytes")
    compressed = gzip.compress(pcm)
    flags = (
        _ASR_FLAG_NEGATIVE_WITH_SEQUENCE if is_final else _ASR_FLAG_POSITIVE_SEQUENCE
    )
    wire_sequence = -sequence if is_final else sequence
    return b"".join(
        (
            _asr_header(
                _ASR_CLIENT_AUDIO_REQUEST,
                flags,
                serialization=_ASR_SERIALIZATION_NONE,
                compression=_ASR_COMPRESSION_GZIP,
            ),
            struct.pack(">iI", wire_sequence, len(compressed)),
            compressed,
        )
    )


def parse_asr_response(packet: bytes) -> AsrWireResponse:
    """Parse one current Volcengine binary response with strict bounds checks."""

    if not isinstance(packet, bytes):
        raise TypeError("ASR response must be bytes")
    if len(packet) < 4:
        raise ValueError("ASR response is shorter than its header")
    version = packet[0] >> 4
    header_words = packet[0] & 0x0F
    if version != 1 or header_words < 1:
        raise ValueError("ASR response has an unsupported header")
    header_bytes = header_words * 4
    if header_bytes > len(packet):
        raise ValueError("ASR response header exceeds packet size")
    message_type = packet[1] >> 4
    flags = packet[1] & 0x0F
    serialization = packet[2] >> 4
    compression = packet[2] & 0x0F
    offset = header_bytes
    sequence: int | None = None
    event = 0
    if flags & 0x01:
        sequence, offset = _read_i32(packet, offset, "sequence")
    is_final = bool(flags & 0x02)
    if flags & 0x04:
        event, offset = _read_i32(packet, offset, "event")

    code = 0
    if message_type == _ASR_SERVER_FULL_RESPONSE:
        payload_size, offset = _read_u32(packet, offset, "payload size")
    elif message_type == _ASR_SERVER_ERROR_RESPONSE:
        code, offset = _read_i32(packet, offset, "error code")
        payload_size, offset = _read_u32(packet, offset, "payload size")
    else:
        raise ValueError("ASR response has an unsupported message type")
    if payload_size != len(packet) - offset:
        raise ValueError("ASR response payload size does not match the packet")
    raw_payload = packet[offset:]
    if compression == _ASR_COMPRESSION_GZIP and raw_payload:
        try:
            raw_payload = gzip.decompress(raw_payload)
        except (OSError, EOFError) as exc:
            raise ValueError("ASR response gzip payload is invalid") from exc
    elif compression != _ASR_COMPRESSION_NONE:
        raise ValueError("ASR response uses unsupported compression")

    payload: Mapping[str, Any] | None = None
    if raw_payload:
        if serialization != _ASR_SERIALIZATION_JSON:
            raise ValueError("ASR response uses unsupported serialization")
        try:
            decoded = json.loads(raw_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("ASR response JSON payload is invalid") from exc
        if not isinstance(decoded, dict):
            raise ValueError("ASR response JSON payload must be an object")
        payload = cast(dict[str, Any], decoded)
    elif serialization not in {_ASR_SERIALIZATION_NONE, _ASR_SERIALIZATION_JSON}:
        raise ValueError("ASR response uses unsupported serialization")
    return AsrWireResponse(
        code=code,
        event=event,
        is_final=is_final,
        sequence=sequence,
        payload=payload,
    )


def _read_i32(packet: bytes, offset: int, field: str) -> tuple[int, int]:
    if offset + 4 > len(packet):
        raise ValueError(f"ASR response is missing {field}")
    return struct.unpack(">i", packet[offset : offset + 4])[0], offset + 4


def _read_u32(packet: bytes, offset: int, field: str) -> tuple[int, int]:
    if offset + 4 > len(packet):
        raise ValueError(f"ASR response is missing {field}")
    return struct.unpack(">I", packet[offset : offset + 4])[0], offset + 4


class VolcAsrClient:
    """Bidirectional streaming ASR using Volcengine's current binary protocol."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float,
        resource_id: str = VOLC_ASR_RESOURCE_ID,
    ) -> None:
        self.base_url = _required(base_url, "ASR base_url")
        self._api_key = _required(api_key, "ASR api_key")
        self.resource_id = _required(resource_id, "ASR resource_id")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds

    async def transcribe(
        self,
        pcm_chunks: AsyncIterable[bytes],
        *,
        uid: str,
        sample_rate: int = 16_000,
    ) -> AsyncIterator[SpeechRecognitionResult]:
        if sample_rate != 16_000:
            raise ValueError("Volc ASR MVP requires 16000 Hz PCM")
        request_id = str(uuid4())
        headers = {
            "X-Api-Key": self._api_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": request_id,
        }
        sender: asyncio.Task[None] | None = None
        latest_text = ""
        last_emitted_text = ""
        emitted_final = False
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with connect(
                    self.base_url,
                    additional_headers=headers,
                    max_size=4 * 1024 * 1024,
                    ping_interval=None,
                    close_timeout=min(self.timeout_seconds, 5.0),
                ) as websocket:
                    await websocket.send(
                        build_asr_full_request(
                            1,
                            self._request_payload(uid=uid, sample_rate=sample_rate),
                        )
                    )
                    initial = await websocket.recv()
                    self._validate_wire_response(initial, request_id)
                    sender = asyncio.create_task(
                        self._send_audio(websocket, pcm_chunks),
                        name=f"volc-asr-send-{request_id}",
                    )
                    async for raw in websocket:
                        response = self._validate_wire_response(raw, request_id)
                        text = self._extract_text(response, request_id)
                        if text:
                            latest_text = text
                        if response.is_final:
                            emitted_final = True
                            yield SpeechRecognitionResult(
                                text=latest_text,
                                is_final=True,
                                request_id=request_id,
                            )
                            break
                        if text and text != last_emitted_text:
                            last_emitted_text = text
                            yield SpeechRecognitionResult(
                                text=text,
                                is_final=False,
                                request_id=request_id,
                            )
                    if not emitted_final:
                        raise CloudProviderError(
                            "volc_asr",
                            "stream_ended_without_final",
                            retryable=True,
                            request_id=request_id,
                        )
                    if sender.done():
                        sender.result()
        except CloudProviderError:
            raise
        except TimeoutError as exc:
            raise CloudProviderError(
                "volc_asr",
                "timeout",
                retryable=True,
                request_id=request_id,
            ) from exc
        except InvalidStatus as exc:
            raise _websocket_status_error("volc_asr", request_id, exc) from exc
        except (OSError, WebSocketException) as exc:
            raise CloudProviderError(
                "volc_asr",
                "connection_failed",
                retryable=True,
                request_id=request_id,
            ) from exc
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CloudProviderError(
                "volc_asr",
                "protocol_error",
                retryable=False,
                request_id=request_id,
            ) from exc
        finally:
            if sender is not None and not sender.done():
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)

    @staticmethod
    def _request_payload(*, uid: str, sample_rate: int) -> dict[str, Any]:
        return {
            "user": {"uid": _required(uid, "ASR uid")},
            "audio": {
                "format": "pcm",
                "codec": "raw",
                "rate": sample_rate,
                "bits": 16,
                "channel": 1,
            },
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
                "enable_ddc": True,
                "show_utterances": True,
                "enable_nonstream": False,
            },
        }

    @staticmethod
    async def _send_audio(
        websocket: ClientConnection,
        chunks: AsyncIterable[bytes],
    ) -> None:
        sequence = 2
        async for pcm in chunks:
            if not isinstance(pcm, bytes):
                raise TypeError("ASR PCM chunks must be bytes")
            if not pcm:
                continue
            if len(pcm) % 2:
                raise ValueError("ASR PCM chunks must end on an S16LE sample")
            await websocket.send(build_asr_audio_request(sequence, pcm, is_final=False))
            sequence += 1
        await websocket.send(build_asr_audio_request(sequence, b"", is_final=True))

    @staticmethod
    def _validate_wire_response(raw: str | bytes, request_id: str) -> AsrWireResponse:
        if not isinstance(raw, bytes):
            raise TypeError("Volc ASR returned a non-binary frame")
        response = parse_asr_response(raw)
        if response.code != 0:
            raise CloudProviderError(
                "volc_asr",
                "provider_error",
                retryable=response.code >= 500_000,
                request_id=request_id,
            )
        payload = response.payload
        if payload is not None:
            code = payload.get("code")
            if isinstance(code, int) and code not in {0, 1000, 1013}:
                raise CloudProviderError(
                    "volc_asr",
                    "provider_error",
                    retryable=code in {429} or code >= 500_000,
                    request_id=request_id,
                )
        return response

    @staticmethod
    def _extract_text(response: AsrWireResponse, request_id: str) -> str:
        payload = response.payload
        if payload is None:
            return ""
        if payload.get("code") == 1013:
            return ""
        if "error" in payload:
            raise CloudProviderError(
                "volc_asr",
                "provider_error",
                retryable=False,
                request_id=request_id,
            )
        result = payload.get("result")
        if not isinstance(result, dict):
            return ""
        text = result.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
        utterances = result.get("utterances")
        if not isinstance(utterances, list):
            return ""
        parts = [
            item["text"].strip()
            for item in utterances
            if isinstance(item, dict)
            and item.get("definite") is True
            and isinstance(item.get("text"), str)
            and item["text"].strip()
        ]
        return "".join(parts)


class VolcTtsClient:
    """Unidirectional chunked HTTP TTS returning raw 24 kHz S16LE PCM."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float,
        resource_id: str = VOLC_TTS_RESOURCE_ID,
        speaker: str = DEFAULT_TTS_SPEAKER,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = _required(base_url, "TTS base_url")
        self._api_key = _required(api_key, "TTS api_key")
        self.resource_id = _required(resource_id, "TTS resource_id")
        self.speaker = _required(speaker, "TTS speaker")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds
        self._client = client

    async def synthesize(
        self,
        text: str,
        *,
        sample_rate: int = 24_000,
    ) -> AsyncIterator[bytes]:
        normalized = _required(text, "TTS text")
        if sample_rate != 24_000:
            raise ValueError("Volc TTS MVP requires 24000 Hz PCM")
        request_id = str(uuid4())
        client = self._client or httpx.AsyncClient(timeout=self.timeout_seconds)
        owns_client = self._client is None
        terminal = False
        try:
            async with client.stream(
                "POST",
                self.base_url,
                headers={
                    "X-Api-Key": self._api_key,
                    "X-Api-Resource-Id": self.resource_id,
                    "X-Api-Request-Id": request_id,
                    "Content-Type": "application/json",
                    "Connection": "keep-alive",
                },
                json={
                    "req_params": {
                        "text": normalized,
                        "speaker": self.speaker,
                        "audio_params": {"format": "pcm", "sample_rate": sample_rate},
                    }
                },
                timeout=self.timeout_seconds,
            ) as response:
                _check_http_status("volc_tts", request_id, response)
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    audio, line_terminal = _parse_tts_line(line, request_id)
                    if audio is not None:
                        yield audio
                    if line_terminal:
                        terminal = True
                        break
            if not terminal:
                raise CloudProviderError(
                    "volc_tts",
                    "stream_ended_without_final",
                    retryable=True,
                    request_id=request_id,
                )
        except CloudProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise CloudProviderError(
                "volc_tts",
                "timeout",
                retryable=True,
                request_id=request_id,
            ) from exc
        except httpx.HTTPError as exc:
            raise CloudProviderError(
                "volc_tts",
                "connection_failed",
                retryable=True,
                request_id=request_id,
            ) from exc
        finally:
            if owns_client:
                await client.aclose()


def _parse_tts_line(line: str, request_id: str) -> tuple[bytes | None, bool]:
    try:
        value = json.loads(line)
    except json.JSONDecodeError as exc:
        raise CloudProviderError(
            "volc_tts",
            "protocol_error",
            retryable=False,
            request_id=request_id,
        ) from exc
    if not isinstance(value, dict) or not isinstance(value.get("code"), int):
        raise CloudProviderError(
            "volc_tts",
            "protocol_error",
            retryable=False,
            request_id=request_id,
        )
    code = cast(int, value["code"])
    if code == VOLC_TTS_TERMINAL_CODE:
        return None, True
    if code != 0:
        raise CloudProviderError(
            "volc_tts",
            "provider_error",
            retryable=code == 429 or code >= 500_000,
            request_id=request_id,
        )
    data = value.get("data")
    if data is None or data == "":
        return None, False
    if not isinstance(data, str):
        raise CloudProviderError(
            "volc_tts",
            "protocol_error",
            retryable=False,
            request_id=request_id,
        )
    try:
        decoded = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CloudProviderError(
            "volc_tts",
            "protocol_error",
            retryable=False,
            request_id=request_id,
        ) from exc
    if not decoded:
        raise CloudProviderError(
            "volc_tts",
            "protocol_error",
            retryable=False,
            request_id=request_id,
        )
    return decoded, False


class DeepSeekClient:
    """OpenAI-compatible streaming chat adapter for DeepSeek."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        thinking: str | None = "disabled",
        max_tokens: int | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = _required(base_url, "LLM base_url").rstrip("/")
        self._api_key = _required(api_key, "LLM api_key")
        self.model = _required(model, "LLM model")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds
        self.thinking = thinking.strip() if isinstance(thinking, str) else None
        if max_tokens is not None and (
            isinstance(max_tokens, bool) or max_tokens < 1 or max_tokens > 8_192
        ):
            raise ValueError("LLM max_tokens must be between 1 and 8192")
        self.max_tokens = max_tokens
        self._client = client

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
    ) -> AsyncIterator[str]:
        async for chunk in self.stream_chat_events(messages):
            if chunk.content:
                yield chunk.content

    async def stream_chat_events(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ChatToolDefinition] = (),
        tool_choice: ChatToolChoice = "auto",
    ) -> AsyncIterator[ChatStreamChunk]:
        if not messages:
            raise ValueError("messages must not be empty")
        if tool_choice not in {"auto", "none", "required"}:
            raise ValueError("unsupported chat tool choice")
        if len(tools) > 32:
            raise ValueError("chat tools exceed the 32-tool limit")
        tool_names = [tool.name for tool in tools]
        if len(tool_names) != len(set(tool_names)):
            raise ValueError("chat tool names must be unique")
        request_id = str(uuid4())
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [message.to_dict() for message in messages],
            "stream": True,
        }
        if tools:
            payload["tools"] = [tool.to_dict() for tool in tools]
            payload["tool_choice"] = tool_choice
        if self.thinking:
            payload["thinking"] = {"type": self.thinking}
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        client = self._client or httpx.AsyncClient(timeout=self.timeout_seconds)
        owns_client = self._client is None
        finished = False
        try:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "X-Request-Id": request_id,
                },
                json=payload,
                timeout=self.timeout_seconds,
            ) as response:
                _check_http_status("deepseek", request_id, response)
                async for line in response.aiter_lines():
                    stripped = line.strip()
                    if not stripped or stripped.startswith(":"):
                        continue
                    if not stripped.startswith("data:"):
                        continue
                    data = stripped[5:].strip()
                    if data == "[DONE]":
                        finished = True
                        break
                    chunk = _parse_deepseek_chunk(data, request_id)
                    if chunk.tool_calls and not tools:
                        raise CloudProviderError(
                            "deepseek",
                            "unexpected_tool_call",
                            retryable=False,
                            request_id=request_id,
                        )
                    yield chunk
            if not finished:
                raise CloudProviderError(
                    "deepseek",
                    "stream_ended_without_final",
                    retryable=True,
                    request_id=request_id,
                )
        except CloudProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise CloudProviderError(
                "deepseek",
                "timeout",
                retryable=True,
                request_id=request_id,
            ) from exc
        except httpx.HTTPError as exc:
            raise CloudProviderError(
                "deepseek",
                "connection_failed",
                retryable=True,
                request_id=request_id,
            ) from exc
        finally:
            if owns_client:
                await client.aclose()


def _parse_deepseek_chunk(data: str, request_id: str) -> ChatStreamChunk:
    try:
        value = json.loads(data)
    except json.JSONDecodeError as exc:
        raise CloudProviderError(
            "deepseek",
            "protocol_error",
            retryable=False,
            request_id=request_id,
        ) from exc
    if not isinstance(value, dict):
        raise CloudProviderError(
            "deepseek",
            "protocol_error",
            retryable=False,
            request_id=request_id,
        )
    choices = value.get("choices")
    if not isinstance(choices, list):
        raise CloudProviderError(
            "deepseek",
            "protocol_error",
            retryable=False,
            request_id=request_id,
        )
    if not choices:
        return ChatStreamChunk()
    if not isinstance(choices[0], dict):
        raise CloudProviderError(
            "deepseek",
            "protocol_error",
            retryable=False,
            request_id=request_id,
        )
    finish_reason = choices[0].get("finish_reason")
    if finish_reason is not None and not isinstance(finish_reason, str):
        raise CloudProviderError(
            "deepseek",
            "protocol_error",
            retryable=False,
            request_id=request_id,
        )
    delta = choices[0].get("delta")
    if not isinstance(delta, dict):
        return ChatStreamChunk(finish_reason=finish_reason)
    content = delta.get("content")
    if content is not None and not isinstance(content, str):
        raise CloudProviderError(
            "deepseek",
            "protocol_error",
            retryable=False,
            request_id=request_id,
        )
    raw_tool_calls = delta.get("tool_calls", [])
    if raw_tool_calls is None:
        raw_tool_calls = []
    if not isinstance(raw_tool_calls, list) or len(raw_tool_calls) > 8:
        raise CloudProviderError(
            "deepseek",
            "protocol_error",
            retryable=False,
            request_id=request_id,
        )
    tool_calls: list[ChatToolCallDelta] = []
    for raw_call in raw_tool_calls:
        if not isinstance(raw_call, dict):
            raise CloudProviderError(
                "deepseek",
                "protocol_error",
                retryable=False,
                request_id=request_id,
            )
        index = raw_call.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise CloudProviderError(
                "deepseek",
                "protocol_error",
                retryable=False,
                request_id=request_id,
            )
        call_id = raw_call.get("id")
        if call_id is not None and (
            not isinstance(call_id, str) or not call_id or len(call_id) > 128
        ):
            raise CloudProviderError(
                "deepseek",
                "protocol_error",
                retryable=False,
                request_id=request_id,
            )
        call_type = raw_call.get("type")
        if call_type is not None and call_type != "function":
            raise CloudProviderError(
                "deepseek",
                "protocol_error",
                retryable=False,
                request_id=request_id,
            )
        function = raw_call.get("function", {})
        if not isinstance(function, dict):
            raise CloudProviderError(
                "deepseek",
                "protocol_error",
                retryable=False,
                request_id=request_id,
            )
        name = function.get("name")
        arguments = function.get("arguments", "")
        if name is not None and not isinstance(name, str):
            raise CloudProviderError(
                "deepseek",
                "protocol_error",
                retryable=False,
                request_id=request_id,
            )
        if not isinstance(arguments, str):
            raise CloudProviderError(
                "deepseek",
                "protocol_error",
                retryable=False,
                request_id=request_id,
            )
        tool_calls.append(
            ChatToolCallDelta(
                index=index,
                call_id=call_id,
                name=name,
                arguments=arguments,
            )
        )
    return ChatStreamChunk(
        content=content,
        tool_calls=tuple(tool_calls),
        finish_reason=finish_reason,
    )


def _check_http_status(
    provider: str,
    request_id: str,
    response: httpx.Response,
) -> None:
    status = response.status_code
    if 200 <= status < 300:
        return
    if status in {401, 403}:
        code, retryable = "authentication_failed", False
    elif status == 429:
        code, retryable = "rate_limited", True
    elif status >= 500:
        code, retryable = "unavailable", True
    else:
        code, retryable = "request_rejected", False
    raise CloudProviderError(
        provider,
        code,
        retryable=retryable,
        request_id=request_id,
    )


def _websocket_status_error(
    provider: str,
    request_id: str,
    error: InvalidStatus,
) -> CloudProviderError:
    response = cast(Any, error).response
    status = getattr(response, "status_code", None)
    if status in {401, 403}:
        return CloudProviderError(
            provider,
            "authentication_failed",
            retryable=False,
            request_id=request_id,
        )
    if status == 429:
        return CloudProviderError(
            provider,
            "rate_limited",
            retryable=True,
            request_id=request_id,
        )
    return CloudProviderError(
        provider,
        "connection_rejected",
        retryable=isinstance(status, int) and status >= 500,
        request_id=request_id,
    )


def _required(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty")
    return value.strip()
