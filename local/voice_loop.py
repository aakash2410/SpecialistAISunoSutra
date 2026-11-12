"""Run the full Specialist loop on the device, with swappable providers.

    speech or page  ->  translate  ->  Specialist  ->  translate  ->  speech

Give it a request in any of these ways:
  * a WAV file:      python -m local.voice_loop --in-lang hi --from-wav question.wav
  * a page image:    python -m local.voice_loop --in-lang hi --from-image page.jpg
  * typed text:      python -m local.voice_loop --text "What is photosynthesis?"
                     (repeat --text to ask follow-ups in one session)
  * a USB mic:       python -m local.voice_loop --mic-device plughw:1,0

Swap any component by name (contracts live in pocketinfer.specialist.providers):
  --asr auto|whisper|bhashini   --mt bhashini|none   --tts bhashini   --llm ollama|extractive
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

import numpy as np

# Import the device runtime + providers from the bundled package.
sys.path.insert(0, os.path.join(os.path.abspath(os.path.dirname(__file__)), "..", "python"))
sys.path.insert(0, os.path.join(os.path.abspath(os.path.dirname(__file__)), ".."))

from pocketinfer.models.embed import Embed  # noqa: E402
from pocketinfer.specialist import SpecialistEngine  # noqa: E402

from local.providers import make_asr, make_mt, make_tts, make_llm  # noqa: E402


def _resolve_index() -> str:
    """Canonical index: $SPECIALIST_INDEX_DIR > the first baked index found, in the order
    local/corpus_index.py gives (diksha_g6_8_science, then diksha_g7_all)."""
    from local.corpus_index import resolve_index

    return resolve_index()


DEFAULT_INDEX = _resolve_index()


def _fmt_timings(t: dict) -> str:
    return ", ".join(f"{k}={v*1000:.0f}ms" for k, v in t.items() if v is not None)


class LoopHarness:
    screen = None  # the 2.8 inch screen (local.screen), when --screen is on

    def __init__(self, args):
        self.args = args
        self.in_lang = args.in_lang.lower()
        self.out_lang = args.out_lang.lower()
        # The languages of the current request and its answer. With "auto" they are worked
        # out for every request (see pocketinfer.specialist.language).
        self.turn_in = "en" if self.in_lang == "auto" else self.in_lang
        self.turn_out = self.turn_in if self.out_lang == "auto" else self.out_lang

        # Providers (each is swappable via the factory names).
        asr = args.asr
        self.router = None
        self.asr = None
        if not (args.text or args.from_image):
            if self.in_lang == "auto":
                self.router = self._make_router(args)
            else:
                if asr == "auto":
                    # Bhashini's speech model only covers Hindi, so English goes to tiny Whisper.
                    asr = "whisper" if self.in_lang == "en" else "bhashini"
                self.asr = make_asr(asr, whisper_model=args.whisper_model)
        self.mt = make_mt(args.mt)                       # used for query-side translation too
        self.tts_provider = None if args.no_speak else make_tts(args.tts)

        # Streaming LLM client (only ollama truly streams; other backends are
        # pseudo-streamed by sentence-splitting a full answer inside the engine).
        stream_llm = None
        if args.llm == "ollama":
            from pocketinfer.specialist.llm import ollama_grounded_stream_client

            stream_llm = ollama_grounded_stream_client(args.ollama_model)

        # The engine now owns MT + TTS (as callables wrapping the providers), so
        # both the sync and streaming paths go through the same formalized API.
        tts_cb = (lambda text, lang: self.tts_provider.synthesize(text, lang)) if self.tts_provider else None
        self.engine = SpecialistEngine(
            index_dir=args.index,
            embedder=Embed(model_name=args.embed_model),
            llm=make_llm(args.llm, ollama_model=args.ollama_model),
            stream_llm=stream_llm,
            mt=(lambda text, src, tgt: self.mt.translate(text, src, tgt)),
            tts=tts_cb,
        )
        from pocketinfer.specialist.session import Session

        self.session = Session()
        self.screen = None
        if args.screen:
            from local.screen import open_screen

            self.screen = open_screen("hi" if self.in_lang == "hi" else "en",
                                      touch="screen" in args.trigger)
        # Only a real answer model can rewrite follow-ups; the extractive one would echo a passage.
        self.rewrite_llm = self.engine.llm if args.llm == "ollama" else None
        self._texts = list(args.text or [])
        self._warm_up(args)
        asr_name = "auto (language ID, Whisper, Bhashini)" if self.router else getattr(self.asr, "name", "(none)")
        print(
            f"[providers] asr={asr_name}  mt={self.mt.name}  "
            f"tts={getattr(self.tts_provider,'name','(off)')}  llm={args.llm}  "
            f"in={self.in_lang} out={self.out_lang}"
        )
        if self.in_lang not in ("en", "auto") and self.mt.name == "none":
            print("[warn] input language is not English but MT is 'none' — the query "
                  "won't be translated before retrieval over the English corpus.")

    @staticmethod
    def _make_router(args):
        """Language ID first, then Whisper for English, else Bhashini (see language.py)."""
        from pocketinfer.models.langid import LanguageId
        from pocketinfer.specialist.language import LanguageRouter

        if args.asr != "auto":
            raise SystemExit("--in-lang auto picks the speech recognition itself, so leave --asr as auto")
        whisper = make_asr("whisper", whisper_model=args.whisper_model)
        bhashini = make_asr("bhashini")
        router = LanguageRouter(LanguageId().log_probs, whisper.transcribe_scored, bhashini.transcribe)
        router.parts = (whisper, bhashini)
        return router

    def _warm_up(self, args) -> None:
        """Run every part once before the first question, because first runs are slow.

        On the Jetson the first question took 24 s: about 20 s for Ollama to load the
        answer model and 2.4 s for the answer check's first run.
        """
        t0 = time.time()
        steps = []
        if args.llm == "ollama":
            from pocketinfer.specialist.llm import preload_ollama_model

            steps.append(("answer model", lambda: preload_ollama_model(args.ollama_model)))
        steps.append(("retrieval", lambda: self.engine.embedder.embed_one("warm up", kind="query")))
        steps.append(("answer check", lambda: self.engine.verifier.support_score("Plants make food.", "Plants make food.")))
        steps.append(("safety model", lambda: self.engine.safety.verifier.support_score("I feel fine.", "I feel fine.")))
        if self.in_lang != "en" or self.out_lang != "en":
            steps.append(("translation", lambda: self.mt.translate("Plants make food.", "en", "hi")))
        from pocketinfer.specialist.providers import AudioClip

        silence = AudioClip(pcm=b"\0\0" * 16000, sample_rate=16000)
        if self.asr is not None:
            steps.append(("speech input", lambda: self.asr.transcribe(silence, self.in_lang)))
        if self.router is not None:
            whisper, bhashini = self.router.parts
            steps.append(("language ID", lambda: self.router.langid(np.zeros(16000, np.float32), self.router.languages)))
            steps.append(("English speech input", lambda: whisper.transcribe(silence, "en")))
            steps.append(("Hindi speech input", lambda: bhashini.transcribe(silence, "hi")))
        for name, step in steps:
            try:
                step()
            except Exception as e:  # noqa: BLE001
                print(f"[warn] could not warm up the {name} ({e})")
        print(f"[ready] warmed up in {time.time() - t0:.1f} s")

    def _capture_text(self, mic) -> str:
        from pocketinfer.specialist.language import script_language

        auto = self.in_lang == "auto"
        if self.args.text:
            text = self._texts.pop(0) if self._texts else ""
            if auto:
                self.turn_in = script_language(text)
            return text
        t0 = time.time()
        if self.args.from_image:
            from pocketinfer.models.ocr import Ocr

            with open(self.args.from_image, "rb") as f:
                image = f.read()
            # Hindi pages carry English terms, so read with both packs. An English page is
            # then read again with English alone, which reads English text best.
            raw = Ocr().infer(image, "hi" if auto else self.in_lang)["text"]
            if auto:
                self.turn_in = script_language(raw)
                if self.turn_in == "en":
                    raw = Ocr().infer(image, "en")["text"]
            print(f"[1 OCR ] ({(time.time()-t0)*1000:.0f}ms) -> {raw!r}")
            from pocketinfer.specialist.page_questions import extract_questions_from_ocr, question_from_page

            question = question_from_page(raw, self.args.question_index)
            print(f"[1 PAGE] {len(extract_questions_from_ocr(raw))} question(s) found, using: {question!r}")
            return question
        clip = mic.record()
        t1 = time.time()
        if self.router is not None:
            route = self.router.route(clip)
            self.turn_in, raw = route.language, route.text
            print(f"[1 LANG] {route.language}: {route.reason} ({_fmt_timings(route.timings)})")
        else:
            raw = self.asr.transcribe(clip, self.in_lang)
        print(f"[1 ASR ] ({(time.time()-t1)*1000:.0f}ms, {clip.duration_seconds:.1f}s audio) -> {raw!r}")
        return raw

    def _choose_output(self, *texts: str) -> None:
        """Answer in the request's language, unless it asks for one by name."""
        if self.out_lang != "auto":
            self.turn_out = self.out_lang
            return
        from pocketinfer.specialist.language import requested_output

        self.turn_out = requested_output(*texts) or self.turn_in

    def _translate_query(self, raw: str, T: dict) -> str:
        """Translate the request into English for the Specialist, if needed."""
        if self.turn_in == "en":
            return raw
        ts = time.time()
        query_en = self.mt.translate(raw, self.turn_in, "en")
        T["mt_in"] = time.time() - ts
        print(f"[2 MT  ] {self.turn_in}->en -> {query_en!r}")
        return query_en

    NOTHING_TO_REPEAT = {"en": "There is nothing to repeat yet. Please ask a question.",
                         "hi": "अभी दोहराने के लिए कुछ नहीं है। कृपया एक सवाल पूछिए।"}

    def _replay(self, command: str) -> bool:
        """Say the last answer again, slower and with pauses if asked, without asking again."""
        self.session.expire_if_idle()
        if not self.session.last_text:
            text = self.NOTHING_TO_REPEAT.get(self.turn_out, self.NOTHING_TO_REPEAT["en"])
            print(f"[{command}] nothing to repeat yet")
            if self.tts_provider is not None:
                from local.audio import play_wav_bytes

                play_wav_bytes(self.tts_provider.synthesize(text, self.turn_out))
            return True
        slow = command == "slower"
        print(f"[{command}] saying the last answer again{' slowly' if slow else ''}:")
        if self.screen:
            self.screen.show_answer(list(self.session.last_text), self.turn_out)
        for text in self.session.last_text:
            print(f"   · {text}")
        if self.tts_provider is not None:
            from local.audio import SLOW_GAP_SECONDS, play_wav_bytes, slow_wav_bytes

            for audio in self.session.last_audio:
                play_wav_bytes(slow_wav_bytes(audio) if slow else audio)
                if slow:
                    time.sleep(SLOW_GAP_SECONDS)
        self.session.last_used = self.session.clock()
        return True

    def run_once(self, mic) -> bool:
        from local.audio import play_wav_bytes
        from pocketinfer.specialist import speak_stream

        T = {}
        t0 = time.time()
        ts = time.time()
        screen = self.screen
        if screen and mic is None:  # typed text or a page: no listening step
            screen.listening()
        raw = self._capture_text(mic)
        T["capture"] = time.time() - ts
        if not raw:
            print("[skip] nothing heard.")
            if screen:
                screen.ready()
            return False
        if screen:
            screen.heard(raw, self.turn_in)
        if self.session.is_reset(raw):
            self.session.clear()
            print("[session] new topic, earlier questions forgotten")
            return True
        self._choose_output(raw)
        # Checked before translation, so a Hindi "दोबारा बोलो" replays at once.
        command = self.session.command(raw)
        if command:
            return self._replay(command)
        query_en = self._translate_query(raw, T)
        if self.session.is_reset(query_en):
            self.session.clear()
            print("[session] new topic, earlier questions forgotten")
            return True
        self._choose_output(raw, query_en)
        command = self.session.command(query_en)
        if command:
            return self._replay(command)
        if self.in_lang == "auto" or self.out_lang == "auto":
            print(f"[lang ] question in {self.turn_in}, answer in {self.turn_out}")

        header = {"printed": False}

        def on_sentence(sr):
            if not header.get("rewrite") and self.session.last_rewrite:
                print(f"[follow] ({self.session.last_rewrite_seconds*1000:.0f}ms) understood as: "
                      f"{self.session.last_rewrite!r}")
                header["rewrite"] = True
            if sr.timings:
                print(f"[steps] {_fmt_timings(sr.timings)}")
            if sr.refused:
                print(f"[3 SPEC] REFUSED — {sr.reason}")
                if screen:
                    screen.refused(sr.spoken_text, self.turn_out)
                return
            if screen:
                screen.sentence(sr.spoken_text, sr.example, self.turn_out)
            if not header["printed"]:
                print("[3 SPEC] streaming:")
                header["printed"] = True
            for c in sr.citations:
                print(f"          · {c}")
            print(f"   · {'(example) ' if sr.example else ''}{sr.spoken_text}")

        speaking = self.tts_provider is not None
        play_fn = play_wav_bytes if speaking else (lambda _b: None)
        from pocketinfer.specialist.session import ask

        results, first_audio = speak_stream(
            ask(self.engine, self.session, query_en, self.turn_out, llm=self.rewrite_llm),
            play_fn, on_sentence,
        )

        self.session.keep_last(results)
        if speaking:
            T["first_audio"] = first_audio
        T["TOTAL"] = time.time() - t0
        n = sum(1 for r in results if not r.refused)
        print(f"[time ] end-to-end: {_fmt_timings(T)}  (sentences={n})")
        return True

    def run(self):
        once = self.run_once
        # Single-shot input modes. The screen keeps showing the last answer afterwards.
        if self.args.text or self.args.from_image or self.args.from_wav:
            try:
                if self.args.text:
                    for _ in self.args.text:
                        once(None)
                elif self.args.from_image:
                    once(None)
                else:
                    from local.audio import WavFileMic

                    once(WavFileMic(self.args.from_wav))
            finally:
                if self.screen:
                    self.screen.close()
            return

        # Live mic loop.
        from local.audio import AlsaArecordMic
        from local.triggers import make_trigger

        trigger = None if self.args.trigger == "enter" else make_trigger(
            self.args.trigger, self.args.gpio_chip, self.args.gpio_line, screen=self.screen)
        mic = AlsaArecordMic(self.args.mic_device, trigger=trigger, on_state=self._mic_state)
        print(f"\n== Specialist voice loop == trigger={getattr(trigger, 'name', 'enter')} "
              f"(Ctrl-C to quit)", flush=True)
        try:
            while True:
                try:
                    once(mic)
                except KeyboardInterrupt:
                    raise
                except Exception:  # noqa: BLE001
                    # One failed question (a service restarting, a bad recording) must not
                    # stop the device: log it and wait for the next press.
                    logging.exception("this question failed, waiting for the next one")
                    if self.screen:
                        self.screen.error()
        except KeyboardInterrupt:
            print("\nbye.")
        finally:
            if self.screen:
                self.screen.close()

    def _mic_state(self, state: str) -> None:
        if not self.screen:
            return
        if state == "waiting":
            self.screen.ready()
        elif state == "listening":
            self.screen.listening()


