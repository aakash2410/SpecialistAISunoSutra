"""Choose the language of each request, so nobody has to set it.

Speech goes through two checks, cheapest first:

  1. The language ID model (about 50 ms) scores the clip against the languages this
     device has. If it is clearly an Indian language, that language's speech recognition
     runs straight away.
  2. Otherwise Whisper, the English speech recognition, transcribes it. If Whisper is
     confident, the request is English and that transcript is used, so English costs
     nothing extra. If Whisper is unsure, the clip goes to the Indian language after all.

The language ID model alone hears Indian English as Hindi, and Whisper's confidence alone
only just separates Hinglish from English. Together they separated all 54 synthetic test
clips (US, British and Indian English, Hindi, Hinglish). The thresholds below were set on
those clips and must be set again on real classroom recordings.

Typed questions and page photos are decided by their script. The answer comes back in the
language of the request, unless the request names one ("in English", "हिंदी में").
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Sequence, Tuple

# The language ID model's log-probability for the best Indian language, minus English's.
# At or above this, the clip is that language without asking Whisper. Synthetic Indian
# English reached 10.6 at most, and synthetic Hindi and Hinglish went as low as 7.0, so
# some Hindi clips still go through Whisper.
INDIC_SURE = 16.0
# Whisper's mean token log-probability. At or above this, the clip is English. Synthetic
# English was never below -0.42, and Hindi and Hinglish never above -0.50.
ENGLISH_SURE = -0.6

_DEVANAGARI_WORD = re.compile(r"[\u0900-\u097F]+")
_LATIN_WORD = re.compile(r"[A-Za-z]+")

_ASK_HINDI = re.compile(r"(\bin hindi\b|\bhindi (mein|me|men)\b|हिंदी में|हिन्दी में)", re.IGNORECASE)
_ASK_ENGLISH = re.compile(r"(\bin english\b|\benglish (mein|me|men)\b|अंग्रेज़ी में|अंग्रेजी में|इंग्लिश में)",
                          re.IGNORECASE)


def script_language(text: str) -> str:
    """'hi' when at least half the words are in Devanagari, otherwise 'en'.

    Words, not letters, so typed Hinglish such as "photosynthesis क्या होता है?" counts as
    Hindi, as it would be spoken.
    """
    hindi = len(_DEVANAGARI_WORD.findall(text or ""))
    english = len(_LATIN_WORD.findall(text or ""))
    return "hi" if hindi and hindi >= english else "en"


def requested_output(*texts: str) -> Optional[str]:
    """The answer language the request asks for by name, or None."""
    for text in texts:
        if not text:
            continue
        if _ASK_ENGLISH.search(text):
            return "en"
        if _ASK_HINDI.search(text):
            return "hi"
    return None


@dataclass
class Route:
    language: str
    text: str
    reason: str
    scores: Dict[str, float] = field(default_factory=dict)
    whisper_confidence: Optional[float] = None
    timings: Dict[str, float] = field(default_factory=dict)


class LanguageRouter:
    """Pick the speech recognition for a clip, and return its transcript.

    ``langid(audio_float32, languages) -> {language: log_prob}``,
    ``english(clip) -> (text, confidence)`` and ``indic(clip, language) -> text`` are the
    three parts, passed in so each can be swapped or faked.
    """

    def __init__(self, langid: Callable, english: Callable[..., Tuple[str, float]],
                 indic: Callable[..., str], languages: Sequence[str] = ("hi", "en"),
                 indic_sure: float = INDIC_SURE, english_sure: float = ENGLISH_SURE):
        self.langid, self.english, self.indic = langid, english, indic
        self.languages = tuple(languages)
        self.indic_languages = tuple(lang for lang in self.languages if lang != "en")
        self.indic_sure, self.english_sure = indic_sure, english_sure

    def route(self, clip) -> Route:
        timings = {}
        if not self.indic_languages:
            text, confidence = self.english(clip)
            return Route("en", text, "only English is installed", whisper_confidence=confidence)

        t0 = time.time()
        scores = self.langid(_float_audio(clip), self.languages)
        timings["langid"] = time.time() - t0
        best = max(self.indic_languages, key=lambda lang: scores.get(lang, float("-inf")))
        if "en" not in self.languages:
            t0 = time.time()
            text = self.indic(clip, best)
            timings["asr"] = time.time() - t0
            return Route(best, text, "English is not installed", scores, timings=timings)

        margin = scores.get(best, float("-inf")) - scores.get("en", float("-inf"))
        if margin >= self.indic_sure:
            t0 = time.time()
            text = self.indic(clip, best)
            timings["asr"] = time.time() - t0
            return Route(best, text, f"language ID sure it is {best} ({margin:.1f})", scores, timings=timings)

        t0 = time.time()
        text, confidence = self.english(clip)
        timings["whisper"] = time.time() - t0
        if confidence >= self.english_sure:
            return Route("en", text, f"Whisper sure it is English ({confidence:.2f})", scores, confidence, timings)

        t0 = time.time()
        text = self.indic(clip, best)
        timings["asr"] = time.time() - t0
        return Route(best, text, f"Whisper unsure ({confidence:.2f}), so {best}", scores, confidence, timings)


def _float_audio(clip):
    import numpy as np

    if (clip.sample_rate, clip.sample_width, clip.channels) != (16000, 2, 1):
        raise ValueError("language ID needs 16 kHz mono 16-bit audio, as the microphone records")
    return np.frombuffer(clip.pcm, dtype=np.int16).astype(np.float32) / 32768.0
