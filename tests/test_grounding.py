"""Grounding + refusal — the core safety behavior of The Specialist."""

import pytest

from pocketinfer.specialist.grounding import (
    REFUSAL_SENTINEL,
    build_grounding,
    classify_intent,
    clean_answer,
    is_refusal_response,
    iter_sentences,
)
from pocketinfer.specialist.vector_index import Retrieved


def _passage(score, text="Photosynthesis is how plants make food."):
    return Retrieved(score=score, text=text, content_id="do_1",
                     chapter="Ch1 Nutrition", section="Photosynthesis", page=2,
                     source="https://diksha.gov.in/")


@pytest.mark.parametrize("text,expected", [
    ("Explain photosynthesis", "explain"),
    ("What is heat?", "explain"),
    ("Simplify this for a child", "simplify"),
    ("saral bhasha mein samjhao", "simplify"),
    ("How many bones are in the body", "answer"),
])
def test_intent_classification(text, expected):
    assert classify_intent(text) == expected


def test_refuses_when_no_passages():
    g = build_grounding("anything", [], min_score=0.10)
    assert g.refused
    assert "0.00" in g.reason or "similarity" in g.reason.lower()


def test_refuses_when_below_threshold():
    g = build_grounding("weak match", [_passage(0.05)], min_score=0.10)
    assert g.refused


def test_answers_when_above_threshold():
    g = build_grounding("explain photosynthesis", [_passage(0.42)], min_score=0.10)
    assert not g.refused
    assert g.intent == "explain"
    assert g.citations and g.citations[0].startswith("DIKSHA ")


def test_grounded_prompt_contains_sources_and_constraint():
    g = build_grounding("explain photosynthesis", [_passage(0.42)], min_score=0.10)
    assert "Photosynthesis is how plants make food." in g.system_prompt
    # Grounding constraint present; refusal is handled by the retrieval gate, not the LLM.
    assert "only from" in g.system_prompt.lower() or "only use" in g.system_prompt.lower()
    assert "explain photosynthesis" in g.user_prompt.lower()


@pytest.mark.parametrize("text,is_refusal", [
    (REFUSAL_SENTINEL, True),
    (f"  {REFUSAL_SENTINEL}  ", True),
    ("", True),
    ("NO_ANSWER_IN_SOURCE\n\nPlease clarify what you mean.", True),  # leads with sentinel
    ("Photosynthesis is the process by which...", False),
    ("Heat flows from hot to cold.\n\n(No answer in source)", False),  # trailing leak, not a refusal
])
def test_generation_gate_detects_refusal(text, is_refusal):
    assert is_refusal_response(text) is is_refusal


@pytest.mark.parametrize("raw,expected", [
    ("Heat flows from hot to cold.\n\n(No answer in source)", "Heat flows from hot to cold."),
    ("Plants make food. NO_ANSWER_IN_SOURCE", "Plants make food."),
    ("A clean answer.", "A clean answer."),
    ("Here's an explanation of photosynthesis:\n\nPlants make food.", "Plants make food."),
    ("Here is how it works: heat rises.", "heat rises."),
])
def test_clean_answer_strips_preamble_and_refusal(raw, expected):
    assert clean_answer(raw) == expected


def test_iter_sentences_assembles_from_token_stream():
    # Simulate an LLM streaming tokens (split mid-word/mid-sentence).
    tokens = ["Heat flows", " from hot", " to cold.", " It has thr", "ee ways!", " Done?"]
    assert list(iter_sentences(tokens)) == ["Heat flows from hot to cold.", "It has three ways!", "Done?"]


def test_iter_sentences_flushes_unterminated_remainder():
    assert list(iter_sentences(["No period here"])) == ["No period here"]


def test_iter_sentences_handles_devanagari_danda():
    assert list(iter_sentences(["यह वाक्य है।", " दूसरा।"])) == ["यह वाक्य है।", "दूसरा।"]
