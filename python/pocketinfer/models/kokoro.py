"""Speech output: Kokoro-82M on ONNX, on the CPU, in American English and Hindi.

The kokoro-onnx package needs NumPy 2, but the Specialist environment has to stay below
it, so this runs the same model files directly. Text becomes phonemes with espeak-ng,
phonemes become token ids, and the model turns those and a voice into audio.
"""

from __future__ import annotations

import io
import os
import wave
from typing import Dict, List, Optional

import numpy as np

SAMPLE_RATE = 24000
MODEL_FILE = "kokoro-v1.0.int8.onnx"
VOICES_FILE = "voices-v1.0.bin"
_DIR_ENV = "SPECIALIST_KOKORO_DIR"
# The model takes at most this many phonemes at once.
_MAX_PHONEMES = 510
_ESPEAK_LANGUAGE = {"en": "en-us", "hi": "hi"}
_DEFAULT_VOICE = {"en": "af_heart", "hi": "hf_alpha"}
_VOCAB = {";": 1, ":": 2, ",": 3, ".": 4, "!": 5, "?": 6, "—": 9, "…": 10, "\"": 11, "(": 12, ")": 13, "“": 14, "”": 15, " ": 16, "̃": 17, "ʣ": 18, "ʥ": 19, "ʦ": 20, "ʨ": 21, "ᵝ": 22, "ꭧ": 23, "A": 24, "I": 25, "O": 31, "Q": 33, "S": 35, "T": 36, "W": 39, "Y": 41, "ᵊ": 42, "a": 43, "b": 44, "c": 45, "d": 46, "e": 47, "f": 48, "h": 50, "i": 51, "j": 52, "k": 53, "l": 54, "m": 55, "n": 56, "o": 57, "p": 58, "q": 59, "r": 60, "s": 61, "t": 62, "u": 63, "v": 64, "w": 65, "x": 66, "y": 67, "z": 68, "ɑ": 69, "ɐ": 70, "ɒ": 71, "æ": 72, "β": 75, "ɔ": 76, "ɕ": 77, "ç": 78, "ɖ": 80, "ð": 81, "ʤ": 82, "ə": 83, "ɚ": 85, "ɛ": 86, "ɜ": 87, "ɟ": 90, "ɡ": 92, "ɥ": 99, "ɨ": 101, "ɪ": 102, "ʝ": 103, "ɯ": 110, "ɰ": 111, "ŋ": 112, "ɳ": 113, "ɲ": 114, "ɴ": 115, "ø": 116, "ɸ": 118, "θ": 119, "œ": 120, "ɹ": 123, "ɾ": 125, "ɻ": 126, "ʁ": 128, "ɽ": 129, "ʂ": 130, "ʃ": 131, "ʈ": 132, "ʧ": 133, "ʊ": 135, "ʋ": 136, "ʌ": 138, "ɣ": 139, "ɤ": 140, "χ": 142, "ʎ": 143, "ʒ": 147, "ʔ": 148, "ˈ": 156, "ˌ": 157, "ː": 158, "ʰ": 162, "ʲ": 164, "↓": 169, "→": 171, "↗": 172, "↘": 173, "ᵻ": 177}


def _resolve_dir(explicit: Optional[str] = None) -> Optional[str]:
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(here, "..", "..", ".."))
    for candidate in (explicit, os.environ.get(_DIR_ENV),
                      os.path.join(repo_root, "local", "models", "kokoro"),
                      "/opt/specialist/models/kokoro"):
        if candidate and all(os.path.exists(os.path.join(candidate, f)) for f in (MODEL_FILE, VOICES_FILE)):
            return candidate
    return None


def _trim(audio: np.ndarray, threshold: float = 0.01, pad: int = SAMPLE_RATE // 50) -> np.ndarray:
    """Remove silence from both ends, because it delays each spoken sentence."""
    loud = np.flatnonzero(np.abs(audio) > threshold)
    return audio[max(loud[0] - pad, 0):loud[-1] + pad] if loud.size else audio


def _split(phonemes: str) -> List[str]:
    """Split on spaces so that each piece fits the model."""
    pieces, current = [], ""
    for word in phonemes.split(" "):
        if current and len(current) + len(word) + 1 > _MAX_PHONEMES:
            pieces.append(current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    return pieces + ([current] if current else [])


class Kokoro:
    def __init__(self, model_dir: Optional[str] = None):
        import espeakng_loader
        import onnxruntime as ort
        from phonemizer.backend.espeak.wrapper import EspeakWrapper

        found = _resolve_dir(model_dir)
        if not found:
            raise RuntimeError(f"Kokoro not found. Put {MODEL_FILE} and {VOICES_FILE} in "
                               f"local/models/kokoro or set {_DIR_ENV}.")
        EspeakWrapper.set_library(espeakng_loader.get_library_path())
        EspeakWrapper.set_data_path(espeakng_loader.get_data_path())
        self._session = ort.InferenceSession(os.path.join(found, MODEL_FILE),
                                             providers=["CPUExecutionProvider"])
        self._inputs = {i.name: i.type for i in self._session.get_inputs()}
        self._voices = np.load(os.path.join(found, VOICES_FILE))
        self._phonemizers: Dict[str, object] = {}

    def _phonemes(self, text: str, language: str) -> str:
        from phonemizer.backend import EspeakBackend

        espeak = _ESPEAK_LANGUAGE.get(language)
        if espeak is None:
            raise ValueError(f"Kokoro does not speak '{language}'")
        if language not in self._phonemizers:
            # English words inside Hindi are read too, without espeak's language markers.
            self._phonemizers[language] = EspeakBackend(
                espeak, preserve_punctuation=True, with_stress=True, language_switch="remove-flags")
        return self._phonemizers[language].phonemize([text], strip=True)[0]

    def _voice(self, language: str, voice: Optional[str]) -> np.ndarray:
        name = voice or os.environ.get(f"SPECIALIST_VOICE_{language.upper()}") or _DEFAULT_VOICE[language]
        if name not in self._voices:
            raise ValueError(f"Kokoro has no voice '{name}'")
        return self._voices[name]

    def _infer(self, tokens: List[int], voice: np.ndarray, speed: float) -> np.ndarray:
        token_input = "input_ids" if "input_ids" in self._inputs else "tokens"
        speed_type = np.int32 if "int" in self._inputs["speed"] else np.float32
        return self._session.run(None, {
            token_input: np.array([[0, *tokens, 0]], dtype=np.int64),
            # There is one style per phoneme count, so n phonemes use row n - 1.
            "style": np.asarray(voice[min(len(tokens), len(voice)) - 1], dtype=np.float32),
            "speed": np.array([speed], dtype=speed_type),
        })[0].ravel()

    def synthesize(self, text: str, language: str = "en", voice: Optional[str] = None,
                   speed: float = 1.0) -> bytes:
        """Return mono 16-bit WAV bytes."""
        phonemes = "".join(p for p in self._phonemes(text, language) if p in _VOCAB)
        style = self._voice(language, voice)
        audio = [_trim(self._infer([_VOCAB[p] for p in piece], style, speed))
                 for piece in _split(phonemes) if piece.strip()]
        if not audio:
            raise ValueError(f"Nothing to say in {text!r}")
        samples = np.clip(np.concatenate(audio) * 32767, -32768, 32767).astype(np.int16)
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(samples.tobytes())
        return out.getvalue()
