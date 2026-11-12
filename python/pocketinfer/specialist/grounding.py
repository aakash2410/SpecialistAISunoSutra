"""Grounding + refusal logic.

This is what makes the device "The Specialist" rather than a general chatbot:
the LLM may answer *only* from the retrieved DIKSHA passages, and the device
says so when the content doesn't cover the question.

Two refusal gates:
  1. Retrieval gate  — if no passage both scores above the threshold and shares a
     key term with the request, the corpus doesn't cover it, so we refuse before
     spending an LLM call.
  2. Generation gate — the system prompt forbids outside knowledge and tells the
     LLM to emit a refusal sentinel if the passages are insufficient; we detect
     that and convert it into the standard refusal.

The request is also classified into an intent — explain / simplify / answer —
which shapes the instruction given to the LLM.
"""

from __future__ import annotations

import difflib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set

from .vector_index import Retrieved

# Spoken when the corpus doesn't cover the request. Kept short for TTS.
REFUSAL_TEXT = (
    "I can't answer that from the approved DIKSHA textbook content on this device."
)
# The LLM is told to emit exactly this token if the passages are insufficient.
REFUSAL_SENTINEL = "NO_ANSWER_IN_SOURCE"

# Students in rural India connect with examples from their own lives. After a checked
# explanation, the answer model is asked separately for one example of the facts it kept (see
# pipeline._example). Asking for it in the answer prompt itself made the model state facts
# from its own knowledge, and 4 of 60 answers were refused. A direct answer gets no example:
# on "What is the speed of light?" the model's examples had nothing to do with the question.
EXAMPLE_INTENTS = ("explain", "simplify")
EXAMPLE_SYSTEM = (
    "You give one short example to help students in a village school in India picture a fact. "
    "Use their everyday life, such as fields and crops, cattle, a hand pump or well, cooking on "
    "a chulha, the monsoon or the weekly market, never cities or other countries. The example "
    "must show one of the facts you are given, exactly as stated. Do not add any new fact, "
    "number or name. Reply with one short sentence, under 20 words, that starts with 'For example,'."
)
# Words an example may use freely: things from a village student's everyday life. Any other
# word the facts and question do not already use counts as new content (see pipeline).
EVERYDAY_WORDS = set()  # filled below, once _content_terms is defined


def example_prompt(request: str, facts: List[str]) -> str:
    listed = "\n".join(f"- {f}" for f in facts)
    return f"Question: {request}\n\nFacts:\n{listed}\n\nOne example, in one sentence:"


# When the class's teacher has written down everyday things the children know (see
# teacher_examples.py), the model is shown the few closest to the topic and asked to turn
# the best one into an example. "NONE" is not an example, so the generic prompt above is
# tried next.
# Asked to pick the best fit, the model forced one: "the soap is an acid", "drying red
# chillies is a practical application of photosynthesis". Told to use one only if it really
# is a case of a fact, it dropped both and kept "after the first rain, seeds swell up by
# absorbing water" (8 explanations, 11 November). "Is like" comparisons were nonsense, such
# as pulling a bucket from a well being like a plant taking in oxygen.
TEACHER_EXAMPLE_SYSTEM = (
    "You give one short example to help students in a village school in India picture a fact. "
    "Their teacher has listed things the children see and do every day. Use one of them only if "
    "it really is a case of one of the facts. Say what really happens in it, not what it is like, "
    "and never say anything about it that the facts do not say. Do not add any new fact, number "
    "or name. If none of them is truly a case of a fact, reply NONE. Otherwise reply with one "
    "short sentence, under 20 words, that starts with 'For example,'."
)


def teacher_example_prompt(request: str, facts: List[str], everyday: List[str]) -> str:
    listed = "\n".join(f"- {f}" for f in facts)
    known = "\n".join(f"- {e}" for e in everyday)
    return (f"Question: {request}\n\nFacts:\n{listed}\n\nThe teacher's everyday examples:\n{known}\n\n"
            "One example, in one sentence:")


