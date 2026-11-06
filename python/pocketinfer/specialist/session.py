"""Short-term memory so the device can answer follow-up questions.

A follow-up such as "and in animals?" means nothing on its own, so the answer model first
rewrites it as a complete question using the last few turns. That question then goes
through the same safety check, search, gate and answer check as any other, so memory
helps understand the question but is never a source of facts.

Memory lives only in the running process and is never written to disk. It clears after
3 minutes of silence, when someone says "new topic" or "नया सवाल", when the device
restarts, and after any question the safety check flags, because a shared classroom
device should not carry one person's words into the next person's turn.
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Iterator, Optional

IDLE_SECONDS = 180
MAX_TURNS = 3
# Rewrites longer than this are treated as a failure, and the question is used as asked.
_MAX_REWRITE_WORDS = 40

_RESET = re.compile(
    r"^\W*(new topic|new question|start again|start over|नया सवाल|नया विषय|नया टॉपिक)\W*$",
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

    @staticmethod
    def is_reset(text: str) -> bool:
        return bool(_RESET.match(text or ""))

    def clear(self) -> None:
        self.turns.clear()

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
