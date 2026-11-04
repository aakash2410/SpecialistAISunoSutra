"""On-device answer verifier: does an approved passage support this sentence?

``cross-encoder/nli-deberta-v3-small`` (quantised, ~173 MB) on onnxruntime. It reads
a passage and a sentence and scores whether the passage entails the sentence, which
is the check retrieval scores cannot do: a passage can be related to a question
without saying what the model answered.

Long passages can hide a match, so each passage is checked whole and then sentence
by sentence, stopping at the first one that supports the claim. If the model is
missing this raises rather than letting unverified answers through.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import TYPE_CHECKING, List, Optional, Sequence

import numpy as np

if TYPE_CHECKING:
    from ..specialist.vector_index import Retrieved

logger = logging.getLogger(__name__)

# Scores cluster near 0 and 1, so results did not change between 0.5 and 0.8.
SUPPORT_THRESHOLD = 0.5

_DIR_ENV = "SPECIALIST_VERIFIER_DIR"
_DIRNAME = "nli-deberta-v3-small-onnx"
_SENTENCE_RE = re.compile(r"(?<=[.!?;])\s+")


def _resolve_dir(explicit: Optional[str] = None, env: str = _DIR_ENV, dirname: str = _DIRNAME) -> Optional[str]:
    here = os.path.dirname(os.path.abspath(__file__))            # .../python/pocketinfer/models
    repo_root = os.path.abspath(os.path.join(here, "..", "..", ".."))
    candidates = [
        explicit,
        os.environ.get(env),
        os.path.join(repo_root, "local", "models", dirname),     # dev checkout
        os.path.join("/opt/specialist/models", dirname),         # device data dir
    ]
    for c in candidates:
        if c and all(os.path.exists(os.path.join(c, f))
                     for f in ("model.onnx", "tokenizer.json", "config.json")):
            return c
    return None


class Verifier:
    def __init__(self, model_dir: Optional[str] = None):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        found = _resolve_dir(model_dir)
        if not found:
            raise RuntimeError(
                "Answer verifier not found. Fetch it with tools/fetch_onnx_verifier.py "
                f"or set {_DIR_ENV} to a dir with model.onnx + tokenizer.json + config.json."
            )
        with open(os.path.join(found, "config.json"), encoding="utf-8") as f:
            labels = json.load(f)["id2label"]
        self._entailment = next(int(i) for i, name in labels.items() if name.lower() == "entailment")
        self._contradiction = next(int(i) for i, name in labels.items() if name.lower() == "contradiction")
        self._tokenizer = Tokenizer.from_file(os.path.join(found, "tokenizer.json"))
        self._tokenizer.enable_truncation(max_length=512)
        self._tokenizer.no_padding()
        so = ort.SessionOptions()
        so.log_severity_level = 3  # 3 = error and above only
        self._session = ort.InferenceSession(os.path.join(found, "model.onnx"), sess_options=so,
                                             providers=["CPUExecutionProvider"])
        self._inputs = {i.name for i in self._session.get_inputs()}
        logger.info("Verifier: using %s", found)

    def _logits(self, premise: str, sentence: str) -> np.ndarray:
        enc = self._tokenizer.encode(premise, sentence)
        feeds = {"input_ids": np.array([enc.ids], dtype=np.int64),
                 "attention_mask": np.array([enc.attention_mask], dtype=np.int64)}
        if "token_type_ids" in self._inputs:
            feeds["token_type_ids"] = np.array([enc.type_ids], dtype=np.int64)
        return self._session.run(None, feeds)[0][0]

    def entailment_margin(self, premise: str, sentence: str) -> float:
        """How much more the model favours entailment than contradiction, in logits."""
        logits = self._logits(premise, sentence)
        return float(logits[self._entailment] - logits[self._contradiction])

    def support_score(self, premise: str, sentence: str) -> float:
        """Probability that ``premise`` entails ``sentence``."""
        logits = self._logits(premise, sentence)
        probs = np.exp(logits - logits.max())
        return float((probs / probs.sum())[self._entailment])

    def supporting_passage(self, sentence: str, passages: Sequence[Retrieved]) -> Optional[Retrieved]:
        """Return the first passage that supports ``sentence``, or None."""
        for passage in passages:
            if self.support_score(passage.text, sentence) >= SUPPORT_THRESHOLD:
                return passage
        for passage in passages:
            parts: List[str] = [p.strip() for p in _SENTENCE_RE.split(passage.text) if p.strip()]
            if len(parts) > 1 and any(self.support_score(p, sentence) >= SUPPORT_THRESHOLD for p in parts):
                return passage
        return None
