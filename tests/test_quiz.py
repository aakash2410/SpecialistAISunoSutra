"""Quizzes for a small group of students revising a topic."""

from types import SimpleNamespace

import pytest

from pocketinfer.specialist import lesson_plan, quiz
from pocketinfer.specialist.grounding import _content_terms
from pocketinfer.specialist.pipeline import SentenceResult
from pocketinfer.specialist.session import Session, ask


@pytest.mark.parametrize("said, topic", [
    ("Quiz me on photosynthesis", "photosynthesis"),
    ("Start a quiz on acids", "acids"),
    ("Give us a quiz about the food chain", "the food chain"),
    ("Can you quiz us on the parts of a flower, please?", "the parts of a flower"),
    ("Let's have a photosynthesis quiz", "photosynthesis"),
    ("Test us on light for class 7", "light"),
    ("Take a quiz", None),
    ("Quiz.", None),
    ("क्विज़", None),
])
def test_the_topic_is_taken_from_the_request(said, topic):
    assert quiz.wants_quiz(said)
    assert quiz.topic_of(said) == topic


@pytest.mark.parametrize("said", [
    "What is photosynthesis?", "What is a quiz?", "Give me a lesson plan on acids with a quiz", "Why do we sweat?",
])
def test_ordinary_questions_and_lesson_plans_are_not_quizzes(said):
    assert not quiz.handles(Session(), said)


@pytest.mark.parametrize("said, stop", [
    ("Stop the quiz", True), ("end quiz", True), ("Finish the quiz now", True), ("बस", True),
    ("Enough.", True), ("That's enough", True), ("Please stop", True),
    ("carbon dioxide", False), ("It stops the leaves drying out", False),
])
def test_stop_phrases(said, stop):
    assert quiz.is_stop(said) == stop


@pytest.mark.parametrize("question, answer, claim", [
    ("What are the two main products of photosynthesis?", "glucose and oxygen",
     "The two main products of photosynthesis are glucose and oxygen."),
    ("Which gas do plants take in during photosynthesis?", "carbon dioxide",
     "The gas that plants take in during photosynthesis is carbon dioxide."),
    ("Which gas is released during photosynthesis?", "Oxygen.", "Oxygen is released during photosynthesis."),
    ("Who uses energy from the sun to make food?", "green plants",
     "Green plants uses energy from the sun to make food."),
    ("When is World Water Day celebrated?", "22 March", "World Water Day is celebrated 22 March."),
    ("Why do you think sunlight is necessary for photosynthesis?", "Because it gives energy",
     "Sunlight is necessary for photosynthesis because it gives energy."),
])
def test_an_answer_becomes_a_statement_to_check(question, answer, claim):
    assert quiz.statement(question, answer) == claim


QUESTIONS = ("1. What are the two main products of photosynthesis? "
             "2. What do I know about photosynthesis? "
             "3. Which gas do plants take in during photosynthesis?")
PASSAGE = ("Photosynthesis gives glucose and oxygen as its two main products. "
           "Plants take in carbon dioxide gas during photosynthesis.")
Q1 = "What are the two main products of photosynthesis?"
Q2 = "Which gas do plants take in during photosynthesis?"


class FakeEngine:
    """A textbook page with three questions, one passage, and an NLI stand-in that
    entails a statement when the passage has all of its content words."""

    top_k = 3

    def __init__(self, chunks=None, flagged=(), refuse=()):
        if chunks is None:
            chunks = [{"content_id": "book", "page": 5, "chapter": "Nutrition", "text": QUESTIONS}]
        self.index = SimpleNamespace(chunks=list(chunks))
        self.vocabulary = None
        self.flagged, self.refuse, self.asked = set(flagged), set(refuse), []
        self.safety = SimpleNamespace(check=lambda text: text in self.flagged)
        self.verifier = SimpleNamespace(
            support_score=lambda premise, claim: float(_content_terms(claim) <= _content_terms(premise)))

    def stream_answer(self, request, target_language="en"):
        self.asked.append(request)
        if request in self.flagged:
            yield SentenceResult(0, "safety reply", "", refused=True, reason="safety: harm (pattern)")
        elif request in self.refuse:
            yield SentenceResult(0, "I can't answer that.", "", refused=True, reason="not covered")
        else:
            yield SentenceResult(0, "Plants make glucose and oxygen.", "")
            yield SentenceResult(1, "They take in carbon dioxide.", "")

    def say(self, text, target_language="en", index=0):
        return SentenceResult(index, text, "")

    def _search(self, query):
        return [SimpleNamespace(content_id="book", page=5, text=PASSAGE)]


def _turn(engine, session, said):
    return [r.text_en for r in ask(engine, session, said)]