_EXAMPLE_START = re.compile(r"^\W*(for example|for instance|e\.g\.|imagine|think of|just like|like when)\b",
                            re.IGNORECASE)


def is_example(sentence: str) -> bool:
    return bool(_EXAMPLE_START.match(sentence or ""))


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


# Words that say nothing about a request's topic, including a few romanized-Hindi
# request words so "mujhe photosynthesis samjhaye" reduces to its topic.
_STOPWORDS = set(
    """the and but for with from into about over under then this that these those its
    are was were been being you they she him her his them their your our his who whom
    how what why when where which can could should would will shall may might must not
    yes some any all each more most other than there here also such only very does did
    has had have explain simplify simple describe define tell give meaning mean make
    makes made need needs use used using work works happen happens please
    mujhe mein hindi kya kyon kaise batao samjhao samjhaye samjha aur""".split()
)


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


def _content_terms(text: str) -> Set[str]:
    """The words that carry a request's topic, lowercased with plurals folded."""
    terms = set()
    for word in re.findall(r"[^\W\d_]+", text.lower(), flags=re.UNICODE):
        if len(word) < 3 or word in _STOPWORDS:
            continue
        terms.add(re.sub(r"(?:es|s)$", "", word) if len(word) > 4 else word)
    return terms


def _is_relevant(passage: Retrieved, request_terms: Set[str], min_score: float) -> bool:
    if passage.score < min_score:
        return False
    return bool(request_terms & _content_terms(f"{passage.text} {passage.chapter} {passage.section}"))


# "How do I cook food" asks how to do a task, and one shared word is not enough to answer
# it: "food" is in every nutrition chapter. A request like this is answered only when one
# passage names every word of the task, as a lesson on adding fractions names "add" and
# "fractions". A word of five letters or more counts when the passage has its first five
# letters, so "separation" names "separate". "How do plants make food?" asks how something
# works, so it is not held to this. On 32 first-person how-to questions a textbook answers
# and 24 everyday tasks (11 November), this refused 2 of the 32 ("How do we save forests?" and
# "How can we separate salt from water?", whose passages never say "save" or "separate") and 11
# of the 24, including two the off-topic benchmark had answered.
_HOW_TO = re.compile(r"^\W*(?:how\s+(?:do|can|should|could|would|shall|will|must)\s+(?:i|we)\b|how\s+to\b)",
                     re.IGNORECASE)
# Words a task can leave out: counts, and verbs that do not name the task.
_NOT_TASK = set("one two three four five six seven eight nine ten hundred thousand know understand".split())


def asks_how_to(request: str) -> bool:
    return bool(_HOW_TO.match(request or ""))


def _covers_task(passage: Retrieved, request_terms: Set[str]) -> bool:
    terms = _content_terms(f"{passage.text} {passage.chapter} {passage.section}")
    stems = {t[:5] for t in terms if len(t) >= 5}
    return all(w in terms or (len(w) >= 5 and w[:5] in stems) for w in request_terms - _NOT_TASK)


EVERYDAY_WORDS |= _content_terms(
    """village field fields crop crops farm farmer farmers cow cows buffalo goat cattle well hand pump
    water rain monsoon river pond tree trees leaf leaves sun sunlight sunny day night morning evening
    school student students child children teacher class home house roof wall floor kitchen cooking
    cook chulha stove fire pot pan dal rice roti wheat food meal milk tea glass cup spoon plate bucket
    rope cloth clothes market weekly shop seller buyer money rupee road bicycle mother father family
    friend people hand hands feet stone soil mud seed seeds grass plant plants animal animals bird
    birds insect insects flower flowers fruit fruits vegetable vegetables salt sugar lemon juice
    example instance like just when imagine"""
)


