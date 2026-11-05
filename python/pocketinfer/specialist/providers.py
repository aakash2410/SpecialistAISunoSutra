"""The captured audio clip, shared by the microphone, speech input and the voice loop."""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass


@dataclass
class AudioClip:
    """A chunk of captured audio, backend-agnostic.

    Holds raw little-endian PCM plus its format, and can render a WAV container on demand.
    """

    pcm: bytes
    sample_rate: int = 16000
    sample_width: int = 2  # bytes per sample (16-bit)
    channels: int = 1

    def get_wav_data(self) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(self.channels)
            w.setsampwidth(self.sample_width)
            w.setframerate(self.sample_rate)
            w.writeframes(self.pcm)
        return buf.getvalue()

    @classmethod
    def from_wav_bytes(cls, data: bytes) -> "AudioClip":
        with wave.open(io.BytesIO(data), "rb") as w:
            return cls(
                pcm=w.readframes(w.getnframes()),
                sample_rate=w.getframerate(),
                sample_width=w.getsampwidth(),
                channels=w.getnchannels(),
            )

    @classmethod
    def from_wav_file(cls, path: str) -> "AudioClip":
        with open(path, "rb") as f:
            return cls.from_wav_bytes(f.read())

    @property
    def duration_seconds(self) -> float:
        frames = len(self.pcm) / (self.sample_width * self.channels)
        return frames / float(self.sample_rate or 1)
