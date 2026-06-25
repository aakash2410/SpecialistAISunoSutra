"""On-device embedding model for retrieval.

This is the *new* model the Specialist spec asks for: a small, multilingual,
Indic-capable embedding model that runs on local storage and is used to embed
the teacher's request and search the baked DIKSHA index.

It follows the same wrapper shape as the other pocketinfer models
(``Asr``/``Nmt``/``Tts``/``Ollama``): an ``__init__``, an inference method
(``embed``), and ``verify`` / ``update`` classmethods so it can be declared in
an application manifest and managed by the service.

Backends (auto-selected, best first):
  1. sentence-transformers  -> a real Indic-capable model (default:
     ``intfloat/multilingual-e5-small``, ~118M params, normalized 384-d vectors).
     This is what ships on the device. Stateless: the same weights embed both
     passages (build time) and queries (run time).
  2. tfidf                  -> a dependency-free, deterministic TF-IDF embedder.
     It is *fitted* on the corpus at build time (vocabulary + IDF weights) and
     that small state is saved next to the index, then reloaded at run time.
     Lower quality than a neural model, but it weights rare content terms so the
     offline demo genuinely retrieves the right passage and refuses off-topic
     questions — no model download required.

The SAME embedder (and, for tfidf, the SAME fitted state) is used at build time
and run time, so query and index vectors are always comparable.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from collections import Counter
from typing import Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

# Default on-device model. Small, multilingual, strong on retrieval, and covers
# major Indic scripts. Sized for the benchmark tab in the spec.
DEFAULT_MODEL_NAME = "intfloat/multilingual-e5-small"

# Recommended refusal thresholds by backend. Neural cosine and TF-IDF cosine
# live on very different scales (e5 has a high similarity floor; pruned TF-IDF
# sends unrelated queries to ~0), so the gate must be backend-aware.
RECOMMENDED_MIN_SCORE = {
    "sentence-transformers": 0.78,
    "tfidf": 0.10,
}

# Filenames for the persisted tfidf state (saved alongside the index).
_TFIDF_VOCAB_FILE = "tfidf_vocab.json"
_TFIDF_IDF_FILE = "tfidf_idf.npy"

# Drop function words and over-common terms so only discriminative content terms
# drive retrieval. Includes a few romanized-Hindi request words so spoken Hindi
# requests reduce to their content terms (e.g. "samjhaye" -> dropped).
_STOPWORDS = set(
    """a an the of to in on at is are was were be been being and or but if then this
    that these those it its as for with by from into about over under we you they he
    she i me my our your their them his her do does did how what why when where which
    who whom can could should would will shall may might must not no yes some any all
    each more most other than there here also so such only very
    mujhe mein hindi me ko ka ki ke hai ho kya kyon kaise batao samjhao samjhaye samjha
    aur ek""".split()
)
_MAX_DF_RATIO = 0.6  # ignore terms appearing in >60% of chunks


def _tokens(text: str) -> List[str]:
    return [
        w
        for w in re.findall(r"\w+", text.lower(), flags=re.UNICODE)
        if len(w) > 2 and w not in _STOPWORDS
    ]


class Embed:
    def __init__(self, model_name: str = DEFAULT_MODEL_NAME, prefer: str = "auto"):
        """``prefer``: 'auto' | 'sentence-transformers' | 'tfidf'."""
        self.logger = logging.getLogger(__name__)
        self.model_name = model_name
        self.backend: Optional[str] = None
        self._model = None
        self.dim = 0

        # tfidf fitted state
        self._vocab: Dict[str, int] = {}
        self._idf: Optional[np.ndarray] = None

        if prefer in ("auto", "sentence-transformers"):
            try:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(model_name)
                self.dim = self._model.get_sentence_embedding_dimension()
                self.backend = "sentence-transformers"
                self.logger.info("Embed: using sentence-transformers '%s' (dim=%d)", model_name, self.dim)
            except Exception as e:  # noqa: BLE001 - any failure -> fall back
                if prefer == "sentence-transformers":
                    raise
                self.logger.warning("Embed: sentence-transformers unavailable (%s); using tfidf fallback", e)

        if self.backend is None:
            self.backend = "tfidf"
            self.logger.info("Embed: using deterministic TF-IDF fallback (fit required before use)")

    # e5-family models expect "query:" / "passage:" prefixes for asymmetric search.
    def _prefix(self, texts: Sequence[str], kind: str) -> List[str]:
        if self.backend == "sentence-transformers" and "e5" in self.model_name.lower():
            tag = "query: " if kind == "query" else "passage: "
            return [tag + t for t in texts]
        return list(texts)

    # --- build-time fit (tfidf only) -------------------------------------
    def fit(self, corpus_texts: Sequence[str]) -> "Embed":
        """Fit the TF-IDF vocabulary + IDF on the corpus. No-op for neural backends."""
        if self.backend != "tfidf":
            return self
        df: Counter = Counter()
        for t in corpus_texts:
            for tok in set(_tokens(t)):
                df[tok] += 1
        n = max(1, len(corpus_texts))
        # Prune over-common terms so unrelated queries fall to ~0 similarity.
        kept = sorted(tok for tok in df if df[tok] <= _MAX_DF_RATIO * n)
        self._vocab = {tok: i for i, tok in enumerate(kept)}
        idf = np.zeros(len(self._vocab), dtype=np.float32)
        for tok, i in self._vocab.items():
            # smoothed idf
            idf[i] = math.log((1 + n) / (1 + df[tok])) + 1.0
        self._idf = idf
        self.dim = len(self._vocab)
        self.logger.info("TF-IDF fitted: vocab=%d over %d docs", self.dim, n)
        return self

    def _tfidf_vectorize(self, texts: Sequence[str]) -> np.ndarray:
        if self._idf is None or not self._vocab:
            raise RuntimeError(
                "TF-IDF embedder is not fitted/loaded. Call fit() at build time or "
                "load_state() at run time."
            )
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for r, t in enumerate(texts):
            counts = Counter(tok for tok in _tokens(t) if tok in self._vocab)
            if not counts:
                continue
            for tok, c in counts.items():
                j = self._vocab[tok]
                out[r, j] = (1.0 + math.log(c)) * self._idf[j]  # sublinear tf * idf
            norm = np.linalg.norm(out[r])
            if norm > 0:
                out[r] /= norm
        return out

    # --- inference -------------------------------------------------------
    def embed(self, texts: Sequence[str], kind: str = "passage") -> np.ndarray:
        """Embed strings. ``kind`` is 'query' or 'passage'.

        Returns an (n, dim) float32 array of L2-normalized row vectors, so a dot
        product is cosine similarity.
        """
        if isinstance(texts, str):
            texts = [texts]
        if self.backend == "sentence-transformers":
            vecs = self._model.encode(
                self._prefix(texts, kind), normalize_embeddings=True, convert_to_numpy=True
            )
            return vecs.astype(np.float32)
        return self._tfidf_vectorize(texts)

    def embed_one(self, text: str, kind: str = "query") -> np.ndarray:
        return self.embed([text], kind=kind)[0]

    @property
    def recommended_min_score(self) -> float:
        return RECOMMENDED_MIN_SCORE.get(self.backend, 0.30)

    # --- state persistence (tfidf only) ----------------------------------
    def save_state(self, index_dir: str) -> None:
        if self.backend != "tfidf":
            return
        with open(os.path.join(index_dir, _TFIDF_VOCAB_FILE), "w", encoding="utf-8") as f:
            json.dump(self._vocab, f, ensure_ascii=False)
        np.save(os.path.join(index_dir, _TFIDF_IDF_FILE), self._idf)

    def load_state(self, index_dir: str) -> "Embed":
        if self.backend != "tfidf":
            return self
        vocab_path = os.path.join(index_dir, _TFIDF_VOCAB_FILE)
        idf_path = os.path.join(index_dir, _TFIDF_IDF_FILE)
        if not (os.path.exists(vocab_path) and os.path.exists(idf_path)):
            raise FileNotFoundError(
                f"TF-IDF state not found in {index_dir}. Rebuild the index with build_index.py."
            )
        with open(vocab_path, "r", encoding="utf-8") as f:
            self._vocab = json.load(f)
        self._idf = np.load(idf_path)
        self.dim = len(self._vocab)
        return self

    @staticmethod
    def has_state(index_dir: str) -> bool:
        return os.path.exists(os.path.join(index_dir, _TFIDF_VOCAB_FILE))

    # --- pocketinfer model contract --------------------------------------
    @classmethod
    def verify(cls, args):
        try:
            model_name = args.get("model_name", DEFAULT_MODEL_NAME)
            inst = cls(model_name=model_name, prefer=args.get("prefer", "auto"))
            if inst.backend == "sentence-transformers":
                v = inst.embed_one("verification probe", kind="query")
                if v.shape[0] != inst.dim:
                    return False, "Embedding dimension mismatch."
            return True, f"Embedding model available via '{inst.backend}'."
        except Exception as e:  # noqa: BLE001
            return False, str(e)

    @classmethod
    def update(cls, args):
        """Pre-download the model so run time stays fully offline."""
        model_name = args.get("model_name", DEFAULT_MODEL_NAME)
        try:
            from sentence_transformers import SentenceTransformer

            SentenceTransformer(model_name)  # triggers a one-time cached download
            return True, f"Embedding model '{model_name}' cached."
        except Exception as e:  # noqa: BLE001
            logger.warning("Embed.update: could not pre-fetch '%s' (%s)", model_name, e)
            return True, "Embedding model will use TF-IDF fallback (no download needed)."
