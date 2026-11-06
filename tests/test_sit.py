"""System integration tests: do the real parts work together?

Each test crosses component boundaries using real parts. The only stand-ins are for
translation and speech in the tests marked as wiring, so that path stays covered on
machines without Bhashini. The live Bhashini tests below cover the real thing.
"""

import base64
import json
import os
import re
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
def make_engine(built_index, verifier, safety_model):
    from pocketinfer.specialist import SpecialistEngine

    def _make(**hooks):
        return SpecialistEngine(index_dir=built_index, verifier=verifier, safety_model=safety_model, **hooks)

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


# ------------------------------------------------------------------ harvesting
def test_garbled_harvest_is_repaired_indexed_and_answered(tmp_path, tesseract_ready, e5_ready, verifier, safety_model):
    """A junk text layer goes through real OCR, into the real index, out as a cited answer."""
    fitz = pytest.importorskip("fitz")
    from ingest.build_index import build
    from ingest.diksha_pull import load_local, repair_documents
    from pocketinfer.specialist import SpecialistEngine

    def scanned_pdf(body):
        """A PDF holding only a picture of the text, like a scan."""
        drawn = fitz.open()
        drawn.new_page(width=600, height=400).insert_textbox((30, 30, 570, 370), body, fontsize=16)
        scan = fitz.open()
        scan.new_page(width=600, height=400).insert_image((0, 0, 600, 400), pixmap=drawn[0].get_pixmap(dpi=200))
        return scan.tobytes()

    body = ("Photosynthesis is the process by which green plants make their own food. "
            "The leaves of the plant use sunlight and water for this process, and it is "
            "how a plant gets all of the food that it needs in order to grow.")
    readable = ("Heat flows from a hotter object to a colder object, and this is what we "
                "call the transfer of heat. It is one of the ways in which energy moves.")
    source = {"page": 1, "chapter": "Science", "section": "page 1", "license": "CC BY 4.0"}
    docs = [
        {**source, "content_id": "good", "source": "good.pdf", "text": readable},
        {**source, "content_id": "scan", "source": "scan.pdf", "text": "\x02\x03\x04\x05\x06" * 40},
        {**source, "content_id": "blank", "source": "blank.pdf", "text": "\uf037\uf065\uf020" * 40},
        {**source, "content_id": "gone", "source": "gone.pdf", "text": "\x02\x03\x04\x05" * 40},
        {**source, "content_id": "repeat", "source": "repeat.pdf", "text": readable},
        {**source, "content_id": "plan", "source": "plan.pdf",
         "text": "Lesson Plan. " + readable + " The teacher will divide the class into groups."},
    ]

    def fetch(url):
        if url == "gone.pdf":
            raise OSError("not found")
        return scanned_pdf(body if url == "scan.pdf" else " ")

    kept, report = repair_documents(docs, fetch=fetch)
    assert [d["content_id"] for d in kept] == ["good", "scan"]
    assert "photosynthesis" in kept[1]["text"].lower()
    assert report == {"readable as harvested": 3, "recovered by OCR": 1,
                      "dropped: private-use glyphs": 1, "dropped: source unavailable": 1,
                      "dropped: duplicate": 1, "dropped: lesson plan or front matter": 1}

    corpus = tmp_path / "corpus"
    (corpus / "raw").mkdir(parents=True)
    (corpus / "raw" / "chapters.json").write_text(json.dumps({"collection": {}, "documents": kept}))
    assert len(load_local(str(corpus / "raw" / "chapters.json"))) == 2
    build(str(corpus), model_name="intfloat/multilingual-e5-small", max_words=120, overlap=25)
    result = SpecialistEngine(index_dir=str(corpus / "index"), verifier=verifier,
                              safety_model=safety_model).answer("What is photosynthesis?")
    assert not result.refused, result.reason
    assert "scan" in result.citations[0]


def test_repair_survives_a_corrupt_page_and_resumes(tmp_path, tesseract_ready, monkeypatch):
    """One bad page must not lose the book, and a second run must not repeat the OCR."""
    fitz = pytest.importorskip("fitz")
    from ingest import diksha_pull

    body = ("Heat flows from a hotter object to a colder object, and this is what we "
            "call the transfer of heat. It is one of the ways in which energy moves.")
    drawn = fitz.open()
    for _ in range(2):
        drawn.new_page(width=600, height=300).insert_textbox((30, 30, 570, 270), body, fontsize=16)
    scan = fitz.open()
    for page in drawn:
        scan.new_page(width=600, height=300).insert_image((0, 0, 600, 300), pixmap=page.get_pixmap(dpi=200))
    pdf = scan.tobytes()
    docs = [{"source": "book.pdf", "page": n, "text": "\x02\x03\x04\x05" * 40} for n in (1, 2)]

    real_get_pixmap = fitz.Page.get_pixmap

    def corrupt_first_page(self, *args, **kwargs):
        if self.number == 0:
            raise ValueError("object is not a stream")
        return real_get_pixmap(self, *args, **kwargs)

    monkeypatch.setattr(fitz.Page, "get_pixmap", corrupt_first_page)
    progress = str(tmp_path / "progress")
    kept, report = diksha_pull.repair_documents(docs, fetch=lambda url: pdf, progress_path=progress)
    assert [d["page"] for d in kept] == [2]
    assert "Heat" in kept[0]["text"]
    assert report == {"recovered by OCR": 1, "dropped: control characters": 1}

    def must_not_fetch(url):
        raise AssertionError("resumed run fetched a book it had already finished")

    kept, report = diksha_pull.repair_documents(docs, fetch=must_not_fetch, progress_path=progress)
    assert [d["page"] for d in kept] == [2]


