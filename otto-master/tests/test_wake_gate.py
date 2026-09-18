from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator, Mapping
from typing import Any

import pytest

from otto_master.gateways.cloud import ChatToolDefinition
from otto_master.message_bus import MessageBus
from otto_master.messages import JsonValue, Message, MessageKind
from otto_master.services.llm import LlmSentence, LlmToolCall
from otto_master.services.robot_tools import RobotToolError, RobotToolResult
from otto_master.services.tts import TtsPlaybackResult
from otto_master.services.wake_gate import ConversationState, WakeGateService

DEVICE_ID = "aabbccddee01"
SESSION_ID = "ws-session-1"


class FakeAsrGate:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.arms: list[tuple[str, str]] = []

    async def arm_next_utterance(self, device_id: str, session_id: str) -> None:
        self.trace.append("asr:arm")
        self.arms.append((device_id, session_id))

    async def close_device_input(
        self,
        device_id: str,
        *,
        cancel_active: bool = True,
    ) -> None:
        self.trace.append(f"asr:close:{cancel_active}")


class FakeSentenceSource:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.prompts: list[str] = []

    async def stream_sentences(self, device_id: str, prompt: str) -> AsyncIterator[str]:
        self.trace.append("llm:start")
        self.prompts.append(prompt)
        yield "这是第一句。"
        yield "这是第二句。"


class FakeToolSentenceSource:
    def __init__(
        self,
        trace: list[str],
        items: list[LlmSentence | LlmToolCall],
    ) -> None:
        self.trace = trace
        self.items = items
        self.prompts: list[str] = []
        self.offered_tools: list[tuple[str, ...]] = []
        self.recorded_results: list[dict[str, Any]] = []

    async def stream_turn(
        self,
        device_id: str,
        prompt: str,
        *,
        tools: tuple[ChatToolDefinition, ...] = (),
    ) -> AsyncIterator[LlmSentence | LlmToolCall]:
        self.trace.append("llm:start")
        self.prompts.append(prompt)
        self.offered_tools.append(tuple(tool.name for tool in tools))
        for item in self.items:
            yield item

    async def record_tool_result(
        self,
        device_id: str,
        prompt: str,
        tool_call: LlmToolCall,
        *,
        success: bool,
        result_code: str,
    ) -> None:
        self.trace.append(f"llm:tool_result:{success}")
        self.recorded_results.append(
            {
                "device_id": device_id,
                "prompt": prompt,
                "tool_call": tool_call,
                "success": success,
                "result_code": result_code,
            }
        )


