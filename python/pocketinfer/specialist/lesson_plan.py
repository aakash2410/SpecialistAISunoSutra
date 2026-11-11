"""Lesson plans for a teacher preparing the next class (user journey 1).

A teacher can say what the class covered last time, then ask for a lesson plan:

    "Yesterday we covered photosynthesis."
    "Give me a lesson plan on respiration in plants for tomorrow's class."

Classes mix learning levels, so by default a plan covers three: students who need more
help, students on track and students who are ahead. A teacher can ask for one level
instead: "a lesson plan on fractions for weak students", or "for students who are ahead".

Only the explanations are written by the answer model:

  1. Recap: a fixed sentence built from the teacher's own words about the last class.
  2. Key ideas: the normal grounded answer to "Explain <topic>", with the same safety
     check, search, gate and answer check as any question. If that is refused, there is
     no plan, because a plan with no checked content would be the model's own lesson.
  3. A simpler explanation for students who need more help: the grounded answer to
     "Explain <topic> in simple words", checked the same way.
  4. Questions quoted from the textbook pages the topic is on. They must read cleanly and
     stand on their own. Questions that ask for a fact check understanding, and questions
     that ask why or how stretch students who are ahead. Without one, a fixed prompt is used.
  5. Close: a fixed sentence.

Textbook activities are not used: on the scanned pages, almost none could be quoted
cleanly. Fixed sentences are translated like any answer.
"""

from __future__ import annotations

import re
from typing import Iterator, List, Optional, Tuple

from .grounding import _content_terms

MAX_QUESTIONS = 2
# Share of a question's words (3 letters or more) that must be in the index vocabulary,
# so scan damage is never read aloud. Clean questions scored 0.9 or more.
CLEAN_SHARE = 0.9
# Pages either side of each top search result that questions may come from.
NEAR_PAGES = 2

_PLAN = re.compile(
    r"\b(lesson[- ]?plans?|teaching plans?|plan (?:a |the |my |our )?(?:lesson|class|period)s?)\b"
    r"|पाठ[- ]?योजना|लेसन प्लान",
    re.IGNORECASE,
)
_WHEN = re.compile(
    r"\b(?:for|in)?\s*(?:the |my |our )?(?:tomorrow'?s?|next|today'?s?|upcoming|coming)\s*"
    r"(?:class|lesson|period|session)?\b"
    r"|\b(?:for|in)\s+(?:class|grade|standard)\s*\d+\b|\b(?:class|grade)\s*\d+\b",
    re.IGNORECASE,
)
_FILLER = set(
    """a an the on about for of to in and so now with me us i we you can could would will
    please give make prepare create write suggest need want help teach teaching plan lesson
    topic chapter new short simple quick some my our what is it it's its should do how
    tell okay ok yes""".split()
)
_COVERED = re.compile(
    r"\b(?:we|i)\s+(?:have\s+|had\s+)?(?:already\s+)?(?:covered|did|done|finished|taught|"
    r"completed|studied|learned|learnt|discussed|read)\s+(?P<what>.+)$",
    re.IGNORECASE,
)
_COVERED_TIME = re.compile(
    r"\s*\b(?:yesterday|today|earlier|so far|last (?:class|time|week|period|lesson)|"
    r"in (?:the )?(?:last|previous) (?:class|lesson|period))\b\s*$",
    re.IGNORECASE,
)
_SENTENCES = re.compile(r"[.;!?।]+")
# Where a remark about the last class stops and the next request starts.
_TURN = re.compile(r",|\b(?:and now|now|so|but|can you|could you|please|give|i need|i want)\b", re.IGNORECASE)

