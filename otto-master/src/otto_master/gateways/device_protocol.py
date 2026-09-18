"""Transport-neutral Otto JSON validation and domain-message translation."""

from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from typing import Any, NoReturn, cast

from ..messages import JsonValue, Message, MessageKind
from .mqtt_broker import normalize_device_id

MAX_DEVICE_MESSAGE_BYTES = 64 * 1024
MAX_DEVICE_ACTIONS = 128
MAX_JSON_DEPTH = 16
MAX_JSON_NODES = 4096
MAX_ACTION_PARAMETERS = 32
QUERY_TYPES = {
    "device.state.query.requested": "otto_query",
    "device.actions.query.requested": "otto_actions",
}
ACTION_COMMAND_TOPIC = "device.action.execute.requested"
STOP_COMMAND_TOPIC = "device.stop.execute.requested"
OUTBOUND_COMMAND_TOPICS = (*QUERY_TYPES, ACTION_COMMAND_TOPIC, STOP_COMMAND_TOPIC)
SUPPORTED_DEVICE_TRANSPORTS = frozenset({"mqtt", "tcp", "websocket"})
_ACTION_RESERVED_FIELDS = frozenset({"type", "id", "action"})


class DeviceProtocolError(ValueError):
    """Raised when an untrusted device frame violates the external contract."""


def _reject_constant(value: str) -> NoReturn:
    raise DeviceProtocolError(f"non-finite JSON number is not allowed: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DeviceProtocolError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def decode_device_json(
    payload: bytes,
    *,
    maximum_bytes: int = MAX_DEVICE_MESSAGE_BYTES,
) -> dict[str, Any]:
    """Decode bounded UTF-8 JSON while rejecting duplicates and non-finite values."""

    if not payload:
        raise DeviceProtocolError("device payload must not be empty")
    if len(payload) > maximum_bytes:
        raise DeviceProtocolError(f"device payload exceeds {maximum_bytes} bytes")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except UnicodeDecodeError as exc:
        raise DeviceProtocolError("device payload must be UTF-8") from exc
    except DeviceProtocolError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise DeviceProtocolError("device payload must be valid JSON") from exc
    if not isinstance(value, dict):
        raise DeviceProtocolError("device payload must be a JSON object")
    _validate_json_shape(value)
    return value


def _validate_json_shape(value: Any) -> None:
    remaining = MAX_JSON_NODES

    def visit(item: Any, depth: int) -> None:
        nonlocal remaining
        remaining -= 1
        if remaining < 0:
            raise DeviceProtocolError(f"device JSON exceeds {MAX_JSON_NODES} values")
        if depth > MAX_JSON_DEPTH:
            raise DeviceProtocolError(f"device JSON exceeds depth {MAX_JSON_DEPTH}")
        if isinstance(item, dict):
            for child in item.values():
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)

    visit(value, 0)


def required_string(value: dict[str, Any], field: str, *, maximum: int) -> str:
    raw = value.get(field)
    if not isinstance(raw, str) or not raw.strip():
        raise DeviceProtocolError(f"{field} must be a non-empty string")
    normalized = raw.strip()
    if len(normalized) > maximum:
        raise DeviceProtocolError(f"{field} exceeds {maximum} characters")
    return normalized


def optional_external_id(value: dict[str, Any]) -> str | None:
    if "id" not in value:
        return None
    return required_string(value, "id", maximum=128)


def validate_payload_identity(device_id: str, value: dict[str, Any]) -> None:
    supplied_mac = value.get("mac")
    if supplied_mac is None:
        return
    if not isinstance(supplied_mac, str):
        raise DeviceProtocolError("mac must be a string")
    try:
        payload_device_id = normalize_device_id(supplied_mac)
    except ValueError as exc:
        raise DeviceProtocolError("mac is not a valid hardware address") from exc
    if payload_device_id != device_id:
        raise DeviceProtocolError("payload MAC does not match authenticated device")


def _base_payload(
    device_id: str,
    transport: str,
    external_message_id: str | None,
) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {
        "device_id": device_id,
        "mac": device_id,
        "transport": transport,
    }
    if external_message_id is not None:
        result["external_message_id"] = external_message_id
    return result


