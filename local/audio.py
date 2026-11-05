"""Audio capture and playback for the voice loop.

Two ways to get a spoken request: an ALSA microphone and a WAV file
(for testing without hardware). Both return an ``AudioClip`` at 16 kHz mono 16-bit,
which Whisper and Bhashini both accept.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile

from pocketinfer.specialist.providers import AudioClip

logger = logging.getLogger(__name__)

DEFAULT_RATE = 16000


class AlsaArecordMic:
    """Record through ALSA ``arecord``. Press Enter to start, Enter again to stop.

    ``plughw`` converts whatever the device captures to 16 kHz mono. Find the device
    with ``arecord -l``.
    """

    name = "arecord-mic"

    def __init__(self, device: str = "plughw:1,0", sample_rate: int = DEFAULT_RATE, prompt: bool = True):
        self.device = device
        self.sample_rate = sample_rate
        self.prompt = prompt

    def record(self) -> AudioClip:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        if self.prompt:
            input(f"\n[mic] Press Enter to START recording ({self.device}), then speak...")
        proc = subprocess.Popen(
            ["arecord", "-D", self.device, "-f", "S16_LE",
             "-r", str(self.sample_rate), "-c", "1", tmp.name],
            stderr=subprocess.DEVNULL,
        )
        try:
            input("[mic] Recording... press Enter to STOP.\n")
        finally:
            proc.terminate()
            proc.wait()
        try:
            return AudioClip.from_wav_file(tmp.name)
        finally:
            os.unlink(tmp.name)


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
