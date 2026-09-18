"""Application configuration loading and validation for Otto Master."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from dotenv import dotenv_values


class ConfigError(ValueError):
    """Raised when the application configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    name: str


@dataclass(frozen=True, slots=True)
class ServerConfig:
    host: str
    port: int
    websocket_path: str


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    message_queue_size: int
    worker_threads: int
    shutdown_timeout_seconds: float


@dataclass(frozen=True, slots=True)
class DiscoveryConfig:
    enabled: bool
    hostname: str
    service_type: str


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    path: str
    wal: bool


@dataclass(frozen=True, slots=True)
class AudioConfig:
    input_sample_rate: int
    output_sample_rate: int
    channels: int
    frame_duration_ms: int
    format: str


@dataclass(frozen=True, slots=True)
class WakeConfig:
    command_timeout_seconds: float
    conversation_timeout_seconds: float
    acknowledgement_template: str
    fail_closed: bool


@dataclass(frozen=True, slots=True)
class CloudProviderConfig:
    provider: str
    base_url: str
    api_key_env: str
    model: str | None = None
    stream: bool = False
    thinking: str | None = None


@dataclass(frozen=True, slots=True)
class CloudConfig:
    request_timeout_seconds: float
    asr: CloudProviderConfig
    llm: CloudProviderConfig
    tts: CloudProviderConfig