class FakeRobotToolGateway:
    def __init__(
        self,
        trace: list[str],
        *,
        tool_name: str,
        result: RobotToolResult | RobotToolError,
    ) -> None:
        self.trace = trace
        self.tool_name = tool_name
        self.result = result
        self.executions: list[tuple[str, LlmToolCall, str]] = []

    async def tools_for_device(
        self,
        device_id: str,
    ) -> tuple[ChatToolDefinition, ...]:
        self.trace.append("tools:list")
        return (
            ChatToolDefinition(
                name=self.tool_name,
                description="test tool",
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
        )

    async def execute(
        self,
        device_id: str,
        tool_call: LlmToolCall,
        *,
        correlation_id: str,
    ) -> RobotToolResult:
        self.trace.append("tool:execute")
        self.executions.append((device_id, tool_call, correlation_id))
        if isinstance(self.result, RobotToolError):
            raise self.result
        return self.result


class FakeSentencePlayer:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.played: list[list[str]] = []

    async def play_sentences(
        self,
        device_id: str,
        sentences: AsyncIterable[str],
        *,
        correlation_id: str | None = None,
    ) -> TtsPlaybackResult:
        self.trace.append("answer:start")
        collected = [sentence async for sentence in sentences]
        self.played.append(collected)
        self.trace.append("answer:stop")
        return TtsPlaybackResult("p1", device_id, SESSION_ID, len(collected), 2, 0)

    async def cancel_device(self, device_id: str) -> bool:
        self.trace.append("answer:cancel")
        return False


class FakePlayback:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self._stopped = False

    @property
    def device_id(self) -> str:
        return DEVICE_ID

    @property
    def session_id(self) -> str:
        return SESSION_ID

    async def send_sentence(self, text: str) -> None:
        raise AssertionError("laughter control playback must not synthesize speech")

    async def send_audio(self, packet: bytes) -> None:
        raise AssertionError("laughter control playback must not send TTS audio")

    async def stop(self) -> None:
        if not self._stopped:
            self.trace.append("laugh:tts_stop")
            self._stopped = True


class FakePlaybackSink:
    def __init__(
        self,
        trace: list[str],
        *,
        auto_listen_bus: MessageBus | None = None,
        transcription_error: str | None = None,
    ) -> None:
        self.trace = trace
        self.closed_sessions: list[tuple[str, str]] = []
        self.transcriptions: list[tuple[str, str, str]] = []
        self.auto_listen_bus = auto_listen_bus
        self.transcription_error = transcription_error

    async def start_tts_playback(self, device_id: str) -> FakePlayback:
        assert device_id == DEVICE_ID
        self.trace.append("laugh:tts_start")
        return FakePlayback(self.trace)

    async def send_transcription(
        self,
        device_id: str,
        session_id: str,
        text: str,
    ) -> None:
        self.trace.append("display:user")
        if self.transcription_error is not None:
            raise RuntimeError(self.transcription_error)
        self.transcriptions.append((device_id, session_id, text))

    async def close_audio_session(self, device_id: str, session_id: str) -> bool:
        if self.auto_listen_bus is not None:
            await self.auto_listen_bus.publish(_audio_started("automatic-post-tts"))
            await self.auto_listen_bus.drain()
        self.trace.append("session:close")
        self.closed_sessions.append((device_id, session_id))
        return True


class FakeDispatcher:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.actions: list[dict[str, Any]] = []
        self.stops = 0

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
    ) -> dict[str, Any]:
        self.trace.append("laugh:action")
        self.actions.append(
            {
                "device_id": device_id,
                "action": action,
                "parameters": dict(parameters or {}),
                "confirmation": confirmation,
                "source": source,
            }
        )
        return {"command_id": "laugh-command", "status": "requested"}

    async def submit_stop(
        self,
        *,
        device_id: str,
        source: str = "webui",
        command_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        self.stops += 1
        self.trace.append("robot:stop")
        return {"command_id": "stop-command", "status": "requested"}


class _ActionNotSupported(RuntimeError):
    code = "action_not_supported"


class _DeviceStateUnsafe(RuntimeError):
    code = "device_state_unsafe"


class CatalogRefreshingDispatcher(FakeDispatcher):
    def __init__(self, trace: list[str]) -> None:
        super().__init__(trace)
        self.catalog_ready = False
        self.attempts = 0

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
    ) -> dict[str, Any]:
        self.attempts += 1
        if not self.catalog_ready:
            raise _ActionNotSupported("action is not in the device catalog")
        return await super().submit_action(
            device_id=device_id,
            action=action,
            parameters=parameters,
            confirmation=confirmation,
            source=source,
            command_id=command_id,
            correlation_id=correlation_id,
        )


class StartupRefreshingDispatcher(CatalogRefreshingDispatcher):
    def __init__(self, trace: list[str]) -> None:
        super().__init__(trace)
        self.state_ready = False

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
    ) -> dict[str, Any]:
        if not self.catalog_ready:
            self.attempts += 1
            raise _ActionNotSupported("action is not in the device catalog")
        if not self.state_ready:
            self.attempts += 1
            raise _DeviceStateUnsafe("device state is not initialized")
        return await super().submit_action(
            device_id=device_id,
            action=action,
            parameters=parameters,
            confirmation=confirmation,
            source=source,
            command_id=command_id,
            correlation_id=correlation_id,
        )


def _audio_started(utterance_id: str) -> Message:
    return Message.create(
        topic="audio.input.started",
        kind=MessageKind.EVENT,
        source=f"device:{DEVICE_ID}:websocket",
        target="service:asr",
        payload={
            "device_id": DEVICE_ID,
            "transport": "websocket",
            "session_id": SESSION_ID,
            "utterance_id": utterance_id,
        },
    )


def _transcription(utterance_id: str, text: str) -> Message:
    return Message.create(
        topic="voice.transcription.completed",
        kind=MessageKind.RESULT,
        source="service:asr",
        target=f"device:{DEVICE_ID}",
        correlation_id=utterance_id,
        payload={
            "device_id": DEVICE_ID,
            "session_id": SESSION_ID,
            "utterance_id": utterance_id,
            "text": text,
            "is_final": True,
        },
    )


def _activity(utterance_id: str, speaking: bool = True) -> Message:
    return Message.create(
        topic="audio.input.activity",
        kind=MessageKind.EVENT,
        source=f"device:{DEVICE_ID}:websocket",
        target="service:wake_gate",
        payload={
            "device_id": DEVICE_ID,
            "transport": "websocket",
            "session_id": SESSION_ID,
            "utterance_id": utterance_id,
            "speaking": speaking,
        },
    )


def _session_closed(reason: str = "device_goodbye") -> Message:
    return Message.create(
        topic="voice.session.closed",
        kind=MessageKind.EVENT,
        source=f"device:{DEVICE_ID}:websocket",
        target=f"device:{DEVICE_ID}",
        payload={
            "device_id": DEVICE_ID,
            "transport": "websocket",
            "session_id": SESSION_ID,
            "reason": reason,
        },
    )


