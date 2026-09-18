"""Versioned SQLite schema migrations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite


class MigrationError(RuntimeError):
    """Raised when the database schema cannot be migrated safely."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    sql: str


_MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    transport TEXT,
    status TEXT NOT NULL DEFAULT 'offline',
    capabilities_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS device_groups (
    group_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS device_group_members (
    group_id TEXT NOT NULL REFERENCES device_groups(group_id) ON DELETE CASCADE,
    device_id TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, device_id)
);

CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    correlation_id TEXT,
    topic TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('command', 'event', 'state', 'result')),
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_topic_created_at
    ON messages (topic, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_correlation_id
    ON messages (correlation_id);

CREATE TABLE IF NOT EXISTS commands (
    command_id TEXT PRIMARY KEY,
    correlation_id TEXT,
    topic TEXT NOT NULL,
    target TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS command_results (
    result_id TEXT PRIMARY KEY,
    command_id TEXT,
    correlation_id TEXT,
    status TEXT NOT NULL,
    error TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_command_results_command_id
    ON command_results (command_id);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


_MIGRATION_2 = """
ALTER TABLE devices ADD COLUMN mac TEXT;
ALTER TABLE devices ADD COLUMN ip_address TEXT;
ALTER TABLE devices ADD COLUMN firmware_version TEXT;
ALTER TABLE devices ADD COLUMN last_hello_at TEXT;
ALTER TABLE devices ADD COLUMN last_heartbeat_at TEXT;
ALTER TABLE devices ADD COLUMN action_state TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE devices ADD COLUMN current_action TEXT;
ALTER TABLE devices ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1;
ALTER TABLE devices ADD COLUMN last_error TEXT;
ALTER TABLE devices ADD COLUMN session_generation INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_devices_status ON devices (status);
CREATE INDEX IF NOT EXISTS idx_devices_mac ON devices (mac);

CREATE TABLE IF NOT EXISTS device_actions (
    device_id TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    action_name TEXT NOT NULL,
    schema_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (device_id, action_name)
);
"""


MIGRATIONS: tuple[Migration, ...] = (
    Migration(version=1, sql=_MIGRATION_1),
    Migration(version=2, sql=_MIGRATION_2),
)
SCHEMA_VERSION = MIGRATIONS[-1].version


async def current_schema_version(connection: aiosqlite.Connection) -> int:
    cursor = await connection.execute("PRAGMA user_version")
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    if row is None:
        raise MigrationError("SQLite did not return user_version")
    return int(row[0])


async def apply_migrations(connection: aiosqlite.Connection) -> int:
    """Apply each pending migration atomically and return the final version."""

    version = await current_schema_version(connection)
    if version > SCHEMA_VERSION:
        raise MigrationError(
            f"database schema version {version} is newer than supported {SCHEMA_VERSION}"
        )

    for migration in MIGRATIONS:
        if migration.version <= version:
            continue
        applied_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        script = f"""
BEGIN IMMEDIATE;
{migration.sql}
INSERT OR IGNORE INTO schema_migrations (version, applied_at)
VALUES ({migration.version}, '{applied_at}');
PRAGMA user_version = {migration.version};
COMMIT;
"""
        try:
            await connection.executescript(script)
        except Exception as exc:
            await connection.rollback()
            raise MigrationError(f"failed to apply migration {migration.version}") from exc
        version = migration.version
    return version