# ------------------------------------------------- answers against the sources
SUPPORTED = "Photosynthesis is the process by which green plants make their own food."
INVENTED = "The Moon is made of green cheese."


def test_unsupported_sentence_is_dropped_and_the_rest_kept(make_engine):
    result = make_engine(llm=lambda system, user: f"{SUPPORTED} {INVENTED}").answer("What is photosynthesis?")
    assert not result.refused
    assert result.answer_en == SUPPORTED
    assert result.dropped_sentences == [INVENTED]
    assert "Photosynthesis" in result.citations[0]


def test_answer_with_no_supported_sentence_is_refused(make_engine):
    result = make_engine(llm=lambda system, user: INVENTED).answer("What is photosynthesis?")
    assert result.refused
    assert result.citations == []
    assert "supported" in result.reason


def test_streaming_drops_unsupported_sentences_before_speech(make_engine):
    spoken = []

    def tts(text, lang):
        spoken.append(text)
        return b"WAV"

    engine = make_engine(llm=lambda system, user: f"{INVENTED} {SUPPORTED}", tts=tts)
    results = list(engine.stream_answer("What is photosynthesis?"))
    assert [r.text_en for r in results] == [SUPPORTED]
    assert spoken == [SUPPORTED]
    assert results[0].citations


# ------------------------------------------------------------ follow-up questions
def test_follow_up_is_rewritten_from_memory_then_answered_like_any_question(make_engine):
    from pocketinfer.specialist.session import Session, ask

    engine = make_engine(llm=lambda system, user: SUPPORTED)
    session = Session()
    seen = []

    def rewrite(system, user):
        seen.append(user)
        return "Rewritten question: How do green plants make their own food?"

    first = list(ask(engine, session, "What is photosynthesis?", llm=rewrite))
    assert seen == [] and first[0].text_en == SUPPORTED
    second = list(ask(engine, session, "How does it work?", llm=rewrite))
    assert "Q: What is photosynthesis?" in seen[0] and SUPPORTED in seen[0]
    assert session.last_rewrite == "How do green plants make their own food?"
    assert second[0].text_en == SUPPORTED and second[0].citations


def test_memory_clears_after_silence_on_reset_and_after_a_safety_flag(make_engine):
    from pocketinfer.specialist.session import Session, ask

    now = [0.0]
    engine = make_engine(llm=lambda system, user: SUPPORTED)
    session = Session(clock=lambda: now[0])
    list(ask(engine, session, "What is photosynthesis?"))
    assert len(session.turns) == 1
    now[0] += 181
    session.expire_if_idle()
    assert not session.turns

    list(ask(engine, session, "What is photosynthesis?"))
    flagged = list(ask(engine, session, "I want to kill myself"))
    assert flagged[0].reason.startswith("safety:") and not session.turns
    assert Session.is_reset("New topic.") and Session.is_reset("नया सवाल") and not Session.is_reset("What is a new topic?")


def test_streaming_refuses_when_nothing_survives(make_engine):
    results = list(make_engine(llm=lambda system, user: INVENTED).stream_answer("What is photosynthesis?"))
    assert len(results) == 1
    assert results[0].refused


def test_only_the_passage_behind_the_answer_is_cited(make_engine):
    # Retrieval brings back several photosynthesis and plant passages for this one.
    answer = "Oxygen is released during this process."
    result = make_engine(llm=lambda system, user: answer).answer("how do leaves make food for the plant")
    assert result.answer_en == answer
    assert len(result.citations) == 1
    assert "Photosynthesis" in result.citations[0]


# ------------------------------------------------------------------ search
SPEED = "The speed of light is about 300,000 kilometres per second."


def _light_search(*args, **kwargs):
    """Three close passages that only mention light, then the one that answers."""
    from pocketinfer.specialist.vector_index import Retrieved

    def passage(score, text, page):
        return Retrieved(score, text, "do_light", "Light", "Light", page, "test")

    return [
        passage(0.90, "Light travels in a straight line. Shadows form when light is blocked.", 1),
        passage(0.89, "A mirror reflects light. The image in a plane mirror is upright.", 2),
        passage(0.89, "Light from the Sun has seven colours, which a prism can separate.", 3),
        passage(0.87, SPEED + " Nothing travels faster.", 4),
    ]


