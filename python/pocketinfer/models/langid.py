"""Spoken language ID: which language a recording is in, before choosing speech recognition.

An ONNX export of SpeechBrain's ECAPA-TDNN trained on VoxLingua107 (Apache 2.0, 107
languages; see tools/export_langid.py). It takes raw 16 kHz audio, and takes about 50 ms
for a short question on a Mac CPU.

On its own it is not enough for this device: in tests with synthetic voices it heard
Indian English as Hindi 9 times out of 12. pocketinfer.specialist.language pairs it
with Whisper's confidence to settle English.
"""

from __future__ import annotations

import json
import os
from typing import Dict, Iterable, Optional

import numpy as np

_DIR_ENV = "SPECIALIST_LANGID_DIR"
_DIRNAME = "lang-id-ecapa-onnx"


def _resolve_dir(explicit: Optional[str] = None) -> Optional[str]:
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(here, "..", "..", ".."))
    for c in (explicit, os.environ.get(_DIR_ENV), os.path.join(repo_root, "local", "models", _DIRNAME),
              os.path.join("/opt/specialist/models", _DIRNAME)):
        if c and all(os.path.exists(os.path.join(c, f)) for f in ("model.onnx", "labels.json")):
            return c
    return None


class LanguageId:
    def __init__(self, model_dir: Optional[str] = None):
        import onnxruntime as ort

        found = _resolve_dir(model_dir)
        if not found:
            raise RuntimeError(f"Language ID model not found. Export it with tools/export_langid.py, or set {_DIR_ENV}.")
        with open(os.path.join(found, "labels.json"), encoding="utf-8") as f:
            self.labels = json.load(f)
        so = ort.SessionOptions()
        so.log_severity_level = 3
        self._session = ort.InferenceSession(os.path.join(found, "model.onnx"), sess_options=so,
                                             providers=["CPUExecutionProvider"])

    def log_probs(self, audio: np.ndarray, languages: Optional[Iterable[str]] = None) -> Dict[str, float]:
        """Log-probability per language for float32 mono 16 kHz audio, optionally only some languages."""
        audio = np.asarray(audio, dtype=np.float32).reshape(1, -1)
        out = self._session.run(None, {"audio": audio})[0][0]
        scores = dict(zip(self.labels, (float(v) for v in out)))
        if languages is None:
            return scores
        return {lang: scores[lang] for lang in languages if lang in scores}
