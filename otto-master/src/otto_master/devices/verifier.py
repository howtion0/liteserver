"""Non-moving device connection verification over correlated domain messages."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any, cast
from uuid import uuid4

from ..message_bus import MessageBus
from ..messages import JsonValue, Message, MessageKind
from .manager import DeviceManager

StatusProvider = Callable[[], Mapping[str, Any]]
TransportStatusProvider = Callable[[str], Mapping[str, Any]]
Clock = Callable[[], datetime]
Timer = Callable[[], float]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class VerificationInterruptedError(RuntimeError):
    """Raised internally when a pending verification can no longer finish."""


@dataclass(slots=True)
class _PendingResponse:
    target: str
    expected_topic: str
    transport: str
    future: asyncio.Future[Message]


class DeviceVerifier:
    """Run read-only state and action-catalog queries against one live session."""

    def __init__(
        self,
        message_bus: MessageBus,
        device_manager: DeviceManager,
        *,
        broker_status: StatusProvider,
        gateway_status: StatusProvider,
        transport_status: TransportStatusProvider | None = None,
        timeout_seconds: float,
        clock: Clock = _utc_now,
        timer: Timer = monotonic,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("verification timeout must be positive")
        self.message_bus = message_bus
        self.device_manager = device_manager
        self.broker_status = broker_status
        self.gateway_status = gateway_status
        self.transport_status = transport_status
        self.timeout_seconds = timeout_seconds
        self.clock = clock
        self.timer = timer
        self._observer_id: str | None = None
        self._pending: dict[str, _PendingResponse] = {}
        self._device_locks: dict[str, asyncio.Lock] = {}
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    @property
    def pending_count(self) -> int:
        return len(self._pending)

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
                    VerificationInterruptedError("device verifier stopped")
                )
        self._pending.clear()

    async def __call__(self, message: Message) -> None:
        if message.topic == "device.transport.unavailable":
            transport = message.payload.get("transport")
            if isinstance(transport, str):
                self._interrupt_transport(transport, f"{transport} transport became unavailable")
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
        if pending is None or pending.target != message.target or pending.future.done():
            return
        if message.payload.get("transport") != pending.transport:
            return
        if message.topic == "device.command.failed":
            reason = message.payload.get("reason")
            pending.future.set_exception(
                VerificationInterruptedError(
                    reason if isinstance(reason, str) else "device command failed"
                )
            )
        elif message.topic == pending.expected_topic:
            pending.future.set_result(message)

    async def verify(self, device_id: str) -> dict[str, Any] | None:
        if not self._running:
            raise RuntimeError("device verifier is not running")
        device = await self.device_manager.get_device(device_id)
        if device is None:
            return None
        lock = self._device_locks.setdefault(device_id, asyncio.Lock())
        async with lock:
            return await self._verify_locked(device_id)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "healthy": self._running,
            "state": "running" if self._running else "stopped",
            "pending": self.pending_count,
            "timeout_seconds": self.timeout_seconds,
        }

    async def _verify_locked(self, device_id: str) -> dict[str, Any]:
        started_at = self.clock()
        started_timer = self.timer()
        verification_id = str(uuid4())
        report: dict[str, Any] = {
            "verification_id": verification_id,
            "device_id": device_id,
            "passed": False,
            "started_at": _iso(started_at),
            "completed_at": None,
            "duration_ms": None,
            "checks": [],
            "failure": None,
        }
        await self.message_bus.publish(
            Message.create(
                topic="device.verification.requested",
                kind=MessageKind.COMMAND,
                source="web_gateway",
                target=f"device:{device_id}",
                message_id=verification_id,
                payload={"device_id": device_id, "mode": "read_only"},
            )
        )
        checks = cast(list[dict[str, JsonValue]], report["checks"])
        device = await self.device_manager.get_device(device_id)
        if device is None:
            self._add_check(checks, "session", False, "device session disappeared")
            report["failure"] = "device session disappeared"
            return await self._finish(report, started_timer)

        transport_value = device.get("transport")
        transport = transport_value if isinstance(transport_value, str) else "unknown"
        report["transport"] = transport
        if transport == "mqtt":
            broker = self.broker_status()
            gateway = self.gateway_status()
            self._add_check(
                checks,
                "mqtt_broker",
                broker.get("healthy") is True,
                str(broker.get("state", "unknown")),
            )
            self._add_check(
                checks,
                "mqtt_gateway",
                gateway.get("healthy") is True,
                str(gateway.get("state", "unknown")),
            )
        else:
            gateway = (
                self.transport_status(transport)
                if self.transport_status is not None
                else self.gateway_status()
            )
            self._add_check(
                checks,
                "transport_gateway",
                gateway.get("healthy") is True,
                str(gateway.get("state", "unknown")),
            )
        self._add_check(
            checks,
            "session_online",
            device.get("status") == "online",
            str(device.get("status", "unknown")),
        )
        self._add_check(
            checks,
            "transport",
            transport in {"mqtt", "tcp", "websocket"},
            transport,
        )
        heartbeat = device.get("last_heartbeat_at")
        self._add_check(
            checks,
            "recent_heartbeat",
            isinstance(heartbeat, str) and bool(heartbeat),
            heartbeat if isinstance(heartbeat, str) else "missing",
        )
        capabilities = device.get("capabilities")
        capability_map = capabilities if isinstance(capabilities, dict) else {}
        state_capability = capability_map.get("state") is True
        actions_capability = capability_map.get("actions") is True
        self._add_check(
            checks,
            "state_capability",
            state_capability,
            "declared" if state_capability else "missing",
        )
        self._add_check(
            checks,
            "actions_capability",
            actions_capability,
            "declared" if actions_capability else "missing",
        )
        failed_preflight = next(
            (str(item["detail"]) for item in checks if item["status"] == "fail"),
            None,
        )
        if failed_preflight is not None:
            report["failure"] = f"preflight failed: {failed_preflight}"
            return await self._finish(report, started_timer)

        state_response, state_command_id, state_latency, state_error = await self._request(
            device_id=device_id,
            transport=transport,
            request_topic="device.state.query.requested",
            response_topic="device.state.received",
        )
        state_ok = state_response is not None
        self._add_check(
            checks,
            "state_query",
            state_ok,
            state_error or "correlated state received",
            command_id=state_command_id,
            latency_ms=state_latency,
        )
        if state_response is None:
            report["failure"] = state_error
            return await self._finish(report, started_timer)
        report["action_state"] = state_response.payload.get("action_state")
        report["current_action"] = state_response.payload.get("current_action")

        actions_response, actions_command_id, actions_latency, actions_error = (
            await self._request(
                device_id=device_id,
                transport=transport,
                request_topic="device.actions.query.requested",
                response_topic="device.actions.catalog.received",
            )
        )
        actions = actions_response.payload.get("actions") if actions_response else None
        actions_ok = isinstance(actions, list) and bool(actions)
        detail = actions_error or (
            f"received {len(actions)} actions" if isinstance(actions, list) else "invalid catalog"
        )
        self._add_check(
            checks,
            "actions_query",
            actions_ok,
            detail,
            command_id=actions_command_id,
            latency_ms=actions_latency,
        )
        if not isinstance(actions, list) or not actions:
            report["failure"] = actions_error or "device returned an empty action catalog"
            return await self._finish(report, started_timer)
        report["actions_count"] = len(actions)
        report["passed"] = True
        return await self._finish(report, started_timer)

    async def _request(
        self,
        *,
        device_id: str,
        transport: str,
        request_topic: str,
        response_topic: str,
    ) -> tuple[Message | None, str, float, str | None]:
        command = Message.create(
            topic=request_topic,
            kind=MessageKind.COMMAND,
            source="device_verifier",
            target=f"device:{device_id}",
            payload={"device_id": device_id, "transport": transport},
        )
        future: asyncio.Future[Message] = asyncio.get_running_loop().create_future()
        self._pending[command.message_id] = _PendingResponse(
            target=command.target,
            expected_topic=response_topic,
            transport=transport,
            future=future,
        )
        started = self.timer()
        try:
            await self.message_bus.publish(command)
            response = await asyncio.wait_for(future, timeout=self.timeout_seconds)
        except TimeoutError:
            return (
                None,
                command.message_id,
                round((self.timer() - started) * 1000, 3),
                f"timed out after {self.timeout_seconds:g}s",
            )
        except VerificationInterruptedError as exc:
            return (
                None,
                command.message_id,
                round((self.timer() - started) * 1000, 3),
                str(exc),
            )
        finally:
            self._pending.pop(command.message_id, None)
        return (
            response,
            command.message_id,
            round((self.timer() - started) * 1000, 3),
            None,
        )

    async def _finish(self, report: dict[str, Any], started_timer: float) -> dict[str, Any]:
        report["completed_at"] = _iso(self.clock())
        report["duration_ms"] = round((self.timer() - started_timer) * 1000, 3)
        if self.message_bus.running:
            await self.message_bus.publish(
                Message.create(
                    topic="device.verification.completed",
                    kind=MessageKind.RESULT,
                    source="device_verifier",
                    target=f"device:{report['device_id']}",
                    correlation_id=str(report["verification_id"]),
                    payload=cast(Mapping[str, JsonValue], report),
                )
            )
        return report

    @staticmethod
    def _add_check(
        checks: list[dict[str, JsonValue]],
        name: str,
        passed: bool,
        detail: str,
        *,
        command_id: str | None = None,
        latency_ms: float | None = None,
    ) -> None:
        check: dict[str, JsonValue] = {
            "name": name,
            "status": "pass" if passed else "fail",
            "detail": detail,
        }
        if command_id is not None:
            check["command_id"] = command_id
        if latency_ms is not None:
            check["latency_ms"] = latency_ms
        checks.append(check)

    def _interrupt_all(self, reason: str) -> None:
        for pending in tuple(self._pending.values()):
            if not pending.future.done():
                pending.future.set_exception(VerificationInterruptedError(reason))

    def _interrupt_transport(self, transport: str, reason: str) -> None:
        for pending in self._pending.values():
            if pending.transport == transport and not pending.future.done():
                pending.future.set_exception(VerificationInterruptedError(reason))

    def _interrupt_target(self, target: str, reason: str) -> None:
        for pending in tuple(self._pending.values()):
            if pending.target == target and not pending.future.done():
                pending.future.set_exception(VerificationInterruptedError(reason))