# A request whose topic word the index has never seen is not answered from its other words.
# On 12 November speech recognition heard "heat conduction" as "heat endaction", and the device
# answered from "heat" alone, with lesson aims. Instead it offers a textbook word spelt like it
# that goes with the request's other words ("Did you mean heat conduction?"). With no such word,
# it asks the teacher to say it again if the other words would have been answered, and refuses
# as before if not, so "What is Bitcoin?" is still refused. Every word of 3 letters or more in the index counts as
# known, so a rare textbook term is never blocked, and only request words of 5 letters or more
# are checked.
WORDS_FILE = "words.json"
# Too few words to call any word unknown: the 10-passage demo slice and test indexes.
WORDS_MIN_PASSAGES = 1000
UNKNOWN_MIN_LETTERS = 5
# How close a word must be to be offered (see closest_term). "endaction" and "conduction" score
# 0.74. Lengths may differ by 2 letters at most, so "shakes" is never offered for "shakespeare".
CLOSE_TERM = 0.7
CLOSE_LENGTH = 2
# On average, this share of the passages with an offered word must have each of the request's
# other topic words, and at least 3 must have one. On the Class 6 to 8 index, "heat" is in 66%
# of the passages with "conduction", 27% with "reaction" and 6% with "education"; "cook" is in
# 3% of those with "bigiyan", a scan error offered for "biryani".
MIN_SHARE = 0.35
MIN_TOGETHER = 3
# Request words that need be in no textbook: politeness, and Hindi typed in English letters.
_NOT_TOPIC = set(
    """hello thanks thank okay kindly sorry actually basically really
    baare bataiye bataye batayein batao samjhaiye kripya chahiye kijiye karke hamein humein kaise
    kyunki matlab sakte""".split()
)
_LETTERS = re.compile(r"[a-z]+")
_SUFFIXES = ("es", "s", "ed", "ing", "ly", "er", "est")

# Said instead of an answer. The Hindi is written by hand, as the safety replies are.
UNCLEAR_TEXT = {
    "en": "Sorry, I did not catch one of the words, or it is not in the textbooks on this device. "
          "Please say it again.",
    "hi": "माफ़ कीजिए, मैं एक शब्द ठीक से समझ नहीं पाया, या वह इस डिवाइस की पाठ्यपुस्तकों में नहीं है। "
          "कृपया फिर से बोलिए।",
}
DID_YOU_MEAN_TEXT = {
    "en": "Sorry, I did not catch that. Did you mean {term}? Please ask again.",
    "hi": "माफ़ कीजिए, मैं ठीक से समझ नहीं पाया। क्या आपका मतलब {term} था? कृपया फिर से पूछिए।",
}


def index_words(texts: Iterable[str]) -> Dict[str, int]:
    """How many passages each word of 3 letters or more is in."""
    counts: Dict[str, int] = {}
    for text in texts:
        for w in set(_LETTERS.findall(text.lower())):
            if len(w) >= 3:
                counts[w] = counts.get(w, 0) + 1
    return counts


def write_index_words(index_dir: str, texts: Iterable[str]) -> None:
    with open(os.path.join(index_dir, WORDS_FILE), "w", encoding="utf-8") as f:
        json.dump(dict(sorted(index_words(texts).items())), f, separators=(",", ":"))


def load_index_words(index_dir: str, texts: List[str]) -> Optional[Dict[str, int]]:
    """The index's words and their passage counts, from words.json, or worked out from ``texts``
    for an index built before the file existed. None for a small index, which turns the check off."""
    if len(texts) < WORDS_MIN_PASSAGES:
        return None
    path = os.path.join(index_dir, WORDS_FILE)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return index_words(texts)


def _is_topic_word(word: str) -> bool:
    return len(word) >= 3 and word not in _STOPWORDS and word not in _NOT_TOPIC


def _known(word: str, words) -> bool:
    return word in words or any(word.endswith(s) and word[:-len(s)] in words for s in _SUFFIXES)


def unknown_words(request: str, words) -> List[str]:
    """Topic words of the request, 5 letters or more, that are nowhere in the index."""
    return [w for w in _LETTERS.findall((request or "").lower())
            if len(w) >= UNKNOWN_MIN_LETTERS and _is_topic_word(w) and not _known(w, words)]


