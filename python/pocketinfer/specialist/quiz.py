"""Quizzes for a small group of students revising a topic.

Students ask the device to quiz them, then answer each question out loud:

    "Quiz me on photosynthesis."
    "Start a quiz on acids."
    "Give us a quiz about the food chain."

If no topic is named, the device asks for one and takes the next turn as the topic.

  1. Questions: quoted from the textbook pages the topic is on, with lesson_plan's
     textbook_questions, so they read cleanly and stand on their own. Questions about the
     students themselves ("What do I know about light?") are left out, since no answer to
     them can be checked. The answer model never writes a question. If there is no
     such question, the device says so and no quiz starts.
  2. Each answer is judged against the textbook passages for the question, and if they
     do not support it, against the first sentence of the device's own grounded, checked
     answer to the question (see judge).
  3. A right answer gets a fixed "That's right." Any other answer gets a fixed
     "Not quite." and then the rest of that grounded answer, so the feedback always comes
     from the textbook and is made only once. If that answer is refused, the device says
     it cannot check this one and moves on.
  4. Then the next question, until the last one, which gets a fixed closing sentence.
     "Stop the quiz" or "बस" ends it early.

No score is kept or spoken, and nothing about the students is kept beyond the session,
which lives in RAM. Every answer goes through the safety check like any question, and a
flagged one gets the normal safety reply and ends the quiz. Fixed sentences are
translated like any answer, and "repeat that" replays the last question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Tuple

from ..models.verify import SUPPORT_THRESHOLD
from . import lesson_plan
from .grounding import _STOPWORDS

MAX_QUESTIONS = 6

_QUIZ = re.compile(r"\bquiz(?:zes|zed)?\b|\btest (?:me|us)\b|क्विज़?", re.IGNORECASE)
# "What is a quiz?" asks about quizzes rather than for one.
_ABOUT_QUIZZES = re.compile(r"^\W*(?:what|why|who|when|where|which)\b(?!.*\b(?:me|us)\b)", re.IGNORECASE)
_STOP = re.compile(
    r"\b(?:stop|end|finish|quit|exit|cancel|close)\s+(?:the\s+|this\s+|our\s+|my\s+)?(?:quiz|test)\b"
    r"|^\W*(?:please\s+)?(?:stop|end|finish|quit|enough|that'?s enough|that is enough|"
    r"that'?s all|that is all|that'?s it|we'?re done|we are done|no more(?: questions)?|"
    r"bas|बस(?: करो| करें| कीजिए)?)"
    r"(?:\s+now)?(?:\s+please)?\W*$",
    re.IGNORECASE,
)
# Words around a topic that are part of the request, not the topic.
_FILLER = lesson_plan._FILLER | set(
    """start begin have take let let's lets do play run ask set quiz quizzes test questions
    question few little small round time from""".split()
)
_EDGE = "\"',:;.!?()"
# Answers that say the student does not know carry no claim to judge.
_NO_IDEA = re.compile(r"\b(?:don'?t know|do not know|no idea|not sure|pass|skip)\b", re.IGNORECASE)
# Textbook questions about the student rather than the topic have no answer to check:
# "What do I know about light?", "Where have you seen...?", "What did you find most interesting...?".
_ABOUT_STUDENT = re.compile(
    r"\b(?:I|my|me)\b|\bhave you\b|\byou\s+(?:find|found|like|liked|feel|felt|enjoy|enjoyed|remember|"
    r"learn|learned|learnt|want|see|saw|seen|observe|observed|face|faced)\b|\binteresting\b|\bsurpris",
    re.IGNORECASE,
)
# Passage sentences: split at full stops and at the bullets scanned teacher's books use.
_SPLIT = re.compile(r"(?<=[.!?;])\s+|\s*[©●▪•\uf0b7➢]\s*")
# Multiple choice options: "(a) 22 April (b) 24 March". A sentence listing them supports any
# option. Scanning often damages the letters, so many brackets count too.
_OPTION = re.compile(r"[({]\s*[a-d]\s*\)|\b[a-d]\)", re.IGNORECASE)
_BRACKET = re.compile(r"[({]")
# Teacher's book notes say what a lesson covers, not what is true: "Describes the role of
# water and sunlight in photosynthesis."
_LESSON_NOTE = re.compile(
    r"^\W*(?:describes|identifies|understands|explains|lists|recogni[sz]es|differentiates|classifies|"
    r"relates|appreciates|demonstrates|learning objectives?|prior concepts?|concepts? covered|tlm|"
    r"teacher resources?|period\s+\d)\b", re.IGNORECASE)
# What goes in and what comes out: "Carbon dioxide and water ... are converted into glucose
# and oxygen", or "Photosynthesis needs carbon dioxide and water". The verifier cannot tell
# the two sides apart, so a question that asks for one side needs the answer on that side.
_TURNS_INTO = re.compile(
    r"\b(?:converted|changed|turned|broken down|made)\s+into\b|\bto (?:form|make|produce|give|release)\b"
    r"|\b(?:forms|produces|releases|gives out|yields)\b", re.IGNORECASE)
_OUT_WORDS = re.compile(r"\b(?:products?|produced|formed|released?|given out|gives? out|end result)\b",
                        re.IGNORECASE)
_IN_WORDS = re.compile(r"\b(?:raw materials?|requirements?|required|requires?|needs?|needed|reactants?|"
                       r"takes? in|taken in|use up|uses up)\b", re.IGNORECASE)
# Words that link an answer to its question, not part of what it claims.
_LINKS = {"because", "since", "out", "get", "gets", "got", "make", "makes", "made", "through", "due",
          "like", "called", "known", "help", "helps"}
# Numbers are compared as digits: "22nd" and "twenty second" are both 22, and 37°C is 37 degrees Celsius.
_UNIT_WORDS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
               "fifteen sixteen seventeen eighteen nineteen").split()
_ORDINAL_WORDS = ("first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth "
                  "thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth").split()
_SMALL = {**{w: i for i, w in enumerate(_UNIT_WORDS)}, **{w: i + 1 for i, w in enumerate(_ORDINAL_WORDS)},
          "twentieth": 20, "thirtieth": 30}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_DIGIT_WORDS = "|".join(w for w, n in _SMALL.items() if 0 < n < 10)
_NUMBER_WORDS = re.compile(
    rf"\b(?:({'|'.join(_TENS)})(?:[\s-]+({_DIGIT_WORDS}))?|({'|'.join(sorted(_SMALL, key=len, reverse=True))}))\b",
    re.IGNORECASE)
_ORDINAL = re.compile(r"\b(\d+)(?:st|nd|rd|th)\b", re.IGNORECASE)
_CELSIUS = re.compile(r"(\d)\s*[°⁰º]\s*C\b")
_NUMBER = re.compile(r"\d+")
# Bars a passage sentence must clear to be used as evidence, chosen on the calibration pairs.
MAX_SENTENCE_WORDS = 60
MIN_CLEAN_SHARE = 0.7
MIN_ANSWER_SHARE = 0.5
# A word of this many letters or more also matches a longer form of it: "reflect" and "reflection".
MIN_PREFIX = 5

WHICH_TOPIC = "Which topic should the quiz be on?"
NO_QUESTIONS = "I could not find any textbook questions on {topic} to ask, so there is no quiz."
INTRO = "Here is a quiz on {topic}, with questions from the textbook. Say stop the quiz to finish early."
FIRST = "First question."
NEXT = "Next question."
RIGHT = "That's right."
NOT_QUITE = "Not quite. Here is what the textbook says."
CANT_CHECK = "I can't check that answer against the textbook, so let's move on."
LAST = "That was the last question on {topic}. Well done, everyone."
STOPPED = "Okay, the quiz is over."


@dataclass
class Quiz:
    """A running quiz: kept on the Session, in RAM, and cleared with it."""
    topic: str
    current: Tuple[str, str]                                  # (question, citation) being asked
    remaining: List[Tuple[str, str]] = field(default_factory=list)


def wants_quiz(text: str) -> bool:
    text = text or ""
    return bool(_QUIZ.search(text)) and not is_stop(text) and not _ABOUT_QUIZZES.search(text) \
        and not lesson_plan.wants_plan(text)


def is_stop(text: str) -> bool:
    return bool(_STOP.search(text or ""))


def _trim(words: List[str]) -> List[str]:
    kept_the = False
    while words and words[0].lower().strip(_EDGE) in _FILLER | {""}:
        # "the" stays when it leads straight into the topic: "about the food chain".
        kept_the = words[0].lower().strip(_EDGE) == "the"
        words = words[1:]
    while words and words[-1].lower().strip(_EDGE) in _FILLER | {""}:
        words = words[:-1]
    if words and kept_the:
        words = ["the", *words]
    return [w.strip(_EDGE) if i in (0, len(words) - 1) else w for i, w in enumerate(words)]


def _words(text: str) -> List[str]:
    return _trim(lesson_plan._WHEN.sub(" ", text or "").split())


def topic_of(text: str) -> Optional[str]:
    """The topic a quiz request names, or None if it names none.

    The topic usually follows the request ("quiz me on acids"), and sometimes comes
    before it ("a photosynthesis quiz").
    """
    m = _QUIZ.search(text or "")
    if not m:
        return None
    after = _words(text[m.end():])
    if after:
        return " ".join(after)
    before = _words(re.split(r"[,.;!?]", text[:m.start()])[-1])
    return " ".join(before) or None


def plain_topic(text: str) -> Optional[str]:
    """A topic given on its own, in answer to "Which topic should the quiz be on?"."""
    return " ".join(_words(text)) or None


def handles(session, text: str) -> bool:
    """Whether this turn belongs to a quiz rather than a normal question."""
    if session.quiz is not None or session.pending_quiz:
        return True
    return wants_quiz(text) or is_stop(text) and bool(_QUIZ.search(text or ""))


# Question to statement. "What are the products of X?" + "A" -> "The products of X are A."
_AUX = r"is|are|was|were"
_DO = r"do|does|did"
_MODAL = r"can|could|should|must|will|would"
_STATEMENTS = [
    # "What is X called?" -> "X is called A."
    (re.compile(rf"^(?:what|which)\s+({_AUX})\s+(.+?)\s+(called|known as)$", re.I), "{2} {1} {3} {a}"),
    # "What are the products of X?" -> "The products of X are A."
    (re.compile(rf"^(?:what|which|who)\s+({_AUX})\s+(.+)$", re.I), "{2} {1} {a}"),
    # "Which gas do plants take in?" -> "The gas that plants take in is A."
    (re.compile(rf"^(?:which|what)\s+(\w+)\s+(?:{_DO})\s+(.+)$", re.I), "the {1} that {2} is {a}"),
    # "Which gas is given out?" -> "A is given out."
    (re.compile(r"^which\s+\w+\s+(.+)$", re.I), "{a} {1}"),
    # "Who uses energy from the sun?" -> "A uses energy from the sun."
    (re.compile(r"^(?:who|what)\s+(\w+(?:s|ed))\b(.*)$", re.I), "{a} {1}{2}"),
    # "When is World Water Day celebrated?" -> "World Water Day is celebrated A."
    (re.compile(rf"^(?:when|where)\s+({_AUX})\s+(.+?)\s+(\w+(?:ed|en))$", re.I), "{2} {1} {3} {a}"),
    (re.compile(rf"^(?:when|where)\s+({_AUX})\s+(.+)$", re.I), "{2} {1} {a}"),
    # "Where does most of our food come from?" -> "Most of our food come from A."
    (re.compile(rf"^(?:when|where)\s+(?:{_DO})\s+(.+)$", re.I), "{1} {a}"),
    # "Why is X celebrated?" -> "X is celebrated because A."
    (re.compile(rf"^why\s+({_AUX})\s+(.+?)\s+(\w+(?:ed|en))$", re.I), "{2} {1} {3} because {a}"),
    # "Why do plants need sunlight?" -> "Plants need sunlight because A."
    (re.compile(rf"^why\s+(?:{_DO})\s+(.+)$", re.I), "{1} because {a}"),
    # "Why can we not see light?" -> "We can not see light because A."
    (re.compile(rf"^why\s+({_MODAL})\s+(\w+)\s+(.+)$", re.I), "{2} {1} {3} because {a}"),
    # "Why sunlight is necessary", once "do you think" is gone -> "Sunlight is necessary because A."
    (re.compile(rf"^why\s+(?!(?:{_AUX}|{_DO}|{_MODAL})\b)(.+)$", re.I), "{1} because {a}"),
    # "Name the gas given out." / "Define photosynthesis." -> "The gas given out is A."
    (re.compile(r"^(?:name|state|give|list)\s+(.+)$", re.I), "{1} is {a}"),
    (re.compile(r"^define\s+(.+)$", re.I), "{1} is {a}"),
]


def statement(question: str, answer: str) -> str:
    """The student's answer as one statement that a passage can support or not.

    A short answer such as "oxygen" cannot be checked on its own, and the question with
    the answer stuck on the end reads like the textbook's own question list, which often
    sits next to the wrong answer. So common question shapes are turned into a
    statement. Other shapes ("How does...?") are checked as "The answer to <question>
    is: <answer>", since answers to them are usually full sentences.
    """
    q = re.sub(r"\s+", " ", question).strip().rstrip("?").strip()
    q = re.sub(r"\b(?:do|can) you (?:think|say|tell)\s+", "", q, flags=re.I)
    a = re.sub(r"\s+", " ", answer).strip().rstrip(".!?").strip()
    a = re.sub(r"^because\s+", "", a, flags=re.I)
    for pattern, shape in _STATEMENTS:
        m = pattern.match(q)
        if m:
            text = shape.format(None, *m.groups(), a=a)
            return text[0].upper() + text[1:] + "."
    return f'The answer to "{question.strip()}" is: {answer.strip()}'


def _digits(text: str) -> str:
    """Numbers as digits: "twenty second March" and "22nd March" both become "22 March"."""
    def spelt(m):
        if m.group(1):
            return str(_TENS[m.group(1).lower()] + (_SMALL[m.group(2).lower()] if m.group(2) else 0))
        return str(_SMALL[m.group(3).lower()])
    text = _NUMBER_WORDS.sub(spelt, text or "")
    return _CELSIUS.sub(r"\1 degrees Celsius", _ORDINAL.sub(r"\1", text))


def _stem(word: str) -> str:
    """One form for a word's plural, past and -ing forms and British spellings:
    "substances" and "substance", "carries" and "carry", "haemoglobin" and "hemoglobin"."""
    if len(word) > 5:
        word = word.replace("ae", "e").replace("oe", "e")
    if word.endswith("ies") and len(word) > 4:
        word = word[:-3] + "y"
    elif word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        word = word[:-1]
    if word.endswith("ed") and len(word) > 5:
        word = word[:-2]
    elif word.endswith("ing") and len(word) > 6:
        word = word[:-3]
    return word[:-1] if word.endswith("e") and len(word) > 4 else word


def _terms(text: str) -> set:
    """The content words of a text, stemmed, and its numbers as digits."""
    text = _digits(text).lower()
    words = {_stem(w) for w in re.findall(r"[^\W\d_]+", text) if len(w) >= 3 and w not in _STOPWORDS}
    return words | set(_NUMBER.findall(text))


_LINK_TERMS = {_stem(w) for w in _LINKS}


def _answer_terms(question: str, answer: str) -> set:
    """The content words and numbers the answer adds to the question."""
    return _terms(answer) - _terms(question) - _LINK_TERMS


def _named(new: set, terms: set) -> set:
    """The answer's words that a text names, a short word also matching a longer form of it."""
    return {t for t in new if t in terms or not t.isdigit() and len(t) >= MIN_PREFIX
            and any(s.startswith(t) or len(s) >= MIN_PREFIX and t.startswith(s) for s in terms if not s.isdigit())}


