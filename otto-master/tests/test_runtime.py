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


async def _respond_to_read_only_verification(
    client: MQTTClient,
    provisioning: dict[str, object],
) -> list[dict[str, object]]:
    received: list[dict[str, object]] = []
    for expected_type in ("otto_query", "otto_actions"):
        packet = await client.deliver_message(timeout_duration=2)
        assert packet is not None
        assert packet.qos == 0
        assert packet.retain is False
        payload = json.loads(bytes(packet.data))
        assert payload["type"] == expected_type
        assert isinstance(payload["id"], str)
        received.append(payload)
        if expected_type == "otto_query":
            response: dict[str, object] = {
                "type": "otto_state",
                "id": payload["id"],
                "action_state": "idle",
                "current_action": None,
            }
        else:
            response = {
                "type": "otto_actions",
                "id": payload["id"],
                "actions": [
                    {"name": "device-1-only"},
                    {"name": "swing", "parameters": {"steps": "integer"}},
                ],
            }
        await _publish_device(client, provisioning, response)
    return received


async def _respond_to_action_and_stop(
    client: MQTTClient,
    provisioning: dict[str, object],
) -> list[dict[str, object]]:
    received: list[dict[str, object]] = []
    expected = ["otto_action", "stop"]
    action_state = "idle"
    current_action: str | None = None
    while expected:
        packet = await client.deliver_message(timeout_duration=2)
        assert packet is not None
        assert packet.qos == 0
        assert packet.retain is False
        payload = json.loads(bytes(packet.data))
        assert isinstance(payload["id"], str)
        if payload["type"] == "otto_query":
            await _publish_device(
                client,
                provisioning,
                {
                    "type": "otto_state",
                    "id": payload["id"],
                    "action_state": action_state,
                    "current_action": current_action,
                },
            )
            continue
        assert payload["type"] == expected.pop(0)
        received.append(payload)
        if payload["type"] == "otto_action":
            assert payload["action"] == "swing"
            assert payload["steps"] == 2
            action_state = "moving"
            current_action = "swing"
            await _publish_device(
                client,
                provisioning,
                {
                    "type": "otto_action_ack",
                    "id": payload["id"],
                    "ok": True,
                    "action": "swing",
                    "runtime": {
                        "otto": {"action": {"state": "moving", "name": "swing"}}
                    },
                },
            )
            await asyncio.sleep(0.02)
            action_state = "idle"
            current_action = None
            await _publish_device(
                client,
                provisioning,
                {
                    "type": "otto_state",
                    "id": payload["id"],
                    "action_state": "idle",
                    "current_action": None,
                },
            )
        else:
            action_state = "idle"
            current_action = "idle"
            await _publish_device(
                client,
                provisioning,
                {
                    "type": "otto_stop_ack",
                    "id": payload["id"],
                    "ok": True,
                    "runtime": {
                        "otto": {"action": {"state": "idle", "name": "idle"}}
                    },
                },
            )
    return received


async def _respond_to_one_action(
    client: MQTTClient,
    provisioning: dict[str, object],
) -> dict[str, object]:
    while True:
        packet = await client.deliver_message(timeout_duration=2)
        assert packet is not None
        assert packet.qos == 0
        assert packet.retain is False
        payload = json.loads(bytes(packet.data))
        if payload["type"] == "otto_query":
            await _publish_device(
                client,
                provisioning,
                {
                    "type": "otto_state",
                    "id": payload["id"],
                    "action_state": "idle",
                    "current_action": None,
                },
            )
            continue
        assert payload["type"] == "otto_action"
        await _publish_device(
            client,
            provisioning,
            {
                "type": "otto_action_ack",
                "id": payload["id"],
                "ok": True,
                "action": payload["action"],
                "runtime": {
                    "otto": {
                        "action": {"state": "moving", "name": payload["action"]}
                    }
                },
            },
        )
        await _publish_device(
            client,
            provisioning,
            {
                "type": "otto_state",
                "id": payload["id"],
                "action_state": "idle",
                "current_action": None,
            },
        )
        completed = payload
        while True:
            try:
                trailing = await client.deliver_message(timeout_duration=0.1)
            except TimeoutError:
                return completed
            assert trailing is not None
            assert trailing.qos == 0
            assert trailing.retain is False
            trailing_payload = json.loads(bytes(trailing.data))
            assert trailing_payload["type"] == "otto_query", "duplicate action was published"
            await _publish_device(
                client,
                provisioning,
                {
                    "type": "otto_state",
                    "id": trailing_payload["id"],
                    "action_state": "idle",
                    "current_action": None,
                },
            )


