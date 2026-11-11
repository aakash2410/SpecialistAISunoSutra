"""Quick revision of a topic for one class at a time, in a multi-grade classroom."""

import json
from types import SimpleNamespace

import numpy as np
import pytest

from pocketinfer.specialist import lesson_plan, quiz, revision
from pocketinfer.specialist.pipeline import SentenceResult, SpecialistEngine
from pocketinfer.specialist.session import Session, ask
from pocketinfer.specialist.vector_index import VectorIndex, grade_of, grade_number


@pytest.mark.parametrize("said, grade", [
    ("Revise fractions with class 6", 6), ("Revision of fractions for class 7", 7),
    ("Recap photosynthesis for class 8 students", 8), ("Revise light with class VII", 7),
    ("Recap heat for 8th class", 8), ("Revise acids for class seven", 7),
    ("Revise the food chain for the sixth standard", 6), ("Revise it for grade 6", 6),
    ("Revise photosynthesis with कक्षा 6", 6), ("class-8 students", 8),
    ("Do a quick revision of acids", None), ("Give 6 examples", None), ("In class I learned light", None),
])
def test_the_class_is_taken_from_the_request(said, grade):
    assert grade_of(said) == grade


@pytest.mark.parametrize("tag, grade", [("Class 6", 6), ("Grade VIII", 8), ("7", 7), (7, 7), ("Others", None)])
def test_grade_tags_on_chunks_are_read_as_numbers(tag, grade):
    assert grade_number(tag) == grade


@pytest.mark.parametrize("said, topic", [
    ("Revise fractions with class 6", "fractions"),
    ("Revision of fractions for class 7", "fractions"),
    ("Do a quick revision of acids", "acids"),
    ("Recap photosynthesis for class 8 students", "photosynthesis"),
    ("Can you revise the parts of a flower for grade 6, please?", "the parts of a flower"),
    ("A quick photosynthesis recap for class 8", "photosynthesis"),
    ("Revise fractions for class 6 weak students", "fractions"),
    ("Revise it with them using different examples", None),
    ("Do a quick revision", None),
])
def test_the_topic_is_taken_from_the_request(said, topic):
    assert revision.wants_revision(said)
    assert revision.topic_of(said) == topic


@pytest.mark.parametrize("said, taught", [
    ("I just taught acids to class 8.", "acids"),
    ("We have finished photosynthesis with class 6 today", "photosynthesis"),
    ("I taught class 7 science, nutrition in plants.", "nutrition in plants"),
])
def test_what_was_just_taught_is_taken_from_the_teachers_words(said, taught):
    assert revision.taught_of(said) == taught


@pytest.mark.parametrize("said", [
    "What is revision?", "Why do we revise?", "What is photosynthesis?", "Quiz me on acids",
    "Give me a lesson plan on acids with a recap", "We covered seeds yesterday.", "What about class 8?",
])
def test_ordinary_questions_quizzes_and_lesson_plans_are_not_revision(said):
    assert not revision.handles(Session(), said)


# A tiny index: four passages in two dimensions, tagged as the new harvest tags them.
CHUNKS = [
    {"text": "class 6 passage", "grade": ["Class 6"], "content_id": "six", "page": 1},
    {"text": "class 7 passage", "grade": ["Class 7"], "content_id": "seven", "page": 1},
    {"text": "class 6 and 8 passage", "grade": ["Class 6", "Class 8"], "content_id": "six_eight", "page": 1},
    {"text": "untagged passage", "content_id": "old", "page": 1},
]
VECTORS = [[0.6, 0.8], [1.0, 0.0], [0.0, 1.0], [0.8, 0.6]]


def _index(tmp_path, chunks=CHUNKS):
    np.save(tmp_path / "embeddings.npy", np.asarray(VECTORS, dtype=np.float32))
    (tmp_path / "chunks.jsonl").write_text("\n".join(json.dumps(c) for c in chunks), encoding="utf-8")
    return VectorIndex(str(tmp_path))


QUERY = np.asarray([1.0, 0.0], dtype=np.float32)   # nearest the class 7 passage


def _found(results):
    return [r.content_id for r in results]