def _message(
    *,
    device_id: str,
    transport: str,
    topic: str,
    kind: MessageKind,
    payload: dict[str, JsonValue],
    correlation_id: str | None = None,
) -> Message:
    return Message.create(
        topic=topic,
        kind=kind,
        source=f"device:{device_id}:{transport}",
        target=f"device:{device_id}",
        payload=payload,
        correlation_id=correlation_id,
    )


def _hello(
    device_id: str,
    transport: str,
    value: dict[str, Any],
    *,
    expected_protocol: str,
) -> Message:
    protocol = required_string(value, "protocol", maximum=32)
    if protocol != expected_protocol:
        raise DeviceProtocolError("unsupported device protocol")
    if "mac" not in value:
        raise DeviceProtocolError("hello must include mac")
    name = required_string(value, "name", maximum=80)
    firmware_version = required_string(value, "firmware_version", maximum=80)
    capabilities = value.get("capabilities", {})
    if not isinstance(capabilities, dict):
        raise DeviceProtocolError("capabilities must be an object")
    payload = _base_payload(device_id, transport, optional_external_id(value))
    payload.update(
        {
            "protocol": protocol,
            "name": name,
            "device_name": name,
            "firmware_version": firmware_version,
            "capabilities": cast(dict[str, JsonValue], capabilities),
        }
    )
    ip_address = value.get("ip_address")
    if ip_address is not None:
        if not isinstance(ip_address, str):
            raise DeviceProtocolError("ip_address must be a string")
        try:
            payload["ip_address"] = str(ipaddress.ip_address(ip_address.strip()))
        except ValueError as exc:
            raise DeviceProtocolError("ip_address is invalid") from exc
    return _message(
        device_id=device_id,
        transport=transport,
        topic="device.connected",
        kind=MessageKind.EVENT,
        payload=payload,
    )


def heartbeat_message(device_id: str, transport: str) -> Message:
    return _message(
        device_id=device_id,
        transport=transport,
        topic="device.heartbeat.received",
        kind=MessageKind.EVENT,
        payload=_base_payload(device_id, transport, None),
    )


def disconnected_message(device_id: str, transport: str, reason: str) -> Message:
    payload = _base_payload(device_id, transport, None)
    payload["reason"] = reason[:512]
    return _message(
        device_id=device_id,
        transport=transport,
        topic="device.disconnected",
        kind=MessageKind.EVENT,
        payload=payload,
    )


def _runtime_action(value: dict[str, Any]) -> tuple[Any, Any]:
    state = value.get("action_state")
    action_name = value.get("current_action")
    runtime = value.get("runtime")
    if isinstance(runtime, dict):
        otto = runtime.get("otto")
        if isinstance(otto, dict):
            action = otto.get("action")
            if isinstance(action, dict):
                state = action.get("state", state)
                action_name = action.get("name", action_name)
    return state, action_name


def _state(device_id: str, transport: str, value: dict[str, Any]) -> Message:
    action_state, current_action = _runtime_action(value)
    if action_state not in {"unknown", "idle", "moving"}:
        raise DeviceProtocolError("state must contain a known action state")
    if current_action is not None and (
        not isinstance(current_action, str) or not current_action.strip()
    ):
        raise DeviceProtocolError("current action must be a non-empty string or null")
    normalized_action = current_action.strip() if isinstance(current_action, str) else None
    if normalized_action is not None and len(normalized_action) > 80:
        raise DeviceProtocolError("current action exceeds 80 characters")
    external_id = optional_external_id(value)
    payload = _base_payload(device_id, transport, external_id)
    payload["action_state"] = cast(str, action_state)
    payload["current_action"] = normalized_action
    return _message(
        device_id=device_id,
        transport=transport,
        topic="device.state.received",
        kind=MessageKind.STATE,
        payload=payload,
        correlation_id=external_id,
    )