def _parts(question: str, answer: str) -> List[set]:
    """The parts of a list answer, each as its own words: "glucose and oxygen" has two."""
    answer = re.sub(r"^\W*(?:because(?: of)?|due to|it is|they are)\s+", "", answer.strip(), flags=re.I)
    parts = (_answer_terms(question, p) for p in re.split(r",\s*(?:and\s+)?|\s+and\s+", answer))
    return [p for p in parts if p]


def _evidence(engine, question: str) -> List[str]:
    """Sentences from the top passages for the question that can count as evidence.

    Questions, lists of options, teacher's notes, long runs of text and badly scanned
    sentences are left out: the verifier will say any of them supports almost anything.
    """
    found: List[str] = []
    for passage in engine._search(question)[:engine.top_k]:
        for sentence in _SPLIT.split(passage.text):
            sentence = sentence.strip()
            if (len(sentence.split()) < 4 or len(sentence.split()) > MAX_SENTENCE_WORDS
                    or "?" in sentence or len(_OPTION.findall(sentence)) >= 2
                    or len(_BRACKET.findall(sentence)) >= 3 or _LESSON_NOTE.match(sentence)
                    or lesson_plan._clean_share(sentence, engine.vocabulary) < MIN_CLEAN_SHARE
                    or sentence in found):
                continue
            found.append(sentence)
    return found


