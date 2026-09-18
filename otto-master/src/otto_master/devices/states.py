"""Device and action state contracts."""

from __future__ import annotations

from enum import Enum


class DeviceStatus(str, Enum):
    UNKNOWN = "unknown"
    PROVISIONING = "provisioning"
    CONNECTING = "connecting"
    ONLINE = "online"
    STALE = "stale"
    OFFLINE = "offline"
    DISABLED = "disabled"
    ERROR = "error"


class DeviceActionState(str, Enum):
    UNKNOWN = "unknown"
    IDLE = "idle"
    MOVING = "moving"


class DeviceStateError(RuntimeError):
    """Raised when code attempts an impossible device transition."""


_ALLOWED_TRANSITIONS: dict[DeviceStatus, frozenset[DeviceStatus]] = {
    DeviceStatus.UNKNOWN: frozenset(
        {
            DeviceStatus.PROVISIONING,
            DeviceStatus.CONNECTING,
            DeviceStatus.OFFLINE,
            DeviceStatus.DISABLED,
            DeviceStatus.ERROR,
        }
    ),
    DeviceStatus.PROVISIONING: frozenset(
        {
            DeviceStatus.CONNECTING,
            DeviceStatus.OFFLINE,
            DeviceStatus.DISABLED,
            DeviceStatus.ERROR,
        }
    ),
    DeviceStatus.CONNECTING: frozenset(
        {
            DeviceStatus.ONLINE,
            DeviceStatus.STALE,
            DeviceStatus.OFFLINE,
            DeviceStatus.DISABLED,
            DeviceStatus.ERROR,
        }
    ),
    DeviceStatus.ONLINE: frozenset(
        {
            DeviceStatus.CONNECTING,
            DeviceStatus.STALE,
            DeviceStatus.OFFLINE,
            DeviceStatus.DISABLED,
            DeviceStatus.ERROR,
        }
    ),
    DeviceStatus.STALE: frozenset(
        {
            DeviceStatus.CONNECTING,
            DeviceStatus.ONLINE,
            DeviceStatus.OFFLINE,
            DeviceStatus.DISABLED,
            DeviceStatus.ERROR,
        }
    ),
    DeviceStatus.OFFLINE: frozenset(
        {
            DeviceStatus.PROVISIONING,
            DeviceStatus.CONNECTING,
            DeviceStatus.DISABLED,
            DeviceStatus.ERROR,
        }
    ),
    DeviceStatus.DISABLED: frozenset({DeviceStatus.OFFLINE}),
    DeviceStatus.ERROR: frozenset(
        {DeviceStatus.CONNECTING, DeviceStatus.OFFLINE, DeviceStatus.DISABLED}
    ),
}


def validate_device_transition(current: DeviceStatus, target: DeviceStatus) -> None:
    if current is target:
        return
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise DeviceStateError(f"invalid device transition: {current.value} -> {target.value}")
