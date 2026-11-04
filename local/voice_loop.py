"""Run the full Specialist loop on the device, with swappable providers.

    speech or page  ->  translate  ->  Specialist  ->  translate  ->  speech

Give it a request in any of these ways:
  * a WAV file:      python -m local.voice_loop --in-lang hi --from-wav question.wav
  * a page image:    python -m local.voice_loop --in-lang hi --from-image page.jpg
  * typed text:      python -m local.voice_loop --text "What is photosynthesis?"
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

# Import the device runtime + providers from the bundled package.
sys.path.insert(0, os.path.join(os.path.abspath(os.path.dirname(__file__)), "..", "python"))
sys.path.insert(0, os.path.join(os.path.abspath(os.path.dirname(__file__)), ".."))

from pocketinfer.models.embed import Embed  # noqa: E402
from pocketinfer.specialist import SpecialistEngine  # noqa: E402

from local.providers import make_asr, make_mt, make_tts, make_llm  # noqa: E402


def _resolve_index() -> str:
    """Canonical index: $SPECIALIST_INDEX_DIR > the first baked index found."""
    env = os.environ.get("SPECIALIST_INDEX_DIR")
    if env:
        return env
    roots = ("/opt/specialist/corpus", os.path.join(os.path.dirname(__file__), "..", "corpus"))
    for root in roots:
        for name in ("diksha_g7_all", "diksha_g7_science"):
            p = os.path.join(root, name, "index")
            if os.path.exists(os.path.join(p, "embeddings.npy")):
                return p
    return os.path.join(roots[1], "diksha_g7_science", "index")


DEFAULT_INDEX = _resolve_index()


def _fmt_timings(t: dict) -> str:
    return ", ".join(f"{k}={v*1000:.0f}ms" for k, v in t.items() if v is not None)


class LoopHarness:
    def __init__(self, args):
        self.args = args
        self.in_lang = args.in_lang.lower()
        self.out_lang = args.out_lang.lower()

        # Providers (each is swappable via the factory names).
        asr = args.asr
        if asr == "auto":
            asr = "whisper" if self.in_lang == "en" else "bhashini"
        self.asr = None if (args.text or args.from_image) else make_asr(asr, whisper_model=args.whisper_model)
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
        print(
            f"[providers] asr={getattr(self.asr,'name','(none)')}  mt={self.mt.name}  "
            f"tts={getattr(self.tts_provider,'name','(off)')}  llm={args.llm}  "
            f"stream={'on' if args.stream else 'off'}  "
            f"in={self.in_lang} out={self.out_lang}"
        )
        if self.in_lang != "en" and self.mt.name == "none":
            print("[warn] input language is not English but MT is 'none' — the query "
                  "won't be translated before retrieval over the English corpus.")

    def _capture_text(self, mic) -> str:
        if self.args.text:
            return self.args.text
        t0 = time.time()
        if self.args.from_image:
            from pocketinfer.models.ocr import Ocr

            with open(self.args.from_image, "rb") as f:
                raw = Ocr().infer(f.read(), self.in_lang)["text"]
            print(f"[1 OCR ] ({(time.time()-t0)*1000:.0f}ms) -> {raw!r}")
            return raw
        clip = mic.record()
        raw = self.asr.transcribe(clip, self.in_lang)
        print(f"[1 ASR ] ({(time.time()-t0)*1000:.0f}ms, {clip.duration_seconds:.1f}s audio) -> {raw!r}")
        return raw

    def _translate_query(self, raw: str, T: dict) -> str:
        """Translate the request into English for the Specialist, if needed."""
        if self.in_lang == "en":
            return raw
        ts = time.time()
        query_en = self.mt.translate(raw, self.in_lang, "en")
        T["mt_in"] = time.time() - ts
        print(f"[2 MT  ] {self.in_lang}->en -> {query_en!r}")
        return query_en

    def run_once(self, mic) -> bool:
        from local.audio import play_wav_bytes

        T = {}
        t0 = time.time()
        ts = time.time()
        raw = self._capture_text(mic)
        T["capture"] = time.time() - ts
        if not raw:
            print("[skip] nothing heard.")
            return False
        query_en = self._translate_query(raw, T)

        # The engine does embed -> retrieve -> ground -> LLM -> MT -> TTS-synth.
        ts = time.time()
        result = self.engine.answer(query_en, target_language=self.out_lang)
        T["specialist"] = time.time() - ts
        if result.refused:
            print(f"[3 SPEC] REFUSED — {result.reason}")
        else:
            print(f"[3 SPEC] ({result.intent}) -> {result.answer_en}")
            for c in result.citations:
                print(f"          · {c}")
            if self.out_lang != "en":
                print(f"[4 MT  ] en->{self.out_lang} -> {result.answer_localized!r}")

        if result.audio and self.tts_provider:
            ts = time.time()
            play_wav_bytes(result.audio)
            T["playback"] = time.time() - ts

        T["TOTAL"] = time.time() - t0
        print(f"[time ] core: {_fmt_timings(result.timings)}")
        print(f"[time ] end-to-end: {_fmt_timings(T)}")
        return True

    def run_once_stream(self, mic) -> bool:
        from local.audio import play_wav_bytes
        from pocketinfer.specialist import speak_stream

        T = {}
        t0 = time.time()
        ts = time.time()
        raw = self._capture_text(mic)
        T["capture"] = time.time() - ts
        if not raw:
            print("[skip] nothing heard.")
            return False
        query_en = self._translate_query(raw, T)

        header = {"printed": False}

        def on_sentence(sr):
            if sr.refused:
                print(f"[3 SPEC] REFUSED — {sr.reason}")
                return
            if not header["printed"]:
                print("[3 SPEC] streaming:")
                for c in sr.citations:
                    print(f"          · {c}")
                header["printed"] = True
            print(f"   · {sr.spoken_text}")

        speaking = self.tts_provider is not None
        play_fn = play_wav_bytes if speaking else (lambda _b: None)
        results, first_audio = speak_stream(
            self.engine.stream_answer(query_en, target_language=self.out_lang),
            play_fn, on_sentence,
        )

        if speaking:
            T["first_audio"] = first_audio
        T["TOTAL"] = time.time() - t0
        n = sum(1 for r in results if not r.refused)
        print(f"[time ] end-to-end: {_fmt_timings(T)}  (sentences={n})")
        return True

    def run(self):
        once = self.run_once_stream if self.args.stream else self.run_once
        # Single-shot input modes.
        if self.args.text or self.args.from_image:
            once(None)
            return
        if self.args.from_wav:
            from local.audio import WavFileMic

            once(WavFileMic(self.args.from_wav))
            return

        # Live mic loop.
        from local.audio import AlsaArecordMic

        mic = AlsaArecordMic(self.args.mic_device)
        print("\n== Specialist voice loop == (Ctrl-C to quit)")
        try:
            while True:
                once(mic)
        except KeyboardInterrupt:
            print("\nbye.")


def main():
    ap = argparse.ArgumentParser(description="Specialist voice loop.")
    # input modes
    ap.add_argument("--text", default=None, help="skip ASR: use this text as the request")
    ap.add_argument("--from-wav", default=None, help="use a WAV file as the spoken request")
    ap.add_argument("--from-image", default=None, help="read the request from a page image (OCR)")
    ap.add_argument("--mic-device", default="plughw:1,0",
                    help="ALSA capture device for the live mic (find it with arecord -l)")
    # providers (swappable)
    ap.add_argument("--asr", default="auto", help="auto|whisper|bhashini")
    ap.add_argument("--mt", default="bhashini", help="bhashini|none")
    ap.add_argument("--tts", default="bhashini", help="bhashini")
    ap.add_argument("--llm", default="ollama", help="ollama|extractive")
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
    ap.add_argument("--whisper-model", default="small.en", help="faster-whisper model (e.g. small.en, base.en)")
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
