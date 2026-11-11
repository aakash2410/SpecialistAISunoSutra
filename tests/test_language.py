"""Working out the language of each request, so nobody has to set it."""

import os

import pytest

from pocketinfer.specialist.language import (
    ENGLISH_SURE, INDIC_SURE, LanguageRouter, requested_output, script_language,
)
from pocketinfer.specialist.providers import AudioClip


@pytest.mark.parametrize("text, language", [
    ("What is photosynthesis?", "en"), ("प्रकाश संश्लेषण क्या है?", "hi"),
    ("photosynthesis क्या होता है?", "hi"), ("Explain photosynthesis, प्रकाश", "en"), ("", "en"), ("123?", "en"),
])
def test_typed_text_and_pages_go_by_script(text, language):
    assert script_language(text) == language


@pytest.mark.parametrize("texts, language", [
    (("मुझे photosynthesis समझाइए, हिंदी में।",), "hi"), (("Explain it in Hindi",), "hi"),
    (("Explain photosynthesis to me, Hindi mein",), "hi"), (("Tell me in English",), "en"),
    (("इसे अंग्रेज़ी में बताइए",), "en"), (("What is photosynthesis?",), None),
    (("", "Explain it in English."), "en"), (("Who speaks Hindi in India?",), None),
])
def test_a_named_answer_language_wins(texts, language):
    assert requested_output(*texts) == language


CLIP = AudioClip(pcm=b"\0\0" * 16000)


class _Parts:
    def __init__(self, hi, en, whisper_confidence=-0.1):
        self.scores = {"hi": hi, "en": en}
        self.confidence = whisper_confidence
        self.calls = []

    def langid(self, audio, languages):
        self.calls.append("langid")
        assert len(audio) == 16000 and tuple(languages) == ("hi", "en")
        return dict(self.scores)

    def english(self, clip):
        self.calls.append("whisper")
        return "what is photosynthesis", self.confidence

    def indic(self, clip, language):
        self.calls.append(f"bhashini:{language}")
        return "प्रकाश संश्लेषण क्या है"

    def router(self):
        return LanguageRouter(self.langid, self.english, self.indic)


def test_clear_hindi_skips_whisper():
    parts = _Parts(hi=-0.1, en=-0.1 - INDIC_SURE - 1)
    route = parts.router().route(CLIP)
    assert route.language == "hi" and route.text == "प्रकाश संश्लेषण क्या है"
    assert parts.calls == ["langid", "bhashini:hi"]


def test_unclear_clip_that_whisper_is_sure_of_is_english_and_uses_its_transcript():
    parts = _Parts(hi=-0.3, en=-3.0, whisper_confidence=ENGLISH_SURE + 0.3)  # Indian English
    route = parts.router().route(CLIP)
    assert route.language == "en" and route.text == "what is photosynthesis"
    assert parts.calls == ["langid", "whisper"]


def test_unclear_clip_that_whisper_is_unsure_of_goes_to_hindi():
    parts = _Parts(hi=-0.3, en=-8.0, whisper_confidence=ENGLISH_SURE - 0.4)
    route = parts.router().route(CLIP)
    assert route.language == "hi" and parts.calls == ["langid", "whisper", "bhashini:hi"]


def test_english_only_device_never_runs_language_id():
    parts = _Parts(hi=0, en=0)
    route = LanguageRouter(parts.langid, parts.english, parts.indic, languages=("en",)).route(CLIP)
    assert route.language == "en" and parts.calls == ["whisper"]


def test_the_best_installed_indian_language_is_chosen():
    calls = []
    scores = {"hi": -9.0, "ta": -0.1, "en": -30.0}
    router = LanguageRouter(lambda audio, langs: scores, lambda clip: ("", -2.0),
                            lambda clip, lang: calls.append(lang) or "", languages=("hi", "ta", "en"))
    assert router.route(CLIP).language == "ta" and calls == ["ta"]


def test_audio_in_another_format_is_refused():
    with pytest.raises(ValueError):
        _Parts(0, 0).router().route(AudioClip(pcm=b"\0\0" * 8000, sample_rate=8000))


def test_the_onnx_model_hears_the_hindi_recording_as_hindi(langid_ready, fixtures_dir):
    from pocketinfer.models.langid import LanguageId
    from pocketinfer.specialist.language import _float_audio

    clip = AudioClip.from_wav_file(os.path.join(fixtures_dir, "question_hi.wav"))
    scores = LanguageId().log_probs(_float_audio(clip), ["hi", "en", "xx"])
    assert set(scores) == {"hi", "en"}
    assert scores["hi"] - scores["en"] >= INDIC_SURE


def test_indian_english_recording_is_routed_to_english(langid_ready, whisper_ready, fixtures_dir):
    """The language ID model alone calls this Indian English voice Hindi; Whisper settles it."""
    from local.providers import WhisperASR
    from pocketinfer.models.langid import LanguageId

    router = LanguageRouter(LanguageId().log_probs, WhisperASR().transcribe_scored,
                            lambda clip, lang: pytest.fail("should not reach Hindi speech recognition"))
    route = router.route(AudioClip.from_wav_file(os.path.join(fixtures_dir, "question_en.wav")))
    assert route.language == "en" and "photosynthesis" in route.text.lower()
    assert route.whisper_confidence is not None
