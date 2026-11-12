"""Lesson plans for a teacher preparing the next class (user journey 1)."""

from types import SimpleNamespace

import pytest

from pocketinfer.specialist import lesson_plan
from pocketinfer.specialist.pipeline import SentenceResult
from pocketinfer.specialist.session import Session, ask


@pytest.mark.parametrize("said, topic", [
    ("Give me a lesson plan on photosynthesis for tomorrow's class", "photosynthesis"),
    ("Plan a lesson about acids, bases and salts for class 7", "acids, bases and salts"),
    ("Can you make a lesson plan on the Mughal empire?", "the Mughal empire"),
    ('"Photosynthesis" lesson plan, please', "Photosynthesis"),
    ("Yesterday we covered photosynthesis and now I need a lesson plan on respiration in plants",
     "respiration in plants"),
    ("Lesson plan for the next class.", None),
    ("पाठ योजना", None),
])
def test_the_topic_is_taken_from_the_request(said, topic):
    assert lesson_plan.wants_plan(said)
    assert lesson_plan.topic_of(said) == topic


@pytest.mark.parametrize("said, covered", [
    ("We covered fractions last class.", "fractions"),
    ("Yesterday we did heat.", "heat"),
    ("We covered the parts of a flower yesterday.", "the parts of a flower"),
    ("We finished photosynthesis, now give me a lesson plan on respiration", "photosynthesis"),
    ("What did we cover?", None),
    ("Why is the sky blue?", None),
])
def test_what_the_class_covered_is_taken_from_the_teachers_words(said, covered):
    assert lesson_plan.covered_of(said) == covered


@pytest.mark.parametrize("question", [
    "What is photosynthesis?", "How do plants make food?", "Explain the plan of a house", "Why do we sweat?",
])
def test_ordinary_questions_are_not_lesson_planning(question):
    assert not lesson_plan.handles(Session(), question)


class FakeEngine:
    """Answers "Explain <topic>" with fixed sentences, or refuses topics it lacks."""

    top_k = 3

    def __init__(self, ideas=("Plants make food from sunlight.", "Leaves hold chlorophyll."),
                 chunks=(), flagged=()):
        self.ideas, self.flagged, self.asked = list(ideas), set(flagged), []
        self.index = SimpleNamespace(chunks=list(chunks))
        self.vocabulary = None
        self.safety = SimpleNamespace(check=lambda text: text in self.flagged)

    def stream_answer(self, request, target_language="en"):
        self.asked.append(request)
        if request in self.flagged:
            yield SentenceResult(0, "safety reply", "", refused=True, reason="safety: harm (pattern)")
        elif not self.ideas or "black holes" in request:
            yield SentenceResult(0, "I can't answer that.", "", refused=True, reason="not covered")
        else:
            for i, text in enumerate(self.ideas):
                yield SentenceResult(i, text, "")

    def say(self, text, target_language="en", index=0):
        return SentenceResult(index, text, "")

    def _search(self, topic):
        return [SimpleNamespace(content_id="book", page=5)]


def _turn(engine, session, said):
    return [r.text_en for r in ask(engine, session, said)]


def test_the_teacher_prep_journey():
    engine, session = FakeEngine(), Session()
    assert _turn(engine, session, "Yesterday we covered seeds.") == [lesson_plan.NOTED]
    assert _turn(engine, session, "Give me a lesson plan for the next class.") == [lesson_plan.WHICH_TOPIC]

    plan = _turn(engine, session, "Photosynthesis.")
    assert engine.asked == ["Explain Photosynthesis", "Explain Photosynthesis in simple words"]
    ideas = ["Plants make food from sunlight.", "Leaves hold chlorophyll."]
    assert plan == [
        "Here is a lesson plan on Photosynthesis, for a class at different levels.",
        "Start with a short recap of seeds. Ask the class what they remember.",
        lesson_plan.KEY_IDEAS_ALL, *ideas,
        lesson_plan.SIMPLE_ALL, *ideas,
        "Next, ask two or three students to explain Photosynthesis in their own words.",
        "Ask students who are ahead to explain Photosynthesis to a classmate who needs help.",
        "To close, ask one student to sum up Photosynthesis in one sentence.",
    ]
    assert not session.pending_lesson
    # The recap was about the class before that lesson, so the next plan starts fresh.
    assert _turn(engine, session, "Lesson plan on leaves")[1] == lesson_plan.RECAP_FRESH.format(topic="leaves")


