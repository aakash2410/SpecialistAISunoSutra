"""Whisper ASR (faster-whisper / CTranslate2).

Context-aware speech-to-text: unlike the small Vosk model (Kaldi + a weak n-gram
LM, which mangles technical vocabulary), Whisper attends over the whole utterance,
so science terms like "photosynthesis" transcribe correctly.

Runs on **faster-whisper**, whose backend is **CTranslate2 — already installed on
the Suno Sutra device** (the BHASHINI NMT uses it, built with CUDA), so this adds
no heavy new runtime, just the small model weights.

Follows the platform model-wrapper shape (``__init__`` + ``verify``/``update``).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "small.en"
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
                 device: str = "auto", compute_type: str = "auto"):
        from faster_whisper import WhisperModel

        download_root = _resolve_download_root(model_dir)
        # 'auto' lets CTranslate2 pick CUDA when available (it is, on the Jetson),
        # else CPU. compute_type 'auto' picks a sensible precision per device.
        self.model_size = model_size
        self._model = WhisperModel(
            model_size, device=device, compute_type=compute_type, download_root=download_root
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
        # Lenient: don't block app startup if Whisper isn't installed/downloaded —
        # the app falls back to Vosk. Report status so provisioning can act.
        try:
            import faster_whisper  # noqa: F401
        except Exception as e:  # noqa: BLE001
            return True, f"faster-whisper not installed ({e}); ASR will fall back to Vosk."
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
            return True, "Whisper unavailable; ASR will fall back to Vosk."
