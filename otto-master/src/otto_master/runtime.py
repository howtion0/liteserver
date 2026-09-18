"""Application lifecycle and dependency assembly for Phase 1 and Phase 2."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from types import TracebackType
from typing import Self

from .config import AppConfig
from .message_bus import MessageBus
from .storage.database import Database, MessageLogSubscriber
from .structured_logging import close_otto_handlers, configure_logging


class RuntimeState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"


class Runtime:
    """Own the process-level lifecycle and Phase 1 dependencies."""

    def __init__(self, config: AppConfig, *, message_bus: MessageBus | None = None) -> None:
        self.config = config
        self.message_bus = message_bus or MessageBus(config.runtime.message_queue_size)
        self.database = Database(
            config.resolve_path(config.database.path),
            wal=config.database.wal,
        )
        self._message_logger = MessageLogSubscriber(self.database)
        self._storage_observer_id: str | None = None
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
        configure_logging(self.config.logging, self.config.config_path.parent)
        try:
            await self.database.start()
            await self.message_bus.start()
            self._storage_observer_id = await self.message_bus.subscribe_observer(
                self._message_logger
            )
        except Exception:
            await self.message_bus.stop()
            await self.database.close()
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
            await asyncio.wait_for(
                self.message_bus.stop(), timeout=self.config.runtime.shutdown_timeout_seconds
            )
        finally:
            if self._storage_observer_id is not None:
                await self.message_bus.unsubscribe_observer(self._storage_observer_id)
                self._storage_observer_id = None
            try:
                await self.database.close()
            finally:
                self.worker_pool.shutdown(wait=True, cancel_futures=True)
                self._state = RuntimeState.STOPPED
                self._logger.info("runtime_stopped", extra={"event": "runtime_stopped"})
                close_otto_handlers()

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
