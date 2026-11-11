"""Quick revision of a topic just taught, one class at a time (user journey 2).

In a multi-grade classroom, a teacher finishes a topic with one class, asks the device to
revise it with those students, and turns to the other class:

    "Revise photosynthesis with class 6."
    "Revision of fractions for class 7."
    "I just taught acids to class 8. Revise it with them using different examples."
    "Now do the same for class 7."

Classes 6, 7 and 8 are covered. The class is optional: without one, the last class named
in the session is used, and failing that the whole index.

  1. Intro: a fixed sentence naming the topic and the class.
  2. Explanation: the normal grounded answer to "Explain <topic>", searched in that class's
     passages only (see VectorIndex.search), with the same safety check, gate and answer
     check as any question. It ends with the checked example from village life, so the
     students hear the idea in a new way. If it is refused, that refusal is all that is said.
  3. Recall questions: one or two quoted from the textbook pages the topic is on, for that
     class, with lesson_plan's textbook_questions. Questions that ask why or how, and
     questions about the students themselves, are left out. Without one, a fixed prompt.
  4. Close: a fixed sentence.

The class and topic are kept on the Session, in RAM, so "and for class 8?" revises the same
topic for another class. Nothing about the students is kept, and the memory clears after
3 minutes like the rest. Fixed sentences are translated like any answer.
"""

from __future__ import annotations

import re
from typing import Iterator, List, Optional

from . import lesson_plan, quiz
from .vector_index import GRADE_PHRASE, grade_of

GRADES = (6, 7, 8)
MAX_QUESTIONS = 2

_REVISE = re.compile(
    r"\b(?:revis(?:e|es|ed|ing|ion)|recap(?:s|ped|ping)?|recapitulate)\b|रिवीजन|रिविज़न|रिवाइज़?",
    re.IGNORECASE,
)
# "What is revision?" asks about revision rather than for one.
_ABOUT = re.compile(
    r"^\W*(?:what|why|who|when|where|which|how)\b(?!.*\b(?:me|us|them|class|grade|standard|students?)\b)",
    re.IGNORECASE,
)
# What the teacher says was just taught: "I just taught acids to class 8".
_TAUGHT = re.compile(
    r"\b(?:we|i)\s+(?:(?:have|had|'ve|just|already|now)\s+)*(?:covered|taught|did|done|finished|"
    r"completed|discussed|explained|studied|learned|learnt)\s+(?P<what>[^.;!?।]+)",
    re.IGNORECASE,
)
# "using different examples", "with new examples": how to revise, not what.
_EXAMPLES = re.compile(
    r"\b(?:using|with|through|by giving|giving|give)\s+(?:some\s+|a\s+few\s+)?(?:different|new|other|"
    r"fresh|more|simple|easy|everyday)?\s*examples?\b",
    re.IGNORECASE,
)
_CLAUSE = re.compile(r",|\b(?:and|now|so|but|can you|could you|please)\b", re.IGNORECASE)
_EDGE = "\"',:;.!?()"
_FILLER = lesson_plan._FILLER | set(
    """revise revision recap quick quickly short brief small do let's lets them students student
    class children kids pupils again concept concepts this that these those same just today
    taught science maths mathematics physics chemistry biology subject over through go going
    all whole everyone everybody once""".split()
)
# Words a follow-up such as "now do the same for class 8" may use besides the class.
_FOLLOW_WORDS = set(
    """now do the same for and with what about how again please it that this too also one
    them us class grade standard students children kids pupils okay ok let's lets next
    revise revision recap repeat same thing topic""".split()
)

INTRO = "Here is a quick revision of {topic}."
INTRO_GRADE = "Here is a quick revision of {topic} for class {grade}."
ASK_ONE = "Now ask the class this question from the textbook."
ASK_MANY = "Now ask the class these questions from the textbook."
ASK_FIXED = "Now ask two or three students to explain {topic} in their own words."
CLOSE = "That is the end of the revision of {topic}. Ask the class if anything is still unclear."
WHICH_TOPIC = "Which topic should I revise? For example, say revise photosynthesis with class 7."
OTHER_GRADE = "I can only revise topics for classes 6, 7 and 8."


def wants_revision(text: str) -> bool:
    text = text or ""
    return bool(_REVISE.search(text)) and not _ABOUT.search(text) and not lesson_plan.wants_plan(text)


def _trim(words: List[str]) -> List[str]:
    kept_the = False
    while words and words[0].lower().strip(_EDGE) in _FILLER | {""}:
        # "the" stays when it leads straight into the topic: "recap the food chain".
        kept_the = words[0].lower().strip(_EDGE) == "the"
        words = words[1:]
    while words and words[-1].lower().strip(_EDGE) in _FILLER | {""}:
        words = words[:-1]
    if words and kept_the:
        words = ["the", *words]
    return [w.strip(_EDGE) if i in (0, len(words) - 1) else w for i, w in enumerate(words)]


