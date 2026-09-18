from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from otto_master.messages import Message, MessageKind
from otto_master.storage.database import Database
from otto_master.storage.migrations import MIGRATIONS, SCHEMA_VERSION


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
        "device_actions",
    }.issubset(await database.table_names())
    await database.close()


@pytest.mark.asyncio
async def test_device_snapshot_and_action_catalog_round_trip(tmp_path: Path) -> None:
    database = await Database.open(tmp_path / "otto.db")
    await database.upsert_device(
        device_id="aabbccddeeff",
        name="EVA1",
        mac="aabbccddeeff",
        transport="mqtt",
        status="online",
        capabilities={"actions": True},
        ip_address="192.0.2.10",
        firmware_version="2.0.5-test",
        last_hello_at="2026-09-18T08:00:00Z",
        last_heartbeat_at="2026-09-18T08:00:05Z",
        action_state="idle",
        current_action=None,
        enabled=True,
        last_error=None,
        session_generation=1,
    )
    await database.save_device_actions(
        "aabbccddeeff",
        [{"name": "walk"}, {"name": "swing", "parameters": {"steps": "integer"}}],
    )

    device = await database.fetch_device("aabbccddeeff")
    assert device is not None
    assert device["capabilities"] == {"actions": True}
    assert device["enabled"] is True
    assert [item["name"] for item in await database.fetch_device_actions("aabbccddeeff")] == [
        "swing",
        "walk",
    ]
    assert await database.mark_active_devices_offline() == 1
    downgraded = await database.fetch_device("aabbccddeeff")
    assert downgraded is not None
    assert downgraded["status"] == "offline"
    assert downgraded["action_state"] == "unknown"
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
async def test_existing_phase2_database_migrates_from_v1_to_v2(tmp_path: Path) -> None:
    path = tmp_path / "otto.db"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(MIGRATIONS[0].sql)
        connection.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (1, ?)",
            ("2026-09-18T08:00:00Z",),
        )
        connection.execute(
            """
            INSERT INTO devices (
                device_id, name, transport, status, capabilities_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "aabbccddeeff",
                "EVA1",
                "mqtt",
                "online",
                '{"actions":true}',
                "2026-09-18T08:00:00Z",
                "2026-09-18T08:00:00Z",
            ),
        )
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
    finally:
        connection.close()

    database = await Database.open(path)
    try:
        assert await database.schema_version() == 2
        migrated = await database.fetch_device("aabbccddeeff")
        assert migrated is not None
        assert migrated["mac"] is None
        assert migrated["enabled"] is True
        assert migrated["action_state"] == "unknown"
        assert "device_actions" in await database.table_names()
    finally:
        await database.close()


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
