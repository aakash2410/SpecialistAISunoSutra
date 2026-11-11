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