async def _wait_state(service: WakeGateService, expected: ConversationState) -> None:
    deadline = asyncio.get_running_loop().time() + 2
    while asyncio.get_running_loop().time() < deadline:
        device = service.status()["devices"].get(DEVICE_ID)
        if isinstance(device, dict) and device.get("state") == expected.value:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"wake gate did not reach {expected.value}: {service.status()}")


async def _wait_action_count(dispatcher: FakeDispatcher, expected: int) -> None:
    deadline = asyncio.get_running_loop().time() + 2
    while asyncio.get_running_loop().time() < deadline:
        if len(dispatcher.actions) >= expected:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(
        f"dispatcher did not receive {expected} actions: {dispatcher.actions}"
    )


async def _install_sound_responder(
    bus: MessageBus,
    trace: list[str],
    states: list[bool],
) -> str:
    index = 0

    async def respond(query: Message) -> None:
        nonlocal index
        state = states[min(index, len(states) - 1)]
        index += 1
        trace.append(f"sound:{state}")
        await bus.publish(
            Message.create(
                topic="device.state.received",
                kind=MessageKind.STATE,
                source=f"device:{DEVICE_ID}:websocket",
                target=f"device:{DEVICE_ID}",
                correlation_id=query.message_id,
                payload={
                    "device_id": DEVICE_ID,
                    "transport": "websocket",
                    "action_state": "idle",
                    "current_action": None,
                    "sound_busy": state,
                },
            )
        )

    return await bus.subscribe("device.state.query.requested", respond)


async def _install_actions_responder(
    bus: MessageBus,
    trace: list[str],
    dispatcher: CatalogRefreshingDispatcher,
) -> str:
    async def respond(query: Message) -> None:
        trace.append("actions:query")
        dispatcher.catalog_ready = True
        await bus.publish(
            Message.create(
                topic="device.actions.catalog.received",
                kind=MessageKind.EVENT,
                source=f"device:{DEVICE_ID}:websocket",
                target=f"device:{DEVICE_ID}",
                correlation_id=query.message_id,
                payload={
                    "device_id": DEVICE_ID,
                    "transport": "websocket",
                    "actions": [{"name": "swing"}],
                },
            )
        )

    return await bus.subscribe("device.actions.query.requested", respond)


@pytest.mark.asyncio
async def test_every_question_is_armed_only_after_real_laughter_busy_edge() -> None:
    trace: list[str] = []
    bus = MessageBus()
    asr = FakeAsrGate(trace)
    llm = FakeSentenceSource(trace)
    tts = FakeSentencePlayer(trace)
    dispatcher = FakeDispatcher(trace)
    playback_sink = FakePlaybackSink(trace)
    service = WakeGateService(
        bus,
        asr,
        llm,
        tts,
        dispatcher,
        playback_sink,
        query_interval_seconds=0.001,
        query_timeout_seconds=0.1,
        laughter_timeout_seconds=1,
    )

    await bus.start()
    responder = await _install_sound_responder(
        bus,
        trace,
        [False, True, True, False, True, False],
    )
    await service.start()
    try:
        await bus.publish(_audio_started("trigger-1"))
        await _wait_state(service, ConversationState.LISTENING)

        assert dispatcher.actions == [
            {
                "device_id": DEVICE_ID,
                "action": "swing",
                "parameters": {"amount": 0},
                "confirmation": True,
                "source": "service:wake_gate",
            }
        ]
        arm_index = trace.index("asr:arm")
        assert "sound:True" in trace[:arm_index]
        assert trace.index("laugh:action") < arm_index
        completed_index = max(
            index for index, event in enumerate(trace[:arm_index]) if event == "sound:False"
        )
        assert completed_index < trace.index("laugh:tts_start") < arm_index
        assert trace.index("asr:arm") < trace.index("laugh:tts_stop")

        await bus.publish(_audio_started("question-1"))
        await _wait_state(service, ConversationState.RECOGNIZING)
        await bus.publish(_transcription("question-1", "你是谁？"))
        await _wait_state(service, ConversationState.LISTENING)

        assert llm.prompts == ["你是谁？"]
        assert tts.played == [["这是第一句。", "这是第二句。"]]
        assert playback_sink.transcriptions == [(DEVICE_ID, SESSION_ID, "你是谁？")]
        assert trace.index("display:user") < trace.index("llm:start")
        assert playback_sink.closed_sessions == []
        assert len(dispatcher.actions) == 2
        status = service.status()["devices"][DEVICE_ID]
        assert status["session_id"] == SESSION_ID
        assert status["turn_count"] == 1
    finally:
        await service.shutdown()
        await bus.unsubscribe(responder)
        await bus.stop()


