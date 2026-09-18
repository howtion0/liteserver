from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from otto_master.config import DispatchConfig, load_config
from otto_master.devices.manager import DeviceManager
from otto_master.dispatch.commands import CommandRepository
from otto_master.dispatch.dispatcher import CommandDispatcher
from otto_master.gateways.device_tcp import DeviceTcpGateway
from otto_master.gateways.mqtt_broker import MqttCredential, MqttCredentialStore
from otto_master.message_bus import MessageBus
from otto_master.messages import Message, MessageKind
from otto_master.storage.database import Database

DEVICE_ID = "aabbccddee01"


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def _send(writer: asyncio.StreamWriter, value: dict[str, Any]) -> None:
    writer.write(json.dumps(value, separators=(",", ":")).encode() + b"\n")
    await writer.drain()


async def _receive(reader: asyncio.StreamReader) -> dict[str, Any]:
    line = await asyncio.wait_for(reader.readline(), timeout=1)
    assert line.endswith(b"\n")
    value = json.loads(line)
    assert isinstance(value, dict)
    return value


def _hello(credential: MqttCredential, *, token: str | None = None) -> dict[str, Any]:
    return {
        "type": "hello",
        "protocol": "otto-master/1",
        "mac": "aa:bb:cc:dd:ee:01",
        "client_id": credential.client_id,
        "token": token if token is not None else credential.password,
        "name": "EVA1",
        "firmware_version": "2.0.5-test",
        "capabilities": {"actions": True, "state": True, "stop": True},
    }


