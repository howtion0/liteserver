from __future__ import annotations

import asyncio
from typing import Any

import pytest

from otto_master.message_bus import MessageBus
from otto_master.messages import Message, MessageKind
from otto_master.services.conversation_control import (
    ConversationControlError,
    ConversationControlService,
)


class FakeDevices:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {
            "aabbccddee01": {
                "device_id": "aabbccddee01",
                "status": "online",
                "transport": "mqtt",
                "capabilities": {"conversation_control": True},
            },
            "aabbccddee02": {
                "device_id": "aabbccddee02",
                "status": "online",
                "transport": "mqtt",
                "capabilities": {"conversation_control": True},
            },
            "aabbccddee03": {
                "device_id": "aabbccddee03",
                "status": "offline",
                "transport": "mqtt",
                "capabilities": {"conversation_control": True},
            },
            "aabbccddee04": {
                "device_id": "aabbccddee04",
                "status": "online",
                "transport": "mqtt",
                "capabilities": {},
            },
        }

    async def get_device(self, device_id: str) -> dict[str, Any] | None:
        return self.items.get(device_id)


async def _service(
    *,
    timeout_seconds: float = 0.2,
) -> tuple[MessageBus, ConversationControlService]:
    bus = MessageBus()
    await bus.start()
    service = ConversationControlService(
        bus,
        FakeDevices(),  # type: ignore[arg-type]
        timeout_seconds=timeout_seconds,
    )
    await service.start()
    return bus, service


async def _close(bus: MessageBus, service: ConversationControlService) -> None:
    await service.shutdown()
    await bus.stop()


async def test_control_waits_for_matching_device_transport_command_and_id() -> None:
    bus, service = await _service()
    requests: asyncio.Queue[Message] = asyncio.Queue()
    subscription = await bus.subscribe(
        "device.conversation.start.requested",
        requests.put,
    )
    try:
        control = asyncio.create_task(
            service.control(
                device_id="AA:BB:CC:DD:EE:01",
                command="start",
                correlation_id="web-batch-1",
            )
        )
        request = await asyncio.wait_for(requests.get(), timeout=0.2)
        assert request.target == "device:aabbccddee01"
        assert request.correlation_id == request.payload["command_id"]
        assert request.payload["request_correlation_id"] == "web-batch-1"

        for target, transport, command in (
            ("device:aabbccddee02", "mqtt", "start"),
            ("device:aabbccddee01", "websocket", "start"),
            ("device:aabbccddee01", "mqtt", "stop"),
        ):
            await bus.publish(
                Message.create(
                    topic="device.conversation.control.accepted",
                    kind=MessageKind.RESULT,
                    source="device_mqtt",
                    target=target,
                    correlation_id=request.message_id,
                    payload={
                        "device_id": target.removeprefix("device:"),
                        "transport": transport,
                        "command": command,
                        "accepted": True,
                    },
                )
            )
        await asyncio.sleep(0)
        assert not control.done()

        await bus.publish(
            Message.create(
                topic="device.conversation.control.accepted",
                kind=MessageKind.RESULT,
                source="device_mqtt",
                target=request.target,
                correlation_id=request.message_id,
                payload={
                    "device_id": "aabbccddee01",
                    "transport": "mqtt",
                    "command": "start",
                    "accepted": True,
                },
            )
        )
        result = await asyncio.wait_for(control, timeout=0.2)
        assert result["device_id"] == "aabbccddee01"
        assert result["command"] == "start"
        assert result["status"] == "accepted"
        assert service.status()["accepted"] == 1
        assert service.status()["pending"] == 0
    finally:
        await bus.unsubscribe(subscription)
        await _close(bus, service)


async def test_controls_two_devices_concurrently_without_cross_talk() -> None:
    bus, service = await _service()
    requests: asyncio.Queue[Message] = asyncio.Queue()
    subscription = await bus.subscribe(
        "device.conversation.start.requested",
        requests.put,
    )
    try:
        tasks = [
            asyncio.create_task(service.control(device_id=device_id, command="start"))
            for device_id in ("aabbccddee01", "aabbccddee02")
        ]
        outbound = [await asyncio.wait_for(requests.get(), timeout=0.2) for _ in tasks]
        assert {item.target for item in outbound} == {
            "device:aabbccddee01",
            "device:aabbccddee02",
        }
        for request in reversed(outbound):
            await bus.publish(
                Message.create(
                    topic="device.conversation.control.accepted",
                    kind=MessageKind.RESULT,
                    source="device_mqtt",
                    target=request.target,
                    correlation_id=request.message_id,
                    payload={
                        "device_id": request.target.removeprefix("device:"),
                        "transport": "mqtt",
                        "command": "start",
                        "accepted": True,
                    },
                )
            )
        results = await asyncio.gather(*tasks)
        assert {item["device_id"] for item in results} == {
            "aabbccddee01",
            "aabbccddee02",
        }
        assert service.status()["accepted"] == 2
    finally:
        await bus.unsubscribe(subscription)
        await _close(bus, service)


@pytest.mark.parametrize(
    ("device_id", "code"),
    [
        ("aabbccddee99", "device_not_found"),
        ("aabbccddee03", "device_not_online"),
        ("aabbccddee04", "capability_missing"),
    ],
)
async def test_control_fails_closed_during_preflight(device_id: str, code: str) -> None:
    bus, service = await _service()
    try:
        with pytest.raises(ConversationControlError) as captured:
            await service.control(device_id=device_id, command="start")
        assert captured.value.code == code
        assert service.status()["pending"] == 0
    finally:
        await _close(bus, service)


async def test_control_rejection_and_timeout_are_stable_and_leave_no_pending_request() -> None:
    bus, service = await _service(timeout_seconds=0.03)
    requests: asyncio.Queue[Message] = asyncio.Queue()
    subscription = await bus.subscribe(
        "device.conversation.stop.requested",
        requests.put,
    )
    try:
        rejected = asyncio.create_task(
            service.control(device_id="aabbccddee01", command="stop")
        )
        request = await asyncio.wait_for(requests.get(), timeout=0.2)
        await bus.publish(
            Message.create(
                topic="device.conversation.control.failed",
                kind=MessageKind.RESULT,
                source="device_mqtt",
                target=request.target,
                correlation_id=request.message_id,
                payload={
                    "device_id": "aabbccddee01",
                    "transport": "mqtt",
                    "command": "stop",
                    "accepted": False,
                    "error": "device_busy",
                },
            )
        )
        with pytest.raises(ConversationControlError) as captured:
            await rejected
        assert captured.value.code == "conversation_rejected"

        with pytest.raises(ConversationControlError) as timed_out:
            await service.control(device_id="aabbccddee01", command="stop")
        assert timed_out.value.code == "conversation_control_timeout"
        assert service.status()["pending"] == 0
        assert service.status()["failed"] == 2
        assert service.status()["timed_out"] == 1
    finally:
        await bus.unsubscribe(subscription)
        await _close(bus, service)
