"""Grounding + refusal logic.

This is what makes the device "The Specialist" rather than a general chatbot:
the LLM may answer *only* from the retrieved DIKSHA passages, and the device
says so when the content doesn't cover the question.

Two refusal gates:
  1. Retrieval gate  — if the best passage's similarity is below threshold, the
     corpus almost certainly doesn't cover the request, so we refuse before
     spending an LLM call.
  2. Generation gate — the system prompt forbids outside knowledge and tells the
     LLM to emit a refusal sentinel if the passages are insufficient; we detect
     that and convert it into the standard refusal.

The request is also classified into an intent — explain / simplify / answer —
which shapes the instruction given to the LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

from .vector_index import Retrieved

# Spoken when the corpus doesn't cover the request. Kept short for TTS.
REFUSAL_TEXT = (
    "I can't answer that from the approved DIKSHA textbook content on this device."
)
# The LLM is told to emit exactly this token if the passages are insufficient.
REFUSAL_SENTINEL = "NO_ANSWER_IN_SOURCE"

# Default retrieval gate. With the real e5 embedder, on-topic queries sit well
# above this; the hashing fallback is noisier, so the demo lowers it.
DEFAULT_MIN_SCORE = 0.30

INTENT_PATTERNS = {
    "simplify": [
        r"\bsimplif", r"\beasier\b", r"\bsimple\b", r"\bsaral\b", r"\baasaan\b",
        r"\basaan\b", r"\bsamjh", r"\bchild\b", r"\bkid", r"\bbacch",
    ],
    "explain": [
        r"\bexplain", r"\bwhat is\b", r"\bwhat are\b", r"\bhow does\b", r"\bdescribe\b",
        r"\bsamjha", r"\bbatao\b", r"\bkya hai\b", r"\bkya hota\b",
    ],
}


@dataclass
class GroundingResult:
    refused: bool
    intent: str
    passages: List[Retrieved]
    system_prompt: str = ""
    user_prompt: str = ""
    reason: str = ""
    citations: List[str] = field(default_factory=list)


def classify_intent(request: str) -> str:
    r = request.lower()
    for intent, pats in INTENT_PATTERNS.items():
        if any(re.search(p, r) for p in pats):
            return intent
    return "answer"


def _instruction(intent: str) -> str:
    return {
        "explain": "Explain clearly for a school student, using only the source passages. Keep it to 3-5 short sentences.",
        "simplify": "Explain in very simple words a young student understands, using only the source passages. Use 3-5 short sentences.",
        "answer": "Answer directly, using only the source passages. One to three short sentences.",
    }[intent]


def build_grounding(
    request: str,
    passages: List[Retrieved],
    min_score: float = DEFAULT_MIN_SCORE,
) -> GroundingResult:
    """Decide whether to answer, and if so build the grounded LLM prompts."""
    intent = classify_intent(request)

    # Gate 1: nothing retrieved, or nothing close enough.
    if not passages or passages[0].score < min_score:
        best = passages[0].score if passages else 0.0
        return GroundingResult(
            refused=True,
            intent=intent,
            passages=passages,
            reason=f"Best passage similarity {best:.2f} < threshold {min_score:.2f}.",
        )

    # Only ground on passages that actually clear the relevance threshold — drops
    # low-scoring noise passages from both the prompt (faster) and the citations
    # (a teacher shouldn't see an unrelated chapter cited). The gate above
    # guarantees at least passages[0] qualifies.
    relevant = [p for p in passages if p.score >= min_score]
    numbered = []
    citations = []
    for i, p in enumerate(relevant, 1):
        numbered.append(f"[{i}] ({p.citation()})\n{p.text}")
        citations.append(p.citation())
    sources_block = "\n\n".join(numbered)

    # NOTE: no self-refusal instruction. The retrieval gate above already rejected
    # off-topic requests (neural similarity < threshold), so the passages here ARE
    # relevant — a small model told it "may refuse" over-triggers on messy textbook
    # text and drops good answers. Its only job is to answer from the passages.
    system_prompt = (
        "You are The Specialist, a grounded subject expert for a rural classroom. "
        "The numbered SOURCE passages below were selected as relevant to the "
        "teacher's request, from approved DIKSHA textbook content. Answer the "
        "request only from these passages — do not use outside knowledge and do "
        "not guess. They are relevant, so give a clear, direct answer to the "
        "question; explain it in your own words — do NOT just copy the passage text. "
        "Keep it short — a few sentences — correct, and suitable to read aloud to a "
        "class. Answer in English; translation happens afterwards.\n\n"
        f"SOURCE passages:\n{sources_block}"
    )
    user_prompt = f"Teacher's request: {request}\n\n{_instruction(intent)}"

    return GroundingResult(
        refused=False,
        intent=intent,
        passages=relevant,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        citations=citations,
    )


# Matches the sentinel and natural-language variants ("No answer in source"),
# with underscores or spaces — small models render it inconsistently.
_REFUSAL_RE = re.compile(r"no[_ ]answer[_ ]in[_ ]source", re.IGNORECASE)

# Small models like to open with a meta preamble ("Here's an explanation ...:").
# It's not part of the answer and just wastes MT/TTS, so strip a leading
# "Here's/Here is <...>:" clause.
_PREAMBLE_RE = re.compile(r"^\s*here(?:['’`]s| is)\b[^:\n]{0,80}:\s*", re.IGNORECASE)


def is_refusal_response(llm_text: str) -> bool:
    """True if the LLM refused — i.e. it LEADS with the sentinel (or is empty).

    Anchored to the start so an otherwise-good answer that merely *trails* a
    stray 'No answer in source' note is treated as an answer (and cleaned), not
    a refusal — a real refusal begins with the sentinel.
    """
    t = (llm_text or "").strip()
    if not t:
        return True
    return _REFUSAL_RE.match(t) is not None


_SENTENCE_END_RE = re.compile(r"[.!?।]+[\s\"')\]]*")


def iter_sentences(chunks):
    """Assemble complete sentences from a stream of text chunks (for streaming).

    Yields each sentence as soon as its terminating punctuation arrives, so the
    downstream MT/TTS can start on sentence 1 while the LLM is still producing
    sentence 2. Flushes any trailing remainder at the end.
    """
    buf = ""
    for ch in chunks:
        buf += ch
        while True:
            m = _SENTENCE_END_RE.search(buf)
            if not m:
                break
            cut = m.end()
            sentence = buf[:cut].strip()
            buf = buf[cut:]
            if sentence:
                yield sentence
    if buf.strip():
        yield buf.strip()


def clean_answer(llm_text: str) -> str:
    """Strip a meta preamble and any leaked refusal phrase from a valid answer."""
    cleaned = _PREAMBLE_RE.sub("", llm_text or "")
    cleaned = _REFUSAL_RE.sub("", cleaned)
    cleaned = re.sub(r"\(\s*\)|\[\s*\]", "", cleaned)   # drop emptied ()/[] left behind
    return cleaned.strip()
