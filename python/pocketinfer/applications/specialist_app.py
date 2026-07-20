"""The Specialist — Suno Sutra application (on-device runtime).

Adds a "Specialist" mode plus a camera/OCR capture path on top of the existing
voice path, and runs the fully offline answer loop:

    button press -> capture (voice ASR or camera OCR)
        -> translate request to English (if needed)
        -> SpecialistEngine: embed -> retrieve -> ground -> grounded LLM
        -> translate answer to the output language (MT) -> speak (TTS)

Every answer is grounded in the baked DIKSHA index and cites its source on
screen; when the corpus doesn't cover the request the device says so out loud.

This app is auto-registered by ``pocketinfer.applications`` (the package __init__
imports every module so the @RegisterApplication decorators run), so it appears
in ``pocketinfer-service --list-apps`` with no extra wiring. It follows the same
structure and HAL usage as the platform's ``hear_the_world`` sample.
"""

import base64
import os
import time
import wave
import threading
from io import BytesIO

from pocketinfer.applications.base import BaseApplication
from pocketinfer.applications.registry import RegisterApplication

from pocketinfer.models.vosk import Vosk
from pocketinfer.models.asr import Asr
from pocketinfer.models.nmt import Nmt
from pocketinfer.models.tts import Tts
from pocketinfer.models.ocr import Ocr
from pocketinfer.models.embed import Embed
from pocketinfer.audio import AudioPlayer

from pocketinfer.specialist import SpecialistEngine
from pocketinfer.specialist.llm import ollama_grounded_client

# Content lives in a data directory separate from the code, so the syllabus can be
# refreshed without redeploying the module. Override per-device with:
#   pocketinfer-service --setting index_dir=/path/to/index
DEFAULT_INDEX = os.environ.get(
    "SPECIALIST_INDEX_DIR",
    "/opt/specialist/corpus/diksha_g7_science/index",
)


