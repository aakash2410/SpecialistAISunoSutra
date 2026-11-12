"""Audio capture and playback for the voice loop.

Two ways to get a spoken request: an ALSA microphone and a WAV file
(for testing without hardware). Both return an ``AudioClip`` at 16 kHz mono 16-bit,
which Whisper and Bhashini both accept.
"""

from __future__ import annotations

import logging
import os
import struct
import subprocess
import tempfile
import time
from typing import Optional

from pocketinfer.specialist.providers import AudioClip

logger = logging.getLogger(__name__)

DEFAULT_RATE = 16000


MAX_RECORD_SECONDS = 30   # recording stops by itself if nobody presses again
MIN_RECORD_SECONDS = 0.5  # a second press sooner than this is a bounce, not a stop


class AlsaArecordMic:
    """Record through ALSA ``arecord``, started and stopped by a trigger.

    With no trigger it asks for Enter in the terminal, as before. With a ``key`` or
    ``gpio`` trigger (see local.triggers) it beeps when recording starts and stops,
    because a device with no screen has no other way to show it is listening.

    ``plughw`` converts whatever the device captures to 16 kHz mono. Find the device
    with ``arecord -l``.
    """

    name = "arecord-mic"

    def __init__(self, device: str = "plughw:1,0", sample_rate: int = DEFAULT_RATE, prompt: bool = True,
                 trigger=None, max_seconds: float = MAX_RECORD_SECONDS, cues: Optional[bool] = None,
                 on_state=None):
        self.device = device
        self.sample_rate = sample_rate
        self.prompt = prompt
        self.trigger = trigger
        self.max_seconds = max_seconds
        self.cues = trigger is not None if cues is None else cues
        # Told "waiting" and "listening", e.g. by the screen (local.screen).
        self.on_state = on_state or (lambda _state: None)

    def _wait_start(self) -> None:
        if self.trigger is None:
            if self.prompt:
                input(f"\n[mic] Press Enter to START recording ({self.device}), then speak...")
            return
        print(f"\n[mic] waiting for the talk button ({self.trigger.name})", flush=True)
        self.trigger.wait()

    def _wait_stop(self, started: float) -> None:
        if self.trigger is None:
            input("[mic] Recording... press Enter to STOP.\n")
            return
        print("[mic] recording, press again to stop", flush=True)
        while True:
            left = self.max_seconds - (time.monotonic() - started)
            if left <= 0 or not self.trigger.wait(timeout=left):
                print(f"[mic] stopped after {self.max_seconds:.0f} s", flush=True)
                return
            if time.monotonic() - started >= MIN_RECORD_SECONDS:
                return

    def record(self) -> AudioClip:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        self.on_state("waiting")
        self._wait_start()
        self.on_state("listening")
        if self.cues:
            play_wav_bytes(beep_wav(START_TONE))  # before recording starts, so it is not recorded
        proc = subprocess.Popen(
            ["arecord", "-D", self.device, "-f", "S16_LE",
             "-r", str(self.sample_rate), "-c", "1", tmp.name],
            stderr=subprocess.DEVNULL,
        )
        try:
            self._wait_stop(time.monotonic())
        finally:
            proc.terminate()
            proc.wait()
        if self.cues:
            play_wav_bytes(beep_wav(STOP_TONE))
        try:
            return AudioClip.from_wav_file(tmp.name)
        finally:
            os.unlink(tmp.name)


START_TONE, STOP_TONE = 880.0, 660.0  # rising for "listening", falling for "done"


def beep_wav(frequency: float, seconds: float = 0.15, rate: int = DEFAULT_RATE, volume: float = 0.3) -> bytes:
    """A short sine tone as WAV bytes, faded in and out so it does not click."""
    import io
    import math
    import wave

    n = int(seconds * rate)
    fade = max(1, int(0.01 * rate))
    frames = bytearray()
    for i in range(n):
        envelope = min(1.0, i / fade, (n - 1 - i) / fade)
        frames += struct.pack("<h", int(32767 * volume * envelope * math.sin(2 * math.pi * frequency * i / rate)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


class WavFileMic:
    """A 'mic' that replays a WAV file, for testing without hardware."""

    name = "wav-file"

    def __init__(self, path: str):
        self.path = path

    def record(self) -> AudioClip:
        logger.info("Loading audio from %s", self.path)
        return AudioClip.from_wav_file(self.path)


def play_wav_bytes(data: bytes) -> None:
    """Play WAV bytes. Set SPECIALIST_ALSA_DEVICE to choose the speaker (see ``aplay -l``)."""
    if not data:
        return
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    try:
        tmp.write(data)
        tmp.close()
        dev = os.environ.get("SPECIALIST_ALSA_DEVICE")
        cmd = ["aplay"] + (["-D", dev] if dev else []) + [tmp.name]
        r = subprocess.run(cmd)
        # A raw 'hw:' device does no format conversion and rejects mono/odd rates;
        # retry through the 'plug' plugin, which converts channels/rate on the fly.
        if r.returncode != 0 and dev and dev.startswith("hw:"):
            subprocess.run(["aplay", "-D", "plug" + dev, tmp.name], check=False)
    finally:
        os.unlink(tmp.name)


SLOW_TEMPO = 0.8       # "slower" plays at 80% speed, without lowering the pitch
SLOW_GAP_SECONDS = 1.0  # and leaves a pause after each sentence, for writing it down


def slow_wav_bytes(data: bytes, tempo: float = SLOW_TEMPO) -> bytes:
    """Return the WAV slowed to ``tempo`` with ffmpeg, or unchanged if ffmpeg fails."""
    if not data:
        return data
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = os.path.join(tmp, "in.wav"), os.path.join(tmp, "out.wav")
        with open(src, "wb") as f:
            f.write(data)
        try:
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src,
                            "-af", f"atempo={tempo}", dst], check=True, capture_output=True, timeout=20)
            with open(dst, "rb") as f:
                return f.read()
        except (OSError, subprocess.SubprocessError):
            logger.warning("could not slow the audio down, playing it at normal speed")
            return data
