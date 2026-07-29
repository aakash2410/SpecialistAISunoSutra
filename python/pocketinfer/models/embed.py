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
# Recommended refusal thresholds by backend. e5 has a HIGH similarity floor
# (unrelated text still scores ~0.80), so the gate sits at ~0.84 — empirically
# between out-of-corpus tops (~0.76-0.81) and in-corpus tops (~0.88-0.91) on the
# starter corpus. Retune per corpus with a threshold sweep if needed.
RECOMMENDED_MIN_SCORE = {
    "sentence-transformers": 0.84,
    "onnx": 0.84,   # same e5 model, same cosine scale
    "tfidf": 0.10,
}

# Where to find the ONNX model files (a dir with tokenizer.json + a *.onnx).
_ONNX_ENV_DIR = "SPECIALIST_ONNX_DIR"
_ONNX_DIRNAME = "multilingual-e5-small-onnx"

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


# --- ONNX backend -------------------------------------------------------------
# Runs the neural model via onnxruntime (already installed on the Jetson via the
# base image) + a lightweight `tokenizers` — no torch / sklearn / scipy, so it
# sidesteps the Jetson NumPy/ABI dependency conflicts entirely.
def _find_onnx_file(model_dir: str) -> Optional[str]:
    for name in ("model.onnx", "model_quantized.onnx", "model_qint8.onnx"):
        p = os.path.join(model_dir, name)
        if os.path.exists(p):
            return p
    hits = sorted(f for f in os.listdir(model_dir) if f.endswith(".onnx"))
    return os.path.join(model_dir, hits[0]) if hits else None


def _resolve_onnx_dir(explicit: Optional[str] = None) -> Optional[str]:
    here = os.path.dirname(os.path.abspath(__file__))            # .../python/pocketinfer/models
    repo_root = os.path.abspath(os.path.join(here, "..", "..", ".."))
    candidates = [
        explicit,
        os.environ.get(_ONNX_ENV_DIR),
        os.path.join(repo_root, "local", "models", _ONNX_DIRNAME),   # dev checkout
        os.path.join("/opt/specialist/models", _ONNX_DIRNAME),       # device data dir
    ]
    for c in candidates:
        if c and os.path.isdir(c) and os.path.exists(os.path.join(c, "tokenizer.json")) \
                and _find_onnx_file(c):
            return c
    return None


class _OnnxE5:
    """multilingual-e5-small via onnxruntime + tokenizers: mean-pool + L2-normalize."""

    def __init__(self, model_dir: str):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        onnx_path = _find_onnx_file(model_dir)
        if not onnx_path:
            raise FileNotFoundError(f"no .onnx model in {model_dir}")
        self.tokenizer = Tokenizer.from_file(os.path.join(model_dir, "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=512)
        self.tokenizer.enable_padding()
        avail = ort.get_available_providers()
        providers = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in avail]
        # Quiet onnxruntime's info/warning chatter (e.g. the harmless Jetson
        # "GPU device discovery failed /sys/class/drm" line).
        so = ort.SessionOptions()
        so.log_severity_level = 3  # 3 = error and above only
        self.session = ort.InferenceSession(onnx_path, sess_options=so, providers=providers or None)
        self.input_names = {i.name for i in self.session.get_inputs()}
        last_dim = self.session.get_outputs()[0].shape[-1]
        self.dim = last_dim if isinstance(last_dim, int) else 384  # e5-small hidden size
        self.onnx_path = onnx_path

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        enc = self.tokenizer.encode_batch(list(texts))
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        feeds = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.input_names:
            feeds["token_type_ids"] = np.zeros_like(ids)
        last_hidden = self.session.run(None, feeds)[0]           # (B, T, H)
        m = mask[:, :, None].astype(np.float32)
        summed = (last_hidden * m).sum(axis=1)
        counts = np.clip(m.sum(axis=1), 1e-9, None)
        emb = summed / counts                                    # masked mean pool
        emb /= np.clip(np.linalg.norm(emb, axis=1, keepdims=True), 1e-12, None)
        return emb.astype(np.float32)


class Embed:
    def __init__(self, model_name: str = DEFAULT_MODEL_NAME, prefer: str = "auto",
                 onnx_model_dir: Optional[str] = None):
        """``prefer``: 'auto' | 'onnx' | 'sentence-transformers' | 'tfidf'.

        In 'auto' the order is ONNX (robust on the Jetson) -> sentence-transformers
        -> TF-IDF fallback.
        """
        self.logger = logging.getLogger(__name__)
        self.model_name = model_name
        self.backend: Optional[str] = None
        self._model = None
        self._onnx: Optional[_OnnxE5] = None
        self.dim = 0

        # tfidf fitted state
        self._vocab: Dict[str, int] = {}
        self._idf: Optional[np.ndarray] = None

        # 1. ONNX — preferred on device: reuses onnxruntime, no torch/sklearn.
        if prefer in ("auto", "onnx"):
            onnx_dir = _resolve_onnx_dir(onnx_model_dir)
            if onnx_dir:
                try:
                    self._onnx = _OnnxE5(onnx_dir)
                    self.dim = self._onnx.dim
                    self.backend = "onnx"
                    self.logger.info("Embed: using ONNX '%s' from %s (dim=%d)",
                                     model_name, onnx_dir, self.dim)
                except Exception as e:  # noqa: BLE001
                    if prefer == "onnx":
                        raise
                    self.logger.warning("Embed: ONNX backend unavailable (%s)", e)
            elif prefer == "onnx":
                raise RuntimeError(
                    "ONNX model not found. Fetch it with tools/fetch_onnx_embedder.py "
                    f"or set {_ONNX_ENV_DIR} to a dir with tokenizer.json + a .onnx file."
                )

        # 2. sentence-transformers.
        if self.backend is None and prefer in ("auto", "sentence-transformers"):
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

        # 3. TF-IDF fallback.
        if self.backend is None:
            self.backend = "tfidf"
            self.logger.info("Embed: using deterministic TF-IDF fallback (fit required before use)")

    # e5-family models expect "query:" / "passage:" prefixes for asymmetric search.
    def _prefix(self, texts: Sequence[str], kind: str) -> List[str]:
        if self.backend in ("sentence-transformers", "onnx") and "e5" in self.model_name.lower():
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
        if self.backend == "onnx":
            return self._onnx.encode(self._prefix(texts, kind))
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
            if inst.backend in ("sentence-transformers", "onnx"):
                v = inst.embed_one("verification probe", kind="query")
                if v.shape[0] != inst.dim:
                    return False, "Embedding dimension mismatch."
            return True, f"Embedding model available via '{inst.backend}' (dim={inst.dim})."
        except Exception as e:  # noqa: BLE001
            return False, str(e)

    @classmethod
    def update(cls, args):
        """Ensure a model is present so run time stays fully offline."""
        model_name = args.get("model_name", DEFAULT_MODEL_NAME)
        # Preferred on device: the ONNX model files (fetched by tools/fetch_onnx_embedder.py).
        onnx_dir = _resolve_onnx_dir(args.get("onnx_model_dir"))
        if onnx_dir:
            return True, f"ONNX embedding model present at {onnx_dir}."
        try:
            from sentence_transformers import SentenceTransformer

            SentenceTransformer(model_name)  # triggers a one-time cached download
            return True, f"Embedding model '{model_name}' cached."
        except Exception as e:  # noqa: BLE001
            logger.warning("Embed.update: could not pre-fetch '%s' (%s)", model_name, e)
            return True, ("No neural model available; the embedder will use the TF-IDF "
                          "fallback. Run tools/fetch_onnx_embedder.py for production quality.")
