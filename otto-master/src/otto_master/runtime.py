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
from .gateways.device_mqtt import DeviceMqttGateway
from .gateways.mdns import MdnsError, MdnsGateway
from .gateways.mqtt_broker import EmbeddedMqttBroker
from .gateways.web import EventHub, WebContext, WebGateway
from .message_bus import MessageBus
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
        self.device_verifier = DeviceVerifier(
            self.message_bus,
            self.device_manager,
            broker_status=self.mqtt_broker.status,
            gateway_status=self.device_mqtt.status,
            timeout_seconds=config.mqtt.query_timeout_seconds,
        )
        self.command_repository = CommandRepository(self.database)
        self.dispatcher = CommandDispatcher(
            self.message_bus,
            self.command_repository,
            self.device_manager,
            config.dispatch,
        )
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
                component_status=self.component_status,
                started_at=self._started_at,
                started_monotonic=self._started_monotonic,
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
            await self.mqtt_broker.start()
            await self.device_manager.start()
            await self.device_verifier.start()
            await self.dispatcher.start()
            await self.device_mqtt.start()
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
            await self._stop_with_timeout(self.device_verifier.shutdown(), "device verifier")
            await self._stop_with_timeout(self.dispatcher.shutdown(), "dispatcher")
            await self._stop_with_timeout(self.device_mqtt.shutdown(), "MQTT device gateway")
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
            "device_manager": {
                "enabled": True,
                "healthy": self.device_manager.running,
                "state": "running" if self.device_manager.running else "stopped",
                "devices": self.device_manager.device_count,
            },
            "device_verifier": self.device_verifier.status(),
            "dispatcher": self.dispatcher.status(),
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
            await self._stop_with_timeout(self.device_verifier.shutdown(), "device verifier")
            await self._stop_with_timeout(self.dispatcher.shutdown(), "dispatcher")
            await self._stop_with_timeout(self.device_mqtt.shutdown(), "MQTT device gateway")
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
