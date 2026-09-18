from __future__ import annotations

import hashlib
import socket
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any

import httpx

from otto_master.config import RuntimeSecrets, load_config
from otto_master.gateways.mqtt_broker import EmbeddedMqttBroker
from otto_master.gateways.web import EventHub, WebContext, create_app
from otto_master.message_bus import MessageBus
from otto_master.messages import Message, MessageKind
from otto_master.storage.database import Database


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _config(
    tmp_path: Path,
    *,
    host: str = "127.0.0.1",
    console_token: str | None = None,
    provisioning_token: str | None = None,
    mqtt_enabled: bool = False,
) -> Any:
    loaded = load_config()
    return replace(
        loaded,
        config_path=tmp_path / "config.yaml",
        server=replace(
            loaded.server,
            enabled=False,
            host=host,
            port=8080,
            allowed_origins=("http://testserver",),
        ),
        mqtt=replace(
            loaded.mqtt,
            enabled=mqtt_enabled,
            host="127.0.0.1",
            port=_free_port(),
            credentials_path=".local-secrets/test-mqtt.json",
        ),
        discovery=replace(loaded.discovery, enabled=False),
        ota=replace(
            loaded.ota,
            firmware_path="firmware/test.bin",
            firmware_version="2.0.5",
            target_hardware="eva-v1",
        ),
        secrets=RuntimeSecrets(
            console_token=console_token,
            mqtt_master_password="master-test-password",
            provisioning_token=provisioning_token,
        ),
    )


async def _context(config: Any) -> WebContext:
    database = await Database.open(config.resolve_path(config.database.path))
    bus = MessageBus()
    await bus.start()
    broker = EmbeddedMqttBroker(
        config.mqtt,
        config.resolve_path(config.mqtt.credentials_path),
        configured_master_password=config.secrets.mqtt_master_password,
        public_host=config.discovery.hostname,
    )
    if config.mqtt.enabled:
        await broker.start()
    events = EventHub(history_size=8, subscriber_queue_size=2)

    def components() -> dict[str, dict[str, Any]]:
        return {
            "sqlite": {"enabled": True, "healthy": database.is_open, "state": "running"},
            "message_bus": {"enabled": True, "healthy": bus.running, "state": "running"},
            "mqtt": broker.status(),
        }

    return WebContext(
        config=config,
        database=database,
        message_bus=bus,
        mqtt_broker=broker,
        events=events,
        component_status=components,
        started_at=datetime.now(UTC),
        started_monotonic=monotonic(),
    )


async def _close_context(context: WebContext) -> None:
    await context.message_bus.stop()
    await context.mqtt_broker.shutdown()
    await context.database.close()


async def test_control_plane_static_health_ota_and_errors(tmp_path: Path) -> None:
    config = _config(tmp_path)
    firmware_bytes = b"otto-firmware-test\x00\x01"
    firmware_path = config.resolve_path(config.ota.firmware_path)
    firmware_path.parent.mkdir(parents=True)
    firmware_path.write_bytes(firmware_bytes)
    context = await _context(config)
    app = create_app(context)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            root = await client.get("/")
            assert root.status_code == 200
            assert "Otto Master" in root.text
            assert "frame-ancestors 'none'" in root.headers["content-security-policy"]

            health = await client.get("/api/v1/health", headers={"X-Correlation-ID": "test-cid"})
            assert health.status_code == 200
            assert health.headers["x-correlation-id"] == "test-cid"
            assert health.json()["components"]["sqlite"]["healthy"] is True

            firmware = await client.get("/api/v1/firmware")
            assert firmware.json()["available"] is True
            assert firmware.json()["size"] == len(firmware_bytes)
            assert firmware.json()["sha256"] == hashlib.sha256(firmware_bytes).hexdigest()
            download = await client.get("/api/v1/firmware/download")
            assert download.content == firmware_bytes

            firmware_path.unlink()
            missing = await client.get("/api/v1/firmware/download")
            assert missing.status_code == 404
            assert missing.json()["error"]["code"] == "firmware_not_found"
            assert missing.json()["error"]["correlation_id"]

            manifest = await client.get("/api/v1/ota/manifest")
            assert "password" not in manifest.text.lower()
            assert manifest.json()["mqtt"]["publish_topic_template"].endswith("/up")
    finally:
        await _close_context(context)


