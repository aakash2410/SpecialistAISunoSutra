"""A second search for questions the first search could not answer.

Search scores sit close together, so the passage that explains a topic can rank just
below ones that only mention it. When the plain top passages give no answer, the
pipeline asks ``rescue`` for another set: the candidates re-ranked with a boost for
passages that contain the question's own phrase, such as "speed of light". The gate
still judges each passage by its original search score, so a rescue changes which
passages are used, never how strict the gate is.
"""

from __future__ import annotations

import json
import os
import re
from typing import Iterable, List, Optional, Sequence, Set

from .grounding import _STOPWORDS
from .vector_index import Retrieved

# Tested on 61 dev questions and 30 off-topic ones (6 November). Boosting every
# question changed 10 answers: 4 better, 3 worse, and one off-topic leak. Used only
# as a rescue, it answers "What is the speed of light?" and leaves the rest alone.
CANDIDATES = 20
PHRASE_BOOST = 0.04
# Lowering instructions, question lists, damaged scans and repeated words changed 51
# of 61 answers, with as many losses as gains. Off until a stronger answer check can
# catch the garbled answers it caused.
INSTRUCTION_WEIGHT = 0.0
QUESTION_WEIGHT = 0.0
GARBLE_WEIGHT = 0.0
REPEAT_WEIGHT = 0.0

_INSTRUCTION = re.compile(
    r"\bthe teacher\b|\bteachers? (?:will|should|may|can|asks?|explains?|shows?|tells?|recalls?|defines?)\b"
    r"|\bstudents (?:will|should|can)\b|\bask (?:the )?(?:students|children|learners)\b|\bCFUs?\b"
    r"|\bhomework\b|\bopen[- ]ended\b|\bfactual questions?\b|\blearning (?:objectives?|outcomes?)\b"
    r"|\bin this concept\b|\bgroup work\b|\bdiscuss(?:es|ion)?\b|\bworksheet\b",
    re.IGNORECASE,
)
_WORD = re.compile(r"[a-z]{3,}")
VOCABULARY_FILE = "vocabulary.json"


def load_vocabulary(index_dir: str) -> Optional[Set[str]]:
    path = os.path.join(index_dir, VOCABULARY_FILE)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return set(json.load(f))


def penalty(text: str, vocabulary: Optional[Set[str]] = None) -> float:
    """How much to lower a passage's score, from what it looks like."""
    words = _WORD.findall(text.lower())
    instructions = min(len(_INSTRUCTION.findall(text)), 4)
    questions = min(text.count("?"), 6)
    garble = 0.0
    if vocabulary and words:
        garble = 1 - sum(w in vocabulary for w in words) / len(words)
    tokens = text.split()
    repeat = sum(a == b for a, b in zip(tokens, tokens[1:])) / len(tokens) if len(tokens) > 1 else 0.0
    return (INSTRUCTION_WEIGHT * instructions + QUESTION_WEIGHT * questions
            + GARBLE_WEIGHT * garble + REPEAT_WEIGHT * repeat)


def _phrases(question: str) -> List[re.Pattern]:
    """Runs of two or more content words from the question, allowing short words between."""
    words = re.findall(r"[^\W\d_]+", question.lower())
    phrases, run = [], []
    for w in words + [""]:
        if w and (len(w) >= 3 and w not in _STOPWORDS):
            run.append(w)
        elif w in ("of", "the", "a", "an", "in", "to") and run:
            continue
        else:
            if len(run) >= 2:
                for a, b in zip(run, run[1:]):
                    phrases.append(re.compile(rf"\b{re.escape(a)}\w{{0,2}}\W+(?:\w+\W+){{0,2}}{re.escape(b)}", re.IGNORECASE))
            run = []
    return phrases


def boost(question: str, text: str) -> float:
    return PHRASE_BOOST if any(p.search(text) for p in _phrases(question)) else 0.0


def rerank(question: str, candidates: Sequence[Retrieved], k: int,
           vocabulary: Optional[Set[str]] = None) -> List[Retrieved]:
    ranked = sorted(candidates, key=lambda p: p.score - penalty(p.text, vocabulary) + boost(question, p.text),
                    reverse=True)
    return list(ranked[:k])


def rescue(question: str, candidates: Sequence[Retrieved], k: int,
           vocabulary: Optional[Set[str]] = None) -> Optional[List[Retrieved]]:
    """Passages to try when the plain top k gave no answer.

    ``candidates`` must be in search order. Returns None when re-ranking brings in no
    new passage containing the question's phrase, since another try would only repeat
    the refusal at the cost of a second answer-model call.
    """
    plain = list(candidates[:k])
    ranked = rerank(question, candidates, k, vocabulary)
    if not any(p not in plain and boost(question, p.text) for p in ranked):
        return None
    return ranked


def write_vocabulary(index_dir: str, words: Iterable[str]) -> None:
    with open(os.path.join(index_dir, VOCABULARY_FILE), "w", encoding="utf-8") as f:
        json.dump(sorted(words), f)
