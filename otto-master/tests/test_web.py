from __future__ import annotations

import asyncio
import hashlib
import socket
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any

import httpx

from otto_master.config import RuntimeSecrets, load_config
from otto_master.dispatch.commands import DispatchRequestError
from otto_master.gateways.mqtt_broker import EmbeddedMqttBroker
from otto_master.gateways.web import EventHub, WebContext, create_app
from otto_master.message_bus import MessageBus
from otto_master.messages import Message, MessageKind
from otto_master.services.conversation_control import ConversationControlError
from otto_master.storage.database import Database


class FakeDeviceReader:
    def __init__(self) -> None:
        self.devices = [
            {
                "device_id": "aabbccddee01",
                "name": "EVA1",
                "status": "online",
                "transport": "mqtt",
            },
            {
                "device_id": "aabbccddee02",
                "name": "EVA2",
                "status": "stale",
                "transport": "mqtt",
            },
        ]

    async def list_devices(self) -> list[dict[str, Any]]:
        return list(self.devices)

    async def get_device(self, device_id: str) -> dict[str, Any] | None:
        return next(
            (item for item in self.devices if item["device_id"] == device_id),
            None,
        )

    async def get_actions(self, device_id: str) -> list[dict[str, Any]] | None:
        if await self.get_device(device_id) is None:
            return None
        return [{"name": "swing"}] if device_id.endswith("01") else [{"name": "walk"}]


class FakeDeviceVerifier:
    async def verify(self, device_id: str) -> dict[str, Any] | None:
        if device_id != "aabbccddee01":
            return None
        return {
            "verification_id": "verify-1",
            "device_id": device_id,
            "passed": True,
            "checks": [{"name": "state_query", "status": "pass"}],
        }


class FakeCommandDispatcher:
    def __init__(self) -> None:
        self.commands: dict[str, dict[str, Any]] = {}
        self.counter = 0
        self.active_actions = 0
        self.max_active_actions = 0

    async def submit_action(self, **values: Any) -> dict[str, Any]:
        self.active_actions += 1
        self.max_active_actions = max(self.max_active_actions, self.active_actions)
        try:
            await asyncio.sleep(0)
            if values["device_id"] == "aabbccddee99":
                raise DispatchRequestError("device_not_online", "device is not online")
            return self._create("action", values)
        finally:
            self.active_actions -= 1

    async def submit_stop(self, **values: Any) -> dict[str, Any]:
        return self._create("stop", values)

    async def stop_cluster(self, *, source: str = "webui") -> dict[str, Any]:
        del source
        return {"requested": 2, "accepted": 2, "items": []}

    async def get_command(self, command_id: str) -> dict[str, Any] | None:
        return self.commands.get(command_id)

    def _create(self, command_type: str, values: dict[str, Any]) -> dict[str, Any]:
        self.counter += 1
        command_id = f"command-{self.counter}"
        command = {
            "command_id": command_id,
            "status": "requested",
            "terminal": False,
            "payload": {
                "command_type": command_type,
                "device_id": values["device_id"],
            },
            "history": [{"status": "requested"}],
        }
        self.commands[command_id] = command
        return command


class FakeConversationControl:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.calls: list[dict[str, Any]] = []

    async def control(self, **values: Any) -> dict[str, Any]:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.calls.append(values)
        try:
            await asyncio.sleep(0)
            if values["device_id"] == "aabbccddee99":
                raise ConversationControlError("device_not_online", "device is not online")
            return {
                "device_id": values["device_id"],
                "command": values["command"],
                "command_id": f"conversation-{len(self.calls)}",
                "status": "accepted",
                "transport": "mqtt",
            }
        finally:
            self.active -= 1


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
        devices=None,
        verifier=None,
        dispatcher=None,
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
            assert 'id="quick-action-grid"' in root.text
            assert 'id="control-auth-badge"' in root.text
            assert 'id="control-status"' in root.text
            assert 'id="cluster-stop" class="danger" type="button" disabled' in root.text
            assert 'data-quick-action="walk"' in root.text
            app_js = await client.get("/assets/app.js")
            assert app_js.status_code == 200
            assert 'bootstrapLoopbackToken' in app_js.text
            assert 'fragment.get("console_token")' in app_js.text
            assert 'hasControlAuthorization' in app_js.text
            assert '控制命令未发送' in app_js.text
            assert "前进" in root.text and "后退" in root.text
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

            dns_rebinding = await client.put(
                "/api/v1/settings",
                json={"logging_level": "INFO"},
                headers={"Host": "attacker.invalid", "Origin": "http://attacker.invalid"},
            )
            assert dns_rebinding.status_code == 403
            assert dns_rebinding.json()["error"]["code"] == "origin_denied"
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

            private_conversations = await client.get("/api/v1/conversations")
            assert private_conversations.status_code == 401
            assert private_conversations.json()["error"]["code"] == "authentication_required"

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


