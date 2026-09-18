from __future__ import annotations

import os
import socket
from pathlib import Path
from urllib.parse import quote

import pytest
from amqtt.client import ConnectError, MQTTClient  # type: ignore[import-untyped]

from otto_master.config import MqttConfig
from otto_master.gateways.mqtt_broker import (
    BrokerState,
    DeviceProvisioning,
    EmbeddedMqttBroker,
    MqttCredential,
    normalize_device_id,
)


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _config(port: int) -> MqttConfig:
    return MqttConfig(
        enabled=True,
        host="127.0.0.1",
        port=port,
        max_connections=20,
        credentials_path="credentials.json",
        master_username="otto-master",
        master_password_env="OTTO_MQTT_MASTER_PASSWORD",
        heartbeat_stale_seconds=15,
        heartbeat_offline_seconds=30,
        gateway_reconnect_seconds=2,
        query_timeout_seconds=3,
    )


async def _connect(credential: MqttCredential | DeviceProvisioning, port: int) -> MQTTClient:
    username = credential.username
    password = credential.password
    client_id = credential.client_id
    client = MQTTClient(
        client_id=client_id,
        config={"auto_reconnect": False, "reconnect_retries": 0, "plugins": {}},
    )
    uri = (
        f"mqtt://{quote(username, safe='')}:{quote(password, safe='')}"
        f"@127.0.0.1:{port}/"
    )
    await client.connect(uri)
    return client


def test_device_id_is_mac_derived_and_strict() -> None:
    assert normalize_device_id("AA:BB:CC:DD:EE:FF") == "aabbccddeeff"
    assert normalize_device_id("aa-bb-cc-dd-ee-ff") == "aabbccddeeff"
    with pytest.raises(ValueError):
        normalize_device_id("EVA1")


@pytest.mark.asyncio
async def test_broker_rejects_anonymous_and_enforces_per_device_acl(tmp_path: Path) -> None:
    port = _free_port()
    broker = EmbeddedMqttBroker(
        _config(port),
        tmp_path / "credentials.json",
        configured_master_password="master-test-password",
    )
    clients: list[MQTTClient] = []
    await broker.start()
    try:
        eva1 = await broker.provision_device("aa:bb:cc:dd:ee:01")
        eva2 = await broker.provision_device("aa:bb:cc:dd:ee:02")
        master_credential = await broker.credentials.master_credential()

        anonymous = MQTTClient(
            client_id="anonymous-test",
            config={"auto_reconnect": False, "reconnect_retries": 0, "plugins": {}},
        )
        with pytest.raises(ConnectError):
            await anonymous.connect(f"mqtt://127.0.0.1:{port}/")

        wrong_identity = MQTTClient(
            client_id="not-eva1",
            config={"auto_reconnect": False, "reconnect_retries": 0, "plugins": {}},
        )
        wrong_uri = (
            f"mqtt://{quote(eva1.username)}:{quote(eva1.password)}@127.0.0.1:{port}/"
        )
        with pytest.raises(ConnectError):
            await wrong_identity.connect(wrong_uri)

        master = await _connect(master_credential, port)
        client1 = await _connect(eva1, port)
        client2 = await _connect(eva2, port)
        clients.extend([client1, client2, master])

        assert await master.subscribe([("otto/v1/devices/+/up", 0)]) == [0]
        assert await client1.subscribe([(eva1.subscribe_topic, 0)]) == [0]
        assert await client1.subscribe([(eva2.subscribe_topic, 0)]) == [128]

        await client1.publish(eva1.publish_topic, b"eva1-up", qos=0, retain=False)
        received = await master.deliver_message(timeout_duration=2)
        assert received is not None
        assert received.topic == eva1.publish_topic
        assert bytes(received.data) == b"eva1-up"

        await client1.publish(eva2.publish_topic, b"cross-device", qos=0, retain=False)
        with pytest.raises(TimeoutError):
            await master.deliver_message(timeout_duration=0.2)

        await master.publish(eva1.subscribe_topic, b"master-down", qos=0, retain=False)
        down = await client1.deliver_message(timeout_duration=2)
        assert down is not None
        assert down.topic == eva1.subscribe_topic
        assert bytes(down.data) == b"master-down"

        assert broker.status()["anonymous"] is False
        assert broker.status()["authentication"] == "required"
        assert broker.status()["connected_clients"] == 3
        assert "password" not in str(broker.status()).lower()
    finally:
        for client in clients:
            await client.disconnect()
        await broker.shutdown()

    assert broker.state is BrokerState.STOPPED
    assert broker.status()["connected_clients"] == 0


@pytest.mark.asyncio
async def test_credentials_are_stable_and_not_exposed_by_repr(tmp_path: Path) -> None:
    port = _free_port()
    path = tmp_path / "credentials.json"
    broker = EmbeddedMqttBroker(
        _config(port),
        path,
        configured_master_password="master-test-password",
    )
    await broker.start()
    first = await broker.provision_device("aa:bb:cc:dd:ee:ff")
    second = await broker.provision_device("aabbccddeeff")
    assert broker.credentials.authenticate_device_token("aa:bb:cc:dd:ee:ff", first.password)
    assert not broker.credentials.authenticate_device_token("aabbccddeeff", "wrong-token")
    assert broker.credentials.expected_device_client_id("aabbccddeeff") == first.client_id
    await broker.shutdown()

    assert first == second
    assert first.password not in repr(first)
    assert path.is_file()
    if os.name != "nt" and path.stat().st_mode & 0o077:
        pytest.fail("credential store must not be group/world readable")

    restarted = EmbeddedMqttBroker(
        _config(port),
        path,
        configured_master_password="master-test-password",
    )
    await restarted.start()
    try:
        restored = await restarted.provision_device("aa:bb:cc:dd:ee:ff")
        assert restored.password == first.password
    finally:
        await restarted.shutdown()
