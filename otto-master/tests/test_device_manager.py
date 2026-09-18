from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from otto_master.devices.manager import DeviceManager
from otto_master.message_bus import MessageBus
from otto_master.messages import JsonValue, Message, MessageKind
from otto_master.storage.database import Database


class FakeClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 18, 8, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


def _message(topic: str, payload: dict[str, JsonValue]) -> Message:
    device_id = "aabbccddee01"
    return Message.create(
        topic=topic,
        kind=MessageKind.EVENT,
        source=f"device:{device_id}:mqtt",
        target=f"device:{device_id}",
        payload={"device_id": device_id, "mac": device_id, **payload},
    )


@pytest.mark.asyncio
async def test_manager_tracks_one_session_liveness_and_persisted_catalog(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    database = await Database.open(tmp_path / "otto.db")
    bus = MessageBus()
    await bus.start()
    manager = DeviceManager(
        database,
        bus,
        stale_seconds=15,
        offline_seconds=30,
        clock=clock,
        monitor_interval=60,
    )
    await manager.start()
    try:
        await bus.publish(
            _message(
                "device.connected",
                {
                    "name": "EVA1",
                    "firmware_version": "2.0.5-test",
                    "ip_address": "192.0.2.10",
                    "capabilities": {"actions": True},
                },
            )
        )
        await bus.publish(_message("device.heartbeat.received", {}))
        await bus.publish(
            _message(
                "device.state.received",
                {"action_state": "moving", "current_action": "swing"},
            )
        )
        await bus.publish(
            _message(
                "device.actions.catalog.received",
                {"actions": [{"name": "swing"}, {"name": "walk"}]},
            )
        )
        await bus.drain()

        first = await manager.get_device("aabbccddee01")
        assert first is not None
        assert first["status"] == "online"
        assert first["action_state"] == "moving"
        assert first["actions_count"] == 2
        assert manager.device_count == 1

        clock.advance(1)
        await bus.publish(
            _message(
                "device.connected",
                {
                    "name": "EVA-Renamed",
                    "firmware_version": "2.0.6-test",
                    "ip_address": "192.0.2.99",
                    "capabilities": {"actions": True, "state": True},
                },
            )
        )
        await bus.publish(_message("device.heartbeat.received", {}))
        await bus.drain()
        updated = await manager.get_device("aabbccddee01")
        assert updated is not None
        assert updated["name"] == "EVA-Renamed"
        assert updated["ip_address"] == "192.0.2.99"
        assert updated["session_generation"] == 2
        assert manager.device_count == 1

        clock.advance(16)
        await manager.evaluate_liveness()
        stale = await manager.get_device("aabbccddee01")
        assert stale is not None and stale["status"] == "stale"

        clock.advance(15)
        await manager.evaluate_liveness()
        offline = await manager.get_device("aabbccddee01")
        assert offline is not None and offline["status"] == "offline"
        stored = await database.fetch_device("aabbccddee01")
        assert stored is not None and stored["status"] == "offline"
        assert [item["name"] for item in await database.fetch_device_actions("aabbccddee01")] == [
            "swing",
            "walk",
        ]
    finally:
        await manager.shutdown()
        await bus.stop()
        await database.close()

    restarted_database = await Database.open(tmp_path / "otto.db")
    restarted_bus = MessageBus()
    await restarted_bus.start()
    restarted = DeviceManager(
        restarted_database,
        restarted_bus,
        stale_seconds=15,
        offline_seconds=30,
        clock=clock,
        monitor_interval=60,
    )
    await restarted.start()
    try:
        restored = await restarted.get_device("aabbccddee01")
        assert restored is not None
        assert restored["status"] == "offline"
        assert restored["name"] == "EVA-Renamed"
        assert restored["firmware_version"] == "2.0.6-test"
        assert [item["name"] for item in (await restarted.get_actions("aabbccddee01") or [])] == [
            "swing",
            "walk",
        ]
    finally:
        await restarted.shutdown()
        await restarted_bus.stop()
        await restarted_database.close()


@pytest.mark.asyncio
async def test_manager_rejects_cross_target_identity(tmp_path: Path) -> None:
    database = await Database.open(tmp_path / "otto.db")
    bus = MessageBus()
    await bus.start()
    manager = DeviceManager(database, bus, stale_seconds=15, offline_seconds=30)
    await manager.start()
    try:
        message = _message("device.heartbeat.received", {})
        forged = Message.create(
            topic=message.topic,
            kind=message.kind,
            source=message.source,
            target="device:aabbccddee02",
            payload=message.payload,
        )
        await bus.publish(forged)
        await bus.drain()
        assert await manager.list_devices() == []
    finally:
        await manager.shutdown()
        await bus.stop()
        await database.close()
