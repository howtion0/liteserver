from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from otto_master.messages import Message, MessageKind
from otto_master.storage.database import Database
from otto_master.storage.migrations import SCHEMA_VERSION


def make_message(index: int, *, payload: dict[str, object] | None = None) -> Message:
    return Message.create(
        topic="device.connected" if index % 2 == 0 else "device.heartbeat.received",
        kind=MessageKind.EVENT,
        source="test",
        target=f"device:test-{index}",
        message_id=f"message-{index}",
        payload=payload or {"index": index},  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_first_open_creates_database_and_schema(tmp_path: Path) -> None:
    path = tmp_path / "data" / "otto.db"
    database = await Database.open(path)

    assert path.is_file()
    assert await database.schema_version() == SCHEMA_VERSION
    assert {
        "schema_migrations",
        "devices",
        "device_groups",
        "device_group_members",
        "messages",
        "commands",
        "command_results",
        "settings",
    }.issubset(await database.table_names())
    await database.close()
    assert database.is_open is False


@pytest.mark.asyncio
async def test_migrations_are_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "otto.db"
    first = await Database.open(path)
    tables = await first.table_names()
    await first.close()

    second = await Database.open(path)
    assert await second.schema_version() == SCHEMA_VERSION
    assert await second.table_names() == tables
    await second.close()


@pytest.mark.asyncio
async def test_concurrent_message_writes_are_serialized(tmp_path: Path) -> None:
    database = await Database.open(tmp_path / "otto.db")

    await asyncio.gather(*(database.log_message(make_message(index)) for index in range(100)))

    assert await database.message_count() == 100
    await database.close()


@pytest.mark.asyncio
async def test_message_log_redacts_sensitive_fields(tmp_path: Path) -> None:
    database = await Database.open(tmp_path / "otto.db")
    message = make_message(
        1,
        payload={
            "api_key": "do-not-store",
            "nested": {"password": "also-do-not-store"},
            "raw_audio": "base64-or-bytes-reference",
            "safe": "keep-me",
        },
    )

    await database.log_message(message)
    stored = await database.fetch_message(message.message_id)
    assert stored is not None
    payload = stored["payload"]
    assert payload["api_key"] == "[REDACTED]"
    assert payload["nested"]["password"] == "[REDACTED]"
    assert payload["raw_audio"] == "[REDACTED]"
    assert payload["safe"] == "keep-me"
    assert "do-not-store" not in json.dumps(payload)
    await database.close()