def _actions(device_id: str, transport: str, value: dict[str, Any]) -> Message:
    raw_actions = value.get("actions")
    if not isinstance(raw_actions, list):
        raise DeviceProtocolError("actions must be a list")
    if len(raw_actions) > MAX_DEVICE_ACTIONS:
        raise DeviceProtocolError(f"actions exceeds {MAX_DEVICE_ACTIONS} entries")
    actions: list[dict[str, JsonValue]] = []
    names: set[str] = set()
    for item in raw_actions:
        if isinstance(item, str):
            normalized: dict[str, JsonValue] = {"name": item.strip()}
        elif isinstance(item, dict):
            normalized = cast(dict[str, JsonValue], dict(item))
        else:
            raise DeviceProtocolError("each action must be a string or object")
        name = normalized.get("name")
        if not isinstance(name, str) or not name.strip():
            raise DeviceProtocolError("each action must have a non-empty name")
        name = name.strip()
        if len(name) > 80:
            raise DeviceProtocolError("action name exceeds 80 characters")
        if name in names:
            raise DeviceProtocolError(f"duplicate action name: {name}")
        names.add(name)
        normalized["name"] = name
        actions.append(normalized)
    external_id = optional_external_id(value)
    payload = _base_payload(device_id, transport, external_id)
    payload["actions"] = cast(list[JsonValue], actions)
    return _message(
        device_id=device_id,
        transport=transport,
        topic="device.actions.catalog.received",
        kind=MessageKind.EVENT,
        payload=payload,
        correlation_id=external_id,
    )


def _ack(
    device_id: str,
    transport: str,
    value: dict[str, Any],
    *,
    stop: bool,
) -> tuple[Message, ...]:
    external_id = required_string(value, "id", maximum=128)
    ok = value.get("ok")
    if not isinstance(ok, bool):
        raise DeviceProtocolError("ack ok must be a boolean")
    payload = _base_payload(device_id, transport, external_id)
    payload["accepted"] = ok
    payload["command_type"] = "stop" if stop else "action"
    action = value.get("action")
    if action is not None:
        if not isinstance(action, str) or not action.strip() or len(action.strip()) > 80:
            raise DeviceProtocolError("ack action must be a valid action name")
        payload["action"] = action.strip()
    error = value.get("error")
    if error is not None:
        if not isinstance(error, str) or len(error) > 512:
            raise DeviceProtocolError("ack error must be a bounded string")
        payload["error"] = error
    if stop:
        internal_topic = "robot.stop.accepted" if ok else "robot.stop.failed"
    else:
        internal_topic = "robot.action.accepted" if ok else "robot.action.failed"
    messages = [
        _message(
            device_id=device_id,
            transport=transport,
            topic=internal_topic,
            kind=MessageKind.RESULT,
            payload=payload,
            correlation_id=external_id,
        )
    ]
    action_state, _ = _runtime_action(value)
    if action_state is not None:
        messages.append(_state(device_id, transport, value))
    return tuple(messages)


def _error(device_id: str, transport: str, value: dict[str, Any]) -> Message:
    external_id = required_string(value, "id", maximum=128)
    error = required_string(value, "error", maximum=512)
    payload = _base_payload(device_id, transport, external_id)
    payload.update({"accepted": False, "error": error})
    return _message(
        device_id=device_id,
        transport=transport,
        topic="robot.action.failed",
        kind=MessageKind.RESULT,
        payload=payload,
        correlation_id=external_id,
    )


def translate_otto_value(
    device_id: str,
    value: dict[str, Any],
    *,
    transport: str,
    hello_protocol: str | None = None,
) -> tuple[Message, ...]:
    """Translate one authenticated Otto object into transport-neutral messages."""

    if transport not in SUPPORTED_DEVICE_TRANSPORTS:
        raise DeviceProtocolError("unsupported device transport")
    try:
        normalized_id = normalize_device_id(device_id)
    except ValueError as exc:
        raise DeviceProtocolError("invalid authenticated device identity") from exc
    validate_payload_identity(normalized_id, value)
    message_type = required_string(value, "type", maximum=64)
    if message_type == "hello":
        if hello_protocol is None:
            raise DeviceProtocolError("hello is not allowed on this protocol path")
        return (
            _hello(
                normalized_id,
                transport,
                value,
                expected_protocol=hello_protocol,
            ),
        )
    if message_type == "heartbeat":
        payload = _base_payload(normalized_id, transport, optional_external_id(value))
        return (
            _message(
                device_id=normalized_id,
                transport=transport,
                topic="device.heartbeat.received",
                kind=MessageKind.EVENT,
                payload=payload,
            ),
        )
    if message_type == "otto_state":
        return (_state(normalized_id, transport, value),)
    if message_type == "otto_actions":
        return (_actions(normalized_id, transport, value),)
    if message_type == "otto_action_ack":
        return _ack(normalized_id, transport, value, stop=False)
    if message_type == "otto_stop_ack":
        return _ack(normalized_id, transport, value, stop=True)
    if message_type == "error":
        return (_error(normalized_id, transport, value),)
    raise DeviceProtocolError(f"unsupported device message type: {message_type}")


