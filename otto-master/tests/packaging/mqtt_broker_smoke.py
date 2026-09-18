"""Standalone PyInstaller smoke entry for the embedded MQTT broker."""

from __future__ import annotations

import asyncio
import socket
import tempfile
from pathlib import Path

from otto_master.config import MqttConfig
from otto_master.gateways.mqtt_broker import BrokerState, EmbeddedMqttBroker


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def smoke() -> None:
    with tempfile.TemporaryDirectory(prefix="otto-mqtt-smoke-") as directory:
        port = free_port()
        config = MqttConfig(
            enabled=True,
            host="127.0.0.1",
            port=port,
            max_connections=4,
            credentials_path="credentials.json",
            master_username="otto-master",
            master_password_env="OTTO_MQTT_MASTER_PASSWORD",
            heartbeat_stale_seconds=15,
            heartbeat_offline_seconds=30,
            gateway_reconnect_seconds=2,
            query_timeout_seconds=3,
        )
        broker = EmbeddedMqttBroker(
            config,
            Path(directory) / "credentials.json",
            configured_master_password="pyinstaller-smoke-password",
        )
        await broker.start()
        if broker.state is not BrokerState.RUNNING:
            raise RuntimeError("broker did not enter running state")
        await broker.shutdown()
        if broker.state is not BrokerState.STOPPED:
            raise RuntimeError("broker did not enter stopped state")


def main() -> int:
    asyncio.run(smoke())
    print("mqtt-broker-smoke:pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