async def _wait_device(
    manager: DeviceManager,
    *,
    status: str,
    timeout: float = 1,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        device = await manager.get_device(DEVICE_ID)
        if device is not None and device["status"] == status:
            return device
        await asyncio.sleep(0.01)
    raise AssertionError(f"device did not reach {status}: {await manager.get_device(DEVICE_ID)}")


async def _wait_command(
    repository: CommandRepository,
    command_id: str,
    status: str,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + 2
    while asyncio.get_running_loop().time() < deadline:
        command = await repository.get(command_id)
        if command is not None and command["status"] == status:
            return command
        await asyncio.sleep(0.01)
    raise AssertionError(f"command did not reach {status}: {await repository.get(command_id)}")


async def _wait_actions(
    manager: DeviceManager,
    *,
    timeout: float = 1,
) -> list[dict[str, Any]]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        actions = await manager.get_actions(DEVICE_ID)
        if actions:
            return actions
        await asyncio.sleep(0.01)
    raise AssertionError("device action catalog was not received")


async def _setup(
    tmp_path: Path,
) -> tuple[
    Database,
    MessageBus,
    DeviceManager,
    DeviceTcpGateway,
    MqttCredential,
    int,
]:
    loaded = load_config()
    port = _free_port()
    tcp_config = replace(
        loaded.tcp,
        enabled=True,
        host="127.0.0.1",
        port=port,
        max_connections=1,
        max_frame_bytes=1024,
        hello_timeout_seconds=0.2,
        write_timeout_seconds=0.2,
    )
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
    await manager.start()
    credentials = MqttCredentialStore(
        tmp_path / "credentials.json",
        master_username="otto-master",
        configured_master_password="master-test-password",
    )
    await credentials.start()
    credential = await credentials.provision_device(DEVICE_ID)
    gateway = DeviceTcpGateway(tcp_config, credentials, bus)
    await gateway.start()
    return database, bus, manager, gateway, credential, port


async def _close(
    database: Database,
    bus: MessageBus,
    manager: DeviceManager,
    gateway: DeviceTcpGateway,
) -> None:
    await gateway.shutdown()
    await bus.drain()
    await manager.shutdown()
    await bus.stop()
    await database.close()


@pytest.mark.asyncio
async def test_tcp_authentication_translation_routing_and_connection_replacement(
    tmp_path: Path,
) -> None:
    database, bus, manager, gateway, credential, port = await _setup(tmp_path)
    observed: list[Message] = []
    observer_id = await bus.subscribe_observer(observed.append)
    writers: list[asyncio.StreamWriter] = []
    try:
        rejected_reader, rejected_writer = await asyncio.open_connection("127.0.0.1", port)
        writers.append(rejected_writer)
        await _send(rejected_writer, _hello(credential, token="wrong-token"))
        assert await asyncio.wait_for(rejected_reader.read(), timeout=1) == b""

        identity_reader, identity_writer = await asyncio.open_connection(
            "127.0.0.1", port
        )
        writers.append(identity_writer)
        invalid_identity = _hello(credential)
        invalid_identity["client_id"] = "unprovisioned-client"
        await _send(identity_writer, invalid_identity)
        assert await asyncio.wait_for(identity_reader.read(), timeout=1) == b""

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writers.append(writer)
        await _send(writer, _hello(credential))
        hello_ack = await _receive(reader)
        assert hello_ack["type"] == "hello"
        assert hello_ack["protocol"] == "otto-master/1"
        device = await _wait_device(manager, status="online")
        assert device["transport"] == "tcp"
        assert device["available_transports"] == ["tcp"]

        await _send(
            writer,
            {
                "type": "otto_actions",
                "id": "catalog-1",
                "actions": [{"name": "swing"}],
            },
        )
        await _send(
            writer,
            {
                "type": "otto_state",
                "id": "state-1",
                "action_state": "idle",
                "current_action": None,
            },
        )
        assert [item["name"] for item in await _wait_actions(manager)] == ["swing"]

        query = Message.create(
            topic="device.state.query.requested",
            kind=MessageKind.COMMAND,
            source="test",
            target=f"device:{DEVICE_ID}",
            message_id="tcp-query-1",
            payload={"device_id": DEVICE_ID, "transport": "tcp"},
        )
        await bus.publish(query)
        outbound = await _receive(reader)
        assert outbound == {"type": "otto_query", "id": "tcp-query-1"}
        await bus.drain()
        assert any(
            message.topic == "device.command.published"
            and message.payload.get("transport") == "tcp"
            for message in observed
        )
        assert credential.password not in json.dumps(
            [message.to_dict() for message in observed]
        )

        replacement_reader, replacement_writer = await asyncio.open_connection(
            "127.0.0.1", port
        )
        writers.append(replacement_writer)
        await _send(replacement_writer, _hello(credential))
        replacement_ack = await _receive(replacement_reader)
        assert replacement_ack["session_id"] != hello_ack["session_id"]
        assert await asyncio.wait_for(reader.read(), timeout=1) == b""
        still_online = await _wait_device(manager, status="online")
        assert still_online["transport"] == "tcp"

        replacement_writer.write(b"x" * 1100 + b"\n")
        await replacement_writer.drain()
        assert await asyncio.wait_for(replacement_reader.read(), timeout=1) == b""
        await _wait_device(manager, status="offline")
        assert gateway.status()["messages_rejected"] == 1
    finally:
        await bus.unsubscribe_observer(observer_id)
        for writer in writers:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
        await _close(database, bus, manager, gateway)


@pytest.mark.asyncio
async def test_tcp_dispatcher_action_and_stop_share_persisted_lifecycle(
    tmp_path: Path,
) -> None:
    database, bus, manager, gateway, credential, port = await _setup(tmp_path)
    repository = CommandRepository(database)
    dispatcher = CommandDispatcher(
        bus,
        repository,
        manager,
        DispatchConfig(
            queue_size_per_device=4,
            ack_timeout_seconds=0.5,
            completion_timeout_seconds=1,
            state_query_interval_seconds=0.1,
        ),
    )
    await dispatcher.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    responder: asyncio.Task[None] | None = None
    try:
        await _send(writer, _hello(credential))
        await _receive(reader)
        await _wait_device(manager, status="online")
        await _send(
            writer,
            {
                "type": "otto_actions",
                "actions": [
                    {
                        "name": "swing",
                        "parameters": {"steps": {"type": "integer", "required": True}},
                    }
                ],
            },
        )
        await _send(
            writer,
            {"type": "otto_state", "action_state": "idle", "current_action": None},
        )
        await _wait_actions(manager)

        async def respond() -> None:
            completed = 0
            while completed < 2:
                command = await _receive(reader)
                if command["type"] == "otto_query":
                    await _send(
                        writer,
                        {
                            "type": "otto_state",
                            "id": command["id"],
                            "action_state": "idle",
                            "current_action": None,
                        },
                    )
                    continue
                if command["type"] == "otto_action":
                    await _send(
                        writer,
                        {
                            "type": "otto_action_ack",
                            "id": command["id"],
                            "ok": True,
                            "action": command["action"],
                            "runtime": {
                                "otto": {
                                    "action": {
                                        "state": "moving",
                                        "name": command["action"],
                                    }
                                }
                            },
                        },
                    )
                    await _send(
                        writer,
                        {
                            "type": "otto_state",
                            "id": command["id"],
                            "action_state": "idle",
                            "current_action": None,
                        },
                    )
                else:
                    assert command["type"] == "stop"
                    await _send(
                        writer,
                        {
                            "type": "otto_stop_ack",
                            "id": command["id"],
                            "ok": True,
                            "runtime": {
                                "otto": {"action": {"state": "idle", "name": "idle"}}
                            },
                        },
                    )
                completed += 1

        responder = asyncio.create_task(respond())
        action = await dispatcher.submit_action(
            device_id=DEVICE_ID,
            action="swing",
            parameters={"steps": 2},
            confirmation=True,
            command_id="tcp-action-1",
        )
        assert action["payload"]["transport"] == "tcp"
        completed_action = await _wait_command(repository, "tcp-action-1", "completed")
        assert [item["status"] for item in completed_action["history"]] == [
            "requested",
            "published",
            "accepted",
            "moving",
            "completed",
        ]

        stop = await dispatcher.submit_stop(device_id=DEVICE_ID, command_id="tcp-stop-1")
        assert stop["payload"]["transport"] == "tcp"
        completed_stop = await _wait_command(repository, "tcp-stop-1", "completed")
        assert [item["status"] for item in completed_stop["history"]] == [
            "requested",
            "published",
            "accepted",
            "completed",
        ]
        await asyncio.wait_for(responder, timeout=1)
    finally:
        if responder is not None and not responder.done():
            responder.cancel()
            await asyncio.gather(responder, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await dispatcher.shutdown()
        await _close(database, bus, manager, gateway)
