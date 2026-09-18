from __future__ import annotations

import json

import pytest

from otto_master.gateways.device_mqtt import (
    MAX_DEVICE_MESSAGE_BYTES,
    DeviceMessageError,
    encode_device_command,
    translate_device_message,
)
from otto_master.messages import Message, MessageKind

TOPIC = "otto/v1/devices/aabbccddeeff/up"


def _translate(value: dict[str, object]) -> tuple[Message, ...]:
    return translate_device_message(TOPIC, json.dumps(value).encode())


def test_translates_read_only_device_protocol_messages() -> None:
    hello = _translate(
        {
            "type": "hello",
            "protocol": "otto-mqtt/1",
            "name": "EVA1",
            "mac": "AA:BB:CC:DD:EE:FF",
            "firmware_version": "2.0.5-test",
            "ip_address": "192.0.2.10",
            "capabilities": {"actions": True, "state": True},
        }
    )[0]
    assert hello.topic == "device.connected"
    assert hello.target == "device:aabbccddeeff"
    assert hello.payload["device_id"] == "aabbccddeeff"
    assert hello.payload["name"] == "EVA1"

    heartbeat = _translate({"type": "heartbeat", "id": "beat-1"})[0]
    assert heartbeat.topic == "device.heartbeat.received"
    assert heartbeat.payload["external_message_id"] == "beat-1"

    state = _translate(
        {
            "type": "otto_state",
            "runtime": {"otto": {"action": {"state": "moving", "name": "swing"}}},
        }
    )[0]
    assert state.topic == "device.state.received"
    assert state.payload["action_state"] == "moving"
    assert state.payload["current_action"] == "swing"

    actions = _translate(
        {
            "type": "otto_actions",
            "id": "catalog-1",
            "actions": ["walk", {"name": "swing", "parameters": {"steps": "integer"}}],
        }
    )[0]
    assert actions.topic == "device.actions.catalog.received"
    assert actions.payload["actions"] == [
        {"name": "walk"},
        {"name": "swing", "parameters": {"steps": "integer"}},
    ]


@pytest.mark.parametrize(
    ("external_type", "ok", "expected_topic"),
    [
        ("otto_action_ack", True, "robot.action.accepted"),
        ("otto_action_ack", False, "robot.action.failed"),
        ("otto_stop_ack", True, "robot.stop.accepted"),
        ("otto_stop_ack", False, "robot.stop.failed"),
    ],
)
def test_translates_ack_without_claiming_action_completion(
    external_type: str,
    ok: bool,
    expected_topic: str,
) -> None:
    message = _translate(
        {"type": external_type, "id": "cmd-1", "ok": ok, "action": "swing"}
    )[0]
    assert message.topic == expected_topic
    assert message.correlation_id == "cmd-1"
    assert message.payload["accepted"] is ok


def test_ack_runtime_state_is_a_separate_correlated_state_fact() -> None:
    messages = _translate(
        {
            "type": "otto_action_ack",
            "id": "cmd-1",
            "ok": True,
            "action": "swing",
            "runtime": {"otto": {"action": {"state": "moving", "name": "swing"}}},
        }
    )

    assert [message.topic for message in messages] == [
        "robot.action.accepted",
        "device.state.received",
    ]
    assert all(message.correlation_id == "cmd-1" for message in messages)
    assert messages[1].payload["action_state"] == "moving"

    with pytest.raises(DeviceMessageError, match="known action state"):
        _translate(
            {
                "type": "otto_action_ack",
                "id": "cmd-2",
                "ok": True,
                "runtime": {
                    "otto": {"action": {"state": "teleporting", "name": "swing"}}
                },
            }
        )


@pytest.mark.parametrize(
    ("topic", "payload"),
    [
        (
            TOPIC,
            {
                "type": "hello",
                "protocol": "otto-mqtt/1",
                "name": "EVA1",
                "mac": "aa:bb:cc:dd:ee:00",
                "firmware_version": "2.0.5-test",
            },
        ),
        (TOPIC, {"type": "unknown"}),
        (TOPIC, {"type": "otto_actions", "actions": [{"missing": "name"}]}),
        (TOPIC, {"type": "otto_state", "action_state": "teleporting"}),
        ("otto/v1/devices/EVA1/up", {"type": "heartbeat"}),
    ],
)
def test_rejects_identity_conflicts_unknown_types_and_invalid_shapes(
    topic: str,
    payload: dict[str, object],
) -> None:
    with pytest.raises(DeviceMessageError):
        translate_device_message(topic, json.dumps(payload).encode())


