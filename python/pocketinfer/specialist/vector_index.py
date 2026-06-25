"""Local vector index over the baked DIKSHA corpus.

Run-time component: loads the index that the off-device ingestion pipeline baked
into the device image and answers nearest-neighbour queries entirely offline.
No network, no external vector DB — just NumPy over a memory-mapped array, which
keeps RAM and latency within the demo board's budget.

Index layout (one directory per grade+subject slice):
    index/
      embeddings.npy   float32 (n, dim), L2-normalized rows
      chunks.jsonl     one JSON object per row: text + DIKSHA source metadata
      manifest.json    embedder name/dim, counts, build provenance
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class Retrieved:
    score: float
    text: str
    content_id: str
    chapter: str
    section: str
    page: int
    source: str

    def citation(self) -> str:
        return f"DIKSHA {self.content_id} · {self.chapter} · {self.section} (p.{self.page})"


class VectorIndex:
    def __init__(self, index_dir: str):
        self.index_dir = index_dir
        self.manifest = self._load_manifest(index_dir)
        emb_path = os.path.join(index_dir, "embeddings.npy")
        # mmap keeps it off the heap; the array is read-only at run time.
        self.embeddings = np.load(emb_path, mmap_mode="r")
        self.chunks = self._load_chunks(os.path.join(index_dir, "chunks.jsonl"))
        if len(self.chunks) != self.embeddings.shape[0]:
            raise ValueError(
                f"Index corrupt: {len(self.chunks)} chunks vs {self.embeddings.shape[0]} vectors"
            )
        self.dim = int(self.embeddings.shape[1])
        logger.info(
            "VectorIndex loaded: %d chunks, dim=%d, embedder=%s",
            len(self.chunks), self.dim, self.manifest.get("embedder"),
        )

    @staticmethod
    def _load_manifest(index_dir: str) -> dict:
        path = os.path.join(index_dir, "manifest.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    @staticmethod
    def _load_chunks(path: str) -> List[dict]:
        chunks = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    chunks.append(json.loads(line))
        return chunks

    def search(self, query_vec: np.ndarray, top_k: int = 4) -> List[Retrieved]:
        if query_vec.shape[0] != self.dim:
            raise ValueError(
                f"Query dim {query_vec.shape[0]} != index dim {self.dim}. "
                "Did the query use the same embedder the index was built with?"
            )
        # Rows are L2-normalized, so dot product == cosine similarity.
        scores = np.asarray(self.embeddings) @ query_vec.astype(np.float32)
        k = min(top_k, len(self.chunks))
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        results = []
        for i in top:
            c = self.chunks[int(i)]
            results.append(
                Retrieved(
                    score=float(scores[int(i)]),
                    text=c["text"],
                    content_id=c.get("content_id", "unknown"),
                    chapter=c.get("chapter", ""),
                    section=c.get("section", ""),
                    page=int(c.get("page", 0)),
                    source=c.get("source", self.manifest.get("source", "DIKSHA")),
                )
            )
        return results

    @property
    def embedder_name(self) -> Optional[str]:
        return self.manifest.get("embedder")
