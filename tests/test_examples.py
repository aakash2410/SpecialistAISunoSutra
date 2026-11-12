"""Local examples: after an explanation, the answer model writes one, checked under its own rule."""

import pytest

from pocketinfer.specialist.grounding import EXAMPLE_SYSTEM, build_grounding, example_prompt, is_example
from pocketinfer.specialist.vector_index import Retrieved

PASSAGE = Retrieved(0.9, "Heat flows from a hotter object to a colder object by conduction when the two "
                         "objects are in contact. Metals are good conductors of heat.", "b", "3", "p", 1, "s")
FACT = "Heat moves from one object to another when they are in direct contact."
REQUEST = "Explain conduction of heat"
GOOD = "For example, when you stir hot dal with a steel spoon, the heat moves up to the handle of the spoon."


def test_the_answer_prompt_still_forbids_the_models_own_examples():
    g = build_grounding(REQUEST, [PASSAGE], min_score=0.1)
    assert "do NOT add your own examples" in g.system_prompt
    assert "village" in EXAMPLE_SYSTEM and FACT in example_prompt(REQUEST, [FACT])


@pytest.mark.parametrize("sentence, example", [
    ("For example, a steel glass of hot tea warms your hand.", True),
    ("For instance, a ladle in a pot of dal gets hot.", True),
    ("Metals are good conductors of heat.", False),
    ("Heat flows, for example, through metals.", False),
])
def test_an_example_is_recognised_by_how_it_starts(sentence, example):
    assert is_example(sentence) is example


def _example(engine, written, request=REQUEST, intent="explain", kept=(FACT,)):
    engine.llm = lambda system, user: written
    return engine._example(request, intent, list(kept), [PASSAGE] * len(kept), [PASSAGE])


@pytest.mark.parametrize("written, kept", [
    (GOOD, True),
    ("For example, a steel spoon in hot dal gets hot in 5 minutes.", False),                # adds a number
    ("For example, Ramesh touched a hot spoon in Varanasi.", False),                        # adds names
    ("For example, the heat moves from the cold handle to the hot dal.", False),            # flips the direction
    ("For example, in a village, farmers use a chulha to cook food with wood from the forest.", False),  # off the question
    ("For example, a chulha in a village has a metal plate that melts in the heat.", False),  # not a case of the fact
    ("Conduction is common in villages.", False),                                           # not an example
    ("For example, a farmer's chimney lets wind blow through copper coils, which creates a magnetic effect "
     "that powers the pump.", False),                                                      # too much new content
])
def test_an_example_is_kept_only_if_it_adds_no_fact_and_shows_one(specialist, written, kept):
    assert (_example(specialist, written) == (written, PASSAGE)) is kept


def test_direct_answers_get_no_example(specialist):
    assert _example(specialist, GOOD, request="Which way does heat flow?", intent="answer") is None
    assert _example(specialist, GOOD, kept=()) is None


# ------------------------------------------------------- the teacher's own examples
from types import SimpleNamespace

import numpy as np

from pocketinfer.specialist import teacher_examples as te
from pocketinfer.specialist.grounding import TEACHER_EXAMPLE_SYSTEM, _content_terms, teacher_example_prompt
from pocketinfer.specialist.pipeline import SpecialistEngine

FILLED = """# A note from the template
1. Drying red chillies on the roof in the sun

- Stirring hot kheer in a brass kadhai
Stirring hot kheer in a brass kadhai.
Call the tractor driver on 9876543210
Taking the goats to graze by the pond
Sharpening a knife to hurt someone
गाय का दूध निकालना
""" + "Carrying a heavy bundle of dry sugarcane leaves home from the field " * 3


def _flagged(line):
    return "hurt" in line


@pytest.fixture(autouse=True)
def _cache_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv(te.CACHE_ENV, str(tmp_path / "cache"))


def test_the_teacher_file_keeps_only_short_safe_english_lines_without_numbers():
    lines = te.clean_lines(FILLED, _flagged)
    assert lines[:3] == ["Drying red chillies on the roof in the sun", "Stirring hot kheer in a brass kadhai",
                         "Taking the goats to graze by the pond"]
    assert len(lines) == 4 and len(lines[3]) <= te.MAX_CHARS and lines[3].startswith("Carrying a heavy bundle")