def _answers_only_from_the_speed_passage(system, user):
    from pocketinfer.specialist.grounding import REFUSAL_SENTINEL
    return SPEED if "300,000" in system else REFUSAL_SENTINEL


def test_a_refused_question_is_retried_once_on_the_passage_with_its_phrase(make_engine):
    calls = []

    def llm(system, user):
        calls.append(system)
        return _answers_only_from_the_speed_passage(system, user)

    engine = make_engine(llm=llm)
    engine.index.search = _light_search
    result = engine.answer("What is the speed of light?")
    assert not result.refused and result.answer_en == SPEED
    assert len(calls) == 2 and "300,000" not in calls[0]
    engine.llm = _answers_only_from_the_speed_passage
    streamed = list(engine.stream_answer("What is the speed of light?"))
    assert [s.text_en for s in streamed] == [SPEED]


def test_no_retry_when_re_ranking_finds_nothing_new_with_the_phrase(make_engine):
    calls = []

    def llm(system, user):
        calls.append(user)
        from pocketinfer.specialist.grounding import REFUSAL_SENTINEL
        return REFUSAL_SENTINEL

    engine = make_engine(llm=llm)
    engine.index.search = _light_search
    assert engine.answer("Why do shadows form behind objects?").refused
    assert len(calls) == 1


def test_questions_about_today_are_refused_before_search(make_engine):
    from pocketinfer.specialist.pipeline import asks_for_live_information

    engine = make_engine(llm=_must_not_run, stream_llm=_must_not_run)
    engine.index.search = _must_not_run
    for question in ("What is the price of gold today?", "Who is the latest prime minister?"):
        assert engine.answer(question).refused
        streamed = list(engine.stream_answer(question))
        assert len(streamed) == 1 and streamed[0].refused
    assert not asks_for_live_information("What is electric current?")


# ------------------------------------------------------------- safety check
def _must_not_run(*args):
    raise AssertionError("A flagged request reached the answer model")


def test_flagged_request_never_reaches_the_model_or_retrieval(make_engine):
    engine = make_engine(llm=_must_not_run, stream_llm=_must_not_run)
    engine.index.search = _must_not_run
    for question in ("My uncle touches me and I do not like it", "I want to kill myself"):
        result = engine.answer(question)
        assert result.refused and result.reason.startswith("safety:")
        assert result.citations == []
        streamed = list(engine.stream_answer(question))
        assert len(streamed) == 1 and streamed[0].refused and streamed[0].reason.startswith("safety:")


def test_safety_response_in_hindi_is_the_written_text_not_a_translation(make_engine):
    from pocketinfer.specialist.safety import RESPONSES

    engine = make_engine(mt=_must_not_run, tts=lambda text, lang: b"WAV")
    result = engine.answer("My teacher hits me every day", target_language="hi")
    assert result.answer_localized == RESPONSES["care"]["hi"]
    assert "1098" in result.answer_localized and result.audio == b"WAV"
    streamed = list(engine.stream_answer("I want to kill myself", target_language="hi"))
    assert streamed[0].text_localized == RESPONSES["self_harm"]["hi"]
    assert streamed[0].audio == b"WAV"


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
def test_full_index_leaves_out_garbled_scans_and_teacher_manuals():
    index = os.path.join(os.path.dirname(CORPUS_DIR), "diksha_g7_all", "index")
    with open(os.path.join(index, "chunks.jsonl"), encoding="utf-8") as f:
        chunks = [json.loads(line) for line in f]
    with open(os.path.join(index, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["num_chunks"] == len(chunks)
    assert manifest["removed"]["dropped: garbled text"] > 0
    assert not [c for c in chunks if re.search("teachher-manual|lesson-plan", c["source"])]


def test_hindi_maths_terms_reach_translation_in_english(monkeypatch):
    from local import providers

    sent = []

    class Translator:
        def infer(self, text, src, tgt):
            sent.append(text)
            return {"translated_text": "What is different?"}

    monkeypatch.setattr("pocketinfer.models.nmt.Nmt", Translator)
    mt = providers.BhashiniMT()
    # If the translator drops the term, it is added back so retrieval still sees the topic.
    assert mt.translate("भिन्न क्या होता है?", "HI", "EN") == "What is different? (fraction)"
    assert sent[-1] == "fraction क्या होता है?"
    mt.translate("दो भिन्न प्रकार के पौधे कौन से हैं?", "HI", "EN")
    assert sent[-1] == "दो भिन्न प्रकार के पौधे कौन से हैं?"
    mt.translate("क्या कास्ट सिस्टम अच्छा होता है?", "HI", "EN")
    assert sent[-1] == "क्या caste system अच्छा होता है?"
    mt.translate("चालन क्या है?", "HI", "EN")
    assert sent[-1] == "conduction क्या है?"
    mt.translate("वाहन चालन का लाइसेंस कैसे मिलता है?", "HI", "EN")
    assert sent[-1] == "वाहन चालन का लाइसेंस कैसे मिलता है?"
    assert mt.translate("भिन्न", "EN", "HI") == "What is different?"
    assert sent[-1] == "भिन्न"


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
