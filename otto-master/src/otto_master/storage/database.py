"""Single-entry SQLite persistence with serialized writes and redaction."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosqlite

from ..messages import JsonValue, Message
from .migrations import apply_migrations, current_schema_version


class DatabaseError(RuntimeError):
    """Raised when the SQLite storage lifecycle or operation is invalid."""


_REDACTED = "[REDACTED]"
MAX_PERSISTED_PAYLOAD_BYTES = 256 * 1024
_SENSITIVE_EXACT = {
    "accesskey",
    "apikey",
    "authorization",
    "credential",
    "password",
    "privatekey",
    "rawaudio",
    "secret",
    "token",
    "audio",
    "audiobytes",
    "audiodata",
    "opus",
    "pcm",
    "buffer",
}
_SENSITIVE_SUFFIXES = ("token", "secret", "password", "apikey", "credential")


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return normalized in _SENSITIVE_EXACT or normalized.endswith(_SENSITIVE_SUFFIXES)


def _sanitize(value: JsonValue, key: str | None = None) -> JsonValue:
    if key is not None and _is_sensitive_key(key):
        return _REDACTED
    if isinstance(value, dict):
        return {child_key: _sanitize(child, child_key) for child_key, child in value.items()}
    if isinstance(value, list):
        return [_sanitize(child) for child in value]
    return value


def sanitize_payload(payload: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Return a JSON-compatible payload without secrets or raw audio fields."""

    sanitized = _sanitize(dict(payload))
    if not isinstance(sanitized, dict):
        raise DatabaseError("sanitized message payload is not an object")
    return sanitized


