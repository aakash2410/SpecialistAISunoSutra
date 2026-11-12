"""Build-time pipeline: DIKSHA pull -> chunk -> embed -> index.

Run this ONCE, online, off the device. It bakes the index that the device then
uses fully offline. Re-run it during Suno Sutra's periodic update window to
refresh the corpus, exactly like any other model or asset.

    python ingest/build_index.py \
        --corpus corpus/diksha_g6_8_science \
        --model intfloat/multilingual-e5-small

Outputs into <corpus>/index/:
    embeddings.npy   float32 (n, dim), L2-normalized
    chunks.jsonl     text + DIKSHA source metadata (grade, subject, board, licence), one per line
    manifest.json    embedder, dim, counts, licence and grade summaries, provenance
"""

from __future__ import annotations

import argparse
import collections
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

from ingest.chunk import chunk_documents, dedupe_documents  # noqa: E402
from ingest.diksha_pull import load_local  # noqa: E402
from ingest.page_quality import (  # noqa: E402
    clean_chunks, drop_non_science_pages, drop_other_classes, english_vocabulary,
)
from pocketinfer.specialist.rerank import write_vocabulary  # noqa: E402
from pocketinfer.models.embed import DEFAULT_MODEL_NAME, RECOMMENDED_MIN_SCORE, Embed  # noqa: E402

logger = logging.getLogger(__name__)


def _tally(chunks, key) -> dict:
    """Chunks per value of ``key``; a chunk tagged with several grades counts for each."""
    counts: collections.Counter = collections.Counter()
    for c in chunks:
        values = c.get(key)
        values = values if isinstance(values, list) else [values]
        counts.update(str(v) if v else "(none)" for v in (values or [None]))
    return dict(counts.most_common())


def _harvest_classes(raw_path: str) -> list:
    """The classes the harvest asked DIKSHA for, from raw/chapters.json."""
    with open(raw_path, encoding="utf-8") as f:
        grades = json.load(f).get("collection", {}).get("gradeLevel") or []
    return grades if isinstance(grades, list) else [grades]


def build(corpus_dir: str, model_name: str, max_words: int, overlap: int):
    raw_path = os.path.join(corpus_dir, "raw", "chapters.json")
    if not os.path.exists(raw_path):
        raise FileNotFoundError(f"No raw corpus at {raw_path}. Run diksha_pull first.")

    documents, repeats = dedupe_documents(load_local(raw_path))
    if repeats:
        logger.info("Left out %d repeated pages (items tagged with several grades)", repeats)
    # Pages left out before chunking, so they are never embedded.
    page_counts: dict = {}
    classes = _harvest_classes(raw_path)
    if classes:
        documents, counts = drop_other_classes(documents, classes)
        page_counts.update(counts)
    documents, counts = drop_non_science_pages(documents)
    page_counts.update(counts)
    if page_counts:
        logger.info("Pages left out: %s", page_counts)
    chunks, removed = clean_chunks(chunk_documents(documents, max_words=max_words, overlap=overlap))
    if not chunks:
        raise ValueError("No chunks produced — is raw/chapters.json empty?")
    logger.info("Left out of the index: %s", removed)
    logger.info("Chunked %d documents into %d chunks", len(documents), len(chunks))

    embedder = Embed(model_name=model_name)
    texts = [c["text"] for c in chunks]
    logger.info("Embedding %d chunks with '%s'...", len(texts), model_name)
    embeddings = embedder.embed(texts, kind="passage").astype(np.float32)

    index_dir = os.path.join(corpus_dir, "index")
    os.makedirs(index_dir, exist_ok=True)

    np.save(os.path.join(index_dir, "embeddings.npy"), embeddings)
    with open(os.path.join(index_dir, "chunks.jsonl"), "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    # Search re-ranking uses this to spot damaged scans.
    write_vocabulary(index_dir, english_vocabulary(chunks))

    manifest = {
        "embedder": model_name,
        "embedder_backend": "onnx",
        "dim": int(embeddings.shape[1]),
        "num_chunks": len(chunks),
        "num_documents": len(documents),
        "removed": {**removed, "repeated pages": repeats} if repeats else removed,
        "pages_removed": page_counts,
        "num_items": len({c["content_id"] for c in chunks}),
        "licenses": _tally(chunks, "license"),
        "items_by_license": _tally(list({c["content_id"]: c for c in chunks}.values()), "license"),
        "grades": _tally(chunks, "grade"),
        "subjects": _tally(chunks, "subject"),
        "chunk_max_words": max_words,
        "chunk_overlap": overlap,
        "recommended_min_score": RECOMMENDED_MIN_SCORE,
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


def main():
    ap = argparse.ArgumentParser(description="Bake a DIKSHA vector index (build time).")
    ap.add_argument("--corpus", default=os.path.join(
        os.path.dirname(__file__), "..", "corpus", "diksha_g7_science"))
    ap.add_argument("--model", default=DEFAULT_MODEL_NAME)
    ap.add_argument("--max-words", type=int, default=120)
    ap.add_argument("--overlap", type=int, default=25)
    args = ap.parse_args()
    build(os.path.abspath(args.corpus), args.model, args.max_words, args.overlap)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    main()