def test_a_search_for_a_class_uses_only_its_passages_and_untagged_ones(tmp_path):
    index = _index(tmp_path)
    assert _found(index.search(QUERY, top_k=4)) == ["seven", "old", "six", "six_eight"]
    assert _found(index.search(QUERY, top_k=4, grade=6)) == ["old", "six", "six_eight"]
    assert _found(index.search(QUERY, top_k=4, grade="Class 7")) == ["seven", "old"]
    assert _found(index.search(QUERY, top_k=4, grade=8)) == ["old", "six_eight"]


def test_a_class_with_no_passages_falls_back_to_every_passage(tmp_path):
    index = _index(tmp_path)
    assert _found(index.search(QUERY, top_k=2, grade=9)) == ["seven", "old"]
    # Class 8 has passages, but none close enough to answer from.
    assert _found(index.search(QUERY, top_k=2, grade=8, min_score=0.9)) == ["seven", "old"]
    assert _found(index.search(QUERY, top_k=2, grade=8, min_score=0.7)) == ["old", "six_eight"]


def test_an_index_without_grades_searches_as_before(tmp_path):
    index = _index(tmp_path, [{k: v for k, v in c.items() if k != "grade"} for c in CHUNKS])
    assert index.rows_for_grade(6) is None
    plain = index.search(QUERY, top_k=3)
    assert index.search(QUERY, top_k=3, grade=6) == plain
    assert [(r.content_id, r.score) for r in plain] == [("seven", 1.0), ("old", pytest.approx(0.8)),
                                                      ("six", pytest.approx(0.6))]


def test_the_engine_passes_the_class_and_its_gate_to_the_search():
    calls = []
    engine = SpecialistEngine.__new__(SpecialistEngine)
    engine.embedder = SimpleNamespace(embed_one=lambda text, kind: QUERY)
    engine.index = SimpleNamespace(search=lambda qvec, **kwargs: calls.append(kwargs) or [])
    engine.top_k, engine.min_score = 3, 0.8
    engine._search("acids")
    engine._search("acids", grade=7)
    assert calls == [{"top_k": 20}, {"top_k": 20, "grade": 7, "min_score": 0.8}]


PAGE = ("1. What is the colour of litmus in an acid? 2. Why do you think acids taste sour? "
        "3. What did you find most interesting about acids? 4. What is the taste of an acid?")


class FakeEngine:
    """Explains topics with fixed sentences, and remembers which class each search was for."""

    top_k = 3

    def __init__(self, chunks=None, flagged=(), refuse=()):
        if chunks is None:
            chunks = [{"content_id": "book", "page": 5, "chapter": "Acids", "text": PAGE}]
        self.index = SimpleNamespace(chunks=list(chunks))
        self.vocabulary = None
        self.flagged, self.refuse = set(flagged), set(refuse)
        self.asked, self.searched = [], []
        self.safety = SimpleNamespace(check=lambda text: text in self.flagged or "weak" in text)

    def stream_answer(self, request, target_language="en", grade=None):
        self.asked.append((request, grade))
        if request in self.flagged:
            yield SentenceResult(0, "safety reply", "", refused=True, reason="safety: harm (pattern)")
        elif any(topic in request for topic in self.refuse):
            yield SentenceResult(0, "I can't answer that.", "", refused=True, reason="not covered")
        else:
            yield SentenceResult(0, "Acids taste sour.", "")
            yield SentenceResult(1, "For example, curd and lemon taste sour.", "", example=True)

    def say(self, text, target_language="en", index=0):
        return SentenceResult(index, text, "")

    def _search(self, query, grade=None):
        self.searched.append((query, grade))
        return [SimpleNamespace(content_id="book", page=5)]


def _turn(engine, session, said):
    return [r.text_en for r in ask(engine, session, said)]


CITE = "DIKSHA book · Acids · page 5"


