"""User acceptance tests: does the device meet the v1 spec, as a teacher sees it?

Each test maps to a promise in "Product Specifications: The Specialist v1" and runs
on the real answer model. Tests needing the device install skip elsewhere.
"""

import base64
import os
import socket
import time

import pytest

from conftest import run_command
from pocketinfer.specialist.grounding import REFUSAL_TEXT
from pocketinfer.specialist.vector_index import Retrieved


# ----------------------------------- "Every answer traces to a DIKSHA source"
@pytest.mark.parametrize("question, chapter", [
    ("What is photosynthesis?", "Nutrition in Plants"),
    ("How do leaves make food for the plant?", "Nutrition in Plants"),
    ("How does heat travel through a metal spoon?", "Heat"),
    ("What is conduction?", "Heat"),
])
def test_teacher_gets_a_cited_answer(specialist, question, chapter):
    result = specialist.answer(question)
    assert not result.refused, result.reason
    assert result.answer_en.strip()
    assert result.citations[0].startswith("DIKSHA ")
    assert chapter in result.citations[0]


@pytest.mark.parametrize("question, intent", [
    ("Explain photosynthesis", "explain"),
    ("Simplify photosynthesis for a child", "simplify"),
    ("Which gas is released during photosynthesis?", "answer"),
])
def test_device_explains_simplifies_or_answers(specialist, question, intent):
    result = specialist.answer(question)
    assert not result.refused, result.reason
    assert result.intent == intent
    assert result.answer_en.strip()


# ------------------------------------- "The device refuses when content is missing"
@pytest.mark.parametrize("question", [
    "Who won the cricket world cup?",
    "What caused the French Revolution?",
    "How do I repair a car engine?",
    "What is the capital of France?",
    # Maths is not in the corpus, but these score as high as real science questions.
    "What is a fraction?",
    "Explain fractions",
    "What is a numerator?",
    "What is the Pythagoras theorem?",
])
def test_device_says_so_when_the_topic_is_not_covered(specialist, question):
    result = specialist.answer(question)
    assert result.refused
    assert result.answer_en == REFUSAL_TEXT
    assert result.citations == []


@pytest.mark.parametrize("question, wrong_chapters", [
    ("What is photosynthesis?", ["Respiration"]),
    ("What is respiration?", ["Nutrition in Plants", "Changes"]),
    ("What is speed?", ["Respiration", "Heat"]),
])
def test_device_does_not_cite_unrelated_chapters(specialist, question, wrong_chapters):
    citations = " ".join(specialist.answer(question).citations)
    assert citations
    for chapter in wrong_chapters:
        assert chapter not in citations


@pytest.mark.parametrize("question, passage, memory_answer", [
    ("How many chambers does the human heart have?",
     "The heart is a muscular organ. It pumps blood through arteries to all parts of the body.",
     "four"),
    # Known failure. The verifier accepts this common fact the passage never states.
    pytest.param("What gas do plants release during photosynthesis?",
                 "Photosynthesis takes place in the green leaves of a plant. Chlorophyll captures sunlight.",
                 "oxygen",
                 marks=pytest.mark.xfail(reason="The verifier accepts some common knowledge.", strict=False)),
    ("How many bones does an adult human have?",
     "The skeleton gives shape and support to the body. Bones protect delicate organs.",
     "206"),
])
def test_device_does_not_answer_from_memory(specialist, monkeypatch, question, passage, memory_answer):
    """The topic matches, but the approved passage does not contain the answer."""
    hit = Retrieved(0.95, passage, "uat", "uat", "uat", 1, "uat")
    monkeypatch.setattr(specialist.index, "search", lambda *args, **kwargs: [hit])
    result = specialist.answer(question)
    assert result.refused or memory_answer not in result.answer_en.lower(), result.answer_en


