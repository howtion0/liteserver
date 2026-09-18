"""Fail-closed laughter gate and per-device looping voice conversations."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol
from uuid import uuid4

from ..gateways.cloud import ChatToolDefinition
from ..message_bus import MessageBus
from ..messages import JsonValue, Message, MessageKind
from .llm import LlmProtocolError, LlmSentence, LlmToolCall
from .robot_tools import RobotToolError, RobotToolResult
from .tts import TtsPlayback, TtsPlaybackResult


class ConversationState(str, Enum):
    WAITING = "waiting"
    LAUGHING = "laughing"
    LISTENING = "listening"
    RECOGNIZING = "recognizing"
    ANSWERING = "answering"
    FAILED = "failed"


class AsrGate(Protocol):
    async def arm_next_utterance(self, device_id: str, session_id: str) -> None: ...

    async def close_device_input(
        self,
        device_id: str,
        *,
        cancel_active: bool = True,
    ) -> None: ...


class SentenceSource(Protocol):
    def stream_sentences(self, device_id: str, prompt: str) -> AsyncIterable[str]: ...

    def stream_turn(
        self,
        device_id: str,
        prompt: str,
        *,
        tools: Sequence[ChatToolDefinition] = (),
    ) -> AsyncIterable[LlmSentence | LlmToolCall]: ...

    async def record_tool_result(
        self,
        device_id: str,
        prompt: str,
        tool_call: LlmToolCall,
        *,
        success: bool,
        result_code: str,
    ) -> None: ...


class SentencePlayer(Protocol):
    async def play_sentences(
        self,
        device_id: str,
        sentences: AsyncIterable[str],
        *,
        correlation_id: str | None = None,
    ) -> TtsPlaybackResult: ...

    async def cancel_device(self, device_id: str) -> bool: ...


class PlaybackSink(Protocol):
    async def start_tts_playback(self, device_id: str) -> TtsPlayback: ...

    async def send_transcription(
        self,
        device_id: str,
        session_id: str,
        text: str,
    ) -> None: ...

    async def close_audio_session(self, device_id: str, session_id: str) -> bool: ...


class RobotDispatcher(Protocol):
    async def submit_action(
        self,
        *,
        device_id: str,
        action: str,
        parameters: Mapping[str, JsonValue] | None = None,
        confirmation: bool,
        source: str = "webui",
        command_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]: ...

    async def submit_stop(
        self,
        *,
        device_id: str,
        source: str = "webui",
        command_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]: ...


class RobotToolGateway(Protocol):
    async def tools_for_device(self, device_id: str) -> tuple[ChatToolDefinition, ...]: ...

    async def execute(
        self,
        device_id: str,
        tool_call: LlmToolCall,
        *,
        correlation_id: str,
    ) -> RobotToolResult: ...


@dataclass(slots=True)
class _Conversation:
    device_id: str
    state: ConversationState = ConversationState.WAITING
    transport: str | None = None
    session_id: str | None = None
    utterance_id: str | None = None
    round_id: str | None = None
    task: asyncio.Task[None] | None = None
    idle_task: asyncio.Task[None] | None = None
    speech_detected: bool = False
    turn_count: int = 0
    exit_reason: str | None = None
    failure_code: str | None = None


@dataclass(slots=True)
class _PendingQuery:
    device_id: str
    future: asyncio.Future[Message]


class WakeGateService:
    """Require a verified laugh before each turn in a bounded chat loop."""

    def __init__(
        self,
        message_bus: MessageBus,
        asr: AsrGate,
        llm: SentenceSource,
        tts: SentencePlayer,
        dispatcher: RobotDispatcher,
        playback_sink: PlaybackSink,
        *,
        laughter_action: str = "swing",
        laughter_parameters: Mapping[str, JsonValue] | None = None,
        query_interval_seconds: float = 0.25,
        query_timeout_seconds: float = 1.5,
        laughter_timeout_seconds: float = 22.0,
        conversation_timeout_seconds: float = 15.0,
        idle_timeout_seconds: float = 8.0,
        transcription_max_chars: int = 512,
        robot_tools: RobotToolGateway | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if query_interval_seconds <= 0 or query_timeout_seconds <= 0:
            raise ValueError("query intervals must be positive")
        if laughter_timeout_seconds <= query_timeout_seconds:
            raise ValueError("laughter timeout must exceed one query timeout")
        if conversation_timeout_seconds <= 0:
            raise ValueError("conversation timeout must be positive")
        if idle_timeout_seconds <= 0:
            raise ValueError("idle timeout must be positive")
        if transcription_max_chars < 32:
            raise ValueError("transcription_max_chars must be at least 32")
        self.message_bus = message_bus
        self.asr = asr
        self.llm = llm
        self.tts = tts
        self.dispatcher = dispatcher
        self.playback_sink = playback_sink
        self.laughter_action = laughter_action
        self.laughter_parameters = dict(laughter_parameters or {"amount": 0})
        self.query_interval_seconds = query_interval_seconds
        self.query_timeout_seconds = query_timeout_seconds
        self.laughter_timeout_seconds = laughter_timeout_seconds
        self.conversation_timeout_seconds = conversation_timeout_seconds
        self.idle_timeout_seconds = idle_timeout_seconds
        self.transcription_max_chars = transcription_max_chars
        self.robot_tools = robot_tools
        self._logger = logger or logging.getLogger("otto_master.wake_gate")
        self._conversations: dict[str, _Conversation] = {}
        self._pending_queries: dict[str, _PendingQuery] = {}
        self._subscriptions: list[str] = []
        self._lock = asyncio.Lock()
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._subscriptions = [
            await self.message_bus.subscribe("audio.input.started", self._on_audio_started),
            await self.message_bus.subscribe(
                "voice.transcription.completed",
                self._on_transcription_completed,
            ),
            await self.message_bus.subscribe(
                "voice.transcription.failed",
                self._on_transcription_failed,
            ),
            await self.message_bus.subscribe(
                "voice.transcription.partial",
                self._on_transcription_partial,
            ),
            await self.message_bus.subscribe("audio.input.activity", self._on_audio_activity),
            await self.message_bus.subscribe("voice.session.closed", self._on_session_closed),
            await self.message_bus.subscribe("device.state.received", self._on_device_state),
            await self.message_bus.subscribe(
                "device.actions.catalog.received",
                self._on_device_state,
            ),
            await self.message_bus.subscribe("device.disconnected", self._on_disconnected),
        ]
        self._running = True

    async def shutdown(self) -> None:
        if not self._running and not self._subscriptions:
            return
        self._running = False
        for subscription in self._subscriptions:
            await self.message_bus.unsubscribe(subscription)
        self._subscriptions.clear()
        async with self._lock:
            tasks = tuple(
                {
                    task
                    for conversation in self._conversations.values()
                    for task in (conversation.task, conversation.idle_task)
                    if task is not None
                }
            )
            self._conversations.clear()
            pending = tuple(item.future for item in self._pending_queries.values())
            self._pending_queries.clear()
        for task in tasks:
            task.cancel()
        for future in pending:
            if not future.done():
                future.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def reset_device(self, device_id: str) -> None:
        await self.asr.close_device_input(device_id)
        async with self._lock:
            conversation = self._conversations.setdefault(
                device_id,
                _Conversation(device_id=device_id),
            )
            task = conversation.task
            idle_task = conversation.idle_task
            conversation.state = ConversationState.WAITING
            conversation.transport = None
            conversation.session_id = None
            conversation.utterance_id = None
            conversation.round_id = None
            conversation.task = None
            conversation.idle_task = None
            conversation.speech_detected = False
            conversation.turn_count = 0
            conversation.exit_reason = "reset"
            conversation.failure_code = None
        tasks = tuple(item for item in (task, idle_task) if item is not None)
        for item in tasks:
            item.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "healthy": self._running,
            "state": "running" if self._running else "stopped",
            "devices": {
                device_id: {
                    "state": conversation.state.value,
                    "transport": conversation.transport,
                    "session_id": conversation.session_id,
                    "round_id": conversation.round_id,
                    "speech_detected": conversation.speech_detected,
                    "turn_count": conversation.turn_count,
                    "exit_reason": conversation.exit_reason,
                    "failure_code": conversation.failure_code,
                }
                for device_id, conversation in self._conversations.items()
            },
        }

    async def _on_audio_started(self, message: Message) -> None:
        identity = _audio_identity(message)
        transport = message.payload.get("transport")
        if identity is None or transport not in {"mqtt", "websocket"}:
            return
        device_id, session_id, utterance_id = identity
        publish_state: ConversationState | None = None
        async with self._lock:
            conversation = self._conversations.setdefault(
                device_id,
                _Conversation(device_id=device_id),
            )
            if conversation.state is ConversationState.WAITING or (
                conversation.state is ConversationState.FAILED
                and conversation.session_id != session_id
            ):
                conversation.state = ConversationState.LAUGHING
                conversation.transport = transport
                conversation.session_id = session_id
                conversation.utterance_id = None
                conversation.round_id = str(uuid4())
                conversation.speech_detected = False
                conversation.turn_count = 0
                conversation.exit_reason = None
                conversation.failure_code = None
                conversation.task = asyncio.create_task(
                    self._run_laughter_gate(conversation, session_id),
                    name=f"otto-laughter-{device_id}",
                )
                publish_state = ConversationState.LAUGHING
            elif (
                conversation.state is ConversationState.LISTENING
                and conversation.session_id == session_id
                and conversation.transport == transport
            ):
                conversation.state = ConversationState.RECOGNIZING
                conversation.utterance_id = utterance_id
                publish_state = ConversationState.RECOGNIZING
        if publish_state is not None:
            await self._publish_state(device_id, publish_state)

    async def _on_transcription_completed(self, message: Message) -> None:
        identity = _audio_identity(message)
        text = message.payload.get("text")
        if identity is None or not isinstance(text, str):
            return
        device_id, session_id, utterance_id = identity
        prompt = text.strip()[: self.transcription_max_chars].rstrip()
        if not prompt:
            await self._restart_after_empty_transcription(
                device_id,
                session_id,
                utterance_id,
            )
            return
        idle_task: asyncio.Task[None] | None = None
        async with self._lock:
            conversation = self._conversations.get(device_id)
            if (
                conversation is None
                or conversation.state is not ConversationState.RECOGNIZING
                or conversation.session_id != session_id
                or conversation.utterance_id != utterance_id
            ):
                return
            idle_task = conversation.idle_task
            conversation.idle_task = None
            conversation.speech_detected = True
            conversation.turn_count += 1
            conversation.state = ConversationState.ANSWERING
            conversation.task = asyncio.create_task(
                self._run_answer(
                    conversation,
                    session_id,
                    prompt,
                    utterance_id,
                ),
                name=f"otto-answer-{device_id}",
            )
        if idle_task is not None:
            idle_task.cancel()
            await asyncio.gather(idle_task, return_exceptions=True)
        await self.asr.close_device_input(device_id, cancel_active=False)
        await self._publish_state(device_id, ConversationState.ANSWERING)

    async def _restart_after_empty_transcription(
        self,
        device_id: str,
        session_id: str,
        utterance_id: str,
    ) -> None:
        """Laugh once and reopen listening when ASR returns no usable text."""

        idle_task: asyncio.Task[None] | None = None
        round_id: str | None = None
        conversation: _Conversation | None = None
        async with self._lock:
            conversation = self._conversations.get(device_id)
            if (
                conversation is None
                or conversation.state is not ConversationState.RECOGNIZING
                or conversation.session_id != session_id
                or conversation.utterance_id != utterance_id
            ):
                return
            idle_task = conversation.idle_task
            conversation.idle_task = None
            conversation.state = ConversationState.LAUGHING
            conversation.utterance_id = None
            conversation.round_id = str(uuid4())
            round_id = conversation.round_id
            conversation.speech_detected = False
            conversation.task = None
        if idle_task is not None:
            idle_task.cancel()
            await asyncio.gather(idle_task, return_exceptions=True)
        await self.asr.close_device_input(device_id, cancel_active=False)
        await self._publish_state(device_id, ConversationState.LAUGHING)
        async with self._lock:
            if (
                conversation.state is not ConversationState.LAUGHING
                or conversation.session_id != session_id
                or conversation.round_id != round_id
            ):
                return
            conversation.task = asyncio.create_task(
                self._run_laughter_gate(conversation, session_id),
                name=f"otto-empty-transcription-{device_id}",
            )

    async def _on_transcription_partial(self, message: Message) -> None:
        await self._mark_speech_activity(message)

    async def _on_audio_activity(self, message: Message) -> None:
        if message.payload.get("speaking") is True:
            await self._mark_speech_activity(message)

    async def _mark_speech_activity(self, message: Message) -> None:
        identity = _audio_identity(message)
        if identity is None:
            return
        device_id, session_id, utterance_id = identity
        idle_task: asyncio.Task[None] | None = None
        async with self._lock:
            conversation = self._conversations.get(device_id)
            if (
                conversation is None
                or conversation.state
                not in {ConversationState.LISTENING, ConversationState.RECOGNIZING}
                or conversation.session_id != session_id
                or conversation.utterance_id != utterance_id
            ):
                return
            conversation.speech_detected = True
            idle_task = conversation.idle_task
            conversation.idle_task = None
        if idle_task is not None:
            idle_task.cancel()
            await asyncio.gather(idle_task, return_exceptions=True)

    async def _on_transcription_failed(self, message: Message) -> None:
        identity = _audio_identity(message)
        if identity is None:
            return
        device_id, session_id, utterance_id = identity
        code = message.payload.get("error_code")
        async with self._lock:
            conversation = self._conversations.get(device_id)
            if (
                conversation is None
                or conversation.state is not ConversationState.RECOGNIZING
                or conversation.session_id != session_id
                or conversation.utterance_id != utterance_id
            ):
                return
        await self._fail_device(
            conversation,
            code if isinstance(code, str) else "asr_failed",
        )

    async def _on_device_state(self, message: Message) -> None:
        correlation_id = message.correlation_id
        device_id = message.payload.get("device_id")
        if not isinstance(correlation_id, str) or not isinstance(device_id, str):
            return
        async with self._lock:
            pending = self._pending_queries.get(correlation_id)
            if pending is None or pending.device_id != device_id or pending.future.done():
                return
            pending.future.set_result(message)

    async def _on_disconnected(self, message: Message) -> None:
        device_id = message.payload.get("device_id")
        transport = message.payload.get("transport")
        if not isinstance(device_id, str):
            return
        async with self._lock:
            conversation = self._conversations.get(device_id)
        if conversation is not None and (
            not isinstance(transport, str) or conversation.transport == transport
        ):
            await self._fail_device(conversation, "device_disconnected")

    async def _on_session_closed(self, message: Message) -> None:
        device_id = message.payload.get("device_id")
        session_id = message.payload.get("session_id")
        reason = message.payload.get("reason")
        if not isinstance(device_id, str) or not isinstance(session_id, str):
            return
        normalized_reason = reason if isinstance(reason, str) else "voice_session_closed"
        async with self._lock:
            conversation = self._conversations.get(device_id)
            if (
                conversation is None
                or conversation.session_id != session_id
                or conversation.state in {ConversationState.WAITING, ConversationState.FAILED}
            ):
                return
        if normalized_reason in {"device_goodbye", "server_goodbye"}:
            await self._end_device(
                conversation,
                normalized_reason,
                close_session=False,
            )
            return
        await self._fail_device(conversation, "voice_session_closed")

    async def _run_laughter_gate(
        self,
        conversation: _Conversation,
        session_id: str,
        *,
        laughter_already_finished: bool = False,
    ) -> None:
        playback: TtsPlayback | None = None
        listening_opened = False
        failure_code: str | None = None
        try:
            await self.asr.close_device_input(conversation.device_id)
            if not laughter_already_finished:
                await self._submit_laughter_action(conversation)
                await self._await_laughter_finished(conversation)
            # The local OGG and streamed TTS share the firmware decoder. Enter
            # speaking only after sound.busy falls so ResetDecoder cannot erase
            # packets from the laugh that is currently playing.
            playback = await self.playback_sink.start_tts_playback(conversation.device_id)
            if playback.session_id != session_id:
                raise RuntimeError("device_session_changed")
            await self.asr.arm_next_utterance(conversation.device_id, session_id)
            async with self._lock:
                if conversation.state is not ConversationState.LAUGHING:
                    raise RuntimeError("conversation_state_changed")
                conversation.state = ConversationState.LISTENING
                conversation.task = None
                conversation.utterance_id = None
                conversation.speech_detected = False
                round_id = conversation.round_id
                if round_id is None:
                    raise RuntimeError("conversation_state_changed")
                idle_task = asyncio.create_task(
                    self._run_idle_timeout(conversation, session_id, round_id),
                    name=f"otto-idle-{conversation.device_id}",
                )
                conversation.idle_task = idle_task
            listening_opened = True
            await self._publish_state(conversation.device_id, ConversationState.LISTENING)
            await playback.stop()
            playback = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - fail closed across service boundaries
            failure_code = _stable_error_code(exc)
        finally:
            if playback is not None:
                try:
                    await playback.stop()
                except Exception as exc:  # noqa: BLE001 - normalize at service boundary
                    if failure_code is None:
                        failure_code = _stable_error_code(exc)
                    self._logger.warning(
                        "wake_gate_tts_stop_failed",
                        extra={
                            "event": "wake_gate_tts_stop_failed",
                            "device_id": conversation.device_id,
                        },
                    )
            if not listening_opened:
                await self.asr.close_device_input(conversation.device_id)
        if failure_code is not None:
            await self._fail_device(conversation, failure_code)

    async def _submit_laughter_action(self, conversation: _Conversation) -> None:
        async def submit() -> None:
            await self.dispatcher.submit_action(
                device_id=conversation.device_id,
                action=self.laughter_action,
                parameters=self.laughter_parameters,
                confirmation=True,
                source="service:wake_gate",
                correlation_id=conversation.round_id,
            )

        deadline = asyncio.get_running_loop().time() + self.query_timeout_seconds
        catalog_refreshed = False
        while True:
            try:
                await submit()
                return
            except Exception as exc:
                code = getattr(exc, "code", None)
                if code == "action_not_supported" and not catalog_refreshed:
                    catalog = await self._query_actions(conversation)
                    actions = catalog.payload.get("actions")
                    if not isinstance(actions, list) or not any(
                        isinstance(item, dict) and item.get("name") == self.laughter_action
                        for item in actions
                    ):
                        raise RuntimeError("laughter_action_not_supported") from exc
                    catalog_refreshed = True
                    continue
                if code == "device_state_unsafe":
                    if asyncio.get_running_loop().time() >= deadline:
                        raise
                    state = await self._query_state(conversation)
                    action_idle = state.payload.get("action_state") == "idle"
                    sound_idle = state.payload.get("sound_busy") is not True
                    if not action_idle or not sound_idle:
                        if asyncio.get_running_loop().time() >= deadline:
                            raise
                        await asyncio.sleep(self.query_interval_seconds)
                    continue
                if code not in {"action_not_supported", "device_state_unsafe"}:
                    raise
                if asyncio.get_running_loop().time() >= deadline:
                    raise
                # Device Manager consumes query replies on the same asynchronous
                # bus. No action is published until its bounded snapshot catches
                # up with the verified catalog and idle state.
                await asyncio.sleep(0.01)

    async def _await_laughter_finished(self, conversation: _Conversation) -> None:
        deadline = asyncio.get_running_loop().time() + self.laughter_timeout_seconds
        observed_busy = False
        while asyncio.get_running_loop().time() < deadline:
            try:
                state = await self._query_state(conversation)
            except RuntimeError as exc:
                if str(exc) != "laughter_state_query_timeout":
                    raise
                await asyncio.sleep(self.query_interval_seconds)
                continue
            busy = state.payload.get("sound_busy")
            if busy is True:
                observed_busy = True
            elif busy is False and observed_busy:
                return
            await asyncio.sleep(self.query_interval_seconds)
        code = "laughter_not_finished" if observed_busy else "laughter_not_started"
        raise RuntimeError(code)

    async def _query_state(self, conversation: _Conversation) -> Message:
        device_id = conversation.device_id
        transport = conversation.transport
        if transport not in {"mqtt", "websocket"}:
            raise RuntimeError("device_session_changed")
        query = Message.create(
            topic="device.state.query.requested",
            kind=MessageKind.COMMAND,
            source="service:wake_gate",
            target=f"device:{device_id}",
            payload={"device_id": device_id, "transport": transport},
        )
        future: asyncio.Future[Message] = asyncio.get_running_loop().create_future()
        async with self._lock:
            self._pending_queries[query.message_id] = _PendingQuery(device_id, future)
        try:
            await self.message_bus.publish(query)
            return await asyncio.wait_for(
                asyncio.shield(future),
                timeout=self.query_timeout_seconds,
            )
        except TimeoutError as exc:
            raise RuntimeError("laughter_state_query_timeout") from exc
        finally:
            async with self._lock:
                self._pending_queries.pop(query.message_id, None)

    async def _query_actions(self, conversation: _Conversation) -> Message:
        device_id = conversation.device_id
        transport = conversation.transport
        if transport not in {"mqtt", "websocket"}:
            raise RuntimeError("device_session_changed")
        query = Message.create(
            topic="device.actions.query.requested",
            kind=MessageKind.COMMAND,
            source="service:wake_gate",
            target=f"device:{device_id}",
            payload={"device_id": device_id, "transport": transport},
        )
        future: asyncio.Future[Message] = asyncio.get_running_loop().create_future()
        async with self._lock:
            self._pending_queries[query.message_id] = _PendingQuery(device_id, future)
        try:
            await self.message_bus.publish(query)
            return await asyncio.wait_for(
                asyncio.shield(future),
                timeout=self.query_timeout_seconds,
            )
        except TimeoutError as exc:
            raise RuntimeError("laughter_actions_query_timeout") from exc
        finally:
            async with self._lock:
                self._pending_queries.pop(query.message_id, None)

    async def _run_answer(
        self,
        conversation: _Conversation,
        session_id: str,
        prompt: str,
        utterance_id: str,
    ) -> None:
        completed_laugh = False
        try:
            async with asyncio.timeout(self.conversation_timeout_seconds):
                await self.playback_sink.send_transcription(
                    conversation.device_id,
                    session_id,
                    prompt,
                )
                await self.message_bus.publish(
                    Message.create(
                        topic="voice.transcription.displayed",
                        kind=MessageKind.RESULT,
                        source="service:wake_gate",
                        target=f"device:{conversation.device_id}",
                        correlation_id=utterance_id,
                        payload={
                            "device_id": conversation.device_id,
                            "session_id": session_id,
                            "utterance_id": utterance_id,
                            "text": prompt,
                        },
                    )
                )
                if self.robot_tools is None:
                    sentences = self.llm.stream_sentences(conversation.device_id, prompt)
                    result = await self.tts.play_sentences(
                        conversation.device_id,
                        sentences,
                        correlation_id=utterance_id,
                    )
                    if result.session_id != session_id:
                        raise RuntimeError("device_session_changed")
                else:
                    completed_laugh = await self._run_tool_capable_turn(
                        conversation,
                        session_id,
                        prompt,
                        utterance_id,
                    )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            await self._fail_device(conversation, "conversation_timeout")
            return
        except Exception as exc:  # noqa: BLE001 - fail closed across service boundaries
            await self._fail_device(conversation, _stable_error_code(exc))
            return

        async with self._lock:
            if conversation.state is not ConversationState.ANSWERING:
                return
            conversation.state = ConversationState.LAUGHING
            conversation.utterance_id = None
            conversation.round_id = str(uuid4())
            conversation.speech_detected = False
            conversation.task = asyncio.current_task()
        await self._publish_state(conversation.device_id, ConversationState.LAUGHING)
        await self._run_laughter_gate(
            conversation,
            session_id,
            laughter_already_finished=completed_laugh,
        )

    async def _run_tool_capable_turn(
        self,
        conversation: _Conversation,
        session_id: str,
        prompt: str,
        utterance_id: str,
    ) -> bool:
        if self.robot_tools is None:
            raise RuntimeError("robot_tools_unavailable")
        tools = await self.robot_tools.tools_for_device(conversation.device_id)
        stream = self.llm.stream_turn(
            conversation.device_id,
            prompt,
            tools=tools,
        )
        iterator = stream.__aiter__()
        try:
            first = await anext(iterator)
        except StopAsyncIteration as exc:
            raise LlmProtocolError("empty_llm_response") from exc

        if isinstance(first, LlmSentence):
            pending_tool: LlmToolCall | None = None

            async def sentences() -> AsyncIterable[str]:
                nonlocal pending_tool
                yield first.text
                async for item in iterator:
                    if isinstance(item, LlmSentence):
                        if pending_tool is not None:
                            raise LlmProtocolError("mixed_text_and_tool_call")
                        yield item.text
                        continue
                    if pending_tool is not None:
                        raise LlmProtocolError("multiple_tool_calls_not_allowed")
                    pending_tool = item

            text_playback = await self.tts.play_sentences(
                conversation.device_id,
                sentences(),
                correlation_id=utterance_id,
            )
            if text_playback.session_id != session_id:
                raise RuntimeError("device_session_changed")
            if pending_tool is None:
                return False
            return await self._execute_tool_turn(
                conversation,
                session_id,
                prompt,
                utterance_id,
                pending_tool,
            )

        extra = await anext(iterator, None)
        if extra is not None:
            raise LlmProtocolError("multiple_tool_calls_not_allowed")
        return await self._execute_tool_turn(
            conversation,
            session_id,
            prompt,
            utterance_id,
            first,
        )

    async def _execute_tool_turn(
        self,
        conversation: _Conversation,
        session_id: str,
        prompt: str,
        utterance_id: str,
        tool_call: LlmToolCall,
    ) -> bool:
        if self.robot_tools is None:
            raise RuntimeError("robot_tools_unavailable")
        await self._publish_tool_event(
            conversation,
            session_id,
            utterance_id,
            tool_call,
            topic="voice.tool.requested",
        )
        try:
            tool_result = await self.robot_tools.execute(
                conversation.device_id,
                tool_call,
                correlation_id=utterance_id,
            )
        except RobotToolError as exc:
            await self.llm.record_tool_result(
                conversation.device_id,
                prompt,
                tool_call,
                success=False,
                result_code=exc.code,
            )
            await self._publish_tool_event(
                conversation,
                session_id,
                utterance_id,
                tool_call,
                topic="voice.tool.failed",
                error_code=exc.code,
            )
            failure_playback = await self.tts.play_sentences(
                conversation.device_id,
                _single_sentence(exc.user_message),
                correlation_id=utterance_id,
            )
            if failure_playback.session_id != session_id:
                raise RuntimeError("device_session_changed")
            return False

        await self.llm.record_tool_result(
            conversation.device_id,
            prompt,
            tool_call,
            success=True,
            result_code=tool_result.status,
        )
        await self._publish_tool_event(
            conversation,
            session_id,
            utterance_id,
            tool_call,
            topic="voice.tool.completed",
            result=tool_result,
        )
        return tool_result.completed_laugh

    async def _publish_tool_event(
        self,
        conversation: _Conversation,
        session_id: str,
        utterance_id: str,
        tool_call: LlmToolCall,
        *,
        topic: str,
        result: RobotToolResult | None = None,
        error_code: str | None = None,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "device_id": conversation.device_id,
            "session_id": session_id,
            "utterance_id": utterance_id,
            "tool_call_id": tool_call.call_id,
            "tool_name": tool_call.name,
        }
        if result is not None:
            payload.update(
                {
                    "command_id": result.command_id,
                    "command_type": result.command_type,
                    "status": result.status,
                }
            )
            if result.action is not None:
                payload["action"] = result.action
        if error_code is not None:
            payload["error_code"] = error_code[:128]
        await self.message_bus.publish(
            Message.create(
                topic=topic,
                kind=MessageKind.RESULT if topic != "voice.tool.requested" else MessageKind.EVENT,
                source="service:wake_gate",
                target=f"device:{conversation.device_id}",
                correlation_id=utterance_id,
                payload=payload,
            )
        )

    async def _run_idle_timeout(
        self,
        conversation: _Conversation,
        session_id: str,
        round_id: str,
    ) -> None:
        await asyncio.sleep(self.idle_timeout_seconds)
        async with self._lock:
            if (
                conversation.session_id != session_id
                or conversation.round_id != round_id
                or conversation.state
                not in {ConversationState.LISTENING, ConversationState.RECOGNIZING}
                or conversation.speech_detected
            ):
                return
        await self._end_device(conversation, "idle_timeout", close_session=True)

    async def _end_device(
        self,
        conversation: _Conversation,
        reason: str,
        *,
        close_session: bool,
    ) -> None:
        current = asyncio.current_task()
        task: asyncio.Task[None] | None = None
        idle_task: asyncio.Task[None] | None = None
        session_id: str | None = None
        async with self._lock:
            if conversation.state is ConversationState.WAITING:
                return
            task = conversation.task
            idle_task = conversation.idle_task
            session_id = conversation.session_id
            conversation.state = ConversationState.WAITING
            conversation.transport = None
            conversation.session_id = None
            conversation.utterance_id = None
            conversation.round_id = None
            conversation.task = None
            conversation.idle_task = None
            conversation.speech_detected = False
            conversation.exit_reason = reason[:128]
            conversation.failure_code = None
        pending = tuple(
            item
            for item in (task, idle_task)
            if item is not None and item is not current
        )
        for item in pending:
            item.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await self.asr.close_device_input(conversation.device_id)
        await self.tts.cancel_device(conversation.device_id)
        if close_session and session_id is not None:
            await self.playback_sink.close_audio_session(
                conversation.device_id,
                session_id,
            )
        await self._publish_state(
            conversation.device_id,
            ConversationState.WAITING,
            reason=reason,
        )

    async def _fail_device(self, conversation: _Conversation, code: str) -> None:
        current = asyncio.current_task()
        task: asyncio.Task[None] | None = None
        idle_task: asyncio.Task[None] | None = None
        session_id: str | None = None
        async with self._lock:
            if conversation.state is ConversationState.FAILED:
                return
            task = conversation.task
            idle_task = conversation.idle_task
            session_id = conversation.session_id
            conversation.state = ConversationState.FAILED
            conversation.failure_code = code[:128]
            conversation.task = None
            conversation.idle_task = None
        pending = tuple(
            item
            for item in (task, idle_task)
            if item is not None and item is not current
        )
        for item in pending:
            item.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await self.asr.close_device_input(conversation.device_id)
        await self.tts.cancel_device(conversation.device_id)
        try:
            await self.dispatcher.submit_stop(
                device_id=conversation.device_id,
                source="service:wake_gate",
                correlation_id=conversation.round_id,
            )
        except Exception:  # noqa: BLE001 - cleanup failure is reported, not re-raised
            self._logger.warning(
                "wake_gate_safety_stop_failed",
                extra={
                    "event": "wake_gate_safety_stop_failed",
                    "device_id": conversation.device_id,
                },
            )
        if session_id is not None:
            try:
                await self.playback_sink.close_audio_session(
                    conversation.device_id,
                    session_id,
                )
            except Exception:  # noqa: BLE001 - preserve the primary failure code
                self._logger.warning(
                    "wake_gate_session_close_failed",
                    extra={
                        "event": "wake_gate_session_close_failed",
                        "device_id": conversation.device_id,
                    },
                )
        await self._publish_state(
            conversation.device_id,
            ConversationState.FAILED,
            error_code=conversation.failure_code,
        )

    async def _publish_state(
        self,
        device_id: str,
        state: ConversationState,
        *,
        error_code: str | None = None,
        reason: str | None = None,
    ) -> None:
        payload: dict[str, JsonValue] = {"device_id": device_id, "state": state.value}
        if error_code is not None:
            payload["error_code"] = error_code
        if reason is not None:
            payload["reason"] = reason[:128]
        await self.message_bus.publish(
            Message.create(
                topic="voice.session.state.changed",
                kind=MessageKind.STATE,
                source="service:wake_gate",
                target=f"device:{device_id}",
                payload=payload,
            )
        )


def _audio_identity(message: Message) -> tuple[str, str, str] | None:
    device_id = message.payload.get("device_id")
    session_id = message.payload.get("session_id")
    utterance_id = message.payload.get("utterance_id")
    if not all(isinstance(value, str) and value for value in (device_id, session_id, utterance_id)):
        return None
    return str(device_id), str(session_id), str(utterance_id)


async def _single_sentence(text: str) -> AsyncIterable[str]:
    yield text


def _stable_error_code(error: Exception) -> str:
    code = getattr(error, "code", None)
    if isinstance(code, str) and code:
        return code[:128]
    message = str(error)
    stable = {
        "device_session_changed",
        "conversation_state_changed",
        "laughter_not_started",
        "laughter_not_finished",
        "laughter_state_query_timeout",
        "laughter_actions_query_timeout",
        "laughter_action_not_supported",
        "conversation_timeout",
        "websocket_device_not_connected",
        "websocket_audio_write_failed",
        "websocket_control_write_failed",
        "mqtt_udp_session_not_connected",
        "mqtt_udp_device_address_unknown",
        "mqtt_udp_gateway_not_running",
        "mqtt_udp_audio_write_failed",
        "mqtt_udp_control_write_failed",
        "robot_tools_unavailable",
        "empty_llm_response",
        "mixed_text_and_tool_call",
        "multiple_tool_calls_not_allowed",
        "incomplete_tool_call",
        "invalid_tool_arguments",
        "tool_not_offered",
    }
    return message if message in stable else "voice_session_failed"
