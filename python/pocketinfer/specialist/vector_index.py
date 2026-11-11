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

A search can be limited to one class ("Class 6"), using the ``grade`` list each chunk
carries. A chunk with no grade matches any class, since older indexes have no grade field.
Filtering is used rather than boosting: it is simpler, and a passage from another class
can never outrank the class the teacher named. If no chunk is tagged with that class, or
none of its passages scores high enough to answer from, the search uses every passage.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Union

import numpy as np

logger = logging.getLogger(__name__)

_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
             "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12}
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9,
          "x": 10, "xi": 11, "xii": 12}


def _alternatives(words) -> str:
    return "|".join(sorted(words, key=len, reverse=True))


_GRADE_NAME = r"(?:class|grade|standard|std\.?|कक्षा)"
# "class 6", "grade VI", "class six", "कक्षा 6", or "6th class", "sixth standard".
GRADE_PHRASE = re.compile(
    rf"(?<!\w){_GRADE_NAME}\s*[-:]?\s*(?:(\d{{1,2}})(?:st|nd|rd|th)?|({_alternatives(_WORDS)}|"
    rf"{_alternatives(_ORDINALS)})|({_alternatives(_ROMAN)}))(?!\w)"
    rf"|(?<!\w)(?:(\d{{1,2}})(?:st|nd|rd|th)|({_alternatives(_ORDINALS)}))\s+{_GRADE_NAME}(?!\w)",
    re.IGNORECASE,
)


def grade_of(text: str) -> Optional[int]:
    """The class an utterance names, as a number, or None: "revise acids with class 7" gives 7.

    The word class, grade or standard must be there, so "6 examples" names no class. A
    lone "I" is not read as class 1, since "in class I learned" is far more likely.
    """
    for m in GRADE_PHRASE.finditer(text or ""):
        digits, word, roman, digits_before, ordinal_before = m.groups()
        if digits or digits_before:
            n = int(digits or digits_before)
        elif word or ordinal_before:
            w = (word or ordinal_before).lower()
            n = _WORDS.get(w) or _ORDINALS[w]
        elif roman and roman.lower() != "i":
            n = _ROMAN[roman.lower()]
        else:
            continue
        if 1 <= n <= 12:
            return n
    return None


def grade_number(value: Union[str, int, None]) -> Optional[int]:
    """A grade tag as a number: "Class 6", "Grade VI", "class six" or a bare "6"."""
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    return int(text) if text.isdigit() else grade_of(text)


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
        self._grade_rows: Dict[int, Optional[np.ndarray]] = {}
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

    def rows_for_grade(self, grade: Union[str, int]) -> Optional[np.ndarray]:
        """The rows a search for this class may use, or None for every row.

        A chunk matches if it is tagged with the class or has no grade tag at all. None
        when no chunk is tagged with the class, or when every chunk matches anyway.
        """
        n = grade_number(grade)
        if n is None:
            return None
        if n not in self._grade_rows:
            rows, tagged = [], False
            for i, c in enumerate(self.chunks):
                tags = c.get("grade") or []
                tags = [grade_number(t) for t in (tags if isinstance(tags, (list, tuple)) else [tags])]
                if n in tags:
                    rows.append(i)
                    tagged = True
                elif not any(tags):
                    rows.append(i)
            usable = tagged and len(rows) < len(self.chunks)
            self._grade_rows[n] = np.asarray(rows, dtype=np.int64) if usable else None
        return self._grade_rows[n]

    def search(self, query_vec: np.ndarray, top_k: int = 4, grade: Union[str, int, None] = None,
               min_score: Optional[float] = None) -> List[Retrieved]:
        """The top_k passages nearest the query, best first.

        With ``grade``, only that class's passages (see ``rows_for_grade``). If none of them
        scores ``min_score`` or more, every passage is searched instead, as without a grade.
        """
        if query_vec.shape[0] != self.dim:
            raise ValueError(
                f"Query dim {query_vec.shape[0]} != index dim {self.dim}. "
                "Did the query use the same embedder the index was built with?"
            )
        # Rows are L2-normalized, so dot product == cosine similarity.
        scores = np.asarray(self.embeddings) @ query_vec.astype(np.float32)
        rows = None if grade is None else self.rows_for_grade(grade)
        if rows is not None:
            k = min(top_k, len(rows))
            top = rows[np.argpartition(-scores[rows], k - 1)[:k]]
            top = top[np.argsort(-scores[top])]
            if min_score is None or float(scores[top[0]]) >= min_score:
                return self._results(scores, top)
            logger.info("No passage for class %s scores %.2f or more, so searching every class", grade, min_score)
        k = min(top_k, len(self.chunks))
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        return self._results(scores, top)

    def _results(self, scores: np.ndarray, top: np.ndarray) -> List[Retrieved]:
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