_QUESTION = re.compile(
    r"(?:^|(?<=[.!?:])\s+|\d+[.)]\s*)((?:What|Why|How|Which|Where|When|Who|Name|Define|Explain|"
    r"Describe|Give|State|List)\b[^.?!]{12,160}\?)"
)
# A question that needs its page to make sense: a pointing word, a figure, an activity.
_NEEDS_PAGE = re.compile(
    r"\b(it|its|this|that|these|those|they|them|their|he|she|his|her|above|below|following|"
    r"given|shown|each|figure|fig|table|activity|observe|your)\b",
    re.IGNORECASE,
)
_WORD = re.compile(r"[a-z]{3,}")
# Who the plan is for: "for weak students", "for students who are ahead", "for all levels".
_LEVEL_PHRASE = re.compile(
    r"\b(?:for|with)\s+(?:the\s+|my\s+|our\s+)?(?:[\w-]+\s+){0,3}?(?:students?|learners?|children|kids|pupils)"
    r"(?:\s+who\s+(?:are|need|find)\b(?:\s+(?!on\b|about\b|for\b)[\w-]+){1,3})?"
    r"|\b(?:for|at|across)\s+(?:all\s+|different\s+|mixed\s+|every\s+|three\s+|multiple\s+)?(?:learning\s+)?levels?\b"
    r"|\b(?:for\s+)?(?:a\s+)?mixed[- ](?:ability|level)\s+class\b",
    re.IGNORECASE,
)
_SUPPORT = re.compile(r"\b(weak|weaker|slow|slower|struggling|behind|beginners?|below|low|lower|catch(?:ing)? up|help)\b",
                      re.IGNORECASE)
_STRETCH = re.compile(r"\b(advanced|bright|brighter|ahead|fast|faster|gifted|above|high|higher|top|strong|stronger|clever)\b",
                      re.IGNORECASE)
# Textbook questions that ask for reasons rather than facts.
_THINKING = re.compile(r"^(why|how|explain|describe)\b|\bdo you think\b|\bdiffer|\bcompare", re.IGNORECASE)

LEVELS = ("all", "support", "stretch")
# Every word a level phrase may use. Only a phrase made of these words alone is neutralised
# before the safety check, since the safety model reads "for weak students" as a concern.
_LEVEL_WORDS = set("""for with the my our a students student learners children kids pupils who are need
    needs find more help weak weaker slow slower struggling behind beginner beginners below low lower
    level levels catching catch up advanced bright brighter ahead fast faster gifted above high higher
    top strong stronger clever grade all different mixed every three multiple learning ability class
    it hard""".split())

INTRO = {
    "all": "Here is a lesson plan on {topic}, for a class at different levels.",
    "support": "Here is a lesson plan on {topic}, for students who need more help.",
    "stretch": "Here is a lesson plan on {topic}, for students who are ahead.",
}
RECAP_COVERED = "Start with a short recap of {covered}. Ask the class what they remember."
RECAP_FRESH = "Start by asking the class what they already know about {topic}."
KEY_IDEAS = "Then teach these key ideas from the textbook."
KEY_IDEAS_ALL = "Then teach everyone these key ideas from the textbook."
SIMPLE_ALL = "For students who need more help, explain it more simply."
SIMPLE_ONLY = "Then explain these ideas simply, one at a time."
CHECK_QUOTED = "To check that everyone has understood, ask this question from the textbook."
CHECK_QUOTED_MANY = "Next, ask the class these questions from the textbook."
ASK_FIXED = "Next, ask two or three students to explain {topic} in their own words."
STRETCH_QUOTED = "To stretch students who are ahead, ask this question from the textbook."
STRETCH_QUOTED_MANY = "Next, ask these questions from the textbook, which need more thought."
STRETCH_FIXED = "Ask students who are ahead to explain {topic} to a classmate who needs help."
CLOSE = "To close, ask one student to sum up {topic} in one sentence."
# Kept for older callers and tests: the questions heading in a one-level plan.
ASK_QUOTED = CHECK_QUOTED_MANY
WHICH_TOPIC = "Which topic is the lesson on?"
NOTED = "Noted. Ask me for a lesson plan for the next class when you are ready."


def wants_plan(text: str) -> bool:
    return bool(_PLAN.search(text or ""))


def _sentences(text: str) -> List[str]:
    return [c.strip() for c in _SENTENCES.split(text or "") if c and c.strip()]


_EDGE = "\"',:;.!?()"


