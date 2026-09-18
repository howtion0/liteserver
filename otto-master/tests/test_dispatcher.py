from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from otto_master.config import DispatchConfig
from otto_master.dispatch.commands import CommandRepository
from otto_master.dispatch.dispatcher import CommandDispatcher, DispatchRequestError
from otto_master.message_bus import MessageBus
from otto_master.messages import JsonValue, Message, MessageKind
from otto_master.storage.database import Database

OutboundHandler = Callable[[Message], Awaitable[None]]


class FakeDevices:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {
            "aabbccddee01": self._device("aabbccddee01", "EVA1"),
            "aabbccddee02": self._device("aabbccddee02", "EVA2"),
        }
        self.actions: dict[str, list[dict[str, JsonValue]]] = {
            device_id: [
                {
                    "name": "swing",
                    "parameters": {
                        "steps": {
                            "type": "integer",
                            "required": True,
                            "minimum": 1,
                            "maximum": 5,
                        }
                    },
                }
            ]
            for device_id in self.items
        }

    @staticmethod
    def _device(device_id: str, name: str) -> dict[str, Any]:
        return {
            "device_id": device_id,
            "name": name,
            "status": "online",
            "transport": "mqtt",
            "enabled": True,
            "action_state": "idle",
            "capabilities": {"actions": True, "state": True, "stop": True},
        }

    async def list_devices(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self.items.values()]

    async def get_device(self, device_id: str) -> dict[str, Any] | None:
        item = self.items.get(device_id)
        return dict(item) if item is not None else None

    async def get_actions(
        self,
        device_id: str,
    ) -> list[dict[str, JsonValue]] | None:
        actions = self.actions.get(device_id)
        return [dict(item) for item in actions] if actions is not None else None


def _config() -> DispatchConfig:
    return DispatchConfig(
        queue_size_per_device=2,
        ack_timeout_seconds=0.15,
        completion_timeout_seconds=0.4,
        state_query_interval_seconds=0.05,
    )


async def _start(
    tmp_path: Path,
) -> tuple[Database, MessageBus, CommandRepository, CommandDispatcher, FakeDevices]:
    database = await Database.open(tmp_path / "otto.db")
    bus = MessageBus()
    await bus.start()
    repository = CommandRepository(database)
    devices = FakeDevices()
    dispatcher = CommandDispatcher(bus, repository, devices, _config())
    await dispatcher.start()
    return database, bus, repository, dispatcher, devices


async def _close(
    database: Database,
    bus: MessageBus,
    dispatcher: CommandDispatcher,
) -> None:
    await dispatcher.shutdown()
    await bus.stop()
    await database.close()


async def _publish(
    bus: MessageBus,
    outbound: Message,
    *,
    topic: str,
    payload: dict[str, JsonValue],
) -> None:
    await bus.publish(
        Message.create(
            topic=topic,
            kind=MessageKind.RESULT if topic != "device.state.received" else MessageKind.STATE,
            source=(
                f"device:{outbound.payload['device_id']}:"
                f"{outbound.payload['transport']}"
            ),
            target=outbound.target,
            correlation_id=str(outbound.payload["command_id"]),
            payload={
                "device_id": outbound.payload["device_id"],
                "transport": outbound.payload["transport"],
                **payload,
            },
        )
    )


async def _published(bus: MessageBus, outbound: Message) -> None:
    await _publish(
        bus,
        outbound,
        topic="device.command.published",
        payload={
            "command_topic": outbound.topic,
            "qos": 0,
            "retain": False,
        },
    )


async def _action_accepted(bus: MessageBus, outbound: Message) -> None:
    await _publish(
        bus,
        outbound,
        topic="robot.action.accepted",
        payload={"accepted": True, "command_type": "action"},
    )


async def _stop_accepted(bus: MessageBus, outbound: Message) -> None:
    await _publish(
        bus,
        outbound,
        topic="robot.stop.accepted",
        payload={"accepted": True, "command_type": "stop"},
    )


async def _state(
    bus: MessageBus,
    outbound: Message,
    state: str,
    action: str | None,
    *,
    sound_busy: bool | None = None,
) -> None:
    payload: dict[str, JsonValue] = {"action_state": state, "current_action": action}
    if sound_busy is not None:
        payload["sound_busy"] = sound_busy
    await _publish(
        bus,
        outbound,
        topic="device.state.received",
        payload=payload,
    )