@pytest.mark.asyncio
async def test_llm_tool_call_executes_on_current_device_without_tts_narration() -> None:
    trace: list[str] = []
    bus = MessageBus()
    tool_call = LlmToolCall(
        call_id="call-walk-1",
        name="self_otto_walk_forward",
        arguments={"steps": 2},
    )
    llm = FakeToolSentenceSource(trace, [tool_call])
    tts = FakeSentencePlayer(trace)
    dispatcher = FakeDispatcher(trace)
    robot_tools = FakeRobotToolGateway(
        trace,
        tool_name=tool_call.name,
        result=RobotToolResult(
            tool_name=tool_call.name,
            command_id="walk-command",
            command_type="action",
            action="walk",
            status="completed",
        ),
    )
    service = WakeGateService(
        bus,
        FakeAsrGate(trace),
        llm,
        tts,
        dispatcher,
        FakePlaybackSink(trace),
        query_interval_seconds=0.001,
        query_timeout_seconds=0.1,
        laughter_timeout_seconds=1,
        robot_tools=robot_tools,
    )
    tool_events: list[Message] = []

    async def capture_tool_event(message: Message) -> None:
        tool_events.append(message)

    await bus.start()
    sound_responder = await _install_sound_responder(
        bus,
        trace,
        [True, False, True, False],
    )
    requested = await bus.subscribe("voice.tool.requested", capture_tool_event)
    completed = await bus.subscribe("voice.tool.completed", capture_tool_event)
    await service.start()
    try:
        await bus.publish(_audio_started("trigger-1"))
        await _wait_state(service, ConversationState.LISTENING)
        await bus.publish(_audio_started("question-1"))
        await _wait_state(service, ConversationState.RECOGNIZING)
        await bus.publish(_transcription("question-1", "向前走两步"))
        await _wait_state(service, ConversationState.LISTENING)
        await bus.drain()

        assert llm.offered_tools == [("self_otto_walk_forward",)]
        assert robot_tools.executions == [(DEVICE_ID, tool_call, "question-1")]
        assert llm.recorded_results == [
            {
                "device_id": DEVICE_ID,
                "prompt": "向前走两步",
                "tool_call": tool_call,
                "success": True,
                "result_code": "completed",
            }
        ]
        assert tts.played == []
        assert len(dispatcher.actions) == 2
        assert [event.topic for event in tool_events] == [
            "voice.tool.requested",
            "voice.tool.completed",
        ]
        assert tool_events[-1].payload["command_id"] == "walk-command"
        assert trace.index("tool:execute") < trace.index("llm:tool_result:True")
    finally:
        await service.shutdown()
        await bus.unsubscribe(sound_responder)
        await bus.unsubscribe(requested)
        await bus.unsubscribe(completed)
        await bus.stop()


@pytest.mark.asyncio
async def test_llm_text_response_keeps_streaming_tts_and_does_not_execute_tool() -> None:
    trace: list[str] = []
    bus = MessageBus()
    llm = FakeToolSentenceSource(
        trace,
        [LlmSentence("奶龙在呢！"), LlmSentence("今天也要开心呀。")],
    )
    tts = FakeSentencePlayer(trace)
    dispatcher = FakeDispatcher(trace)
    robot_tools = FakeRobotToolGateway(
        trace,
        tool_name="self_otto_laugh",
        result=RobotToolResult(
            tool_name="self_otto_laugh",
            command_id="unused",
            command_type="action",
            action="laugh",
            status="completed",
        ),
    )
    service = WakeGateService(
        bus,
        FakeAsrGate(trace),
        llm,
        tts,
        dispatcher,
        FakePlaybackSink(trace),
        query_interval_seconds=0.001,
        query_timeout_seconds=0.1,
        laughter_timeout_seconds=1,
        robot_tools=robot_tools,
    )

    await bus.start()
    responder = await _install_sound_responder(
        bus,
        trace,
        [True, False, True, False],
    )
    await service.start()
    try:
        await bus.publish(_audio_started("trigger-1"))
        await _wait_state(service, ConversationState.LISTENING)
        await bus.publish(_audio_started("question-1"))
        await _wait_state(service, ConversationState.RECOGNIZING)
        await bus.publish(_transcription("question-1", "你是谁？"))
        await _wait_state(service, ConversationState.LISTENING)

        assert tts.played == [["奶龙在呢！", "今天也要开心呀。"]]
        assert robot_tools.executions == []
        assert llm.recorded_results == []
    finally:
        await service.shutdown()
        await bus.unsubscribe(responder)
        await bus.stop()


