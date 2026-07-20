"""Provider contracts for the Specialist loop.

The full loop is a chain of swappable stages:

    ASR  ->  MT (optional)  ->  Specialist core  ->  MT (optional)  ->  TTS

Each stage is defined here as a small typed Protocol, so any backend that
implements the interface can be dropped in without touching the rest of the
loop. On the device these are BHASHINI (ASR/MT/TTS) + the stock LLM; on a
laptop they are Vosk / macOS ``say`` / a no-op MT / ollama — same interfaces,
different implementations.

This module is intentionally dependency-free (stdlib only) so it can be imported
on the device and on a laptop alike; concrete providers live next to their
dependencies (e.g. the ``local/`` harness).
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass
from typing import Callable, Optional, Protocol, runtime_checkable


@dataclass
class AudioClip:
    """A chunk of captured audio, backend-agnostic.

    Holds raw little-endian PCM plus its format, and can render a WAV container
    on demand (BHASHINI ASR wants WAV bytes; Vosk wants raw PCM frames).
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


@runtime_checkable
class MicSource(Protocol):
    """Captures audio from an input device (or a file, for testing)."""

    name: str

    def record(self) -> AudioClip:
        ...


@runtime_checkable
class ASRProvider(Protocol):
    """Speech -> text."""

    name: str

    def transcribe(self, clip: AudioClip, language: str = "en") -> str:
        ...


@runtime_checkable
class MTProvider(Protocol):
    """Text -> translated text. ``NoopMT`` is a valid passthrough implementation."""

    name: str

    def translate(self, text: str, src_lang: str, tgt_lang: str) -> str:
        ...


@runtime_checkable
class TTSProvider(Protocol):
    """Text -> spoken audio.

    ``speak`` plays it; ``synthesize`` optionally returns audio bytes (WAV) for
    callers that want to handle playback themselves (e.g. the device's ALSA
    player). A provider may implement only ``speak`` and return None from
    ``synthesize``.
    """

    name: str

    def speak(self, text: str, language: str = "en") -> None:
        ...

    def synthesize(self, text: str, language: str = "en") -> Optional[bytes]:
        ...


# The LLM contract is already a simple callable used by SpecialistEngine:
#   (system_prompt, user_prompt) -> answer_text
LLMProvider = Callable[[str, str], str]
