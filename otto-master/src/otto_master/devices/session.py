"""In-memory state for one stable MAC-derived device identity."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..messages import JsonValue
from .states import DeviceActionState, DeviceStatus, validate_device_transition

_TRANSPORT_PRIORITY = {"mqtt": 0, "websocket": 1, "tcp": 2}


def isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class DeviceSession:
    device_id: str
    mac: str
    name: str
    transport: str = "mqtt"
    status: DeviceStatus = DeviceStatus.UNKNOWN
    firmware_version: str | None = None
    ip_address: str | None = None
    capabilities: dict[str, JsonValue] = field(default_factory=dict)
    last_hello_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    action_state: DeviceActionState = DeviceActionState.UNKNOWN
    current_action: str | None = None
    sound_busy: bool | None = None
    sound_name: str | None = None
    actions: tuple[dict[str, JsonValue], ...] = ()
    actions_updated_at: datetime | None = None
    enabled: bool = True
    last_error: str | None = None
    session_generation: int = 0
    hello_received: bool = False
    transport_last_seen: dict[str, datetime] = field(default_factory=dict)
    transport_profiles: dict[str, dict[str, JsonValue]] = field(default_factory=dict)

    def transition(self, target: DeviceStatus) -> bool:
        if self.status is target:
            return False
        validate_device_transition(self.status, target)
        self.status = target
        return True

    def apply_hello(self, payload: dict[str, JsonValue], now: datetime) -> bool:
        transport = self._payload_transport(payload)
        had_transport = bool(self.transport_last_seen)
        previous_transport = self.transport
        self.transport_last_seen[transport] = now
        self.transport_profiles[transport] = dict(payload)
        self.transport = self._preferred_transport()
        if (
            self.status in {DeviceStatus.UNKNOWN, DeviceStatus.OFFLINE, DeviceStatus.ERROR}
            or self.transport != previous_transport
        ):
            self.transition(DeviceStatus.CONNECTING)
        if transport == self.transport:
            if not had_transport or self.transport != previous_transport:
                self._clear_transport_state()
            self._apply_selected_profile()
        self.hello_received = True
        self.session_generation += 1
        self.last_hello_at = now
        self.last_error = None
        return True

    def apply_heartbeat(self, payload: dict[str, JsonValue], now: datetime) -> bool:
        transport = self._payload_transport(payload)
        previous_transport = self.transport
        self.transport_last_seen[transport] = now
        self.transport = self._preferred_transport()
        if transport == self.transport:
            self.last_heartbeat_at = now
        if not self.hello_received:
            if self.status in {DeviceStatus.UNKNOWN, DeviceStatus.OFFLINE}:
                self.transition(DeviceStatus.CONNECTING)
            return True
        if transport == self.transport:
            if self.status is DeviceStatus.ERROR:
                self.transition(DeviceStatus.CONNECTING)
            self.transition(DeviceStatus.ONLINE)
        elif previous_transport != self.transport:
            self._clear_transport_state()
            self._apply_selected_profile()
            self.transition(DeviceStatus.CONNECTING)
        return True

    def remove_transport(self, transport: str, *, status: DeviceStatus) -> bool:
        if transport not in self.transport_last_seen:
            return False
        self.transport_last_seen.pop(transport, None)
        self.transport_profiles.pop(transport, None)
        if not self.transport_last_seen:
            self.hello_received = False
            self.last_heartbeat_at = None
            return self.transition(status)
        previous_transport = self.transport
        self.transport = self._preferred_transport()
        self.hello_received = True
        self.last_heartbeat_at = self.transport_last_seen[self.transport]
        if self.transport != previous_transport:
            self._clear_transport_state()
            self._apply_selected_profile()
        if self.status in {DeviceStatus.OFFLINE, DeviceStatus.ERROR, DeviceStatus.STALE}:
            if self.status in {DeviceStatus.OFFLINE, DeviceStatus.ERROR}:
                self.transition(DeviceStatus.CONNECTING)
            self.transition(DeviceStatus.ONLINE)
        return True

    def apply_state(self, payload: dict[str, JsonValue]) -> bool:
        raw_state = payload.get("action_state")
        try:
            action_state = (
                DeviceActionState(raw_state)
                if isinstance(raw_state, str)
                else DeviceActionState.UNKNOWN
            )
        except ValueError:
            action_state = DeviceActionState.UNKNOWN
        current_action = payload.get("current_action")
        normalized_action = current_action if isinstance(current_action, str) else None
        raw_sound_busy = payload.get("sound_busy")
        sound_busy = raw_sound_busy if isinstance(raw_sound_busy, bool) else self.sound_busy
        raw_sound_name = payload.get("sound_name")
        sound_name = (
            raw_sound_name if isinstance(raw_sound_name, str) else self.sound_name
        )
        changed = (
            action_state is not self.action_state
            or normalized_action != self.current_action
            or sound_busy is not self.sound_busy
            or sound_name != self.sound_name
        )
        self.action_state = action_state
        self.current_action = normalized_action
        self.sound_busy = sound_busy
        self.sound_name = sound_name
        return changed

    def apply_actions(
        self,
        actions: list[dict[str, JsonValue]],
        now: datetime,
    ) -> bool:
        normalized = tuple(dict(action) for action in actions)
        changed = normalized != self.actions
        self.actions = normalized
        self.actions_updated_at = now
        return changed

    def evaluate_liveness(
        self,
        now: datetime,
        *,
        stale_seconds: float,
        offline_seconds: float,
    ) -> bool:
        if self.status in {DeviceStatus.DISABLED, DeviceStatus.OFFLINE}:
            return False
        expired = [
            transport
            for transport, last_seen in self.transport_last_seen.items()
            if (now - last_seen).total_seconds() >= offline_seconds
        ]
        if expired:
            selected_expired = self.transport in expired
            for transport in expired:
                self.transport_last_seen.pop(transport, None)
                self.transport_profiles.pop(transport, None)
            if not self.transport_last_seen:
                self.hello_received = False
                self.last_heartbeat_at = None
                return self.transition(DeviceStatus.OFFLINE)
            if selected_expired:
                self.transport = self._preferred_transport()
                self.last_heartbeat_at = self.transport_last_seen[self.transport]
                self._clear_transport_state()
                self._apply_selected_profile()
                if self.status in {DeviceStatus.STALE, DeviceStatus.ERROR}:
                    if self.status is DeviceStatus.ERROR:
                        self.transition(DeviceStatus.CONNECTING)
                    self.transition(DeviceStatus.ONLINE)
            return True
        reference = self.transport_last_seen.get(self.transport)
        if reference is None:
            reference = self.last_heartbeat_at or self.last_hello_at
        if reference is None:
            return False
        age = (now - reference).total_seconds()
        if age >= stale_seconds and self.status in {
            DeviceStatus.CONNECTING,
            DeviceStatus.ONLINE,
        }:
            return self.transition(DeviceStatus.STALE)
        return False

    @staticmethod
    def _payload_transport(payload: dict[str, JsonValue]) -> str:
        transport = payload.get("transport")
        if not isinstance(transport, str) or transport not in _TRANSPORT_PRIORITY:
            raise ValueError("device message has an unsupported transport")
        return transport

    def _preferred_transport(self) -> str:
        if not self.transport_last_seen:
            return self.transport
        return min(self.transport_last_seen, key=_TRANSPORT_PRIORITY.__getitem__)

    def _apply_selected_profile(self) -> None:
        profile = self.transport_profiles.get(self.transport)
        if profile is None:
            return
        name = profile.get("name")
        if isinstance(name, str) and name.strip():
            self.name = name.strip()
        firmware = profile.get("firmware_version")
        if isinstance(firmware, str):
            self.firmware_version = firmware
        ip_address = profile.get("ip_address")
        self.ip_address = ip_address if isinstance(ip_address, str) else None
        capabilities = profile.get("capabilities")
        self.capabilities = dict(capabilities) if isinstance(capabilities, dict) else {}

    def _clear_transport_state(self) -> None:
        self.action_state = DeviceActionState.UNKNOWN
        self.current_action = None
        self.sound_busy = None
        self.sound_name = None
        self.actions = ()
        self.actions_updated_at = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "mac": self.mac,
            "status": self.status.value,
            "transport": self.transport,
            "available_transports": sorted(
                self.transport_last_seen,
                key=_TRANSPORT_PRIORITY.__getitem__,
            ),
            "firmware_version": self.firmware_version,
            "ip_address": self.ip_address,
            "capabilities": dict(self.capabilities),
            "last_hello_at": isoformat(self.last_hello_at),
            "last_heartbeat_at": isoformat(self.last_heartbeat_at),
            "action_state": self.action_state.value,
            "current_action": self.current_action,
            "sound_busy": self.sound_busy,
            "sound_name": self.sound_name,
            "actions_count": len(self.actions),
            "actions_updated_at": isoformat(self.actions_updated_at),
            "enabled": self.enabled,
            "last_error": self.last_error,
            "session_generation": self.session_generation,
        }
