"""Correlated, capability-gated remote conversation start and stop commands."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from ..devices.manager import DeviceManager
from ..gateways.mqtt_broker import normalize_device_id
from ..message_bus import MessageBus
from ..messages import Message, MessageKind

ConversationCommand = Literal["start", "stop"]
_REQUEST_TOPICS: dict[ConversationCommand, str] = {
    "start": "device.conversation.start.requested",
    "stop": "device.conversation.stop.requested",
}


class ConversationControlError(RuntimeError):
    """Stable failure returned by the formal conversation control path."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class _PendingControl:
    target: str
    transport: str
    command: ConversationCommand
    future: asyncio.Future[Message]


class ConversationControlService:
    """Send one exact device command and wait for its correlated device ACK."""

    def __init__(
        self,
        message_bus: MessageBus,
        devices: DeviceManager,
        *,
        timeout_seconds: float,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("conversation control timeout must be positive")
        self.message_bus = message_bus
        self.devices = devices
        self.timeout_seconds = timeout_seconds
        self._observer_id: str | None = None
        self._pending: dict[str, _PendingControl] = {}
        self._device_locks: dict[str, asyncio.Lock] = {}
        self._running = False
        self._accepted = 0
        self._failed = 0
        self._timed_out = 0

    async def start(self) -> None:
        if self._running:
            return
        self._observer_id = await self.message_bus.subscribe_observer(self)
        self._running = True

    async def shutdown(self) -> None:
        if not self._running and self._observer_id is None:
            return
        self._running = False
        if self._observer_id is not None:
            await self.message_bus.unsubscribe_observer(self._observer_id)
            self._observer_id = None
        for pending in tuple(self._pending.values()):
            if not pending.future.done():
                pending.future.set_exception(
                    ConversationControlError(
                        "conversation_control_stopped",
                        "conversation control service stopped",
                    )
                )
        self._pending.clear()

    async def __call__(self, message: Message) -> None:
        if message.topic == "device.transport.unavailable":
            transport = message.payload.get("transport")
            if isinstance(transport, str):
                self._interrupt_transport(transport)
            return
        if message.topic == "device.state.changed":
            status = message.payload.get("status")
            if status in {"offline", "disabled", "error"}:
                self._interrupt_target(message.target, f"device became {status}")
            return
        correlation_id = message.correlation_id
        if correlation_id is None:
            return
        pending = self._pending.get(correlation_id)
        if (
            pending is None
            or pending.target != message.target
            or pending.future.done()
            or message.payload.get("transport") != pending.transport
        ):
            return
        if message.topic == "device.command.failed":
            reason = message.payload.get("reason")
            pending.future.set_exception(
                ConversationControlError(
                    "conversation_publish_failed",
                    reason if isinstance(reason, str) else "device command failed",
                )
            )
            return
        if message.payload.get("command") != pending.command:
            return
        if message.topic == "device.conversation.control.accepted":
            pending.future.set_result(message)
        elif message.topic == "device.conversation.control.failed":
            reason = message.payload.get("error")
            pending.future.set_exception(
                ConversationControlError(
                    "conversation_rejected",
                    reason if isinstance(reason, str) else "device rejected conversation control",
                )
            )

    async def control(
        self,
        *,
        device_id: str,
        command: ConversationCommand,
        source: str = "webui",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        if not self._running:
            raise ConversationControlError(
                "conversation_control_unavailable",
                "conversation control service is not running",
            )
        if command not in _REQUEST_TOPICS:
            raise ConversationControlError(
                "invalid_conversation_command",
                "conversation command must be start or stop",
            )
        try:
            normalized = normalize_device_id(device_id)
        except ValueError as exc:
            raise ConversationControlError(
                "invalid_device_id",
                "device ID must be a hardware address",
            ) from exc
        lock = self._device_locks.setdefault(normalized, asyncio.Lock())
        async with lock:
            return await self._control_locked(
                device_id=normalized,
                command=command,
                source=source,
                correlation_id=correlation_id,
            )

    async def _control_locked(
        self,
        *,
        device_id: str,
        command: ConversationCommand,
        source: str,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        device = await self.devices.get_device(device_id)
        if device is None:
            raise ConversationControlError("device_not_found", "device does not exist")
        if device.get("status") != "online":
            raise ConversationControlError("device_not_online", "device is not online")
        capabilities = device.get("capabilities")
        if not isinstance(capabilities, dict) or capabilities.get("conversation_control") is not True:
            raise ConversationControlError(
                "capability_missing",
                "device does not declare conversation control support",
            )
        transport = device.get("transport")
        if transport not in {"mqtt", "tcp", "websocket"}:
            raise ConversationControlError(
                "transport_unavailable",
                "device has no supported active transport",
            )
        command_id = str(uuid4())
        target = f"device:{device_id}"
        request = Message.create(
            topic=_REQUEST_TOPICS[command],
            kind=MessageKind.COMMAND,
            source=source,
            target=target,
            message_id=command_id,
            correlation_id=command_id,
            payload={
                "device_id": device_id,
                "transport": transport,
                "command_id": command_id,
                "command": command,
                "request_correlation_id": correlation_id,
            },
        )
        future: asyncio.Future[Message] = asyncio.get_running_loop().create_future()
        self._pending[command_id] = _PendingControl(
            target=target,
            transport=transport,
            command=command,
            future=future,
        )
        try:
            await self.message_bus.publish(request)
            await asyncio.wait_for(future, timeout=self.timeout_seconds)
        except TimeoutError as exc:
            self._timed_out += 1
            self._failed += 1
            raise ConversationControlError(
                "conversation_control_timeout",
                f"device did not acknowledge within {self.timeout_seconds:g}s",
            ) from exc
        except ConversationControlError:
            self._failed += 1
            raise
        finally:
            self._pending.pop(command_id, None)
        self._accepted += 1
        return {
            "device_id": device_id,
            "command": command,
            "command_id": command_id,
            "status": "accepted",
            "transport": transport,
            "acknowledged_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }

    def status(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "healthy": self._running,
            "state": "running" if self._running else "stopped",
            "pending": len(self._pending),
            "accepted": self._accepted,
            "failed": self._failed,
            "timed_out": self._timed_out,
            "timeout_seconds": self.timeout_seconds,
        }

    def _interrupt_transport(self, transport: str) -> None:
        for pending in tuple(self._pending.values()):
            if pending.transport == transport and not pending.future.done():
                pending.future.set_exception(
                    ConversationControlError(
                        "transport_unavailable",
                        f"{transport} transport became unavailable",
                    )
                )

    def _interrupt_target(self, target: str, reason: str) -> None:
        for pending in tuple(self._pending.values()):
            if pending.target == target and not pending.future.done():
                pending.future.set_exception(
                    ConversationControlError("device_not_online", reason)
                )