@RegisterApplication({
    "name": "The Specialist",
    "description": "A grounded, offline subject expert. Ask by voice or camera; "
                   "answers come only from approved DIKSHA content.",
    "author": "PocketInfer",
    "version": "1.0.0",
    "models": {
        # Reuse the platform's stock quantised LLM (same model HearTheWorld ships
        # with) — grounding is done purely via the prompt, so no new model pull is
        # needed. Swap for any stock text-capable LLM present on the device.
        "ollama": {"model_name": "qwen3-vl:2b"},
        "vosk": {"model_name": "vosk-model-small-en-us-0.15"},
        "asr": {},
        "nmt": {},
        "tts": {},
        "ocr": {},
        "embed": {"model_name": "intfloat/multilingual-e5-small"},
    },
    "default_settings": {
        "input_language": "hi",
        "output_language": "hi",
        "input_mode": "voice",          # 'voice' (ASR) or 'camera' (OCR)
        "index_dir": DEFAULT_INDEX,
    },
    "service_dependencies": ["ollama", "bashini_models"],
})
class TheSpecialist(BaseApplication):
    def start(self):
        self.vosk = Vosk(model_name=self.METADATA["models"]["vosk"]["model_name"])
        self.asr = Asr()
        self.nmt = Nmt()
        self.tts = Tts()
        self.ocr = Ocr()
        self.embedder = Embed(model_name=self.METADATA["models"]["embed"]["model_name"])

        llm = ollama_grounded_client(self.METADATA["models"]["ollama"]["model_name"])
        # MT + TTS are wired into the engine; the engine returns localized text
        # and synthesized audio, the app just plays it.
        self.engine = SpecialistEngine(
            index_dir=self.settings["index_dir"],
            embedder=self.embedder,
            llm=llm,
            mt=lambda text, src, tgt: self.nmt.infer(text, src, tgt)["translated_text"],
            tts=lambda text, lang: base64.b64decode(self.tts.infer(text, lang)["audio_base64"]),
        )
        self.board.subscribe_to_ui(self.ui_cb)
        super().start()

    def ui_cb(self, msg):
        if msg.startswith("ASR"):
            self.settings["input_language"] = msg[4:].lower()
        elif msg.startswith("TTS"):
            self.settings["output_language"] = msg[4:].lower()
        elif msg.startswith("MODE"):
            self.settings["input_mode"] = msg[5:].lower()

    def delayed(self, fn, *fa, delay=1.0):
        threading.Thread(
            target=lambda: (time.sleep(delay), fn(*fa)), daemon=True
        ).start()

    def _capture_request(self):
        """Return (request_text_en, raw_text) from voice or camera."""
        in_lang = self.settings["input_language"]
        if self.settings["input_mode"] == "camera":
            self.board.statusbar("Running: OCR")
            img = self.board.camera_frame_jpg()
            raw = self.ocr.infer(img, in_lang).get("text", "").strip()
        else:
            self.board.statusbar("Running: ASR")
            if in_lang != "en":
                wav = self.board.audio.to_audio_data().get_wav_data()
                raw = self.asr.infer(wav, in_lang).get("text", "").strip()
            else:
                raw = self.vosk.recognize(self.board.audio.to_audio_data())["text"].strip()

        if not raw:
            return "", ""
        if in_lang != "en":
            self.board.statusbar(f"Running: NMT {in_lang} -> en")
            request_en = self.nmt.infer(raw, in_lang, "EN")["translated_text"]
        else:
            request_en = raw
        return request_en, raw

    def run(self):
        self.logger.debug("Specialist starting with settings: %s", self.settings)
        while self.running:
            try:
                mode = self.settings["input_mode"]
                self.board.statusbar(f"Ready ({mode}) - Press Button")
                self.board.mode_text(f"Specialist · {mode}")
                if hasattr(self.board, "UI"):
                    self.board.UI.force_refresh()
                self.board.wait_for_trigger_button_down()
                self.board.statusbar("Release Button")
                self.board.top_text("")
                self.board.bottom_text("")
                self.board.led_animation(1)

                # Voice mode records while the button is held; camera mode snaps on release.
                if mode == "voice":
                    self.board.audio.start()
                self.board.wait_for_trigger_button_up()
                if mode == "voice":
                    self.board.audio.stop()

                request_en, raw = self._capture_request()
                if not request_en:
                    self.board.statusbar("Didn't catch that — try again")
                    self.board.led_animation(0)
                    continue
                self.board.top_text(raw or request_en)
                self.logger.info("Request (en): %s", request_en)

                self.board.statusbar("Running: Retrieve + LLM")
                result = self.engine.answer(
                    request_en, target_language=self.settings["output_language"]
                )

                if result.refused:
                    self.board.bottom_text("⚠ " + result.answer_localized)
                    self.logger.info("Refused: %s", result.reason)
                else:
                    self.board.bottom_text(result.spoken_text)
                    src = result.citations[0] if result.citations else ""
                    self.delayed(self.board.statusbar, "Source: " + src, delay=0.1)
                    self.logger.info("Answer: %s | sources=%s", result.answer_en, result.citations)

                # Speak the answer (engine already synthesized it via the TTS client).
                self.board.statusbar("Running: Playback")
                self.board.led_animation(0)
                if result.audio:
                    wav_obj = wave.open(BytesIO(result.audio), "rb")
                    with AudioPlayer(wav_obj.getframerate(), self.board.ALSA_PLAYBACK_DEVICE) as player:
                        player.play(wav_obj.readframes(wav_obj.getnframes()))

                self.logger.debug("Timings: %s", result.timings)
            except KeyboardInterrupt:
                self.logger.info("Exit")
                self.board.clear_screen()
                self.running = False
            except Exception as e:  # noqa: BLE001
                self.logger.exception("Error in Specialist loop: %s", e)
                self.board.statusbar("Error: {}".format(str(e)))
                time.sleep(1)
