"""Embedded MQTT 3.1.1 broker with Otto-specific authentication and ACLs."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
import secrets
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from amqtt.broker import Broker  # type: ignore[import-untyped]
from amqtt.contexts import Action, BaseContext  # type: ignore[import-untyped]
from amqtt.plugins.base import (  # type: ignore[import-untyped]
    BaseAuthPlugin,
    BasePlugin,
    BaseTopicPlugin,
)
from amqtt.session import Session  # type: ignore[import-untyped]

from ..config import MqttConfig


class MqttBrokerError(RuntimeError):
    """Raised when broker credentials or lifecycle operations fail."""


class BrokerState(str, Enum):
    DISABLED = "disabled"
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class CredentialRole(str, Enum):
    MASTER = "master"
    DEVICE = "device"


_DEVICE_ID_PATTERN = re.compile(r"^[0-9a-f]{12}$")
_DEVICE_UP_PATTERN = re.compile(r"^otto/v1/devices/(?P<device_id>[0-9a-f]{12})/up$")
_DEVICE_DOWN_PATTERN = re.compile(r"^otto/v1/devices/(?P<device_id>[0-9a-f]{12})/down$")


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def normalize_device_id(value: str) -> str:
    """Normalize a MAC-derived device ID and reject non-MAC identifiers."""

    normalized = re.sub(r"[:-]", "", value.strip().lower())
    if not _DEVICE_ID_PATTERN.fullmatch(normalized):
        raise ValueError("device_id must be a 12-digit hexadecimal MAC address")
    return normalized


@dataclass(frozen=True, slots=True)
class MqttCredential:
    username: str
    password: str = field(repr=False)
    client_id: str
    role: CredentialRole
    device_id: str | None


@dataclass(frozen=True, slots=True)
class DeviceProvisioning:
    device_id: str
    endpoint: str
    client_id: str
    username: str
    password: str = field(repr=False)
    publish_topic: str
    subscribe_topic: str


class MqttCredentialStore:
    """Persist local MQTT credentials while keeping them out of normal data APIs."""

    def __init__(
        self,
        path: Path,
        *,
        master_username: str,
        configured_master_password: str | None = None,
    ) -> None:
        self.path = path
        self.master_username = master_username
        self.configured_master_password = configured_master_password
        self._credentials: dict[str, MqttCredential] = {}
        self._device_usernames: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._loaded = False

    async def start(self) -> None:
        async with self._lock:
            if self._loaded:
                return
            self._load()
            changed = self._ensure_master()
            if changed:
                self._write()
            self._loaded = True

    async def master_credential(self) -> MqttCredential:
        await self.start()
        credential = self._credentials.get(self.master_username)
        if credential is None or credential.role is not CredentialRole.MASTER:
            raise MqttBrokerError("master MQTT credential is unavailable")
        return credential

    async def provision_device(self, value: str) -> MqttCredential:
        device_id = normalize_device_id(value)
        await self.start()
        async with self._lock:
            username = self._device_usernames.get(device_id)
            if username is not None:
                return self._credentials[username]
            username = f"device-{device_id}"
            credential = MqttCredential(
                username=username,
                password=secrets.token_urlsafe(32),
                client_id=f"otto-{device_id}",
                role=CredentialRole.DEVICE,
                device_id=device_id,
            )
            self._credentials[username] = credential
            self._device_usernames[device_id] = username
            self._write()
            return credential

    def authenticate(self, username: str | None, password: str | None, client_id: str | None) -> bool:
        if not self._loaded or not username or password is None or not client_id:
            return False
        credential = self._credentials.get(username)
        if credential is None or credential.client_id != client_id:
            return False
        return hmac.compare_digest(credential.password, password)

    def authorize(self, username: str | None, topic: str | None, action: Action | None) -> bool:
        if not self._loaded or not username or not topic or action is None:
            return False
        credential = self._credentials.get(username)
        if credential is None:
            return False
        if credential.role is CredentialRole.MASTER:
            return self._authorize_master(topic, action)
        if credential.device_id is None:
            return False
        return self._authorize_device(credential.device_id, topic, action)

    @staticmethod
    def _authorize_master(topic: str, action: Action) -> bool:
        if action is Action.SUBSCRIBE:
            return topic == "otto/v1/devices/+/up" or _DEVICE_UP_PATTERN.fullmatch(topic) is not None
        if action is Action.PUBLISH:
            return _DEVICE_DOWN_PATTERN.fullmatch(topic) is not None
        if action is Action.RECEIVE:
            return _DEVICE_UP_PATTERN.fullmatch(topic) is not None
        return False

    @staticmethod
    def _authorize_device(device_id: str, topic: str, action: Action) -> bool:
        if action is Action.PUBLISH:
            return topic == f"otto/v1/devices/{device_id}/up"
        if action in {Action.SUBSCRIBE, Action.RECEIVE}:
            return topic == f"otto/v1/devices/{device_id}/down"
        return False

    def _ensure_master(self) -> bool:
        existing = self._credentials.get(self.master_username)
        if existing is not None and existing.role is not CredentialRole.MASTER:
            raise MqttBrokerError("configured MQTT master username belongs to a device")
        password = self.configured_master_password
        if existing is not None and password is None:
            return False
        if password is None:
            password = secrets.token_urlsafe(32)
        updated = MqttCredential(
            username=self.master_username,
            password=password,
            client_id="otto-master",
            role=CredentialRole.MASTER,
            device_id=None,
        )
        if existing == updated:
            return False
        self._credentials[self.master_username] = updated
        return True

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("version") != 1:
                raise ValueError("unsupported credential store version")
            users = raw.get("users")
            if not isinstance(users, list):
                raise TypeError("users must be a list")
            for item in users:
                self._load_item(item)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise MqttBrokerError(f"invalid MQTT credential store: {self.path}") from exc

    def _load_item(self, item: Any) -> None:
        if not isinstance(item, dict):
            raise TypeError("credential entry must be an object")
        username = item.get("username")
        password = item.get("password")
        client_id = item.get("client_id")
        role_value = item.get("role")
        device_id_value = item.get("device_id")
        if not isinstance(username, str) or not username:
            raise ValueError("credential entry has invalid username")
        if not isinstance(password, str) or not password:
            raise ValueError("credential entry has invalid password")
        if not isinstance(client_id, str) or not client_id:
            raise ValueError("credential entry has invalid strings")
        role = CredentialRole(role_value)
        device_id: str | None = None
        if role is CredentialRole.DEVICE:
            if not isinstance(device_id_value, str):
                raise ValueError("device credential is missing device_id")
            device_id = normalize_device_id(device_id_value)
        elif device_id_value is not None:
            raise ValueError("master credential must not have device_id")
        if username in self._credentials:
            raise ValueError("duplicate MQTT username")
        credential = MqttCredential(
            username=username,
            password=password,
            client_id=client_id,
            role=role,
            device_id=device_id,
        )
        self._credentials[username] = credential
        if device_id is not None:
            if device_id in self._device_usernames:
                raise ValueError("duplicate MQTT device_id")
            self._device_usernames[device_id] = username

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "users": [
                {
                    "username": credential.username,
                    "password": credential.password,
                    "client_id": credential.client_id,
                    "role": credential.role.value,
                    "device_id": credential.device_id,
                }
                for credential in sorted(
                    self._credentials.values(), key=lambda item: item.username
                )
            ],
        }
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            try:
                temporary_path.chmod(0o600)
            except OSError:
                pass
            os.replace(temporary_path, self.path)
            try:
                self.path.chmod(0o600)
            except OSError:
                pass
        finally:
            temporary_path.unlink(missing_ok=True)


class _BrokerMetrics:
    def __init__(self) -> None:
        self.sessions: dict[str, int] = {}

    @property
    def connected_clients(self) -> int:
        return len(self.sessions)

    def connected(self, client_id: str, session: object) -> None:
        self.sessions[client_id] = id(session)

    def disconnected(self, client_id: str, session: object) -> None:
        if self.sessions.get(client_id) == id(session):
            self.sessions.pop(client_id, None)


@dataclass(slots=True)
class _BrokerRegistry:
    credentials: MqttCredentialStore
    metrics: _BrokerMetrics


_BROKER_REGISTRIES: dict[str, _BrokerRegistry] = {}


def _registry(registry_id: str) -> _BrokerRegistry:
    try:
        return _BROKER_REGISTRIES[registry_id]
    except KeyError as exc:
        raise MqttBrokerError("MQTT plugin registry is unavailable") from exc


class OttoAuthPlugin(BaseAuthPlugin):  # type: ignore[misc]
    """aMQTT plugin that rejects anonymous and mismatched client identities."""

    @dataclass
    class Config:
        registry_id: str

    async def authenticate(self, *, session: Session) -> bool:
        return _registry(self.config.registry_id).credentials.authenticate(
            session.username,
            session.password,
            session.client_id,
        )


class OttoTopicAclPlugin(BaseTopicPlugin):  # type: ignore[misc]
    """aMQTT plugin enforcing exact per-device up/down topic access."""

    @dataclass
    class Config:
        registry_id: str

    async def topic_filtering(
        self,
        *,
        session: Session | None = None,
        topic: str | None = None,
        action: Action | None = None,
    ) -> bool:
        username = session.username if session is not None else None
        return _registry(self.config.registry_id).credentials.authorize(username, topic, action)


class OttoBrokerMetricsPlugin(BasePlugin[BaseContext]):  # type: ignore[misc]
    """aMQTT event plugin exposing only aggregate connection health."""

    @dataclass
    class Config:
        registry_id: str

    async def on_broker_client_connected(
        self, *, client_id: str, client_session: object
    ) -> None:
        _registry(self.config.registry_id).metrics.connected(client_id, client_session)

    async def on_broker_client_disconnected(
        self, *, client_id: str, client_session: object
    ) -> None:
        _registry(self.config.registry_id).metrics.disconnected(client_id, client_session)


class EmbeddedMqttBroker:
    """Lifecycle adapter around aMQTT that hides third-party types from the runtime."""

    def __init__(
        self,
        config: MqttConfig,
        credentials_path: Path,
        *,
        configured_master_password: str | None = None,
        public_host: str = "master.local",
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.public_host = public_host.rstrip(".")
        self.credentials = MqttCredentialStore(
            credentials_path,
            master_username=config.master_username,
            configured_master_password=configured_master_password,
        )
        self._state = BrokerState.CREATED if config.enabled else BrokerState.DISABLED
        self._broker: Broker | None = None
        self._registry_id: str | None = None
        self._metrics = _BrokerMetrics()
        self._last_error: str | None = None
        self._logger = logger or logging.getLogger("otto_master.mqtt_broker")

    @property
    def state(self) -> BrokerState:
        return self._state

    @property
    def running(self) -> bool:
        return self._state is BrokerState.RUNNING

    async def start(self) -> None:
        if self._state is BrokerState.DISABLED:
            return
        if self._state is BrokerState.RUNNING:
            return
        if self._state not in {BrokerState.CREATED, BrokerState.STOPPED, BrokerState.ERROR}:
            raise MqttBrokerError(f"broker cannot start from state {self._state.value}")
        self._state = BrokerState.STARTING
        self._last_error = None
        await self.credentials.start()
        registry_id = str(uuid4())
        _BROKER_REGISTRIES[registry_id] = _BrokerRegistry(self.credentials, self._metrics)
        self._registry_id = registry_id
        config: dict[str, Any] = {
            "listeners": {
                "default": {
                    "type": "tcp",
                    "bind": f"{self.config.host}:{self.config.port}",
                    "max_connections": self.config.max_connections,
                }
            },
            "timeout_disconnect_delay": 0,
            "plugins": {
                "otto_master.gateways.mqtt_broker.OttoAuthPlugin": {
                    "registry_id": registry_id
                },
                "otto_master.gateways.mqtt_broker.OttoTopicAclPlugin": {
                    "registry_id": registry_id
                },
                "otto_master.gateways.mqtt_broker.OttoBrokerMetricsPlugin": {
                    "registry_id": registry_id
                },
            },
        }
        broker = Broker(config)
        try:
            await broker.start()
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._state = BrokerState.ERROR
            _BROKER_REGISTRIES.pop(registry_id, None)
            self._registry_id = None
            raise MqttBrokerError(
                f"failed to start MQTT broker on {self.config.host}:{self.config.port}"
            ) from exc
        self._broker = broker
        self._state = BrokerState.RUNNING
        self._logger.info(
            "mqtt_broker_started",
            extra={
                "event": "mqtt_broker_started",
                "host": self.config.host,
                "port": self.config.port,
                "anonymous": False,
            },
        )

    async def shutdown(self) -> None:
        if self._state in {BrokerState.DISABLED, BrokerState.STOPPED}:
            return
        self._state = BrokerState.STOPPING
        broker = self._broker
        self._broker = None
        try:
            if broker is not None:
                await broker.shutdown()
        finally:
            if self._registry_id is not None:
                _BROKER_REGISTRIES.pop(self._registry_id, None)
                self._registry_id = None
            self._metrics.sessions.clear()
            self._state = BrokerState.STOPPED
            self._logger.info("mqtt_broker_stopped", extra={"event": "mqtt_broker_stopped"})

    async def provision_device(self, value: str) -> DeviceProvisioning:
        if not self.running:
            raise MqttBrokerError("MQTT broker is not running")
        credential = await self.credentials.provision_device(value)
        if credential.device_id is None:
            raise MqttBrokerError("provisioned credential has no device identity")
        return DeviceProvisioning(
            device_id=credential.device_id,
            endpoint=f"mqtt://{self.public_host}:{self.config.port}",
            client_id=credential.client_id,
            username=credential.username,
            password=credential.password,
            publish_topic=f"otto/v1/devices/{credential.device_id}/up",
            subscribe_topic=f"otto/v1/devices/{credential.device_id}/down",
        )

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "state": self._state.value,
            "healthy": self.running,
            "host": self.config.host,
            "port": self.config.port,
            "public_endpoint": f"mqtt://{self.public_host}:{self.config.port}",
            "protocol": "mqtt-3.1.1",
            "connected_clients": self._metrics.connected_clients,
            "authentication": "required",
            "anonymous": False,
            "acl": "per-device",
            "last_error": self._last_error,
            "checked_at": utc_now(),
        }
