from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from otto_master.config import LoggingConfig, RuntimeSecrets, load_config
from otto_master.gateways.device_ws import DevicePlaybackError
from otto_master.messages import Message
from otto_master.runtime import Runtime

DEVICE_ID = "aabbccddee01"
DEVICE_MAC = "aa:bb:cc:dd:ee:01"


def _free_ports(count: int) -> tuple[int, ...]:
    ports: set[int] = set()
    while len(ports) < count:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            ports.add(int(listener.getsockname()[1]))
    return tuple(ports)


def _assert_port_released(port: int) -> None:
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))


def _headers(provisioned: dict[str, Any], **updates: str) -> dict[str, str]:
    websocket = provisioned["websocket"]
    assert isinstance(websocket, dict)
    result = {
        "Authorization": f"Bearer {websocket['token']}",
        "Protocol-Version": "1",
        "Device-Id": DEVICE_MAC,
        "Client-Id": str(websocket["client_id"]),
    }
    result.update(updates)
    return result


def _hello() -> dict[str, Any]:
    return {
        "type": "hello",
        "version": 1,
        "transport": "websocket",
        "mac": DEVICE_MAC,
        "name": "EVA1",
        "firmware_version": "2.0.5-test",
        "features": {
            "mcp": True,
            "otto_state": True,
            "otto_actions": True,
            "otto_stop": True,
        },
        "audio_params": {
            "format": "opus",
            "sample_rate": 16000,
            "channels": 1,
            "frame_duration": 60,
        },
    }


async def _receive_json(websocket: ClientConnection) -> dict[str, Any]:
    raw = await asyncio.wait_for(websocket.recv(), timeout=2)
    assert isinstance(raw, str)
    value = json.loads(raw)
    assert isinstance(value, dict)
    return value


async def _wait_device(
    runtime: Runtime,
    *,
    status: str,
    actions_count: int | None = None,
    timeout: float = 2,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        device = await runtime.device_manager.get_device(DEVICE_ID)
        if (
            device is not None
            and device["status"] == status
            and (actions_count is None or device["actions_count"] == actions_count)
        ):
            return device
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"device did not reach {status}/{actions_count}: "
        f"{await runtime.device_manager.get_device(DEVICE_ID)}"
    )