async def _wait_status(
    repository: CommandRepository,
    command_id: str,
    expected: str,
    *,
    timeout: float = 2.0,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        command = await repository.get(command_id)
        if command is not None and command["status"] == expected:
            return command
        await asyncio.sleep(0.01)
    command = await repository.get(command_id)
    raise AssertionError(f"command {command_id} did not reach {expected}: {command}")


async def _subscribe_outbound(
    bus: MessageBus,
    *,
    action: OutboundHandler | None = None,
    stop: OutboundHandler | None = None,
) -> None:
    if action is not None:
        await bus.subscribe("device.action.execute.requested", action)
    if stop is not None:
        await bus.subscribe("device.stop.execute.requested", stop)


@pytest.mark.asyncio
async def test_actions_are_serial_per_device_and_duplicate_id_is_idempotent(
    tmp_path: Path,
) -> None:
    database, bus, repository, dispatcher, _ = await _start(tmp_path)
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    outbound_ids: list[str] = []

    async def handle_action(message: Message) -> None:
        command_id = str(message.payload["command_id"])
        outbound_ids.append(command_id)
        await _published(bus, message)
        await _action_accepted(bus, message)
        await _state(bus, message, "moving", "swing")
        if command_id == "command-1":
            first_started.set()
            await release_first.wait()
        await _state(bus, message, "idle", None)

    await _subscribe_outbound(bus, action=handle_action)
    try:
        await dispatcher.submit_action(
            device_id="aabbccddee01",
            action="swing",
            parameters={"steps": 2},
            confirmation=True,
            command_id="command-1",
        )
        await asyncio.wait_for(first_started.wait(), timeout=1)
        await dispatcher.submit_action(
            device_id="aabbccddee01",
            action="swing",
            parameters={"steps": 3},
            confirmation=True,
            command_id="command-2",
        )
        await asyncio.sleep(0.05)
        assert outbound_ids == ["command-1"]

        release_first.set()
        await _wait_status(repository, "command-1", "completed")
        await _wait_status(repository, "command-2", "completed")
        assert outbound_ids == ["command-1", "command-2"]

        duplicate = await dispatcher.submit_action(
            device_id="aabbccddee01",
            action="swing",
            parameters={"steps": 2},
            confirmation=True,
            command_id="command-1",
        )
        assert duplicate["status"] == "completed"
        await asyncio.sleep(0.05)
        assert outbound_ids == ["command-1", "command-2"]

        with pytest.raises(DispatchRequestError, match="another request"):
            await dispatcher.submit_action(
                device_id="aabbccddee01",
                action="swing",
                parameters={"steps": 4},
                confirmation=True,
                command_id="command-1",
            )
        assert (await repository.get("command-1"))["status"] == "completed"  # type: ignore[index]
    finally:
        release_first.set()
        await _close(database, bus, dispatcher)


@pytest.mark.asyncio
async def test_different_device_workers_execute_in_parallel(tmp_path: Path) -> None:
    database, bus, repository, dispatcher, _ = await _start(tmp_path)
    both_started = asyncio.Event()
    release = asyncio.Event()
    started: set[str] = set()

    async def handle_action(message: Message) -> None:
        started.add(str(message.payload["device_id"]))
        if len(started) == 2:
            both_started.set()
        await _published(bus, message)
        await _action_accepted(bus, message)
        await _state(bus, message, "moving", "swing")
        await release.wait()
        await _state(bus, message, "idle", None)

    await _subscribe_outbound(bus, action=handle_action)
    try:
        await dispatcher.submit_action(
            device_id="aabbccddee01",
            action="swing",
            parameters={"steps": 1},
            confirmation=True,
            command_id="eva1-command",
        )
        await dispatcher.submit_action(
            device_id="aabbccddee02",
            action="swing",
            parameters={"steps": 1},
            confirmation=True,
            command_id="eva2-command",
        )
        await asyncio.wait_for(both_started.wait(), timeout=1)
        assert started == {"aabbccddee01", "aabbccddee02"}
        release.set()
        await _wait_status(repository, "eva1-command", "completed")
        await _wait_status(repository, "eva2-command", "completed")
    finally:
        release.set()
        await _close(database, bus, dispatcher)


@pytest.mark.asyncio
async def test_action_completion_waits_for_local_sound_to_finish(tmp_path: Path) -> None:
    database, bus, repository, dispatcher, _ = await _start(tmp_path)
    motion_finished = asyncio.Event()
    release_sound = asyncio.Event()

    async def handle_action(message: Message) -> None:
        await _published(bus, message)
        await _action_accepted(bus, message)
        await _state(bus, message, "moving", "swing", sound_busy=True)
        await _state(bus, message, "idle", None, sound_busy=True)
        motion_finished.set()
        await release_sound.wait()
        await _state(bus, message, "idle", None, sound_busy=False)

    await _subscribe_outbound(bus, action=handle_action)
    try:
        await dispatcher.submit_action(
            device_id="aabbccddee01",
            action="swing",
            parameters={"steps": 2},
            confirmation=True,
            command_id="sound-drain-action",
        )
        await asyncio.wait_for(motion_finished.wait(), timeout=1)
        await asyncio.sleep(0.05)
        command = await repository.get("sound-drain-action")
        assert command is not None
        assert command["status"] == "moving"

        release_sound.set()
        completed = await _wait_status(repository, "sound-drain-action", "completed")
        assert completed["history"][-1]["status"] == "completed"
    finally:
        release_sound.set()
        await _close(database, bus, dispatcher)


@pytest.mark.asyncio
async def test_out_of_order_transport_facts_still_produce_legal_history(tmp_path: Path) -> None:
    database, bus, repository, dispatcher, _ = await _start(tmp_path)

    async def handle_action(message: Message) -> None:
        await _state(bus, message, "idle", None)
        await _state(bus, message, "moving", "swing")
        await _action_accepted(bus, message)
        await _published(bus, message)

    await _subscribe_outbound(bus, action=handle_action)
    try:
        await dispatcher.submit_action(
            device_id="aabbccddee01",
            action="swing",
            parameters={"steps": 2},
            confirmation=True,
            command_id="out-of-order",
        )
        completed = await _wait_status(repository, "out-of-order", "completed")
        assert [item["status"] for item in completed["history"]] == [
            "requested",
            "published",
            "accepted",
            "moving",
            "completed",
        ]
    finally:
        await _close(database, bus, dispatcher)


@pytest.mark.asyncio
async def test_device_queue_is_bounded_before_mqtt_publish(tmp_path: Path) -> None:
    database, bus, repository, dispatcher, _ = await _start(tmp_path)
    first_started = asyncio.Event()
    release = asyncio.Event()
    outbound: list[str] = []

    async def handle_action(message: Message) -> None:
        outbound.append(str(message.payload["command_id"]))
        await _published(bus, message)
        await _action_accepted(bus, message)
        await _state(bus, message, "moving", "swing")
        first_started.set()
        await release.wait()
        await _state(bus, message, "idle", None)

    await _subscribe_outbound(bus, action=handle_action)
    try:
        for command_id in ("active", "queued-1", "queued-2"):
            await dispatcher.submit_action(
                device_id="aabbccddee01",
                action="swing",
                parameters={"steps": 2},
                confirmation=True,
                command_id=command_id,
            )
            if command_id == "active":
                await asyncio.wait_for(first_started.wait(), timeout=1)
        with pytest.raises(DispatchRequestError, match="queue is full"):
            await dispatcher.submit_action(
                device_id="aabbccddee01",
                action="swing",
                parameters={"steps": 2},
                confirmation=True,
                command_id="overflow",
            )
        assert await repository.get("overflow") is None
        assert outbound == ["active"]
        release.set()
        for command_id in ("active", "queued-1", "queued-2"):
            await _wait_status(repository, command_id, "completed")
    finally:
        release.set()
        await _close(database, bus, dispatcher)


@pytest.mark.asyncio
async def test_stop_preempts_active_action_and_cancels_queued_actions(tmp_path: Path) -> None:
    database, bus, repository, dispatcher, _ = await _start(tmp_path)
    action_moving = asyncio.Event()
    outbound: list[tuple[str, str]] = []

    async def handle_action(message: Message) -> None:
        outbound.append(("action", str(message.payload["command_id"])))
        await _published(bus, message)
        await _action_accepted(bus, message)
        await _state(bus, message, "moving", "swing")
        action_moving.set()

    async def handle_stop(message: Message) -> None:
        outbound.append(("stop", str(message.payload["command_id"])))
        await _published(bus, message)
        await _stop_accepted(bus, message)
        await _state(bus, message, "idle", "idle")

    await _subscribe_outbound(bus, action=handle_action, stop=handle_stop)
    try:
        await dispatcher.submit_action(
            device_id="aabbccddee01",
            action="swing",
            parameters={"steps": 2},
            confirmation=True,
            command_id="active-action",
        )
        await asyncio.wait_for(action_moving.wait(), timeout=1)
        await _wait_status(repository, "active-action", "moving")
        await dispatcher.submit_action(
            device_id="aabbccddee01",
            action="swing",
            parameters={"steps": 3},
            confirmation=True,
            command_id="queued-action",
        )
        await dispatcher.submit_stop(
            device_id="aabbccddee01",
            command_id="stop-command",
        )

        active = await _wait_status(repository, "active-action", "failed")
        queued = await _wait_status(repository, "queued-action", "failed")
        stopped = await _wait_status(repository, "stop-command", "completed")
        assert active["history"][-1]["error"] == "interrupted_by_stop"
        assert queued["history"][-1]["error"] == "cancelled_by_stop:stop-command"
        assert stopped["history"][-1]["status"] == "completed"
        assert outbound == [("action", "active-action"), ("stop", "stop-command")]
    finally:
        await _close(database, bus, dispatcher)


@pytest.mark.asyncio
async def test_ack_timeout_does_not_retry_action_and_enqueues_safety_stop(
    tmp_path: Path,
) -> None:
    database, bus, repository, dispatcher, _ = await _start(tmp_path)
    outbound: list[tuple[str, str]] = []

    async def handle_action(message: Message) -> None:
        outbound.append(("action", str(message.payload["command_id"])))
        await _published(bus, message)

    async def handle_stop(message: Message) -> None:
        outbound.append(("stop", str(message.payload["command_id"])))
        await _published(bus, message)
        await _stop_accepted(bus, message)
        await _state(bus, message, "idle", "idle")

    await _subscribe_outbound(bus, action=handle_action, stop=handle_stop)
    try:
        await dispatcher.submit_action(
            device_id="aabbccddee01",
            action="swing",
            parameters={"steps": 2},
            confirmation=True,
            command_id="timeout-action",
        )
        timed_out = await _wait_status(repository, "timeout-action", "timeout")
        assert timed_out["history"][-1]["error"] == "publish_or_ack_timeout"

        deadline = asyncio.get_running_loop().time() + 2
        while len(outbound) < 2 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert [item[0] for item in outbound] == ["action", "stop"]
        assert [item for item in outbound if item[0] == "action"] == [
            ("action", "timeout-action")
        ]
        safety_stop = await _wait_status(repository, outbound[1][1], "completed")
        assert safety_stop["payload"]["source"] == "dispatcher:safety"
        assert safety_stop["correlation_id"] == "timeout-action"
    finally:
        await _close(database, bus, dispatcher)


@pytest.mark.asyncio
async def test_idle_timeout_after_moving_enqueues_safety_stop(tmp_path: Path) -> None:
    database, bus, repository, dispatcher, _ = await _start(tmp_path)
    outbound: list[tuple[str, str]] = []

    async def handle_action(message: Message) -> None:
        outbound.append(("action", str(message.payload["command_id"])))
        await _published(bus, message)
        await _action_accepted(bus, message)
        await _state(bus, message, "moving", "swing")

    async def handle_stop(message: Message) -> None:
        outbound.append(("stop", str(message.payload["command_id"])))
        await _published(bus, message)
        await _stop_accepted(bus, message)
        await _state(bus, message, "idle", "idle")

    await _subscribe_outbound(bus, action=handle_action, stop=handle_stop)
    try:
        await dispatcher.submit_action(
            device_id="aabbccddee01",
            action="swing",
            parameters={"steps": 2},
            confirmation=True,
            command_id="idle-timeout-action",
        )
        timed_out = await _wait_status(repository, "idle-timeout-action", "timeout")
        assert timed_out["history"][-1]["error"] == "moving_or_idle_timeout"
        deadline = asyncio.get_running_loop().time() + 2
        while len(outbound) < 2 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert [item[0] for item in outbound] == ["action", "stop"]
        await _wait_status(repository, outbound[1][1], "completed")
    finally:
        await _close(database, bus, dispatcher)


@pytest.mark.asyncio
async def test_transport_disconnect_is_a_terminal_command_state(tmp_path: Path) -> None:
    database, bus, repository, dispatcher, _ = await _start(tmp_path)

    async def handle_action(message: Message) -> None:
        await _published(bus, message)
        await _action_accepted(bus, message)
        await bus.publish(
            Message.create(
                topic="device.transport.unavailable",
                kind=MessageKind.EVENT,
                source="device_mqtt",
                target="service:device_manager",
                payload={"transport": "mqtt", "reason": "broker_disconnected"},
            )
        )

    await _subscribe_outbound(bus, action=handle_action)
    try:
        await dispatcher.submit_action(
            device_id="aabbccddee01",
            action="swing",
            parameters={"steps": 2},
            confirmation=True,
            command_id="disconnected-action",
        )
        disconnected = await _wait_status(
            repository, "disconnected-action", "disconnected"
        )
        assert disconnected["history"][-1]["error"] == "broker_disconnected"
    finally:
        await _close(database, bus, dispatcher)


@pytest.mark.asyncio
async def test_selected_transport_switch_is_a_terminal_command_state(
    tmp_path: Path,
) -> None:
    database, bus, repository, dispatcher, _ = await _start(tmp_path)

    async def handle_action(message: Message) -> None:
        await _published(bus, message)
        await _action_accepted(bus, message)
        await bus.publish(
            Message.create(
                topic="device.state.changed",
                kind=MessageKind.STATE,
                source="device_manager",
                target=message.target,
                correlation_id="connection-event",
                payload={
                    "device_id": message.payload["device_id"],
                    "transport": "websocket",
                    "status": "online",
                },
            )
        )

    await _subscribe_outbound(bus, action=handle_action)
    try:
        await dispatcher.submit_action(
            device_id="aabbccddee01",
            action="swing",
            parameters={"steps": 2},
            confirmation=True,
            command_id="transport-switched-action",
        )
        disconnected = await _wait_status(
            repository,
            "transport-switched-action",
            "disconnected",
        )
        assert disconnected["history"][-1]["error"] == "device_transport_changed"
    finally:
        await _close(database, bus, dispatcher)


@pytest.mark.asyncio
async def test_cluster_stop_splits_into_per_device_results(tmp_path: Path) -> None:
    database, bus, repository, dispatcher, devices = await _start(tmp_path)
    devices.items["aabbccddee02"]["status"] = "offline"
    received: list[str] = []

    async def handle_stop(message: Message) -> None:
        received.append(str(message.payload["device_id"]))
        await _published(bus, message)
        await _stop_accepted(bus, message)
        await _state(bus, message, "idle", "idle")

    await _subscribe_outbound(bus, stop=handle_stop)
    try:
        result = await dispatcher.stop_cluster(source="test")
        assert result["requested"] == 2
        assert result["accepted"] == 1
        accepted = next(item for item in result["items"] if item["accepted"])
        rejected = next(item for item in result["items"] if not item["accepted"])
        assert accepted["device_id"] == "aabbccddee01"
        assert rejected["device_id"] == "aabbccddee02"
        assert rejected["error"]["code"] == "device_not_online"
        await _wait_status(repository, str(accepted["command_id"]), "completed")
        assert received == ["aabbccddee01"]
    finally:
        await _close(database, bus, dispatcher)


@pytest.mark.asyncio
async def test_validation_rejects_unsafe_or_invalid_actions_before_persistence(
    tmp_path: Path,
) -> None:
    database, bus, repository, dispatcher, devices = await _start(tmp_path)
    outbound: list[Message] = []

    async def handle_action(message: Message) -> None:
        outbound.append(message)

    await _subscribe_outbound(bus, action=handle_action)
    try:
        cases = (
            {
                "action": "swing",
                "parameters": {"steps": 2},
                "confirmation": False,
                "command_id": "no-confirmation",
            },
            {
                "action": "unknown",
                "parameters": {},
                "confirmation": True,
                "command_id": "unknown-action",
            },
            {
                "action": "swing",
                "parameters": {"steps": 99},
                "confirmation": True,
                "command_id": "bad-parameters",
            },
        )
        for case in cases:
            with pytest.raises(DispatchRequestError):
                await dispatcher.submit_action(device_id="aabbccddee01", **case)  # type: ignore[arg-type]
        devices.items["aabbccddee01"]["status"] = "offline"
        with pytest.raises(DispatchRequestError, match="not online"):
            await dispatcher.submit_action(
                device_id="aabbccddee01",
                action="swing",
                parameters={"steps": 2},
                confirmation=True,
                command_id="offline-action",
            )
        await bus.drain()
        assert outbound == []
        for command_id in (
            "no-confirmation",
            "unknown-action",
            "bad-parameters",
            "offline-action",
        ):
            assert await repository.get(command_id) is None
    finally:
        await _close(database, bus, dispatcher)
