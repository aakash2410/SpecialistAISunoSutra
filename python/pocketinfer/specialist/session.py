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

"Switch off" (or "बंद हो जाओ") is a command too. The device asks for a spoken yes first, and
remembers that it asked for 20 seconds, so the very next utterance decides it.
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
    r"^\W*(new topic|new question|another topic|another question|different topic|next topic|"
    r"(i have |ask )?(a |an )?(new|another|different) question|"
    r"change (the )?(topic|subject)|switch (the )?(topic|subject)|"
    r"(talk about |ask about |ask )?something else|move on|start again|start over|reset|forget (that|it|this)|"
    r"नया सवाल|नया प्रश्न|नया विषय|नया टॉपिक|दूसरा विषय|दूसरा सवाल|अगला विषय|अगला सवाल|"
    r"(विषय|टॉपिक) बदल(ो|िए|ें|ते हैं)|कुछ और|आगे बढ़(ो|िए|ते हैं))\W*$",
    re.IGNORECASE,
)
# Finishing: the conversation is cleared and the device says a short goodbye.
_CLOSE = re.compile(
    r"^\W*(that'?s (all|it|enough)|that is (all|it|enough)|(we'?re|i'?m|we are|i am) done|done|finished|"
    r"nothing (else|more)|no more( questions)?|stop|end|exit|quit|bye( bye)?|good ?bye|see you|"
    r"thank you( (so|very) much)?|thanks( a lot)?|"
    r"बस( इतना ही| करो| कीजिए)?|इतना ही|धन्यवाद|बहुत धन्यवाद|शुक्रिया|अलविदा|"
    r"(ख़त्म|खत्म) करो|हो गया)\W*$",
    re.IGNORECASE,
)
# Switching the device off. Only the device itself ("yourself", "the device") can follow the
# verb, so "switch off the light" or "Why does the fan turn off?" stay questions. "बंद करो"
# is here, not in _CLOSE: said to the device it means "switch off", and the spoken yes the
# device then asks for makes a mishearing cost one "no".
_DEVICE_EN = r"(yourself|it|the (device|system|machine|specialist)|this (device|machine|system))"
_DEVICE_HI = (r"((डिवाइस|मशीन|सिस्टम|यंत्र|अपने आप|ख़ुद|खुद|इस|इसे|इसको|device|machine|system|apne aap|khud)"
              r"( को| ko)? )?")
_OFF_HI = r"ऑ(फ|फ़)़?"  # ऑफ़ is written with or without the nukta
_POWEROFF = re.compile(
    r"^\W*" + r"(please\s+)?("
    r"(switch|turn|power|shut) (" + _DEVICE_EN + r" )?off( " + _DEVICE_EN + r")?|"
    r"(power|shut) (" + _DEVICE_EN + r" )?down( " + _DEVICE_EN + r")?|"
    + _DEVICE_HI + r"(स्विच|पावर) " + _OFF_HI + r"( (करो|कर दो|कीजिए|कर दीजिए|करिए|करें|हो जाओ|हो जाइए))?|"
    + _DEVICE_HI + r"(switch|power) off( (karo|kar do|kariye|kijiye|kar dijiye|ho jao|ho jaao))?|"
    + _DEVICE_HI + r"बंद (करो|कर दो|कीजिए|कर दीजिए|करिए|करें|कर दें|हो जाओ|हो जाइए|हो जाएँ|हो जाएं)|"
    + _DEVICE_HI + r"band (karo|kar do|kariye|kijiye|kar dijiye|ho jao|ho jaao|ho jaiye))"
    r"(\s+please)?\W*$",
    re.IGNORECASE,
)
# The answer to "Do you want me to switch off?". Only a plain yes, or the command again,
# switches off; anything else keeps the device on.
_YES_WORD = re.compile(r"^(yes|yeah|yep|yup|haan|haa|han|हाँ|हां|हा)$", re.IGNORECASE)
_YES_CORE = re.compile(
    r"^((ji |जी )?(haan|haa|han|हाँ|हां|हा)( ji| जी)?|yep|yup|sure|of course|confirm|do it|go ahead|"
    r"please do|ज़रूर|जरूर|बिल्कुल|कर दो|कर दीजिए)(\s+please)?$",
    re.IGNORECASE,
)
_NO_WORD = re.compile(r"^(no|nope|not|don'?t|do not|nahi|nahin|nahi?n|नहीं|नही|मत|ना|रुको|रुकिए|wait|cancel)$",
                      re.IGNORECASE)
POWEROFF_CONFIRM_SECONDS = 20
# Words said around a command, as in "Okay, new topic now" or "अच्छा, दोबारा बोलो".
_LEADING = re.compile(
    r"^\W*(ok(ay)?|alright|all right|right|so|well|hmm+|um+|uh+|yes|yeah|now|please|"
    r"let'?s|let us|can we|could we|can you|could you|i want to|i would like to|i'd like to|"
    r"अच्छा|ठीक है|ठीक|चलो|अब|जी|हाँ|हां|कृपया)(\s+|$)",
    re.IGNORECASE,
)
# Spaces, not \b, mark word edges: \b splits Hindi words at their vowel signs.
_TRAILING = re.compile(r"(^|\s+)(now|please|then|for now|for today|अब|जी|कृपया)\W*$", re.IGNORECASE)

# What the device says back. Answered in the request's language.
RESET_REPLY = {"en": "Okay, new topic. What would you like to ask?",
               "hi": "ठीक है, नया विषय। आप क्या पूछना चाहेंगे?"}
CLOSE_REPLY = {"en": "Okay. Ask me whenever you have another question.",
               "hi": "ठीक है। जब अगला सवाल हो, पूछ लीजिए।"}