def main():
    ap = argparse.ArgumentParser(description="Specialist voice loop.")
    # input modes
    ap.add_argument("--text", action="append", default=None,
                    help="skip ASR: use this text as the request; repeat it to ask follow-ups")
    ap.add_argument("--from-wav", default=None, help="use a WAV file as the spoken request")
    ap.add_argument("--from-image", default=None, help="read the request from a page image (OCR)")
    ap.add_argument("--question-index", type=int, default=None,
                    help="with --from-image, which question on the page to use, from 0 (default: the middle one)")
    ap.add_argument("--mic-device", default=os.environ.get("SPECIALIST_MIC_DEVICE", "plughw:1,0"),
                    help="ALSA capture device for the live mic (find it with arecord -l)")
    ap.add_argument("--trigger", default=os.environ.get("SPECIALIST_TRIGGER", "enter"),
                    help="what starts and stops recording on the live mic: enter (terminal), "
                         "key (any USB keyboard or keypad, no terminal needed), gpio (touch sensor) "
                         "or screen (tap the touch screen). Join several with commas: gpio,screen")
    ap.add_argument("--screen", action="store_true",
                    default=os.environ.get("SPECIALIST_SCREEN", "") == "1",
                    help="show the state, the question and the answer on the 2.8 inch screen")
    ap.add_argument("--gpio-chip", default=os.environ.get("SPECIALIST_GPIO_CHIP"),
                    help="with --trigger gpio, the chip from gpioinfo, e.g. gpiochip0")
    ap.add_argument("--gpio-line", type=int, default=(int(os.environ["SPECIALIST_GPIO_LINE"])
                                                      if os.environ.get("SPECIALIST_GPIO_LINE") else None),
                    help="with --trigger gpio, the line number from gpioinfo")
    # providers (swappable)
    ap.add_argument("--asr", default="auto", help="auto|whisper|bhashini")
    ap.add_argument("--mt", default="bhashini", help="bhashini|none")
    ap.add_argument("--tts", default="bhashini", help="bhashini")
    ap.add_argument("--llm", default="ollama", help="ollama|extractive")
    ap.add_argument("--no-speak", action="store_true", help="don't play TTS (print only)")
    # languages
    ap.add_argument("--in-lang", default="auto",
                    help="hi, en, or auto to work it out for each request: speech by language ID and "
                         "Whisper, typed text and pages by their script")
    ap.add_argument("--out-lang", default="auto",
                    help="hi, en, or auto to answer in the request's language, unless it names one")
    # models / paths
    ap.add_argument("--index", default=DEFAULT_INDEX)
    ap.add_argument("--embed-model", default="intfloat/multilingual-e5-small")
    ap.add_argument("--whisper-model", default="tiny.en", help="faster-whisper model (e.g. tiny.en, base.en)")
    ap.add_argument("--ollama-model", default="qwen2.5:1.5b")
    ap.add_argument("--log-level", default="WARNING")
    args = ap.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper()), format="%(levelname)s %(message)s")
    if not os.path.exists(os.path.join(args.index, "embeddings.npy")):
        print(f"No index at {args.index}. Build it: python ingest/build_index.py")
        sys.exit(1)

    LoopHarness(args).run()


if __name__ == "__main__":
    main()
