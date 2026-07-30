"""Laptop demo of the Specialist offline answer loop — no device required.

Runs embed -> retrieve -> ground -> (extractive) answer entirely offline, so you
can see grounding and refusal working before deploying to a Suno Sutra board.

    python ingest/build_index.py        # bake the index once
    python demo.py                       # run the canned questions
    python demo.py --ask "Explain photosynthesis simply"
    python demo.py --interactive

By default it uses the dependency-free embedder + extractive answerer, so it
needs nothing beyond numpy. If you have ollama running with a local model, pass
--ollama <model> to use a real grounded LLM. If the BHASHINI service is up, pass
--lang hi to translate the answer.
"""

from __future__ import annotations

import argparse
import os
import sys

# Import the same runtime the device uses, from the bundled package under python/.
sys.path.insert(0, os.path.join(os.path.abspath(os.path.dirname(__file__)), "python"))

from pocketinfer.specialist import SpecialistEngine  # noqa: E402


def resolve_index(corpus_root: str) -> str:
    """The single canonical index location.

    Precedence: $SPECIALIST_INDEX_DIR  >  the freshly-built diksha_g7_all  >  the
    committed reference corpus. Set the env var to pin one folder permanently.
    """
    env = os.environ.get("SPECIALIST_INDEX_DIR")
    if env:
        return env
    for name in ("diksha_g7_all", "diksha_g7_science"):
        p = os.path.join(corpus_root, name, "index")
        if os.path.exists(os.path.join(p, "embeddings.npy")):
            return p
    return os.path.join(corpus_root, "diksha_g7_science", "index")


DEFAULT_INDEX = resolve_index(os.path.join(os.path.dirname(__file__), "corpus"))

CANNED = [
    ("Mujhe photosynthesis samjhaye, Hindi mein.", None),   # in-corpus, simplify
    ("What is photosynthesis?", None),                      # in-corpus, explain
    ("How is heat transferred?", None),                     # in-corpus, answer
    ("Explain the rules of cricket", None),                 # NOT in corpus -> refuse
    ("Tell me about the French Revolution", None),          # NOT in corpus -> refuse
]


def make_engine(args) -> SpecialistEngine:
    llm = None
    if args.ollama:
        from pocketinfer.specialist.llm import ollama_grounded_client

        llm = ollama_grounded_client(args.ollama)

    mt = None
    if args.lang and args.lang.lower() != "en":
        try:
            import requests

            def mt(text, src, tgt):  # noqa: ANN001
                r = requests.post(
                    "http://localhost:11400/nmt",
                    json={"text": text, "src_lang": src, "tgt_lang": tgt},
                    timeout=20,
                )
                return r.json()["translated_text"]
        except Exception:  # noqa: BLE001
            mt = None

    return SpecialistEngine(
        index_dir=args.index,
        llm=llm,
        mt=mt,
        min_score=args.min_score,
    )


def show(result):
    print("\n" + "=" * 72)
    print(f"Q ({result.intent}): {result.request}")
    print("-" * 72)
    if result.refused:
        print(f"REFUSED: {result.answer_localized}")
        print(f"  why: {result.reason}")
        if result.passages:
            print(f"  (closest passage scored {result.passages[0].score:.2f})")
    else:
        print(f"ANSWER: {result.spoken_text}")
        if result.target_language and result.target_language.lower() != "en" and result.answer_en:
            print(f"  (English: {result.answer_en})")
        print("  Sources:")
        for c in result.citations:
            print(f"    - {c}")
    t = result.timings
    timing = ", ".join(f"{k}={v*1000:.0f}ms" for k, v in t.items())
    print(f"  timings: {timing}")


def main():
    ap = argparse.ArgumentParser(description="Specialist offline loop demo.")
    ap.add_argument("--index", default=DEFAULT_INDEX)
    ap.add_argument("--ask", default=None, help="ask a single question and exit")
    ap.add_argument("--interactive", action="store_true")
    ap.add_argument("--lang", default="en", help="target language code (e.g. hi). Needs BHASHINI service.")
    ap.add_argument("--ollama", default=None, help="ollama model name for a real grounded LLM")
    ap.add_argument("--min-score", type=float, default=None, help="override refusal threshold")
    args = ap.parse_args()

    if not os.path.exists(os.path.join(args.index, "embeddings.npy")):
        print(f"No index at {args.index}. Build it first:\n  python ingest/build_index.py")
        sys.exit(1)

    engine = make_engine(args)

    if args.ask:
        show(engine.answer(args.ask, target_language=args.lang))
        return

    if args.interactive:
        print("Specialist demo — type a question (blank to quit).")
        while True:
            try:
                q = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not q:
                break
            show(engine.answer(q, target_language=args.lang))
        return

    for q, _ in CANNED:
        show(engine.answer(q, target_language=args.lang))


if __name__ == "__main__":
    main()