async def test_mutations_require_auth_and_reject_arbitrary_fields(tmp_path: Path) -> None:
    config = _config(tmp_path, host="0.0.0.0", console_token="console-secret")
    context = await _context(config)
    await context.database.save_setting("mqtt.password", "must-not-leak")
    app = create_app(context)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            unauthenticated = await client.put(
                "/api/v1/settings",
                json={"logging_level": "DEBUG"},
                headers={"Origin": "http://testserver"},
            )
            assert unauthenticated.status_code == 401
            assert unauthenticated.json()["error"]["code"] == "authentication_required"

            denied_origin = await client.put(
                "/api/v1/settings",
                json={"logging_level": "DEBUG"},
                headers={
                    "Authorization": "Bearer console-secret",
                    "Origin": "http://attacker.invalid",
                },
            )
            assert denied_origin.status_code == 403
            assert denied_origin.json()["error"]["code"] == "origin_denied"

            authenticated = await client.put(
                "/api/v1/settings",
                json={"logging_level": "DEBUG"},
                headers={
                    "Authorization": "Bearer console-secret",
                    "Origin": "http://testserver",
                },
            )
            assert authenticated.status_code == 200
            assert authenticated.json()["effect"] == "server_restart_required"
            safe_settings = await client.get("/api/v1/settings")
            assert "must-not-leak" not in safe_settings.text

            arbitrary_topic = await client.post(
                "/api/v1/commands/action",
                json={
                    "device_id": "aabbccddeeff",
                    "action": "swing",
                    "parameters": {},
                    "topic": "otto/v1/devices/other/down",
                },
                headers={"Authorization": "Bearer console-secret"},
            )
            assert arbitrary_topic.status_code == 422
            assert arbitrary_topic.json()["error"]["code"] == "validation_error"

            valid_but_not_ready = await client.post(
                "/api/v1/commands/action",
                json={
                    "device_id": "aabbccddeeff",
                    "action": "swing",
                    "parameters": {},
                },
                headers={"Authorization": "Bearer console-secret"},
            )
            assert valid_but_not_ready.status_code == 503
            assert valid_but_not_ready.json()["error"]["code"] == "component_not_ready"
    finally:
        await _close_context(context)


async def test_event_cursor_resume_redaction_and_slow_client_isolation() -> None:
    hub = EventHub(history_size=3, subscriber_queue_size=1)
    first = Message.create(
        topic="device.connected",
        kind=MessageKind.EVENT,
        source="test",
        target="device:aabbccddeeff",
        payload={"name": "EVA1", "token": "must-not-leak"},
    )
    await hub.publish(first)
    initial = await hub.read(limit=10)
    assert initial["items"][0]["event"]["payload"]["token"] == "[REDACTED]"

    subscription = await hub.subscribe(after=1, stream_id=hub.stream_id)
    assert subscription.initial_frame["resumed"] is True
    assert subscription.initial_frame["events"] == []

    for index in range(3):
        await hub.publish(
            Message.create(
                topic="device.heartbeat.received",
                kind=MessageKind.EVENT,
                source="test",
                target="device:aabbccddeeff",
                payload={"index": index},
            )
        )

    backpressure = subscription.queue.get_nowait()
    assert backpressure["type"] == "resync_required"
    assert backpressure["reason"] == "subscriber_backpressure"
    assert hub.cursor == 4
    await hub.unsubscribe(subscription.subscription_id)

    expired = await hub.read(after=0, stream_id=hub.stream_id, limit=10)
    assert expired["resumed"] is False
    assert expired["resync_reason"] == "cursor_expired"


async def test_provisioning_returns_scoped_credentials_only_on_protected_endpoint(
    tmp_path: Path,
) -> None:
    config = _config(
        tmp_path,
        host="0.0.0.0",
        provisioning_token="provision-secret",
        mqtt_enabled=True,
    )
    context = await _context(config)
    app = create_app(context)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            blocked = await client.post(
                "/api/v1/ota/provision",
                json={"mac": "aa:bb:cc:dd:ee:ff"},
            )
            assert blocked.status_code == 401

            provisioned = await client.post(
                "/api/v1/ota/provision",
                json={"mac": "aa:bb:cc:dd:ee:ff", "name": "EVA1"},
                headers={"X-Otto-Provisioning-Token": "provision-secret"},
            )
            assert provisioned.status_code == 200
            body = provisioned.json()
            assert body["device_id"] == "aabbccddeeff"
            assert body["mqtt"]["publish_topic"] == "otto/v1/devices/aabbccddeeff/up"
            assert body["mqtt"]["subscribe_topic"] == "otto/v1/devices/aabbccddeeff/down"
            assert body["mqtt"]["password"]
            assert provisioned.headers["cache-control"] == "no-store"

            settings = await client.get("/api/v1/settings")
            manifest = await client.get("/api/v1/ota/manifest")
            assert body["mqtt"]["password"] not in settings.text
            assert body["mqtt"]["password"] not in manifest.text
    finally:
        await _close_context(context)