async def _wait_command(
    client: httpx.AsyncClient,
    command_id: str,
    status: str,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + 2
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/api/v1/commands/{command_id}")
        if response.status_code == 200 and response.json()["status"] == status:
            value = response.json()
            assert isinstance(value, dict)
            return value
        await asyncio.sleep(0.01)
    raise AssertionError(f"command {command_id} did not reach {status}")


async def _wait_audio_messages(observed: list[Message], count: int) -> list[Message]:
    deadline = asyncio.get_running_loop().time() + 2
    while asyncio.get_running_loop().time() < deadline:
        frames = [message for message in observed if message.topic == "audio.input.frame"]
        if len(frames) >= count:
            return frames
        await asyncio.sleep(0.01)
    raise AssertionError(f"only received {len(frames)} audio frame messages")


async def _assert_rejected(uri: str, headers: dict[str, str]) -> None:
    with pytest.raises((InvalidStatus, ConnectionClosed)):
        async with connect(
            uri,
            additional_headers=headers,
            ping_interval=None,
            proxy=None,
        ) as websocket:
            await websocket.recv()


@pytest.mark.asyncio
async def test_xiaozhi_websocket_v1_runtime_audio_queries_actions_and_cleanup(
    tmp_path: Path,
) -> None:
    loaded = load_config()
    http_port, mqtt_port, tcp_port = _free_ports(3)
    config = replace(
        loaded,
        config_path=tmp_path / "config.yaml",
        logging=LoggingConfig(level="INFO", jsonl_path="logs/runtime.jsonl"),
        server=replace(
            loaded.server,
            enabled=True,
            host="127.0.0.1",
            port=http_port,
            allowed_origins=(f"http://127.0.0.1:{http_port}",),
        ),
        mqtt=replace(
            loaded.mqtt,
            enabled=True,
            host="127.0.0.1",
            port=mqtt_port,
            credentials_path=".local-secrets/runtime-mqtt.json",
            heartbeat_stale_seconds=2,
            heartbeat_offline_seconds=4,
            gateway_reconnect_seconds=0.1,
            query_timeout_seconds=0.4,
        ),
        tcp=replace(
            loaded.tcp,
            enabled=True,
            host="127.0.0.1",
            port=tcp_port,
            hello_timeout_seconds=0.3,
            write_timeout_seconds=0.3,
        ),
        device_websocket=replace(
            loaded.device_websocket,
            enabled=True,
            hello_timeout_seconds=0.3,
            heartbeat_seconds=0.2,
            max_frame_bytes=1024,
            audio_buffer_frames_per_device=2,
        ),
        device_udp=replace(loaded.device_udp, enabled=False),
        dispatch=replace(
            loaded.dispatch,
            queue_size_per_device=4,
            ack_timeout_seconds=0.5,
            completion_timeout_seconds=1,
            state_query_interval_seconds=0.1,
        ),
        discovery=replace(loaded.discovery, enabled=False),
        secrets=RuntimeSecrets(
            console_token=None,
            mqtt_master_password="master-test-password",
            provisioning_token="provision-test-token",
        ),
    )
    runtime = Runtime(config)
    observed: list[Message] = []
    observer_id: str | None = None

    await runtime.start()
    try:
        observer_id = await runtime.message_bus.subscribe_observer(observed.append)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{http_port}") as client:
            response = await client.post(
                "/api/v1/ota/provision",
                json={"mac": DEVICE_MAC},
                headers={"X-Otto-Provisioning-Token": "provision-test-token"},
            )
            assert response.status_code == 200
            provisioned = response.json()
            assert provisioned["device_id"] == DEVICE_ID
            uri = f"ws://127.0.0.1:{http_port}{config.server.websocket_path}"

            await _assert_rejected(
                uri,
                _headers(provisioned, Authorization="Bearer wrong-token"),
            )
            await _assert_rejected(
                uri,
                _headers(provisioned, **{"Client-Id": "unprovisioned-client"}),
            )
            await _assert_rejected(
                uri,
                _headers(provisioned, **{"Protocol-Version": "2"}),
            )

            async with connect(
                uri,
                additional_headers=_headers(provisioned),
                ping_interval=None,
                proxy=None,
            ) as websocket:
                await websocket.send(json.dumps(_hello()))
                server_hello = await _receive_json(websocket)
                assert server_hello["type"] == "hello"
                assert server_hello["transport"] == "websocket"
                assert server_hello["audio_params"]["format"] == "opus"
                session_id = server_hello["session_id"]
                assert isinstance(session_id, str) and session_id
                await websocket.send(b"wake-word-preroll")

                device = await _wait_device(runtime, status="online")
                assert device["transport"] == "websocket"
                assert device["available_transports"] == ["websocket"]

                await websocket.send(
                    json.dumps(
                        {
                            "type": "otto_actions",
                            "session_id": session_id,
                            "actions": [
                                {
                                    "name": "swing",
                                    "parameters": {
                                        "steps": {"type": "integer", "required": True}
                                    },
                                }
                            ],
                        }
                    )
                )
                await websocket.send(
                    json.dumps(
                        {
                            "type": "otto_state",
                            "session_id": session_id,
                            "action_state": "idle",
                            "current_action": None,
                        }
                    )
                )
                await _wait_device(runtime, status="online", actions_count=1)

                verify_task = asyncio.create_task(
                    client.post(f"/api/v1/devices/{DEVICE_ID}/verify")
                )
                for expected_type in ("otto_query", "otto_actions"):
                    query = await _receive_json(websocket)
                    assert query["type"] == expected_type
                    if expected_type == "otto_query":
                        reply: dict[str, Any] = {
                            "type": "otto_state",
                            "id": query["id"],
                            "action_state": "idle",
                            "current_action": None,
                        }
                    else:
                        reply = {
                            "type": "otto_actions",
                            "id": query["id"],
                            "actions": [{"name": "swing"}],
                        }
                    await websocket.send(json.dumps(reply))
                verified = await asyncio.wait_for(verify_task, timeout=2)
                assert verified.status_code == 200
                assert verified.json()["passed"] is True
                assert verified.json()["transport"] == "websocket"

                await websocket.send(
                    json.dumps(
                        {
                            "type": "listen",
                            "session_id": session_id,
                            "state": "start",
                            "mode": "auto",
                        }
                    )
                )
                for frame in (b"\x01\x02", b"\x03", b"\x04\x05\x06"):
                    await websocket.send(frame)
                await websocket.send(
                    json.dumps(
                        {
                            "type": "listen",
                            "session_id": session_id,
                            "state": "detect",
                            "text": "你好小智",
                        }
                    )
                )
                await websocket.send(
                    json.dumps(
                        {
                            "type": "listen",
                            "session_id": session_id,
                            "state": "start",
                            "mode": "auto",
                        }
                    )
                )
                await websocket.send(
                    json.dumps(
                        {
                            "type": "listen",
                            "session_id": session_id,
                            "state": "vad",
                            "speaking": True,
                        }
                    )
                )
                await websocket.send(
                    json.dumps(
                        {
                            "type": "listen",
                            "session_id": session_id,
                            "state": "stop",
                        }
                    )
                )
                frames = await _wait_audio_messages(observed, 3)
                await runtime.message_bus.drain()
                assert runtime.device_websocket.status()["audio_frames_buffered"] == 2
                assert (
                    runtime.device_websocket.status()[
                        "prelisten_audio_frames_dropped"
                    ]
                    == 1
                )
                assert {
                    "audio.input.started",
                    "audio.input.finished",
                    "audio.input.activity",
                    "voice.wake.candidate.received",
                }.issubset({message.topic for message in observed})
                activity = next(
                    message
                    for message in observed
                    if message.topic == "audio.input.activity"
                )
                assert activity.payload["speaking"] is True
                started_messages = [
                    message for message in observed if message.topic == "audio.input.started"
                ]
                finished_messages = [
                    message for message in observed if message.topic == "audio.input.finished"
                ]
                assert len(started_messages) == 2
                assert len(finished_messages) == 2
                started = started_messages[0]
                finished = finished_messages[0]
                utterance_id = str(started.payload["utterance_id"])
                assert finished.payload["utterance_id"] == utterance_id
                assert finished.payload["frame_count"] == 3
                assert finished.payload["reason"] == "listen_restarted"
                assert finished_messages[1].payload["frame_count"] == 0
                assert finished_messages[1].payload["reason"] == "listen_stop"
                assert [message.payload["sequence"] for message in frames] == [0, 1, 2]
                assert all(
                    message.payload["utterance_id"] == utterance_id
                    and message.payload["codec"] == "opus"
                    and message.payload["sample_rate"] == 16000
                    and message.payload["channels"] == 1
                    and message.payload["frame_duration_ms"] == 60
                    for message in frames
                )
                assert all(
                    "bytes" not in json.dumps(message.to_dict()).lower()
                    and "base64" not in json.dumps(message.to_dict()).lower()
                    for message in frames
                )
                frame_refs = [str(message.payload["frame_ref"]) for message in frames]
                assert await runtime.device_websocket.take_audio_frame(
                    DEVICE_ID, utterance_id, 0, frame_refs[0]
                ) is None
                assert await runtime.device_websocket.take_audio_frame(
                    DEVICE_ID, "wrong-utterance", 1, frame_refs[1]
                ) is None
                assert await runtime.device_websocket.take_audio_frame(
                    DEVICE_ID, utterance_id, 99, frame_refs[1]
                ) is None
                assert await runtime.device_websocket.take_audio_frame(
                    DEVICE_ID, utterance_id, 1, frame_refs[1]
                ) == b"\x03"
                assert await runtime.device_websocket.take_audio_frame(
                    DEVICE_ID, utterance_id, 2, frame_refs[2]
                ) == b"\x04\x05\x06"

                with pytest.raises(DevicePlaybackError, match="device_session_changed"):
                    await runtime.device_websocket.send_transcription(
                        DEVICE_ID,
                        "stale-session",
                        "不应发送",
                    )
                await runtime.device_websocket.send_transcription(
                    DEVICE_ID,
                    session_id,
                    "  你是谁？  ",
                )
                assert await _receive_json(websocket) == {
                    "session_id": session_id,
                    "type": "stt",
                    "text": "你是谁？",
                }
                assert runtime.device_websocket.status()["transcriptions_sent"] == 1

                playback = await runtime.device_websocket.start_tts_playback(DEVICE_ID)
                assert await _receive_json(websocket) == {
                    "session_id": session_id,
                    "type": "tts",
                    "state": "start",
                }
                await playback.send_sentence("你好。")
                assert await _receive_json(websocket) == {
                    "session_id": session_id,
                    "type": "tts",
                    "state": "sentence_start",
                    "text": "你好。",
                }
                await playback.send_audio(b"\x11\x22\x33")
                output_audio = await asyncio.wait_for(websocket.recv(), timeout=2)
                assert output_audio == b"\x11\x22\x33"
                await playback.stop()
                assert await _receive_json(websocket) == {
                    "session_id": session_id,
                    "type": "tts",
                    "state": "stop",
                }
                await playback.stop()
                assert runtime.device_websocket.status()["audio_frames_sent"] == 1

                action_response = await client.post(
                    "/api/v1/commands/action",
                    json={
                        "device_id": DEVICE_ID,
                        "action": "swing",
                        "parameters": {},
                        "confirmation": True,
                    },
                )
                assert action_response.status_code == 202
                action_id = action_response.json()["command_id"]
                action = await _receive_json(websocket)
                assert action == {
                    "type": "otto_action",
                    "id": action_id,
                    "action": "swing",
                }
                await websocket.send(
                    json.dumps(
                        {
                            "type": "otto_action_ack",
                            "id": action_id,
                            "ok": True,
                            "action": "swing",
                            "runtime": {
                                "otto": {"action": {"state": "moving", "name": "swing"}}
                            },
                        }
                    )
                )
                await websocket.send(
                    json.dumps(
                        {
                            "type": "otto_state",
                            "id": action_id,
                            "action_state": "idle",
                            "current_action": None,
                        }
                    )
                )
                completed_action = await _wait_command(client, action_id, "completed")
                assert [item["status"] for item in completed_action["history"]] == [
                    "requested",
                    "published",
                    "accepted",
                    "moving",
                    "completed",
                ]

                stop_response = await client.post(f"/api/v1/devices/{DEVICE_ID}/stop")
                assert stop_response.status_code == 202
                stop_id = stop_response.json()["command_id"]
                stop = await _receive_json(websocket)
                assert stop == {"type": "stop", "id": stop_id}
                await websocket.send(
                    json.dumps(
                        {
                            "type": "otto_stop_ack",
                            "id": stop_id,
                            "ok": True,
                            "runtime": {
                                "otto": {"action": {"state": "idle", "name": "idle"}}
                            },
                        }
                    )
                )
                completed_stop = await _wait_command(client, stop_id, "completed")
                assert [item["status"] for item in completed_stop["history"]] == [
                    "requested",
                    "published",
                    "accepted",
                    "completed",
                ]

                health = await client.get("/api/v1/health")
                components = health.json()["components"]
                assert components["mqtt"]["healthy"] is True
                assert components["tcp_gateway"]["healthy"] is True
                assert components["device_websocket"]["healthy"] is True
                assert runtime.device_websocket.status()["messages_published"] == 4
                assert runtime.device_mqtt.status()["messages_published"] == 0
                assert runtime.device_tcp.status()["messages_published"] == 0
                websocket_token = provisioned["websocket"]["token"]
                assert websocket_token not in json.dumps(
                    [message.to_dict() for message in observed]
                )

                async with connect(
                    uri,
                    additional_headers=_headers(provisioned),
                    ping_interval=None,
                    proxy=None,
                ) as replacement:
                    await replacement.send(json.dumps(_hello()))
                    replacement_hello = await _receive_json(replacement)
                    assert replacement_hello["session_id"] != session_id
                    await asyncio.wait_for(websocket.wait_closed(), timeout=2)
                    replacement_device = await _wait_device(runtime, status="online")
                    assert replacement_device["transport"] == "websocket"
                    await replacement.send(b"\x01")
                    await asyncio.sleep(0.05)
                    assert (
                        runtime.device_websocket.status()[
                            "prelisten_audio_frames_dropped"
                        ]
                        == 2
                    )

                await _wait_device(runtime, status="offline")
    finally:
        if observer_id is not None:
            await runtime.message_bus.unsubscribe_observer(observer_id)
        await runtime.shutdown()

    _assert_port_released(http_port)
    _assert_port_released(mqtt_port)
    _assert_port_released(tcp_port)
