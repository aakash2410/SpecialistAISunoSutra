"""Build-time pipeline: DIKSHA pull -> chunk -> embed -> index.

Run this ONCE, online, off the device. It bakes the index that the device then
uses fully offline. Re-run it during Suno Sutra's periodic update window to
refresh the corpus, exactly like any other model or asset.

    python ingest/build_index.py \
        --corpus corpus/diksha_g7_science \
        --model intfloat/multilingual-e5-small

Outputs into <corpus>/index/:
    embeddings.npy   float32 (n, dim), L2-normalized
    chunks.jsonl     text + DIKSHA source metadata, one per line
    manifest.json    embedder, dim, counts, provenance
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

import numpy as np

# Use the SAME embedder the device uses (vectors must be comparable), by
# importing it from the bundled pocketinfer package under python/.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "python"))

from ingest.chunk import chunk_documents  # noqa: E402
from ingest.diksha_pull import load_local  # noqa: E402
from pocketinfer.models.embed import Embed, DEFAULT_MODEL_NAME  # noqa: E402

logger = logging.getLogger(__name__)


def build(corpus_dir: str, model_name: str, prefer: str, max_words: int, overlap: int):
    raw_path = os.path.join(corpus_dir, "raw", "chapters.json")
    if not os.path.exists(raw_path):
        raise FileNotFoundError(f"No raw corpus at {raw_path}. Run diksha_pull first.")

    documents = load_local(raw_path)
    chunks = chunk_documents(documents, max_words=max_words, overlap=overlap)
    if not chunks:
        raise ValueError("No chunks produced — is raw/chapters.json empty?")
    logger.info("Chunked %d documents into %d chunks", len(documents), len(chunks))

    embedder = Embed(model_name=model_name, prefer=prefer)
    texts = [c["text"] for c in chunks]
    # The TF-IDF fallback must be fitted on the corpus before embedding.
    embedder.fit(texts)
    logger.info("Embedding %d chunks with '%s' (backend=%s)...", len(texts), model_name, embedder.backend)
    embeddings = embedder.embed(texts, kind="passage").astype(np.float32)

    index_dir = os.path.join(corpus_dir, "index")
    os.makedirs(index_dir, exist_ok=True)

    # Persist any fitted state (TF-IDF vocab/idf) so run time embeds queries identically.
    embedder.save_state(index_dir)
    np.save(os.path.join(index_dir, "embeddings.npy"), embeddings)
    with open(os.path.join(index_dir, "chunks.jsonl"), "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    manifest = {
        "embedder": model_name,
        "embedder_backend": embedder.backend,
        "dim": int(embeddings.shape[1]),
        "num_chunks": len(chunks),
        "num_documents": len(documents),
        "chunk_max_words": max_words,
        "chunk_overlap": overlap,
        "recommended_min_score": embedder.recommended_min_score,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "corpus": os.path.basename(corpus_dir.rstrip("/")),
    }
    with open(os.path.join(index_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    size_mb = embeddings.nbytes / (1024 * 1024)
    logger.info(
        "Baked index -> %s  (%d chunks, dim=%d, embeddings %.2f MB)",
        index_dir, len(chunks), embeddings.shape[1], size_mb,
    )
    if embedder.backend == "tfidf":
        logger.warning(
            "Built with the TF-IDF FALLBACK embedder (sentence-transformers not "
            "installed). Good enough for the offline demo, but install requirements "
            "and rebuild with the neural model before deployment."
        )


def main():
    ap = argparse.ArgumentParser(description="Bake a DIKSHA vector index (build time).")
    ap.add_argument("--corpus", default=os.path.join(
        os.path.dirname(__file__), "..", "corpus", "diksha_g7_science"))
    ap.add_argument("--model", default=DEFAULT_MODEL_NAME)
    ap.add_argument("--prefer", choices=["auto", "onnx", "sentence-transformers", "tfidf"], default="auto")
    ap.add_argument("--max-words", type=int, default=120)
    ap.add_argument("--overlap", type=int, default=25)
    args = ap.parse_args()
    build(os.path.abspath(args.corpus), args.model, args.prefer, args.max_words, args.overlap)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    main()
