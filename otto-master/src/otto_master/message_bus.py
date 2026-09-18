"""In-process asynchronous message bus."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import uuid4

from .messages import Message, validate_topic

Subscriber = Callable[[Message], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class DeliveryFailure:
    subscription_id: str
    topic: str
    message_id: str
    error_type: str
    error_message: str


@dataclass(frozen=True, slots=True)
class _Subscription:
    subscription_id: str
    topic: str
    callback: Subscriber


class MessageBus:
    """A bounded exact-topic bus with isolated subscriber delivery."""

    def __init__(self, queue_size: int = 1024, *, logger: logging.Logger | None = None) -> None:
        if queue_size < 1:
            raise ValueError("queue_size must be at least 1")
        self._queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=queue_size)
        self._subscriptions: dict[str, _Subscription] = {}
        self._delivery_tasks: set[asyncio.Task[None]] = set()
        self._delivery_failures: list[DeliveryFailure] = []
        self._worker: asyncio.Task[None] | None = None
        self._running = False
        self._logger = logger or logging.getLogger("otto_master.message_bus")

    @property
    def running(self) -> bool:
        return self._running

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def subscription_count(self) -> int:
        return len(self._subscriptions)

    @property
    def delivery_failures(self) -> tuple[DeliveryFailure, ...]:
        return tuple(self._delivery_failures)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker = asyncio.create_task(self._run(), name="otto-message-bus")
        self._logger.info("message_bus_started", extra={"event": "message_bus_started"})

    async def stop(self) -> None:
        if not self._running:
            return
        await self.drain()
        self._running = False
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        self._logger.info("message_bus_stopped", extra={"event": "message_bus_stopped"})

    async def subscribe(self, topic: str, callback: Subscriber) -> str:
        validate_topic(topic)
        if not callable(callback):
            raise TypeError("callback must be callable")
        subscription_id = f"sub-{uuid4()}"
        self._subscriptions[subscription_id] = _Subscription(subscription_id, topic, callback)
        return subscription_id

    async def unsubscribe(self, subscription_id: str) -> bool:
        return self._subscriptions.pop(subscription_id, None) is not None

    async def publish(self, message: Message) -> None:
        if not self._running:
            raise RuntimeError("message bus is not running")
        if not isinstance(message, Message):
            raise TypeError("message must be a Message")
        await self._queue.put(message)

    async def drain(self) -> None:
        """Wait until queued messages and all currently scheduled deliveries finish."""

        while True:
            await self._queue.join()
            completed = {task for task in self._delivery_tasks if task.done()}
            self._delivery_tasks.difference_update(completed)
            deliveries = tuple(self._delivery_tasks)
            if not deliveries:
                return
            await asyncio.gather(*deliveries, return_exceptions=True)

    def clear_delivery_failures(self) -> None:
        self._delivery_failures.clear()

    async def _run(self) -> None:
        while True:
            message = await self._queue.get()
            try:
                subscriptions = tuple(
                    subscription
                    for subscription in self._subscriptions.values()
                    if subscription.topic == message.topic
                )
                for subscription in subscriptions:
                    task = asyncio.create_task(
                        self._deliver(subscription, message),
                        name=f"otto-delivery-{subscription.subscription_id}",
                    )
                    self._delivery_tasks.add(task)
                    task.add_done_callback(self._delivery_tasks.discard)
            finally:
                self._queue.task_done()

    async def _deliver(self, subscription: _Subscription, message: Message) -> None:
        try:
            result = subscription.callback(message)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            failure = DeliveryFailure(
                subscription_id=subscription.subscription_id,
                topic=message.topic,
                message_id=message.message_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            self._delivery_failures.append(failure)
            self._logger.exception(
                "message_delivery_failed",
                extra={
                    "event": "message_delivery_failed",
                    "subscription_id": subscription.subscription_id,
                    "topic": message.topic,
                    "message_id": message.message_id,
                },
            )
