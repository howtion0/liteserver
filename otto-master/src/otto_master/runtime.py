"""Application lifecycle and dependency assembly."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from enum import Enum
from importlib.metadata import PackageNotFoundError, version
from time import monotonic
from types import TracebackType
from typing import Any, Self

from .config import AppConfig
from .devices.manager import DeviceManager
from .devices.verifier import DeviceVerifier
from .dispatch.commands import CommandRepository
from .dispatch.dispatcher import CommandDispatcher
from .gateways.cloud import (
    DEFAULT_TTS_SPEAKER,
    VOLC_ASR_RESOURCE_ID,
    VOLC_TTS_RESOURCE_ID,
    DeepSeekClient,
    VolcAsrClient,
    VolcTtsClient,
)
from .gateways.device_audio import DeviceAudioRouter
from .gateways.device_mqtt import DeviceMqttGateway
from .gateways.device_tcp import DeviceTcpGateway
from .gateways.device_udp import DeviceUdpGateway
from .gateways.device_ws import DeviceWebsocketGateway
from .gateways.mdns import MdnsError, MdnsGateway
from .gateways.mqtt_broker import EmbeddedMqttBroker
from .gateways.web import EventHub, WebContext, WebGateway
from .message_bus import MessageBus
from .services.asr import AsrService
from .services.conversation_control import ConversationControlService
from .services.llm import LlmService
from .services.robot_tools import RobotToolBridge
from .services.tts import TtsService
from .services.wake_gate import WakeGateService
from .storage.database import Database, MessageLogSubscriber
from .structured_logging import close_otto_handlers, configure_logging


class RuntimeState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"


class Runtime:
    """Own process-level dependencies and enforce ordered startup/shutdown."""

    def __init__(self, config: AppConfig, *, message_bus: MessageBus | None = None) -> None:
        self.config = config
        self.message_bus = message_bus or MessageBus(config.runtime.message_queue_size)
        self.database = Database(
            config.resolve_path(config.database.path),
            wal=config.database.wal,
        )
        self._message_logger = MessageLogSubscriber(self.database)
        self._storage_observer_id: str | None = None
        self.mqtt_broker = EmbeddedMqttBroker(
            config.mqtt,
            config.resolve_path(config.mqtt.credentials_path),
            configured_master_password=config.secrets.mqtt_master_password,
            public_host=config.discovery.hostname,
        )
        self.events = EventHub()
        self.device_manager = DeviceManager(
            self.database,
            self.message_bus,
            stale_seconds=config.mqtt.heartbeat_stale_seconds,
            offline_seconds=config.mqtt.heartbeat_offline_seconds,
        )
        self.device_mqtt = DeviceMqttGateway(
            config.mqtt,
            self.mqtt_broker,
            self.message_bus,
        )
        self.device_tcp = DeviceTcpGateway(
            config.tcp,
            self.mqtt_broker.credentials,
            self.message_bus,
        )
        self.device_websocket = DeviceWebsocketGateway(
            config.device_websocket,
            config.audio,
            self.mqtt_broker.credentials,
            self.message_bus,
        )
        self.device_udp = DeviceUdpGateway(
            config.device_udp,
            config.audio,
            self.message_bus,
            self.device_mqtt.publish_device_json,
            mqtt_enabled=config.mqtt.enabled,
        )
        self.device_mqtt.set_voice_gateway(self.device_udp)
        self.device_audio = DeviceAudioRouter(
            self.device_udp,
            self.device_websocket,
        )
        self.device_verifier = DeviceVerifier(
            self.message_bus,
            self.device_manager,
            broker_status=self.mqtt_broker.status,
            gateway_status=self.device_mqtt.status,
            transport_status=self._transport_status,
            timeout_seconds=config.mqtt.query_timeout_seconds,
        )
        self.conversation_control = ConversationControlService(
            self.message_bus,
            self.device_manager,
            timeout_seconds=config.mqtt.query_timeout_seconds,
        )
        self.command_repository = CommandRepository(self.database)
        self.dispatcher = CommandDispatcher(
            self.message_bus,
            self.command_repository,
            self.device_manager,
            config.dispatch,
        )
        self.cloud_asr: VolcAsrClient | None = None
        self.cloud_tts: VolcTtsClient | None = None
        self.cloud_llm: DeepSeekClient | None = None
        self.asr_service: AsrService | None = None
        self.tts_service: TtsService | None = None
        self.llm_service: LlmService | None = None
        self.robot_tools: RobotToolBridge | None = None
        self.wake_gate: WakeGateService | None = None
        self._assemble_voice_mvp()
        self.mdns = MdnsGateway(
            config.discovery,
            http_port=config.server.port,
            mqtt_port=config.mqtt.port,
            project_version=self._project_version(),
        )
        self._started_at = datetime.now(UTC)
        self._started_monotonic = monotonic()
        self.web = WebGateway(
            WebContext(
                config=config,
                database=self.database,
                message_bus=self.message_bus,
                mqtt_broker=self.mqtt_broker,
                events=self.events,
                devices=self.device_manager,
                verifier=self.device_verifier,
                dispatcher=self.dispatcher,
                conversation_control=self.conversation_control,
                component_status=self.component_status,
                started_at=self._started_at,
                started_monotonic=self._started_monotonic,
                device_websocket=self.device_websocket,
            )
        )
        self.worker_pool = ThreadPoolExecutor(
            max_workers=config.runtime.worker_threads,
            thread_name_prefix="otto-worker",
        )
        self._state = RuntimeState.CREATED
        self._closed = asyncio.Event()
        self._background_tasks: set[asyncio.Task[object]] = set()
        self._logger = logging.getLogger("otto_master.runtime")

    @property
    def state(self) -> RuntimeState:
        return self._state

    @property
    def background_task_count(self) -> int:
        return len(self._background_tasks)

    async def start(self) -> None:
        if self._state is RuntimeState.RUNNING:
            return
        if self._state is not RuntimeState.CREATED:
            raise RuntimeError(f"runtime cannot start from state {self._state.value}")
        try:
            configure_logging(self.config.logging, self.config.config_path.parent)
            await self.database.start()
            await self.message_bus.start()
            self._storage_observer_id = await self.message_bus.subscribe_observer(
                self._message_logger
            )
            await self.mqtt_broker.credentials.start()
            await self.mqtt_broker.start()
            await self.device_manager.start()
            await self.device_verifier.start()
            await self.conversation_control.start()
            await self.dispatcher.start()
            await self.device_udp.start()
            await self.device_mqtt.start()
            await self.device_tcp.start()
            if self.config.server.enabled:
                await self.device_websocket.start()
            if self.asr_service is not None:
                await self.asr_service.start()
            if self.wake_gate is not None:
                await self.wake_gate.start()
            await self.web.start()
            try:
                await self.mdns.start()
            except MdnsError:
                self._logger.exception(
                    "mdns_start_failed",
                    extra={"event": "mdns_start_failed"},
                )
        except Exception:
            await self._rollback_startup()
            raise
        self._state = RuntimeState.RUNNING
        self._logger.info(
            "runtime_started",
            extra={"event": "runtime_started", "project": self.config.project.name},
        )

    def create_background_task(
        self,
        coroutine: Coroutine[object, object, object],
        *,
        name: str | None = None,
    ) -> asyncio.Task[object]:
        if self._state is not RuntimeState.RUNNING:
            coroutine.close()
            raise RuntimeError("runtime is not running")
        task = asyncio.create_task(coroutine, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    def request_shutdown(self) -> None:
        self._closed.set()

    async def wait_closed(self) -> None:
        await self._closed.wait()

    async def run(self) -> None:
        await self.start()
        try:
            await self.wait_closed()
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        if self._state is RuntimeState.STOPPED:
            return
        if self._state is RuntimeState.CREATED:
            self._state = RuntimeState.STOPPED
            self.worker_pool.shutdown(wait=True, cancel_futures=True)
            self._closed.set()
            return

        self._state = RuntimeState.STOPPING
        self._closed.set()
        await self._cancel_background_tasks()
        try:
            await self._stop_with_timeout(self.mdns.shutdown(), "mDNS")
            await self._stop_with_timeout(self.web.shutdown(), "web gateway")
            if self.wake_gate is not None:
                await self._stop_with_timeout(self.wake_gate.shutdown(), "wake gate")
            if self.asr_service is not None:
                await self._stop_with_timeout(self.asr_service.shutdown(), "ASR service")
            await self._stop_with_timeout(
                self.conversation_control.shutdown(), "conversation control"
            )
            await self._stop_with_timeout(self.device_verifier.shutdown(), "device verifier")
            await self._stop_with_timeout(self.dispatcher.shutdown(), "dispatcher")
            await self._stop_with_timeout(
                self.device_websocket.shutdown(), "device WebSocket gateway"
            )
            await self._stop_with_timeout(self.device_tcp.shutdown(), "device TCP gateway")
            await self._stop_with_timeout(self.device_mqtt.shutdown(), "MQTT device gateway")
            await self._stop_with_timeout(self.device_udp.shutdown(), "device UDP gateway")
            await self._stop_with_timeout(self.message_bus.drain(), "message bus drain")
            await self._stop_with_timeout(self.device_manager.shutdown(), "device manager")
            await self._stop_with_timeout(self.message_bus.stop(), "message bus")
        finally:
            if self._storage_observer_id is not None:
                await self.message_bus.unsubscribe_observer(self._storage_observer_id)
                self._storage_observer_id = None
            try:
                await self._stop_with_timeout(self.mqtt_broker.shutdown(), "MQTT broker")
            finally:
                try:
                    await self.database.close()
                finally:
                    self.worker_pool.shutdown(wait=True, cancel_futures=True)
                    self._state = RuntimeState.STOPPED
                    self._logger.info("runtime_stopped", extra={"event": "runtime_stopped"})
                    close_otto_handlers()

    def component_status(self) -> dict[str, dict[str, Any]]:
        firmware_path = self.config.resolve_path(self.config.ota.firmware_path)
        return {
            "runtime": {
                "enabled": True,
                "healthy": self._state is RuntimeState.RUNNING,
                "state": self._state.value,
            },
            "message_bus": {
                "enabled": True,
                "healthy": self.message_bus.running,
                "state": "running" if self.message_bus.running else "stopped",
                "queue_depth": self.message_bus.queue_depth,
            },
            "sqlite": {
                "enabled": True,
                "healthy": self.database.is_open,
                "state": "running" if self.database.is_open else "stopped",
            },
            "mqtt": self.mqtt_broker.status(),
            "mqtt_gateway": self.device_mqtt.status(),
            "device_udp": self.device_udp.status(),
            "tcp_gateway": self.device_tcp.status(),
            "device_websocket": self.device_websocket.status(),
            "device_manager": {
                "enabled": True,
                "healthy": self.device_manager.running,
                "state": "running" if self.device_manager.running else "stopped",
                "devices": self.device_manager.device_count,
            },
            "device_verifier": self.device_verifier.status(),
            "conversation_control": self.conversation_control.status(),
            "dispatcher": self.dispatcher.status(),
            "voice_mvp": self._voice_status(),
            "web": self.web.status(),
            "mdns": self.mdns.status(),
            "ota": {
                "enabled": self.config.ota.enabled,
                "healthy": not self.config.ota.enabled or firmware_path.is_file(),
                "state": (
                    "available"
                    if self.config.ota.enabled and firmware_path.is_file()
                    else ("missing" if self.config.ota.enabled else "disabled")
                ),
            },
            "logging": {
                "enabled": True,
                "healthy": True,
                "state": "running",
            },
        }

    async def _rollback_startup(self) -> None:
        try:
            await self._stop_with_timeout(self.mdns.shutdown(), "mDNS")
            await self._stop_with_timeout(self.web.shutdown(), "web gateway")
            if self.wake_gate is not None:
                await self._stop_with_timeout(self.wake_gate.shutdown(), "wake gate")
            if self.asr_service is not None:
                await self._stop_with_timeout(self.asr_service.shutdown(), "ASR service")
            await self._stop_with_timeout(
                self.conversation_control.shutdown(), "conversation control"
            )
            await self._stop_with_timeout(self.device_verifier.shutdown(), "device verifier")
            await self._stop_with_timeout(self.dispatcher.shutdown(), "dispatcher")
            await self._stop_with_timeout(
                self.device_websocket.shutdown(), "device WebSocket gateway"
            )
            await self._stop_with_timeout(self.device_tcp.shutdown(), "device TCP gateway")
            await self._stop_with_timeout(self.device_mqtt.shutdown(), "MQTT device gateway")
            await self._stop_with_timeout(self.device_udp.shutdown(), "device UDP gateway")
            await self._stop_with_timeout(self.message_bus.drain(), "message bus drain")
            await self._stop_with_timeout(self.device_manager.shutdown(), "device manager")
            await self._stop_with_timeout(self.message_bus.stop(), "message bus")
            if self._storage_observer_id is not None:
                await self.message_bus.unsubscribe_observer(self._storage_observer_id)
                self._storage_observer_id = None
            await self._stop_with_timeout(self.mqtt_broker.shutdown(), "MQTT broker")
            await self.database.close()
        finally:
            self.worker_pool.shutdown(wait=True, cancel_futures=True)
            self._state = RuntimeState.STOPPED
            self._closed.set()
            close_otto_handlers()

    async def _stop_with_timeout(self, coroutine: Coroutine[Any, Any, None], name: str) -> None:
        try:
            await asyncio.wait_for(
                coroutine,
                timeout=self.config.runtime.shutdown_timeout_seconds,
            )
        except TimeoutError:
            self._logger.error(
                "component_shutdown_timeout",
                extra={"event": "component_shutdown_timeout", "component": name},
            )
        except Exception as exc:
            self._logger.exception(
                "component_shutdown_failed",
                extra={
                    "event": "component_shutdown_failed",
                    "component": name,
                    "error_type": type(exc).__name__,
                },
            )

    @staticmethod
    def _project_version() -> str:
        try:
            return version("otto-master")
        except PackageNotFoundError:
            return "0.0.0+unknown"

    async def _cancel_background_tasks(self) -> None:
        tasks = tuple(self._background_tasks)
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()

    def _assemble_voice_mvp(self) -> None:
        """Assemble the optional voice stack only when all local secrets exist."""

        config = self.config
        secrets = config.secrets
        providers_ready = (
            (
                (config.server.enabled and config.device_websocket.enabled)
                or (config.mqtt.enabled and config.device_udp.enabled)
            )
            and config.cloud.asr.provider == "volcengine"
            and config.cloud.tts.provider == "volcengine"
            and config.cloud.llm.provider == "deepseek"
            and bool(config.cloud.asr.base_url)
            and bool(config.cloud.tts.base_url)
            and bool(config.cloud.llm.base_url)
            and bool(config.cloud.llm.model)
        )
        keys_ready = all(
            value is not None
            for value in (
                secrets.asr_api_key,
                secrets.tts_api_key,
                secrets.llm_api_key,
            )
        )
        if not providers_ready or not keys_ready:
            return
        if config.audio.input_sample_rate != 16_000:
            raise ValueError("voice MVP requires audio.input_sample_rate=16000")
        if config.audio.output_sample_rate != 24_000:
            raise ValueError("voice MVP requires audio.output_sample_rate=24000")
        if config.audio.channels != 1 or config.audio.frame_duration_ms != 60:
            raise ValueError("voice MVP requires mono 60 ms audio")
        chunk_ms = config.cloud.asr.chunk_ms or 180
        if chunk_ms % config.audio.frame_duration_ms:
            raise ValueError("cloud.asr.chunk_ms must be a multiple of audio.frame_duration_ms")
        asr_key = secrets.asr_api_key
        tts_key = secrets.tts_api_key
        llm_key = secrets.llm_api_key
        llm_model = config.cloud.llm.model
        if asr_key is None or tts_key is None or llm_key is None or llm_model is None:
            return
        self.cloud_asr = VolcAsrClient(
            base_url=config.cloud.asr.base_url,
            api_key=asr_key,
            timeout_seconds=config.cloud.request_timeout_seconds,
            resource_id=config.cloud.asr.resource_id or VOLC_ASR_RESOURCE_ID,
        )
        self.cloud_tts = VolcTtsClient(
            base_url=config.cloud.tts.base_url,
            api_key=tts_key,
            timeout_seconds=config.cloud.request_timeout_seconds,
            resource_id=config.cloud.tts.resource_id or VOLC_TTS_RESOURCE_ID,
            speaker=config.cloud.tts.speaker or DEFAULT_TTS_SPEAKER,
        )
        self.cloud_llm = DeepSeekClient(
            base_url=config.cloud.llm.base_url,
            api_key=llm_key,
            model=llm_model,
            timeout_seconds=config.cloud.request_timeout_seconds,
            thinking=config.cloud.llm.thinking,
            max_tokens=config.cloud.llm.max_tokens,
        )
        self.asr_service = AsrService(
            self.message_bus,
            self.device_audio,
            self.cloud_asr,
            chunk_frames=chunk_ms // config.audio.frame_duration_ms,
            queue_frames=max(
                32,
                int(
                    config.wake.conversation_timeout_seconds
                    * 1_000
                    / config.audio.frame_duration_ms
                ),
            ),
            partial_stability_seconds=config.wake.speech_end_grace_seconds,
            max_utterance_seconds=config.wake.max_utterance_seconds,
        )
        self.tts_service = TtsService(
            self.message_bus,
            self.device_audio,
            self.cloud_tts,
            sample_rate=config.audio.output_sample_rate,
            channels=config.audio.channels,
            frame_duration_ms=config.audio.frame_duration_ms,
            pcm_gain=config.audio.tts_pcm_gain,
        )
        self.llm_service = LlmService(
            self.cloud_llm,
            system_prompt=config.cloud.llm.system_prompt
            or "你叫奶龙。请用一到三句简短中文口语回答。",
            prompt_max_chars=config.cloud.llm.input_max_chars or 512,
            response_max_chars=config.cloud.llm.output_max_chars or 96,
        )
        self.robot_tools = RobotToolBridge(
            self.device_manager,
            self.dispatcher,
            completion_timeout_seconds=(
                config.dispatch.ack_timeout_seconds
                + config.dispatch.completion_timeout_seconds
                + 2.0
            ),
        )
        self.wake_gate = WakeGateService(
            self.message_bus,
            self.asr_service,
            self.llm_service,
            self.tts_service,
            self.dispatcher,
            self.device_audio,
            laughter_action=config.wake.laughter_action,
            laughter_parameters={"steps": 1, "speed": 1000, "amount": 0},
            query_interval_seconds=config.wake.state_query_interval_seconds,
            query_timeout_seconds=config.wake.state_query_timeout_seconds,
            laughter_timeout_seconds=config.wake.laughter_timeout_seconds,
            conversation_timeout_seconds=config.wake.conversation_timeout_seconds,
            idle_timeout_seconds=config.wake.idle_timeout_seconds,
            transcription_max_chars=config.cloud.llm.input_max_chars or 512,
            robot_tools=self.robot_tools,
        )

    def _voice_status(self) -> dict[str, Any]:
        if self.wake_gate is None or self.asr_service is None or self.tts_service is None:
            return {
                "enabled": False,
                "healthy": True,
                "state": "disabled",
                "reason": "provider_configuration_or_local_credentials_unavailable",
            }
        wake = self.wake_gate.status()
        return {
            "enabled": True,
            "healthy": (
                wake.get("healthy") is True and self.asr_service.status()["healthy"] is True
            ),
            "state": wake.get("state", "unknown"),
            "asr": self.asr_service.status(),
            "llm": self.llm_service.status() if self.llm_service is not None else {},
            "tts": self.tts_service.status(),
            "gate": {
                key: value
                for key, value in wake.items()
                if key not in {"devices", "enabled", "healthy", "state"}
            },
            "sessions": wake.get("devices", {}),
            "providers": {
                "asr": self.config.cloud.asr.provider,
                "tts": self.config.cloud.tts.provider,
                "llm": self.config.cloud.llm.provider,
            },
        }

    def _transport_status(self, transport: str) -> dict[str, Any]:
        if transport == "mqtt":
            broker = self.mqtt_broker.status()
            gateway = self.device_mqtt.status()
            return {
                "healthy": broker.get("healthy") is True and gateway.get("healthy") is True,
                "state": f"broker={broker.get('state')},gateway={gateway.get('state')}",
            }
        if transport == "tcp":
            return self.device_tcp.status()
        if transport == "websocket":
            return self.device_websocket.status()
        return {"healthy": False, "state": "unsupported"}

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.shutdown()