def closest_term(word: str, others: Set[str], pool: Iterable[str], texts: Iterable[str],
                 counts: Dict[str, int]) -> Optional[str]:
    """The textbook word the teacher most likely said instead of ``word``, or None.

    ``pool`` is the words that may be offered (words in 8 books or more, so a scanning error is
    never offered), ``others`` the request's other topic words, which say what it is about, and
    ``counts`` the passages each word is in (load_index_words).
    Spelling alone picks badly: "reaction" and "education" are closer to "endaction" than
    "conduction" is, and "smile" is very close to "simile". So a close word is offered only
    when the passages that have it often have the other words too: two thirds of the passages
    with "conduction" mention heat, and a quarter of those with "reaction". With no other
    topic word, nothing is offered.
    """
    others = {o for o in others if o not in _NOT_TOPIC}
    if not others:
        return None
    close = [w for w in difflib.get_close_matches(word, list(pool), n=30, cutoff=CLOSE_TERM)
             if abs(len(w) - len(word)) <= CLOSE_LENGTH and _is_topic_word(w)]
    if not close:
        return None
    hits: dict = {}
    for text in texts:
        text = text.lower()
        found = sum(o in text for o in others)
        if found:
            words = set(_LETTERS.findall(text))
            for w in close:
                if w in words:
                    hits[w] = hits.get(w, 0) + found
    share = {w: n / (counts[w] * len(others)) for w, n in hits.items() if n >= MIN_TOGETHER and counts.get(w)}
    ratio = lambda w: difflib.SequenceMatcher(None, word, w).ratio()
    best = max(share, key=lambda w: (share[w], ratio(w)), default=None)
    return best if best and share[best] >= MIN_SHARE else None


def topic_phrase(request: str, word: str, term: str) -> str:
    """The topic words around ``word`` in the request, with ``term`` in its place, up to 3 words:
    "tell me about the heat endaction" gives "heat conduction"."""
    tokens = _LETTERS.findall(request.lower())
    i = tokens.index(word)
    start, end = i, i + 1
    while start > 0 and i - start < 2 and _is_topic_word(tokens[start - 1]):
        start -= 1
    while end < len(tokens) and end - start < 3 and _is_topic_word(tokens[end]):
        end += 1
    return " ".join(term if j == i else tokens[j] for j in range(start, end))


def build_grounding(
    request: str,
    passages: List[Retrieved],
    min_score: float = DEFAULT_MIN_SCORE,
) -> GroundingResult:
    """Decide whether to answer, and if so build the grounded LLM prompts."""
    intent = classify_intent(request)

    # Gate 1: a passage must score well AND share a key term with the request. e5
    # scores everything high, so score alone cannot separate them: "what is a
    # fraction" outscored "what is conduction" on a corpus with no maths. This also
    # keeps unrelated chapters out of the prompt and the citations.
    request_terms = _content_terms(request)
    relevant = [p for p in passages if _is_relevant(p, request_terms, min_score)]
    if not relevant:
        best = passages[0].score if passages else 0.0
        return GroundingResult(
            refused=True,
            intent=intent,
            passages=passages,
            reason=f"No passage covers this request (best similarity {best:.2f}, "
                   f"threshold {min_score:.2f}).",
        )
    if asks_how_to(request) and not any(_covers_task(p, request_terms) for p in relevant):
        return GroundingResult(
            refused=True,
            intent=intent,
            passages=passages,
            reason="Asks how to do a task, and no passage names all of it.",
        )

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
        "request only from these passages. Rephrase them in simple, clear words, "
        "but use ONLY facts that are actually stated in the passages — do NOT add "
        "your own examples, numbers, or claims, and do NOT use outside knowledge. "
        "If a detail is not in the passages, do not state it. Accuracy matters more "
        "than completeness — a class will hear this. "
        "Keep it short — a few sentences — and suitable to read aloud. "
        "Answer in English; translation happens afterwards.\n\n"
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
