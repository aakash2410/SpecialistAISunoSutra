"""Build-time: chunk harvested documents into retrieval units.

Each source section is split into overlapping word windows so a passage is small
enough to ground a single answer but large enough to stay coherent. DIKSHA
source metadata (content_id, chapter, section, page) rides along on every chunk
so every answer can cite where it came from.
"""

from __future__ import annotations

import re
from typing import Iterable, List


def _words(text: str) -> List[str]:
    return re.findall(r"\S+", text.strip())


def chunk_text(text: str, max_words: int = 120, overlap: int = 25) -> List[str]:
    words = _words(text)
    if len(words) <= max_words:
        return [" ".join(words)] if words else []
    step = max(1, max_words - overlap)
    chunks = []
    for start in range(0, len(words), step):
        window = words[start : start + max_words]
        if not window:
            break
        chunks.append(" ".join(window))
        if start + max_words >= len(words):
            break
    return chunks


def chunk_documents(
    documents: Iterable[dict], max_words: int = 120, overlap: int = 25
) -> List[dict]:
    out: List[dict] = []
    for doc in documents:
        pieces = chunk_text(doc.get("text", ""), max_words=max_words, overlap=overlap)
        for i, piece in enumerate(pieces):
            chunk = {
                "text": piece,
                "content_id": doc.get("content_id", "unknown"),
                "chapter": doc.get("chapter", ""),
                "section": doc.get("section", ""),
                "page": doc.get("page", 0),
                "source": doc.get("source", "https://diksha.gov.in/"),
                "license": doc.get("license"),
                "chunk_index": i,
            }
            out.append(chunk)
    return out
