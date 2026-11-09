#!/usr/bin/env python3
"""First-sentence latency for the six Flite versions. Needs the Jetson, with Bhashini running.

    cd ~/specialist && /opt/specialist/venv/bin/python -W ignore tests/benchmark/tts_variants.py

Each cell is the time from question to first Hindi audio ready: search, Qwen's first sentence,
the answer check and translation, plus Flite and its filter.
"""

import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [os.path.join(ROOT, "python"), ROOT]
os.chdir(ROOT)

from local.providers import make_mt  # noqa: E402
from local.voice_loop import DEFAULT_INDEX  # noqa: E402
from pocketinfer.specialist import SpecialistEngine  # noqa: E402
from pocketinfer.specialist.llm import ollama_grounded_client, ollama_grounded_stream_client  # noqa: E402

FLITE = "/opt/specialist/bhashini_models/tts/flite"
INDEX = DEFAULT_INDEX
QUESTIONS = ["What is photosynthesis?", "What is conduction?", "What is a fraction?"]
CLEAN = "equalizer=f=1000:t=q:w=1:g=-4,equalizer=f=200:t=q:w=1:g=3,lowpass=f=6000,volume=-3dB,alimiter=limit=0.8"
DEEP = "asetrate=16000*0.90,aresample=16000,atempo=1.111"
VARIANTS = [
    ("1 current", 110, None),
    ("2 lower pitch, cleaned", 90, CLEAN),
    ("3 lower, deeper", 90, f"asetrate=16000*0.94,aresample=16000,atempo=1.064,{CLEAN}"),
    ("4 lower, much deeper", 90, f"{DEEP},{CLEAN}"),
    ("5 as 4, small room", 90, f"{DEEP},aecho=0.8:0.85:30:0.15,{CLEAN}"),
    ("6 lowest, deeper", 75, f"asetrate=16000*0.94,aresample=16000,atempo=1.064,{CLEAN}"),
]


def speech_seconds(text, pitch, audio_filter):
    with tempfile.TemporaryDirectory() as tmp:
        raw, out = os.path.join(tmp, "a.wav"), os.path.join(tmp, "b.wav")
        t0 = time.time()
        subprocess.run([f"{FLITE}/bin/flite", "-voice", f"{FLITE}/voices/cmu_indic_hin_ab.flitevox",
                        "--setf", "duration_stretch=1.0", "--setf", f"int_f0_target_mean={pitch}", "-t", text, raw],
                       check=True, capture_output=True)
        if audio_filter:
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", raw, "-af", audio_filter, out],
                           check=True, capture_output=True)
        return time.time() - t0


def main():
    model = sys.argv[1] if len(sys.argv) > 1 else "qwen2.5:1.5b"
    engine = SpecialistEngine(index_dir=INDEX, llm=ollama_grounded_client(model), stream_llm=ollama_grounded_stream_client(model),
                              mt=make_mt("bhashini").translate)
    engine.answer("What is a noun?")  # warm up
    print("| Question | First sentence (words) | Before speech (s) | " + " | ".join(v[0] for v in VARIANTS) + " |")
    print("|---|---|---|" + "---|" * len(VARIANTS))
    for question in QUESTIONS:
        t0 = time.time()
        first = next(engine.stream_answer(question, target_language="hi"))
        before = time.time() - t0
        cells = [f"{before + speech_seconds(first.text_localized, p, f):.2f}" for _, p, f in VARIANTS]
        print(f"| {question} | {len(first.text_en.split())} | {before:.2f} | " + " | ".join(cells) + " |")
    print("\nKokoro is not installed and is left out on purpose.")


if __name__ == "__main__":
    main()