async def test_device_read_apis_use_live_manager_snapshot(tmp_path: Path) -> None:
    context = await _context(_config(tmp_path))
    context.devices = FakeDeviceReader()
    app = create_app(context)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            listing = await client.get("/api/v1/devices", params={"limit": 1, "offset": 1})
            detail = await client.get("/api/v1/devices/aabbccddee01")
            actions = await client.get("/api/v1/devices/aabbccddee01/actions")
            missing = await client.get("/api/v1/devices/aabbccddee99/actions")

        assert listing.json()["total"] == 2
        assert listing.json()["items"][0]["device_id"] == "aabbccddee02"
        assert detail.json()["status"] == "online"
        assert actions.json() == {
            "device_id": "aabbccddee01",
            "items": [{"name": "swing"}],
            "count": 1,
        }
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "device_not_found"
    finally:
        await _close_context(context)


async def test_device_verify_api_uses_protected_verifier_and_returns_404(
    tmp_path: Path,
) -> None:
    context = await _context(_config(tmp_path))
    context.verifier = FakeDeviceVerifier()
    app = create_app(context)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            verified = await client.post("/api/v1/devices/aabbccddee01/verify")
            missing = await client.post("/api/v1/devices/aabbccddee99/verify")

        assert verified.status_code == 200
        assert verified.json()["passed"] is True
        assert verified.json()["checks"][0]["name"] == "state_query"
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "device_not_found"
    finally:
        await _close_context(context)


async def test_command_apis_delegate_to_dispatcher_and_return_persistent_state(
    tmp_path: Path,
) -> None:
    context = await _context(_config(tmp_path))
    context.dispatcher = FakeCommandDispatcher()
    app = create_app(context)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            action = await client.post(
                "/api/v1/commands/action",
                json={
                    "device_id": "aabbccddee01",
                    "action": "swing",
                    "parameters": {"steps": 2},
                    "confirmation": True,
                },
            )
            command_id = action.json()["command_id"]
            stored = await client.get(f"/api/v1/commands/{command_id}")
            missing = await client.get("/api/v1/commands/missing")
            stopped = await client.post("/api/v1/devices/aabbccddee01/stop")
            cluster = await client.post("/api/v1/cluster/stop")
            unavailable = await client.post(
                "/api/v1/commands/action",
                json={
                    "device_id": "aabbccddee99",
                    "action": "swing",
                    "parameters": {},
                    "confirmation": True,
                },
            )

        assert action.status_code == 202
        assert stored.status_code == 200
        assert stored.json()["history"] == [{"status": "requested"}]
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "command_not_found"
        assert stopped.status_code == 202
        assert stopped.json()["payload"]["command_type"] == "stop"
        assert cluster.status_code == 202
        assert cluster.json()["accepted"] == 2
        assert unavailable.status_code == 409
        assert unavailable.json()["error"]["code"] == "device_not_online"
    finally:
        await _close_context(context)


async def test_batch_command_apis_use_explicit_unique_targets_and_isolate_results(
    tmp_path: Path,
) -> None:
    context = await _context(_config(tmp_path))
    dispatcher = FakeCommandDispatcher()
    context.dispatcher = dispatcher
    app = create_app(context)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            action = await client.post(
                "/api/v1/commands/actions/batch",
                json={
                    "device_ids": ["aabbccddee01", "aabbccddee99"],
                    "action": "swing",
                    "parameters": {"steps": 2},
                    "confirmation": True,
                },
            )
            duplicate = await client.post(
                "/api/v1/commands/actions/batch",
                json={
                    "device_ids": ["aabbccddee01", "aabbccddee01"],
                    "action": "swing",
                    "confirmation": True,
                },
            )
            wildcard = await client.post(
                "/api/v1/commands/actions/batch",
                json={
                    "device_ids": ["*"],
                    "action": "swing",
                    "confirmation": True,
                },
            )
            unconfirmed = await client.post(
                "/api/v1/commands/actions/batch",
                json={
                    "device_ids": ["aabbccddee01"],
                    "action": "swing",
                    "confirmation": False,
                },
            )
            stopped = await client.post(
                "/api/v1/commands/stops/batch",
                json={
                    "device_ids": ["aabbccddee01", "aabbccddee02"],
                    "confirmation": True,
                },
            )

        assert action.status_code == 202
        assert action.json()["requested"] == 2
        assert action.json()["accepted"] == 1
        assert action.json()["failed"] == 1
        assert action.json()["items"][0]["device_id"] == "aabbccddee01"
        assert action.json()["items"][0]["accepted"] is True
        assert action.json()["items"][1]["error"]["code"] == "device_not_online"
        assert dispatcher.max_active_actions == 2
        assert duplicate.status_code == 422
        assert duplicate.json()["error"]["code"] == "duplicate_device_ids"
        assert wildcard.status_code == 422
        assert wildcard.json()["error"]["code"] == "invalid_device_ids"
        assert unconfirmed.status_code == 422
        assert unconfirmed.json()["error"]["code"] == "confirmation_required"
        assert stopped.status_code == 202
        assert stopped.json()["accepted"] == 2
        assert [item["device_id"] for item in stopped.json()["items"]] == [
            "aabbccddee01",
            "aabbccddee02",
        ]
    finally:
        await _close_context(context)


