"""Transport-neutral access to short-lived device audio and TTS playback."""

from __future__ import annotations

from typing import Protocol

from ..services.tts import TtsPlayback


class AudioFrameStore(Protocol):
    async def take_audio_frame(
        self,
        device_id: str,
        utterance_id: str,
        sequence: int,
        frame_ref: str,
    ) -> bytes | None: ...


class PlaybackGateway(AudioFrameStore, Protocol):
    async def start_tts_playback(self, device_id: str) -> TtsPlayback: ...

    async def send_transcription(
        self,
        device_id: str,
        session_id: str,
        text: str,
    ) -> None: ...

    async def close_audio_session(self, device_id: str, session_id: str) -> bool: ...


class SessionPlaybackGateway(PlaybackGateway, Protocol):
    async def has_session(self, device_id: str) -> bool: ...


class DeviceAudioRouter:
    """Route opaque frame references and playback to the active device transport."""

    def __init__(
        self,
        mqtt_udp: SessionPlaybackGateway,
        websocket: PlaybackGateway,
    ) -> None:
        self.mqtt_udp = mqtt_udp
        self.websocket = websocket

    async def take_audio_frame(
        self,
        device_id: str,
        utterance_id: str,
        sequence: int,
        frame_ref: str,
    ) -> bytes | None:
        if frame_ref.startswith("udp:"):
            return await self.mqtt_udp.take_audio_frame(
                device_id,
                utterance_id,
                sequence,
                frame_ref,
            )
        return await self.websocket.take_audio_frame(
            device_id,
            utterance_id,
            sequence,
            frame_ref,
        )

    async def start_tts_playback(self, device_id: str) -> TtsPlayback:
        if await self.mqtt_udp.has_session(device_id):
            return await self.mqtt_udp.start_tts_playback(device_id)
        return await self.websocket.start_tts_playback(device_id)

    async def send_transcription(
        self,
        device_id: str,
        session_id: str,
        text: str,
    ) -> None:
        """Display one final user transcription on the active device session."""

        if await self.mqtt_udp.has_session(device_id):
            await self.mqtt_udp.send_transcription(device_id, session_id, text)
            return
        await self.websocket.send_transcription(device_id, session_id, text)

    async def close_audio_session(self, device_id: str, session_id: str) -> bool:
        """Close one completed voice turn on its currently active transport."""

        if await self.mqtt_udp.has_session(device_id):
            return await self.mqtt_udp.close_audio_session(device_id, session_id)
        return await self.websocket.close_audio_session(device_id, session_id)
