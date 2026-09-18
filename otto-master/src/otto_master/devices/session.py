"""In-memory state for one stable MAC-derived device identity."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..messages import JsonValue
from .states import DeviceActionState, DeviceStatus, validate_device_transition


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
    actions: tuple[dict[str, JsonValue], ...] = ()
    actions_updated_at: datetime | None = None
    enabled: bool = True
    last_error: str | None = None
    session_generation: int = 0
    hello_received: bool = False

    def transition(self, target: DeviceStatus) -> bool:
        if self.status is target:
            return False
        validate_device_transition(self.status, target)
        self.status = target
        return True

    def apply_hello(self, payload: dict[str, JsonValue], now: datetime) -> bool:
        self.transition(DeviceStatus.CONNECTING)
        self.hello_received = True
        self.session_generation += 1
        self.last_hello_at = now
        self.last_error = None
        changed = True
        name = payload.get("name")
        if isinstance(name, str) and name.strip() and name.strip() != self.name:
            self.name = name.strip()
            changed = True
        firmware = payload.get("firmware_version")
        if isinstance(firmware, str) and firmware != self.firmware_version:
            self.firmware_version = firmware
            changed = True
        ip_address = payload.get("ip_address")
        if isinstance(ip_address, str) and ip_address != self.ip_address:
            self.ip_address = ip_address
            changed = True
        capabilities = payload.get("capabilities")
        if isinstance(capabilities, dict) and capabilities != self.capabilities:
            self.capabilities = dict(capabilities)
            changed = True
        return changed

    def apply_heartbeat(self, now: datetime) -> bool:
        self.last_heartbeat_at = now
        if not self.hello_received:
            if self.status in {DeviceStatus.UNKNOWN, DeviceStatus.OFFLINE}:
                self.transition(DeviceStatus.CONNECTING)
            return True
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
        changed = action_state is not self.action_state or normalized_action != self.current_action
        self.action_state = action_state
        self.current_action = normalized_action
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
        reference = self.last_heartbeat_at or self.last_hello_at
        if reference is None:
            return False
        age = (now - reference).total_seconds()
        if age >= offline_seconds:
            self.hello_received = False
            return self.transition(DeviceStatus.OFFLINE)
        if age >= stale_seconds and self.status in {
            DeviceStatus.CONNECTING,
            DeviceStatus.ONLINE,
        }:
            return self.transition(DeviceStatus.STALE)
        return False

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "mac": self.mac,
            "status": self.status.value,
            "transport": self.transport,
            "firmware_version": self.firmware_version,
            "ip_address": self.ip_address,
            "capabilities": dict(self.capabilities),
            "last_hello_at": isoformat(self.last_hello_at),
            "last_heartbeat_at": isoformat(self.last_heartbeat_at),
            "action_state": self.action_state.value,
            "current_action": self.current_action,
            "actions_count": len(self.actions),
            "actions_updated_at": isoformat(self.actions_updated_at),
            "enabled": self.enabled,
            "last_error": self.last_error,
            "session_generation": self.session_generation,
        }
