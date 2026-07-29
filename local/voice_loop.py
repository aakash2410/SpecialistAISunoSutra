"""Run the full Specialist loop locally, with swappable providers.

    ASR  ->  MT (optional)  ->  Specialist  ->  MT (optional)  ->  TTS

Input a custom testcase three ways:
  * live mic (default):   python -m local.voice_loop
  * typed text:           python -m local.voice_loop --text "What is photosynthesis?"
  * a WAV file:           python -m local.voice_loop --from-wav question.wav
  * say-synthesized:      python -m local.voice_loop --say-question "explain photosynthesis"

Swap any component by name (contracts live in pocketinfer.specialist.providers):
  --asr vosk|bhashini   --mt none|bhashini   --tts say|bhashini   --llm extractive|ollama

Defaults are the offline laptop stack (vosk / none / say / extractive). Selecting
the bhashini providers runs the identical loop against the device's BHASHINI
service — same code, different backends.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

# Import the device runtime + providers from the bundled package.
sys.path.insert(0, os.path.join(os.path.abspath(os.path.dirname(__file__)), "..", "python"))
sys.path.insert(0, os.path.join(os.path.abspath(os.path.dirname(__file__)), ".."))

from pocketinfer.models.embed import Embed  # noqa: E402
from pocketinfer.specialist import SpecialistEngine  # noqa: E402

from local.providers import make_asr, make_mt, make_tts, make_llm  # noqa: E402

DEFAULT_INDEX = os.path.join(
    os.path.dirname(__file__), "..", "corpus", "diksha_g7_science", "index",
)


def _fmt_timings(t: dict) -> str:
    return ", ".join(f"{k}={v*1000:.0f}ms" for k, v in t.items() if v is not None)


class LoopHarness:
    def __init__(self, args):
        self.args = args
        self.in_lang = args.in_lang.lower()
        self.out_lang = args.out_lang.lower()

        # Providers (each is swappable via the factory names).
        self.asr = None if args.text else make_asr(args.asr, vosk_model=args.vosk_model)
        self.mt = make_mt(args.mt)
        self.tts = None if args.no_speak else make_tts(args.tts)
        self.llm_fn = make_llm(args.llm, ollama_model=args.ollama_model)

        # Streaming LLM client (only ollama truly streams; other backends are
        # pseudo-streamed by sentence-splitting a full answer).
        self.stream_llm = None
        if args.stream and args.llm == "ollama":
            from pocketinfer.specialist.llm import ollama_grounded_stream_client

            self.stream_llm = ollama_grounded_stream_client(args.ollama_model)

        # Specialist core (English in/out); MT + TTS are applied explicitly below.
        self.engine = SpecialistEngine(
            index_dir=args.index,
            embedder=Embed(model_name=args.embed_model, prefer=args.embed_prefer),
            llm=self.llm_fn,
        )
        print(
            f"[providers] asr={getattr(self.asr,'name','(text)')}  mt={self.mt.name}  "
            f"tts={getattr(self.tts,'name','(off)')}  llm={args.llm}  "
            f"embed={self.engine.embedder.backend}  in={self.in_lang} out={self.out_lang}"
        )
        if self.in_lang != "en" and self.mt.name == "none":
            print("[warn] input language is not English but MT is 'none' — the query "
                  "won't be translated before retrieval over the English corpus.")

    def _capture_text(self, mic) -> str:
        if self.args.text:
            return self.args.text
        t0 = time.time()
        clip = mic.record()
        raw = self.asr.transcribe(clip, self.in_lang)
        print(f"[1 ASR ] ({(time.time()-t0)*1000:.0f}ms, {clip.duration_seconds:.1f}s audio) -> {raw!r}")
        return raw

    def run_once(self, mic) -> bool:
        T = {}
        t0 = time.time()
        ts = time.time()
        raw = self._capture_text(mic)
        T["capture"] = time.time() - ts
        if not raw:
            print("[skip] nothing heard.")
            return False

        # Stage 2: MT (optional) — user request -> English for the Specialist.
        query_en = raw
        if self.in_lang != "en":
            ts = time.time()
            query_en = self.mt.translate(raw, self.in_lang, "en")
            T["mt_in"] = time.time() - ts
            print(f"[2 MT  ] {self.in_lang}->en -> {query_en!r}")

        # Stage 3: the Specialist (embed -> retrieve -> ground -> LLM), in English.
        ts = time.time()
        result = self.engine.answer(query_en, target_language="en")
        T["specialist"] = time.time() - ts
        if result.refused:
            print(f"[3 SPEC] REFUSED — {result.reason}")
        else:
            print(f"[3 SPEC] ({result.intent}) -> {result.answer_en}")
            for c in result.citations:
                print(f"          · {c}")

        # Stage 4: MT (optional) — English answer -> output language.
        answer_out = result.answer_en
        if self.out_lang != "en":
            ts = time.time()
            answer_out = self.mt.translate(result.answer_en, "en", self.out_lang)
            T["mt_out"] = time.time() - ts
            print(f"[4 MT  ] en->{self.out_lang} -> {answer_out!r}")

        # Stage 5: TTS — speak it.
        if self.tts:
            print(f"[5 TTS ] speaking via {self.tts.name} ({self.out_lang})...")
            ts = time.time()
            self.tts.speak(answer_out, self.out_lang)
            T["tts"] = time.time() - ts

        T["TOTAL"] = time.time() - t0
        print(f"[time ] core: {_fmt_timings(result.timings)}")
        print(f"[time ] end-to-end: {_fmt_timings(T)}")
        return True

    # --- streaming path: speak sentence N while N+1 is still being produced -----
    def _stream_source(self, g):
        """Yield LLM text chunks. Real streaming for ollama; a single chunk
        (split downstream into sentences) for non-streaming backends."""
        if self.stream_llm is not None:
            return self.stream_llm(g.system_prompt, g.user_prompt)
        return iter([self.llm_fn(g.system_prompt, g.user_prompt)])

    def _speak_bytes_blocking(self, text: str):
        if not self.tts or self.args.no_speak:
            return
        from local.audio import play_wav_bytes

        play_wav_bytes(self.tts.synthesize(text, self.out_lang))

    def run_once_stream(self, mic) -> bool:
        import queue
        import threading

        from local.audio import play_wav_bytes
        from pocketinfer.specialist.grounding import (
            REFUSAL_TEXT, clean_answer, is_refusal_response, iter_sentences,
        )

        T = {}
        t0 = time.time()
        ts = time.time()
        raw = self._capture_text(mic)
        T["capture"] = time.time() - ts
        if not raw:
            print("[skip] nothing heard.")
            return False

        query_en = raw
        if self.in_lang != "en":
            ts = time.time()
            query_en = self.mt.translate(raw, self.in_lang, "en")
            T["mt_in"] = time.time() - ts
            print(f"[2 MT  ] {self.in_lang}->en -> {query_en!r}")

        # embed + retrieve + ground (no LLM yet)
        ts = time.time()
        g = self.engine.prepare(query_en)
        T["prepare"] = time.time() - ts
        if g.refused:
            print(f"[3 SPEC] REFUSED — {g.reason}")
            text = REFUSAL_TEXT
            if self.out_lang != "en" and self.mt.name != "none":
                text = self.mt.translate(REFUSAL_TEXT, "en", self.out_lang)
            self._speak_bytes_blocking(text)
            T["TOTAL"] = time.time() - t0
            print(f"[time ] end-to-end: {_fmt_timings(T)}")
            return True

        print(f"[3 SPEC] ({g.intent}) streaming:")
        for c in g.citations:
            print(f"          · {c}")

        # Ordered player thread: playback (no GPU) overlaps generation/MT/TTS.
        speaking = bool(self.tts) and not self.args.no_speak
        q: "queue.Queue" = queue.Queue()
        marks = {"first_audio": None}

        def _player():
            while True:
                item = q.get()
                if item is None:
                    q.task_done()
                    break
                if marks["first_audio"] is None:
                    marks["first_audio"] = time.time() - t0
                play_wav_bytes(item)
                q.task_done()

        pt = threading.Thread(target=_player, daemon=True)
        if speaking:
            pt.start()

        n = 0
        for i, sentence in enumerate(iter_sentences(self._stream_source(g))):
            if i == 0 and is_refusal_response(sentence):
                print("[3 SPEC] REFUSED — model reported no answer in source")
                self._speak_bytes_blocking(REFUSAL_TEXT)
                break
            text = clean_answer(sentence)
            if not text:
                continue
            out = text
            if self.out_lang != "en":
                out = self.mt.translate(text, "en", self.out_lang)  # per-sentence: serial to :11400
            print(f"   · {out}")
            if speaking:
                q.put(self.tts.synthesize(out, self.out_lang))  # serial synth, ordered play
            n += 1

        if speaking:
            q.put(None)
            pt.join()
            T["first_audio"] = marks["first_audio"]
        T["TOTAL"] = time.time() - t0
        print(f"[time ] end-to-end: {_fmt_timings(T)}  (sentences={n})")
        return True

    def run(self):
        once = self.run_once_stream if self.args.stream else self.run_once
        # Single-shot input modes.
        if self.args.text:
            once(None)
            return
        if self.args.from_wav:
            from local.audio import WavFileMic

            once(WavFileMic(self.args.from_wav))
            return
        if self.args.say_question:
            from local.audio import SayFileMic

            once(SayFileMic(self.args.say_question, voice=self.args.say_voice))
            return

        # Live mic loop.
        from local.audio import SoundDeviceMic

        mic = SoundDeviceMic()
        print("\n== Specialist voice loop == (Ctrl-C to quit)")
        try:
            while True:
                once(mic)
        except KeyboardInterrupt:
            print("\nbye.")


def main():
    ap = argparse.ArgumentParser(description="Local Specialist voice loop.")
    # input modes
    ap.add_argument("--text", default=None, help="skip ASR: use this text as the request")
    ap.add_argument("--from-wav", default=None, help="use a WAV file as the spoken request")
    ap.add_argument("--say-question", default=None, help="synthesize this question with 'say' then run it")
    ap.add_argument("--say-voice", default=None, help="voice for --say-question (e.g. Lekha for Hindi)")
    # providers (swappable)
    ap.add_argument("--asr", default="vosk", help="vosk|bhashini")
    ap.add_argument("--mt", default="none", help="none|bhashini")
    ap.add_argument("--tts", default="say", help="say|bhashini")
    ap.add_argument("--llm", default="extractive", help="extractive|ollama")
    ap.add_argument("--stream", action="store_true",
                    help="stream the answer sentence-by-sentence: speak sentence N while "
                         "N+1 is still being generated/translated (cuts time-to-first-audio)")
    ap.add_argument("--no-speak", action="store_true", help="don't play TTS (print only)")
    # languages
    ap.add_argument("--in-lang", default="en")
    ap.add_argument("--out-lang", default="en")
    # models / paths
    ap.add_argument("--index", default=DEFAULT_INDEX)
    ap.add_argument("--embed-model", default="intfloat/multilingual-e5-small")
    ap.add_argument("--embed-prefer", default="auto",
                    help="auto|onnx|tfidf|sentence-transformers — match the index's embedder")
    ap.add_argument("--vosk-model", default=None, help="path to a Vosk model dir")
    ap.add_argument("--ollama-model", default="llama3.2:1b")
    ap.add_argument("--log-level", default="WARNING")
    args = ap.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper()), format="%(levelname)s %(message)s")
    if not os.path.exists(os.path.join(args.index, "embeddings.npy")):
        print(f"No index at {args.index}. Build it: python ingest/build_index.py")
        sys.exit(1)

    LoopHarness(args).run()


if __name__ == "__main__":
    main()
