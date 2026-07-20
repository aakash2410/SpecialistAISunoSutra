"""Audio capture + playback for the laptop harness.

Implements the ``MicSource`` contract two ways: a live microphone (sounddevice)
and a WAV file (for automated tests / replaying captured questions). Both return
a backend-agnostic ``AudioClip`` (16 kHz mono 16-bit — what Vosk and BHASHINI
ASR both accept).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import threading

from pocketinfer.specialist.providers import AudioClip

logger = logging.getLogger(__name__)

DEFAULT_RATE = 16000


class SoundDeviceMic:
    """Live microphone. Press Enter to start, Enter again to stop."""

    name = "sounddevice-mic"

    def __init__(self, sample_rate: int = DEFAULT_RATE, prompt: bool = True):
        try:
            import sounddevice  # noqa: F401
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                "sounddevice not available. Install the laptop deps:\n"
                "  pip install -r local/requirements-local.txt\n"
                f"(underlying error: {e})"
            )
        self.sample_rate = sample_rate
        self.prompt = prompt

    def record(self) -> AudioClip:
        import numpy as np
        import sounddevice as sd

        frames = []

        def _callback(indata, frame_count, time_info, status):  # noqa: ANN001
            if status:
                logger.debug("mic status: %s", status)
            frames.append(indata.copy())

        if self.prompt:
            input("\n[mic] Press Enter to START recording, then speak...")
        stop = threading.Event()

        def _wait_enter():
            input("[mic] Recording... press Enter to STOP.\n")
            stop.set()

        t = threading.Thread(target=_wait_enter, daemon=True)
        t.start()
        with sd.InputStream(samplerate=self.sample_rate, channels=1, dtype="int16", callback=_callback):
            while not stop.is_set():
                sd.sleep(100)

        if not frames:
            return AudioClip(pcm=b"", sample_rate=self.sample_rate)
        pcm = np.concatenate(frames, axis=0).tobytes()
        return AudioClip(pcm=pcm, sample_rate=self.sample_rate, sample_width=2, channels=1)


class WavFileMic:
    """A 'mic' that replays a WAV file — used for automated, headless testing."""

    name = "wav-file"

    def __init__(self, path: str):
        self.path = path

    def record(self) -> AudioClip:
        logger.info("Loading audio from %s", self.path)
        return AudioClip.from_wav_file(self.path)


class SayFileMic:
    """Synthesizes a question with macOS ``say`` and feeds it in as if spoken.

    Lets you exercise the whole ASR->...->TTS loop headlessly (no live mic) with
    an arbitrary text question. macOS only.
    """

    name = "say-file"

    def __init__(self, text: str, sample_rate: int = DEFAULT_RATE, voice: str | None = None):
        self.text = text
        self.sample_rate = sample_rate
        self.voice = voice

    def record(self) -> AudioClip:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        cmd = ["say", "--file-format=WAVE", f"--data-format=LEI16@{self.sample_rate}", "-o", tmp.name]
        if self.voice:
            cmd += ["-v", self.voice]
        cmd.append(self.text)
        subprocess.run(cmd, check=True)
        try:
            return AudioClip.from_wav_file(tmp.name)
        finally:
            os.unlink(tmp.name)


def play_wav_bytes(data: bytes) -> None:
    """Play WAV bytes on macOS via afplay (used when a TTS returns audio bytes)."""
    if not data:
        return
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    try:
        tmp.write(data)
        tmp.close()
        player = "afplay" if sys.platform == "darwin" else "aplay"
        subprocess.run([player, tmp.name], check=False)
    finally:
        os.unlink(tmp.name)
