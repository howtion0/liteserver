from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from otto_master.devices.manager import DeviceManager
from otto_master.devices.verifier import DeviceVerifier
from otto_master.message_bus import MessageBus
from otto_master.messages import JsonValue, Message, MessageKind
from otto_master.storage.database import Database

DEVICE_ID = "aabbccddee01"


def _device_message(topic: str, payload: dict[str, JsonValue]) -> Message:
    return Message.create(
        topic=topic,
        kind=MessageKind.EVENT,
        source=f"device:{DEVICE_ID}:mqtt",
        target=f"device:{DEVICE_ID}",
        payload={
            "device_id": DEVICE_ID,
            "mac": DEVICE_ID,
            "transport": "mqtt",
            **payload,
        },
    )


async def _online_manager(
    tmp_path: Path,
) -> tuple[Database, MessageBus, DeviceManager, DeviceVerifier]:
    database = await Database.open(tmp_path / "otto.db")
    bus = MessageBus()
    await bus.start()
    manager = DeviceManager(
        database,
        bus,
        stale_seconds=15,
        offline_seconds=30,
        monitor_interval=60,
    )
    verifier = DeviceVerifier(
        bus,
        manager,
        broker_status=lambda: {"healthy": True, "state": "running"},
        gateway_status=lambda: {"healthy": True, "state": "running"},
        timeout_seconds=0.1,
    )
    await manager.start()
    await verifier.start()
    await bus.publish(
        _device_message(
            "device.connected",
            {
                "name": "EVA1",
                "firmware_version": "2.0.5-test",
                "capabilities": {"state": True, "actions": True},
            },
        )
    )
    await bus.publish(_device_message("device.heartbeat.received", {}))
    await bus.drain()
    return database, bus, manager, verifier


async def _close(
    database: Database,
    bus: MessageBus,
    manager: DeviceManager,
    verifier: DeviceVerifier,
) -> None:
    await verifier.shutdown()
    await manager.shutdown()
    await bus.stop()
    await database.close()


@pytest.mark.asyncio
async def test_verifier_correlates_state_and_actions_and_reports_steps(
    tmp_path: Path,
) -> None:
    database, bus, manager, verifier = await _online_manager(tmp_path)
    command_ids: list[str] = []

    async def respond(message: Message) -> None:
        command_ids.append(message.message_id)
        if message.topic == "device.state.query.requested":
            await bus.publish(
                Message.create(
                    topic="device.state.received",
                    kind=MessageKind.STATE,
                    source=f"device:{DEVICE_ID}:mqtt",
                    target=message.target,
                    correlation_id=message.message_id,
                    payload={
                        "device_id": DEVICE_ID,
                        "mac": DEVICE_ID,
                        "transport": "mqtt",
                        "action_state": "idle",
                        "current_action": None,
                    },
                )
            )
        elif message.topic == "device.actions.query.requested":
            await bus.publish(
                Message.create(
                    topic="device.actions.catalog.received",
                    kind=MessageKind.EVENT,
                    source=f"device:{DEVICE_ID}:mqtt",
                    target=message.target,
                    correlation_id=message.message_id,
                    payload={
                        "device_id": DEVICE_ID,
                        "mac": DEVICE_ID,
                        "transport": "mqtt",
                        "actions": [{"name": "swing"}, {"name": "walk"}],
                    },
                )
            )

    state_subscription = await bus.subscribe("device.state.query.requested", respond)
    actions_subscription = await bus.subscribe("device.actions.query.requested", respond)
    try:
        report = await verifier.verify(DEVICE_ID)
        assert report is not None
        assert report["passed"] is True
        assert report["action_state"] == "idle"
        assert report["actions_count"] == 2
        assert len(command_ids) == 2
        checks = {item["name"]: item for item in report["checks"]}
        assert checks["state_query"]["command_id"] == command_ids[0]
        assert checks["actions_query"]["command_id"] == command_ids[1]
        assert all(item["status"] == "pass" for item in report["checks"])
        assert verifier.pending_count == 0
    finally:
        await bus.unsubscribe(state_subscription)
        await bus.unsubscribe(actions_subscription)
        await _close(database, bus, manager, verifier)


@pytest.mark.asyncio
async def test_verifier_ignores_wrong_target_and_times_out_without_leaking_pending(
    tmp_path: Path,
) -> None:
    database, bus, manager, verifier = await _online_manager(tmp_path)

    async def wrong_target(message: Message) -> None:
        await bus.publish(
            Message.create(
                topic="device.state.received",
                kind=MessageKind.STATE,
                source="forged",
                target="device:aabbccddee02",
                correlation_id=message.message_id,
                payload={
                    "device_id": "aabbccddee02",
                    "mac": "aabbccddee02",
                    "transport": "mqtt",
                    "action_state": "idle",
                    "current_action": None,
                },
            )
        )
        await bus.publish(
            Message.create(
                topic="device.state.received",
                kind=MessageKind.STATE,
                source=f"device:{DEVICE_ID}:websocket",
                target=message.target,
                correlation_id=message.message_id,
                payload={
                    "device_id": DEVICE_ID,
                    "mac": DEVICE_ID,
                    "transport": "websocket",
                    "action_state": "idle",
                    "current_action": None,
                },
            )
        )
        await bus.publish(
            Message.create(
                topic="device.state.received",
                kind=MessageKind.STATE,
                source=f"device:{DEVICE_ID}:mqtt",
                target=message.target,
                correlation_id="wrong-command-id",
                payload={
                    "device_id": DEVICE_ID,
                    "mac": DEVICE_ID,
                    "transport": "mqtt",
                    "action_state": "idle",
                    "current_action": None,
                },
            )
        )

    subscription = await bus.subscribe("device.state.query.requested", wrong_target)
    try:
        report = await verifier.verify(DEVICE_ID)
        assert report is not None
        assert report["passed"] is False
        assert "timed out" in str(report["failure"])
        assert verifier.pending_count == 0
    finally:
        await bus.unsubscribe(subscription)
        await _close(database, bus, manager, verifier)


@pytest.mark.asyncio
async def test_verifier_shutdown_interrupts_in_flight_query(tmp_path: Path) -> None:
    database, bus, manager, verifier = await _online_manager(tmp_path)
    verifier.timeout_seconds = 5
    task = asyncio.create_task(verifier.verify(DEVICE_ID))
    try:
        for _ in range(100):
            if verifier.pending_count == 1:
                break
            await asyncio.sleep(0.001)
        assert verifier.pending_count == 1
        await verifier.shutdown()
        report = await asyncio.wait_for(task, timeout=0.5)
        assert report is not None
        assert report["passed"] is False
        assert report["failure"] == "device verifier stopped"
        assert verifier.pending_count == 0
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await manager.shutdown()
        await bus.stop()
        await database.close()