def test_an_empty_or_missing_file_leaves_examples_as_they_were(tmp_path, monkeypatch):
    assert te.load_teacher_examples(_flagged, te.TEMPLATE_PATH) == []        # the template as shipped
    assert te.load_teacher_examples(_flagged, str(tmp_path / "missing.txt")) == []
    monkeypatch.setenv(te.ENV, str(tmp_path / "missing.txt"))
    assert te.resolve_path() == str(tmp_path / "missing.txt")
    monkeypatch.delenv(te.ENV)
    assert te.resolve_path() in (te.DEVICE_PATH, te.TEMPLATE_PATH)


def test_the_template_explains_the_format_and_has_only_notes():
    with open(te.TEMPLATE_PATH, encoding="utf-8") as f:
        text = f.read()
    assert all(not line.strip() or line.startswith("#") for line in text.splitlines())
    assert "# Drying red chillies on the roof in the sun" in text and "30 to 40" in text


def test_the_lines_closest_to_the_topic_are_picked():
    lines = ["kites", "dal", "goats", "chillies"]
    vectors = np.eye(4, dtype=np.float32)
    query = np.array([0.1, 0.9, 0.0, 0.5], dtype=np.float32)
    assert te.pick(query, vectors, lines, k=2) == ["dal", "chillies"]
    assert te.pick(query, vectors, lines, k=3, margin=0.1) == ["dal", "chillies"]   # kites is below the mean
    assert te.pick(query, vectors, lines, k=3, margin=0.6) == []                    # none stands out
    assert te.pick(query, vectors[:0], [], k=2) == []


def test_the_teacher_prompt_shows_the_picked_lines_and_allows_none():
    prompt = teacher_example_prompt(REQUEST, [FACT], ["Stirring hot kheer in a brass kadhai"])
    assert FACT in prompt and "- Stirring hot kheer in a brass kadhai" in prompt
    assert "NONE" in TEACHER_EXAMPLE_SYSTEM and "For example," in TEACHER_EXAMPLE_SYSTEM


class _Embedder:
    """Puts any text about heat on the kheer line, gilli danda far from it, and the rest in between."""

    def embed(self, texts, kind="passage"):
        return np.array([self.embed_one(t, kind) for t in texts], dtype=np.float32)

    def embed_one(self, text, kind="query"):
        text = text.lower()
        if "heat" in text or "kheer" in text:
            return np.array([1.0, 0.0], dtype=np.float32)
        if "gilli" in text:
            return np.array([0.0, 1.0], dtype=np.float32)
        return np.array([0.9, 0.4359], dtype=np.float32)


def _bare_engine(tmp_path, lines):
    path = tmp_path / "teacher_examples.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    engine = SpecialistEngine.__new__(SpecialistEngine)
    engine.embedder = _Embedder()
    engine.safety = SimpleNamespace(check=_flagged, check_line=_flagged)
    engine.use_teacher_examples(str(path))
    return engine


def test_the_teacher_prompt_comes_first_and_the_village_prompt_follows_if_it_fails(tmp_path):
    engine = _bare_engine(tmp_path, ["Flying kites from the rooftop", "Stirring hot kheer in a brass kadhai",
                                     "Taking the goats to graze", "Drying chillies", "Playing gilli danda"])
    calls = []
    engine.llm = lambda system, user: calls.append((system, user)) or "NONE"
    engine._checked_example = lambda reply, *rest: None if reply == "NONE" else (reply, PASSAGE)
    assert engine._example(REQUEST, "explain", [FACT], [PASSAGE], [PASSAGE]) is None
    assert [c[0] for c in calls] == [TEACHER_EXAMPLE_SYSTEM, EXAMPLE_SYSTEM]
    shown = calls[0][1].split("everyday examples:")[1]
    assert shown.index("kheer") < shown.index("kites") < shown.index("goats")       # closest first
    assert "chillies" not in shown and "gilli" not in shown                         # three at most, none far off


