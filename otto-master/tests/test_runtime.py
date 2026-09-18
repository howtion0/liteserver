from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from websockets.asyncio.client import connect

from otto_master.config import LoggingConfig, load_config
from otto_master.gateways.mqtt_broker import MqttBrokerError
from otto_master.messages import Message, MessageKind
from otto_master.runtime import Runtime, RuntimeState
from otto_master.storage.database import Database


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _assert_port_released(port: int) -> None:
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))


@pytest.mark.asyncio
async def test_runtime_starts_and_shuts_down_without_leaking_tasks(tmp_path: Path) -> None:
    loaded = load_config()
    config = replace(
        loaded,
        config_path=tmp_path / "config.yaml",
        logging=LoggingConfig(level="INFO", jsonl_path="logs/runtime.jsonl"),
        server=replace(loaded.server, enabled=False),
        mqtt=replace(loaded.mqtt, enabled=False),
        discovery=replace(loaded.discovery, enabled=False),
    )
    runtime = Runtime(config)

    await runtime.start()
    assert runtime.state is RuntimeState.RUNNING
    assert runtime.message_bus.running is True
    assert runtime.database.is_open is True
    assert runtime.database.path == tmp_path / "data" / "otto.db"

    async def background() -> None:
        await asyncio.Event().wait()

    runtime.create_background_task(background(), name="test-background")
    assert runtime.background_task_count == 1
    await runtime.shutdown()
    await runtime.shutdown()

    assert runtime.state is RuntimeState.STOPPED
    assert runtime.message_bus.running is False
    assert runtime.database.is_open is False
    assert runtime.background_task_count == 0
    assert not list(asyncio.all_tasks()) or all(
        task is asyncio.current_task() or task.done() for task in asyncio.all_tasks()
    )


@pytest.mark.asyncio
async def test_runtime_shutdown_flushes_in_flight_messages(tmp_path: Path) -> None:
    loaded = load_config()
    config = replace(
        loaded,
        config_path=tmp_path / "config.yaml",
        logging=LoggingConfig(level="INFO", jsonl_path="logs/runtime.jsonl"),
        server=replace(loaded.server, enabled=False),
        mqtt=replace(loaded.mqtt, enabled=False),
        discovery=replace(loaded.discovery, enabled=False),
    )
    runtime = Runtime(config)
    await runtime.start()

    for index in range(12):
        await runtime.message_bus.publish(
            Message.create(
                topic="device.connected",
                kind=MessageKind.EVENT,
                source="test",
                target=f"device:test-{index}",
                message_id=f"runtime-message-{index}",
                payload={"index": index},
            )
        )

    await runtime.shutdown()

    database = await Database.open(tmp_path / "data" / "otto.db")
    assert await database.message_count() == 12
    await database.close()


@pytest.mark.asyncio
async def test_runtime_serves_health_and_releases_network_ports(tmp_path: Path) -> None:
    loaded = load_config()
    http_port = _free_port()
    mqtt_port = _free_port()
    config = replace(
        loaded,
        config_path=tmp_path / "config.yaml",
        logging=LoggingConfig(level="INFO", jsonl_path="logs/runtime.jsonl"),
        server=replace(
            loaded.server,
            enabled=True,
            host="127.0.0.1",
            port=http_port,
            allowed_origins=(f"http://127.0.0.1:{http_port}",),
        ),
        mqtt=replace(
            loaded.mqtt,
            enabled=True,
            host="127.0.0.1",
            port=mqtt_port,
            credentials_path=".local-secrets/runtime-mqtt.json",
        ),
        discovery=replace(loaded.discovery, enabled=False),
    )
    runtime = Runtime(config)

    await runtime.start()
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{http_port}") as client:
            health = await client.get("/api/v1/health")
            mqtt = await client.get("/api/v1/mqtt/status")
            root = await client.get("/")

        assert health.status_code == 200
        assert health.json()["components"]["sqlite"]["healthy"] is True
        assert health.json()["components"]["web"]["healthy"] is True
        assert health.json()["components"]["mqtt"]["healthy"] is True
        assert mqtt.json()["port"] == mqtt_port
        assert mqtt.json()["anonymous"] is False
        assert root.status_code == 200
        assert "Otto Master" in root.text

        async with connect(
            f"ws://127.0.0.1:{http_port}/api/v1/events/stream",
            origin=f"http://127.0.0.1:{http_port}",
        ) as websocket:
            snapshot = json.loads(await websocket.recv())
            assert snapshot["type"] == "snapshot"
            assert snapshot["cursor"] == 0
            await runtime.message_bus.publish(
                Message.create(
                    topic="device.connected",
                    kind=MessageKind.EVENT,
                    source="runtime-test",
                    target="device:aabbccddeeff",
                    payload={"name": "EVA1"},
                )
            )
            await runtime.message_bus.drain()
            event = json.loads(await websocket.recv())
            assert event["type"] == "event"
            assert event["cursor"] == 1
            assert event["event"]["topic"] == "device.connected"
    finally:
        await runtime.shutdown()

    _assert_port_released(http_port)
    _assert_port_released(mqtt_port)
    assert runtime.state is RuntimeState.STOPPED


@pytest.mark.asyncio
async def test_runtime_start_failure_rolls_back_every_started_component(tmp_path: Path) -> None:
    loaded = load_config()
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        mqtt_port = int(occupied.getsockname()[1])
        config = replace(
            loaded,
            config_path=tmp_path / "config.yaml",
            logging=LoggingConfig(level="INFO", jsonl_path="logs/runtime.jsonl"),
            server=replace(loaded.server, enabled=False),
            mqtt=replace(
                loaded.mqtt,
                enabled=True,
                host="127.0.0.1",
                port=mqtt_port,
                credentials_path=".local-secrets/runtime-mqtt.json",
            ),
            discovery=replace(loaded.discovery, enabled=False),
        )
        runtime = Runtime(config)

        with pytest.raises(MqttBrokerError):
            await runtime.start()

    assert runtime.state is RuntimeState.STOPPED
    assert runtime.database.is_open is False
    assert runtime.message_bus.running is False
    assert runtime.mqtt_broker.running is False
    await runtime.shutdown()
