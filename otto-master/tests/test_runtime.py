from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from amqtt.client import MQTTClient  # type: ignore[import-untyped]
from websockets.asyncio.client import connect

from otto_master.config import LoggingConfig, RuntimeSecrets, load_config
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


async def _connect_fake_device(provisioning: dict[str, object], port: int) -> MQTTClient:
    mqtt = provisioning["mqtt"]
    assert isinstance(mqtt, dict)
    client_id = str(mqtt["client_id"])
    username = quote(str(mqtt["username"]), safe="")
    password = quote(str(mqtt["password"]), safe="")
    client = MQTTClient(
        client_id=client_id,
        config={"auto_reconnect": False, "reconnect_retries": 0, "plugins": {}},
    )
    await client.connect(f"mqtt://{username}:{password}@127.0.0.1:{port}/")
    assert await client.subscribe([(str(mqtt["subscribe_topic"]), 0)]) == [0]
    return client


async def _publish_device(
    client: MQTTClient,
    provisioning: dict[str, object],
    payload: dict[str, object],
) -> None:
    mqtt = provisioning["mqtt"]
    assert isinstance(mqtt, dict)
    await client.publish(
        str(mqtt["publish_topic"]),
        json.dumps(payload).encode(),
        qos=0,
        retain=False,
    )


async def _wait_for_two_online(client: httpx.AsyncClient) -> list[dict[str, object]]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5
    while loop.time() < deadline:
        response = await client.get("/api/v1/devices")
        items = response.json()["items"]
        if len(items) == 2 and all(
            item["status"] == "online" and item["actions_count"] == 2 for item in items
        ):
            return items
        await asyncio.sleep(0.02)
    raise AssertionError("two fake devices did not become online")


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


@pytest.mark.asyncio
async def test_runtime_two_fake_devices_read_only_mqtt_integration(tmp_path: Path) -> None:
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
            heartbeat_stale_seconds=0.5,
            heartbeat_offline_seconds=1.0,
            gateway_reconnect_seconds=0.1,
        ),
        discovery=replace(loaded.discovery, enabled=False),
        secrets=RuntimeSecrets(
            console_token=None,
            mqtt_master_password="master-test-password",
            provisioning_token="provision-test-token",
        ),
    )
    runtime = Runtime(config)
    fake_clients: list[MQTTClient] = []

    await runtime.start()
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{http_port}") as client:
            provisioned: list[dict[str, object]] = []
            for suffix in ("01", "02"):
                response = await client.post(
                    "/api/v1/ota/provision",
                    json={"mac": f"aa:bb:cc:dd:ee:{suffix}"},
                    headers={"X-Otto-Provisioning-Token": "provision-test-token"},
                )
                assert response.status_code == 200
                provisioned.append(response.json())

            for item in provisioned:
                fake_clients.append(await _connect_fake_device(item, mqtt_port))

            for index, (mqtt_client, item) in enumerate(
                zip(fake_clients, provisioned, strict=True),
                start=1,
            ):
                mac = f"aa:bb:cc:dd:ee:0{index}"
                await _publish_device(
                    mqtt_client,
                    item,
                    {
                        "type": "hello",
                        "protocol": "otto-mqtt/1",
                        "name": f"EVA{index}",
                        "mac": mac,
                        "firmware_version": "2.0.5-test",
                        "ip_address": f"192.0.2.{10 + index}",
                        "capabilities": {"actions": True, "state": True},
                    },
                )
                await _publish_device(mqtt_client, item, {"type": "heartbeat"})
                await _publish_device(
                    mqtt_client,
                    item,
                    {
                        "type": "otto_state",
                        "action_state": "idle",
                        "current_action": None,
                    },
                )
                await _publish_device(
                    mqtt_client,
                    item,
                    {
                        "type": "otto_actions",
                        "id": f"catalog-{index}",
                        "actions": [
                            {"name": f"device-{index}-only"},
                            {"name": "swing", "parameters": {"steps": "integer"}},
                        ],
                    },
                )

            devices = await _wait_for_two_online(client)
            assert {item["device_id"] for item in devices} == {
                "aabbccddee01",
                "aabbccddee02",
            }
            assert {item["name"] for item in devices} == {"EVA1", "EVA2"}
            assert all(item["transport"] == "mqtt" for item in devices)

            first_actions = await client.get("/api/v1/devices/aabbccddee01/actions")
            second_actions = await client.get("/api/v1/devices/aabbccddee02/actions")
            assert {item["name"] for item in first_actions.json()["items"]} == {
                "device-1-only",
                "swing",
            }
            assert {item["name"] for item in second_actions.json()["items"]} == {
                "device-2-only",
                "swing",
            }

            events = await client.get("/api/v1/events", params={"limit": 256})
            event_topics = {item["event"]["topic"] for item in events.json()["items"]}
            assert {
                "device.connected",
                "device.heartbeat.received",
                "device.actions.catalog.received",
                "device.state.changed",
            }.issubset(event_topics)
            assert all(
                "password" not in json.dumps(item).lower()
                for item in (devices, first_actions.json(), second_actions.json())
            )

            for mqtt_client in fake_clients:
                with pytest.raises(TimeoutError):
                    await mqtt_client.deliver_message(timeout_duration=0.1)

            await runtime.device_mqtt.shutdown()
            await runtime.message_bus.drain()
            unavailable = (await client.get("/api/v1/devices")).json()["items"]
            assert {item["status"] for item in unavailable} == {"error"}
            health = await client.get("/api/v1/health")
            assert health.json()["status"] == "degraded"
            assert "mqtt_gateway" in health.json()["unhealthy_components"]
    finally:
        for mqtt_client in fake_clients:
            await mqtt_client.disconnect()
        await runtime.shutdown()

    _assert_port_released(http_port)
    _assert_port_released(mqtt_port)
    database = await Database.open(tmp_path / "data" / "otto.db")
    try:
        persisted = await database.list_devices(limit=10)
        assert len(persisted) == 2
        assert {item["status"] for item in persisted} == {"offline"}
        assert len(await database.fetch_device_actions("aabbccddee01")) == 2
        assert len(await database.fetch_device_actions("aabbccddee02")) == 2
    finally:
        await database.close()