@pytest.mark.parametrize("compares", [
    "For example, stirring kheer takes energy, just like heat moving into a spoon.",
    "For example, stirring hot kheer is a practical application of the fact that heat moves.",
    "For example, stirring hot kheer demonstrates conduction.",
])
def test_a_teacher_example_that_only_compares_is_not_used(tmp_path, compares):
    engine = _bare_engine(tmp_path, ["Stirring hot kheer in a brass kadhai"])
    engine.llm = lambda system, user: compares if system == TEACHER_EXAMPLE_SYSTEM else GOOD
    engine._checked_example = lambda reply, *rest: (reply, PASSAGE)
    assert engine._example(REQUEST, "explain", [FACT], [PASSAGE], [PASSAGE]) == (GOOD, PASSAGE)


def test_with_no_line_close_to_the_topic_only_the_village_prompt_is_used(tmp_path):
    engine = _bare_engine(tmp_path, ["Playing gilli danda in the lane"])
    calls = []
    engine.llm = lambda system, user: calls.append(system) or GOOD
    engine._checked_example = lambda reply, *rest: (reply, PASSAGE)
    assert engine._example(REQUEST, "explain", [FACT], [PASSAGE], [PASSAGE]) == (GOOD, PASSAGE)
    assert calls == [EXAMPLE_SYSTEM]


def test_without_a_teacher_file_only_the_village_prompt_is_used(tmp_path):
    engine = _bare_engine(tmp_path, ["# only a note", ""])
    calls = []
    engine.llm = lambda system, user: calls.append(system) or GOOD
    engine._checked_example = lambda reply, *rest: (reply, PASSAGE)
    assert engine._example(REQUEST, "explain", [FACT], [PASSAGE], [PASSAGE]) == (GOOD, PASSAGE)
    assert calls == [EXAMPLE_SYSTEM]


def test_the_teachers_words_count_as_everyday_words(tmp_path):
    sentence = "For example, stirring hot kheer in a brass kadhai warms the ladle."
    known = " ".join([REQUEST, FACT, PASSAGE.text])
    plain = _bare_engine(tmp_path, [])._new_content(sentence, known)
    assert _content_terms("kheer brass kadhai") <= plain
    line = "Stirring hot kheer in a brass kadhai"
    taught = _bare_engine(tmp_path, [line])._new_content(sentence, known)
    assert taught == plain - _content_terms(line) and "warm" in taught


KHEER = "A steel ladle left in the hot kheer gets hot"


def test_qwen_turns_a_teachers_example_into_one_that_passes_the_check(specialist, tmp_path):
    path = tmp_path / "teacher_examples.txt"
    path.write_text("\n".join(["Flying kites from the rooftop", "Playing gilli danda in the lane", KHEER,
                               "Taking the goats to graze by the pond", "Sowing seeds after the first rain",
                               "Churning buttermilk in a clay pot", "Riding a bicycle to the next village"]))
    specialist.use_teacher_examples(str(path))
    asked = specialist.llm
    systems = []
    specialist.llm = lambda system, user: systems.append(system) or asked(system, user)
    found = specialist._example(REQUEST, "explain", [FACT], [PASSAGE], [PASSAGE])
    assert systems[0] == TEACHER_EXAMPLE_SYSTEM
    assert found is not None and found[1] == PASSAGE, found
    assert any(w in found[0].lower() for w in ("kheer", "kadhai", "ladle")), found[0]

    for invented in ("For example, a steel ladle in hot kheer gets hot in 5 minutes.",
                     "For example, a steel ladle in hot kheer gets hot because steel is magnetic."):
        specialist.llm = lambda system, user: invented
        assert specialist._example(REQUEST, "explain", [FACT], [PASSAGE], [PASSAGE]) is None, invented


def test_a_teacher_example_without_its_lead_gets_one_and_none_stays_none(tmp_path):
    engine = _bare_engine(tmp_path, ["Stirring hot kheer in a brass kadhai", "Flying kites", "Taking the goats out"])
    seen = []
    engine._checked_example = lambda reply, *rest: seen.append(reply)
    engine.llm = lambda system, user: "A steel ladle in hot kheer gets hot." if system == TEACHER_EXAMPLE_SYSTEM else "NONE"
    engine._example(REQUEST, "explain", [FACT], [PASSAGE], [PASSAGE])
    assert seen == ["For example, a steel ladle in hot kheer gets hot.", "NONE"]
    seen.clear()
    engine.llm = lambda system, user: "NONE."
    engine._example(REQUEST, "explain", [FACT], [PASSAGE], [PASSAGE])
    assert seen == ["NONE.", "NONE."]