def test_rejects_duplicate_fields_non_utf8_and_oversized_payloads() -> None:
    with pytest.raises(DeviceMessageError, match="duplicate JSON field"):
        translate_device_message(TOPIC, b'{"type":"heartbeat","type":"hello"}')
    with pytest.raises(DeviceMessageError, match="UTF-8"):
        translate_device_message(TOPIC, b"\xff")
    with pytest.raises(DeviceMessageError, match="exceeds"):
        translate_device_message(TOPIC, b"x" * (MAX_DEVICE_MESSAGE_BYTES + 1))
    deeply_nested: object = True
    for _ in range(18):
        deeply_nested = {"nested": deeply_nested}
    with pytest.raises(DeviceMessageError, match="depth"):
        _translate(
            {
                "type": "hello",
                "protocol": "otto-mqtt/1",
                "name": "EVA1",
                "mac": "aa:bb:cc:dd:ee:ff",
                "firmware_version": "2.0.5-test",
                "capabilities": deeply_nested,
            }
        )


def test_encodes_only_allowlisted_read_only_queries_to_exact_down_topic() -> None:
    command = Message.create(
        topic="device.state.query.requested",
        kind=MessageKind.COMMAND,
        source="device_verifier",
        target="device:aabbccddeeff",
        message_id="query-1",
        payload={"device_id": "aabbccddeeff", "transport": "mqtt"},
    )

    encoded = encode_device_command(command)

    assert encoded.device_id == "aabbccddeeff"
    assert encoded.topic == "otto/v1/devices/aabbccddeeff/down"
    assert json.loads(encoded.payload) == {"type": "otto_query", "id": "query-1"}

    actions = Message.create(
        topic="device.actions.query.requested",
        kind=MessageKind.COMMAND,
        source="device_verifier",
        target="device:aabbccddeeff",
        message_id="actions-1",
        payload={"device_id": "aabbccddeeff"},
    )
    assert json.loads(encode_device_command(actions).payload)["type"] == "otto_actions"


def test_encodes_dispatcher_action_and_stop_with_command_id() -> None:
    action = Message.create(
        topic="device.action.execute.requested",
        kind=MessageKind.COMMAND,
        source="dispatcher",
        target="device:aabbccddeeff",
        correlation_id="command-1",
        payload={
            "device_id": "aabbccddeeff",
            "command_id": "command-1",
            "action": "swing",
            "parameters": {"steps": 2, "speed": 1000},
        },
    )
    stop = Message.create(
        topic="device.stop.execute.requested",
        kind=MessageKind.COMMAND,
        source="dispatcher",
        target="device:aabbccddeeff",
        correlation_id="stop-1",
        payload={"device_id": "aabbccddeeff", "command_id": "stop-1"},
    )

    encoded_action = encode_device_command(action)
    encoded_stop = encode_device_command(stop)

    assert encoded_action.topic == "otto/v1/devices/aabbccddeeff/down"
    assert encoded_action.external_id == "command-1"
    assert json.loads(encoded_action.payload) == {
        "type": "otto_action",
        "id": "command-1",
        "action": "swing",
        "steps": 2,
        "speed": 1000,
    }
    assert json.loads(encoded_stop.payload) == {"type": "stop", "id": "stop-1"}


def test_action_encoder_rejects_reserved_or_normalized_duplicate_parameters() -> None:
    def action(parameters: dict[str, object]) -> Message:
        return Message.create(
            topic="device.action.execute.requested",
            kind=MessageKind.COMMAND,
            source="dispatcher",
            target="device:aabbccddeeff",
            correlation_id="command-1",
            payload={
                "device_id": "aabbccddeeff",
                "command_id": "command-1",
                "action": "swing",
                "parameters": parameters,
            },
        )

    with pytest.raises(DeviceMessageError, match="invalid parameter name"):
        encode_device_command(action({" action ": "walk"}))
    with pytest.raises(DeviceMessageError, match="invalid parameter name"):
        encode_device_command(action({"steps": 1, " steps ": 2}))


@pytest.mark.parametrize(
    "message",
    [
        Message.create(
            topic="robot.action.requested",
            kind=MessageKind.COMMAND,
            source="test",
            target="device:aabbccddeeff",
            payload={"device_id": "aabbccddeeff", "action": "walk"},
        ),
        Message.create(
            topic="device.state.query.requested",
            kind=MessageKind.COMMAND,
            source="test",
            target="device:aabbccddee00",
            payload={"device_id": "aabbccddeeff"},
        ),
        Message.create(
            topic="device.state.query.requested",
            kind=MessageKind.EVENT,
            source="test",
            target="device:aabbccddeeff",
            payload={"device_id": "aabbccddeeff"},
        ),
    ],
)
def test_outbound_encoder_rejects_unowned_actions_cross_target_and_non_commands(
    message: Message,
) -> None:
    with pytest.raises(DeviceMessageError):
        encode_device_command(message)