def _wrong_side(question: str, new: set, sentence: str) -> bool:
    """The question asks what comes out and the sentence gives the answer as what goes in,
    or the other way round."""
    asks_out, asks_in = bool(_OUT_WORDS.search(question)), bool(_IN_WORDS.search(question))
    if asks_out == asks_in:
        return False
    m = _TURNS_INTO.search(sentence)
    if m:
        side = sentence[m.end():] if asks_out else sentence[:m.start()]
        return len(_named(new, _terms(side))) < MIN_ANSWER_SHARE * len(new)
    # No turning point: a sentence only about what goes in cannot answer what comes out.
    names_in, names_out = bool(_IN_WORDS.search(sentence)), bool(_OUT_WORDS.search(sentence))
    return (names_in and not names_out) if asks_out else (names_out and not names_in)


def _claim(question: str, answer: str) -> Optional[Tuple[set, str]]:
    """The answer's own words and its statement, or None if it adds nothing to judge."""
    new = _answer_terms(question, answer)
    if _NO_IDEA.search(answer or "") or not new:
        return None
    return new, _digits(statement(question, answer))


def _supports(engine, question: str, new: set, claim: str, sentence: str, *premises: str) -> set:
    """The answer's words a sentence names, if it supports the claim; else an empty set.

    The sentence must name at least half of the answer's words and every number in it,
    give the answer on the side the question asks about, and the verifier must score it
    (or one of ``premises``, which contain it) at SUPPORT_THRESHOLD or more.
    """
    named = _named(new, _terms(sentence))
    if (len(named) < MIN_ANSWER_SHARE * len(new) or not {t for t in new if t.isdigit()} <= named
            or _wrong_side(question, new, sentence)):
        return set()
    for premise in (sentence, *premises):
        if engine.verifier.support_score(_digits(premise), claim) >= SUPPORT_THRESHOLD:
            return named
    return set()