POWEROFF_ASK_REPLY = {"en": "Do you want me to switch off? Say yes to switch off, or no to stay on.",
                      "hi": "क्या मैं बंद हो जाऊँ? बंद करने के लिए हाँ कहिए, या चालू रखने के लिए नहीं कहिए।"}
STAY_ON_REPLY = {"en": "Okay, I'll stay on.",
                 "hi": "ठीक है, मैं चालू रहूँगा।"}
POWEROFF_REPLY = {"en": "Switching off. Wait for the fan to stop before you unplug me.",
                  "hi": "मैं बंद हो रहा हूँ। पंखा रुकने के बाद ही मेरा प्लग निकालिए।"}
POWEROFF_FAILED_REPLY = {"en": "Sorry, I could not switch off. I'll stay on.",
                         "hi": "माफ़ कीजिए, मैं बंद नहीं हो पाया। मैं चालू रहूँगा।"}


def _strip_filler(clause: str) -> str:
    """The clause without the filler words around it ("okay ... now")."""
    previous = None
    while clause != previous:
        previous = clause
        clause = _TRAILING.sub("", _LEADING.sub("", clause)).strip()
    return clause


def _clauses(text: str) -> List[str]:
    return [c.strip() for c in re.split(r"[.,!?।;]+", text or "") if c.strip()]


def parse_command(text: str):
    """What the whole utterance asks for, with any question said after a topic change.

    Returns (kind, rest). kind is "reset", "close", "poweroff", "repeat", "slower", "ack"
    (only filler, such as "okay"), or None for an ordinary question. rest is the question said after
    "new topic", as in "New topic. What is friction?", else "".
    """
    kinds = []
    clauses = _clauses(text)
    for i, clause in enumerate(clauses):
        core = _strip_filler(clause)
        if not core:
            kinds.append("ack")
        elif _RESET.match(core):
            kinds.append("reset")
        elif _CLOSE.match(core):
            kinds.append("close")
        elif _POWEROFF.match(core):
            kinds.append("poweroff")
        elif _SLOWER.match(core):
            kinds.append("slower")
        elif _REPEAT.match(core):
            kinds.append("repeat")
        elif "reset" in kinds:  # a topic change, then the new question
            return "reset", " ".join(clauses[i:])
        else:
            return None, ""
    found = set(kinds) - {"ack"}
    if not found:
        return ("ack", "") if kinds else (None, "")
    if "reset" in found and found <= {"reset", "close"}:
        return "reset", ""
    if found == {"close"}:
        return "close", ""
    if "poweroff" in found and found <= {"poweroff", "close"}:  # "That's all, switch off"
        return "poweroff", ""
    if len(found) == 1:
        return found.pop(), ""
    return None, ""


def _words(text: str) -> List[str]:
    return re.findall(r"[\w\u0900-\u097F']+", (text or "").lower())


def is_confirmation(text: str) -> bool:
    """True when the utterance is a plain yes ("yes please", "हाँ जी") or asks to switch off
    again ("ok switch off", "हाँ, बंद करो"). A no, a question or anything else is False."""
    if parse_command(text)[0] == "poweroff":
        return True
    if any(_NO_WORD.match(w) for w in _words(text)):
        return False
    clauses = _clauses(text)
    said_yes = False
    for clause in clauses:
        core = _strip_filler(clause)
        if core and not _YES_CORE.match(core):
            return False
        said_yes = said_yes or bool(core) or any(_YES_WORD.match(w) for w in _words(clause))
    return said_yes


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
        # When the device asked "Do you want me to switch off?", or None. The next
        # utterance answers it (see answer_poweroff).
        self.pending_poweroff: Optional[float] = None

    @staticmethod
    def is_reset(text: str) -> bool:
        return parse_command(text)[0] == "reset"

    @staticmethod
    def command(text: str) -> Optional[str]:
        """Return "slower" or "repeat" when the whole utterance asks for one, otherwise None."""
        kind = parse_command(text)[0]
        return kind if kind in ("slower", "repeat") else None

    @property
    def mid_activity(self) -> bool:
        """A quiz or a lesson plan is waiting for the teacher's reply, so "yes" or "okay"
        is an answer to it, not small talk."""
        return bool(self.quiz or self.pending_quiz or self.pending_lesson)

    def clear(self) -> None:
        self.turns.clear()
        self.last_audio, self.last_text = [], []
        self.covered, self.pending_lesson = None, False
        self.quiz, self.pending_quiz = None, False
        self.grade, self.revision_topic = None, None
        self.pending_poweroff = None

    def ask_poweroff(self) -> None:
        """The device has just asked whether to switch off. Call after the question is spoken."""
        self.pending_poweroff = self.clock()

    def answer_poweroff(self, text: str, now: Optional[float] = None) -> Optional[str]:
        """Decide a pending switch-off with the utterance that follows it.

        Returns "confirm" for a yes within POWEROFF_CONFIRM_SECONDS, "cancel" for anything
        else (that utterance is not answered as a question), or None when nothing is pending.
        ``now`` is when the reply started (when recording began), defaulting to the clock.
        Once the time has passed, a late yes is told the device is staying on, and anything
        else is a new turn as usual: a question is answered and "switch off" asks again.
        """
        if self.pending_poweroff is None:
            return None
        asked, self.pending_poweroff = self.pending_poweroff, None
        now = self.clock() if now is None else now
        if now - asked <= POWEROFF_CONFIRM_SECONDS:
            return "confirm" if is_confirmation(text) else "cancel"
        if parse_command(text)[0] != "poweroff" and is_confirmation(text):
            return "cancel"
        return None

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
