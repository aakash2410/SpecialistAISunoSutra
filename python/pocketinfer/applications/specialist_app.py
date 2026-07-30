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

from pocketinfer.specialist import SpecialistEngine, speak_stream
from pocketinfer.specialist.llm import ollama_grounded_client, ollama_grounded_stream_client

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
        # Small quantised TEXT LLM. The platform's stock qwen3-vl:2b is a vision
        # model — too heavy for the real-time grounded text loop on the Orin Nano
        # 8GB (2.8GB, spills to CPU, 60s+ reloads). llama3.2:1b runs 100% on GPU,
        # loads in ~8s, and grounds cleanly (grounding is prompt-based). Pull with:
        #   ollama pull llama3.2:1b
        "ollama": {"model_name": "llama3.2:1b"},
        # Whisper (faster-whisper) is the primary English ASR — context-aware, so
        # it handles science vocabulary the small Vosk model mangles. Vosk stays
        # as a fallback if Whisper isn't installed/downloaded.
        "whisper": {"model_size": "small.en"},
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

        # Whisper for English ASR, with a graceful fallback to Vosk.
        self.whisper = None
        try:
            from pocketinfer.models.whisper import Whisper

            self.whisper = Whisper(model_size=self.METADATA["models"]["whisper"]["model_size"])
            self.logger.info("English ASR: Whisper (%s)", self.METADATA["models"]["whisper"]["model_size"])
        except Exception as e:  # noqa: BLE001
            self.logger.warning("Whisper unavailable (%s); English ASR falls back to Vosk", e)

        model_name = self.METADATA["models"]["ollama"]["model_name"]
        # Streaming is the default: the engine emits the answer sentence by
        # sentence, and each sentence is translated + synthesized + spoken while
        # the next one is still being generated — so the class hears the first
        # sentence within ~a second or two instead of waiting for the whole answer.
        # (MT is called per sentence, which also sidesteps BHASHINI NMT truncating
        # a full paragraph.)
        self.engine = SpecialistEngine(
            index_dir=self.settings["index_dir"],
            embedder=self.embedder,
            llm=ollama_grounded_client(model_name),
            stream_llm=ollama_grounded_stream_client(model_name),
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

    def _play_wav(self, audio: bytes):
        """Play one WAV clip through the board speaker (used by speak_stream).

        Not every board exposes ALSA_PLAYBACK_DEVICE (the devboard doesn't), so
        resolve the device robustly and fall back to `aplay` (which handles the
        USB-audio mono->stereo conversion via plughw).
        """
        if not audio:
            return
        device = (os.environ.get("SPECIALIST_ALSA_DEVICE")
                  or getattr(self.board, "ALSA_PLAYBACK_DEVICE", None))
        wav_obj = wave.open(BytesIO(audio), "rb")
        if device:
            try:
                with AudioPlayer(wav_obj.getframerate(), device) as player:
                    player.play(wav_obj.readframes(wav_obj.getnframes()))
                return
            except Exception as e:  # noqa: BLE001
                self.logger.warning("AudioPlayer(%s) failed (%s); falling back to aplay", device, e)
        # Fallback: aplay to the configured device (plughw handles format conversion).
        import subprocess
        import tempfile

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        try:
            tmp.write(audio)
            tmp.close()
            dev = device or "plughw:0,0"
            if dev.startswith("hw:"):
                dev = "plug" + dev
            subprocess.run(["aplay", "-D", dev, tmp.name], check=False)
        finally:
            os.unlink(tmp.name)

    def _capture_request(self):
        """Return (request_text_en, raw_text) from voice or camera."""
        in_lang = self.settings["input_language"]
        if self.settings["input_mode"] == "camera":
            self.board.statusbar("Running: OCR")
            img = self.board.camera_frame_jpg()
            raw = self.ocr.infer(img, in_lang).get("text", "").strip()
        else:
            self.board.statusbar("Running: ASR")
            audio_data = self.board.audio.to_audio_data()
            if in_lang != "en":
                raw = self.asr.infer(audio_data.get_wav_data(), in_lang).get("text", "").strip()
            elif self.whisper is not None:
                # 16 kHz mono int16 PCM -> Whisper (context-aware, handles sci vocab)
                pcm = audio_data.get_raw_data(convert_rate=16000, convert_width=2)
                raw = self.whisper.transcribe(pcm, language="en").strip()
            else:
                raw = self.vosk.recognize(audio_data)["text"].strip()

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
                self.board.led_animation(0)
                out_lang = self.settings["output_language"]

                # Stream the answer: each sentence is spoken as it's ready, while
                # the next is still being produced. speak_stream plays audio on a
                # background thread; on_sentence updates the screen.
                def on_sentence(sr):
                    if sr.refused:
                        self.board.bottom_text("⚠ " + sr.spoken_text)
                        self.logger.info("Refused: %s", sr.reason)
                        return
                    self.board.bottom_text(sr.spoken_text)
                    if sr.index == 0 and sr.citations:
                        self.delayed(self.board.statusbar, "Source: " + sr.citations[0], delay=0.1)
                    self.logger.info("Sentence[%d]: %s", sr.index, sr.text_en)

                results, first_audio = speak_stream(
                    self.engine.stream_answer(request_en, target_language=out_lang),
                    self._play_wav, on_sentence,
                )
                self.logger.debug("Streamed %d sentences, first_audio=%.2fs",
                                  len(results), first_audio or 0.0)
            except KeyboardInterrupt:
                self.logger.info("Exit")
                self.board.clear_screen()
                self.running = False
            except Exception as e:  # noqa: BLE001
                self.logger.exception("Error in Specialist loop: %s", e)
                self.board.statusbar("Error: {}".format(str(e)))
                time.sleep(1)