def judge_passages(engine, question: str, answer: str) -> bool:
    """Rules 1 to 3 of ``judge``: the answer against the passages alone."""
    claim = _claim(question, answer)
    if claim is None:
        return False
    new, text = claim
    parts = _parts(question, answer)
    named: set = set()
    for sentence in _evidence(engine, question):
        named |= _supports(engine, question, new, text, sentence)
        if named and all(part & named for part in parts):
            return True
    return False


def judge_said(engine, question: str, answer: str, said: str) -> bool:
    """Rule 4 of ``judge``: the answer against the first sentence of the device's own answer."""
    claim = _claim(question, answer)
    if claim is None or not said:
        return False
    new, text = claim
    return bool(_supports(engine, question, new, text, said, f"{question} {said}"))


def judge(engine, question: str, answer: str, said: Optional[str] = None) -> bool:
    """Whether the textbook supports the student's answer. The rule, which is deterministic:

    1. The answer must add a content word or number the question does not already have,
       so repeating the question or saying "I don't know" is never right. Words are
       compared in one form ("substances" and "substance", "haemoglobin" and
       "hemoglobin", "reflect" and "reflection"), and numbers as digits ("22nd" and
       "twenty second" are 22, 37°C is 37 degrees Celsius).
    2. The answer is made into one statement with the question (see ``statement``). A
       sentence supports it if it names at least half of the answer's own words and every
       number in it, and the verifier scores it as supporting the statement at
       SUPPORT_THRESHOLD (0.9) or more, the same bar every answer sentence has to clear.
       If the question asks only what goes in or only what comes out (the products of
       photosynthesis, say), the sentence must also give the answer on that side: after
       "converted into" for products, and never in a sentence only about what is needed.
    3. It is right if sentences from the top passages for the question (see ``_evidence``)
       support it and, between them, name every part of a list answer: "oxygen and
       nitrogen" is not right because a sentence says "food and oxygen".
    4. Or it is right if the first sentence of the device's own answer to the question,
       ``said``, supports it, on its own or after the question. That answer has already
       been checked against the passages, and it is a clean sentence written for this
       question, so it can say "carbohydrates" where the student says "glucose", or
       "Light travels in straight lines" to "Why can we not see light through a bent pipe?".
       Only its first sentence counts, so the student is not kept waiting.

    The shared words stop the verifier agreeing with a sentence that is about something
    else: on its own it said "Plants make food in sunlight" supports "Snakes use energy
    from the sun to make food". Anything that is not right, including an answer the
    textbook does not cover, is "not quite", and the student then hears the textbook's own
    answer, so a right answer marked wrong costs less than a wrong one marked right.
    """
    return judge_passages(engine, question, answer) or said is not None and judge_said(engine, question, answer, said)


