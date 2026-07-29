"""End-to-end SpecialistEngine loop: retrieve -> ground -> answer / refuse,
plus the MT and TTS injection points and timings.
"""

from pocketinfer.specialist.grounding import REFUSAL_TEXT


def test_in_corpus_question_is_answered_with_citations(engine):
    r = engine.answer("What is photosynthesis?")
    assert not r.refused
    assert r.intent == "explain"
    assert "Photosynthesis" in r.answer_en
    assert r.citations and any("Nutrition in Plants" in c or "Photosynthesis" in c for c in r.citations)
    assert r.passages[0].score >= 0.10


def test_off_syllabus_question_is_refused(engine):
    r = engine.answer("Tell me about the French Revolution")
    assert r.refused
    assert r.answer_en == REFUSAL_TEXT
    assert r.citations == []
    assert "similarity" in r.reason.lower()


def test_timings_reported(engine):
    r = engine.answer("How is heat transferred?")
    assert {"embed", "retrieve"} <= set(r.timings)
    assert all(v >= 0 for v in r.timings.values())


def test_generation_gate_refusal_from_llm(make_engine):
    """Even with a passage retrieved, if the LLM says it can't answer, we refuse."""
    engine = make_engine(llm=lambda system, user: "NO_ANSWER_IN_SOURCE")
    r = engine.answer("What is photosynthesis?")
    assert r.refused
    assert "insufficient" in r.reason.lower() or "source" in r.reason.lower()


def test_mt_hook_localizes_the_answer(make_engine):
    engine = make_engine(mt=lambda text, src, tgt: f"[{tgt}]{text}")
    r = engine.answer("What is photosynthesis?", target_language="hi")
    assert not r.refused
    assert r.answer_localized.startswith("[hi]")
    assert r.spoken_text.startswith("[hi]")
    assert not r.answer_en.startswith("[hi]"), "English answer must remain untranslated"


def test_mt_hook_localizes_the_refusal(make_engine):
    engine = make_engine(mt=lambda text, src, tgt: f"<{tgt}>{text}")
    r = engine.answer("Tell me about cricket rules", target_language="hi")
    assert r.refused
    assert r.answer_localized.startswith("<hi>")


def test_tts_hook_receives_spoken_text(make_engine):
    captured = {}

    def tts(text, lang):
        captured["text"], captured["lang"] = text, lang
        return b"FAKEWAV"

    engine = make_engine(tts=tts)
    r = engine.answer("How is heat transferred?", target_language="en")
    assert r.audio == b"FAKEWAV"
    assert captured["text"] == r.spoken_text
    assert captured["lang"] == "en"


def test_min_score_defaults_from_manifest(engine):
    # TF-IDF index bakes recommended_min_score=0.10 into the manifest.
    assert engine.min_score == 0.10


# --- streaming API ----------------------------------------------------------
def test_stream_answer_yields_sentences_with_citations(engine):
    from pocketinfer.specialist import SentenceResult

    results = list(engine.stream_answer("What is photosynthesis?"))
    assert len(results) >= 1
    assert all(isinstance(r, SentenceResult) for r in results)
    assert not results[0].refused
    assert results[0].citations  # first item carries citations
    assert all("." in r.text_en or "!" in r.text_en or r.index == len(results) - 1 for r in results)


def test_stream_answer_refuses_off_syllabus(engine):
    results = list(engine.stream_answer("Tell me about the French Revolution"))
    assert len(results) == 1
    assert results[0].refused


def test_stream_answer_localizes_and_synthesizes(make_engine):
    engine = make_engine(
        mt=lambda text, src, tgt: f"[{tgt}]{text}",
        tts=lambda text, lang: b"WAV:" + text.encode(),
    )
    results = list(engine.stream_answer("How is heat transferred?", target_language="hi"))
    assert results and not results[0].refused
    assert results[0].text_localized.startswith("[hi]")
    assert results[0].audio.startswith(b"WAV:[hi]")


def test_speak_stream_overlaps_and_reports_first_audio(make_engine):
    from pocketinfer.specialist import speak_stream

    engine = make_engine(tts=lambda text, lang: b"AUDIO")
    played = []
    results, first_audio = speak_stream(
        engine.stream_answer("What is photosynthesis?"),
        play_fn=lambda b: played.append(b),
    )
    assert len(results) >= 1
    assert len(played) == len(results)      # every sentence's audio was played
    assert first_audio is not None and first_audio >= 0