def _json(value: JsonValue) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_PERSISTED_PAYLOAD_BYTES:
        raise DatabaseError(
            f"persisted JSON payload exceeds {MAX_PERSISTED_PAYLOAD_BYTES} bytes"
        )
    return encoded


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class Database:
    """An aiosqlite database with one connection and a single write gate."""

    def __init__(
        self,
        path: Path,
        *,
        wal: bool = True,
        logger: logging.Logger | None = None,
    ) -> None:
        self.path = path
        self.wal = wal
        self._connection: aiosqlite.Connection | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._logger = logger or logging.getLogger("otto_master.storage")

    @classmethod
    async def open(
        cls,
        path: str | Path,
        *,
        wal: bool = True,
        logger: logging.Logger | None = None,
    ) -> Database:
        database = cls(Path(path), wal=wal, logger=logger)
        await database.start()
        return database

    @property
    def is_open(self) -> bool:
        return self._connection is not None

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._connection is not None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = await aiosqlite.connect(self.path)
            connection.row_factory = aiosqlite.Row
            try:
                await connection.execute("PRAGMA foreign_keys = ON")
                await connection.execute("PRAGMA busy_timeout = 5000")
                if self.wal:
                    await connection.execute("PRAGMA journal_mode = WAL")
                await apply_migrations(connection)
            except Exception:
                await connection.close()
                raise
            self._connection = connection
            self._logger.info(
                "database_started",
                extra={"event": "database_started", "path": str(self.path)},
            )

    async def close(self) -> None:
        async with self._lifecycle_lock:
            connection = self._connection
            if connection is None:
                return
            async with self._operation_lock:
                await connection.commit()
                await connection.close()
                self._connection = None
            self._logger.info(
                "database_closed",
                extra={"event": "database_closed", "path": str(self.path)},
            )

    async def schema_version(self) -> int:
        async with self._operation_lock:
            return await current_schema_version(self._require_connection())

    async def table_names(self) -> set[str]:
        async with self._operation_lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()
            return {str(row[0]) for row in rows}

    async def log_message(self, message: Message) -> None:
        payload_json = _json(sanitize_payload(message.payload))
        recorded_at = _now()
        async with self._operation_lock:
            connection = self._require_connection()
            await connection.execute(
                """
                INSERT OR IGNORE INTO messages (
                    message_id, version, correlation_id, topic, kind, source, target,
                    created_at, payload_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.message_id,
                    message.version,
                    message.correlation_id,
                    message.topic,
                    message.kind.value,
                    message.source,
                    message.target,
                    message.created_at.isoformat().replace("+00:00", "Z"),
                    payload_json,
                    recorded_at,
                ),
            )
            await connection.commit()

    async def message_count(self) -> int:
        async with self._operation_lock:
            connection = self._require_connection()
            cursor = await connection.execute("SELECT COUNT(*) FROM messages")
            try:
                row = await cursor.fetchone()
            finally:
                await cursor.close()
            if row is None:
                return 0
            return int(row[0])

    async def fetch_message(self, message_id: str) -> dict[str, Any] | None:
        async with self._operation_lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                "SELECT * FROM messages WHERE message_id = ?", (message_id,)
            )
            try:
                row = await cursor.fetchone()
            finally:
                await cursor.close()
            if row is None:
                return None
            result = dict(row)
            result["payload"] = json.loads(str(result["payload_json"]))
            result.pop("payload_json", None)
            return result

    async def list_messages(
        self,
        *,
        after: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if after < 0:
            raise ValueError("after must not be negative")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        async with self._operation_lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                """
                SELECT rowid AS cursor, *
                FROM messages
                WHERE rowid > ?
                ORDER BY rowid ASC
                LIMIT ?
                """,
                (after, limit),
            )
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()
        messages: list[dict[str, Any]] = []
        for row in rows:
            result = dict(row)
            result["payload"] = json.loads(str(result.pop("payload_json")))
            messages.append(result)
        return messages

    async def list_devices(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if offset < 0:
            raise ValueError("offset must not be negative")
        async with self._operation_lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                """
                SELECT device_id, name, mac, transport, status, ip_address,
                       firmware_version, capabilities_json, last_hello_at,
                       last_heartbeat_at, action_state, current_action, enabled,
                       last_error, session_generation, created_at, updated_at
                FROM devices
                ORDER BY name COLLATE NOCASE, device_id
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            )
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()
        return [self._device_row(row) for row in rows]

    async def fetch_device(self, device_id: str) -> dict[str, Any] | None:
        async with self._operation_lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                """
                SELECT device_id, name, mac, transport, status, ip_address,
                       firmware_version, capabilities_json, last_hello_at,
                       last_heartbeat_at, action_state, current_action, enabled,
                       last_error, session_generation, created_at, updated_at
                FROM devices
                WHERE device_id = ?
                """,
                (device_id,),
            )
            try:
                row = await cursor.fetchone()
            finally:
                await cursor.close()
        return self._device_row(row) if row is not None else None

    async def upsert_device(
        self,
        *,
        device_id: str,
        name: str,
        mac: str,
        transport: str,
        status: str,
        capabilities: Mapping[str, JsonValue],
        ip_address: str | None,
        firmware_version: str | None,
        last_hello_at: str | None,
        last_heartbeat_at: str | None,
        action_state: str,
        current_action: str | None,
        enabled: bool,
        last_error: str | None,
        session_generation: int,
    ) -> None:
        if not device_id.strip() or not name.strip() or not mac.strip():
            raise ValueError("device identity and name must not be empty")
        now = _now()
        capabilities_json = _json(dict(capabilities))
        async with self._operation_lock:
            connection = self._require_connection()
            await connection.execute(
                """
                INSERT INTO devices (
                    device_id, name, mac, transport, status, ip_address,
                    firmware_version, capabilities_json, last_hello_at,
                    last_heartbeat_at, action_state, current_action, enabled,
                    last_error, session_generation, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    name = excluded.name,
                    mac = excluded.mac,
                    transport = excluded.transport,
                    status = excluded.status,
                    ip_address = excluded.ip_address,
                    firmware_version = excluded.firmware_version,
                    capabilities_json = excluded.capabilities_json,
                    last_hello_at = excluded.last_hello_at,
                    last_heartbeat_at = excluded.last_heartbeat_at,
                    action_state = excluded.action_state,
                    current_action = excluded.current_action,
                    enabled = excluded.enabled,
                    last_error = excluded.last_error,
                    session_generation = excluded.session_generation,
                    updated_at = excluded.updated_at
                """,
                (
                    device_id,
                    name,
                    mac,
                    transport,
                    status,
                    ip_address,
                    firmware_version,
                    capabilities_json,
                    last_hello_at,
                    last_heartbeat_at,
                    action_state,
                    current_action,
                    int(enabled),
                    last_error,
                    session_generation,
                    now,
                    now,
                ),
            )
            await connection.commit()

    async def save_device_actions(
        self,
        device_id: str,
        actions: Sequence[Mapping[str, JsonValue]],
    ) -> None:
        now = _now()
        normalized: list[tuple[str, str, str]] = []
        names: set[str] = set()
        for action in actions:
            name = action.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("each device action must have a non-empty name")
            if name in names:
                raise ValueError(f"duplicate device action: {name}")
            names.add(name)
            normalized.append((device_id, name, _json(dict(action))))
        async with self._operation_lock:
            connection = self._require_connection()
            await connection.execute(
                "DELETE FROM device_actions WHERE device_id = ?",
                (device_id,),
            )
            if normalized:
                await connection.executemany(
                    """
                    INSERT INTO device_actions (device_id, action_name, schema_json, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    [(*item, now) for item in normalized],
                )
            await connection.commit()

    async def fetch_device_actions(self, device_id: str) -> list[dict[str, JsonValue]]:
        async with self._operation_lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                """
                SELECT schema_json
                FROM device_actions
                WHERE device_id = ?
                ORDER BY action_name COLLATE NOCASE
                """,
                (device_id,),
            )
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()
        actions: list[dict[str, JsonValue]] = []
        for row in rows:
            decoded = json.loads(str(row["schema_json"]))
            if not isinstance(decoded, dict):
                raise DatabaseError("stored device action is not an object")
            actions.append(decoded)
        return actions

    async def create_command_record(
        self,
        *,
        command_id: str,
        correlation_id: str | None,
        topic: str,
        target: str,
        status: str,
        payload: Mapping[str, JsonValue],
        result_payload: Mapping[str, JsonValue] | None = None,
    ) -> bool:
        """Atomically create a command and its first lifecycle result.

        Returns ``False`` when the command ID already exists. Callers must then
        compare the existing immutable request before treating it as an
        idempotent duplicate.
        """

        if not command_id.strip() or not topic.strip() or not target.strip() or not status.strip():
            raise ValueError("command identity, topic, target and status must not be empty")
        now = _now()
        payload_json = _json(sanitize_payload(payload))
        result_json = _json(sanitize_payload(result_payload or {}))
        async with self._operation_lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                """
                INSERT OR IGNORE INTO commands (
                    command_id, correlation_id, topic, target, status,
                    payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    command_id,
                    correlation_id,
                    topic,
                    target,
                    status,
                    payload_json,
                    now,
                    now,
                ),
            )
            inserted = cursor.rowcount == 1
            await cursor.close()
            if inserted:
                await connection.execute(
                    """
                    INSERT INTO command_results (
                        result_id, command_id, correlation_id, status,
                        error, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, NULL, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        command_id,
                        correlation_id,
                        status,
                        result_json,
                        now,
                    ),
                )
            await connection.commit()
            return inserted

    async def transition_command_record(
        self,
        *,
        command_id: str,
        expected_status: str,
        status: str,
        error: str | None = None,
        payload: Mapping[str, JsonValue] | None = None,
    ) -> bool:
        """Conditionally update one command and append matching history."""

        now = _now()
        payload_json = _json(sanitize_payload(payload or {}))
        async with self._operation_lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                """
                UPDATE commands
                SET status = ?, updated_at = ?
                WHERE command_id = ? AND status = ?
                """,
                (status, now, command_id, expected_status),
            )
            changed = cursor.rowcount == 1
            await cursor.close()
            if not changed:
                await connection.rollback()
                return False
            await connection.execute(
                """
                INSERT INTO command_results (
                    result_id, command_id, correlation_id, status,
                    error, payload_json, created_at
                )
                SELECT ?, command_id, correlation_id, ?, ?, ?, ?
                FROM commands
                WHERE command_id = ?
                """,
                (
                    str(uuid4()),
                    status,
                    error,
                    payload_json,
                    now,
                    command_id,
                ),
            )
            await connection.commit()
            return True

    async def fetch_command_record(self, command_id: str) -> dict[str, Any] | None:
        async with self._operation_lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                "SELECT * FROM commands WHERE command_id = ?",
                (command_id,),
            )
            try:
                row = await cursor.fetchone()
            finally:
                await cursor.close()
        return self._command_row(row) if row is not None else None

    async def fetch_command_results(self, command_id: str) -> list[dict[str, Any]]:
        async with self._operation_lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                """
                SELECT result_id, command_id, correlation_id, status,
                       error, payload_json, created_at
                FROM command_results
                WHERE command_id = ?
                ORDER BY rowid ASC
                """,
                (command_id,),
            )
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()
        results: list[dict[str, Any]] = []
        for row in rows:
            result = dict(row)
            result["payload"] = json.loads(str(result.pop("payload_json")))
            results.append(result)
        return results

    async def list_command_records(
        self,
        *,
        statuses: Sequence[str] | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        parameters: list[Any] = []
        where = ""
        if statuses is not None:
            normalized = tuple(statuses)
            if not normalized:
                return []
            where = f"WHERE status IN ({','.join('?' for _ in normalized)})"
            parameters.extend(normalized)
        parameters.append(limit)
        async with self._operation_lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                f"""
                SELECT * FROM commands
                {where}
                ORDER BY created_at ASC, command_id ASC
                LIMIT ?
                """,
                parameters,
            )
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()
        return [self._command_row(row) for row in rows]

    async def mark_active_devices_offline(self) -> int:
        now = _now()
        async with self._operation_lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                """
                UPDATE devices
                SET status = 'offline', action_state = 'unknown', current_action = NULL,
                    updated_at = ?
                WHERE status IN ('unknown', 'provisioning', 'connecting', 'online', 'stale', 'error')
                  AND enabled = 1
                """,
                (now,),
            )
            changed = max(cursor.rowcount, 0)
            await cursor.close()
            await connection.commit()
            return changed

    async def save_setting(self, key: str, value: JsonValue) -> None:
        if not key.strip():
            raise ValueError("setting key must not be empty")
        value_json = _json(_sanitize(value))
        now = _now()
        async with self._operation_lock:
            connection = self._require_connection()
            await connection.execute(
                """
                INSERT INTO settings (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json,
                                                updated_at = excluded.updated_at
                """,
                (key, value_json, now),
            )
            await connection.commit()

    async def load_settings(self) -> dict[str, JsonValue]:
        async with self._operation_lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                "SELECT key, value_json FROM settings ORDER BY key"
            )
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()
        return {str(row["key"]): json.loads(str(row["value_json"])) for row in rows}

    @staticmethod
    def _device_row(row: aiosqlite.Row) -> dict[str, Any]:
        result = dict(row)
        result["capabilities"] = json.loads(str(result.pop("capabilities_json")))
        result["enabled"] = bool(result["enabled"])
        return result

    @staticmethod
    def _command_row(row: aiosqlite.Row) -> dict[str, Any]:
        result = dict(row)
        result["payload"] = json.loads(str(result.pop("payload_json")))
        return result

    async def _count_rows(self, table: str) -> int:
        if table not in {
            "devices",
            "device_groups",
            "messages",
            "commands",
            "command_results",
            "settings",
            "device_actions",
        }:
            raise ValueError("unsupported table")
        async with self._operation_lock:
            connection = self._require_connection()
            cursor = await connection.execute(f"SELECT COUNT(*) FROM {table}")
            try:
                row = await cursor.fetchone()
            finally:
                await cursor.close()
            return int(row[0]) if row is not None else 0

    def _require_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise DatabaseError("database is not open")
        return self._connection


class MessageLogSubscriber:
    """Message Bus callback that persists only sanitized message envelopes."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def __call__(self, message: Message) -> None:
        await self.database.log_message(message)