async def test_batch_conversation_api_uses_explicit_targets_ack_and_result_isolation(
    tmp_path: Path,
) -> None:
    context = await _context(_config(tmp_path))
    controller = FakeConversationControl()
    context.conversation_control = controller
    app = create_app(context)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            started = await client.post(
                "/api/v1/commands/conversations/batch",
                json={
                    "device_ids": ["aabbccddee01", "aabbccddee99"],
                    "command": "start",
                    "confirmation": True,
                },
                headers={"X-Correlation-ID": "batch-conversation-1"},
            )
            duplicate = await client.post(
                "/api/v1/commands/conversations/batch",
                json={
                    "device_ids": ["aabbccddee01", "aabbccddee01"],
                    "command": "stop",
                    "confirmation": True,
                },
            )
            wildcard = await client.post(
                "/api/v1/commands/conversations/batch",
                json={
                    "device_ids": ["*"],
                    "command": "start",
                    "confirmation": True,
                },
            )
            unconfirmed = await client.post(
                "/api/v1/commands/conversations/batch",
                json={
                    "device_ids": ["aabbccddee01"],
                    "command": "start",
                    "confirmation": False,
                },
            )
            invalid_command = await client.post(
                "/api/v1/commands/conversations/batch",
                json={
                    "device_ids": ["aabbccddee01"],
                    "command": "toggle",
                    "confirmation": True,
                },
            )
            stopped = await client.post(
                "/api/v1/commands/conversations/batch",
                json={
                    "device_ids": ["aabbccddee01", "aabbccddee02"],
                    "command": "stop",
                    "confirmation": True,
                },
            )

        assert started.status_code == 202
        assert started.json()["command"] == "start"
        assert started.json()["requested"] == 2
        assert started.json()["accepted"] == 1
        assert started.json()["failed"] == 1
        assert started.json()["items"][0]["status"] == "accepted"
        assert started.json()["items"][1]["error"]["code"] == "device_not_online"
        assert controller.max_active == 2
        assert {call["correlation_id"] for call in controller.calls[:2]} == {
            "batch-conversation-1"
        }
        assert duplicate.status_code == 422
        assert duplicate.json()["error"]["code"] == "duplicate_device_ids"
        assert wildcard.status_code == 422
        assert wildcard.json()["error"]["code"] == "invalid_device_ids"
        assert unconfirmed.status_code == 422
        assert unconfirmed.json()["error"]["code"] == "confirmation_required"
        assert invalid_command.status_code == 422
        assert invalid_command.json()["error"]["code"] == "validation_error"
        assert stopped.status_code == 202
        assert stopped.json()["accepted"] == 2
        assert [item["device_id"] for item in stopped.json()["items"]] == [
            "aabbccddee01",
            "aabbccddee02",
        ]
    finally:
        await _close_context(context)