def _trim(words: List[str]) -> List[str]:
    kept_the = False
    while words and words[0].lower().strip(_EDGE) in _FILLER | {""}:
        # "the" stays when it leads straight into the topic: "on the Mughal empire".
        kept_the = words[0].lower().strip(_EDGE) == "the"
        words = words[1:]
    while words and words[-1].lower().strip(_EDGE) in _FILLER | {""}:
        words = words[:-1]
    if words and kept_the and words[0].lower() != "the":
        words = ["the", *words]
    if words:
        words = [words[0].lstrip(_EDGE), *words[1:-1], words[-1].rstrip(_EDGE)] if len(words) > 1 \
            else [words[0].strip(_EDGE)]
    return words


def _words(text: str) -> List[str]:
    return _trim(_LEVEL_PHRASE.sub(" ", _WHEN.sub(" ", text or "")).split())


def level_of(text: str) -> str:
    """"support", "stretch", or "all" for a mixed class, from who the plan is for."""
    for m in _LEVEL_PHRASE.finditer(text or ""):
        if _SUPPORT.search(m.group(0)):
            return "support"
        if _STRETCH.search(m.group(0)):
            return "stretch"
    return "all"


def topic_of(text: str) -> Optional[str]:
    """The topic a lesson plan request names, or None if it names none.

    The topic usually follows the request ("a lesson plan on fractions"), and sometimes
    comes before it ("fractions lesson plan").
    """
    for sentence in _sentences(text):
        m = _PLAN.search(sentence)
        if not m:
            continue
        after = _words(sentence[m.end():])
        if after:
            return " ".join(after)
        before = _words(_TURN.split(sentence[:m.start()])[-1])
        if before:
            return " ".join(before)
    return None


def plain_topic(text: str) -> Optional[str]:
    """A topic given on its own, in answer to "Which topic is the lesson on?"."""
    return " ".join(_words(text)) or None


def covered_of(text: str) -> Optional[str]:
    """What the teacher says the class covered, from "We covered fractions yesterday"."""
    if "?" in (text or "") and not wants_plan(text):
        return None
    for sentence in _sentences(text):
        m = _COVERED.search(sentence)
        if m:
            what = _TURN.split(m.group("what"))[0]
            words = _trim(_COVERED_TIME.sub("", what).split())
            if words:
                return " ".join(words)
    return None


def safety_text(text: str) -> str:
    """The request as the safety check should read it: a level phrase made only of level
    words becomes "for students". Any other phrase is left in and checked as said."""
    def plain(m):
        words = re.findall(r"[a-z]+", m.group(0).lower())
        return " for students " if words and all(w in _LEVEL_WORDS for w in words) else m.group(0)
    return _LEVEL_PHRASE.sub(plain, text or "")


def handles(session, text: str) -> bool:
    """Whether this turn belongs to lesson planning rather than a normal question."""
    return session.pending_lesson or wants_plan(text) or covered_of(text) is not None


def _clean_share(text: str, vocabulary) -> float:
    if not vocabulary:
        return 1.0
    words = _WORD.findall(text.lower())
    return sum(w in vocabulary for w in words) / max(1, len(words))


def textbook_questions(engine, topic: str, limit: int = MAX_QUESTIONS) -> List[Tuple[str, str]]:
    """Up to ``limit`` (question, citation) pairs quoted from the pages the topic is on."""
    terms = _content_terms(topic)
    top = engine._search(topic)[:engine.top_k]
    pages = {(p.content_id, p.page): p for p in top}
    found: List[Tuple[str, str]] = []
    for chunk in engine.index.chunks:
        cid, page = chunk.get("content_id"), int(chunk.get("page", 0))
        near = next((p for (c, pg), p in pages.items() if c == cid and abs(page - pg) <= NEAR_PAGES), None)
        if near is None:
            continue
        for m in _QUESTION.finditer(chunk.get("text", "")):
            question = re.sub(r"\s+", " ", m.group(1)).strip()
            if (len(question.split()) < 5 or _NEEDS_PAGE.search(question)
                    or not terms & _content_terms(question)
                    or _clean_share(question, engine.vocabulary) < CLEAN_SHARE
                    or any(question == q for q, _ in found)):
                continue
            citation = f"DIKSHA {cid} · {chunk.get('chapter', '')} · page {page}"
            found.append((question, citation))
            if len(found) >= limit:
                return found
    return found