@pytest.mark.parametrize("said, level", [
    ("Give me a lesson plan on photosynthesis for weak students", "support"),
    ("Lesson plan on fractions for students who are behind", "support"),
    ("Lesson plan on photosynthesis for students who need more help", "support"),
    ("Lesson plan on acids for students who are ahead", "stretch"),
    ("Lesson plan for bright students on the food chain", "stretch"),
    ("Lesson plan on heat for a mixed ability class", "all"),
    ("Lesson plan on the top layer of soil", "all"),
])
def test_the_level_is_taken_from_who_the_plan_is_for(said, level):
    assert lesson_plan.level_of(said) == level
    assert "student" not in (lesson_plan.topic_of(said) or "")


QUESTIONS = ("1. What are the two main products of photosynthesis? "
             "2. Why do you think sunlight is necessary for photosynthesis? "
             "3. Which gas do plants take in during photosynthesis?")


def _plan(level):
    engine = FakeEngine(chunks=[{"content_id": "book", "page": 5, "chapter": "1", "text": QUESTIONS}])
    return engine, [r.text_en for r in lesson_plan.stream_lesson_plan(engine, "photosynthesis", level=level)]


def test_a_mixed_class_plan_has_something_for_each_level():
    engine, plan = _plan("all")
    assert lesson_plan.SIMPLE_ALL in plan
    check = plan.index(lesson_plan.CHECK_QUOTED)
    assert plan[check + 1] == "What are the two main products of photosynthesis?"
    stretch = plan.index(lesson_plan.STRETCH_QUOTED)
    assert plan[stretch + 1] == "Why do you think sunlight is necessary for photosynthesis?"


def test_a_plan_for_students_who_need_help_is_simple_and_asks_for_facts():
    engine, plan = _plan("support")
    assert engine.asked == ["Explain photosynthesis in simple words"]
    assert lesson_plan.SIMPLE_ONLY in plan and lesson_plan.STRETCH_QUOTED_MANY not in plan
    asked = plan[plan.index(lesson_plan.CHECK_QUOTED_MANY) + 1:-1]
    assert asked == ["What are the two main products of photosynthesis?",
                     "Which gas do plants take in during photosynthesis?"]


def test_a_plan_for_students_who_are_ahead_asks_why():
    engine, plan = _plan("stretch")
    assert engine.asked == ["Explain photosynthesis"]
    asked = plan[plan.index(lesson_plan.STRETCH_QUOTED_MANY) + 1:-1]
    assert asked == ["Why do you think sunlight is necessary for photosynthesis?"]


def test_tomorrow_does_not_make_the_plan_a_question_about_today():
    engine = FakeEngine()
    _turn(engine, Session(), "Give me a lesson plan on photosynthesis for tomorrow's class")
    assert engine.asked == ["Explain photosynthesis", "Explain photosynthesis in simple words"]


def test_no_plan_without_checked_textbook_content():
    engine = FakeEngine()
    assert _turn(engine, Session(), "Lesson plan on black holes") == ["I can't answer that."]


def test_a_flagged_request_gets_the_safety_reply_and_clears_memory():
    said = "Lesson plan on how to hurt someone"
    engine, session = FakeEngine(flagged={said}), Session()
    _turn(engine, session, "We covered seeds.")
    assert session.covered == "seeds"
    assert _turn(engine, session, said) == ["safety reply"]
    assert session.covered is None and not session.turns