async def test_conversation_snapshot_is_per_device_redacted_and_rejects_stale_session(
    tmp_path: Path,
) -> None:
    context = await _context(_config(tmp_path))
    context.devices = FakeDeviceReader()
    messages = [
        Message.create(
            topic="audio.input.started",
            kind=MessageKind.EVENT,
            source="device:e1",
            target="service:asr",
            payload={
                "device_id": "aabbccddee01",
                "session_id": "session-eva1",
                "utterance_id": "utterance-eva1",
            },
        ),
        Message.create(
            topic="voice.session.state.changed",
            kind=MessageKind.STATE,
            source="service:wake_gate",
            target="device:aabbccddee01",
            payload={"device_id": "aabbccddee01", "state": "answering"},
        ),
        Message.create(
            topic="voice.transcription.displayed",
            kind=MessageKind.RESULT,
            source="service:wake_gate",
            target="device:aabbccddee01",
            payload={
                "device_id": "aabbccddee01",
                "session_id": "session-eva1",
                "utterance_id": "utterance-eva1",
                "text": "请前进",
                "token": "must-not-leak",
            },
        ),
        Message.create(
            topic="tts.synthesis.started",
            kind=MessageKind.EVENT,
            source="service:tts",
            target="device:aabbccddee01",
            correlation_id="utterance-eva1",
            payload={
                "device_id": "aabbccddee01",
                "session_id": "session-eva1",
                "text": "奶龙在呢！",
            },
        ),
        Message.create(
            topic="voice.tool.completed",
            kind=MessageKind.RESULT,
            source="service:wake_gate",
            target="device:aabbccddee01",
            payload={
                "device_id": "aabbccddee01",
                "session_id": "session-eva1",
                "utterance_id": "utterance-eva1",
                "tool_name": "self_otto_walk_forward",
                "action": "walk",
                "command_id": "command-eva1",
                "status": "completed",
            },
        ),
        Message.create(
            topic="audio.input.started",
            kind=MessageKind.EVENT,
            source="device:e2",
            target="service:asr",
            payload={
                "device_id": "aabbccddee02",
                "session_id": "session-eva2-new",
                "utterance_id": "utterance-eva2-new",
            },
        ),
        Message.create(
            topic="voice.transcription.completed",
            kind=MessageKind.RESULT,
            source="service:asr",
            target="device:aabbccddee02",
            payload={
                "device_id": "aabbccddee02",
                "session_id": "session-eva2-old",
                "utterance_id": "utterance-eva2-old",
                "text": "迟到旧文本",
            },
        ),
    ]
    for message in messages:
        await context.events.publish(message)

    app = create_app(context)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/v1/conversations")

        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 2
        lanes = {item["device_id"]: item for item in body["items"]}
        assert lanes["aabbccddee01"]["name"] == "EVA1"
        assert lanes["aabbccddee01"]["state"] == "answering"
        assert lanes["aabbccddee01"]["user_text"] == "请前进"
        assert lanes["aabbccddee01"]["assistant_text"] == "奶龙在呢！"
        assert lanes["aabbccddee01"]["tool_status"] == "completed"
        assert lanes["aabbccddee01"]["action"] == "walk"
        assert lanes["aabbccddee02"]["session_id"] == "session-eva2-new"
        assert lanes["aabbccddee02"]["user_text"] == ""
        assert "must-not-leak" not in response.text

        await context.events.publish(
            Message.create(
                topic="audio.input.started",
                kind=MessageKind.EVENT,
                source="device:e1",
                target="service:asr",
                payload={
                    "device_id": "aabbccddee01",
                    "session_id": "session-eva1",
                    "utterance_id": "utterance-eva1-next",
                },
            )
        )
        retained = {
            item["device_id"]: item for item in await context.events.conversation_snapshot()
        }
        assert retained["aabbccddee01"]["user_text"] == "请前进"
        assert retained["aabbccddee01"]["assistant_text"] == "奶龙在呢！"

        await context.events.publish(
            Message.create(
                topic="voice.transcription.partial",
                kind=MessageKind.EVENT,
                source="service:asr",
                target="device:aabbccddee01",
                payload={
                    "device_id": "aabbccddee01",
                    "session_id": "session-eva1",
                    "utterance_id": "utterance-eva1-next",
                    "text": "下一轮",
                },
            )
        )
        speaking = {
            item["device_id"]: item for item in await context.events.conversation_snapshot()
        }
        assert speaking["aabbccddee01"]["user_text"] == ""
        assert speaking["aabbccddee01"]["assistant_text"] == ""
        assert speaking["aabbccddee01"]["user_partial"] == "下一轮"
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

    first_page = await hub.read(after=1, stream_id=hub.stream_id, limit=1)
    assert first_page["cursor"] == 2
    assert first_page["latest_cursor"] == 4
    assert first_page["has_more"] is True
    second_page = await hub.read(
        after=first_page["cursor"],
        stream_id=hub.stream_id,
        limit=2,
    )
    assert [item["cursor"] for item in second_page["items"]] == [3, 4]
    assert second_page["cursor"] == 4
    assert second_page["has_more"] is False


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
            assert body["tcp"]["protocol"] == "otto-master/1"
            assert body["tcp"]["token"] == body["mqtt"]["password"]
            assert body["websocket"]["protocol_version"] == 1
            assert body["websocket"]["token"] == body["mqtt"]["password"]
            assert provisioned.headers["cache-control"] == "no-store"

            settings = await client.get("/api/v1/settings")
            manifest = await client.get("/api/v1/ota/manifest")
            assert body["mqtt"]["password"] not in settings.text
            assert body["mqtt"]["password"] not in manifest.text
    finally:
        await _close_context(context)
