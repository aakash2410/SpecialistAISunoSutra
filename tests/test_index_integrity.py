"""The SHIPPED corpus/index must be internally consistent.

These run against whatever index is baked into the repo (TF-IDF on a laptop,
possibly the neural model on the device), checking structure only — no embedding
— so they validate the actual artifact that ships to the device.
"""

import json
import os

import numpy as np
import pytest


def _load(index_dir):
    emb = np.load(os.path.join(index_dir, "embeddings.npy"))
    with open(os.path.join(index_dir, "chunks.jsonl"), encoding="utf-8") as f:
        chunks = [json.loads(l) for l in f if l.strip()]
    with open(os.path.join(index_dir, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    return emb, chunks, manifest


def test_counts_align(shipped_index_dir):
    emb, chunks, manifest = _load(shipped_index_dir)
    assert emb.shape[0] == len(chunks), "one embedding row per chunk"
    assert manifest["num_chunks"] == len(chunks)
    assert manifest["dim"] == emb.shape[1]


def test_embeddings_float32_and_normalized(shipped_index_dir):
    emb, _, _ = _load(shipped_index_dir)
    assert emb.dtype == np.float32
    norms = np.linalg.norm(emb, axis=1)
    # allow all-zero rows in principle, but normalized rows must be unit length
    nonzero = norms > 0
    assert np.allclose(norms[nonzero], 1.0, atol=1e-4)


def test_manifest_has_required_fields(shipped_index_dir):
    _, _, manifest = _load(shipped_index_dir)
    for key in ("embedder", "embedder_backend", "dim", "num_chunks", "recommended_min_score"):
        assert key in manifest, f"manifest missing {key}"


def test_every_chunk_has_citation_metadata(shipped_index_dir):
    """Every answer must be traceable to a DIKSHA source, so every chunk needs it."""
    _, chunks, _ = _load(shipped_index_dir)
    for c in chunks:
        assert c.get("text", "").strip(), "empty chunk text"
        assert c.get("content_id"), "chunk missing content_id"
        assert c.get("chapter"), "chunk missing chapter"


def test_shipped_backend_is_known(shipped_index_dir):
    """The shipped index must record a known embedder backend.

    The repo intentionally ships the zero-dependency TF-IDF index (portable, so
    the demo runs anywhere); the production neural (ONNX) index is built at
    deploy time onto the device. device_io_check.py WARNs if a device is left on
    the TF-IDF fallback.
    """
    _, _, manifest = _load(shipped_index_dir)
    assert manifest.get("embedder_backend") in ("onnx", "sentence-transformers", "tfidf")
