"""Authenticated MQTT device uplink and external-to-domain message translation."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol
from urllib.parse import quote

from amqtt.client import MQTTClient  # type: ignore[import-untyped]

from ..config import MqttConfig
from ..message_bus import MessageBus
from ..messages import Message, MessageKind
from .device_protocol import (
    MAX_DEVICE_MESSAGE_BYTES as _MAX_DEVICE_MESSAGE_BYTES,
)
from .device_protocol import (
    OUTBOUND_COMMAND_TOPICS,
    DeviceProtocolError,
    decode_device_json,
    encode_otto_command,
    translate_otto_value,
)
from .mqtt_broker import EmbeddedMqttBroker, normalize_device_id

_UP_TOPIC = re.compile(r"^otto/v1/devices/(?P<device_id>[0-9a-f]{12})/up$")
MAX_DEVICE_MESSAGE_BYTES = _MAX_DEVICE_MESSAGE_BYTES


class DeviceMqttError(RuntimeError):
    """Raised when the gateway lifecycle cannot be established."""


DeviceMessageError = DeviceProtocolError
DeviceControlSender = Callable[[str, dict[str, Any]], Awaitable[None]]


class MqttVoiceGateway(Protocol):
    async def handle_mqtt_value(self, device_id: str, value: dict[str, Any]) -> bool: ...

    async def close_all(self, reason: str) -> None: ...


class DeviceMqttState(str, Enum):
    DISABLED = "disabled"
    CREATED = "created"
    CONNECTING = "connecting"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


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


def translate_device_message(topic: str, payload: bytes) -> tuple[Message, ...]:
    """Validate one untrusted uplink packet and return domain messages."""

    device_id, value = decode_authenticated_device_message(topic, payload)
    return translate_otto_value(
        device_id,
        value,
        transport="mqtt",
        hello_protocol="otto-mqtt/1",
    )


def decode_authenticated_device_message(
    topic: str,
    payload: bytes,
) -> tuple[str, dict[str, Any]]:
    """Decode one MQTT uplink while binding its payload to the authenticated topic."""

    value = decode_device_json(payload)
    return _identity(topic, value), value


@dataclass(frozen=True, slots=True)
class EncodedDeviceCommand:
    device_id: str
    topic: str
    payload: bytes
    external_id: str


def encode_device_command(message: Message) -> EncodedDeviceCommand:
    """Encode an allow-listed internal command for one exact MQTT down topic."""

    encoded = encode_otto_command(message, transport="mqtt")
    return EncodedDeviceCommand(
        device_id=encoded.device_id,
        topic=f"otto/v1/devices/{encoded.device_id}/down",
        payload=encoded.payload,
        external_id=encoded.external_id,
    )


class DeviceMqttGateway:
    """Subscribe as the broker's master identity and feed validated domain messages."""

    def __init__(
        self,
        config: MqttConfig,
        broker: EmbeddedMqttBroker,
        message_bus: MessageBus,
        *,
        voice_gateway: MqttVoiceGateway | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.broker = broker
        self.message_bus = message_bus
        self._voice_gateway = voice_gateway
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

    def set_voice_gateway(self, gateway: MqttVoiceGateway) -> None:
        """Attach the paired encrypted-UDP data plane before startup."""

        if self.running:
            raise DeviceMqttError("cannot replace voice gateway while MQTT is running")
        self._voice_gateway = gateway

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
        for topic in OUTBOUND_COMMAND_TOPICS:
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
        if self._voice_gateway is not None:
            await self._voice_gateway.close_all("mqtt_gateway_stopped")
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
                    device_id, value = decode_authenticated_device_message(
                        packet.topic,
                        bytes(packet.data),
                    )
                    voice_handled = (
                        self._voice_gateway is not None
                        and await self._voice_gateway.handle_mqtt_value(device_id, value)
                    )
                    messages = (
                        ()
                        if voice_handled
                        else translate_otto_value(
                            device_id,
                            value,
                            transport="mqtt",
                            hello_protocol="otto-mqtt/1",
                        )
                    )
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
                if self._voice_gateway is not None:
                    await self._voice_gateway.close_all("mqtt_gateway_disconnected")
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
        if message.payload.get("transport") != "mqtt":
            return
        try:
            command = encode_device_command(message)
        except DeviceMessageError as exc:
            await self._publish_command_failure(message, str(exc))
            return
        try:
            await self._publish_bytes(command.topic, command.payload)
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
                correlation_id=command.external_id,
                payload={
                    "device_id": command.device_id,
                    "transport": "mqtt",
                    "command_topic": message.topic,
                    "outbound_message_id": message.message_id,
                    "qos": 0,
                    "retain": False,
                },
            )
        )

    async def publish_device_json(self, device_id: str, payload: dict[str, Any]) -> None:
        """Publish one bounded internal voice control to an exact device down topic."""

        try:
            normalized = normalize_device_id(device_id)
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise DeviceMqttError("invalid MQTT voice control") from exc
        if not encoded or len(encoded) > MAX_DEVICE_MESSAGE_BYTES:
            raise DeviceMqttError("MQTT voice control exceeds the size limit")
        await self._publish_bytes(
            f"otto/v1/devices/{normalized}/down",
            encoded,
        )
        self._messages_published += 1

    async def _publish_bytes(self, topic: str, payload: bytes) -> None:
        async with self._client_lock:
            client = self._client
            if not self.running or client is None:
                raise DeviceMqttError("MQTT device gateway is unavailable")
            await asyncio.wait_for(
                client.publish(topic, payload, qos=0, retain=False),
                timeout=self.config.query_timeout_seconds,
            )

    async def _publish_command_failure(self, message: Message, reason: str) -> None:
        self._publish_failures += 1
        device_id = message.payload.get("device_id")
        normalized = device_id if isinstance(device_id, str) else "unknown"
        target = message.target if message.target.startswith("device:") else "service:device_verifier"
        correlation_id = message.correlation_id or message.message_id
        await self.message_bus.publish(
            Message.create(
                topic="device.command.failed",
                kind=MessageKind.RESULT,
                source="device_mqtt",
                target=target,
                correlation_id=correlation_id,
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