@pytest.mark.asyncio
async def test_failed_llm_tool_is_recorded_and_spoken_as_failure_only() -> None:
    trace: list[str] = []
    bus = MessageBus()
    tool_call = LlmToolCall("call-jump-1", "self_otto_jump", {"steps": 1})
    llm = FakeToolSentenceSource(trace, [tool_call])
    tts = FakeSentencePlayer(trace)
    dispatcher = FakeDispatcher(trace)
    robot_tools = FakeRobotToolGateway(
        trace,
        tool_name=tool_call.name,
        result=RobotToolError("device_state_unsafe"),
    )
    service = WakeGateService(
        bus,
        FakeAsrGate(trace),
        llm,
        tts,
        dispatcher,
        FakePlaybackSink(trace),
        query_interval_seconds=0.001,
        query_timeout_seconds=0.1,
        laughter_timeout_seconds=1,
        robot_tools=robot_tools,
    )
    failures: list[Message] = []

    async def capture_failure(message: Message) -> None:
        failures.append(message)

    await bus.start()
    responder = await _install_sound_responder(
        bus,
        trace,
        [True, False, True, False],
    )
    failure_subscription = await bus.subscribe("voice.tool.failed", capture_failure)
    await service.start()
    try:
        await bus.publish(_audio_started("trigger-1"))
        await _wait_state(service, ConversationState.LISTENING)
        await bus.publish(_audio_started("question-1"))
        await _wait_state(service, ConversationState.RECOGNIZING)
        await bus.publish(_transcription("question-1", "跳一下"))
        await _wait_state(service, ConversationState.LISTENING)
        await bus.drain()

        assert llm.recorded_results[0]["success"] is False
        assert llm.recorded_results[0]["result_code"] == "device_state_unsafe"
        assert tts.played == [["这个动作没执行成功，请再说一次吧。"]]
        assert len(failures) == 1
        assert failures[0].payload["error_code"] == "device_state_unsafe"
    finally:
        await service.shutdown()
        await bus.unsubscribe(responder)
        await bus.unsubscribe(failure_subscription)
        await bus.stop()


@pytest.mark.asyncio
async def test_explicit_laugh_tool_is_reused_as_next_turn_gate_without_double_laugh() -> None:
    trace: list[str] = []
    bus = MessageBus()
    tool_call = LlmToolCall("call-laugh-1", "self_otto_laugh", {})
    llm = FakeToolSentenceSource(trace, [tool_call])
    tts = FakeSentencePlayer(trace)
    dispatcher = FakeDispatcher(trace)
    robot_tools = FakeRobotToolGateway(
        trace,
        tool_name=tool_call.name,
        result=RobotToolResult(
            tool_name=tool_call.name,
            command_id="laugh-tool-command",
            command_type="action",
            action="laugh",
            status="completed",
        ),
    )
    asr = FakeAsrGate(trace)
    service = WakeGateService(
        bus,
        asr,
        llm,
        tts,
        dispatcher,
        FakePlaybackSink(trace),
        query_interval_seconds=0.001,
        query_timeout_seconds=0.1,
        laughter_timeout_seconds=1,
        robot_tools=robot_tools,
    )

    await bus.start()
    responder = await _install_sound_responder(bus, trace, [True, False])
    await service.start()
    try:
        await bus.publish(_audio_started("trigger-1"))
        await _wait_state(service, ConversationState.LISTENING)
        await bus.publish(_audio_started("question-1"))
        await _wait_state(service, ConversationState.RECOGNIZING)
        await bus.publish(_transcription("question-1", "大笑一下"))
        await _wait_state(service, ConversationState.LISTENING)

        assert len(robot_tools.executions) == 1
        assert len(dispatcher.actions) == 1
        assert tts.played == []
        assert asr.arms == [(DEVICE_ID, SESSION_ID), (DEVICE_ID, SESSION_ID)]
        assert trace.count("laugh:action") == 1
    finally:
        await service.shutdown()
        await bus.unsubscribe(responder)
        await bus.stop()


@pytest.mark.asyncio
async def test_laughter_refreshes_an_initially_missing_action_catalog() -> None:
    trace: list[str] = []
    bus = MessageBus()
    asr = FakeAsrGate(trace)
    dispatcher = CatalogRefreshingDispatcher(trace)
    service = WakeGateService(
        bus,
        asr,
        FakeSentenceSource(trace),
        FakeSentencePlayer(trace),
        dispatcher,
        FakePlaybackSink(trace),
        query_interval_seconds=0.001,
        query_timeout_seconds=0.1,
        laughter_timeout_seconds=1,
    )

    await bus.start()
    sound_responder = await _install_sound_responder(bus, trace, [True, False])
    actions_responder = await _install_actions_responder(bus, trace, dispatcher)
    await service.start()
    try:
        await bus.publish(_audio_started("trigger-1"))
        await _wait_state(service, ConversationState.LISTENING)

        assert dispatcher.attempts == 2
        assert "actions:query" in trace
        assert len(dispatcher.actions) == 1
        assert asr.arms == [(DEVICE_ID, SESSION_ID)]
    finally:
        await service.shutdown()
        await bus.unsubscribe(sound_responder)
        await bus.unsubscribe(actions_responder)
        await bus.stop()


