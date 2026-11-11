"""Build-time: chunk harvested documents into retrieval units.

Each source section is split into overlapping word windows so a passage is small
enough to ground a single answer but large enough to stay coherent. DIKSHA
source metadata (content_id, chapter, section, page, and the grade, subject, board
and licence it is tagged with) rides along on every chunk so every answer can cite
where it came from, and the index can be filtered by class or licence later.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Tuple


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


def _as_list(value) -> List[str]:
    """Grade and subject as lists; older harvests stored a single string."""
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple)) else [value]
    return [str(v).strip() for v in values if v is not None and str(v).strip()]


def dedupe_documents(documents: Iterable[dict]) -> Tuple[List[dict], int]:
    """Keep each (content_id, page) once.

    DIKSHA tags some items with several grades, so harvests of neighbouring classes, or
    a merge of them, can hold the same page twice. The first copy is kept, with the
    grades of every copy. Returns (documents, number removed).
    """
    kept: List[dict] = []
    first = {}
    removed = 0
    for doc in documents:
        cid = doc.get("content_id")
        key = (cid, doc.get("page"))
        if cid in (None, "", "unknown"):
            kept.append(doc)
            continue
        if key not in first:
            first[key] = len(kept)
            kept.append(doc)
            continue
        removed += 1
        original = kept[first[key]]
        grades = _as_list(original.get("grade"))
        extra = [g for g in _as_list(doc.get("grade")) if g not in grades]
        if extra:
            kept[first[key]] = {**original, "grade": grades + extra}
    return kept, removed


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
                "grade": _as_list(doc.get("grade")),
                "subject": _as_list(doc.get("subject")),
                "board": doc.get("board"),
                "chunk_index": i,
            }
            out.append(chunk)
    return out
