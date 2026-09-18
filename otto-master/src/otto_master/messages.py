"""Versioned, transport-independent internal messages."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, TypeAlias, cast
from uuid import uuid4

JsonPrimitive: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


class MessageValidationError(ValueError):
    """Raised when an internal message violates the message contract."""


class MessageKind(str, Enum):
    COMMAND = "command"
    EVENT = "event"
    STATE = "state"
    RESULT = "result"


_TOPIC_PATTERN = re.compile(r"^[a-z0-9]+(?:\.[a-z0-9_]+)*$")


def validate_topic(topic: str) -> str:
    if not isinstance(topic, str) or not _TOPIC_PATTERN.fullmatch(topic):
        raise MessageValidationError(
            "topic must contain lowercase letters, numbers, underscores and dot separators"
        )
    return topic


def _copy_json(value: Any, path: str) -> JsonValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MessageValidationError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        copied: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise MessageValidationError(f"{path} contains a non-string object key")
            copied[key] = _copy_json(item, f"{path}.{key}")
        return copied
    if isinstance(value, list):
        return [_copy_json(item, f"{path}[]") for item in value]
    raise MessageValidationError(f"{path} contains unsupported value {type(value).__name__}")


def _created_at(value: datetime | str) -> datetime:
    if isinstance(value, str):
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            value = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise MessageValidationError("created_at must be a valid ISO-8601 timestamp") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise MessageValidationError("created_at must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class Message:
    """Immutable internal message envelope."""

    version: int
    message_id: str
    correlation_id: str | None
    topic: str
    kind: MessageKind
    source: str
    target: str
    created_at: datetime
    payload: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        if isinstance(self.kind, str):
            try:
                object.__setattr__(self, "kind", MessageKind(self.kind))
            except ValueError as exc:
                raise MessageValidationError(f"unknown message kind: {self.kind}") from exc
        if not isinstance(self.kind, MessageKind):
            raise MessageValidationError("kind must be one of command, event, state or result")
        if not isinstance(self.version, int) or isinstance(self.version, bool) or self.version < 1:
            raise MessageValidationError("version must be a positive integer")
        for field_name, value in (
            ("message_id", self.message_id),
            ("source", self.source),
            ("target", self.target),
        ):
            if not isinstance(value, str) or not value.strip():
                raise MessageValidationError(f"{field_name} must be a non-empty string")
        if self.correlation_id is not None and (
            not isinstance(self.correlation_id, str) or not self.correlation_id.strip()
        ):
            raise MessageValidationError("correlation_id must be null or a non-empty string")
        validate_topic(self.topic)
        object.__setattr__(self, "created_at", _created_at(self.created_at))
        if not isinstance(self.payload, Mapping):
            raise MessageValidationError("payload must be an object")
        payload = _copy_json(self.payload, "payload")
        if not isinstance(payload, dict):
            raise MessageValidationError("payload must be an object")
        object.__setattr__(self, "payload", cast(Mapping[str, JsonValue], payload))
        if self.target == "cluster:all" and self.payload.get("target_scope") != "cluster":
            raise MessageValidationError("cluster broadcasts require payload.target_scope=cluster")
        if self.payload.get("target_scope") == "cluster" and self.target != "cluster:all":
            raise MessageValidationError("target_scope=cluster requires target=cluster:all")

    @classmethod
    def create(
        cls,
        *,
        topic: str,
        kind: MessageKind,
        source: str,
        target: str,
        payload: Mapping[str, JsonValue] | None = None,
        correlation_id: str | None = None,
        version: int = 1,
        message_id: str | None = None,
        created_at: datetime | None = None,
    ) -> Message:
        return cls(
            version=version,
            message_id=message_id or str(uuid4()),
            correlation_id=correlation_id,
            topic=topic,
            kind=kind,
            source=source,
            target=target,
            created_at=created_at or datetime.now(UTC),
            payload=payload or {},
        )

    def to_dict(self) -> JsonObject:
        """Return a JSON-compatible copy suitable for logging or transport."""

        payload = _copy_json(self.payload, "payload")
        if not isinstance(payload, dict):
            raise MessageValidationError("payload must be an object")
        return {
            "version": self.version,
            "message_id": self.message_id,
            "correlation_id": self.correlation_id,
            "topic": self.topic,
            "kind": self.kind.value,
            "source": self.source,
            "target": self.target,
            "created_at": self.created_at.isoformat().replace("+00:00", "Z"),
            "payload": payload,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Message:
        if not isinstance(value, Mapping):
            raise MessageValidationError("message must be an object")
        required = (
            "version",
            "message_id",
            "correlation_id",
            "topic",
            "kind",
            "source",
            "target",
            "created_at",
            "payload",
        )
        missing = [name for name in required if name not in value]
        if missing:
            raise MessageValidationError(f"message is missing fields: {', '.join(missing)}")
        created_at = value["created_at"]
        if not isinstance(created_at, (datetime, str)):
            raise MessageValidationError("created_at must be a datetime or ISO-8601 string")
        kind = value["kind"]
        if not isinstance(kind, (MessageKind, str)):
            raise MessageValidationError("kind must be a string")
        if isinstance(kind, str):
            try:
                kind = MessageKind(kind)
            except ValueError as exc:
                raise MessageValidationError(f"unknown message kind: {kind}") from exc
        payload = value["payload"]
        if not isinstance(payload, Mapping):
            raise MessageValidationError("payload must be an object")
        return cls(
            version=cast(int, value["version"]),
            message_id=cast(str, value["message_id"]),
            correlation_id=cast(str | None, value["correlation_id"]),
            topic=cast(str, value["topic"]),
            kind=kind,
            source=cast(str, value["source"]),
            target=cast(str, value["target"]),
            created_at=_created_at(created_at),
            payload=cast(Mapping[str, JsonValue], payload),
        )
