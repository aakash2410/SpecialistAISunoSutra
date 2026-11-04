"""On-device embedding model for retrieval.

``intfloat/multilingual-e5-small`` on onnxruntime (~118M params, normalized 384-d
vectors): small, multilingual, Indic-capable, and runs from local storage. The same
weights embed passages at build time and queries at run time, so vectors stay
comparable. If the model is missing this raises rather than degrading quietly,
because a device with poor retrieval would look like it works.
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_MODEL_NAME = "intfloat/multilingual-e5-small"

# e5 has a HIGH similarity floor (unrelated text still scores ~0.80), so the gate
# sits at ~0.84 — empirically between out-of-corpus tops (~0.76-0.81) and in-corpus
# tops (~0.88-0.91) on the starter corpus. Retune per corpus with a threshold sweep.
RECOMMENDED_MIN_SCORE = 0.84

# Where to find the ONNX model files (a dir with tokenizer.json + a *.onnx).
_ONNX_ENV_DIR = "SPECIALIST_ONNX_DIR"
_ONNX_DIRNAME = "multilingual-e5-small-onnx"


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

    def encode(self, texts: Sequence[str], batch_size: int = 32) -> np.ndarray:
        # Process in small batches: a single run() over thousands of chunks would
        # allocate a (N, T, 384) tensor (GBs) and OOM the device at build time.
        texts = list(texts)
        out: List[np.ndarray] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            enc = self.tokenizer.encode_batch(batch)
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feeds = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.input_names:
                feeds["token_type_ids"] = np.zeros_like(ids)
            last_hidden = self.session.run(None, feeds)[0]           # (b, T, H)
            m = mask[:, :, None].astype(np.float32)
            emb = (last_hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)  # masked mean pool
            emb /= np.clip(np.linalg.norm(emb, axis=1, keepdims=True), 1e-12, None)
            out.append(emb.astype(np.float32))
        return np.vstack(out) if out else np.zeros((0, self.dim), dtype=np.float32)


class Embed:
    def __init__(self, model_name: str = DEFAULT_MODEL_NAME, onnx_model_dir: Optional[str] = None):
        self.model_name = model_name
        onnx_dir = _resolve_onnx_dir(onnx_model_dir)
        if not onnx_dir:
            raise RuntimeError(
                "Embedding model not found. Fetch it with tools/fetch_onnx_embedder.py "
                f"or set {_ONNX_ENV_DIR} to a dir with tokenizer.json + a .onnx file."
            )
        self._onnx = _OnnxE5(onnx_dir)
        self.dim = self._onnx.dim
        logger.info("Embed: using '%s' from %s (dim=%d)", model_name, onnx_dir, self.dim)

    # e5-family models expect "query:" / "passage:" prefixes for asymmetric search.
    def _prefix(self, texts: Sequence[str], kind: str) -> List[str]:
        if "e5" in self.model_name.lower():
            tag = "query: " if kind == "query" else "passage: "
            return [tag + t for t in texts]
        return list(texts)

    def embed(self, texts: Sequence[str], kind: str = "passage") -> np.ndarray:
        """Embed strings. ``kind`` is 'query' or 'passage'.

        Returns an (n, dim) float32 array of L2-normalized row vectors, so a dot
        product is cosine similarity.
        """
        if isinstance(texts, str):
            texts = [texts]
        return self._onnx.encode(self._prefix(texts, kind))

    def embed_one(self, text: str, kind: str = "query") -> np.ndarray:
        return self.embed([text], kind=kind)[0]
