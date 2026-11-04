"""System integration tests: do the real parts work together?

Each test crosses component boundaries using real parts. The only stand-ins are for
translation and speech in the tests marked as wiring, so that path stays covered on
machines without Bhashini. The live Bhashini tests below cover the real thing.
"""

import base64
import json
import os
import shutil

import numpy as np
import pytest

from conftest import CORPUS_DIR, run_command


def prints_grounded_answer(result):
    assert result.returncode == 0, result.stderr[-500:]
    assert "DIKSHA" in result.stdout, result.stdout[-500:]
    assert "REFUSED" not in result.stdout


# ---------------------------------------------------------- build to answer
@pytest.fixture(scope="module")
def built_index(tmp_path_factory, e5_ready):
    """Run the real ingest: raw DIKSHA text -> chunks -> e5 -> index on disk."""
    from ingest.build_index import build

    corpus = tmp_path_factory.mktemp("corpus") / "g7"
    shutil.copytree(os.path.join(CORPUS_DIR, "raw"), corpus / "raw")
    build(str(corpus), model_name="intfloat/multilingual-e5-small", max_words=120, overlap=25)
    return str(corpus / "index")


@pytest.fixture
def make_engine(built_index):
    from pocketinfer.specialist import SpecialistEngine

    def _make(**hooks):
        return SpecialistEngine(index_dir=built_index, **hooks)

    return _make


def test_freshly_built_index_answers_with_a_citation(make_engine):
    result = make_engine().answer("What is photosynthesis?")
    assert not result.refused
    assert "hotosynthesis" in result.answer_en
    assert any("Nutrition in Plants" in c for c in result.citations)


def test_freshly_built_index_refuses_off_syllabus(make_engine):
    result = make_engine().answer("Who won the cricket world cup?")
    assert result.refused
    assert result.citations == []


def test_retrieval_finds_the_right_chapter_for_a_paraphrase(make_engine):
    result = make_engine().answer("how does heat travel through a metal spoon")
    assert not result.refused
    assert "Heat" in result.citations[0]


def test_engine_refuses_when_the_model_says_the_source_lacks_the_answer(make_engine):
    engine = make_engine(llm=lambda system, user: "NO_ANSWER_IN_SOURCE")
    result = engine.answer("What is photosynthesis?")
    assert result.refused
    assert result.citations == []


# ---------------------------------------------------- engine wiring, stand-ins
def test_wiring_answer_goes_through_translation_then_speech(make_engine):
    spoken = {}

    def tts(text, lang):
        spoken.update(text=text, lang=lang)
        return b"WAV"

    engine = make_engine(mt=lambda text, src, tgt: f"[{tgt}]{text}", tts=tts)
    result = engine.answer("What is photosynthesis?", target_language="hi")
    assert result.answer_localized.startswith("[hi]")
    assert not result.answer_en.startswith("[hi]")
    assert spoken == {"text": result.answer_localized, "lang": "hi"}
    assert result.audio == b"WAV"


def test_wiring_refusal_is_also_translated_and_spoken(make_engine):
    engine = make_engine(mt=lambda text, src, tgt: f"[{tgt}]{text}", tts=lambda text, lang: b"WAV")
    result = engine.answer("Who won the cricket world cup?", target_language="hi")
    assert result.refused
    assert result.answer_localized.startswith("[hi]")
    assert result.audio == b"WAV"


def test_wiring_streaming_plays_every_sentence_in_order(make_engine):
    from pocketinfer.specialist import speak_stream

    engine = make_engine(tts=lambda text, lang: text.encode())
    played = []
    results, first_audio = speak_stream(engine.stream_answer("What is photosynthesis?"), played.append)
    assert results[0].citations
    assert played == [r.text_en.encode() for r in results]
    assert first_audio is not None


# ------------------------------------------------------- the shipped artefact
def test_shipped_index_is_consistent_and_built_with_e5(shipped_index_dir):
    embeddings = np.load(os.path.join(shipped_index_dir, "embeddings.npy"))
    with open(os.path.join(shipped_index_dir, "chunks.jsonl"), encoding="utf-8") as f:
        chunks = [json.loads(line) for line in f if line.strip()]
    with open(os.path.join(shipped_index_dir, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["embedder_backend"] == "onnx"
    assert embeddings.shape == (len(chunks), manifest["dim"])
    assert np.allclose(np.linalg.norm(embeddings, axis=1), 1.0, atol=1e-4)


# --------------------------------------------------------------- the commands
def test_voice_loop_command_answers_typed_text(shipped_index_dir):
    prints_grounded_answer(run_command(
        "-m", "local.voice_loop", "--text", "What is photosynthesis?",
        "--llm", "extractive", "--mt", "none", "--no-speak"))


def test_voice_loop_command_reads_a_page_image(shipped_index_dir, tesseract_ready, fixtures_dir):
    result = run_command(
        "-m", "local.voice_loop", "--from-image", os.path.join(fixtures_dir, "page_question.png"),
        "--llm", "extractive", "--mt", "none", "--no-speak")
    prints_grounded_answer(result)
    assert "photosynthesis" in result.stdout.split("[3 SPEC]")[0].lower()


def test_voice_loop_command_transcribes_english_speech(shipped_index_dir, whisper_ready, fixtures_dir):
    result = run_command(
        "-m", "local.voice_loop", "--from-wav", os.path.join(fixtures_dir, "question_en.wav"),
        "--llm", "extractive", "--mt", "none", "--no-speak")
    prints_grounded_answer(result)
    assert "photosynthesis" in result.stdout.split("[3 SPEC]")[0].lower()


def test_run_specialist_command_uses_the_real_model(shipped_index_dir, ollama_ready):
    prints_grounded_answer(run_command("run_specialist.py", "--model", ollama_ready,
                                       "What is photosynthesis?"))


# ------------------------------------------------------------- live Bhashini
def test_live_translation_and_speech_through_the_engine(make_engine, bhashini_ready):
    from pocketinfer.models.nmt import Nmt
    from pocketinfer.models.tts import Tts

    nmt, tts = Nmt(), Tts()
    engine = make_engine(
        mt=lambda text, src, tgt: nmt.infer(text, src.lower(), tgt.lower())["translated_text"],
        tts=lambda text, lang: base64.b64decode(tts.infer(text, lang)["audio_base64"]),
    )
    sentences = list(engine.stream_answer("What is photosynthesis?", target_language="hi"))
    assert any("ऀ" <= c <= "ॿ" for s in sentences for c in s.text_localized)
    assert all(s.audio[:4] == b"RIFF" for s in sentences)


def test_live_hindi_speech_input(fixtures_dir, shipped_index_dir, bhashini_ready):
    result = run_command(
        "-m", "local.voice_loop", "--in-lang", "hi", "--from-wav",
        os.path.join(fixtures_dir, "question_hi.wav"), "--llm", "extractive", "--no-speak")
    prints_grounded_answer(result)