class _Say:
    """Numbers the sentences of one reply, fixed and grounded alike."""

    def __init__(self, engine, target_language: str):
        self.engine, self.target_language, self.n = engine, target_language, 0

    def __call__(self, text: str, citation: Optional[str] = None):
        result = self.engine.say(text, self.target_language, index=self.n)
        self.n += 1
        if citation:
            result.citations = [citation]
        return result

    def number(self, result):
        result.index = self.n
        self.n += 1
        return result


def _start(engine, session, topic: str, say: _Say) -> Iterator:
    session.pending_quiz = False
    found = lesson_plan.textbook_questions(engine, topic, limit=2 * MAX_QUESTIONS)
    found = [q for q in found if not _ABOUT_STUDENT.search(q[0])][:MAX_QUESTIONS]
    if not found:
        session.quiz = None
        yield say(NO_QUESTIONS.format(topic=topic))
        return
    session.quiz = Quiz(topic, found[0], list(found[1:]))
    yield say(INTRO.format(topic=topic))
    yield say(FIRST)
    yield say(*found[0])


def _feedback(engine, question: str, answer: str, say: _Say) -> Iterator:
    if judge_passages(engine, question, answer):
        yield say(RIGHT)
        return
    # Not supported by the passages alone: the device's own answer is the feedback, and
    # its first sentence is the last chance for the answer to be right (see judge).
    grounded = engine.stream_answer(question, target_language=say.target_language)
    first = next(grounded, None)
    if first is None or first.refused:
        # Never "not quite" when the textbook cannot say what is right.
        yield say(CANT_CHECK)
        return
    if judge_said(engine, question, answer, first.text_en):
        grounded.close()
        yield say(RIGHT)
        return
    yield say(NOT_QUITE)
    for sentence in (first, *grounded):
        if not sentence.refused:   # a refusal after real sentences cannot happen, but never speak one
            yield say.number(sentence)


