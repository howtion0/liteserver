"""Per-device command dispatch, ordering and lifecycle tracking."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from itertools import count
from time import monotonic
from typing import Any, Protocol, cast
from uuid import uuid4

from ..config import DispatchConfig
from ..message_bus import MessageBus
from ..messages import JsonValue, Message, MessageKind, MessageValidationError
from .commands import (
    TERMINAL_STATUSES,
    CommandError,
    CommandRepository,
    CommandSpec,
    CommandStatus,
    CommandTransitionError,
    CommandType,
    DispatchRequestError,
)

_DEVICE_ID = re.compile(r"^[0-9a-f]{12}$")
_SUPPORTED_TRANSPORTS = frozenset({"mqtt", "tcp", "websocket"})
_ACTION_PRIORITY = 10
_STOP_PRIORITY = 0
_EVENT_TOPICS = frozenset(
    {
        "device.command.published",
        "device.command.failed",
        "device.state.received",
        "device.state.changed",
        "device.transport.unavailable",
        "robot.action.accepted",
        "robot.action.failed",
        "robot.stop.accepted",
        "robot.stop.failed",
    }
)


class DispatchDeviceReader(Protocol):
    async def list_devices(self) -> list[dict[str, Any]]: ...

    async def get_device(self, device_id: str) -> dict[str, Any] | None: ...

    async def get_actions(
        self,
        device_id: str,
    ) -> list[dict[str, JsonValue]] | None: ...


@dataclass(slots=True)
class _DeviceWork:
    device_id: str
    queue: asyncio.PriorityQueue[tuple[int, int, str]] = field(
        default_factory=asyncio.PriorityQueue
    )
    pending_actions: set[str] = field(default_factory=set)
    cancelled: set[str] = field(default_factory=set)
    active_command_id: str | None = None
    active_type: CommandType | None = None
    interrupt: asyncio.Event = field(default_factory=asyncio.Event)
    worker: asyncio.Task[None] | None = None


@dataclass(slots=True)
class _LifecycleFacts:
    published: bool = False
    accepted: bool = False
    moving: bool = False
    idle: bool = False
    failure: str | None = None
    rejected: bool = False
    disconnected: str | None = None


class CommandDispatcher:
    """Validate, persist and serialize robot commands without touching transports."""

    def __init__(
        self,
        message_bus: MessageBus,
        repository: CommandRepository,
        devices: DispatchDeviceReader,
        config: DispatchConfig,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.message_bus = message_bus
        self.repository = repository
        self.devices = devices
        self.config = config
        self._logger = logger or logging.getLogger("otto_master.dispatcher")
        self._subscription_ids: list[str] = []
        self._observer_id: str | None = None
        self._contexts: dict[str, _DeviceWork] = {}
        self._specs: dict[str, CommandSpec] = {}
        self._event_queues: dict[str, asyncio.Queue[Message]] = {}
        self._query_owners: dict[str, str] = {}
        self._command_queries: dict[str, set[str]] = {}
        self._submission_waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._sequence = count()
        self._state_lock = asyncio.Lock()
        self._running = False
        self._accepting = False
        self._recovered_commands = 0
        self._last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._recovered_commands = await self.repository.recover_incomplete()
        self._subscription_ids = [
            await self.message_bus.subscribe(
                "robot.action.requested", self._handle_action_request
            ),
            await self.message_bus.subscribe("robot.stop.requested", self._handle_stop_request),
        ]
        self._observer_id = await self.message_bus.subscribe_observer(self._observe_message)
        self._running = True
        self._accepting = True
        self._logger.info(
            "dispatcher_started",
            extra={
                "event": "dispatcher_started",
                "recovered_commands": self._recovered_commands,
            },
        )

    async def shutdown(self) -> None:
        if not self._running and self._observer_id is None:
            return
        self._accepting = False
        for subscription_id in self._subscription_ids:
            await self.message_bus.unsubscribe(subscription_id)
        self._subscription_ids.clear()
        if self._observer_id is not None:
            await self.message_bus.unsubscribe_observer(self._observer_id)
            self._observer_id = None
        async with self._state_lock:
            tasks = tuple(
                context.worker
                for context in self._contexts.values()
                if context.worker is not None
            )
            for context in self._contexts.values():
                context.interrupt.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.repository.recover_incomplete()
        async with self._state_lock:
            for waiter in self._submission_waiters.values():
                if not waiter.done():
                    waiter.set_exception(
                        DispatchRequestError("dispatcher_stopped", "dispatcher is stopping")
                    )
            self._submission_waiters.clear()
            self._contexts.clear()
            self._specs.clear()
            self._event_queues.clear()
            self._query_owners.clear()
            self._command_queries.clear()
        self._running = False
        self._logger.info("dispatcher_stopped", extra={"event": "dispatcher_stopped"})

    def status(self) -> dict[str, Any]:
        queued = sum(context.queue.qsize() for context in self._contexts.values())
        active = sum(
            1 for context in self._contexts.values() if context.active_command_id is not None
        )
        return {
            "enabled": True,
            "healthy": self._running,
            "state": "running" if self._running else "stopped",
            "accepting": self._accepting,
            "active_commands": active,
            "queued_commands": queued,
            "device_workers": len(self._contexts),
            "recovered_commands": self._recovered_commands,
            "last_error": self._last_error,
        }

    async def submit_action(
        self,
        *,
        device_id: str,
        action: str,
        parameters: Mapping[str, JsonValue] | None = None,
        confirmation: bool,
        source: str = "webui",
        command_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            message = Message.create(
                topic="robot.action.requested",
                kind=MessageKind.COMMAND,
                source=source,
                target=f"device:{device_id}",
                message_id=command_id or str(uuid4()),
                correlation_id=correlation_id,
                payload={
                    "device_id": device_id,
                    "action": action,
                    "parameters": dict(parameters or {}),
                    "confirmation": confirmation,
                },
            )
        except (MessageValidationError, RecursionError) as exc:
            raise DispatchRequestError("invalid_parameters", str(exc)) from exc
        return await self._submit(message)

    async def submit_stop(
        self,
        *,
        device_id: str,
        source: str = "webui",
        command_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            message = Message.create(
                topic="robot.stop.requested",
                kind=MessageKind.COMMAND,
                source=source,
                target=f"device:{device_id}",
                message_id=command_id or str(uuid4()),
                correlation_id=correlation_id,
                payload={"device_id": device_id, "confirmation": True},
            )
        except (MessageValidationError, RecursionError) as exc:
            raise DispatchRequestError("invalid_command", str(exc)) from exc
        return await self._submit(message)

    async def stop_cluster(self, *, source: str = "webui") -> dict[str, Any]:
        if not self._accepting:
            raise DispatchRequestError("dispatcher_unavailable", "dispatcher is unavailable")
        results: list[dict[str, Any]] = []
        for device in await self.devices.list_devices():
            device_id = device.get("device_id")
            if not isinstance(device_id, str):
                continue
            try:
                command = await self.submit_stop(device_id=device_id, source=source)
            except DispatchRequestError as exc:
                results.append(
                    {
                        "device_id": device_id,
                        "accepted": False,
                        "error": {"code": exc.code, "message": exc.message},
                    }
                )
            else:
                results.append(
                    {
                        "device_id": device_id,
                        "accepted": True,
                        "command_id": command["command_id"],
                        "status": command["status"],
                    }
                )
        return {
            "requested": len(results),
            "accepted": sum(1 for item in results if item["accepted"]),
            "items": results,
        }

    async def get_command(self, command_id: str) -> dict[str, Any] | None:
        return await self.repository.get(command_id)

    async def _submit(self, message: Message) -> dict[str, Any]:
        if not self._accepting or not self.message_bus.running:
            raise DispatchRequestError("dispatcher_unavailable", "dispatcher is unavailable")
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[dict[str, Any]] = loop.create_future()
        async with self._state_lock:
            if message.message_id in self._submission_waiters:
                raise DispatchRequestError(
                    "duplicate_submission",
                    "the command ID is already awaiting acceptance",
                )
            self._submission_waiters[message.message_id] = waiter
        try:
            await self.message_bus.publish(message)
            timeout = max(1.0, self.config.ack_timeout_seconds)
            return await asyncio.wait_for(asyncio.shield(waiter), timeout=timeout)
        except TimeoutError as exc:
            raise DispatchRequestError(
                "dispatcher_submission_timeout",
                "dispatcher did not accept the request in time",
            ) from exc
        finally:
            async with self._state_lock:
                self._submission_waiters.pop(message.message_id, None)

    async def _handle_action_request(self, message: Message) -> None:
        await self._handle_request(message, CommandType.ACTION)

    async def _handle_stop_request(self, message: Message) -> None:
        await self._handle_request(message, CommandType.STOP)

    async def _handle_request(self, message: Message, command_type: CommandType) -> None:
        try:
            spec = self._spec_from_message(message, command_type)
            spec = await self._validate_spec(spec)
            record, inserted = await self.repository.create(spec)
            if inserted:
                record = await self._enqueue(spec)
        except DispatchRequestError as exc:
            await self._resolve_submission(message.message_id, error=exc)
            await self._publish_request_rejected(message, command_type, exc)
        except CommandError as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            error = DispatchRequestError("command_conflict", str(exc))
            await self._resolve_submission(message.message_id, error=error)
            await self._publish_request_rejected(message, command_type, error)
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._logger.exception(
                "dispatcher_request_failed",
                extra={"event": "dispatcher_request_failed", "command_id": message.message_id},
            )
            error = DispatchRequestError("dispatcher_error", "dispatcher rejected the request")
            await self._resolve_submission(message.message_id, error=error)
            await self._publish_request_rejected(message, command_type, error)
        else:
            await self._resolve_submission(message.message_id, result=record)

    def _spec_from_message(self, message: Message, command_type: CommandType) -> CommandSpec:
        if message.kind is not MessageKind.COMMAND:
            raise DispatchRequestError("invalid_command", "request must be a command message")
        device_id = message.payload.get("device_id")
        if not isinstance(device_id, str) or not _DEVICE_ID.fullmatch(device_id):
            raise DispatchRequestError("invalid_device_id", "device_id must be 12 lowercase hex")
        if message.target != f"device:{device_id}":
            raise DispatchRequestError(
                "target_mismatch", "command target does not match device_id"
            )
        confirmation = message.payload.get("confirmation", False)
        if not isinstance(confirmation, bool):
            raise DispatchRequestError("invalid_confirmation", "confirmation must be boolean")
        action: str | None = None
        parameters: dict[str, JsonValue] | None = None
        if command_type is CommandType.ACTION:
            value = message.payload.get("action")
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > 80:
                raise DispatchRequestError("invalid_action", "action must be a valid name")
            action = value.strip()
            raw_parameters = message.payload.get("parameters", {})
            if not isinstance(raw_parameters, dict):
                raise DispatchRequestError(
                    "invalid_parameters", "action parameters must be an object"
                )
            parameters = cast(dict[str, JsonValue], dict(raw_parameters))
        return CommandSpec(
            command_id=message.message_id,
            correlation_id=message.correlation_id,
            command_type=command_type,
            device_id=device_id,
            target=message.target,
            source=message.source,
            action=action,
            parameters=parameters,
            confirmation=confirmation,
        )

    async def _validate_spec(self, spec: CommandSpec) -> CommandSpec:
        if not self._accepting:
            raise DispatchRequestError("dispatcher_unavailable", "dispatcher is unavailable")
        device = await self.devices.get_device(spec.device_id)
        if device is None:
            raise DispatchRequestError("device_not_found", "device does not exist")
        if not bool(device.get("enabled", True)):
            raise DispatchRequestError("device_disabled", "device is disabled")
        transport = device.get("transport")
        if not isinstance(transport, str) or transport not in _SUPPORTED_TRANSPORTS:
            raise DispatchRequestError(
                "transport_unavailable", "device has no supported active transport"
            )
        status = device.get("status")
        if status != "online":
            raise DispatchRequestError("device_not_online", "device is not online")
        capabilities = device.get("capabilities")
        if not isinstance(capabilities, dict):
            capabilities = {}
        required_capability = "actions" if spec.command_type is CommandType.ACTION else "stop"
        if capabilities.get(required_capability) is not True:
            raise DispatchRequestError(
                "capability_missing",
                f"device does not declare {required_capability} capability",
            )
        if spec.command_type is CommandType.STOP:
            return CommandSpec(
                command_id=spec.command_id,
                correlation_id=spec.correlation_id,
                command_type=spec.command_type,
                device_id=spec.device_id,
                target=spec.target,
                source=spec.source,
                transport=transport,
                action=spec.action,
                parameters=spec.parameters,
                confirmation=spec.confirmation,
            )
        if not spec.confirmation:
            raise DispatchRequestError(
                "confirmation_required", "robot movement requires explicit confirmation"
            )
        actions = await self.devices.get_actions(spec.device_id)
        if actions is None:
            raise DispatchRequestError("device_not_found", "device does not exist")
        definition = next((item for item in actions if item.get("name") == spec.action), None)
        if definition is None:
            raise DispatchRequestError(
                "action_not_supported", "action is not in the device catalog"
            )
        self._validate_parameters(spec.parameters or {}, definition.get("parameters"))
        async with self._state_lock:
            context = self._contexts.get(spec.device_id)
            active_owned = context is not None and context.active_command_id is not None
            pending = len(context.pending_actions) if context is not None else 0
        if not active_owned and device.get("action_state") not in {"idle", None}:
            raise DispatchRequestError(
                "device_state_unsafe", "device must be idle before accepting an action"
            )
        if pending >= self.config.queue_size_per_device:
            raise DispatchRequestError("device_queue_full", "device action queue is full")
        return CommandSpec(
            command_id=spec.command_id,
            correlation_id=spec.correlation_id,
            command_type=spec.command_type,
            device_id=spec.device_id,
            target=spec.target,
            source=spec.source,
            transport=transport,
            action=spec.action,
            parameters=spec.parameters,
            confirmation=spec.confirmation,
        )

    @staticmethod
    def _validate_parameters(
        parameters: Mapping[str, JsonValue],
        schema_value: JsonValue,
    ) -> None:
        if len(parameters) > 32:
            raise DispatchRequestError("invalid_parameters", "too many action parameters")
        schema = schema_value if isinstance(schema_value, dict) else {}
        unknown = sorted(set(parameters) - set(schema))
        if unknown:
            raise DispatchRequestError(
                "invalid_parameters",
                f"unknown action parameters: {', '.join(unknown)}",
            )
        for name, descriptor in schema.items():
            required = isinstance(descriptor, dict) and descriptor.get("required") is True
            if required and name not in parameters:
                raise DispatchRequestError(
                    "invalid_parameters", f"missing required action parameter: {name}"
                )
            if name not in parameters:
                continue
            value = parameters[name]
            expected = descriptor if isinstance(descriptor, str) else None
            if isinstance(descriptor, dict):
                raw_type = descriptor.get("type")
                expected = raw_type if isinstance(raw_type, str) else None
            valid = {
                "integer": isinstance(value, int) and not isinstance(value, bool),
                "number": isinstance(value, (int, float)) and not isinstance(value, bool),
                "string": isinstance(value, str),
                "boolean": isinstance(value, bool),
            }
            if expected in valid and not valid[expected]:
                raise DispatchRequestError(
                    "invalid_parameters", f"action parameter {name} must be {expected}"
                )
            if isinstance(descriptor, dict):
                allowed = descriptor.get("enum")
                if isinstance(allowed, list) and value not in allowed:
                    raise DispatchRequestError(
                        "invalid_parameters", f"action parameter {name} is not allowed"
                    )
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    minimum = descriptor.get("minimum", descriptor.get("min"))
                    maximum = descriptor.get("maximum", descriptor.get("max"))
                    if isinstance(minimum, (int, float)) and value < minimum:
                        raise DispatchRequestError(
                            "invalid_parameters", f"action parameter {name} is below minimum"
                        )
                    if isinstance(maximum, (int, float)) and value > maximum:
                        raise DispatchRequestError(
                            "invalid_parameters", f"action parameter {name} exceeds maximum"
                        )

    async def _enqueue(self, spec: CommandSpec) -> dict[str, Any]:
        cancelled: list[str] = []
        async with self._state_lock:
            context = self._contexts.get(spec.device_id)
            if context is None:
                context = _DeviceWork(device_id=spec.device_id)
                self._contexts[spec.device_id] = context
            if context.worker is None or context.worker.done():
                context.worker = asyncio.create_task(
                    self._device_worker(context),
                    name=f"otto-dispatch-{spec.device_id}",
                )
            if spec.command_type is CommandType.ACTION:
                if len(context.pending_actions) >= self.config.queue_size_per_device:
                    await self.repository.transition(
                        spec.command_id,
                        CommandStatus.REJECTED,
                        error="device_queue_full",
                    )
                    raise DispatchRequestError("device_queue_full", "device action queue is full")
                context.pending_actions.add(spec.command_id)
                priority = _ACTION_PRIORITY
            else:
                cancelled = list(context.pending_actions)
                context.cancelled.update(cancelled)
                context.pending_actions.clear()
                if context.active_type is CommandType.ACTION:
                    context.interrupt.set()
                priority = _STOP_PRIORITY
            self._specs[spec.command_id] = spec
            context.queue.put_nowait((priority, next(self._sequence), spec.command_id))
        for command_id in cancelled:
            await self._transition_if_active(
                command_id,
                CommandStatus.FAILED,
                error=f"cancelled_by_stop:{spec.command_id}",
                payload={"stop_command_id": spec.command_id},
            )
        stored = await self.repository.get(spec.command_id)
        if stored is None:
            raise DispatchRequestError("dispatcher_error", "queued command disappeared")
        return stored

    async def _device_worker(self, context: _DeviceWork) -> None:
        try:
            while True:
                _, _, command_id = await context.queue.get()
                try:
                    async with self._state_lock:
                        if command_id in context.cancelled:
                            context.cancelled.discard(command_id)
                            self._specs.pop(command_id, None)
                            continue
                        spec = self._specs.get(command_id)
                        if spec is None:
                            continue
                        context.pending_actions.discard(command_id)
                        context.active_command_id = command_id
                        context.active_type = spec.command_type
                        context.interrupt.clear()
                    await self._execute(context, spec)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._last_error = f"{type(exc).__name__}: {exc}"
                    self._logger.exception(
                        "dispatcher_command_failed",
                        extra={"event": "dispatcher_command_failed", "command_id": command_id},
                    )
                    await self._transition_if_active(
                        command_id,
                        CommandStatus.FAILED,
                        error="dispatcher_internal_error",
                    )
                finally:
                    async with self._state_lock:
                        if context.active_command_id == command_id:
                            context.active_command_id = None
                            context.active_type = None
                            context.interrupt.clear()
                        self._specs.pop(command_id, None)
                    context.queue.task_done()
        except asyncio.CancelledError:
            return

    async def _execute(self, context: _DeviceWork, spec: CommandSpec) -> None:
        event_queue: asyncio.Queue[Message] = asyncio.Queue()
        async with self._state_lock:
            self._event_queues[spec.command_id] = event_queue
        outbound_topic = (
            "device.action.execute.requested"
            if spec.command_type is CommandType.ACTION
            else "device.stop.execute.requested"
        )
        payload = spec.payload()
        payload["command_id"] = spec.command_id
        outbound = Message.create(
            topic=outbound_topic,
            kind=MessageKind.COMMAND,
            source="dispatcher",
            target=spec.target,
            correlation_id=spec.command_id,
            payload=payload,
        )
        try:
            await self.message_bus.publish(outbound)
            terminal = await self._track_lifecycle(context, spec, event_queue, outbound_topic)
        finally:
            await self._clear_command_events(spec.command_id)
        if spec.command_type is CommandType.ACTION and terminal is CommandStatus.TIMEOUT:
            try:
                await self.submit_stop(
                    device_id=spec.device_id,
                    source="dispatcher:safety",
                    correlation_id=spec.command_id,
                )
            except DispatchRequestError as exc:
                self._last_error = f"safety_stop_failed: {exc.code}"

    async def _track_lifecycle(
        self,
        context: _DeviceWork,
        spec: CommandSpec,
        events: asyncio.Queue[Message],
        outbound_topic: str,
    ) -> CommandStatus:
        facts = _LifecycleFacts()
        status = CommandStatus.REQUESTED
        started = monotonic()
        ack_deadline = started + self.config.ack_timeout_seconds
        completion_deadline: float | None = None
        next_query: float | None = None

        while status not in TERMINAL_STATUSES:
            now = monotonic()
            if status in {CommandStatus.REQUESTED, CommandStatus.PUBLISHED}:
                deadline = ack_deadline
            else:
                if completion_deadline is None:
                    completion_deadline = now + self.config.completion_timeout_seconds
                    next_query = now
                deadline = completion_deadline
            wake_at = deadline
            if next_query is not None:
                wake_at = min(wake_at, next_query)
            timeout = max(0.0, wake_at - now)
            message, interrupted = await self._wait_for_event(
                events,
                context.interrupt if spec.command_type is CommandType.ACTION else None,
                timeout,
            )
            if interrupted:
                await self.repository.transition(
                    spec.command_id,
                    CommandStatus.FAILED,
                    error="interrupted_by_stop",
                )
                return CommandStatus.FAILED
            if message is not None:
                self._record_fact(facts, message, spec, outbound_topic)
                status = await self._advance_lifecycle(spec, status, facts, started)
                continue
            now = monotonic()
            if next_query is not None and now >= next_query and now < deadline:
                await self._request_state_query(spec)
                next_query = now + self.config.state_query_interval_seconds
                continue
            if now >= deadline:
                reason = (
                    "publish_or_ack_timeout"
                    if status in {CommandStatus.REQUESTED, CommandStatus.PUBLISHED}
                    else "moving_or_idle_timeout"
                )
                await self.repository.transition(
                    spec.command_id,
                    CommandStatus.TIMEOUT,
                    error=reason,
                    payload={"elapsed_ms": round((now - started) * 1000, 3)},
                )
                return CommandStatus.TIMEOUT
        return status

    @staticmethod
    async def _wait_for_event(
        events: asyncio.Queue[Message],
        interrupt: asyncio.Event | None,
        timeout: float,
    ) -> tuple[Message | None, bool]:
        event_task = asyncio.create_task(events.get())
        interrupt_task = asyncio.create_task(interrupt.wait()) if interrupt is not None else None
        tasks: set[asyncio.Task[Any]] = {event_task}
        if interrupt_task is not None:
            tasks.add(interrupt_task)
        done, pending = await asyncio.wait(
            tasks,
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if interrupt_task is not None and interrupt_task in done and interrupt_task.result():
            if event_task in done:
                events.task_done()
            return None, True
        if event_task in done:
            message = event_task.result()
            events.task_done()
            return message, False
        return None, False

    @staticmethod
    def _record_fact(
        facts: _LifecycleFacts,
        message: Message,
        spec: CommandSpec,
        outbound_topic: str,
    ) -> None:
        if message.topic == "device.transport.unavailable":
            if message.payload.get("transport") != spec.transport:
                return
            reason = message.payload.get("reason")
            facts.disconnected = reason if isinstance(reason, str) else "transport_unavailable"
            return
        if message.topic == "device.state.changed":
            if message.payload.get("transport") != spec.transport:
                facts.disconnected = "device_transport_changed"
                return
            if message.payload.get("status") in {
                "stale",
                "offline",
                "error",
                "disabled",
            }:
                facts.disconnected = f"device_{message.payload.get('status')}"
            return
        if message.payload.get("transport") != spec.transport:
            return
        if message.topic == "device.command.failed":
            command_topic = message.payload.get("command_topic")
            if command_topic == outbound_topic:
                reason = message.payload.get("reason")
                facts.failure = reason if isinstance(reason, str) else "device_publish_failed"
            return
        if message.topic == "device.command.published":
            if message.payload.get("command_topic") == outbound_topic:
                facts.published = True
            return
        if message.topic in {"robot.action.failed", "robot.stop.failed"}:
            reason = message.payload.get("error")
            facts.failure = reason if isinstance(reason, str) else "device_rejected_command"
            facts.rejected = message.payload.get("accepted") is False
            return
        expected_ack = (
            "robot.action.accepted"
            if spec.command_type is CommandType.ACTION
            else "robot.stop.accepted"
        )
        if message.topic == expected_ack and message.payload.get("accepted") is True:
            facts.accepted = True
            return
        if message.topic != "device.state.received":
            return
        state = message.payload.get("action_state")
        current_action = message.payload.get("current_action")
        if state == "moving" and spec.command_type is CommandType.ACTION:
            if current_action in {spec.action, None}:
                facts.moving = True
        elif state == "idle":
            facts.idle = True

    async def _advance_lifecycle(
        self,
        spec: CommandSpec,
        status: CommandStatus,
        facts: _LifecycleFacts,
        started: float,
    ) -> CommandStatus:
        def elapsed() -> dict[str, JsonValue]:
            return {"elapsed_ms": round((monotonic() - started) * 1000, 3)}
        if facts.disconnected is not None:
            await self.repository.transition(
                spec.command_id,
                CommandStatus.DISCONNECTED,
                error=facts.disconnected,
                payload=elapsed(),
            )
            return CommandStatus.DISCONNECTED
        if facts.failure is not None:
            terminal = (
                CommandStatus.REJECTED
                if facts.rejected
                and status in {CommandStatus.REQUESTED, CommandStatus.PUBLISHED}
                else CommandStatus.FAILED
            )
            await self.repository.transition(
                spec.command_id,
                terminal,
                error=facts.failure,
                payload=elapsed(),
            )
            return terminal
        if facts.published and status is CommandStatus.REQUESTED:
            await self.repository.transition(
                spec.command_id,
                CommandStatus.PUBLISHED,
                payload=elapsed(),
            )
            status = CommandStatus.PUBLISHED
        if facts.accepted and status is CommandStatus.PUBLISHED:
            await self.repository.transition(
                spec.command_id,
                CommandStatus.ACCEPTED,
                payload=elapsed(),
            )
            status = CommandStatus.ACCEPTED
        if (
            spec.command_type is CommandType.ACTION
            and facts.moving
            and status is CommandStatus.ACCEPTED
        ):
            await self.repository.transition(
                spec.command_id,
                CommandStatus.MOVING,
                payload=elapsed(),
            )
            status = CommandStatus.MOVING
        if facts.idle and (
            (spec.command_type is CommandType.ACTION and status is CommandStatus.MOVING)
            or (spec.command_type is CommandType.STOP and status is CommandStatus.ACCEPTED)
        ):
            await self.repository.transition(
                spec.command_id,
                CommandStatus.COMPLETED,
                payload=elapsed(),
            )
            status = CommandStatus.COMPLETED
        return status

    async def _request_state_query(self, spec: CommandSpec) -> None:
        async with self._state_lock:
            outstanding = self._command_queries.setdefault(spec.command_id, set())
            if outstanding:
                return
            query = Message.create(
                topic="device.state.query.requested",
                kind=MessageKind.COMMAND,
                source="dispatcher",
                target=spec.target,
                payload={"device_id": spec.device_id, "transport": spec.transport},
            )
            outstanding.add(query.message_id)
            self._query_owners[query.message_id] = spec.command_id
        await self.message_bus.publish(query)

    async def _observe_message(self, message: Message) -> None:
        if message.topic not in _EVENT_TOPICS:
            return
        if (
            message.source == "dispatcher"
            and message.topic in {"robot.action.failed", "robot.stop.failed"}
        ):
            return
        deliveries: list[asyncio.Queue[Message]] = []
        async with self._state_lock:
            if message.topic == "device.transport.unavailable":
                deliveries = list(self._event_queues.values())
            elif message.topic == "device.state.changed":
                device_id = message.payload.get("device_id")
                if isinstance(device_id, str):
                    context = self._contexts.get(device_id)
                    if context is not None and context.active_command_id is not None:
                        queue = self._event_queues.get(context.active_command_id)
                        if queue is not None:
                            deliveries = [queue]
            else:
                correlation_id = message.correlation_id
                owner = correlation_id
                if (
                    correlation_id is not None
                    and correlation_id in self._query_owners
                    and message.topic in {"device.state.received", "device.command.failed"}
                ):
                    owner = self._query_owners.pop(correlation_id)
                    self._command_queries.get(owner, set()).discard(correlation_id)
                if owner is not None:
                    spec = self._specs.get(owner)
                    queue = self._event_queues.get(owner)
                    if spec is not None and queue is not None and message.target == spec.target:
                        deliveries = [queue]
        for queue in deliveries:
            queue.put_nowait(message)

    async def _clear_command_events(self, command_id: str) -> None:
        async with self._state_lock:
            self._event_queues.pop(command_id, None)
            query_ids = self._command_queries.pop(command_id, set())
            for query_id in query_ids:
                self._query_owners.pop(query_id, None)

    async def _transition_if_active(
        self,
        command_id: str,
        status: CommandStatus,
        *,
        error: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> None:
        record = await self.repository.get(command_id)
        if record is None or bool(record["terminal"]):
            return
        try:
            await self.repository.transition(
                command_id,
                status,
                error=error,
                payload=payload,
            )
        except CommandTransitionError:
            return

    async def _resolve_submission(
        self,
        command_id: str,
        *,
        result: dict[str, Any] | None = None,
        error: DispatchRequestError | None = None,
    ) -> None:
        async with self._state_lock:
            waiter = self._submission_waiters.get(command_id)
        if waiter is None or waiter.done():
            return
        if error is not None:
            waiter.set_exception(error)
        elif result is not None:
            waiter.set_result(result)

    async def _publish_request_rejected(
        self,
        message: Message,
        command_type: CommandType,
        error: DispatchRequestError,
    ) -> None:
        if not self.message_bus.running:
            return
        await self.message_bus.publish(
            Message.create(
                topic=(
                    "robot.action.failed"
                    if command_type is CommandType.ACTION
                    else "robot.stop.failed"
                ),
                kind=MessageKind.RESULT,
                source="dispatcher",
                target=message.target,
                correlation_id=message.message_id,
                payload={
                    "device_id": message.payload.get("device_id"),
                    "accepted": False,
                    "error": error.code,
                    "detail": error.message,
                },
            )
        )