def _answer(engine, request: str, target_language: str) -> Optional[list]:
    """The checked answer as a list of sentences, or None if it was refused."""
    results = list(engine.stream_answer(request, target_language=target_language))
    if not results or any(r.refused for r in results):
        return None
    return results


def stream_lesson_plan(engine, topic: str, target_language: str = "en",
                       covered: Optional[str] = None, level: str = "all") -> Iterator:
    """Yield the plan sentence by sentence. A refused key-ideas answer is the only thing said."""
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    simple_request = f"Explain {topic} in simple words"
    main = engine.stream_answer(simple_request if level == "support" else f"Explain {topic}",
                                target_language=target_language)
    first = next(main, None)
    if first is None or first.refused:
        if first is not None:
            yield first
        return

    n = [0]

    def say(text: str, citation: Optional[str] = None):
        result = engine.say(text, target_language, index=n[0])
        n[0] += 1
        if citation:
            result.citations = [citation]
        return result

    def spoken(results):
        for r in results:
            if not r.refused:   # a refusal after real sentences cannot happen, but never speak one mid-plan
                r.index = n[0]
                n[0] += 1
                yield r

    yield say(INTRO[level].format(topic=topic))
    yield say(RECAP_COVERED.format(covered=covered) if covered else RECAP_FRESH.format(topic=topic))
    yield say({"all": KEY_IDEAS_ALL, "support": SIMPLE_ONLY, "stretch": KEY_IDEAS}[level])
    yield from spoken([first, *main])

    if level == "all":
        simple = _answer(engine, simple_request, target_language)
        if simple:   # a refused simpler version is left out, since the key ideas stand
            yield say(SIMPLE_ALL)
            yield from spoken(simple)

    found = textbook_questions(engine, topic, limit=8)
    recall = [q for q in found if not _THINKING.search(q[0])]
    thinking = [q for q in found if _THINKING.search(q[0])]
    if level == "all":
        if recall:
            yield say(CHECK_QUOTED)
            yield say(*recall[0])
        else:
            yield say(ASK_FIXED.format(topic=topic))
        if thinking:
            yield say(STRETCH_QUOTED)
            yield say(*thinking[0])
        else:
            yield say(STRETCH_FIXED.format(topic=topic))
    else:
        chosen = (recall if level == "support" else thinking)[:MAX_QUESTIONS]
        if chosen:
            yield say(CHECK_QUOTED_MANY if level == "support" else STRETCH_QUOTED_MANY)
            for question, citation in chosen:
                yield say(question, citation)
        else:
            yield say((ASK_FIXED if level == "support" else STRETCH_FIXED).format(topic=topic))
    yield say(CLOSE.format(topic=topic))


def respond(engine, session, text: str, target_language: str = "en") -> Iterator:
    """Handle a lesson planning turn: note what was covered, ask for a topic, or plan."""
    session.last_rewrite = None
    if engine.safety.check(safety_text(text)):
        # The normal path gives the fixed safety reply, and the caller clears the memory.
        session.pending_lesson = False
        yield from engine.stream_answer(text, target_language=target_language)
        return

    covered = covered_of(text)
    if covered:
        session.covered = covered
    if session.pending_lesson and not wants_plan(text):
        topic = plain_topic(text)
    else:
        topic = topic_of(text)

    if not wants_plan(text) and not session.pending_lesson:
        yield engine.say(NOTED, target_language)
        return
    if not topic:
        session.pending_lesson = True
        yield engine.say(WHICH_TOPIC, target_language)
        return

    session.pending_lesson = False
    # The recap is about the class before this lesson, so it is used for one plan only.
    covered, session.covered = session.covered, None
    spoken = []
    for sentence in stream_lesson_plan(engine, topic, target_language, covered, level_of(text)):
        if not sentence.refused:
            spoken.append(sentence.text_en)
        yield sentence
    if spoken:
        session.remember(f"Give me a lesson plan on {topic}", " ".join(spoken))
