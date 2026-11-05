"""Whisper ASR (faster-whisper / CTranslate2).

Context-aware speech-to-text: unlike the small Vosk model (Kaldi + a weak n-gram
LM, which mangles technical vocabulary), Whisper attends over the whole utterance,
so science terms like "photosynthesis" transcribe correctly.

Runs on **faster-whisper**, whose backend is CTranslate2. It stays on the CPU, so it
takes no GPU memory from the answer model, and it only loads when the request is English
speech, so Hindi sessions do not pay for it.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "tiny.en"
# Where model weights are cached (kept with the other on-device data).
_MODEL_ENV = "SPECIALIST_WHISPER_DIR"
_DEFAULT_DIRS = ("/opt/specialist/models/whisper",)


def _resolve_download_root(explicit: Optional[str]) -> Optional[str]:
    for c in (explicit, os.environ.get(_MODEL_ENV), *_DEFAULT_DIRS):
        if c and os.path.isdir(c):
            return c
    # Fall back to the default HF cache (first run downloads there).
    return explicit or os.environ.get(_MODEL_ENV)


class Whisper:
    def __init__(self, model_size: str = DEFAULT_MODEL, model_dir: Optional[str] = None,
                 device: str = "cpu", compute_type: str = "float32"):
        from faster_whisper import WhisperModel

        download_root = _resolve_download_root(model_dir)
        # float32, because the CTranslate2 build on the Jetson has run nothing else on the CPU.
        # A set model folder means the device, which must never try to download at run time.
        self.model_size = model_size
        self._model = WhisperModel(
            model_size, device=device, compute_type=compute_type, download_root=download_root,
            local_files_only=bool(download_root),
        )
        logger.info("Whisper loaded: %s (device=%s, compute=%s)", model_size, device, compute_type)

    @staticmethod
    def pcm16_to_float32(pcm: bytes) -> np.ndarray:
        """Convert 16-bit little-endian PCM to the float32 [-1, 1] array Whisper wants."""
        return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0

    def transcribe(self, audio, language: Optional[str] = "en", task: str = "transcribe") -> str:
        """Speech to text.

        ``audio`` may be a float32 numpy array (mono, 16 kHz), 16-bit PCM bytes,
        or a path/file. ``language`` is ignored for ``.en`` models (English-only).
        ``task``: 'transcribe' (same language) or 'translate' (any language -> English,
        multilingual models only) — the latter turns Hindi speech into an English
        query in one step, no separate MT.
        """
        if isinstance(audio, (bytes, bytearray)):
            audio = self.pcm16_to_float32(bytes(audio))
        lang = None if self.model_size.endswith(".en") else language
        segments, _info = self._model.transcribe(audio, language=lang, task=task, beam_size=5)
        return "".join(seg.text for seg in segments).strip()

    # --- pocketinfer model contract --------------------------------------
    @classmethod
    def verify(cls, args):
        try:
            import faster_whisper  # noqa: F401
        except Exception as e:  # noqa: BLE001
            return False, f"faster-whisper not installed ({e})."
        return True, "faster-whisper available."

    @classmethod
    def update(cls, args):
        """Pre-download the model so run time stays offline."""
        model_size = args.get("model_size", DEFAULT_MODEL)
        model_dir = args.get("model_dir")
        try:
            cls(model_size=model_size, model_dir=model_dir)  # triggers a one-time download
            return True, f"Whisper model '{model_size}' cached."
        except Exception as e:  # noqa: BLE001
            logger.warning("Whisper.update: could not prepare '%s' (%s)", model_size, e)
            return False, f"Whisper unavailable: {e}"