def test_a_quiz_runs_from_start_to_finish():
    engine, session = FakeEngine(), Session()
    start = list(ask(engine, session, "Quiz me on photosynthesis"))
    assert [r.text_en for r in start] == [quiz.INTRO.format(topic="photosynthesis"), quiz.FIRST, Q1]
    assert start[-1].citations == ["DIKSHA book · Nutrition · page 5"]
    assert [r.index for r in start] == [0, 1, 2]
    # The question about the student is left out.
    assert session.quiz.remaining == [(Q2, "DIKSHA book · Nutrition · page 5")]

    assert _turn(engine, session, "Glucose and oxygen") == [quiz.RIGHT, quiz.NEXT, Q2]
    assert engine.asked == []      # a right answer needs no answer model

    wrong = _turn(engine, session, "Nitrogen")
    assert engine.asked == [Q2]
    assert wrong == [quiz.NOT_QUITE, "Plants make glucose and oxygen.", "They take in carbon dioxide.",
                     quiz.LAST.format(topic="photosynthesis")]
    assert session.quiz is None
    # Nothing about how the students did is kept.
    assert not session.turns


def test_a_wrong_answer_the_textbook_cannot_check_moves_on():
    engine, session = FakeEngine(refuse={Q1}), Session()
    _turn(engine, session, "Quiz us on photosynthesis")
    assert _turn(engine, session, "Carbon dioxide and water") == [quiz.CANT_CHECK, quiz.NEXT, Q2]


@pytest.mark.parametrize("answer", ["I don't know", "photosynthesis", "the products of photosynthesis"])
def test_saying_nothing_new_is_not_right(answer):
    assert not quiz.judge(FakeEngine(), Q1, answer)


def test_questions_and_options_on_the_page_are_not_evidence():
    engine = FakeEngine()
    engine._search = lambda query: [SimpleNamespace(text=(
        "When is World Water Day celebrated? World Water Day is celebrated on (a) 22 April (b) 22 March. "
        "World Water Day is celebrated on 22 March every year."))]
    question = "When is World Water Day celebrated?"
    assert quiz._evidence(engine, question) == ["World Water Day is celebrated on 22 March every year."]
    assert quiz.judge(engine, question, "22 March")
    assert not quiz.judge(engine, question, "22 April")


def test_what_goes_in_is_not_what_comes_out():
    engine = FakeEngine()
    engine._search = lambda query: [SimpleNamespace(text=(
        "In photosynthesis, carbon dioxide and water are converted into glucose and oxygen, its two main products."))]
    assert quiz.judge(engine, Q1, "glucose and oxygen")
    assert not quiz.judge(engine, Q1, "carbon dioxide and water")


@pytest.mark.parametrize("sentence, answer, wrong", [
    ("Because photosynthesis needs sunlight, chlorophyll, carbon dioxide and water, it can only happen "
     "in the green parts of a plant.", "carbon dioxide and water", True),
    ("Carbon dioxide and water, in the presence of sunlight and chlorophyll, are converted into "
     "carbohydrates (glucose) and oxygen.", "carbon dioxide and water", True),
    ("Carbon dioxide and water, in the presence of sunlight and chlorophyll, are converted into "
     "carbohydrates (glucose) and oxygen.", "glucose and oxygen", False),
    ("Leaves are the food factories of plants.", "glucose and oxygen", False),
])
def test_a_sentence_about_what_goes_in_cannot_say_what_comes_out(sentence, answer, wrong):
    assert quiz._wrong_side(Q1, quiz._answer_terms(Q1, answer), sentence) == wrong


@pytest.mark.parametrize("said, digits", [
    ("22nd March", "22 March"), ("twenty second March", "22 March"), ("On March twenty-two", "On March 22"),
    ("thirty seven degree celsius", "37 degree celsius"), ("It is 37°C.", "It is 37 degrees Celsius."),
])
def test_numbers_are_compared_as_digits(said, digits):
    assert quiz._digits(said) == digits


@pytest.mark.parametrize("answer, sentence", [
    ("new substances", "A new substance is formed."), ("haemoglobin", "Blood has hemoglobin."),
    ("It is reflected", "This is called reflection of light."), ("It carries oxygen", "Blood carry oxygen."),
])
def test_words_match_in_any_form(answer, sentence):
    question = "What happens?"
    new = quiz._answer_terms(question, answer)
    assert new and quiz._named(new, quiz._terms(sentence)) == new


def test_an_answer_with_a_number_needs_that_number():
    engine = FakeEngine()
    engine._search = lambda query: [SimpleNamespace(text="World Water Day is celebrated on 22 March every year.")]
    question = "When is World Water Day celebrated?"
    for answer in ("22nd March", "twenty second March", "On March 22"):
        assert quiz.judge(engine, question, answer), answer
    assert not quiz.judge(engine, question, "22 April")
    assert not quiz.judge(engine, question, "23rd March")


def test_every_part_of_a_list_answer_must_be_named():
    engine = FakeEngine()
    engine.verifier = SimpleNamespace(support_score=lambda premise, claim: 1.0)   # agrees with anything
    engine._search = lambda query: [SimpleNamespace(text=(
        "Because it provides food and oxygen, which are necessary for life. "
        "The xylem carries water from the roots. The phloem carries food from the leaves."))]
    assert not quiz.judge(engine, Q1, "Oxygen and nitrogen")
    assert quiz.judge(engine, Q1, "Food and oxygen")
    # Parts may be named in different sentences.
    assert quiz.judge(engine, "What kind of transport system is present in plants?", "Xylem and phloem")