@pytest.mark.asyncio
async def test_laughter_refreshes_catalog_and_idle_state_after_runtime_restart() -> None:
    trace: list[str] = []
    bus = MessageBus()
    asr = FakeAsrGate(trace)
    dispatcher = StartupRefreshingDispatcher(trace)
    service = WakeGateService(
        bus,
        asr,
        FakeSentenceSource(trace),
        FakeSentencePlayer(trace),
        dispatcher,
        FakePlaybackSink(trace),
        query_interval_seconds=0.001,
        query_timeout_seconds=0.1,
        laughter_timeout_seconds=1,
    )
    states = iter((False, True, False))

    async def respond_state(query: Message) -> None:
        dispatcher.state_ready = True
        busy = next(states, False)
        trace.append(f"sound:{busy}")
        await bus.publish(
            Message.create(
                topic="device.state.received",
                kind=MessageKind.STATE,
                source=f"device:{DEVICE_ID}:mqtt",
                target=f"device:{DEVICE_ID}",
                correlation_id=query.message_id,
                payload={
                    "device_id": DEVICE_ID,
                    "transport": "websocket",
                    "action_state": "idle",
                    "current_action": "idle",
                    "sound_busy": busy,
                },
            )
        )

    await bus.start()
    state_responder = await bus.subscribe("device.state.query.requested", respond_state)
    actions_responder = await _install_actions_responder(bus, trace, dispatcher)
    await service.start()
    try:
        await bus.publish(_audio_started("trigger-1"))
        await _wait_state(service, ConversationState.LISTENING)

        assert dispatcher.catalog_ready is True
        assert dispatcher.state_ready is True
        assert len(dispatcher.actions) == 1
        assert asr.arms == [(DEVICE_ID, SESSION_ID)]
    finally:
        await service.shutdown()
        await bus.unsubscribe(state_responder)
        await bus.unsubscribe(actions_responder)
        await bus.stop()


@pytest.mark.asyncio
async def test_post_tts_auto_listen_starts_the_next_laughter_gated_turn() -> None:
    trace: list[str] = []
    bus = MessageBus()
    dispatcher = FakeDispatcher(trace)
    playback_sink = FakePlaybackSink(trace, auto_listen_bus=bus)
    service = WakeGateService(
        bus,
        FakeAsrGate(trace),
        FakeSentenceSource(trace),
        FakeSentencePlayer(trace),
        dispatcher,
        playback_sink,
        query_interval_seconds=0.001,
        query_timeout_seconds=0.1,
        laughter_timeout_seconds=1,
    )

    await bus.start()
    responder = await _install_sound_responder(bus, trace, [True, False, True, False])
    await service.start()
    try:
        await bus.publish(_audio_started("trigger-1"))
        await _wait_state(service, ConversationState.LISTENING)
        await bus.publish(_audio_started("question-1"))
        await _wait_state(service, ConversationState.RECOGNIZING)
        await bus.publish(_transcription("question-1", "你是谁？"))
        await _wait_state(service, ConversationState.LISTENING)
        await bus.drain()

        assert len(dispatcher.actions) == 2
        assert playback_sink.closed_sessions == []
        assert service.status()["devices"][DEVICE_ID]["state"] == "listening"

        await bus.publish(_session_closed())
        await _wait_state(service, ConversationState.WAITING)
        assert service.status()["devices"][DEVICE_ID]["exit_reason"] == "device_goodbye"
    finally:
        await service.shutdown()
        await bus.unsubscribe(responder)
        await bus.stop()


@pytest.mark.asyncio
async def test_final_transcription_is_displayed_once_and_bounded_before_llm() -> None:
    trace: list[str] = []
    bus = MessageBus()
    llm = FakeSentenceSource(trace)
    playback_sink = FakePlaybackSink(trace)
    dispatcher = FakeDispatcher(trace)
    service = WakeGateService(
        bus,
        FakeAsrGate(trace),
        llm,
        FakeSentencePlayer(trace),
        dispatcher,
        playback_sink,
        query_interval_seconds=0.001,
        query_timeout_seconds=0.1,
        laughter_timeout_seconds=1,
        transcription_max_chars=32,
    )

    await bus.start()
    responder = await _install_sound_responder(bus, trace, [True, False, True, False])
    await service.start()
    try:
        await bus.publish(_audio_started("trigger-1"))
        await _wait_state(service, ConversationState.LISTENING)
        await bus.publish(_audio_started("question-1"))
        await _wait_state(service, ConversationState.RECOGNIZING)
        long_text = "奶" * 40
        await bus.publish(_transcription("question-1", long_text))
        await bus.publish(_transcription("question-1", long_text))
        await _wait_state(service, ConversationState.LISTENING)

        bounded = "奶" * 32
        assert playback_sink.transcriptions == [(DEVICE_ID, SESSION_ID, bounded)]
        assert llm.prompts == [bounded]
        assert len(dispatcher.actions) == 2
    finally:
        await service.shutdown()
        await bus.unsubscribe(responder)
        await bus.stop()


