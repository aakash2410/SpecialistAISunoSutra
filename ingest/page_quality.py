"""Build-time: decide whether a harvested page belongs in the corpus.

Many DIKSHA PDFs have a text layer that does not map to real characters, so a page
can look fine to a person while extraction returns junk. A page that fails
``page_problem`` gets one OCR attempt, and is left out if it still fails.

English only for now, because answers are generated from English passages.

``remove_unwanted`` then drops readable pages a teacher's question should not land
on: lesson plans, front matter and repeats. It matches page headers only, so it is a
rough filter, and anything it misses stays in the corpus.
"""

from __future__ import annotations

import collections
import re
from typing import List, Optional, Tuple

_COMMON_WORDS = set(
    "the of and to in is a for that are it on with as this be by we an or from have "
    "you which at can their will not has they these your its was all what how".split()
)
# Real English runs well above this; OCR run over the wrong script sits near zero.
_MIN_COMMON_WORD_SHARE = 0.10
_MAX_BROKEN_SHARE = 0.01
_MIN_LETTER_SHARE = 0.5
_MIN_LATIN_SHARE = 0.9

_NON_TEACHING_RE = re.compile(
    r"lesson plan|learning outcomes?\b|teaching[- ]learning|instructions? for (?:the )?teachers?"
    r"|how will i adapt|^\s*period \d|copyright|\bisbn\b|textbook development"
    r"|table of contents|^\s*contents\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_HEADER_CHARS = 400
_DUPLICATE_CHARS = 300


def page_problem(text: str) -> Optional[str]:
    """Return why this page is unusable, or None if it is readable English."""
    total = len(text)
    if not total:
        return "empty"
    control = sum(1 for c in text if ord(c) < 32 and c not in "\n\t\r")
    if control / total > _MAX_BROKEN_SHARE:
        return "control characters"
    glyphs = sum(1 for c in text if 0xE000 <= ord(c) <= 0xF8FF or c == "�")
    if glyphs / total > _MAX_BROKEN_SHARE:
        return "private-use glyphs"
    letters = [c for c in text if c.isalpha()]
    if len(letters) / total < _MIN_LETTER_SHARE:
        return "too few letters"
    if sum(c.isascii() for c in letters) / len(letters) < _MIN_LATIN_SHARE:
        return "not English"
    words = re.findall(r"[A-Za-z]+", text.lower())
    if not words or sum(w in _COMMON_WORDS for w in words) / len(words) < _MIN_COMMON_WORD_SHARE:
        return "not English"
    return None


def remove_unwanted(docs: List[dict]) -> Tuple[List[dict], dict]:
    """Drop lesson plans, front matter and repeated pages. Returns (kept, counts)."""
    kept: List[dict] = []
    seen = set()
    counts: collections.Counter = collections.Counter()
    for doc in docs:
        text = doc.get("text", "")
        opening = re.sub(r"\s+", " ", text[:_DUPLICATE_CHARS]).lower()
        if _NON_TEACHING_RE.search(text[:_HEADER_CHARS]):
            counts["dropped: lesson plan or front matter"] += 1
        elif opening in seen:
            counts["dropped: duplicate"] += 1
        else:
            seen.add(opening)
            kept.append(doc)
    return kept, dict(counts)
