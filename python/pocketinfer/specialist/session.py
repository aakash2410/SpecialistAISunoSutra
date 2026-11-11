"""Short-term memory so the device can answer follow-up questions.

A follow-up such as "and in animals?" means nothing on its own, so the answer model first
rewrites it as a complete question using the last few turns. That question then goes
through the same safety check, search, gate and answer check as any other, so memory
helps understand the question but is never a source of facts.

Memory lives only in the running process and is never written to disk. It clears after
3 minutes of silence, when someone says "new topic" or "नया सवाल", when the device
restarts, and after any question the safety check flags, because a shared classroom
device should not carry one person's words into the next person's turn.

A teacher can also say what the last class covered and ask for a lesson plan. Those
turns go to lesson_plan.py, and what was covered is kept with the rest of the memory.
Students can ask for a quiz. Those turns go to quiz.py, and the quiz in progress is kept
with the rest of the memory, so it ends when the memory clears. A teacher can ask for a
quick revision of a topic for a class ("revise acids with class 7"). Those turns go to
revision.py, and the class and topic are kept with the rest of the memory, so "now do the
same for class 8" works until the memory clears.

It also keeps the audio of the last answer, so "repeat that" or "slower" can replay it
without asking again, for a teacher taking notes. That audio clears with the rest of the
memory. The one exception is a safety reply: it is the device's own fixed words, so it can
be repeated, while the question that caused it is forgotten.
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Iterator, List, Optional

IDLE_SECONDS = 180
MAX_TURNS = 3
# Rewrites longer than this are treated as a failure, and the question is used as asked.
_MAX_REWRITE_WORDS = 40

_RESET = re.compile(
    r"^\W*(new topic|new question|start again|start over|नया सवाल|नया विषय|नया टॉपिक)\W*$",
    re.IGNORECASE,
)
# Whole-utterance commands only, so "Why is the sky blue again?" is still a question.
_PLEASE = r"(please\s+|कृपया\s+)?"
_REPEAT = re.compile(
    r"^\W*" + _PLEASE + r"(repeat( that| it| the answer)?|say (that|it) again|tell me again|again|once more|"
    r"one more time|pardon|what did you say|दोबारा( से)?( बोलो| बोलिए| बताओ| बताइए| सुनाओ| सुनाइए)?|"
    r"फिर से( बोलो| बोलिए| बताओ| बताइए| सुनाओ| सुनाइए)?|एक बार और( बोलो| बोलिए| बताओ| बताइए)?|दोहराओ|दोहराइए)"
    r"(\s+please)?\W*$",
    re.IGNORECASE,
)
_SLOWER = re.compile(
    r"^\W*" + _PLEASE + r"(slower|slowly|slow down|more slowly|(say|repeat|speak|tell me) (that |it )?"
    r"(again )?(more )?slow(ly|er)|(धीरे|धीमे)( से| से फिर से| से दोबारा)?( बोलो| बोलिए| बताओ| बताइए| सुनाओ| सुनाइए)?)"
    r"(\s+please)?\W*$",
    re.IGNORECASE,
)

# A question that leans on what came before: a pointing word, or too short to stand alone.
_REFERS = re.compile(
    r"\b(it|its|they|them|their|this|that|these|those|he|she|him|her|there|also|another|"
    r"same|more|else|other|why|what about|how about|and)\b",
    re.IGNORECASE,
)
_SHORT_WORDS = 5

_REWRITE_SYSTEM = (
    "You rewrite a student's follow-up question so it can be understood on its own. "
    "Use the earlier conversation only to fill in what the follow-up refers to. "
    "Do not answer it and do not add facts. Reply with only the rewritten question, in English."
)


@dataclass
class Turn:
    question: str
    answer: str


class Session:
    def __init__(self, idle_seconds: float = IDLE_SECONDS, max_turns: int = MAX_TURNS,
                 clock: Callable[[], float] = time.monotonic):
        self.idle_seconds = idle_seconds
        self.clock = clock
        self.turns: Deque[Turn] = deque(maxlen=max_turns)
        self.last_used = clock()
        self.last_rewrite: Optional[str] = None
        self.last_rewrite_seconds = 0.0
        self.last_audio: List[bytes] = []
        self.last_text: List[str] = []
        # Lesson planning (see lesson_plan.py): what the teacher said the class covered,
        # and whether the device has just asked which topic the lesson is on.
        self.covered: Optional[str] = None
        self.pending_lesson = False
        # A quiz (see quiz.py): the one in progress, and whether the device has just asked
        # which topic it should be on.
        self.quiz = None
        self.pending_quiz = False
        # A revision (see revision.py): the last class named, as a number, and the last
        # topic revised, so "now do the same for class 8" knows what to revise.
        self.grade: Optional[int] = None
        self.revision_topic: Optional[str] = None

    @staticmethod
    def is_reset(text: str) -> bool:
        return bool(_RESET.match(text or ""))

    @staticmethod
    def command(text: str) -> Optional[str]:
        """Return "slower" or "repeat" when the whole utterance asks for one, otherwise None."""
        if _SLOWER.match(text or ""):
            return "slower"
        if _REPEAT.match(text or ""):
            return "repeat"
        return None

    def clear(self) -> None:
        self.turns.clear()
        self.last_audio, self.last_text = [], []
        self.covered, self.pending_lesson = None, False
        self.quiz, self.pending_quiz = None, False
        self.grade, self.revision_topic = None, None

    def keep_last(self, results) -> None:
        """Keep what was just said, so it can be repeated. Call after the answer is spoken."""
        said = [r for r in results if (r.spoken_text or "").strip()]
        if said:
            self.last_text = [r.spoken_text for r in said]
            self.last_audio = [r.audio for r in said if r.audio]
            self.last_used = self.clock()

    def expire_if_idle(self) -> None:
        if self.clock() - self.last_used > self.idle_seconds:
            self.clear()

    def remember(self, question: str, answer: str) -> None:
        self.turns.append(Turn(question, answer))

    def needs_context(self, question: str) -> bool:
        if not self.turns:
            return False
        return len(question.split()) <= _SHORT_WORDS or bool(_REFERS.search(question))

    def standalone(self, question: str, llm: Callable[[str, str], str]) -> str:
        """Rewrite a follow-up as a complete question, or return it unchanged if that fails."""
        history = "\n".join(f"Q: {t.question}\nA: {t.answer}" for t in self.turns)
        try:
            reply = llm(_REWRITE_SYSTEM, f"Earlier conversation:\n{history}\n\nFollow-up: {question}")
        except Exception:  # noqa: BLE001
            return question
        lines = [line.strip() for line in (reply or "").splitlines() if line.strip()]
        rewritten = re.sub(r"^(rewritten question|question)\s*:\s*", "", lines[0], flags=re.IGNORECASE) if lines else ""
        rewritten = rewritten.strip(" \"'")
        if not rewritten or len(rewritten.split()) > _MAX_REWRITE_WORDS or "NO_ANSWER" in rewritten:
            return question
        return rewritten


def ask(engine, session: Session, question: str, target_language: str = "en",
        llm: Optional[Callable[[str, str], str]] = None) -> Iterator:
    """Answer a question in the context of the session, sentence by sentence.

    ``question`` is in English. Pass ``llm`` to allow rewriting follow-ups; without it the
    question is answered as asked. The session's ``last_rewrite`` says what was asked.
    """
    session.expire_if_idle()
    from . import lesson_plan, quiz, revision

    # A quiz in progress comes first, so a student's answer is never taken for a lesson plan.
    for mode in (quiz, revision, lesson_plan):
        if not mode.handles(session, question):
            continue
        flagged = False
        for sentence in mode.respond(engine, session, question, target_language):
            flagged = flagged or (sentence.refused and sentence.reason.startswith("safety:"))
            yield sentence
        if flagged:
            session.clear()
        session.last_used = session.clock()
        return

    asked = question
    started = time.time()
    if llm is not None and session.needs_context(question) and not engine.safety.check(question):
        asked = session.standalone(question, llm)
    session.last_rewrite = asked if asked != question else None
    session.last_rewrite_seconds = time.time() - started

    spoken = []
    flagged = False
    for sentence in engine.stream_answer(asked, target_language=target_language):
        if sentence.refused and sentence.reason.startswith("safety:"):
            flagged = True
        elif not sentence.refused:
            spoken.append(sentence.text_en)
        yield sentence

    if flagged:
        session.clear()
    elif spoken:
        session.remember(asked, " ".join(spoken))
    session.last_used = session.clock()
