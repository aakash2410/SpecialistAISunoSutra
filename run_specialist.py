#!/usr/bin/env python3
"""Plug-and-play Specialist runner — typed question in, grounded answer out.

Runs the exact same pipeline as the on-device app (retrieve -> ground -> grounded
LLM -> optional MT -> optional TTS), but from the command line with typed input.
No ASR, no microphone, no camera, no board, no platform service — so it runs
unchanged on a laptop or on the Jetson, and a broken/absent Whisper or Vosk can
never block it.

    python run_specialist.py "explain photosynthesis"     # one-shot
    python run_specialist.py                               # interactive: type questions
    python run_specialist.py --speak                       # also read the answer aloud
    python run_specialist.py --out-lang hi --speak         # translate + speak (needs BHASHINI)

Everything degrades gracefully:
  * Ollama not running / model missing  -> clear one-line message, no traceback.
  * Index missing                       -> clear one-line message with where it looked.
  * MT unavailable (no BHASHINI)         -> stays in English.
  * TTS unavailable                      -> stays text-only.

Swap any piece from the CLI: --model, --out-lang, --tts, --index, --no-stream.
"""

from __future__ import annotations

import argparse
import base64
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
# Works whether or not pocketinfer is pip-installed: prefer the local source tree.
sys.path.insert(0, os.path.join(HERE, "python"))


# --------------------------------------------------------------------------- #
# Index resolution — first location that actually holds a built index wins.
# --------------------------------------------------------------------------- #
def resolve_index(cli_index: str | None) -> str | None:
    candidates = []
    if cli_index:
        candidates.append(cli_index)
    if os.environ.get("SPECIALIST_INDEX_DIR"):
        candidates.append(os.environ["SPECIALIST_INDEX_DIR"])
    for base in ("/opt/specialist/corpus", os.path.join(HERE, "corpus")):
        for name in ("diksha_g7_all", "diksha_g7_science"):
            candidates.append(os.path.join(base, name, "index"))
    for c in candidates:
        if c and os.path.exists(os.path.join(c, "embeddings.npy")):
            return c
    return None


# --------------------------------------------------------------------------- #
# Audio playback — one WAV-bytes player that works on macOS (afplay) and the
# device (aplay via plughw, which handles mono->stereo / rate conversion).
# --------------------------------------------------------------------------- #
def play_wav_bytes(data: bytes) -> None:
    if not data:
        return
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    try:
        tmp.write(data)
        tmp.close()
        if sys.platform == "darwin":
            subprocess.run(["afplay", tmp.name], check=False)
        else:
            dev = os.environ.get("SPECIALIST_ALSA_DEVICE", "plughw:0,0")
            subprocess.run(["aplay", "-D", dev, tmp.name], check=False)
    finally:
        os.unlink(tmp.name)


def say_text(text: str, voice: str | None) -> None:
    cmd = ["say"] + (["-v", voice] if voice else []) + [text]
    subprocess.run(cmd, check=False)


# --------------------------------------------------------------------------- #
# Optional providers — each returns None (and prints why) if unavailable.
# --------------------------------------------------------------------------- #
def make_mt(out_lang: str):
    """BHASHINI machine translation, en <-> out_lang. None if unavailable."""
    if out_lang == "en":
        return None
    try:
        from pocketinfer.models.nmt import Nmt

        nmt = Nmt()
        # The engine says "EN"; the Bhashini service only accepts lowercase codes.
        return lambda text, src, tgt: nmt.infer(text, src.lower(), tgt.lower())["translated_text"]
    except Exception as e:  # noqa: BLE001
        print(f"[warn] translation unavailable ({e}); answering in English.")
        return None


def make_bhashini_tts():
    """BHASHINI TTS returning WAV bytes. None if unavailable."""
    try:
        from pocketinfer.models.tts import Tts

        tts = Tts()
        return lambda text, lang: base64.b64decode(tts.infer(text, lang)["audio_base64"])
    except Exception as e:  # noqa: BLE001
        print(f"[warn] BHASHINI TTS unavailable ({e}); continuing without audio.")
        return None


def resolve_speech(args):
    """Decide the speech backend. Returns (engine_tts, backend, say_voice).

    engine_tts: a (text, lang)->bytes callable given to the engine (BHASHINI only).
    backend:    'bhashini' | 'say' | None  — how a sentence is voiced.
    """
    if not args.speak:
        return None, None, None
    choice = args.tts or ("say" if sys.platform == "darwin" else "bhashini")
    if choice == "bhashini":
        tts = make_bhashini_tts()
        return (tts, "bhashini", None) if tts else (None, None, None)
    if choice == "say":
        if shutil.which("say"):
            return None, "say", args.say_voice
        print("[warn] macOS 'say' not found; continuing without audio.")
        return None, None, None
    print(f"[warn] unknown --tts '{choice}'; continuing without audio.")
    return None, None, None


