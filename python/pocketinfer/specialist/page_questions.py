"""Pick one clean question out of an OCR dump of a textbook page.

A photographed page comes back as one long string of headers, page numbers, figure labels
and several questions at once. Only one question should reach the pipeline, or retrieval
searches for the whole page. This cleans the text, finds the questions, and picks one.
"""

from __future__ import annotations

import re
from typing import List, Optional

_LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "“": '"', "”": '"', "‘": "'", "’": "'", "—": "-", "–": "-"}
_DEV = "ऀ-ॿ"
_NOISE_LINE = re.compile(
    r"^\s*(?:"
    r"[\d०-९]{1,4}"                                              # a bare page number
    r"|(?:page|पृष्ठ)\s*[\d०-९]+.*"
    r"|(?:fig(?:ure)?\.?|चित्र|table|तालिका)\s*[\d०-९]+(?:[.:-][\d०-९]+)*.*"
    r"|(?:chapter|अध्याय|unit|इकाई)\s*[\d०-९ivxIVX]+.{0,40}"
    r"|[^\wऀ-ॿ]*"                                      # nothing but punctuation
    r")\s*$",
    re.IGNORECASE,
)
_ITEM_START = re.compile(r"^\s*(?:(?:Q(?:uestion)?|प्रश्न)\s*\.?\s*)?(?:[\d०-९]{1,3}|[a-hA-H]|[ivxIVX]{1,4})\s*[.):]\s+")
_ENGLISH_ASK = re.compile(
    r"^(?:what|why|how|when|where|who|whom|which|whose|explain|define|describe|name|state|list|give|write|"
    r"differentiate|distinguish|compare|is|are|do|does|did|can)\b", re.IGNORECASE)
_HINDI_ASK = re.compile(r"क्या|क्यों|कैसे|कब|कहाँ|कहां|कौन|किसे|किसको|कितन|समझाइए|समझाइये|बताइए|बताइये|लिखिए|परिभाषित|उदाहरण|अंतर|अन्तर")
_SENTENCE = re.compile(r"(?<=[?।!.])\s+")


def clean_ocr(text: str) -> List[str]:
    """Fix common OCR glitches and return the lines worth reading."""
    for bad, good in _LIGATURES.items():
        text = text.replace(bad, good)
    text = re.sub(rf"(?<=[{_DEV}])\s*\|\s*", "। ", text)                # a danda read as a pipe
    text = re.sub(r"(\w)-\n\s*(\w)", r"\1\2", text)                      # a word split across lines
    if not re.search(r"alumin|\bAl2|\bAl\s*\(|metal", text, re.IGNORECASE):
        text = re.sub(r"\bAl\b", "AI", text)                             # "AI" read as "Al", not aluminium
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return [line for line in lines if line and not _NOISE_LINE.match(line)]


def _is_question(sentence: str) -> bool:
    return sentence.rstrip().endswith("?") or bool(_ENGLISH_ASK.match(sentence)) or bool(_HINDI_ASK.search(sentence))


def _strip_number(line: str) -> str:
    return _ITEM_START.sub("", line, count=1).strip()


def extract_questions_from_ocr(text: str) -> List[str]:
    """The separate questions on a page, in reading order."""
    # Join wrapped lines into numbered items, or into paragraphs when nothing is numbered.
    blocks: List[str] = []
    for line in clean_ocr(text):
        if _ITEM_START.match(line):
            blocks.append(_strip_number(line))
        elif blocks and not re.search(r"[?।!.:]$", blocks[-1]):
            blocks[-1] += " " + line
        else:
            blocks.append(line)
    found: List[str] = []
    for block in blocks:
        for sentence in _SENTENCE.split(block):
            sentence = sentence.strip()
            if len(sentence.split()) >= 2 and _is_question(sentence) and sentence not in found:
                found.append(sentence)
    return found


def select_question(questions: List[str], index: Optional[int] = None) -> Optional[str]:
    """One question: the one asked for, or else the middle one, since the camera is aimed at it."""
    if not questions:
        return None
    if index is not None:
        return questions[index] if 0 <= index < len(questions) else None
    return questions[(len(questions) - 1) // 2]


def question_from_page(text: str, index: Optional[int] = None) -> str:
    """The one question to send on. With none on the page, the cleaned text, kept short."""
    question = select_question(extract_questions_from_ocr(text), index)
    return question if question else " ".join(clean_ocr(text))[:300]
