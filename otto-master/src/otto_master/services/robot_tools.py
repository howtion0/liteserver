"""Bounded LLM tool schemas and execution through the verified robot dispatcher."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from ..gateways.cloud import ChatToolDefinition
from ..messages import JsonValue
from .llm import LlmToolCall

_TERMINAL_COMMAND_STATUSES = frozenset(
    {"completed", "rejected", "timeout", "disconnected", "failed"}
)

_ACTION_TO_TOOL: dict[str, tuple[str, str]] = {
    "walk": (
        "self_otto_walk_forward",
        "让机器人行走。direction=1表示前进，direction=-1表示后退；用户没有说明步数时使用默认值。",
    ),
    "turn": (
        "self_otto_turn_left",
        "让机器人原地转向。direction=1表示左转，direction=-1表示右转。",
    ),
    "jump": ("self_otto_jump", "让机器人原地跳跃，只有用户明确要求跳跃时才调用。"),
    "swing": ("self_otto_swing", "让机器人左右摇摆。"),
    "moonwalk": ("self_otto_moonwalk", "让机器人表演太空步。"),
    "bend": ("self_otto_bend", "让机器人向左或向右弯曲身体。"),
    "shake_leg": ("self_otto_shake_leg", "让机器人摇左腿或右腿。"),
    "updown": ("self_otto_updown", "让机器人做上下起伏动作。"),
    "tiptoe_swing": ("self_otto_tiptoe_swing", "让机器人踮脚左右摇摆。"),
    "jitter": ("self_otto_jitter", "让机器人做短暂抖动动作。"),
    "ascending_turn": ("self_otto_ascending_turn", "让机器人做上升转体动作。"),
    "crusaito": ("self_otto_crusaito", "让机器人做 Crusaito 舞步。"),
    "flapping": ("self_otto_flapping", "让机器人做拍翅式摆动动作。"),
    "laugh": (
        "self_otto_laugh",
        "播放一次约两秒的本地奶龙大笑，不移动舵机。用户要求大笑或确实听不懂时调用。",
    ),
}

_ACTION_PARAMETERS: dict[str, frozenset[str]] = {
    "walk": frozenset({"steps", "speed", "direction", "amount"}),
    "turn": frozenset({"steps", "speed", "direction", "amount"}),
    "jump": frozenset({"steps", "speed"}),
    "swing": frozenset({"steps", "speed", "amount"}),
    "moonwalk": frozenset({"steps", "speed", "direction", "amount"}),
    "bend": frozenset({"steps", "speed", "direction"}),
    "shake_leg": frozenset({"steps", "speed", "direction"}),
    "updown": frozenset({"steps", "speed", "amount"}),
    "tiptoe_swing": frozenset({"steps", "speed", "amount"}),
    "jitter": frozenset({"steps", "speed", "amount"}),
    "ascending_turn": frozenset({"steps", "speed", "amount"}),
    "crusaito": frozenset({"steps", "speed", "direction", "amount"}),
    "flapping": frozenset({"steps", "speed", "direction", "amount"}),
    "laugh": frozenset(),
}

_PARAMETER_DESCRIPTIONS = {
    "steps": "动作次数，省略时由设备使用默认值。",
    "speed": "动作周期毫秒数，数值越小越快；省略时默认1000。",
    "direction": "方向，只能是1或-1，具体含义见工具描述。",
    "amount": "动作或摆臂幅度，单位为度。",
}


class RobotToolError(RuntimeError):
    """A stable, non-secret tool rejection suitable for the voice boundary."""

    def __init__(self, code: str, user_message: str = "这个动作没执行成功，请再说一次吧。"):
        super().__init__(code)
        self.code = code[:128]
        self.user_message = user_message


@dataclass(frozen=True, slots=True)
class RobotToolResult:
    tool_name: str
    command_id: str
    command_type: str
    action: str | None
    status: str

    @property
    def completed_laugh(self) -> bool:
        return self.command_type == "action" and self.action == "laugh" and self.status == "completed"


@dataclass(frozen=True, slots=True)
class _ToolBinding:
    definition: ChatToolDefinition
    command_type: str
    action: str | None


class RobotToolDeviceReader(Protocol):
    async def get_device(self, device_id: str) -> dict[str, Any] | None: ...

    async def get_actions(
        self,
        device_id: str,
    ) -> list[dict[str, JsonValue]] | None: ...


class RobotToolDispatcher(Protocol):
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

    async def get_command(self, command_id: str) -> dict[str, Any] | None: ...


class RobotToolBridge:
    """Expose only safe device actions and wait for their physical lifecycle."""

    def __init__(
        self,
        devices: RobotToolDeviceReader,
        dispatcher: RobotToolDispatcher,
        *,
        completion_timeout_seconds: float = 20.0,
        poll_interval_seconds: float = 0.05,
    ) -> None:
        if completion_timeout_seconds <= 0 or poll_interval_seconds <= 0:
            raise ValueError("robot tool timeouts must be positive")
        self.devices = devices
        self.dispatcher = dispatcher
        self.completion_timeout_seconds = completion_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds

    async def tools_for_device(self, device_id: str) -> tuple[ChatToolDefinition, ...]:
        return tuple(binding.definition for binding in await self._bindings(device_id))

    async def execute(
        self,
        device_id: str,
        tool_call: LlmToolCall,
        *,
        correlation_id: str,
    ) -> RobotToolResult:
        bindings = {binding.definition.name: binding for binding in await self._bindings(device_id)}
        binding = bindings.get(tool_call.name)
        if binding is None:
            raise RobotToolError("tool_not_available")
        arguments = dict(tool_call.arguments)
        _validate_arguments(arguments, binding.definition.parameters)
        command_id = str(
            uuid5(
                NAMESPACE_URL,
                f"otto-llm-tool:{device_id}:{correlation_id}:{tool_call.call_id}:{tool_call.name}",
            )
        )
        try:
            if binding.command_type == "stop":
                await self.dispatcher.submit_stop(
                    device_id=device_id,
                    source="service:llm_tool",
                    command_id=command_id,
                    correlation_id=correlation_id,
                )
            else:
                if binding.action is None:
                    raise RobotToolError("tool_binding_invalid")
                await self.dispatcher.submit_action(
                    device_id=device_id,
                    action=binding.action,
                    parameters=arguments,
                    confirmation=True,
                    source="service:llm_tool",
                    command_id=command_id,
                    correlation_id=correlation_id,
                )
            record = await self._wait_for_terminal(command_id)
        except asyncio.CancelledError:
            if binding.command_type == "action":
                await self._stop_after_interruption(device_id, correlation_id, command_id)
            raise
        except RobotToolError:
            raise
        except Exception as exc:
            code = getattr(exc, "code", None)
            raise RobotToolError(code if isinstance(code, str) else "tool_dispatch_failed") from exc

        status = record.get("status")
        if status != "completed":
            if binding.command_type == "action":
                await self._stop_after_interruption(device_id, correlation_id, command_id)
            error = record.get("error")
            code = error if isinstance(error, str) and error else f"tool_command_{status}"
            raise RobotToolError(code)
        return RobotToolResult(
            tool_name=tool_call.name,
            command_id=command_id,
            command_type=binding.command_type,
            action=binding.action,
            status=status,
        )

    async def _bindings(self, device_id: str) -> tuple[_ToolBinding, ...]:
        device = await self.devices.get_device(device_id)
        if device is None or device.get("status") != "online" or not device.get("enabled", True):
            return ()
        capabilities = device.get("capabilities")
        if not isinstance(capabilities, dict):
            capabilities = {}
        bindings: list[_ToolBinding] = []
        if capabilities.get("actions") is True:
            actions = await self.devices.get_actions(device_id) or []
            for item in actions:
                binding = _action_binding(item)
                if binding is not None:
                    bindings.append(binding)
        if capabilities.get("stop") is True:
            bindings.append(
                _ToolBinding(
                    definition=ChatToolDefinition(
                        name="self_otto_stop",
                        description="立即停止当前机器人动作。只有用户明确要求停止时才调用。",
                        parameters={
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                    ),
                    command_type="stop",
                    action=None,
                )
            )
        return tuple(bindings[:32])

    async def _wait_for_terminal(self, command_id: str) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + self.completion_timeout_seconds
        while True:
            record = await self.dispatcher.get_command(command_id)
            if record is not None:
                status = record.get("status")
                if isinstance(status, str) and status in _TERMINAL_COMMAND_STATUSES:
                    return record
            if asyncio.get_running_loop().time() >= deadline:
                raise RobotToolError("tool_completion_timeout")
            await asyncio.sleep(self.poll_interval_seconds)

    async def _stop_after_interruption(
        self,
        device_id: str,
        correlation_id: str,
        action_command_id: str,
    ) -> None:
        stop_id = str(uuid5(NAMESPACE_URL, f"{action_command_id}:safety-stop"))
        task = asyncio.create_task(
            self.dispatcher.submit_stop(
                device_id=device_id,
                source="service:llm_tool:safety",
                command_id=stop_id,
                correlation_id=correlation_id,
            )
        )
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=4.0)
        except Exception:  # noqa: BLE001 - cleanup is best-effort after primary failure
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def _action_binding(item: Mapping[str, JsonValue]) -> _ToolBinding | None:
    action = item.get("name")
    if not isinstance(action, str) or action not in _ACTION_TO_TOOL:
        return None
    tool_name, description = _ACTION_TO_TOOL[action]
    raw_parameters = item.get("parameters")
    catalog = raw_parameters if isinstance(raw_parameters, dict) else {}
    properties: dict[str, JsonValue] = {}
    required: list[JsonValue] = []
    for name in sorted(_ACTION_PARAMETERS[action]):
        raw_descriptor = catalog.get(name)
        descriptor = _safe_parameter_descriptor(action, name, raw_descriptor)
        if descriptor is None:
            continue
        properties[name] = descriptor
        if isinstance(raw_descriptor, dict) and raw_descriptor.get("required") is True:
            required.append(name)
    schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return _ToolBinding(
        definition=ChatToolDefinition(
            name=tool_name,
            description=description,
            parameters=schema,
        ),
        command_type="action",
        action=action,
    )


def _safe_parameter_descriptor(
    action: str,
    name: str,
    value: JsonValue,
) -> dict[str, JsonValue] | None:
    if not isinstance(value, dict) or value.get("type") != "integer":
        return None
    raw_minimum = value.get("minimum", value.get("min"))
    raw_maximum = value.get("maximum", value.get("max"))
    if not isinstance(raw_minimum, (int, float)) or isinstance(raw_minimum, bool):
        return None
    if not isinstance(raw_maximum, (int, float)) or isinstance(raw_maximum, bool):
        return None
    minimum = int(raw_minimum)
    maximum = int(raw_maximum)
    if name == "steps":
        maximum = min(maximum, 3 if action == "jump" else 10)
    elif name == "speed":
        minimum = max(minimum, 700)
    elif name == "amount":
        maximum = min(maximum, 80)
    descriptor: dict[str, JsonValue] = {
        "type": "integer",
        "description": _PARAMETER_DESCRIPTIONS[name],
        "minimum": minimum,
        "maximum": maximum,
    }
    if name == "direction":
        descriptor["enum"] = [-1, 1]
    default = value.get("default")
    if (
        isinstance(default, int)
        and not isinstance(default, bool)
        and minimum <= default <= maximum
        and (name != "direction" or default in {-1, 1})
    ):
        descriptor["default"] = default
    return descriptor


def _validate_arguments(
    arguments: Mapping[str, JsonValue],
    schema: Mapping[str, Any],
) -> None:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise RobotToolError("tool_schema_invalid")
    unknown = set(arguments) - set(properties)
    if unknown:
        raise RobotToolError("invalid_tool_arguments")
    required = schema.get("required", [])
    if isinstance(required, list) and any(name not in arguments for name in required):
        raise RobotToolError("invalid_tool_arguments")
    for name, value in arguments.items():
        descriptor = properties.get(name)
        if not isinstance(descriptor, dict):
            raise RobotToolError("tool_schema_invalid")
        expected = descriptor.get("type")
        if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            raise RobotToolError("invalid_tool_arguments")
        if expected == "number" and (
            not isinstance(value, (int, float)) or isinstance(value, bool)
        ):
            raise RobotToolError("invalid_tool_arguments")
        if expected == "string" and not isinstance(value, str):
            raise RobotToolError("invalid_tool_arguments")
        if expected == "boolean" and not isinstance(value, bool):
            raise RobotToolError("invalid_tool_arguments")
        allowed = descriptor.get("enum")
        if isinstance(allowed, list) and value not in allowed:
            raise RobotToolError("invalid_tool_arguments")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            minimum = descriptor.get("minimum")
            maximum = descriptor.get("maximum")
            if isinstance(minimum, (int, float)) and value < minimum:
                raise RobotToolError("invalid_tool_arguments")
            if isinstance(maximum, (int, float)) and value > maximum:
                raise RobotToolError("invalid_tool_arguments")
