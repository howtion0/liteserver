from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from otto_master.config import LoggingConfig, load_config
from otto_master.runtime import Runtime, RuntimeState


@pytest.mark.asyncio
async def test_runtime_starts_and_shuts_down_without_leaking_tasks(tmp_path: Path) -> None:
    loaded = load_config()
    config = replace(
        loaded,
        config_path=tmp_path / "config.yaml",
        logging=LoggingConfig(level="INFO", jsonl_path="logs/runtime.jsonl"),
    )
    runtime = Runtime(config)

    await runtime.start()
    assert runtime.state is RuntimeState.RUNNING
    assert runtime.message_bus.running is True

    async def background() -> None:
        await asyncio.Event().wait()

    runtime.create_background_task(background(), name="test-background")
    assert runtime.background_task_count == 1
    await runtime.shutdown()
    await runtime.shutdown()

    assert runtime.state is RuntimeState.STOPPED
    assert runtime.message_bus.running is False
    assert runtime.background_task_count == 0
    assert not list(asyncio.all_tasks()) or all(
        task is asyncio.current_task() or task.done() for task in asyncio.all_tasks()
    )
