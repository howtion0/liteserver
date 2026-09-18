from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from otto_master.dispatch.commands import (
    CommandRepository,
    CommandSpec,
    CommandStatus,
    CommandTransitionError,
    CommandType,
    DuplicateCommandError,
)
from otto_master.storage.database import Database


def _action_spec(
    command_id: str = "command-1",
    *,
    action: str = "swing",
) -> CommandSpec:
    return CommandSpec(
        command_id=command_id,
        correlation_id="request-1",
        command_type=CommandType.ACTION,
        device_id="aabbccddeeff",
        target="device:aabbccddeeff",
        source="test",
        action=action,
        parameters={"steps": 2},
        confirmation=True,
    )


@pytest.mark.asyncio
async def test_repository_is_idempotent_and_rejects_conflicting_duplicate_ids(
    tmp_path: Path,
) -> None:
    database = await Database.open(tmp_path / "otto.db")
    repository = CommandRepository(database)
    try:
        created, inserted = await repository.create(_action_spec())
        duplicate, duplicate_inserted = await repository.create(_action_spec())

        assert inserted is True
        assert duplicate_inserted is False
        assert created["command_id"] == duplicate["command_id"]
        assert created["status"] == "requested"
        assert len(duplicate["history"]) == 1

        with pytest.raises(DuplicateCommandError):
            await repository.create(_action_spec(action="walk"))
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_repository_enforces_lifecycle_and_persists_history(tmp_path: Path) -> None:
    path = tmp_path / "otto.db"
    database = await Database.open(path)
    repository = CommandRepository(database)
    try:
        await repository.create(_action_spec())
        with pytest.raises(CommandTransitionError):
            await repository.transition("command-1", CommandStatus.COMPLETED)

        for status in (
            CommandStatus.PUBLISHED,
            CommandStatus.ACCEPTED,
            CommandStatus.MOVING,
            CommandStatus.COMPLETED,
        ):
            result = await repository.transition(
                "command-1",
                status,
                payload={"evidence": status.value},
            )
        assert result["terminal"] is True
        assert [item["status"] for item in result["history"]] == [
            "requested",
            "published",
            "accepted",
            "moving",
            "completed",
        ]
        with pytest.raises(CommandTransitionError):
            await repository.transition("command-1", CommandStatus.FAILED)
    finally:
        await database.close()

    reopened = await Database.open(path)
    try:
        persisted = await CommandRepository(reopened).get("command-1")
        assert persisted is not None
        assert persisted["status"] == "completed"
        assert len(persisted["history"]) == 5
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_repository_recovers_incomplete_without_replaying(tmp_path: Path) -> None:
    path = tmp_path / "otto.db"
    database = await Database.open(path)
    repository = CommandRepository(database)
    await repository.create(_action_spec("active-command"))
    await repository.transition("active-command", CommandStatus.PUBLISHED)
    await repository.create(_action_spec("completed-command"))
    await repository.transition("completed-command", CommandStatus.PUBLISHED)
    await repository.transition("completed-command", CommandStatus.ACCEPTED)
    await repository.transition("completed-command", CommandStatus.MOVING)
    await repository.transition("completed-command", CommandStatus.COMPLETED)
    await database.close()

    reopened = await Database.open(path)
    recovered_repository = CommandRepository(reopened)
    try:
        assert await recovered_repository.recover_incomplete() == 1
        active = await recovered_repository.get("active-command")
        completed = await recovered_repository.get("completed-command")
        assert active is not None and active["status"] == "disconnected"
        assert active["history"][-1]["payload"] == {"replayed": False}
        assert completed is not None and completed["status"] == "completed"
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_concurrent_identical_creates_have_one_initial_history_row(
    tmp_path: Path,
) -> None:
    database = await Database.open(tmp_path / "otto.db")
    repository = CommandRepository(database)
    try:
        results = await asyncio.gather(
            *(repository.create(_action_spec("same-command")) for _ in range(20))
        )
        assert sum(1 for _, inserted in results if inserted) == 1
        stored = await repository.get("same-command")
        assert stored is not None
        assert len(stored["history"]) == 1
    finally:
        await database.close()
