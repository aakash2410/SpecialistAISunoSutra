"""Concrete providers for the laptop harness + name-keyed factories.

Every provider implements a contract from ``pocketinfer.specialist.providers``,
so they are interchangeable. Pick them by name on the CLI:

    --asr vosk|bhashini
    --mt  none|bhashini            (MT is optional)
    --tts say|bhashini
    --llm extractive|ollama

The ``bhashini`` variants reuse the device's HTTP model wrappers unchanged, so
the *same* voice loop runs on-device by selecting them (they talk to the
BHASHINI service on localhost:11400). The ``vosk`` / ``say`` / ``none`` /
``extractive`` variants are the offline laptop defaults.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess

from pocketinfer.specialist.providers import AudioClip
from pocketinfer.specialist.pipeline import extractive_llm

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- ASR
class VoskASR:
    name = "vosk"

    def __init__(self, model_path: str | None = None):
        try:
            from vosk import Model  # noqa: F401
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                "vosk not installed. Install laptop deps: "
                "pip install -r local/requirements-local.txt\n"
                f"(error: {e})"
            )
        from vosk import Model

        path = model_path or self._locate_model()
        if not path or not os.path.isdir(path):
            raise RuntimeError(
                "Vosk model not found. Download a small model and set VOSK_MODEL_PATH, e.g.:\n"
                "  mkdir -p local/models && cd local/models\n"
                "  curl -LO https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip\n"
                "  unzip vosk-model-small-en-us-0.15.zip\n"
                "Then re-run (or pass --vosk-model <path>)."
            )
        logger.info("Loading Vosk model from %s", path)
        self._model = Model(path)

    @staticmethod
    def _locate_model() -> str | None:
        if os.environ.get("VOSK_MODEL_PATH"):
            return os.environ["VOSK_MODEL_PATH"]
        here = os.path.dirname(__file__)
        candidates = [
            os.path.join(here, "models", "vosk-model-small-en-us-0.15"),
            os.path.join(here, "models", "vosk-model-small-hi-0.22"),
        ]
        for c in candidates:
            if os.path.isdir(c):
                return c
        # any vosk-model-* directory under local/models
        models_dir = os.path.join(here, "models")
        if os.path.isdir(models_dir):
            for d in sorted(os.listdir(models_dir)):
                if d.startswith("vosk-model") and os.path.isdir(os.path.join(models_dir, d)):
                    return os.path.join(models_dir, d)
        return None

    def transcribe(self, clip: AudioClip, language: str = "en") -> str:
        from vosk import KaldiRecognizer

        rec = KaldiRecognizer(self._model, clip.sample_rate)
        rec.AcceptWaveform(clip.pcm)
        result = json.loads(rec.FinalResult())
        return result.get("text", "").strip()


class BhashiniASR:
    name = "bhashini"

    def __init__(self):
        from pocketinfer.models.asr import Asr

        self._asr = Asr()

    def transcribe(self, clip: AudioClip, language: str = "en") -> str:
        return self._asr.infer(clip.get_wav_data(), language).get("text", "").strip()


# ---------------------------------------------------------------------------- MT
class NoopMT:
    """Passthrough — MT is optional. Keeps the loop running English-only."""

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

    @staticmethod
    def _code(lang: str) -> str:
        # Match the platform convention: English is "EN", other languages are the
        # lowercase ISO code (e.g. "hi"). Uppercasing "hi" -> "HI" 500s the model.
        return "EN" if lang.lower() == "en" else lang.lower()

    def translate(self, text: str, src_lang: str, tgt_lang: str) -> str:
        if src_lang.lower() == tgt_lang.lower():
            return text
        src, tgt = self._code(src_lang), self._code(tgt_lang)
        try:
            return self._nmt.infer(text, src, tgt)["translated_text"]
        except Exception as e:  # noqa: BLE001
            # BHASHINI NMT is sentence-tuned and can 500 on a long/multiline
            # paragraph — translate sentence by sentence and stitch back.
            logger.warning("NMT failed on full text (%s); retrying per sentence", e)
            sentences = [s.strip() for s in re.split(r"(?<=[.!?।])\s+", text) if s.strip()]
            out = []
            for s in sentences:
                try:
                    out.append(self._nmt.infer(s, src, tgt)["translated_text"])
                except Exception:  # noqa: BLE001
                    out.append(s)  # leave that sentence untranslated rather than fail
            return " ".join(out)


# --------------------------------------------------------------------------- TTS
# macOS 'say' voices per language (fall back to default voice if unavailable).
_SAY_VOICES = {"hi": "Lekha", "en": None}


class MacSayTTS:
    name = "say"

    def __init__(self):
        if not _which("say"):
            raise RuntimeError("macOS 'say' not found — use --tts bhashini or run on macOS.")

    def _voice(self, language: str) -> str | None:
        return _SAY_VOICES.get(language.lower())

    def speak(self, text: str, language: str = "en") -> None:
        if not text:
            return
        cmd = ["say"]
        voice = self._voice(language)
        if voice:
            cmd += ["-v", voice]
        cmd.append(text)
        # If the chosen voice isn't installed, retry without it.
        if subprocess.run(cmd).returncode != 0 and voice:
            subprocess.run(["say", text])

    def synthesize(self, text: str, language: str = "en"):
        import tempfile

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        cmd = ["say", "--file-format=WAVE", "--data-format=LEI16@22050", "-o", tmp.name]
        voice = self._voice(language)
        if voice:
            cmd += ["-v", voice]
        cmd.append(text)
        subprocess.run(cmd, check=False)
        with open(tmp.name, "rb") as f:
            data = f.read()
        os.unlink(tmp.name)
        return data


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


# --------------------------------------------------------------------------- LLM
def make_llm(name: str, ollama_model: str = "qwen3-vl:2b"):
    if name == "extractive":
        return extractive_llm
    if name == "ollama":
        from pocketinfer.specialist.llm import ollama_grounded_client

        return ollama_grounded_client(ollama_model)
    raise ValueError(f"Unknown llm '{name}' (choose: extractive, ollama)")


# ------------------------------------------------------------------- factories
def _which(cmd: str) -> bool:
    from shutil import which

    return which(cmd) is not None


ASR_PROVIDERS = {"vosk": VoskASR, "bhashini": BhashiniASR}
MT_PROVIDERS = {"none": NoopMT, "bhashini": BhashiniMT}
TTS_PROVIDERS = {"say": MacSayTTS, "bhashini": BhashiniTTS}


def make_asr(name: str, vosk_model: str | None = None):
    if name == "vosk":
        return VoskASR(model_path=vosk_model)
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