def _next(session, say: _Say) -> Iterator:
    quiz = session.quiz
    if not quiz.remaining:
        session.quiz = None
        yield say(LAST.format(topic=quiz.topic))
        return
    quiz.current = quiz.remaining.pop(0)
    yield say(NEXT)
    yield say(*quiz.current)


def respond(engine, session, text: str, target_language: str = "en") -> Iterator:
    """Handle a quiz turn: start a quiz, ask for a topic, judge an answer, or stop."""
    session.last_rewrite = None
    say = _Say(engine, target_language)
    if engine.safety.check(text):
        # The normal path gives the fixed safety reply, and the caller clears the memory.
        session.quiz, session.pending_quiz = None, False
        yield from engine.stream_answer(text, target_language=target_language)
        return

    if is_stop(text):
        session.quiz, session.pending_quiz = None, False
        yield say(STOPPED)
        return
    if wants_quiz(text) or session.pending_quiz:
        topic = topic_of(text) if wants_quiz(text) else plain_topic(text)
        session.pending_lesson = False
        if not topic:
            session.quiz, session.pending_quiz = None, True
            yield say(WHICH_TOPIC)
            return
        yield from _start(engine, session, topic, say)
        return
    yield from _feedback(engine, session.quiz.current[0], text, say)
    yield from _next(session, say)
