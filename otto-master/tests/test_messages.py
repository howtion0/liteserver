from __future__ import annotations

from datetime import UTC, datetime

import pytest

from otto_master.messages import Message, MessageKind, MessageValidationError


def test_message_round_trip_is_json_compatible() -> None:
    created_at = datetime(2026, 9, 18, 8, 0, tzinfo=UTC)
    message = Message.create(
        topic="robot.action.requested",
        kind=MessageKind.COMMAND,
        source="webui",
        target="device:aabbccddeeff",
        correlation_id="request-1",
        message_id="message-1",
        created_at=created_at,
        payload={"action": "walk", "steps": 2, "flags": [True, None]},
    )

    restored = Message.from_dict(message.to_dict())

    assert restored == message
    assert restored.to_json().startswith('{"version":1')
    assert restored.created_at.tzinfo is not None


def test_cluster_broadcast_requires_explicit_scope() -> None:
    with pytest.raises(MessageValidationError, match="cluster broadcasts"):
        Message.create(
            topic="cluster.broadcast.requested",
            kind=MessageKind.COMMAND,
            source="webui",
            target="cluster:all",
        )

    message = Message.create(
        topic="cluster.broadcast.requested",
        kind=MessageKind.COMMAND,
        source="webui",
        target="cluster:all",
        payload={"target_scope": "cluster"},
    )
    assert message.payload["target_scope"] == "cluster"


def test_message_rejects_invalid_topic_and_non_json_payload() -> None:
    with pytest.raises(MessageValidationError, match="topic"):
        Message.create(
            topic="Device/Connected",
            kind=MessageKind.EVENT,
            source="test",
            target="device:test",
        )

    with pytest.raises(MessageValidationError, match="unsupported"):
        Message.create(
            topic="device.connected",
            kind=MessageKind.EVENT,
            source="test",
            target="device:test",
            payload={"raw": b"audio"},  # type: ignore[dict-item]
        )

    with pytest.raises(MessageValidationError, match="kind"):
        Message(
            version=1,
            message_id="message-1",
            correlation_id=None,
            topic="device.connected",
            kind=42,  # type: ignore[arg-type]
            source="test",
            target="device:test",
            created_at=datetime.now(UTC),
            payload={},
        )
