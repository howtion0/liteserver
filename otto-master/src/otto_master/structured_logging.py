"""Small JSONL logging setup shared by the runtime and its components."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from .config import LoggingConfig

_STANDARD_RECORD_FIELDS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)


class JsonFormatter(logging.Formatter):
    """Format records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_FIELDS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))


def configure_logging(
    settings: LoggingConfig,
    base_dir: Path,
    *,
    stream: TextIO | None = None,
) -> logging.Logger:
    """Configure the Otto logger without disturbing unrelated root handlers."""

    logger = logging.getLogger("otto_master")
    logger.setLevel(getattr(logging, settings.level.upper(), logging.INFO))
    logger.propagate = False

    for handler in list(logger.handlers):
        if (handler.get_name() or "").startswith("otto-master-"):
            logger.removeHandler(handler)
            handler.close()

    output = stream if stream is not None else __import__("sys").stderr
    stream_handler = logging.StreamHandler(output)
    stream_handler.set_name("otto-master-stream")
    stream_handler.setFormatter(JsonFormatter())
    logger.addHandler(stream_handler)

    path = Path(settings.jsonl_path)
    if not path.is_absolute():
        path = base_dir / path
    path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.set_name("otto-master-file")
    file_handler.setFormatter(JsonFormatter())
    logger.addHandler(file_handler)
    return logger


def close_otto_handlers(logger: logging.Logger | None = None) -> None:
    target = logger or logging.getLogger("otto_master")
    for handler in list(target.handlers):
        if (handler.get_name() or "").startswith("otto-master-"):
            target.removeHandler(handler)
            handler.close()
