from __future__ import annotations

import asyncio

import pytest

from otto_master.message_bus import MessageBus
from otto_master.messages import Message, MessageKind


def make_message(topic: str = "device.connected") -> Message:
    return Message.create(
        topic=topic,
        kind=MessageKind.EVENT,
        source="test",
        target="device:test",
        payload={"ok": True},
    )


@pytest.mark.asyncio
async def test_publish_delivers_to_multiple_subscribers() -> None:
    bus = MessageBus(queue_size=4)
    received: list[str] = []

    async def first(message: Message) -> None:
        received.append(f"first:{message.message_id}")

    def second(message: Message) -> None:
        received.append(f"second:{message.message_id}")

    await bus.subscribe("device.connected", first)
    await bus.subscribe("device.connected", second)
    await bus.start()
    message = make_message()
    await bus.publish(message)
    await bus.drain()

    assert sorted(received) == sorted([f"first:{message.message_id}", f"second:{message.message_id}"])
    assert bus.queue_depth == 0
    await bus.stop()


@pytest.mark.asyncio
async def test_subscriber_failure_is_isolated_and_observable() -> None:
    bus = MessageBus()
    received: list[str] = []

    async def failing(_: Message) -> None:
        raise RuntimeError("expected failure")

    async def healthy(message: Message) -> None:
        received.append(message.topic)

    failed_id = await bus.subscribe("device.connected", failing)
    await bus.subscribe("device.connected", healthy)
    await bus.start()
    message = make_message()
    await bus.publish(message)
    await bus.drain()

    assert received == ["device.connected"]
    assert len(bus.delivery_failures) == 1
    failure = bus.delivery_failures[0]
    assert failure.subscription_id == failed_id
    assert failure.message_id == message.message_id
    assert failure.error_type == "RuntimeError"
    await bus.stop()


@pytest.mark.asyncio
async def test_unsubscribe_and_lifecycle_are_repeatable() -> None:
    bus = MessageBus()
    calls = 0

    async def callback(_: Message) -> None:
        nonlocal calls
        calls += 1

    subscription_id = await bus.subscribe("device.connected", callback)
    await bus.start()
    await bus.start()
    await bus.publish(make_message())
    await bus.drain()
    assert calls == 1

    assert await bus.unsubscribe(subscription_id) is True
    assert await bus.unsubscribe(subscription_id) is False
    await bus.publish(make_message())
    await bus.drain()
    assert calls == 1

    await bus.stop()
    await bus.stop()
    await bus.start()
    await bus.stop()


@pytest.mark.asyncio
async def test_slow_subscriber_does_not_block_other_topics() -> None:
    bus = MessageBus()
    slow_started = asyncio.Event()
    release_slow = asyncio.Event()
    fast_received = asyncio.Event()

    async def slow(_: Message) -> None:
        slow_started.set()
        await release_slow.wait()

    async def fast(_: Message) -> None:
        fast_received.set()

    await bus.subscribe("device.connected", slow)
    await bus.subscribe("device.heartbeat.received", fast)
    await bus.start()
    await bus.publish(make_message("device.connected"))
    await bus.publish(make_message("device.heartbeat.received"))
    await asyncio.wait_for(slow_started.wait(), timeout=1)
    await asyncio.wait_for(fast_received.wait(), timeout=1)
    release_slow.set()
    await bus.stop()