# --------------------------------------------------------------------------- #
# Answering
# --------------------------------------------------------------------------- #
def _print_citations(citations):
    for c in citations:
        print(f"      · {c}")


def answer_stream(engine, question, out_lang, backend, say_voice):
    from pocketinfer.specialist import speak_stream

    printed_header = {"done": False}

    def on_sentence(sr):
        if sr.refused:
            print(f"\n  ⚠ {sr.spoken_text}\n      ({sr.reason})")
            return
        if not printed_header["done"]:
            _print_citations(sr.citations)
            printed_header["done"] = True
        print(f"  {sr.spoken_text}")
        if backend == "say":
            say_text(sr.spoken_text, say_voice)

    play_fn = play_wav_bytes if backend == "bhashini" else (lambda _b: None)
    speak_stream(engine.stream_answer(question, target_language=out_lang), play_fn, on_sentence)


def answer_once(engine, question, out_lang, backend, say_voice):
    res = engine.answer(question, target_language=out_lang)
    if res.refused:
        print(f"\n  ⚠ {res.spoken_text}\n      ({res.reason})")
        return
    print(f"\n  {res.answer_en}")
    if out_lang != "en" and res.answer_localized:
        print(f"  [{out_lang}] {res.answer_localized}")
    _print_citations(res.citations)
    if backend == "bhashini" and res.audio:
        play_wav_bytes(res.audio)
    elif backend == "say":
        say_text(res.spoken_text, say_voice)


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Plug-and-play Specialist runner (no ASR).")
    ap.add_argument("question", nargs="*", help="the question; omit for interactive mode")
    ap.add_argument("--model", default=os.environ.get("SPECIALIST_LLM_MODEL", "qwen2.5:1.5b"),
                    help="Ollama model (default qwen2.5:1.5b, or $SPECIALIST_LLM_MODEL)")
    ap.add_argument("--index", default=None, help="path to an index dir (else auto-resolved)")
    ap.add_argument("--out-lang", default="en", help="answer language, e.g. 'hi' (needs BHASHINI MT)")
    ap.add_argument("--speak", action="store_true", help="read the answer aloud")
    ap.add_argument("--tts", default=None, choices=["bhashini", "say"],
                    help="force a TTS backend (default: 'say' on macOS, 'bhashini' on device)")
    ap.add_argument("--say-voice", default=None, help="macOS 'say' voice (e.g. Lekha for Hindi)")
    ap.add_argument("--no-stream", action="store_true", help="answer all at once instead of streaming")
    args = ap.parse_args()

    index = resolve_index(args.index)
    if not index:
        print("No index found. Looked in $SPECIALIST_INDEX_DIR, /opt/specialist/corpus/, and "
              "./corpus/ (diksha_g7_all, diksha_g7_science).\n"
              "Point at one with --index /path/to/index or set SPECIALIST_INDEX_DIR.")
        sys.exit(1)

    from pocketinfer.specialist import SpecialistEngine
    from pocketinfer.specialist.llm import ollama_grounded_client, ollama_grounded_stream_client

    mt = make_mt(args.out_lang)
    out_lang = args.out_lang if mt or args.out_lang == "en" else "en"
    engine_tts, backend, say_voice = resolve_speech(args)

    engine = SpecialistEngine(
        index_dir=index,
        embedder=None,                    # defaults to the index's own baked embedder
        llm=ollama_grounded_client(args.model),
        stream_llm=None if args.no_stream else ollama_grounded_stream_client(args.model),
        mt=mt,
        tts=engine_tts,
    )
    print(f"[specialist] index={index}  model={args.model}  "
          f"out={out_lang}  speak={backend or 'off'}  stream={'off' if args.no_stream else 'on'}")

    run = answer_once if args.no_stream else answer_stream

    def ask(q):
        q = q.strip()
        if not q:
            return
        try:
            run(engine, q, out_lang, backend, say_voice)
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            if "connection" in msg or "refused" in msg or "11434" in msg:
                print(f"\n[error] can't reach Ollama ({e}).\n"
                      f"        Start it (`ollama serve`) and pull the model "
                      f"(`ollama pull {args.model}`).")
            else:
                print(f"\n[error] {e}")

    if args.question:
        ask(" ".join(args.question))
        return

    print("Type a question (blank line or Ctrl-D to quit).")
    try:
        while True:
            q = input("\n> ")
            ask(q)
    except (EOFError, KeyboardInterrupt):
        print("\nbye.")


if __name__ == "__main__":
    main()
