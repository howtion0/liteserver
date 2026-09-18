"""Command-line entry point for ``python -m otto_master``."""

from __future__ import annotations

import asyncio
import signal

from .config import ConfigError, load_config
from .runtime import Runtime


async def _run() -> None:
    runtime = Runtime(load_config())
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, runtime.request_shutdown)
        except (NotImplementedError, RuntimeError):
            # Windows may not expose add_signal_handler on its event loop.
            pass
    await runtime.run()


def main() -> int:
    try:
        asyncio.run(_run())
    except ConfigError as exc:
        print(f"configuration error: {exc}")
        return 2
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