async def _wait_for_command(
    client: httpx.AsyncClient,
    command_id: str,
    status: str,
) -> dict[str, object]:
    deadline = asyncio.get_running_loop().time() + 3
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/api/v1/commands/{command_id}")
        if response.status_code == 200 and response.json()["status"] == status:
            return response.json()
        await asyncio.sleep(0.02)
    raise AssertionError(f"command {command_id} did not reach {status}")


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
            heartbeat_stale_seconds=2.0,
            heartbeat_offline_seconds=4.0,
            gateway_reconnect_seconds=0.1,
            query_timeout_seconds=0.2,
        ),
        dispatch=replace(
            loaded.dispatch,
            queue_size_per_device=4,
            ack_timeout_seconds=0.3,
            completion_timeout_seconds=0.8,
            state_query_interval_seconds=0.05,
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
                        "capabilities": {"actions": True, "state": True, "stop": True},
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

            responder = asyncio.create_task(
                _respond_to_read_only_verification(fake_clients[0], provisioned[0])
            )
            verified = await client.post("/api/v1/devices/aabbccddee01/verify")
            down_messages = await responder
            assert verified.status_code == 200
            assert verified.json()["passed"] is True
            assert verified.json()["action_state"] == "idle"
            assert verified.json()["actions_count"] == 2
            assert [item["type"] for item in down_messages] == [
                "otto_query",
                "otto_actions",
            ]
            assert down_messages[0]["id"] != down_messages[1]["id"]
            with pytest.raises(TimeoutError):
                await fake_clients[1].deliver_message(timeout_duration=0.1)

            action_responder = asyncio.create_task(
                _respond_to_action_and_stop(fake_clients[0], provisioned[0])
            )
            action_response = await client.post(
                "/api/v1/commands/action",
                json={
                    "device_id": "aabbccddee01",
                    "action": "swing",
                    "parameters": {"steps": 2},
                    "confirmation": True,
                },
            )
            assert action_response.status_code == 202
            action_id = action_response.json()["command_id"]
            completed_action = await _wait_for_command(client, action_id, "completed")
            assert [item["status"] for item in completed_action["history"]] == [
                "requested",
                "published",
                "accepted",
                "moving",
                "completed",
            ]

            stop_response = await client.post("/api/v1/devices/aabbccddee01/stop")
            assert stop_response.status_code == 202
            stop_id = stop_response.json()["command_id"]
            completed_stop = await _wait_for_command(client, stop_id, "completed")
            assert [item["status"] for item in completed_stop["history"]] == [
                "requested",
                "published",
                "accepted",
                "completed",
            ]
            action_down = await action_responder
            assert [item["type"] for item in action_down] == ["otto_action", "stop"]
            assert action_down[0]["id"] == action_id
            assert action_down[1]["id"] == stop_id
            with pytest.raises(TimeoutError):
                await fake_clients[1].deliver_message(timeout_duration=0.1)

            duplicate_responder = asyncio.create_task(
                _respond_to_one_action(fake_clients[0], provisioned[0])
            )
            duplicate = Message.create(
                topic="robot.action.requested",
                kind=MessageKind.COMMAND,
                source="runtime-test",
                target="device:aabbccddee01",
                message_id="duplicate-command-id",
                payload={
                    "device_id": "aabbccddee01",
                    "action": "swing",
                    "parameters": {"steps": 2},
                    "confirmation": True,
                },
            )
            await runtime.message_bus.publish(duplicate)
            await runtime.message_bus.publish(duplicate)
            duplicate_down = await duplicate_responder
            assert duplicate_down["id"] == "duplicate-command-id"
            await _wait_for_command(client, "duplicate-command-id", "completed")
            with pytest.raises(TimeoutError):
                await fake_clients[0].deliver_message(timeout_duration=0.1)

            await _publish_device(fake_clients[1], provisioned[1], {"type": "heartbeat"})
            timed_out = await client.post("/api/v1/devices/aabbccddee02/verify")
            assert timed_out.status_code == 200
            assert timed_out.json()["passed"] is False
            assert "timed out" in timed_out.json()["failure"]
            assert runtime.device_verifier.pending_count == 0

            events = await client.get("/api/v1/events", params={"limit": 256})
            event_topics = {item["event"]["topic"] for item in events.json()["items"]}
            assert "device.command.published" in event_topics
            assert "device.verification.completed" in event_topics
            assert "robot.action.accepted" in event_topics
            assert "robot.stop.accepted" in event_topics

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
        persisted_action = await database.fetch_command_record(action_id)
        persisted_stop = await database.fetch_command_record(stop_id)
        persisted_duplicate = await database.fetch_command_record("duplicate-command-id")
        assert persisted_action is not None and persisted_action["status"] == "completed"
        assert persisted_stop is not None and persisted_stop["status"] == "completed"
        assert persisted_duplicate is not None and persisted_duplicate["status"] == "completed"
        assert len(await database.fetch_command_results(action_id)) == 5
    finally:
        await database.close()