ROOTS_Q = "Which part of the plant absorbs water and minerals from the soil?"


class AnswersEngine(FakeEngine):
    """Passages too badly scanned to judge from, and a clean checked answer."""

    def _search(self, query):
        return [SimpleNamespace(text="through the roots and transport it (0 the from the root to the leave plant.")]

    def stream_answer(self, request, target_language="en"):
        self.asked.append(request)
        yield SentenceResult(0, "The roots of plants absorb water and minerals from the soil.", "")
        yield SentenceResult(1, "Root hair increases the surface area for absorption.", "")


def test_a_short_right_answer_can_be_judged_on_the_devices_own_answer():
    engine, session = AnswersEngine(), Session()
    session.quiz = quiz.Quiz("transport", (ROOTS_Q, "DIKSHA"), [])
    assert not quiz.judge_passages(engine, ROOTS_Q, "The roots.")
    assert _turn(engine, session, "The roots.") == [quiz.RIGHT, quiz.LAST.format(topic="transport")]
    assert engine.asked == [ROOTS_Q]


def test_a_wrong_answer_hears_the_devices_answer_made_once():
    engine, session = AnswersEngine(), Session()
    session.quiz = quiz.Quiz("transport", (ROOTS_Q, "DIKSHA"), [])
    assert _turn(engine, session, "The leaves.") == [
        quiz.NOT_QUITE, "The roots of plants absorb water and minerals from the soil.",
        "Root hair increases the surface area for absorption.", quiz.LAST.format(topic="transport")]
    assert engine.asked == [ROOTS_Q]


def test_no_quiz_without_textbook_questions():
    engine, session = FakeEngine(chunks=[]), Session()
    assert _turn(engine, session, "Start a quiz on black holes") == [quiz.NO_QUESTIONS.format(topic="black holes")]
    assert session.quiz is None and not session.pending_quiz
    assert not quiz.handles(session, "What is photosynthesis?")


def test_the_quiz_can_be_stopped():
    engine, session = FakeEngine(), Session()
    _turn(engine, session, "Quiz me on photosynthesis")
    assert _turn(engine, session, "Stop the quiz") == [quiz.STOPPED]
    assert session.quiz is None
    assert not quiz.handles(session, "Glucose and oxygen")


def test_a_flagged_answer_gets_the_safety_reply_and_clears_the_session():
    said = "I want to hurt myself"
    engine, session = FakeEngine(flagged={said}), Session()
    _turn(engine, session, "Quiz me on photosynthesis")
    assert _turn(engine, session, said) == ["safety reply"]
    assert session.quiz is None and not session.turns


def test_a_flagged_request_gets_the_safety_reply():
    said = "Quiz me on how to hurt someone"
    engine, session = FakeEngine(flagged={said}), Session()
    assert _turn(engine, session, said) == ["safety reply"]
    assert session.quiz is None


def test_without_a_topic_the_device_asks_for_one():
    engine, session = FakeEngine(), Session()
    assert _turn(engine, session, "Can we have a quiz?") == [quiz.WHICH_TOPIC]
    assert session.pending_quiz
    assert _turn(engine, session, "Photosynthesis.")[-1] == Q1
    assert not session.pending_quiz and session.quiz.topic == "Photosynthesis"


def test_the_quiz_clears_with_the_session():
    clock = [0.0]
    engine, session = FakeEngine(), Session(clock=lambda: clock[0])
    _turn(engine, session, "Quiz me on photosynthesis")
    clock[0] += 200
    session.expire_if_idle()
    assert session.quiz is None
    assert not quiz.handles(session, "Glucose and oxygen")


def test_a_quiz_answer_is_never_taken_for_a_lesson_plan():
    engine, session = FakeEngine(), Session()
    _turn(engine, session, "Quiz me on photosynthesis")
    assert lesson_plan.handles(session, "We learned glucose and oxygen")
    reply = _turn(engine, session, "We learned glucose and oxygen")
    assert reply[0] in (quiz.RIGHT, quiz.NOT_QUITE) and lesson_plan.NOTED not in reply


def test_the_last_question_can_be_repeated():
    engine, session = FakeEngine(), Session()
    session.keep_last(list(ask(engine, session, "Quiz me on photosynthesis")))
    assert session.last_text[-1] == Q1


def test_real_answers_are_judged_against_the_textbook(specialist):
    question = "What are the two main products of photosynthesis?"   # quoted from the textbook
    assert quiz.judge(specialist, question, "glucose and oxygen")
    assert not quiz.judge(specialist, question, "carbon dioxide and water")

    session = Session()
    session.quiz = quiz.Quiz("photosynthesis", (question, "DIKSHA"), [])
    reply = list(ask(specialist, session, "Carbon dioxide and water"))
    texts = [r.text_en for r in reply]
    assert texts[0] == quiz.NOT_QUITE and texts[-1] == quiz.LAST.format(topic="photosynthesis")
    assert not any(r.refused for r in reply) and len(texts) > 2
    assert session.quiz is None