@pytest.mark.asyncio
async def test_eight_second_equivalent_idle_timeout_closes_the_chat_session() -> None:
    trace: list[str] = []
    bus = MessageBus()
    playback_sink = FakePlaybackSink(trace)
    service = WakeGateService(
        bus,
        FakeAsrGate(trace),
        FakeSentenceSource(trace),
        FakeSentencePlayer(trace),
        FakeDispatcher(trace),
        playback_sink,
        query_interval_seconds=0.001,
        query_timeout_seconds=0.1,
        laughter_timeout_seconds=1,
        idle_timeout_seconds=0.03,
    )

    await bus.start()
    responder = await _install_sound_responder(bus, trace, [True, False])
    await service.start()
    try:
        await bus.publish(_audio_started("trigger-1"))
        await _wait_state(service, ConversationState.LISTENING)
        await _wait_state(service, ConversationState.WAITING)

        assert playback_sink.closed_sessions == [(DEVICE_ID, SESSION_ID)]
        assert service.status()["devices"][DEVICE_ID]["exit_reason"] == "idle_timeout"
    finally:
        await service.shutdown()
        await bus.unsubscribe(responder)
        await bus.stop()


@pytest.mark.asyncio
async def test_real_speech_activity_cancels_the_idle_timeout() -> None:
    trace: list[str] = []
    bus = MessageBus()
    playback_sink = FakePlaybackSink(trace)
    service = WakeGateService(
        bus,
        FakeAsrGate(trace),
        FakeSentenceSource(trace),
        FakeSentencePlayer(trace),
        FakeDispatcher(trace),
        playback_sink,
        query_interval_seconds=0.001,
        query_timeout_seconds=0.1,
        laughter_timeout_seconds=1,
        idle_timeout_seconds=0.03,
    )

    await bus.start()
    responder = await _install_sound_responder(bus, trace, [True, False])
    await service.start()
    try:
        await bus.publish(_audio_started("trigger-1"))
        await _wait_state(service, ConversationState.LISTENING)
        await bus.publish(_audio_started("question-1"))
        await _wait_state(service, ConversationState.RECOGNIZING)
        await bus.publish(_activity("question-1"))
        await asyncio.sleep(0.06)

        status = service.status()["devices"][DEVICE_ID]
        assert status["state"] == "recognizing"
        assert status["speech_detected"] is True
        assert playback_sink.closed_sessions == []
    finally:
        await service.shutdown()
        await bus.unsubscribe(responder)
        await bus.stop()


@pytest.mark.asyncio
async def test_transcription_display_failure_closes_session_without_calling_llm() -> None:
    trace: list[str] = []
    bus = MessageBus()
    llm = FakeSentenceSource(trace)
    tts = FakeSentencePlayer(trace)
    dispatcher = FakeDispatcher(trace)
    playback_sink = FakePlaybackSink(
        trace,
        transcription_error="websocket_control_write_failed",
    )
    service = WakeGateService(
        bus,
        FakeAsrGate(trace),
        llm,
        tts,
        dispatcher,
        playback_sink,
        query_interval_seconds=0.001,
        query_timeout_seconds=0.1,
        laughter_timeout_seconds=1,
    )

    await bus.start()
    responder = await _install_sound_responder(bus, trace, [True, False])
    await service.start()
    try:
        await bus.publish(_audio_started("trigger-1"))
        await _wait_state(service, ConversationState.LISTENING)
        await bus.publish(_audio_started("question-1"))
        await _wait_state(service, ConversationState.RECOGNIZING)
        await bus.publish(_transcription("question-1", "你是谁？"))
        await _wait_state(service, ConversationState.FAILED)

        assert llm.prompts == []
        assert tts.played == []
        assert dispatcher.stops == 1
        assert playback_sink.closed_sessions == [(DEVICE_ID, SESSION_ID)]
        assert service.status()["devices"][DEVICE_ID]["failure_code"] == (
            "websocket_control_write_failed"
        )
    finally:
        await service.shutdown()
        await bus.unsubscribe(responder)
        await bus.stop()