def translate_otto_payload(
    device_id: str,
    payload: bytes,
    *,
    transport: str,
    hello_protocol: str | None = None,
    maximum_bytes: int = MAX_DEVICE_MESSAGE_BYTES,
) -> tuple[Message, ...]:
    return translate_otto_value(
        device_id,
        decode_device_json(payload, maximum_bytes=maximum_bytes),
        transport=transport,
        hello_protocol=hello_protocol,
    )


@dataclass(frozen=True, slots=True)
class EncodedOttoCommand:
    device_id: str
    payload: bytes
    external_id: str


def outbound_device_id(message: Message) -> str:
    device_id_value = message.payload.get("device_id")
    if not isinstance(device_id_value, str):
        raise DeviceProtocolError("outbound command requires device_id")
    try:
        device_id = normalize_device_id(device_id_value)
    except ValueError as exc:
        raise DeviceProtocolError("outbound command has invalid device_id") from exc
    if message.target != f"device:{device_id}":
        raise DeviceProtocolError("outbound command target does not match device_id")
    return device_id


def message_transport(message: Message) -> str:
    value = message.payload.get("transport")
    if not isinstance(value, str) or value not in SUPPORTED_DEVICE_TRANSPORTS:
        raise DeviceProtocolError("outbound command requires a supported transport")
    return value


def _outbound_command_id(message: Message) -> str:
    command_id = message.payload.get("command_id")
    if not isinstance(command_id, str) or not command_id.strip():
        raise DeviceProtocolError("outbound action or stop requires command_id")
    command_id = command_id.strip()
    if len(command_id) > 128:
        raise DeviceProtocolError("outbound command ID exceeds 128 characters")
    if message.correlation_id != command_id:
        raise DeviceProtocolError("outbound command correlation does not match command_id")
    return command_id


def _action_payload(message: Message, command_id: str) -> dict[str, JsonValue]:
    action = message.payload.get("action")
    if not isinstance(action, str) or not action.strip() or len(action.strip()) > 80:
        raise DeviceProtocolError("outbound action requires a valid action name")
    parameters = message.payload.get("parameters", {})
    if not isinstance(parameters, dict):
        raise DeviceProtocolError("outbound action parameters must be an object")
    if len(parameters) > MAX_ACTION_PARAMETERS:
        raise DeviceProtocolError(
            f"outbound action exceeds {MAX_ACTION_PARAMETERS} parameters"
        )
    external: dict[str, JsonValue] = {
        "type": "otto_action",
        "id": command_id,
        "action": action.strip(),
    }
    for name, value in parameters.items():
        normalized_name = name.strip() if isinstance(name, str) else ""
        if (
            not isinstance(name, str)
            or not normalized_name
            or len(normalized_name) > 64
            or normalized_name in _ACTION_RESERVED_FIELDS
            or normalized_name in external
        ):
            raise DeviceProtocolError("outbound action contains an invalid parameter name")
        external[normalized_name] = value
    return external


def encode_otto_command(message: Message, *, transport: str) -> EncodedOttoCommand:
    """Encode an allow-listed command only for its explicitly selected transport."""

    if message.topic not in OUTBOUND_COMMAND_TOPICS or message.kind is not MessageKind.COMMAND:
        raise DeviceProtocolError("unsupported outbound device command")
    if message_transport(message) != transport:
        raise DeviceProtocolError("outbound command transport does not match gateway")
    device_id = outbound_device_id(message)
    external_type = QUERY_TYPES.get(message.topic)
    if external_type is not None:
        external_id = message.message_id
        if len(external_id) > 128:
            raise DeviceProtocolError("outbound command ID exceeds 128 characters")
        external_payload: dict[str, JsonValue] = {
            "type": external_type,
            "id": external_id,
        }
    else:
        external_id = _outbound_command_id(message)
        external_payload = (
            _action_payload(message, external_id)
            if message.topic == ACTION_COMMAND_TOPIC
            else {"type": "stop", "id": external_id}
        )
    payload = json.dumps(
        external_payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) > MAX_DEVICE_MESSAGE_BYTES:
        raise DeviceProtocolError(
            f"outbound command exceeds {MAX_DEVICE_MESSAGE_BYTES} bytes"
        )
    return EncodedOttoCommand(
        device_id=device_id,
        payload=payload,
        external_id=external_id,
    )
