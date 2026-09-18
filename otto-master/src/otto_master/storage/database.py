"""Single-entry SQLite persistence with serialized writes and redaction."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
                SELECT device_id, name, transport, status, capabilities_json,
                       created_at, updated_at
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
                SELECT device_id, name, transport, status, capabilities_json,
                       created_at, updated_at
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
        return result

    async def _count_rows(self, table: str) -> int:
        if table not in {"devices", "device_groups", "messages", "commands", "command_results", "settings"}:
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
