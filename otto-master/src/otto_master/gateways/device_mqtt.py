"""Authenticated MQTT device uplink and external-to-domain message translation."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, NoReturn, cast
from urllib.parse import quote

from amqtt.client import MQTTClient  # type: ignore[import-untyped]

from ..config import MqttConfig
from ..message_bus import MessageBus
from ..messages import JsonValue, Message, MessageKind
from .mqtt_broker import EmbeddedMqttBroker, normalize_device_id

MAX_DEVICE_MESSAGE_BYTES = 64 * 1024
MAX_DEVICE_ACTIONS = 128
MAX_JSON_DEPTH = 16
MAX_JSON_NODES = 4096
_UP_TOPIC = re.compile(r"^otto/v1/devices/(?P<device_id>[0-9a-f]{12})/up$")
_QUERY_TYPES = {
    "device.state.query.requested": "otto_query",
    "device.actions.query.requested": "otto_actions",
}


class DeviceMqttError(RuntimeError):
    """Raised when the gateway lifecycle cannot be established."""


class DeviceMessageError(ValueError):
    """Raised when an untrusted device payload violates the MQTT contract."""


class DeviceMqttState(str, Enum):
    DISABLED = "disabled"
    CREATED = "created"
    CONNECTING = "connecting"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


def _reject_constant(value: str) -> NoReturn:
    raise DeviceMessageError(f"non-finite JSON number is not allowed: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DeviceMessageError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _decode_payload(payload: bytes) -> dict[str, Any]:
    if not payload:
        raise DeviceMessageError("device payload must not be empty")
    if len(payload) > MAX_DEVICE_MESSAGE_BYTES:
        raise DeviceMessageError(
            f"device payload exceeds {MAX_DEVICE_MESSAGE_BYTES} bytes"
        )
    try:
        text = payload.decode("utf-8")
        value = json.loads(
            text,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except UnicodeDecodeError as exc:
        raise DeviceMessageError("device payload must be UTF-8") from exc
    except DeviceMessageError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise DeviceMessageError("device payload must be valid JSON") from exc
    if not isinstance(value, dict):
        raise DeviceMessageError("device payload must be a JSON object")
    _validate_json_shape(value)
    return value


def _validate_json_shape(value: Any) -> None:
    remaining = MAX_JSON_NODES

    def visit(item: Any, depth: int) -> None:
        nonlocal remaining
        remaining -= 1
        if remaining < 0:
            raise DeviceMessageError(f"device JSON exceeds {MAX_JSON_NODES} values")
        if depth > MAX_JSON_DEPTH:
            raise DeviceMessageError(f"device JSON exceeds depth {MAX_JSON_DEPTH}")
        if isinstance(item, dict):
            for child in item.values():
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)

    visit(value, 0)


def _required_string(
    payload: dict[str, Any],
    field: str,
    *,
    maximum: int,
) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise DeviceMessageError(f"{field} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise DeviceMessageError(f"{field} exceeds {maximum} characters")
    return normalized


def _optional_external_id(payload: dict[str, Any]) -> str | None:
    if "id" not in payload:
        return None
    return _required_string(payload, "id", maximum=128)


def _identity(topic: str, payload: dict[str, Any]) -> str:
    match = _UP_TOPIC.fullmatch(topic)
    if match is None:
        raise DeviceMessageError("message arrived on an invalid device uplink topic")
    device_id = match.group("device_id")
    supplied_mac = payload.get("mac")
    if supplied_mac is not None:
        if not isinstance(supplied_mac, str):
            raise DeviceMessageError("mac must be a string")
        try:
            payload_device_id = normalize_device_id(supplied_mac)
        except ValueError as exc:
            raise DeviceMessageError("mac is not a valid hardware address") from exc
        if payload_device_id != device_id:
            raise DeviceMessageError("payload MAC does not match authenticated MQTT topic")
    return device_id


def _base_payload(
    device_id: str,
    external_message_id: str | None,
) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {
        "device_id": device_id,
        "mac": device_id,
        "transport": "mqtt",
    }
    if external_message_id is not None:
        result["external_message_id"] = external_message_id
    return result


def _message(
    *,
    device_id: str,
    topic: str,
    kind: MessageKind,
    payload: dict[str, JsonValue],
    correlation_id: str | None = None,
) -> Message:
    return Message.create(
        topic=topic,
        kind=kind,
        source=f"device:{device_id}:mqtt",
        target=f"device:{device_id}",
        payload=payload,
        correlation_id=correlation_id,
    )


def _hello(device_id: str, value: dict[str, Any]) -> Message:
    protocol = _required_string(value, "protocol", maximum=32)
    if protocol != "otto-mqtt/1":
        raise DeviceMessageError("unsupported device MQTT protocol")
    if "mac" not in value:
        raise DeviceMessageError("hello must include mac")
    name = _required_string(value, "name", maximum=80)
    firmware_version = _required_string(value, "firmware_version", maximum=80)
    capabilities = value.get("capabilities", {})
    if not isinstance(capabilities, dict):
        raise DeviceMessageError("capabilities must be an object")
    payload = _base_payload(device_id, _optional_external_id(value))
    payload.update(
        {
            "protocol": protocol,
            "name": name,
            "device_name": name,
            "firmware_version": firmware_version,
            "capabilities": cast(dict[str, JsonValue], capabilities),
        }
    )
    ip_address = value.get("ip_address")
    if ip_address is not None:
        if not isinstance(ip_address, str):
            raise DeviceMessageError("ip_address must be a string")
        try:
            payload["ip_address"] = str(ipaddress.ip_address(ip_address.strip()))
        except ValueError as exc:
            raise DeviceMessageError("ip_address is invalid") from exc
    return _message(
        device_id=device_id,
        topic="device.connected",
        kind=MessageKind.EVENT,
        payload=payload,
    )


def _heartbeat(device_id: str, value: dict[str, Any]) -> Message:
    payload = _base_payload(device_id, _optional_external_id(value))
    return _message(
        device_id=device_id,
        topic="device.heartbeat.received",
        kind=MessageKind.EVENT,
        payload=payload,
    )


def _runtime_action(value: dict[str, Any]) -> tuple[Any, Any]:
    state = value.get("action_state")
    action_name = value.get("current_action")
    runtime = value.get("runtime")
    if isinstance(runtime, dict):
        otto = runtime.get("otto")
        if isinstance(otto, dict):
            action = otto.get("action")
            if isinstance(action, dict):
                state = action.get("state", state)
                action_name = action.get("name", action_name)
    return state, action_name


def _state(device_id: str, value: dict[str, Any]) -> Message:
    action_state, current_action = _runtime_action(value)
    if action_state not in {"unknown", "idle", "moving"}:
        raise DeviceMessageError("state must contain a known action state")
    if current_action is not None and (
        not isinstance(current_action, str) or not current_action.strip()
    ):
        raise DeviceMessageError("current action must be a non-empty string or null")
    normalized_action = current_action.strip() if isinstance(current_action, str) else None
    if normalized_action is not None and len(normalized_action) > 80:
        raise DeviceMessageError("current action exceeds 80 characters")
    payload = _base_payload(device_id, _optional_external_id(value))
    payload["action_state"] = cast(str, action_state)
    payload["current_action"] = normalized_action
    return _message(
        device_id=device_id,
        topic="device.state.received",
        kind=MessageKind.STATE,
        payload=payload,
        correlation_id=_optional_external_id(value),
    )


def _actions(device_id: str, value: dict[str, Any]) -> Message:
    raw_actions = value.get("actions")
    if not isinstance(raw_actions, list):
        raise DeviceMessageError("actions must be a list")
    if len(raw_actions) > MAX_DEVICE_ACTIONS:
        raise DeviceMessageError(f"actions exceeds {MAX_DEVICE_ACTIONS} entries")
    actions: list[dict[str, JsonValue]] = []
    names: set[str] = set()
    for item in raw_actions:
        if isinstance(item, str):
            normalized: dict[str, JsonValue] = {"name": item.strip()}
        elif isinstance(item, dict):
            normalized = cast(dict[str, JsonValue], dict(item))
        else:
            raise DeviceMessageError("each action must be a string or object")
        name = normalized.get("name")
        if not isinstance(name, str) or not name.strip():
            raise DeviceMessageError("each action must have a non-empty name")
        name = name.strip()
        if len(name) > 80:
            raise DeviceMessageError("action name exceeds 80 characters")
        if name in names:
            raise DeviceMessageError(f"duplicate action name: {name}")
        names.add(name)
        normalized["name"] = name
        actions.append(normalized)
    payload = _base_payload(device_id, _optional_external_id(value))
    payload["actions"] = cast(list[JsonValue], actions)
    return _message(
        device_id=device_id,
        topic="device.actions.catalog.received",
        kind=MessageKind.EVENT,
        payload=payload,
        correlation_id=_optional_external_id(value),
    )


def _ack(device_id: str, value: dict[str, Any], *, stop: bool) -> Message:
    external_id = _required_string(value, "id", maximum=128)
    ok = value.get("ok")
    if not isinstance(ok, bool):
        raise DeviceMessageError("ack ok must be a boolean")
    payload = _base_payload(device_id, external_id)
    payload["accepted"] = ok
    action = value.get("action")
    if action is not None:
        if not isinstance(action, str) or not action.strip() or len(action.strip()) > 80:
            raise DeviceMessageError("ack action must be a valid action name")
        payload["action"] = action.strip()
    error = value.get("error")
    if error is not None:
        if not isinstance(error, str) or len(error) > 512:
            raise DeviceMessageError("ack error must be a bounded string")
        payload["error"] = error
    if stop:
        internal_topic = "robot.stop.completed" if ok else "robot.action.failed"
    else:
        internal_topic = "robot.action.dispatched" if ok else "robot.action.failed"
    return _message(
        device_id=device_id,
        topic=internal_topic,
        kind=MessageKind.RESULT,
        payload=payload,
        correlation_id=external_id,
    )


def _error(device_id: str, value: dict[str, Any]) -> Message:
    external_id = _required_string(value, "id", maximum=128)
    error = _required_string(value, "error", maximum=512)
    payload = _base_payload(device_id, external_id)
    payload.update({"accepted": False, "error": error})
    return _message(
        device_id=device_id,
        topic="robot.action.failed",
        kind=MessageKind.RESULT,
        payload=payload,
        correlation_id=external_id,
    )


def translate_device_message(topic: str, payload: bytes) -> tuple[Message, ...]:
    """Validate one untrusted uplink packet and return domain messages."""

    value = _decode_payload(payload)
    device_id = _identity(topic, value)
    message_type = _required_string(value, "type", maximum=64)
    translators = {
        "hello": _hello,
        "heartbeat": _heartbeat,
        "otto_state": _state,
        "otto_actions": _actions,
    }
    translator = translators.get(message_type)
    if translator is not None:
        return (translator(device_id, value),)
    if message_type == "otto_action_ack":
        return (_ack(device_id, value, stop=False),)
    if message_type == "otto_stop_ack":
        return (_ack(device_id, value, stop=True),)
    if message_type == "error":
        return (_error(device_id, value),)
    raise DeviceMessageError(f"unsupported device message type: {message_type}")


@dataclass(frozen=True, slots=True)
class EncodedDeviceCommand:
    device_id: str
    topic: str
    payload: bytes


def encode_device_command(message: Message) -> EncodedDeviceCommand:
    """Encode an allow-listed internal query for one exact MQTT down topic."""

    external_type = _QUERY_TYPES.get(message.topic)
    if external_type is None or message.kind is not MessageKind.COMMAND:
        raise DeviceMessageError("unsupported outbound device command")
    device_id_value = message.payload.get("device_id")
    if not isinstance(device_id_value, str):
        raise DeviceMessageError("outbound command requires device_id")
    try:
        device_id = normalize_device_id(device_id_value)
    except ValueError as exc:
        raise DeviceMessageError("outbound command has invalid device_id") from exc
    if message.target != f"device:{device_id}":
        raise DeviceMessageError("outbound command target does not match device_id")
    if len(message.message_id) > 128:
        raise DeviceMessageError("outbound command ID exceeds 128 characters")
    payload = json.dumps(
        {"type": external_type, "id": message.message_id},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return EncodedDeviceCommand(
        device_id=device_id,
        topic=f"otto/v1/devices/{device_id}/down",
        payload=payload,
    )


class DeviceMqttGateway:
    """Subscribe as the broker's master identity and feed validated domain messages."""

    def __init__(
        self,
        config: MqttConfig,
        broker: EmbeddedMqttBroker,
        message_bus: MessageBus,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.broker = broker
        self.message_bus = message_bus
        self._state = DeviceMqttState.CREATED if config.enabled else DeviceMqttState.DISABLED
        self._client: MQTTClient | None = None
        self._receive_task: asyncio.Task[None] | None = None
        self._command_subscription_ids: list[str] = []
        self._client_lock = asyncio.Lock()
        self._stopping = False
        self._last_error: str | None = None
        self._messages_received = 0
        self._messages_accepted = 0
        self._messages_rejected = 0
        self._messages_published = 0
        self._publish_failures = 0
        self._reconnect_attempts = 0
        self._logger = logger or logging.getLogger("otto_master.device_mqtt")

    @property
    def running(self) -> bool:
        return self._state is DeviceMqttState.RUNNING

    async def start(self) -> None:
        if self._state is DeviceMqttState.DISABLED or self.running:
            return
        if self._state not in {
            DeviceMqttState.CREATED,
            DeviceMqttState.STOPPED,
            DeviceMqttState.ERROR,
        }:
            raise DeviceMqttError(f"gateway cannot start from state {self._state.value}")
        self._stopping = False
        self._state = DeviceMqttState.CONNECTING
        self._last_error = None
        try:
            await self._connect()
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._state = DeviceMqttState.ERROR
            raise DeviceMqttError("failed to connect MQTT device gateway") from exc
        for topic in _QUERY_TYPES:
            subscription_id = await self.message_bus.subscribe(topic, self._publish_command)
            self._command_subscription_ids.append(subscription_id)
        self._receive_task = asyncio.create_task(
            self._receive_loop(),
            name="otto-device-mqtt",
        )
        self._logger.info(
            "device_mqtt_started",
            extra={"event": "device_mqtt_started"},
        )

    async def shutdown(self) -> None:
        if self._state in {DeviceMqttState.DISABLED, DeviceMqttState.STOPPED}:
            return
        if self._state is DeviceMqttState.CREATED:
            self._state = DeviceMqttState.STOPPED
            return
        self._stopping = True
        self._state = DeviceMqttState.STOPPING
        for subscription_id in self._command_subscription_ids:
            await self.message_bus.unsubscribe(subscription_id)
        self._command_subscription_ids.clear()
        task = self._receive_task
        self._receive_task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._disconnect_client()
        await self._publish_transport_unavailable("mqtt_gateway_stopped")
        self._state = DeviceMqttState.STOPPED
        self._logger.info(
            "device_mqtt_stopped",
            extra={"event": "device_mqtt_stopped"},
        )

    def status(self) -> dict[str, Any]:
        enabled = self.config.enabled
        return {
            "enabled": enabled,
            "healthy": self.running if enabled else True,
            "state": self._state.value,
            "subscription": "otto/v1/devices/+/up" if enabled else None,
            "messages_received": self._messages_received,
            "messages_accepted": self._messages_accepted,
            "messages_rejected": self._messages_rejected,
            "messages_published": self._messages_published,
            "publish_failures": self._publish_failures,
            "reconnect_attempts": self._reconnect_attempts,
            "last_error": self._last_error,
        }

    async def _connect(self) -> None:
        if not self.broker.running:
            raise DeviceMqttError("embedded MQTT broker is not running")
        credential = await self.broker.credentials.master_credential()
        client = MQTTClient(
            client_id=credential.client_id,
            config={"auto_reconnect": False, "reconnect_retries": 0, "plugins": {}},
        )
        host = self.config.host
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
        uri_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        uri = (
            f"mqtt://{quote(credential.username, safe='')}:"
            f"{quote(credential.password, safe='')}@{uri_host}:{self.config.port}/"
        )
        try:
            await client.connect(uri)
            result = await client.subscribe([("otto/v1/devices/+/up", 0)])
            if result != [0]:
                raise DeviceMqttError("broker rejected the device uplink subscription")
        except Exception:
            try:
                await client.disconnect()
            except Exception:
                self._logger.debug(
                    "device_mqtt_failed_connect_cleanup",
                    exc_info=True,
                    extra={"event": "device_mqtt_failed_connect_cleanup"},
                )
            raise
        async with self._client_lock:
            self._client = client
        self._state = DeviceMqttState.RUNNING
        self._last_error = None

    async def _receive_loop(self) -> None:
        while not self._stopping:
            client = self._client
            if client is None:
                if not await self._reconnect():
                    return
                continue
            try:
                packet = await client.deliver_message()
                if packet is None:
                    raise DeviceMqttError("MQTT connection ended")
                self._messages_received += 1
                try:
                    messages = translate_device_message(packet.topic, bytes(packet.data))
                except DeviceMessageError as exc:
                    self._messages_rejected += 1
                    self._logger.warning(
                        "device_mqtt_message_rejected",
                        extra={
                            "event": "device_mqtt_message_rejected",
                            "topic": packet.topic,
                            "reason": str(exc),
                        },
                    )
                    continue
                for message in messages:
                    await self.message_bus.publish(message)
                self._messages_accepted += 1
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001 - third-party receive errors vary
                if self._stopping:
                    return
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._state = DeviceMqttState.RECONNECTING
                await self._disconnect_client()
                await self._publish_transport_unavailable("mqtt_gateway_disconnected")

    async def _reconnect(self) -> bool:
        while not self._stopping:
            await asyncio.sleep(self.config.gateway_reconnect_seconds)
            if self._stopping:
                return False
            self._reconnect_attempts += 1
            try:
                await self._connect()
            except Exception as exc:  # noqa: BLE001 - third-party connect errors vary
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._state = DeviceMqttState.RECONNECTING
                continue
            return True
        return False

    async def _disconnect_client(self) -> None:
        async with self._client_lock:
            client = self._client
            self._client = None
        if client is None:
            return
        try:
            await client.disconnect()
        except Exception:
            self._logger.debug(
                "device_mqtt_disconnect_failed",
                exc_info=True,
                extra={"event": "device_mqtt_disconnect_failed"},
            )

    async def _publish_command(self, message: Message) -> None:
        try:
            command = encode_device_command(message)
        except DeviceMessageError as exc:
            await self._publish_command_failure(message, str(exc))
            return
        try:
            async with self._client_lock:
                client = self._client
                if not self.running or client is None:
                    raise DeviceMqttError("MQTT device gateway is unavailable")
                await asyncio.wait_for(
                    client.publish(
                        command.topic,
                        command.payload,
                        qos=0,
                        retain=False,
                    ),
                    timeout=self.config.query_timeout_seconds,
                )
        except Exception as exc:  # noqa: BLE001 - aMQTT publish errors vary by transport
            self._last_error = f"{type(exc).__name__}: {exc}"
            await self._publish_command_failure(message, "mqtt_publish_failed")
            return
        self._messages_published += 1
        await self.message_bus.publish(
            Message.create(
                topic="device.command.published",
                kind=MessageKind.RESULT,
                source="device_mqtt",
                target=f"device:{command.device_id}",
                correlation_id=message.message_id,
                payload={
                    "device_id": command.device_id,
                    "transport": "mqtt",
                    "command_topic": message.topic,
                    "qos": 0,
                    "retain": False,
                },
            )
        )

    async def _publish_command_failure(self, message: Message, reason: str) -> None:
        self._publish_failures += 1
        device_id = message.payload.get("device_id")
        normalized = device_id if isinstance(device_id, str) else "unknown"
        target = message.target if message.target.startswith("device:") else "service:device_verifier"
        await self.message_bus.publish(
            Message.create(
                topic="device.command.failed",
                kind=MessageKind.RESULT,
                source="device_mqtt",
                target=target,
                correlation_id=message.message_id,
                payload={
                    "device_id": normalized,
                    "transport": "mqtt",
                    "command_topic": message.topic,
                    "reason": reason,
                },
            )
        )

    async def _publish_transport_unavailable(self, reason: str) -> None:
        if not self.message_bus.running:
            return
        await self.message_bus.publish(
            Message.create(
                topic="device.transport.unavailable",
                kind=MessageKind.EVENT,
                source="device_mqtt",
                target="service:device_manager",
                payload={"transport": "mqtt", "reason": reason},
            )
        )