def test_the_multi_grade_revision_journey():
    engine, session = FakeEngine(), Session()
    said = "I just taught acids to class 8. Revise it with them using different examples."
    results = list(ask(engine, session, said))
    assert [r.text_en for r in results] == [
        "Here is a quick revision of acids for class 8.",
        "Acids taste sour.", "For example, curd and lemon taste sour.",
        revision.ASK_MANY,
        "What is the colour of litmus in an acid?", "What is the taste of an acid?",
        revision.CLOSE.format(topic="acids"),
    ]
    assert [r.index for r in results] == list(range(len(results)))
    assert results[4].citations == [CITE]
    assert engine.asked == [("Explain acids", 8)]
    assert engine.searched == [("acids", 8)]          # the questions come from class 8 pages too
    assert (session.grade, session.revision_topic) == (8, "acids")

    # The teacher turns to the other class, and later comes back to this one.
    assert _turn(engine, session, "Now do the same for class 7.")[0] == "Here is a quick revision of acids for class 7."
    assert engine.asked[-1] == ("Explain acids", 7)
    assert _turn(engine, session, "And for class 6?")[0] == "Here is a quick revision of acids for class 6."
    # A new topic with no class named is for the class named last.
    assert _turn(engine, session, "Recap photosynthesis")[0] == "Here is a quick revision of photosynthesis for class 6."
    assert engine.asked[-1] == ("Explain photosynthesis", 6)

    session.clear()
    assert session.grade is None and session.revision_topic is None
    assert _turn(engine, session, "Do a quick revision of acids")[0] == "Here is a quick revision of acids."
    assert engine.asked[-1] == ("Explain acids", None)


def test_without_a_textbook_question_a_fixed_prompt_is_used():
    engine = FakeEngine(chunks=[])
    texts = _turn(engine, Session(), "Revise acids with class 7")
    assert texts[-2:] == [revision.ASK_FIXED.format(topic="acids"), revision.CLOSE.format(topic="acids")]


def test_a_topic_the_textbook_does_not_cover_is_refused():
    engine, session = FakeEngine(refuse={"black holes"}), Session()
    assert _turn(engine, session, "Revise black holes with class 8") == ["I can't answer that."]
    assert not session.turns


def test_no_topic_and_other_classes_get_a_fixed_reply():
    engine = FakeEngine()
    assert _turn(engine, Session(), "Do a quick revision") == [revision.WHICH_TOPIC]
    assert _turn(engine, Session(), "Revise acids with class 10") == [revision.OTHER_GRADE]
    assert engine.asked == []


def test_the_last_lesson_plan_topic_is_used_when_none_is_named():
    engine, session = FakeEngine(), Session()
    assert _turn(engine, session, "We covered acids yesterday.") == [lesson_plan.NOTED]
    assert _turn(engine, session, "Revise it with class 7")[0] == "Here is a quick revision of acids for class 7."


def test_a_flagged_request_gets_the_safety_reply_and_clears_memory():
    said = "Revise how to hurt someone with class 7"
    engine, session = FakeEngine(flagged={said}), Session()
    _turn(engine, session, "Revise acids with class 8")
    assert _turn(engine, session, said) == ["safety reply"]
    assert session.grade is None and session.revision_topic is None and not session.turns


def test_level_words_are_not_read_as_a_safety_concern():
    engine = FakeEngine()
    texts = _turn(engine, Session(), "Revise acids with class 6 weak students")
    assert texts[0] == "Here is a quick revision of acids for class 6."


def test_a_quiz_in_progress_is_not_taken_for_a_revision():
    engine, session = FakeEngine(), Session()
    session.quiz = quiz.Quiz("acids", ("What is the taste of an acid?", CITE))
    assert quiz.handles(session, "Revise acids with class 6")


def test_a_revision_can_be_repeated():
    engine, session = FakeEngine(), Session()
    results = list(ask(engine, session, "Revise acids with class 6"))
    session.keep_last(results)
    assert session.last_text == [r.text_en for r in results]


def test_a_real_revision_is_grounded(specialist):
    session = Session()
    results = list(ask(specialist, session, "Revise photosynthesis with class 7"))
    texts = [r.text_en for r in results]
    assert texts[0] == revision.INTRO_GRADE.format(topic="photosynthesis", grade=7)
    assert not any(r.refused for r in results)
    assert texts[-1] == revision.CLOSE.format(topic="photosynthesis")
    assert sum(r.example for r in results) <= 1
    assert session.grade == 7
