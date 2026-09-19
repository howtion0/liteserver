"""Application configuration loading and validation for Otto Master."""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
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
    enabled: bool
    host: str
    port: int
    websocket_path: str
    console_auth_required: bool
    console_token_env: str
    allowed_origins: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MqttConfig:
    enabled: bool
    host: str
    port: int
    max_connections: int
    credentials_path: str
    master_username: str
    master_password_env: str
    heartbeat_stale_seconds: float
    heartbeat_offline_seconds: float
    gateway_reconnect_seconds: float
    query_timeout_seconds: float


@dataclass(frozen=True, slots=True)
class DispatchConfig:
    queue_size_per_device: int
    ack_timeout_seconds: float
    completion_timeout_seconds: float
    state_query_interval_seconds: float


@dataclass(frozen=True, slots=True)
class TcpConfig:
    enabled: bool
    host: str
    port: int
    max_connections: int
    hello_timeout_seconds: float
    write_timeout_seconds: float
    max_frame_bytes: int


@dataclass(frozen=True, slots=True)
class DeviceWebsocketConfig:
    enabled: bool
    hello_timeout_seconds: float
    heartbeat_seconds: float
    max_connections: int
    max_frame_bytes: int
    audio_buffer_frames_per_device: int


@dataclass(frozen=True, slots=True)
class DeviceUdpConfig:
    enabled: bool
    host: str
    port: int
    advertise_host: str
    max_datagram_bytes: int
    audio_buffer_frames_per_device: int
    session_timeout_seconds: float


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
    refresh_interval_seconds: float


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
    tts_pcm_gain: float = 1.0


@dataclass(frozen=True, slots=True)
class WakeConfig:
    command_timeout_seconds: float
    conversation_timeout_seconds: float
    idle_timeout_seconds: float
    speech_end_grace_seconds: float
    max_utterance_seconds: float
    laughter_action: str
    laughter_timeout_seconds: float
    state_query_interval_seconds: float
    state_query_timeout_seconds: float
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
    resource_id: str | None = None
    speaker: str | None = None
    chunk_ms: int | None = None
    max_tokens: int | None = None
    input_max_chars: int | None = None
    output_max_chars: int | None = None
    system_prompt: str | None = None


@dataclass(frozen=True, slots=True)
class CloudConfig:
    request_timeout_seconds: float
    asr: CloudProviderConfig
    llm: CloudProviderConfig
    tts: CloudProviderConfig


@dataclass(frozen=True, slots=True)
class ZhihuConfig:
    enabled: bool
    base_url: str
    access_secret_env: str
    request_timeout_seconds: float
    answer_timeout_seconds: float
    max_response_bytes: int
    max_concurrency: int
    event_history_size: int


@dataclass(frozen=True, slots=True)
class OtaConfig:
    enabled: bool
    firmware_path: str
    firmware_version: str
    target_hardware: str
    provisioning_token_env: str


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    level: str
    jsonl_path: str


