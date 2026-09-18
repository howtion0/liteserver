from __future__ import annotations

from pathlib import Path

import pytest

from otto_master.config import ConfigError, load_config


def test_repository_config_loads_without_exposing_secret_values() -> None:
    config = load_config(
        environment={
            "OTTO_CONSOLE_TOKEN": "console-super-secret",
            "OTTO_MQTT_MASTER_PASSWORD": "mqtt-super-secret",
            "OTTO_PROVISIONING_TOKEN": "provision-super-secret",
        }
    )

    assert config.project.name == "otto-master"
    assert config.cloud.llm.provider == "deepseek"
    assert config.cloud.llm.api_key_env == "DEEPSEEK_API_KEY"
    assert config.cloud.llm.base_url == "https://api.deepseek.com"
    assert config.cloud.llm.max_tokens == 96
    assert config.cloud.llm.input_max_chars == 512
    assert config.cloud.llm.output_max_chars == 96
    assert config.wake.speech_end_grace_seconds == 1.2
    assert config.wake.max_utterance_seconds == 12
    assert config.wake.idle_timeout_seconds == 8
    assert config.wake.laughter_action == "laugh"
    assert config.cloud.llm.system_prompt is not None
    assert "你叫奶龙" in config.cloud.llm.system_prompt
    assert config.cloud.tts.speaker == "zh_female_wanqudashu_moon_bigtts"
    assert config.cloud.tts.resource_id == "volc.service_type.10029"
    assert config.audio.tts_pcm_gain == 2.0
    assert config.ota.firmware_version == "2.0.15"
    assert config.mqtt.enabled is True
    assert config.mqtt.port == 1883
    assert config.mqtt.heartbeat_stale_seconds == 15
    assert config.mqtt.heartbeat_offline_seconds == 30
    assert config.mqtt.query_timeout_seconds == 3
    assert config.dispatch.queue_size_per_device == 16
    assert config.dispatch.ack_timeout_seconds == 3
    assert config.dispatch.completion_timeout_seconds == 30
    assert config.dispatch.state_query_interval_seconds == 1
    assert config.tcp.enabled is False
    assert config.tcp.port == 8765
    assert config.tcp.max_frame_bytes == 65536
    assert config.device_websocket.enabled is True
    assert config.device_websocket.heartbeat_seconds == 5
    assert config.server.websocket_path == "/xiaozhi/v1/"
    assert config.secrets.console_token == "console-super-secret"
    assert config.secrets.mqtt_master_password == "mqtt-super-secret"
    assert config.secrets.provisioning_token == "provision-super-secret"
    assert config.resolve_path(config.logging.jsonl_path).name == "otto-master.jsonl"
    assert "super-secret" not in repr(config)


def test_environment_substitution_uses_explicit_overrides(tmp_path: Path) -> None:
    source = Path(__file__).parents[1] / "config.yaml"
    target = tmp_path / "config.yaml"
    target.write_text(source.read_text(encoding="utf-8").replace("name: otto-master", "name: ${PROJECT_NAME}"), encoding="utf-8")

    config = load_config(target, environment={"PROJECT_NAME": "test-master"})

    assert config.project.name == "test-master"
    assert config.config_path == target.resolve()


@pytest.mark.parametrize(
    ("original", "invalid", "error"),
    [
        ("  port: 8765", "  port: 70000", "tcp.port"),
        ("  max_frame_bytes: 65536", "  max_frame_bytes: 128", "max_frame_bytes"),
        ("  heartbeat_seconds: 5", "  heartbeat_seconds: 0", "heartbeat_seconds"),
        (
            "  websocket_path: /xiaozhi/v1/",
            "  websocket_path: xiaozhi/v1/",
            "websocket_path",
        ),
    ],
)
def test_transport_configuration_rejects_unsafe_bounds(
    tmp_path: Path,
    original: str,
    invalid: str,
    error: str,
) -> None:
    source = Path(__file__).parents[1] / "config.yaml"
    target = tmp_path / "config.yaml"
    contents = source.read_text(encoding="utf-8")
    assert original in contents
    target.write_text(contents.replace(original, invalid, 1), encoding="utf-8")

    with pytest.raises(ConfigError, match=error):
        load_config(target)


@pytest.mark.parametrize("invalid", ["0.24", "4.01", ".nan", ".inf"])
def test_tts_pcm_gain_rejects_out_of_range_or_non_finite_values(
    tmp_path: Path,
    invalid: str,
) -> None:
    source = Path(__file__).parents[1] / "config.yaml"
    target = tmp_path / "config.yaml"
    contents = source.read_text(encoding="utf-8")
    assert "  tts_pcm_gain: 2.0" in contents
    target.write_text(
        contents.replace("  tts_pcm_gain: 2.0", f"  tts_pcm_gain: {invalid}", 1),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="audio.tts_pcm_gain"):
        load_config(target)


@pytest.mark.parametrize("invalid", ["1.2", "29"])
def test_max_utterance_requires_endpoint_and_cloud_timeout_margin(
    tmp_path: Path,
    invalid: str,
) -> None:
    source = Path(__file__).parents[1] / "config.yaml"
    target = tmp_path / "config.yaml"
    contents = source.read_text(encoding="utf-8")
    assert "  max_utterance_seconds: 12" in contents
    target.write_text(
        contents.replace(
            "  max_utterance_seconds: 12",
            f"  max_utterance_seconds: {invalid}",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="wake.max_utterance_seconds"):
        load_config(target)
