from __future__ import annotations

from pathlib import Path

from otto_master.config import load_config


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
    assert config.mqtt.enabled is True
    assert config.mqtt.port == 1883
    assert config.mqtt.heartbeat_stale_seconds == 15
    assert config.mqtt.heartbeat_offline_seconds == 30
    assert config.mqtt.query_timeout_seconds == 3
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
