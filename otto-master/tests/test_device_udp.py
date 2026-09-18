from __future__ import annotations

import asyncio
import socket
import struct
from typing import Any

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from otto_master.config import AudioConfig, DeviceUdpConfig
from otto_master.gateways.device_udp import DeviceUdpGateway, DeviceUdpPlaybackError
from otto_master.message_bus import MessageBus
from otto_master.messages import Message

DEVICE_ID = "aabbccddeeff"


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _crypt(key: bytes, nonce: bytes, payload: bytes) -> bytes:
    cryptor = Cipher(algorithms.AES(key), modes.CTR(nonce)).encryptor()
    return cryptor.update(payload) + cryptor.finalize()


async def _wait_message(messages: list[Message], topic: str) -> Message:
    deadline = asyncio.get_running_loop().time() + 2
    while asyncio.get_running_loop().time() < deadline:
        match = next((message for message in messages if message.topic == topic), None)
        if match is not None:
            return match
        await asyncio.sleep(0.005)
    raise AssertionError(f"message {topic} was not published")


@pytest.mark.asyncio
async def test_mqtt_negotiated_udp_audio_round_trip() -> None:
    port = _free_udp_port()
    controls: list[tuple[str, dict[str, Any]]] = []
    messages: list[Message] = []

    async def send_control(device_id: str, payload: dict[str, Any]) -> None:
        controls.append((device_id, dict(payload)))

    async def observe(message: Message) -> None:
        messages.append(message)

    bus = MessageBus()
    gateway = DeviceUdpGateway(
        DeviceUdpConfig(
            enabled=True,
            host="127.0.0.1",
            port=port,
            advertise_host="127.0.0.1",
            max_datagram_bytes=4096,
            audio_buffer_frames_per_device=8,
            session_timeout_seconds=30,
        ),
        AudioConfig(
            input_sample_rate=16_000,
            output_sample_rate=24_000,
            channels=1,
            frame_duration_ms=60,
            format="opus",
        ),
        bus,
        send_control,
    )
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.bind(("127.0.0.1", 0))
    client.setblocking(False)
    await bus.start()
    observer = await bus.subscribe_observer(observe)
    await gateway.start()
    try:
        handled = await gateway.handle_mqtt_value(
            DEVICE_ID,
            {
                "type": "hello",
                "version": 3,
                "transport": "udp",
                "features": {"mcp": True},
                "audio_params": {
                    "format": "opus",
                    "sample_rate": 16_000,
                    "channels": 1,
                    "frame_duration": 60,
                },
            },
        )
        assert handled is True
        server_hello = controls[-1][1]
        assert server_hello["transport"] == "udp"
        assert server_hello["audio_params"] == {
            "format": "opus",
            "sample_rate": 24_000,
            "channels": 1,
            "frame_duration": 60,
        }
        session_id = str(server_hello["session_id"])
        udp = server_hello["udp"]
        assert isinstance(udp, dict)
        key = bytes.fromhex(str(udp["key"]))
        nonce = bytes.fromhex(str(udp["nonce"]))

        await gateway.handle_mqtt_value(
            DEVICE_ID,
            {
                "type": "listen",
                "session_id": session_id,
                "state": "start",
                "mode": "auto",
            },
        )
        started = await _wait_message(messages, "audio.input.started")
        utterance_id = str(started.payload["utterance_id"])

        await gateway.handle_mqtt_value(
            DEVICE_ID,
            {
                "type": "listen",
                "session_id": session_id,
                "state": "vad",
                "speaking": True,
            },
        )
        activity = await _wait_message(messages, "audio.input.activity")
        assert activity.payload["utterance_id"] == utterance_id
        assert activity.payload["speaking"] is True

        with pytest.raises(DeviceUdpPlaybackError, match="device_session_changed"):
            await gateway.send_transcription(DEVICE_ID, "stale-session", "不应发送")
        await gateway.send_transcription(DEVICE_ID, session_id, "  你是谁？  ")
        assert controls[-1] == (
            DEVICE_ID,
            {
                "session_id": session_id,
                "type": "stt",
                "text": "你是谁？",
            },
        )
        assert gateway.status()["transcriptions_sent"] == 1

        opus = b"device-opus-frame"
        header = bytearray(nonce)
        struct.pack_into("!H", header, 2, len(opus))
        struct.pack_into("!I", header, 8, 1234)
        struct.pack_into("!I", header, 12, 1)
        packet = bytes(header) + _crypt(key, bytes(header), opus)
        await asyncio.get_running_loop().sock_sendto(
            client,
            packet,
            ("127.0.0.1", port),
        )

        frame = await _wait_message(messages, "audio.input.frame")
        assert frame.payload["transport"] == "mqtt"
        assert frame.payload["sequence"] == 0
        assert frame.payload["wire_sequence"] == 1
        assert await gateway.take_audio_frame(
            DEVICE_ID,
            utterance_id,
            0,
            str(frame.payload["frame_ref"]),
        ) == opus

        playback = await gateway.start_tts_playback(DEVICE_ID)
        await playback.send_sentence("你好。")
        response_opus = b"server-opus-frame"
        await playback.send_audio(response_opus)
        response, _ = await asyncio.wait_for(
            asyncio.get_running_loop().sock_recvfrom(client, 4096),
            timeout=2,
        )
        response_header = response[:16]
        assert struct.unpack_from("!H", response_header, 2)[0] == len(response_opus)
        assert struct.unpack_from("!I", response_header, 12)[0] == 1
        assert _crypt(key, response_header, response[16:]) == response_opus
        await playback.stop()

        assert [payload["state"] for _, payload in controls if payload.get("type") == "tts"] == [
            "start",
            "sentence_start",
            "stop",
        ]
        await gateway.handle_mqtt_value(
            DEVICE_ID,
            {
                "type": "listen",
                "session_id": session_id,
                "state": "stop",
            },
        )
        finished = await _wait_message(messages, "audio.input.finished")
        assert finished.payload["utterance_id"] == utterance_id
        assert finished.payload["frame_count"] == 1
        assert gateway.status()["audio_frames_received"] == 1
        assert gateway.status()["audio_frames_sent"] == 1

        close_task = asyncio.create_task(
            gateway.close_audio_session(DEVICE_ID, session_id)
        )
        deadline = asyncio.get_running_loop().time() + 2
        while not any(payload.get("type") == "goodbye" for _, payload in controls):
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError("server goodbye was not published")
            await asyncio.sleep(0.005)
        await gateway.handle_mqtt_value(
            DEVICE_ID,
            {"type": "goodbye", "session_id": session_id},
        )
        assert await close_task is True
        assert await gateway.has_session(DEVICE_ID) is False
        closed = await _wait_message(messages, "voice.session.closed")
        assert closed.payload["session_id"] == session_id
        assert closed.payload["reason"] == "device_goodbye"
    finally:
        await gateway.shutdown()
        await bus.unsubscribe_observer(observer)
        await bus.stop()
        client.close()
