"""Persistent command lifecycle contracts and repository."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..messages import JsonValue
from ..storage.database import Database


class CommandError(RuntimeError):
    """Base class for command lifecycle failures."""


class DispatchRequestError(CommandError):
    """A safe, stable command rejection suitable for an API boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class CommandNotFoundError(CommandError):
    """Raised when a command ID does not exist."""


class DuplicateCommandError(CommandError):
    """Raised when an existing command ID is reused with a different request."""


class CommandTransitionError(CommandError):
    """Raised when a command attempts an illegal lifecycle transition."""


class CommandType(str, Enum):
    ACTION = "action"
    STOP = "stop"


class CommandStatus(str, Enum):
    REQUESTED = "requested"
    PUBLISHED = "published"
    ACCEPTED = "accepted"
    MOVING = "moving"
    COMPLETED = "completed"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    DISCONNECTED = "disconnected"
    FAILED = "failed"


TERMINAL_STATUSES = frozenset(
    {
        CommandStatus.COMPLETED,
        CommandStatus.REJECTED,
        CommandStatus.TIMEOUT,
        CommandStatus.DISCONNECTED,
        CommandStatus.FAILED,
    }
)
ACTIVE_STATUSES = tuple(status for status in CommandStatus if status not in TERMINAL_STATUSES)

_ALLOWED_TRANSITIONS: dict[CommandStatus, frozenset[CommandStatus]] = {
    CommandStatus.REQUESTED: frozenset(
        {
            CommandStatus.PUBLISHED,
            CommandStatus.REJECTED,
            CommandStatus.TIMEOUT,
            CommandStatus.DISCONNECTED,
            CommandStatus.FAILED,
        }
    ),
    CommandStatus.PUBLISHED: frozenset(
        {
            CommandStatus.ACCEPTED,
            CommandStatus.REJECTED,
            CommandStatus.TIMEOUT,
            CommandStatus.DISCONNECTED,
            CommandStatus.FAILED,
        }
    ),
    CommandStatus.ACCEPTED: frozenset(
        {
            CommandStatus.MOVING,
            CommandStatus.COMPLETED,
            CommandStatus.TIMEOUT,
            CommandStatus.DISCONNECTED,
            CommandStatus.FAILED,
        }
    ),
    CommandStatus.MOVING: frozenset(
        {
            CommandStatus.COMPLETED,
            CommandStatus.TIMEOUT,
            CommandStatus.DISCONNECTED,
            CommandStatus.FAILED,
        }
    ),
    **{status: frozenset() for status in TERMINAL_STATUSES},
}


@dataclass(frozen=True, slots=True)
class CommandSpec:
    command_id: str
    correlation_id: str | None
    command_type: CommandType
    device_id: str
    target: str
    source: str
    transport: str = "mqtt"
    action: str | None = None
    parameters: dict[str, JsonValue] | None = None
    confirmation: bool = False

    @property
    def topic(self) -> str:
        if self.command_type is CommandType.ACTION:
            return "robot.action.requested"
        return "robot.stop.requested"

    def payload(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "command_type": self.command_type.value,
            "device_id": self.device_id,
            "source": self.source,
            "transport": self.transport,
            "confirmation": self.confirmation,
        }
        if self.action is not None:
            result["action"] = self.action
        if self.parameters is not None:
            result["parameters"] = dict(self.parameters)
        return result


def _status(value: Any) -> CommandStatus:
    try:
        return CommandStatus(str(value))
    except ValueError as exc:
        raise CommandTransitionError(f"unknown persisted command status: {value}") from exc


class CommandRepository:
    """Serialize command writes and expose complete transition history."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self._lock = asyncio.Lock()

    async def create(self, spec: CommandSpec) -> tuple[dict[str, Any], bool]:
        payload = spec.payload()
        async with self._lock:
            inserted = await self.database.create_command_record(
                command_id=spec.command_id,
                correlation_id=spec.correlation_id,
                topic=spec.topic,
                target=spec.target,
                status=CommandStatus.REQUESTED.value,
                payload=payload,
                result_payload={"command_type": spec.command_type.value},
            )
            existing = await self.database.fetch_command_record(spec.command_id)
            if existing is None:
                raise CommandError("command was not readable after creation")
            if not inserted and not self._same_request(existing, spec, payload):
                raise DuplicateCommandError(
                    f"command ID {spec.command_id} already belongs to another request"
                )
            return await self._with_history(existing), inserted

    async def get(self, command_id: str) -> dict[str, Any] | None:
        record = await self.database.fetch_command_record(command_id)
        if record is None:
            return None
        return await self._with_history(record)

    async def transition(
        self,
        command_id: str,
        status: CommandStatus,
        *,
        error: str | None = None,
        payload: dict[str, JsonValue] | None = None,
    ) -> dict[str, Any]:
        if error is not None and len(error) > 512:
            error = error[:512]
        async with self._lock:
            existing = await self.database.fetch_command_record(command_id)
            if existing is None:
                raise CommandNotFoundError(command_id)
            current = _status(existing["status"])
            if current is status:
                return await self._with_history(existing)
            if status not in _ALLOWED_TRANSITIONS[current]:
                raise CommandTransitionError(
                    f"invalid command transition: {current.value} -> {status.value}"
                )
            changed = await self.database.transition_command_record(
                command_id=command_id,
                expected_status=current.value,
                status=status.value,
                error=error,
                payload=payload,
            )
            if not changed:
                raise CommandTransitionError(
                    f"command {command_id} changed concurrently from {current.value}"
                )
            updated = await self.database.fetch_command_record(command_id)
            if updated is None:
                raise CommandError("command disappeared after transition")
            return await self._with_history(updated)

    async def recover_incomplete(self) -> int:
        """Fail closed after restart without replaying uncertain robot movement."""

        active = await self.database.list_command_records(
            statuses=[status.value for status in ACTIVE_STATUSES]
        )
        recovered = 0
        for item in active:
            try:
                await self.transition(
                    str(item["command_id"]),
                    CommandStatus.DISCONNECTED,
                    error="runtime_restarted_before_terminal_state",
                    payload={"replayed": False},
                )
            except CommandTransitionError:
                continue
            recovered += 1
        return recovered

    async def _with_history(self, record: dict[str, Any]) -> dict[str, Any]:
        result = dict(record)
        result["history"] = await self.database.fetch_command_results(
            str(record["command_id"])
        )
        result["terminal"] = _status(record["status"]) in TERMINAL_STATUSES
        return result

    @staticmethod
    def _same_request(
        existing: dict[str, Any],
        spec: CommandSpec,
        payload: dict[str, JsonValue],
    ) -> bool:
        return (
            existing.get("correlation_id") == spec.correlation_id
            and existing.get("topic") == spec.topic
            and existing.get("target") == spec.target
            and existing.get("payload") == payload
        )