@pytest.mark.asyncio
async def test_answer_timeout_cancels_pipeline_and_closes_session() -> None:
    class HangingSentenceSource:
        async def stream_sentences(
            self,
            device_id: str,
            prompt: str,
        ) -> AsyncIterator[str]:
            trace.append("llm:hang")
            await asyncio.Event().wait()
            yield "不会返回。"

    trace: list[str] = []
    bus = MessageBus()
    dispatcher = FakeDispatcher(trace)
    playback_sink = FakePlaybackSink(trace)
    service = WakeGateService(
        bus,
        FakeAsrGate(trace),
        HangingSentenceSource(),
        FakeSentencePlayer(trace),
        dispatcher,
        playback_sink,
        query_interval_seconds=0.001,
        query_timeout_seconds=0.1,
        laughter_timeout_seconds=1,
        conversation_timeout_seconds=0.02,
    )

    await bus.start()
    responder = await _install_sound_responder(bus, trace, [True, False])
    await service.start()
    try:
        await bus.publish(_audio_started("trigger-1"))
        await _wait_state(service, ConversationState.LISTENING)
        await bus.publish(_audio_started("question-1"))
        await _wait_state(service, ConversationState.RECOGNIZING)
        await bus.publish(_transcription("question-1", "你是谁？"))
        await _wait_state(service, ConversationState.FAILED)

        assert playback_sink.transcriptions == [(DEVICE_ID, SESSION_ID, "你是谁？")]
        assert dispatcher.stops == 1
        assert playback_sink.closed_sessions == [(DEVICE_ID, SESSION_ID)]
        assert service.status()["devices"][DEVICE_ID]["failure_code"] == (
            "conversation_timeout"
        )
    finally:
        await service.shutdown()
        await bus.unsubscribe(responder)
        await bus.stop()


@pytest.mark.asyncio
async def test_laughter_without_busy_true_fails_closed_and_never_arms_asr() -> None:
    trace: list[str] = []
    bus = MessageBus()
    asr = FakeAsrGate(trace)
    dispatcher = FakeDispatcher(trace)
    service = WakeGateService(
        bus,
        asr,
        FakeSentenceSource(trace),
        FakeSentencePlayer(trace),
        dispatcher,
        FakePlaybackSink(trace),
        query_interval_seconds=0.005,
        query_timeout_seconds=0.1,
        laughter_timeout_seconds=0.25,
    )

    await bus.start()
    responder = await _install_sound_responder(bus, trace, [False])
    await service.start()
    try:
        await bus.publish(_audio_started("trigger-1"))
        await _wait_state(service, ConversationState.FAILED)

        assert asr.arms == []
        assert dispatcher.stops == 1
        assert service.status()["devices"][DEVICE_ID]["failure_code"] == (
            "laughter_not_started"
        )
        assert "laugh:tts_start" not in trace
        assert "laugh:tts_stop" not in trace
    finally:
        await service.shutdown()
        await bus.unsubscribe(responder)
        await bus.stop()


@pytest.mark.asyncio
async def test_new_device_session_recovers_after_a_failed_laughter_gate() -> None:
    trace: list[str] = []
    bus = MessageBus()
    asr = FakeAsrGate(trace)
    dispatcher = FakeDispatcher(trace)
    service = WakeGateService(
        bus,
        asr,
        FakeSentenceSource(trace),
        FakeSentencePlayer(trace),
        dispatcher,
        FakePlaybackSink(trace),
        query_interval_seconds=0.005,
        query_timeout_seconds=0.1,
        laughter_timeout_seconds=0.25,
    )

    await bus.start()
    responder = await _install_sound_responder(bus, trace, [False])
    await service.start()
    try:
        await bus.publish(_audio_started("failed-session"))
        await _wait_state(service, ConversationState.FAILED)
        old_round = service.status()["devices"][DEVICE_ID]["round_id"]

        replacement = _audio_started("replacement-session")
        replacement.payload["session_id"] = "ws-session-2"
        await bus.publish(replacement)
        await _wait_state(service, ConversationState.LAUGHING)

        status = service.status()["devices"][DEVICE_ID]
        assert status["session_id"] == "ws-session-2"
        assert status["round_id"] != old_round
        assert status["failure_code"] is None
        await _wait_action_count(dispatcher, 2)
        assert len(dispatcher.actions) == 2
    finally:
        await service.shutdown()
        await bus.unsubscribe(responder)
        await bus.stop()


@pytest.mark.asyncio
async def test_laughter_busy_without_false_completion_fails_closed() -> None:
    trace: list[str] = []
    bus = MessageBus()
    asr = FakeAsrGate(trace)
    dispatcher = FakeDispatcher(trace)
    service = WakeGateService(
        bus,
        asr,
        FakeSentenceSource(trace),
        FakeSentencePlayer(trace),
        dispatcher,
        FakePlaybackSink(trace),
        query_interval_seconds=0.005,
        query_timeout_seconds=0.1,
        laughter_timeout_seconds=0.25,
    )

    await bus.start()
    responder = await _install_sound_responder(bus, trace, [True])
    await service.start()
    try:
        await bus.publish(_audio_started("trigger-1"))
        await _wait_state(service, ConversationState.FAILED)

        assert asr.arms == []
        assert dispatcher.stops == 1
        assert service.status()["devices"][DEVICE_ID]["failure_code"] == (
            "laughter_not_finished"
        )
    finally:
        await service.shutdown()
        await bus.unsubscribe(responder)
        await bus.stop()
