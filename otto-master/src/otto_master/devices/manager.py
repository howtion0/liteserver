"""Device registry and liveness state derived from internal Messages."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, cast

from ..message_bus import MessageBus
from ..messages import JsonValue, Message, MessageKind
from ..storage.database import Database
from .session import DeviceSession
from .states import DeviceActionState, DeviceStateError, DeviceStatus

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


class DeviceManager:
    """Maintain one live session per stable device_id and persist safe snapshots."""

    def __init__(
        self,
        database: Database,
        message_bus: MessageBus,
        *,
        stale_seconds: float,
        offline_seconds: float,
        clock: Clock = _utc_now,
        monitor_interval: float | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if offline_seconds <= stale_seconds:
            raise ValueError("offline_seconds must be greater than stale_seconds")
        self.database = database
        self.message_bus = message_bus
        self.stale_seconds = stale_seconds
        self.offline_seconds = offline_seconds
        self.clock = clock
        self.monitor_interval = monitor_interval or min(max(stale_seconds / 3, 0.1), 1.0)
        self._sessions: dict[str, DeviceSession] = {}
        self._observer_id: str | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._running = False
        self._logger = logger or logging.getLogger("otto_master.device_manager")

    @property
    def running(self) -> bool:
        return self._running

    @property
    def device_count(self) -> int:
        return len(self._sessions)

    async def start(self) -> None:
        if self._running:
            return
        await self.database.mark_active_devices_offline()
        await self._restore_devices()
        self._observer_id = await self.message_bus.subscribe_observer(self)
        self._running = True
        self._monitor_task = asyncio.create_task(
            self._monitor_liveness(),
            name="otto-device-liveness",
        )
        self._logger.info("device_manager_started", extra={"event": "device_manager_started"})

    async def shutdown(self) -> None:
        if not self._running and self._observer_id is None:
            return
        self._running = False
        task = self._monitor_task
        self._monitor_task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._observer_id is not None:
            await self.message_bus.unsubscribe_observer(self._observer_id)
            self._observer_id = None
        await self.mark_transport_unavailable("runtime_shutdown", status=DeviceStatus.OFFLINE)
        self._logger.info("device_manager_stopped", extra={"event": "device_manager_stopped"})

    async def __call__(self, message: Message) -> None:
        if message.topic == "device.transport.unavailable":
            reason = message.payload.get("reason")
            await self.mark_transport_unavailable(
                reason if isinstance(reason, str) else "mqtt_gateway_unavailable"
            )
            return
        handlers = {
            "device.connected": self._apply_hello,
            "device.heartbeat.received": self._apply_heartbeat,
            "device.state.received": self._apply_state,
            "device.actions.catalog.received": self._apply_actions,
        }
        handler = handlers.get(message.topic)
        if handler is None:
            return
        device_id = message.payload.get("device_id")
        if not isinstance(device_id, str) or message.target != f"device:{device_id}":
            self._logger.warning(
                "device_message_identity_rejected",
                extra={"event": "device_message_identity_rejected", "message_id": message.message_id},
            )
            return
        now = self.clock()
        state_event: Message | None = None
        async with self._lock:
            session = self._sessions.get(device_id)
            if session is None:
                mac = message.payload.get("mac")
                if not isinstance(mac, str):
                    return
                session = DeviceSession(device_id=device_id, mac=mac, name=device_id)
                self._sessions[device_id] = session
            if not session.enabled:
                return
            try:
                changed = await handler(session, message, now)
            except (DeviceStateError, TypeError, ValueError) as exc:
                session.last_error = f"{type(exc).__name__}: {exc}"
                try:
                    session.transition(DeviceStatus.ERROR)
                except DeviceStateError:
                    pass
                changed = True
            if changed:
                self._apply_name_conflict(session)
                await self._persist(session)
                if message.topic == "device.actions.catalog.received":
                    await self.database.save_device_actions(
                        session.device_id,
                        session.actions,
                    )
                state_event = self._state_event(session, correlation_id=message.message_id)
        if state_event is not None:
            await self.message_bus.publish(state_event)

    async def list_devices(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [
                session.to_public_dict()
                for session in sorted(
                    self._sessions.values(),
                    key=lambda item: (item.name.casefold(), item.device_id),
                )
            ]

    async def get_device(self, device_id: str) -> dict[str, Any] | None:
        async with self._lock:
            session = self._sessions.get(device_id)
            return session.to_public_dict() if session is not None else None

    async def get_actions(self, device_id: str) -> list[dict[str, JsonValue]] | None:
        async with self._lock:
            session = self._sessions.get(device_id)
            if session is None:
                return None
            return [dict(action) for action in session.actions]

    async def evaluate_liveness(self) -> None:
        now = self.clock()
        events: list[Message] = []
        async with self._lock:
            for session in self._sessions.values():
                if session.evaluate_liveness(
                    now,
                    stale_seconds=self.stale_seconds,
                    offline_seconds=self.offline_seconds,
                ):
                    await self._persist(session)
                    events.append(self._state_event(session))
        for event in events:
            await self.message_bus.publish(event)

    async def mark_transport_unavailable(
        self,
        reason: str,
        *,
        status: DeviceStatus = DeviceStatus.ERROR,
    ) -> None:
        events: list[Message] = []
        async with self._lock:
            for session in self._sessions.values():
                if session.status not in {
                    DeviceStatus.CONNECTING,
                    DeviceStatus.ONLINE,
                    DeviceStatus.STALE,
                    DeviceStatus.ERROR,
                }:
                    continue
                previous_error = session.last_error
                had_hello = session.hello_received
                try:
                    transitioned = session.transition(status)
                except DeviceStateError:
                    transitioned = False
                session.hello_received = False
                session.last_error = reason
                changed = transitioned or had_hello or previous_error != reason
                if changed:
                    await self._persist(session)
                    events.append(self._state_event(session))
        for event in events:
            await self.message_bus.publish(event)

    async def _apply_hello(
        self,
        session: DeviceSession,
        message: Message,
        now: datetime,
    ) -> bool:
        return session.apply_hello(dict(message.payload), now)

    async def _apply_heartbeat(
        self,
        session: DeviceSession,
        message: Message,
        now: datetime,
    ) -> bool:
        del message
        return session.apply_heartbeat(now)

    async def _apply_state(
        self,
        session: DeviceSession,
        message: Message,
        now: datetime,
    ) -> bool:
        del now
        return session.apply_state(dict(message.payload))

    async def _apply_actions(
        self,
        session: DeviceSession,
        message: Message,
        now: datetime,
    ) -> bool:
        value = message.payload.get("actions")
        if not isinstance(value, list):
            raise TypeError("actions payload must be a list")
        actions: list[dict[str, JsonValue]] = []
        for item in value:
            if not isinstance(item, dict):
                raise TypeError("each action must be an object")
            actions.append(dict(item))
        return session.apply_actions(actions, now)

    async def _persist(self, session: DeviceSession) -> None:
        snapshot = session.to_public_dict()
        await self.database.upsert_device(
            device_id=session.device_id,
            name=session.name,
            mac=session.mac,
            transport=session.transport,
            status=session.status.value,
            capabilities=session.capabilities,
            ip_address=session.ip_address,
            firmware_version=session.firmware_version,
            last_hello_at=cast(str | None, snapshot["last_hello_at"]),
            last_heartbeat_at=cast(str | None, snapshot["last_heartbeat_at"]),
            action_state=session.action_state.value,
            current_action=session.current_action,
            enabled=session.enabled,
            last_error=session.last_error,
            session_generation=session.session_generation,
        )

    def _apply_name_conflict(self, session: DeviceSession) -> None:
        conflicts = [
            candidate
            for candidate in self._sessions.values()
            if candidate.device_id != session.device_id
            and candidate.name.casefold() == session.name.casefold()
        ]
        if not conflicts:
            return
        requested = session.name
        session.name = f"{requested}-{session.device_id[-4:]}"
        session.last_error = "name_conflict"

    def _state_event(
        self,
        session: DeviceSession,
        *,
        correlation_id: str | None = None,
    ) -> Message:
        payload = cast(Mapping[str, JsonValue], session.to_public_dict())
        return Message.create(
            topic="device.state.changed",
            kind=MessageKind.STATE,
            source="device_manager",
            target=f"device:{session.device_id}",
            payload=payload,
            correlation_id=correlation_id,
        )

    async def _restore_devices(self) -> None:
        rows = await self.database.list_devices(limit=500)
        async with self._lock:
            for row in rows:
                device_id = str(row["device_id"])
                enabled = bool(row["enabled"])
                session = DeviceSession(
                    device_id=device_id,
                    mac=str(row["mac"] or device_id),
                    name=str(row["name"]),
                    transport=str(row["transport"] or "mqtt"),
                    status=DeviceStatus.OFFLINE if enabled else DeviceStatus.DISABLED,
                    firmware_version=cast(str | None, row["firmware_version"]),
                    ip_address=cast(str | None, row["ip_address"]),
                    capabilities=cast(dict[str, JsonValue], row["capabilities"]),
                    last_hello_at=_parse_time(row["last_hello_at"]),
                    last_heartbeat_at=_parse_time(row["last_heartbeat_at"]),
                    action_state=DeviceActionState.UNKNOWN,
                    current_action=None,
                    actions=tuple(await self.database.fetch_device_actions(device_id)),
                    enabled=enabled,
                    last_error=cast(str | None, row["last_error"]),
                    session_generation=int(row["session_generation"]),
                    hello_received=False,
                )
                self._sessions[device_id] = session

    async def _monitor_liveness(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(self.monitor_interval)
                await self.evaluate_liveness()
        except asyncio.CancelledError:
            return