# These share a word with the corpus, so they pass the gate. The verifier must then
# stop the model's answer from memory.
@pytest.mark.parametrize("question, memory_answer", [
    ("What is the speed of light?", "kilomet"),
    ("What is a heat wave?", "weather"),
    ("What is the speed limit on a highway?", "law"),
])
def test_device_does_not_cite_a_chapter_for_an_answer_from_memory(specialist, question, memory_answer):
    result = specialist.answer(question)
    assert result.refused or memory_answer not in result.answer_en.lower(), result.answer_en


# Known failure. The answer is true to the passage but does not answer the question.
@pytest.mark.xfail(reason="The verifier checks support, not whether the question was answered.", strict=False)
def test_device_refuses_rather_than_answering_a_different_question(specialist):
    assert specialist.answer("how do I cook food").refused


# ---------------------------------------------------- "It runs fully offline"
def test_answering_makes_no_outside_connection(specialist, monkeypatch):
    real_connect = socket.socket.connect
    outside = []

    def connect(self, address):
        host = address[0] if isinstance(address, tuple) else "local"
        if host not in ("127.0.0.1", "::1", "localhost", "local"):
            outside.append(host)
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    assert not specialist.answer("What is photosynthesis?").refused
    assert outside == []


# ------------------------------------------- "The starter corpus covers one grade"
def test_every_passage_in_the_corpus_can_be_cited(specialist):
    for chunk in specialist.index.chunks:
        assert chunk["text"].strip()
        assert chunk.get("content_id") and chunk.get("chapter")


# ----------------------------------------- Teacher asks in Hindi, class hears Hindi
def test_hindi_request_gets_a_spoken_hindi_answer(specialist, bhashini_ready):
    from pocketinfer.models.nmt import Nmt
    from pocketinfer.models.tts import Tts

    nmt, tts = Nmt(), Tts()
    specialist.mt = lambda text, src, tgt: nmt.infer(text, src.lower(), tgt.lower())["translated_text"]
    specialist.tts = lambda text, lang: base64.b64decode(tts.infer(text, lang)["audio_base64"])

    question = nmt.infer("प्रकाश संश्लेषण क्या है?", "hi", "en")["translated_text"]
    result = specialist.answer(question, target_language="hi")
    assert not result.refused, result.reason
    assert any("ऀ" <= c <= "ॿ" for c in result.answer_localized)
    assert result.audio[:4] == b"RIFF"
    assert result.citations


def test_refusal_is_spoken_in_hindi_too(specialist, bhashini_ready):
    from pocketinfer.models.nmt import Nmt
    from pocketinfer.models.tts import Tts

    nmt, tts = Nmt(), Tts()
    specialist.mt = lambda text, src, tgt: nmt.infer(text, src.lower(), tgt.lower())["translated_text"]
    specialist.tts = lambda text, lang: base64.b64decode(tts.infer(text, lang)["audio_base64"])

    result = specialist.answer("Who won the cricket world cup?", target_language="hi")
    assert result.refused
    assert any("ऀ" <= c <= "ॿ" for c in result.answer_localized)
    assert result.audio[:4] == b"RIFF"


# ---------------------------------- "The full loop runs under roughly 2-3 seconds"
def test_typed_answer_starts_within_the_target_on_the_device(specialist, jetson):
    specialist.answer("What is speed?")  # the first call pays the model load
    started = time.time()
    next(specialist.stream_answer("What is photosynthesis?"))
    assert time.time() - started < 3.0


def test_full_hindi_voice_loop_meets_the_target_on_the_device(
        fixtures_dir, shipped_index_dir, bhashini_ready, jetson):
    """Speech in, spoken answer ready. Measures the loop as the voice command reports it."""
    result = run_command(
        "-m", "local.voice_loop", "--in-lang", "hi", "--out-lang", "hi", "--stream",
        "--from-wav", os.path.join(fixtures_dir, "question_hi.wav"), "--llm", "ollama")
    assert result.returncode == 0, result.stderr[-500:]
    assert "first_audio=" in result.stdout, result.stdout[-500:]
    first_audio_ms = int(result.stdout.split("first_audio=")[1].split("ms")[0])
    assert first_audio_ms < 3000
