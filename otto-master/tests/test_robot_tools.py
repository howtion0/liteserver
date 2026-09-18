from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from otto_master.messages import JsonValue
from otto_master.services.llm import LlmToolCall
from otto_master.services.robot_tools import RobotToolBridge, RobotToolError

DEVICE_ID = "aabbccddee01"


def _catalog() -> list[dict[str, JsonValue]]:
    parameters: dict[str, JsonValue] = {
        "steps": {"type": "integer", "minimum": 1, "maximum": 100, "default": 3},
        "speed": {"type": "integer", "minimum": 500, "maximum": 1500, "default": 1000},
        "direction": {"type": "integer", "minimum": -1, "maximum": 1, "default": 1},
        "amount": {"type": "integer", "minimum": 0, "maximum": 170, "default": 50},
    }
    return [
        {"name": "walk", "parameters": parameters},
        {"name": "turn", "parameters": parameters},
        {"name": "laugh", "parameters": parameters},
        {"name": "home", "parameters": parameters},
        {"name": "set_trim", "parameters": parameters},
    ]


class FakeDevices:
    def __init__(self) -> None:
        self.device: dict[str, Any] = {
            "device_id": DEVICE_ID,
            "status": "online",
            "enabled": True,
            "capabilities": {"actions": True, "stop": True},
        }
        self.actions = _catalog()

    async def get_device(self, device_id: str) -> dict[str, Any] | None:
        return dict(self.device) if device_id == DEVICE_ID else None

    async def get_actions(self, device_id: str) -> list[dict[str, JsonValue]] | None:
        return [dict(item) for item in self.actions] if device_id == DEVICE_ID else None


class FakeDispatcher:
    def __init__(self, *, terminal_status: str = "completed") -> None:
        self.terminal_status = terminal_status
        self.actions: list[dict[str, Any]] = []
        self.stops: list[dict[str, Any]] = []
        self.records: dict[str, dict[str, Any]] = {}
        self.hold = False

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
        assert command_id is not None
        request = {
            "device_id": device_id,
            "action": action,
            "parameters": dict(parameters or {}),
            "confirmation": confirmation,
            "source": source,
            "command_id": command_id,
            "correlation_id": correlation_id,
        }
        self.actions.append(request)
        self.records[command_id] = {
            "command_id": command_id,
            "status": "moving" if self.hold else self.terminal_status,
            "error": "simulated_failure" if self.terminal_status != "completed" else None,
        }
        return dict(self.records[command_id])

    async def submit_stop(
        self,
        *,
        device_id: str,
        source: str = "webui",
        command_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        request = {
            "device_id": device_id,
            "source": source,
            "command_id": command_id,
            "correlation_id": correlation_id,
        }
        self.stops.append(request)
        if command_id is not None:
            self.records[command_id] = {"command_id": command_id, "status": "completed"}
        return {"command_id": command_id, "status": "completed"}

    async def get_command(self, command_id: str) -> dict[str, Any] | None:
        return self.records.get(command_id)


@pytest.mark.asyncio
async def test_robot_tools_are_derived_from_catalog_and_narrowed_for_voice_safety() -> None:
    bridge = RobotToolBridge(FakeDevices(), FakeDispatcher())

    tools = await bridge.tools_for_device(DEVICE_ID)
    by_name = {tool.name: tool for tool in tools}

    assert set(by_name) == {
        "self_otto_walk_forward",
        "self_otto_turn_left",
        "self_otto_laugh",
        "self_otto_stop",
    }
    walk = by_name["self_otto_walk_forward"].parameters["properties"]
    assert isinstance(walk, dict)
    assert walk["steps"]["maximum"] == 10
    assert walk["speed"]["minimum"] == 700
    assert walk["direction"]["enum"] == [-1, 1]
    assert by_name["self_otto_laugh"].parameters["properties"] == {}
    assert "self_otto_home" not in by_name


@pytest.mark.asyncio
async def test_robot_tool_executes_on_the_conversation_device_and_waits_for_completion() -> None:
    dispatcher = FakeDispatcher()
    bridge = RobotToolBridge(FakeDevices(), dispatcher, poll_interval_seconds=0.001)
    call = LlmToolCall(
        call_id="call-walk",
        name="self_otto_walk_forward",
        arguments={"steps": 4, "direction": -1},
    )

    result = await bridge.execute(DEVICE_ID, call, correlation_id="utterance-1")

    assert result.status == "completed"
    assert result.action == "walk"
    assert dispatcher.actions == [
        {
            "device_id": DEVICE_ID,
            "action": "walk",
            "parameters": {"steps": 4, "direction": -1},
            "confirmation": True,
            "source": "service:llm_tool",
            "command_id": result.command_id,
            "correlation_id": "utterance-1",
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"steps": 11},
        {"direction": 0},
        {"unknown": 1},
        {"steps": True},
    ],
)
async def test_robot_tool_rejects_untrusted_arguments_before_dispatch(
    arguments: dict[str, JsonValue],
) -> None:
    dispatcher = FakeDispatcher()
    bridge = RobotToolBridge(FakeDevices(), dispatcher)
    call = LlmToolCall("call-invalid", "self_otto_walk_forward", arguments)

    with pytest.raises(RobotToolError, match="invalid_tool_arguments"):
        await bridge.execute(DEVICE_ID, call, correlation_id="utterance-invalid")

    assert dispatcher.actions == []


@pytest.mark.asyncio
async def test_robot_tool_failure_is_reported_and_requests_a_safety_stop() -> None:
    dispatcher = FakeDispatcher(terminal_status="failed")
    bridge = RobotToolBridge(FakeDevices(), dispatcher, poll_interval_seconds=0.001)
    call = LlmToolCall("call-fail", "self_otto_walk_forward", {"steps": 2})

    with pytest.raises(RobotToolError, match="simulated_failure"):
        await bridge.execute(DEVICE_ID, call, correlation_id="utterance-fail")

    assert len(dispatcher.actions) == 1
    assert len(dispatcher.stops) == 1
    assert dispatcher.stops[0]["source"] == "service:llm_tool:safety"


@pytest.mark.asyncio
async def test_robot_tool_cancellation_stops_an_active_action() -> None:
    dispatcher = FakeDispatcher()
    dispatcher.hold = True
    bridge = RobotToolBridge(
        FakeDevices(),
        dispatcher,
        completion_timeout_seconds=2,
        poll_interval_seconds=0.005,
    )
    call = LlmToolCall("call-cancel", "self_otto_walk_forward", {"steps": 2})
    task = asyncio.create_task(
        bridge.execute(DEVICE_ID, call, correlation_id="utterance-cancel")
    )
    while not dispatcher.actions:
        await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(dispatcher.stops) == 1
    assert dispatcher.stops[0]["source"] == "service:llm_tool:safety"