@dataclass(frozen=True, slots=True)
class OtaConfig:
    enabled: bool
    firmware_path: str


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    level: str
    jsonl_path: str


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Validated configuration plus the file it was loaded from."""

    project: ProjectConfig
    server: ServerConfig
    runtime: RuntimeConfig
    discovery: DiscoveryConfig
    database: DatabaseConfig
    audio: AudioConfig
    wake: WakeConfig
    cloud: CloudConfig
    ota: OtaConfig
    logging: LoggingConfig
    config_path: Path
    env_file: Path | None

    def resolve_path(self, value: str | Path) -> Path:
        """Resolve a relative runtime path against the config directory."""

        path = Path(value)
        if path.is_absolute():
            return path
        return self.config_path.parent / path


_ENV_PATTERN = re.compile(r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}|(?P<plain>[A-Za-z_][A-Za-z0-9_]*))")


def default_config_path() -> Path:
    """Return the repository-local default config path."""

    return Path(__file__).resolve().parents[2] / "config.yaml"


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{path} must be a mapping")
    return value


def _section(root: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    if name not in root:
        raise ConfigError(f"missing config section: {name}")
    return _mapping(root[name], name)


def _string(section: Mapping[str, Any], name: str, path: str, *, allow_empty: bool = False) -> str:
    value = section.get(name)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ConfigError(f"{path}.{name} must be a non-empty string")
    return value


def _int(section: Mapping[str, Any], name: str, path: str, *, minimum: int | None = None) -> int:
    value = section.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{path}.{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{path}.{name} must be at least {minimum}")
    return value


def _float(section: Mapping[str, Any], name: str, path: str, *, minimum: float | None = None) -> float:
    value = section.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{path}.{name} must be a number")
    result = float(value)
    if minimum is not None and result < minimum:
        raise ConfigError(f"{path}.{name} must be at least {minimum}")
    return result


def _bool(section: Mapping[str, Any], name: str, path: str) -> bool:
    value = section.get(name)
    if not isinstance(value, bool):
        raise ConfigError(f"{path}.{name} must be a boolean")
    return value


def _substitute(value: Any, environment: Mapping[str, str], path: str = "config") -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            name = match.group("braced") or match.group("plain")
            if name not in environment:
                raise ConfigError(f"{path} references unset environment variable {name}")
            return environment[name]

        return _ENV_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [_substitute(item, environment, f"{path}[]") for item in value]
    if isinstance(value, Mapping):
        return {key: _substitute(item, environment, f"{path}.{key}") for key, item in value.items()}
    return value


def _environment(env_file: Path | None, overrides: Mapping[str, str] | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if env_file is not None and env_file.is_file():
        for key, value in dotenv_values(env_file).items():
            if value is not None:
                values[key] = value
    values.update({key: value for key, value in os.environ.items()})
    if overrides is not None:
        values.update(overrides)
    return values


def _provider(section: Mapping[str, Any], path: str) -> CloudProviderConfig:
    model_value = section.get("model")
    if model_value is not None and not isinstance(model_value, str):
        raise ConfigError(f"{path}.model must be a string when provided")
    thinking_value = section.get("thinking")
    if thinking_value is not None and not isinstance(thinking_value, str):
        raise ConfigError(f"{path}.thinking must be a string when provided")
    stream = section.get("stream", False)
    if not isinstance(stream, bool):
        raise ConfigError(f"{path}.stream must be a boolean")
    return CloudProviderConfig(
        provider=_string(section, "provider", path, allow_empty=True),
        base_url=_string(section, "base_url", path, allow_empty=True),
        api_key_env=_string(section, "api_key_env", path),
        model=model_value,
        stream=stream,
        thinking=thinking_value,
    )


def load_config(
    path: str | Path | None = None,
    *,
    env_file: str | Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> AppConfig:
    """Load and validate YAML config with optional dotenv and environment expansion."""

    config_path = Path(path) if path is not None else default_config_path()
    config_path = config_path.expanduser().resolve()
    if not config_path.is_file():
        raise ConfigError(f"configuration file not found: {config_path}")

    selected_env_file = Path(env_file) if env_file is not None else config_path.parent / ".env"
    selected_env_file = selected_env_file.expanduser().resolve()
    raw_loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    root = _mapping(_substitute(raw_loaded, _environment(selected_env_file, environment)), "config")

    project = _section(root, "project")
    server = _section(root, "server")
    runtime = _section(root, "runtime")
    discovery = _section(root, "discovery")
    database = _section(root, "database")
    audio = _section(root, "audio")
    wake = _section(root, "wake")
    cloud = _section(root, "cloud")
    ota = _section(root, "ota")
    logging_config = _section(root, "logging")

    cloud_asr = _section(cloud, "asr")
    cloud_llm = _section(cloud, "llm")
    cloud_tts = _section(cloud, "tts")

    return AppConfig(
        project=ProjectConfig(name=_string(project, "name", "project")),
        server=ServerConfig(
            host=_string(server, "host", "server"),
            port=_int(server, "port", "server", minimum=1),
            websocket_path=_string(server, "websocket_path", "server"),
        ),
        runtime=RuntimeConfig(
            message_queue_size=_int(runtime, "message_queue_size", "runtime", minimum=1),
            worker_threads=_int(runtime, "worker_threads", "runtime", minimum=1),
            shutdown_timeout_seconds=_float(
                runtime, "shutdown_timeout_seconds", "runtime", minimum=0.1
            ),
        ),
        discovery=DiscoveryConfig(
            enabled=_bool(discovery, "enabled", "discovery"),
            hostname=_string(discovery, "hostname", "discovery"),
            service_type=_string(discovery, "service_type", "discovery"),
        ),
        database=DatabaseConfig(
            path=_string(database, "path", "database"),
            wal=_bool(database, "wal", "database"),
        ),
        audio=AudioConfig(
            input_sample_rate=_int(audio, "input_sample_rate", "audio", minimum=1),
            output_sample_rate=_int(audio, "output_sample_rate", "audio", minimum=1),
            channels=_int(audio, "channels", "audio", minimum=1),
            frame_duration_ms=_int(audio, "frame_duration_ms", "audio", minimum=1),
            format=_string(audio, "format", "audio"),
        ),
        wake=WakeConfig(
            command_timeout_seconds=_float(wake, "command_timeout_seconds", "wake", minimum=0.1),
            conversation_timeout_seconds=_float(
                wake, "conversation_timeout_seconds", "wake", minimum=0.1
            ),
            acknowledgement_template=_string(wake, "acknowledgement_template", "wake"),
            fail_closed=_bool(wake, "fail_closed", "wake"),
        ),
        cloud=CloudConfig(
            request_timeout_seconds=_float(
                cloud, "request_timeout_seconds", "cloud", minimum=0.1
            ),
            asr=_provider(cloud_asr, "cloud.asr"),
            llm=_provider(cloud_llm, "cloud.llm"),
            tts=_provider(cloud_tts, "cloud.tts"),
        ),
        ota=OtaConfig(
            enabled=_bool(ota, "enabled", "ota"),
            firmware_path=_string(ota, "firmware_path", "ota"),
        ),
        logging=LoggingConfig(
            level=_string(logging_config, "level", "logging").upper(),
            jsonl_path=_string(logging_config, "jsonl_path", "logging"),
        ),
        config_path=config_path,
        env_file=selected_env_file if selected_env_file.is_file() else None,
    )