def test_questions_are_quoted_only_when_they_stand_on_their_own():
    text = ("1. What are the two main products of photosynthesis? 2. Why does this happen? "
            "3. What do you observe in the figure about photosynthesis? 4. How do roots take in water?")
    chunks = [{"content_id": "book", "page": 6, "chapter": "Nutrition", "text": text},
              {"content_id": "other", "page": 5, "chapter": "Light",
               "text": "What is the role of photosynthesis in light?"}]
    found = lesson_plan.textbook_questions(FakeEngine(chunks=chunks), "photosynthesis")
    assert found == [("What are the two main products of photosynthesis?", "DIKSHA book · Nutrition · page 6")]


def test_numbered_questions_are_found_and_repeats_are_quoted_once():
    text = ("Exercises (i) Which part of the plant takes in carbon dioxide for photosynthesis? "
            "ii) Which part of a plant takes in carbon dioxide for photosynthesis ? "
            "4 How do leaves take in carbon dioxide for photosynthesis?")
    engine = FakeEngine(chunks=[{"content_id": "book", "page": 5, "chapter": "Nutrition", "text": text}])
    found = [q for q, _ in lesson_plan.textbook_questions(engine, "photosynthesis")]
    assert found == ["Which part of the plant takes in carbon dioxide for photosynthesis?",
                     "How do leaves take in carbon dioxide for photosynthesis?"]


def test_questions_come_from_a_search_for_questions_on_the_topic():
    searched = []
    engine = FakeEngine()
    engine._search = lambda request: searched.append(request) or []
    lesson_plan.textbook_questions(engine, "photosynthesis")
    assert searched == ["photosynthesis questions"]


def test_scan_damage_is_never_quoted():
    chunks = [{"content_id": "book", "page": 5, "chapter": "1",
               "text": "What are the prducts of photosyntheis in leeves?"}]
    engine = FakeEngine(chunks=chunks)
    engine.vocabulary = {"what", "are", "the", "products", "photosynthesis", "leaves"}
    assert lesson_plan.textbook_questions(engine, "photosynthesis") == []


def test_a_plan_can_be_repeated():
    engine, session = FakeEngine(), Session()
    results = list(ask(engine, session, "Lesson plan on photosynthesis"))
    session.keep_last(results)
    assert session.last_text[0] == "Here is a lesson plan on photosynthesis, for a class at different levels."
    assert len(session.last_text) == len(results)


def test_a_real_plan_is_grounded_and_covers_each_level(specialist):
    plan = list(lesson_plan.stream_lesson_plan(specialist, "photosynthesis"))
    texts = [r.text_en for r in plan]
    assert texts[0] == lesson_plan.INTRO["all"].format(topic="photosynthesis")
    assert not any(r.refused for r in plan)
    assert texts[-1] == lesson_plan.CLOSE.format(topic="photosynthesis")
    assert sum(r.example for r in plan) <= 2      # at most one per explanation
    # The test index has fewer clean question pages than the device's, so either is fine.
    assert lesson_plan.CHECK_QUOTED in texts or lesson_plan.ASK_FIXED.format(topic="photosynthesis") in texts
    assert lesson_plan.STRETCH_QUOTED in texts or lesson_plan.STRETCH_FIXED.format(topic="photosynthesis") in texts


@pytest.mark.parametrize("said, checked", [
    ("Lesson plan on the food chain for weak students", "Lesson plan on the food chain  for students "),
    ("Lesson plan on heat for students who are behind", "Lesson plan on heat  for students "),
    ("Lesson plan on heat for students who are suicidal", "Lesson plan on heat for students who are suicidal"),
    ("Lesson plan on heat for kids I want to hurt", "Lesson plan on heat  for students  I want to hurt"),  # still checked
])
def test_level_words_are_not_read_as_a_safety_concern_but_anything_else_is(said, checked):
    assert lesson_plan.safety_text(said) == checked


def test_a_plan_for_weak_students_passes_the_real_safety_check(safety_model):
    from pocketinfer.specialist.safety import SafetyCheck

    check = SafetyCheck(safety_model)
    assert not check.check(lesson_plan.safety_text("Lesson plan on the food chain for weak students"))
    assert check.check(lesson_plan.safety_text("Lesson plan on heat for students who want to die"))
