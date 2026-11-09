#!/usr/bin/env python3
"""Compare answer models on 60 curriculum, 30 off-topic and 8 passage-lacks-answer questions.

    python3 tests/benchmark/answer_models.py qwen2.5:1.5b hf.co/ggml-org/SmolLM3-3B-GGUF:Q4_K_M

Timings are for the machine it runs on. Run it on the Jetson for numbers that count.
"""

import json
import os
import statistics as st
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [os.path.join(ROOT, "python"), ROOT]
os.chdir(ROOT)

import ollama  # noqa: E402

from local.voice_loop import DEFAULT_INDEX  # noqa: E402
from pocketinfer.specialist import SpecialistEngine  # noqa: E402
from pocketinfer.specialist.grounding import build_grounding, is_refusal_response  # noqa: E402
from pocketinfer.specialist.llm import ollama_grounded_client, ollama_grounded_stream_client  # noqa: E402
from pocketinfer.specialist.vector_index import Retrieved  # noqa: E402

INDEX = DEFAULT_INDEX
INSTALLED_NOTE = "ollama pull {}"


def installed():
    return {m.model for m in ollama.Client().list().models}


def run(engine, question):
    t0, first, spoken, refused = time.time(), None, [], False
    for sentence in engine.stream_answer(question):
        first = first if first is not None else time.time() - t0
        refused = refused or sentence.refused
        if not sentence.refused:
            spoken.append(sentence.text_en)
    return dict(refused=refused, first=first, total=time.time() - t0, words=len(" ".join(spoken).split()),
                first_words=len(spoken[0].split()) if spoken else 0, answer=" ".join(spoken))


def score(model, sets):
    llm = ollama_grounded_client(model)
    engine = SpecialistEngine(index_dir=INDEX, llm=llm, stream_llm=ollama_grounded_stream_client(model))
    on = [run(engine, q) for q in sets["curriculum"]]
    off = [run(engine, q) for q in sets["off_topic"]]
    alone = with_check = 0
    for case in sets["passage_lacks_answer"]:
        p = Retrieved(0.95, case["passage"], "p", "p", "p", 1, "p")
        g = build_grounding(case["question"], [p], min_score=0.1)
        text = llm(g.system_prompt, g.user_prompt)
        said_no = is_refusal_response(text)
        alone += said_no
        kept = [] if said_no else [s for s in text.split(". ") if engine.verifier.supporting_passage(s, [p])]
        with_check += not kept
    answered = [r for r in on if not r["refused"]]
    return {
        "Covered questions answered (/60)": sum(not r["refused"] for r in on),
        "Off-topic questions refused (/30)": sum(r["refused"] for r in off),
        "Passage lacks answer, model refused (/8)": alone,
        "Passage lacks answer, refused after the check (/8)": with_check,
        "Typical time per question (s)": round(st.median(r["total"] for r in on), 2),
        "First-sentence latency (s)": round(st.median(r["first"] for r in on), 2),
        "Typical answer length (words)": round(st.median(r["words"] for r in answered)) if answered else 0,
        "Typical first sentence (words)": round(st.median(r["first_words"] for r in answered)) if answered else 0,
    }, {"curriculum": dict(zip(sets["curriculum"], on)), "off_topic": dict(zip(sets["off_topic"], off))}


def main():
    models = sys.argv[1:] or ["qwen2.5:1.5b"]
    with open(os.path.join(os.path.dirname(__file__), "questions.json"), encoding="utf-8") as f:
        sets = json.load(f)
    have = installed()
    missing = [m for m in models if m not in have and f"{m}:latest" not in have]
    for m in missing:
        print(f"Skipping {m}: not installed. Run: {INSTALLED_NOTE.format(m)}")
    models = [m for m in models if m not in missing]
    results, detail = {}, {}
    for m in models:
        results[m], detail[m] = score(m, sets)
    print("\n| Measure | " + " | ".join(models) + " |\n|---|" + "---|" * len(models))
    for key in (next(iter(results.values())) if results else {}):
        print(f"| {key} | " + " | ".join(str(results[m][key]) for m in models) + " |")
    out = os.environ.get("BENCHMARK_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(detail, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