def _words(text: str) -> List[str]:
    text = GRADE_PHRASE.sub(" ", text or "")
    text = _EXAMPLES.sub(" ", text)
    text = lesson_plan._LEVEL_PHRASE.sub(" ", text)
    return _trim(lesson_plan._WHEN.sub(" ", text).split())


def topic_of(text: str) -> Optional[str]:
    """The topic a revision request names, or None if it names none ("revise it").

    The topic usually follows the request ("revision of fractions"), and sometimes comes
    before it ("a quick photosynthesis recap").
    """
    for sentence in lesson_plan._sentences(text):
        m = _REVISE.search(sentence)
        if not m:
            continue
        after = _words(sentence[m.end():])
        if after:
            return " ".join(after)
        before = _words(_CLAUSE.split(sentence[:m.start()])[-1])
        if before:
            return " ".join(before)
    return None


def taught_of(text: str) -> Optional[str]:
    """What the teacher says was just taught: "I just taught acids to class 8" gives acids.

    The first part that names something is used, so "I taught class 7 science, nutrition
    in plants" gives nutrition in plants.
    """
    m = _TAUGHT.search(text or "")
    if not m:
        return None
    for part in _CLAUSE.split(m.group("what")):
        words = _words(lesson_plan._COVERED_TIME.sub("", part))
        if words:
            return " ".join(words)
    return None


def follow_up_grade(session, text: str) -> Optional[int]:
    """The class in "now do the same for class 8" or "and for class 6?", after a revision."""
    if session.revision_topic is None:
        return None
    grade = grade_of(text)
    if grade is None:
        return None
    rest = re.findall(r"[\w']+", GRADE_PHRASE.sub(" ", text).lower())
    return grade if all(w in _FOLLOW_WORDS for w in rest) else None


def handles(session, text: str) -> bool:
    """Whether this turn asks for a revision rather than a normal question."""
    return wants_revision(text) or follow_up_grade(session, text) is not None


class _ForClass:
    """The engine with every search limited to one class, for textbook_questions."""

    def __init__(self, engine, grade: int):
        self._engine, self._grade = engine, grade

    def _search(self, request: str):
        return self._engine._search(request, grade=self._grade)

    def __getattr__(self, name):
        return getattr(self._engine, name)


def recall_questions(engine, topic: str, grade: Optional[int] = None, limit: int = MAX_QUESTIONS):
    """Up to ``limit`` (question, citation) pairs from the textbook that ask for a fact."""
    found = lesson_plan.textbook_questions(engine if grade is None else _ForClass(engine, grade), topic, limit=8)
    return [q for q in found
            if not lesson_plan._THINKING.search(q[0]) and not quiz._ABOUT_STUDENT.search(q[0])][:limit]


def stream_revision(engine, topic: str, grade: Optional[int] = None,
                    target_language: str = "en") -> Iterator:
    """Yield the revision sentence by sentence. A refused explanation is the only thing said."""
    main = engine.stream_answer(f"Explain {topic}", target_language=target_language, grade=grade)
    first = next(main, None)
    if first is None or first.refused:
        if first is not None:
            yield first
        return

    say = quiz._Say(engine, target_language)
    yield say(INTRO_GRADE.format(topic=topic, grade=grade) if grade else INTRO.format(topic=topic))
    for sentence in (first, *main):
        if not sentence.refused:   # a refusal after real sentences cannot happen, but never speak one
            yield say.number(sentence)

    questions = recall_questions(engine, topic, grade)
    if questions:
        yield say(ASK_ONE if len(questions) == 1 else ASK_MANY)
        for question, citation in questions:
            yield say(question, citation)
    else:
        yield say(ASK_FIXED.format(topic=topic))
    yield say(CLOSE.format(topic=topic))


def respond(engine, session, text: str, target_language: str = "en") -> Iterator:
    """Handle a revision turn: work out the topic and class, then revise."""
    session.last_rewrite = None
    session.pending_lesson = False
    if engine.safety.check(lesson_plan.safety_text(text)):
        # The normal path gives the fixed safety reply, and the caller clears the memory.
        yield from engine.stream_answer(text, target_language=target_language)
        return

    follow = None if wants_revision(text) else follow_up_grade(session, text)
    if follow is not None:
        topic, grade = session.revision_topic, follow
    else:
        grade = grade_of(text) or session.grade
        topic = topic_of(text) or taught_of(text) or session.covered or session.revision_topic
    if grade is not None and grade not in GRADES:
        yield engine.say(OTHER_GRADE, target_language)
        return
    if not topic:
        yield engine.say(WHICH_TOPIC, target_language)
        return

    session.grade, session.revision_topic = grade, topic
    spoken = []
    for sentence in stream_revision(engine, topic, grade, target_language):
        if not sentence.refused:
            spoken.append(sentence.text_en)
        yield sentence
    if spoken:
        session.remember(f"Revise {topic}" + (f" for class {grade}" if grade else ""), " ".join(spoken))
