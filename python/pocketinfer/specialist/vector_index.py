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

Within a class, books tagged with that class alone come first. Some DIKSHA books are
tagged with several classes, such as a CBSE "Nutrition" book for Classes 6 to 8, and they
won "revise nutrition in plants with class 6" and "now do the same for class 8" alike, so
both classes heard the same page. Shared books are used only when no book of the class's
own scores high enough, or to fill the list when the class has too few passages.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Union

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


# The most words taken from the passage before, to complete a passage's first sentence.
SENTENCE_LEAD = 40


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
        self._own_rows: Dict[int, Optional[np.ndarray]] = {}
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
            rows, own, tagged = [], [], False
            for i, c in enumerate(self.chunks):
                tags = c.get("grade") or []
                tags = {grade_number(t) for t in (tags if isinstance(tags, (list, tuple)) else [tags])}
                if n in tags:
                    rows.append(i)
                    tagged = True
                    if tags == {n}:
                        own.append(i)
                elif not any(tags):
                    rows.append(i)
            usable = tagged and len(rows) < len(self.chunks)
            self._grade_rows[n] = np.asarray(rows, dtype=np.int64) if usable else None
            # Worth searching first only if it leaves some rows out.
            self._own_rows[n] = (np.asarray(own, dtype=np.int64)
                                 if usable and own and len(own) < len(rows) else None)
        return self._grade_rows[n]

    def own_rows_for_grade(self, grade: Union[str, int]) -> Optional[np.ndarray]:
        """The rows of books tagged with this class and no other, or None if that is no
        smaller a set than ``rows_for_grade``."""
        if self.rows_for_grade(grade) is None:
            return None
        return self._own_rows[grade_number(grade)]

    @staticmethod
    def _best(scores: np.ndarray, rows: np.ndarray, top_k: int) -> np.ndarray:
        k = min(top_k, len(rows))
        top = rows[np.argpartition(-scores[rows], k - 1)[:k]]
        return top[np.argsort(-scores[top])]

    def search(self, query_vec: np.ndarray, top_k: int = 4, grade: Union[str, int, None] = None,
               min_score: Optional[float] = None,
               accept: Optional[Callable[[List[Retrieved]], bool]] = None,
               own_first: bool = True) -> List[Retrieved]:
        """The top_k passages nearest the query, best first.

        With ``grade``, only that class's passages (see ``rows_for_grade``), and first those
        from books tagged with that class alone. If the class's own books have no passage
        that scores ``min_score`` or more, its shared books are searched too, and failing
        that every passage, as without a grade. Shared passages fill the list if the class's own books
        have fewer than top_k. ``accept``, if given, must also pass a set of results before
        it is used: the engine passes its retrieval gate, which also wants a key term.
        ``own_first=False`` searches all the class's books together.
        """
        if query_vec.shape[0] != self.dim:
            raise ValueError(
                f"Query dim {query_vec.shape[0]} != index dim {self.dim}. "
                "Did the query use the same embedder the index was built with?"
            )
        # Rows are L2-normalized, so dot product == cosine similarity.
        scores = np.asarray(self.embeddings) @ query_vec.astype(np.float32)
        rows = None if grade is None else self.rows_for_grade(grade)

        def usable(top: np.ndarray) -> Optional[List[Retrieved]]:
            if min_score is not None and float(scores[top[0]]) < min_score:
                return None
            results = self._results(scores, top)
            return results if accept is None or accept(results) else None

        if rows is not None:
            own = self.own_rows_for_grade(grade) if own_first else None
            if own is not None:
                top = self._best(scores, own, top_k)
                if len(top) < top_k:
                    shared = np.setdiff1d(rows, own, assume_unique=True)
                    top = np.concatenate([top, self._best(scores, shared, top_k - len(top))])
                found = usable(top)
                if found is not None:
                    return found
                logger.info("No book for class %s alone can answer, so using shared books too", grade)
            found = usable(self._best(scores, rows, top_k))
            if found is not None:
                return found
            logger.info("No passage for class %s can answer, so searching every class", grade)
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
                    text=self.text_of(int(i)),
                    content_id=c.get("content_id", "unknown"),
                    chapter=c.get("chapter", ""),
                    section=c.get("section", ""),
                    page=int(c.get("page", 0)),
                    source=c.get("source", self.manifest.get("source", "DIKSHA")),
                )
            )
        return results

    def text_of(self, row: int) -> str:
        """A passage's text, starting where its first sentence starts.

        Passages overlap by a few words, so most start in the middle of a sentence. The
        start of that sentence is taken from the passage before it on the same page, when
        it is short. "...called aerobic respiration. If the breakdown occurs without the
        use of oxygen, the respiration is called anaerobic respiration" lost the words
        before "called", and the answer to "What is aerobic respiration?" described
        anaerobic respiration (12 November). Only the text changes: the passage was
        searched, and is scored, as it was embedded.
        """
        c = self.chunks[row]
        text = c["text"]
        overlap = int(self.manifest.get("chunk_overlap") or 0)
        if not overlap or row == 0 or not c.get("chunk_index"):
            return text
        prev = self.chunks[row - 1]
        if (prev.get("content_id"), prev.get("page"), prev.get("chunk_index")) != (
                c.get("content_id"), c.get("page"), c.get("chunk_index") - 1):
            return text
        words, before = text.split(), prev["text"].split()
        if len(before) <= overlap or before[-overlap:] != words[:overlap]:
            return text
        lead = before[:-overlap]
        ends = [i for i, w in enumerate(lead) if w[-1] in ".?!:"]
        start = ends[-1] + 1 if ends else 0
        if not ends or not 0 < len(lead) - start <= SENTENCE_LEAD:
            return text
        return " ".join(lead[start:] + words)

    @property
    def embedder_name(self) -> Optional[str]:
        return self.manifest.get("embedder")