@dataclass(frozen=True, slots=True)
class RuntimeSecrets:
    """Selected runtime secrets loaded without exposing the full environment."""

    console_token: str | None = field(default=None, repr=False)
    mqtt_master_password: str | None = field(default=None, repr=False)
    provisioning_token: str | None = field(default=None, repr=False)
    asr_api_key: str | None = field(default=None, repr=False)
    llm_api_key: str | None = field(default=None, repr=False)
    tts_api_key: str | None = field(default=None, repr=False)
    zhihu_access_secret: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Validated configuration plus the file it was loaded from."""

    project: ProjectConfig
    server: ServerConfig
    mqtt: MqttConfig
    dispatch: DispatchConfig
    tcp: TcpConfig
    device_websocket: DeviceWebsocketConfig
    device_udp: DeviceUdpConfig
    runtime: RuntimeConfig
    discovery: DiscoveryConfig
    database: DatabaseConfig
    audio: AudioConfig
    wake: WakeConfig
    cloud: CloudConfig
    zhihu: ZhihuConfig
    ota: OtaConfig
    logging: LoggingConfig
    config_path: Path
    env_file: Path | None
    secrets: RuntimeSecrets = field(repr=False)

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


def _int(
    section: Mapping[str, Any],
    name: str,
    path: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    value = section.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{path}.{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{path}.{name} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{path}.{name} must be at most {maximum}")
    return value


def _optional_bounded_int(
    section: Mapping[str, Any],
    name: str,
    path: str,
    *,
    minimum: int,
    maximum: int,
) -> int | None:
    if name not in section:
        return None
    return _int(
        section,
        name,
        path,
        minimum=minimum,
        maximum=maximum,
    )


def _port(section: Mapping[str, Any], name: str, path: str) -> int:
    return _int(section, name, path, minimum=1, maximum=65535)


def _websocket_path(section: Mapping[str, Any]) -> str:
    value = _string(section, "websocket_path", "server")
    if not value.startswith("/") or "?" in value or "#" in value:
        raise ConfigError("server.websocket_path must be an absolute URL path")
    if value == "/api/v1/events/stream":
        raise ConfigError("server.websocket_path conflicts with the console event stream")
    return value


def _float(
    section: Mapping[str, Any],
    name: str,
    path: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    value = section.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{path}.{name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ConfigError(f"{path}.{name} must be finite")
    if minimum is not None and result < minimum:
        raise ConfigError(f"{path}.{name} must be at least {minimum}")
    if maximum is not None and result > maximum:
        raise ConfigError(f"{path}.{name} must be at most {maximum}")
    return result


def _bool(section: Mapping[str, Any], name: str, path: str) -> bool:
    value = section.get(name)
    if not isinstance(value, bool):
        raise ConfigError(f"{path}.{name} must be a boolean")
    return value


def _string_list(section: Mapping[str, Any], name: str, path: str) -> tuple[str, ...]:
    value = section.get(name)
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ConfigError(f"{path}.{name} must be a list of non-empty strings")
    return tuple(value)


def _optional_secret(environment: Mapping[str, str], name: str) -> str | None:
    value = environment.get(name)
    if value is None or not value.strip() or value.strip() == "replace_me":
        return None
    return value.strip()


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
    system_prompt = section.get("system_prompt")
    if system_prompt is not None and (
        not isinstance(system_prompt, str) or not system_prompt.strip()
    ):
        raise ConfigError(f"{path}.system_prompt must be a non-empty string when provided")
    stream = section.get("stream", False)
    if not isinstance(stream, bool):
        raise ConfigError(f"{path}.stream must be a boolean")
    resource_id = section.get("resource_id")
    if resource_id is not None and (
        not isinstance(resource_id, str) or not resource_id.strip()
    ):
        raise ConfigError(f"{path}.resource_id must be a non-empty string when provided")
    speaker = section.get("speaker")
    if speaker is not None and (not isinstance(speaker, str) or not speaker.strip()):
        raise ConfigError(f"{path}.speaker must be a non-empty string when provided")
    chunk_ms = section.get("chunk_ms")
    if chunk_ms is not None and (
        isinstance(chunk_ms, bool) or not isinstance(chunk_ms, int) or chunk_ms < 20
    ):
        raise ConfigError(f"{path}.chunk_ms must be an integer of at least 20")
    max_tokens = _optional_bounded_int(
        section,
        "max_tokens",
        path,
        minimum=1,
        maximum=8_192,
    )
    input_max_chars = _optional_bounded_int(
        section,
        "input_max_chars",
        path,
        minimum=32,
        maximum=32_768,
    )
    output_max_chars = _optional_bounded_int(
        section,
        "output_max_chars",
        path,
        minimum=16,
        maximum=8_192,
    )
    return CloudProviderConfig(
        provider=_string(section, "provider", path, allow_empty=True),
        base_url=_string(section, "base_url", path, allow_empty=True),
        api_key_env=_string(section, "api_key_env", path),
        model=model_value,
        stream=stream,
        thinking=thinking_value,
        resource_id=resource_id,
        speaker=speaker,
        chunk_ms=chunk_ms,
        max_tokens=max_tokens,
        input_max_chars=input_max_chars,
        output_max_chars=output_max_chars,
        system_prompt=system_prompt.strip() if system_prompt is not None else None,
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
    selected_environment = _environment(selected_env_file, environment)
    raw_loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    root = _mapping(_substitute(raw_loaded, selected_environment), "config")

    project = _section(root, "project")
    server = _section(root, "server")
    mqtt = _section(root, "mqtt")
    dispatch = _section(root, "dispatch")
    tcp = _section(root, "tcp")
    device_websocket = _section(root, "device_websocket")
    device_udp = _section(root, "device_udp")
    runtime = _section(root, "runtime")
    discovery = _section(root, "discovery")
    database = _section(root, "database")
    audio = _section(root, "audio")
    wake = _section(root, "wake")
    cloud = _section(root, "cloud")
    zhihu = _section(root, "zhihu")
    ota = _section(root, "ota")
    logging_config = _section(root, "logging")

    console_token_env = _string(server, "console_token_env", "server")
    master_password_env = _string(mqtt, "master_password_env", "mqtt")
    provisioning_token_env = _string(ota, "provisioning_token_env", "ota")
    heartbeat_stale_seconds = _float(
        mqtt, "heartbeat_stale_seconds", "mqtt", minimum=0.1
    )
    heartbeat_offline_seconds = _float(
        mqtt, "heartbeat_offline_seconds", "mqtt", minimum=0.1
    )
    if heartbeat_offline_seconds <= heartbeat_stale_seconds:
        raise ConfigError(
            "mqtt.heartbeat_offline_seconds must be greater than "
            "mqtt.heartbeat_stale_seconds"
        )

    cloud_asr = _section(cloud, "asr")
    cloud_llm = _section(cloud, "llm")
    cloud_tts = _section(cloud, "tts")
    asr_api_key_env = _string(cloud_asr, "api_key_env", "cloud.asr")
    llm_api_key_env = _string(cloud_llm, "api_key_env", "cloud.llm")
    tts_api_key_env = _string(cloud_tts, "api_key_env", "cloud.tts")
    zhihu_access_secret_env = _string(
        zhihu,
        "access_secret_env",
        "zhihu",
    )
    zhihu_base_url = _string(zhihu, "base_url", "zhihu").rstrip("/")
    if zhihu_base_url != "https://developer.zhihu.com":
        raise ConfigError("zhihu.base_url must be https://developer.zhihu.com")
    speech_end_grace_seconds = _float(
        wake,
        "speech_end_grace_seconds",
        "wake",
        minimum=0.1,
    )
    max_utterance_seconds = _float(
        wake,
        "max_utterance_seconds",
        "wake",
        minimum=1.0,
    )
    cloud_request_timeout_seconds = _float(
        cloud,
        "request_timeout_seconds",
        "cloud",
        minimum=0.1,
    )
    if max_utterance_seconds <= speech_end_grace_seconds:
        raise ConfigError(
            "wake.max_utterance_seconds must exceed wake.speech_end_grace_seconds"
        )
    if max_utterance_seconds + speech_end_grace_seconds >= cloud_request_timeout_seconds:
        raise ConfigError(
            "wake.max_utterance_seconds plus wake.speech_end_grace_seconds must be "
            "less than cloud.request_timeout_seconds"
        )

    return AppConfig(
        project=ProjectConfig(name=_string(project, "name", "project")),
        server=ServerConfig(
            enabled=_bool(server, "enabled", "server"),
            host=_string(server, "host", "server"),
            port=_port(server, "port", "server"),
            websocket_path=_websocket_path(server),
            console_auth_required=_bool(server, "console_auth_required", "server"),
            console_token_env=console_token_env,
            allowed_origins=_string_list(server, "allowed_origins", "server"),
        ),
        mqtt=MqttConfig(
            enabled=_bool(mqtt, "enabled", "mqtt"),
            host=_string(mqtt, "host", "mqtt"),
            port=_port(mqtt, "port", "mqtt"),
            max_connections=_int(mqtt, "max_connections", "mqtt", minimum=1),
            credentials_path=_string(mqtt, "credentials_path", "mqtt"),
            master_username=_string(mqtt, "master_username", "mqtt"),
            master_password_env=master_password_env,
            heartbeat_stale_seconds=heartbeat_stale_seconds,
            heartbeat_offline_seconds=heartbeat_offline_seconds,
            gateway_reconnect_seconds=_float(
                mqtt, "gateway_reconnect_seconds", "mqtt", minimum=0.1
            ),
            query_timeout_seconds=_float(
                mqtt, "query_timeout_seconds", "mqtt", minimum=0.1
            ),
        ),
        dispatch=DispatchConfig(
            queue_size_per_device=_int(
                dispatch, "queue_size_per_device", "dispatch", minimum=1
            ),
            ack_timeout_seconds=_float(
                dispatch, "ack_timeout_seconds", "dispatch", minimum=0.1
            ),
            completion_timeout_seconds=_float(
                dispatch, "completion_timeout_seconds", "dispatch", minimum=0.1
            ),
            state_query_interval_seconds=_float(
                dispatch, "state_query_interval_seconds", "dispatch", minimum=0.05
            ),
        ),
        tcp=TcpConfig(
            enabled=_bool(tcp, "enabled", "tcp"),
            host=_string(tcp, "host", "tcp"),
            port=_port(tcp, "port", "tcp"),
            max_connections=_int(tcp, "max_connections", "tcp", minimum=1),
            hello_timeout_seconds=_float(
                tcp, "hello_timeout_seconds", "tcp", minimum=0.1
            ),
            write_timeout_seconds=_float(
                tcp, "write_timeout_seconds", "tcp", minimum=0.1
            ),
            max_frame_bytes=_int(
                tcp, "max_frame_bytes", "tcp", minimum=256, maximum=1024 * 1024
            ),
        ),
        device_websocket=DeviceWebsocketConfig(
            enabled=_bool(device_websocket, "enabled", "device_websocket"),
            hello_timeout_seconds=_float(
                device_websocket,
                "hello_timeout_seconds",
                "device_websocket",
                minimum=0.1,
            ),
            heartbeat_seconds=_float(
                device_websocket,
                "heartbeat_seconds",
                "device_websocket",
                minimum=0.1,
            ),
            max_connections=_int(
                device_websocket, "max_connections", "device_websocket", minimum=1
            ),
            max_frame_bytes=_int(
                device_websocket,
                "max_frame_bytes",
                "device_websocket",
                minimum=256,
                maximum=1024 * 1024,
            ),
            audio_buffer_frames_per_device=_int(
                device_websocket,
                "audio_buffer_frames_per_device",
                "device_websocket",
                minimum=1,
            ),
        ),
        device_udp=DeviceUdpConfig(
            enabled=_bool(device_udp, "enabled", "device_udp"),
            host=_string(device_udp, "host", "device_udp"),
            port=_port(device_udp, "port", "device_udp"),
            advertise_host=_string(
                device_udp,
                "advertise_host",
                "device_udp",
            ),
            max_datagram_bytes=_int(
                device_udp,
                "max_datagram_bytes",
                "device_udp",
                minimum=256,
                maximum=65_507,
            ),
            audio_buffer_frames_per_device=_int(
                device_udp,
                "audio_buffer_frames_per_device",
                "device_udp",
                minimum=1,
            ),
            session_timeout_seconds=_float(
                device_udp,
                "session_timeout_seconds",
                "device_udp",
                minimum=10,
            ),
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
            refresh_interval_seconds=_float(
                discovery,
                "refresh_interval_seconds",
                "discovery",
                minimum=1.0,
            ),
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
            tts_pcm_gain=_float(
                audio,
                "tts_pcm_gain",
                "audio",
                minimum=0.25,
                maximum=4.0,
            ),
        ),
        wake=WakeConfig(
            command_timeout_seconds=_float(wake, "command_timeout_seconds", "wake", minimum=0.1),
            conversation_timeout_seconds=_float(
                wake, "conversation_timeout_seconds", "wake", minimum=0.1
            ),
            idle_timeout_seconds=_float(
                wake, "idle_timeout_seconds", "wake", minimum=0.1
            ),
            speech_end_grace_seconds=speech_end_grace_seconds,
            max_utterance_seconds=max_utterance_seconds,
            laughter_action=_string(wake, "laughter_action", "wake"),
            laughter_timeout_seconds=_float(
                wake, "laughter_timeout_seconds", "wake", minimum=1.0
            ),
            state_query_interval_seconds=_float(
                wake, "state_query_interval_seconds", "wake", minimum=0.05
            ),
            state_query_timeout_seconds=_float(
                wake, "state_query_timeout_seconds", "wake", minimum=0.1
            ),
            acknowledgement_template=_string(wake, "acknowledgement_template", "wake"),
            fail_closed=_bool(wake, "fail_closed", "wake"),
        ),
        cloud=CloudConfig(
            request_timeout_seconds=cloud_request_timeout_seconds,
            asr=_provider(cloud_asr, "cloud.asr"),
            llm=_provider(cloud_llm, "cloud.llm"),
            tts=_provider(cloud_tts, "cloud.tts"),
        ),
        zhihu=ZhihuConfig(
            enabled=_bool(zhihu, "enabled", "zhihu"),
            base_url=zhihu_base_url,
            access_secret_env=zhihu_access_secret_env,
            request_timeout_seconds=_float(
                zhihu,
                "request_timeout_seconds",
                "zhihu",
                minimum=0.1,
                maximum=120,
            ),
            answer_timeout_seconds=_float(
                zhihu,
                "answer_timeout_seconds",
                "zhihu",
                minimum=0.1,
                maximum=180,
            ),
            max_response_bytes=_int(
                zhihu,
                "max_response_bytes",
                "zhihu",
                minimum=1_024,
                maximum=16 * 1024 * 1024,
            ),
            max_concurrency=_int(
                zhihu,
                "max_concurrency",
                "zhihu",
                minimum=1,
                maximum=32,
            ),
            event_history_size=_int(
                zhihu,
                "event_history_size",
                "zhihu",
                minimum=1,
                maximum=1_000,
            ),
        ),
        ota=OtaConfig(
            enabled=_bool(ota, "enabled", "ota"),
            firmware_path=_string(ota, "firmware_path", "ota"),
            firmware_version=_string(ota, "firmware_version", "ota"),
            target_hardware=_string(ota, "target_hardware", "ota"),
            provisioning_token_env=provisioning_token_env,
        ),
        logging=LoggingConfig(
            level=_string(logging_config, "level", "logging").upper(),
            jsonl_path=_string(logging_config, "jsonl_path", "logging"),
        ),
        config_path=config_path,
        env_file=selected_env_file if selected_env_file.is_file() else None,
        secrets=RuntimeSecrets(
            console_token=_optional_secret(selected_environment, console_token_env),
            mqtt_master_password=_optional_secret(selected_environment, master_password_env),
            provisioning_token=_optional_secret(selected_environment, provisioning_token_env),
            asr_api_key=_optional_secret(selected_environment, asr_api_key_env),
            llm_api_key=_optional_secret(selected_environment, llm_api_key_env),
            tts_api_key=_optional_secret(selected_environment, tts_api_key_env),
            zhihu_access_secret=_optional_secret(
                selected_environment,
                zhihu_access_secret_env,
            ),
        ),
    )
