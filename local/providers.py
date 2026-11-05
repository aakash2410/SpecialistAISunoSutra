"""Concrete speech and translation providers, plus name-keyed factories.

Every provider implements a contract from ``pocketinfer.specialist.providers``, so
they are interchangeable. Pick them by name on the command line:

    --asr auto|bhashini|whisper    (auto: Whisper for English, Bhashini for Hindi)
    --mt  bhashini|none
    --tts kokoro|bhashini
    --llm ollama|extractive
"""

from __future__ import annotations

import logging
import re

from pocketinfer.specialist.glossary import apply_glossary, keep_terms
from pocketinfer.specialist.providers import AudioClip
from pocketinfer.specialist.pipeline import extractive_llm

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- ASR
class BhashiniASR:
    name = "bhashini"

    def __init__(self):
        from pocketinfer.models.asr import Asr

        self._asr = Asr()

    def transcribe(self, clip: AudioClip, language: str = "en") -> str:
        return self._asr.infer(clip.get_wav_data(), language).get("text", "").strip()


class WhisperASR:
    name = "whisper"

    def __init__(self, model_size: str = "tiny.en", model_dir: str | None = None,
                 task: str = "transcribe"):
        try:
            from pocketinfer.models.whisper import Whisper

            self._w = Whisper(model_size=model_size, model_dir=model_dir)
        except ImportError as e:
            raise RuntimeError(f"faster-whisper is not installed ({e})")
        self.task = task  # 'transcribe' | 'translate' (non-English speech -> English)

    def transcribe(self, clip: AudioClip, language: str = "en") -> str:
        audio = self._w.pcm16_to_float32(clip.pcm)
        return self._w.transcribe(audio, language=language, task=self.task)


# ---------------------------------------------------------------------------- MT
class NoopMT:
    """Passthrough, so the loop can run English-only without Bhashini."""

    name = "none"

    def translate(self, text: str, src_lang: str, tgt_lang: str) -> str:
        if src_lang.lower() != tgt_lang.lower():
            logger.debug("MT disabled (none): returning text untranslated %s->%s", src_lang, tgt_lang)
        return text


class BhashiniMT:
    name = "bhashini"

    def __init__(self):
        from pocketinfer.models.nmt import Nmt

        self._nmt = Nmt()

    def translate(self, text: str, src_lang: str, tgt_lang: str) -> str:
        # The Bhashini service only accepts lowercase language codes.
        src, tgt = src_lang.lower(), tgt_lang.lower()
        if src == tgt:
            return text
        terms = []
        if src == "hi" and tgt == "en":
            text, terms = apply_glossary(text)
        # ALWAYS translate sentence by sentence. BHASHINI NMT is sentence-tuned and
        # silently truncates a multi-sentence paragraph to the first segment (no
        # error), so a single call would drop most of the answer.
        sentences = [s.strip() for s in re.split(r"(?<=[.!?।])\s+", text.strip()) if s.strip()]
        if not sentences:
            return text
        out = []
        for s in sentences:
            try:
                out.append(self._nmt.infer(s, src, tgt)["translated_text"])
            except Exception as e:  # noqa: BLE001
                logger.warning("NMT failed on a sentence (%s); leaving it untranslated", e)
                out.append(s)
        return keep_terms(" ".join(out), terms)


# --------------------------------------------------------------------------- TTS
class BhashiniTTS:
    name = "bhashini"

    def __init__(self):
        import base64

        from pocketinfer.models.tts import Tts

        self._b64 = base64
        self._tts = Tts()

    def synthesize(self, text: str, language: str = "en"):
        return self._b64.b64decode(self._tts.infer(text, language)["audio_base64"])

    def speak(self, text: str, language: str = "en") -> None:
        from local.audio import play_wav_bytes

        play_wav_bytes(self.synthesize(text, language))


class KokoroTTS:
    name = "kokoro"

    def __init__(self):
        from pocketinfer.models.kokoro import Kokoro

        self._kokoro = Kokoro()

    def synthesize(self, text: str, language: str = "en"):
        return self._kokoro.synthesize(text, language.lower())

    def speak(self, text: str, language: str = "en") -> None:
        from local.audio import play_wav_bytes

        play_wav_bytes(self.synthesize(text, language))


# --------------------------------------------------------------------------- LLM
def make_llm(name: str, ollama_model: str = "qwen2.5:1.5b"):
    if name == "extractive":
        return extractive_llm
    if name == "ollama":
        from pocketinfer.specialist.llm import ollama_grounded_client

        return ollama_grounded_client(ollama_model)
    raise ValueError(f"Unknown llm '{name}' (choose: extractive, ollama)")


# ------------------------------------------------------------------- factories
ASR_PROVIDERS = {"bhashini": BhashiniASR, "whisper": WhisperASR}
MT_PROVIDERS = {"none": NoopMT, "bhashini": BhashiniMT}
TTS_PROVIDERS = {"bhashini": BhashiniTTS, "kokoro": KokoroTTS}


def make_asr(name: str, whisper_model: str = "tiny.en"):
    if name == "whisper":
        return WhisperASR(model_size=whisper_model)
    if name in ASR_PROVIDERS:
        return ASR_PROVIDERS[name]()
    raise ValueError(f"Unknown asr '{name}' (choose: {', '.join(ASR_PROVIDERS)})")


def make_mt(name: str):
    if name in MT_PROVIDERS:
        return MT_PROVIDERS[name]()
    raise ValueError(f"Unknown mt '{name}' (choose: {', '.join(MT_PROVIDERS)})")


def make_tts(name: str):
    if name in TTS_PROVIDERS:
        return TTS_PROVIDERS[name]()
    raise ValueError(f"Unknown tts '{name}' (choose: {', '.join(TTS_PROVIDERS)})")
