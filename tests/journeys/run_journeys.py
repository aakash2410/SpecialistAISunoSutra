"""Run the four user journeys through the real Specialist and say which ones work.

The journeys come from "Tentative User Journey for Doosri Aawaz". Each is scripted in
tests/journeys/journeys.json as a few variants, each a list of turns with simple
checks on what the device does (a lesson plan, a quiz question, an answer in Hindi),
never on its exact words.

Every turn goes through the voice loop's own LoopHarness, as typed text, so it takes
the same path as a spoken request after speech recognition: language by script,
translation, "repeat that" and "slower", "new topic", then session.ask, which routes
to lesson plans, quizzes and grounded answers. Nothing is faked: real retrieval, the
real answer model (Qwen in Ollama), the real answer check and the real safety check.

    python3 -W ignore tests/journeys/run_journeys.py --lang en --out tests/journeys/report_mac_en.md
    python3 -W ignore tests/journeys/run_journeys.py --journey J3 --variant J3-en-a

On the Jetson, where Bhashini runs, for the Hindi variants:

    cd ~/specialist && PYTHONPATH=python:. /opt/specialist/venv/bin/python -W ignore \\
        tests/journeys/run_journeys.py --lang hi --out ~/journeys_hi.md

A variant passes when every check on every turn passes. It fails when a turn marked
"core" fails, since then the journey cannot go on as the user expects; otherwise it is
partial. A journey passes when all its variants pass, fails when none of them passes or
is partial, and is partial in between. A journey that needs a language this run does
not cover (J4 is about a student's home language) is at best partial.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import platform
import re
import sys
import time
import traceback
from argparse import Namespace
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "python"))
sys.path.insert(0, ROOT)

SCRIPTS = os.path.join(HERE, "journeys.json")
BHASHINI_HEALTH = "http://127.0.0.1:11400/health"
OLLAMA_TAGS = "http://127.0.0.1:11434/api/tags"
# Two answers count as different when they share less than this share of their words.
SAME_WORDS = 0.6

_DEVANAGARI = re.compile(r"[\u0900-\u097F]+")
_LATIN = re.compile(r"[A-Za-z]+")


# --------------------------------------------------------------------------- one turn
@dataclass
class TurnRecord:
    said: str                                   # what the user said (typed)
    kind: str = ""
    sentences_en: List[str] = field(default_factory=list)
    sentences_spoken: List[str] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)
    citations: List[str] = field(default_factory=list)
    refused: bool = False
    reasons: List[str] = field(default_factory=list)
    lang_in: str = ""
    lang_out: str = ""
    query_en: Optional[str] = None              # the request after translation into English
    rewrite: Optional[str] = None               # a follow-up as the device understood it
    replayed: List[str] = field(default_factory=list)
    verdict: Optional[str] = None               # quiz feedback: right, not_quite, cant_check
    quiz_next: Optional[str] = None             # after feedback: "next" question or "end"
    seconds: float = 0.0
    first_sentence_seconds: Optional[float] = None
    error: Optional[str] = None
    log: str = ""
    checks: List[tuple] = field(default_factory=list)   # (name, passed, detail)

    @property
    def passed(self) -> bool:
        return self.error is None and all(ok for _, ok, _ in self.checks)


def _prefix(template: str) -> str:
    return template.split("{")[0].strip()


def _classify(rec: TurnRecord, replay: Optional[str], reset: bool) -> str:
    """What kind of reply this was, from the device's own fixed sentences."""
    from pocketinfer.specialist import lesson_plan, quiz

    if reset:
        return "reset"
    if replay:
        return "replay" if rec.replayed else "nothing_to_repeat"
    if not rec.sentences_en:
        return "silent"
    first = rec.sentences_en[0]
    if rec.refused and len(rec.sentences_spoken) <= 1 and all(r for r in rec.reasons):
        return "safety" if any(r.startswith("safety:") for r in rec.reasons) else "refusal"
    if first.startswith(_prefix(quiz.INTRO)):
        return "quiz_question"
    if first.startswith(_prefix(quiz.NO_QUESTIONS)):
        return "quiz_none"
    if first in (quiz.RIGHT, quiz.NOT_QUITE, quiz.CANT_CHECK):
        return "quiz_feedback"
    if first == quiz.STOPPED or first.startswith(_prefix(quiz.LAST)):
        return "quiz_end"
    if first in (quiz.WHICH_TOPIC, lesson_plan.WHICH_TOPIC):
        return "asks_topic"
    if first == lesson_plan.NOTED:
        return "noted"
    if first.startswith(_prefix(lesson_plan.INTRO["all"])):
        return "lesson_plan"
    revision = _revision_module()
    if revision is not None:
        if first.startswith(_prefix(revision.INTRO)):
            return "revision"
        if first == getattr(revision, "WHICH_TOPIC", None):
            return "asks_topic"
        if first == getattr(revision, "OTHER_GRADE", None):
            return "grade_not_covered"
    return "answer"


def _revision_module():
    """Revision mode (journey 2), if this copy of the code has it."""
    try:
        from pocketinfer.specialist import revision
    except ImportError:
        return None
    return revision


def _quiz_details(rec: TurnRecord) -> None:
    from pocketinfer.specialist import quiz

    if rec.kind != "quiz_feedback":
        return
    first = rec.sentences_en[0]
    rec.verdict = {quiz.RIGHT: "right", quiz.NOT_QUITE: "not_quite", quiz.CANT_CHECK: "cant_check"}[first]
    if quiz.NEXT in rec.sentences_en:
        rec.quiz_next = "next"
    elif any(s.startswith(_prefix(quiz.LAST)) for s in rec.sentences_en):
        rec.quiz_next = "end"


class Runner:
    """One LoopHarness, built as the voice loop builds it, reused for every script."""

    def __init__(self, opts):
        import pocketinfer.specialist as specialist_pkg
        from local import voice_loop

        self.voice_loop = voice_loop
        self.pkg = specialist_pkg
        args = Namespace(
            text=["(journey runner)"],          # typed input: no microphone, no speech models
            from_wav=None, from_image=None, question_index=None,
            mic_device=None, trigger="enter", gpio_chip=None, gpio_line=None,
            asr="auto", mt=opts.mt, tts=opts.tts, llm=opts.llm, no_speak=not opts.speak,
            in_lang="auto", out_lang="auto",
            index=opts.index, embed_model=opts.embed_model, whisper_model="tiny.en",
            ollama_model=opts.ollama_model, log_level="WARNING",
        )
        self.args = args
        t0 = time.time()
        self.harness = voice_loop.LoopHarness(args)
        self.build_seconds = time.time() - t0
        self.harness._texts = []
        self._wrap()

    def _wrap(self) -> None:
        """Record what each turn says, without changing what it does."""
        h = self.harness
        self._capture: Dict = {}
        orig_replay, orig_translate = h._replay, h._translate_query
        orig_speak = self.pkg.speak_stream

        def replay(command):
            self._capture["replay"] = command
            self._capture["replayed"] = list(h.session.last_text) if h.session.last_text else []
            return orig_replay(command)

        def translate(raw, T):
            out = orig_translate(raw, T)
            self._capture["query_en"] = out
            return out

        def speak_stream(sentences, play_fn, on_sentence=None):
            t0 = time.time()
            stamps = self._capture.setdefault("stamps", [])

            def timed():
                for s in sentences:
                    stamps.append(time.time() - t0)
                    yield s
            results, first = orig_speak(timed(), play_fn, on_sentence)
            self._capture["results"] = results
            return results, first

        h._replay, h._translate_query = replay, translate
        # run_once imports speak_stream from the package when it runs, so this reaches it.
        self.pkg.speak_stream = speak_stream

    def index_classes(self) -> str:
        """Which classes the index's passages are tagged with, for the report."""
        from collections import Counter

        counts: Counter = Counter()
        chunks = self.harness.engine.index.chunks
        for c in chunks:
            tags = c.get("grade") or []
            for tag in (tags if isinstance(tags, (list, tuple)) else [tags]):
                counts[str(tag)] += 1
        if not counts:
            return (f"{len(chunks)} passages, none tagged with a class, so a search for one class "
                    "returns the same passages as any other")
        return f"{len(chunks)} passages, tagged " + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))

    def new_session(self) -> None:
        from pocketinfer.specialist.session import Session

        self.harness.session = Session()

    def turn(self, said: str) -> TurnRecord:
        h = self.harness
        rec = TurnRecord(said=said)
        self._capture = {}
        h._texts = [said]
        buf = io.StringIO()
        t0 = time.time()
        try:
            with contextlib.redirect_stdout(buf):
                h.run_once(None)
        except Exception:  # noqa: BLE001
            rec.error = traceback.format_exc(limit=4)
        rec.seconds = time.time() - t0
        rec.log = buf.getvalue()
        rec.lang_in, rec.lang_out = h.turn_in, h.turn_out
        rec.query_en = self._capture.get("query_en")
        results = self._capture.get("results") or []
        for r in results:
            if r.refused:
                rec.reasons.append(r.reason)
            if (r.spoken_text or "").strip():
                rec.sentences_en.append(r.text_en)
                rec.sentences_spoken.append(r.spoken_text)
            if r.example or _is_example(r.text_en):
                rec.examples.append(r.text_en)
            rec.citations.extend(r.citations)
        rec.refused = bool(rec.reasons)
        stamps = self._capture.get("stamps") or []
        rec.first_sentence_seconds = stamps[0] if stamps else None
        if results:
            rec.rewrite = h.session.last_rewrite
        rec.replayed = self._capture.get("replayed", [])
        reset = "[session] new topic" in rec.log
        rec.kind = _classify(rec, self._capture.get("replay"), reset)
        _quiz_details(rec)
        return rec


def _is_example(text: str) -> bool:
    from pocketinfer.specialist.grounding import is_example

    return is_example(text)


# --------------------------------------------------------------------------- checks
def _words(text: str) -> set:
    return set(re.findall(r"[^\W\d_]{3,}", (text or "").lower()))


def _script(text: str) -> str:
    hindi, english = len(_DEVANAGARI.findall(text or "")), len(_LATIN.findall(text or ""))
    return "hi" if hindi and hindi >= english else "en"


def check(rec: TurnRecord, expect: dict, earlier: List[TurnRecord]) -> None:
    """Fill rec.checks from the turn's expectations. Unknown keys fail loudly."""
    text_en = " ".join(rec.sentences_en)
    lower = text_en.lower()
    add = rec.checks.append
    if rec.error:
        add(("ran", False, rec.error.strip().splitlines()[-1]))
    for key, want in expect.items():
        if key == "kind":
            kinds = [want] if isinstance(want, str) else list(want)
            add((f"kind {'|'.join(kinds)}", rec.kind in kinds, f"got {rec.kind}"))
        elif key == "refused":
            add((f"refused={want}", rec.refused == want,
                 "; ".join(rec.reasons) if rec.refused else "not refused"))
        elif key == "contains_any":
            hit = [w for w in want if w.lower() in lower]
            add((f"mentions any of {want}", bool(hit), f"found {hit}" if hit else "none found"))
        elif key == "contains_all":
            miss = [w for w in want if w.lower() not in lower]
            add((f"mentions all of {want}", not miss, f"missing {miss}" if miss else "all found"))
        elif key == "not_contains":
            hit = [w for w in want if w.lower() in lower]
            add((f"does not mention {want}", not hit, f"found {hit}" if hit else "none found"))
        elif key == "language":
            spoken = " ".join(rec.sentences_spoken or rec.replayed)
            got = _script(spoken) if spoken else "(nothing said)"
            add((f"spoken in {want}", got == want and rec.lang_out == want,
                 f"script {got}, answer language {rec.lang_out}"))
        elif key == "min_sentences":
            n = len(rec.sentences_en) - (1 if rec.kind == "refusal" else 0)
            add((f"at least {want} sentences", n >= want, f"{n} sentences"))
        elif key == "has_example":
            add((f"has a worked example={want}", bool(rec.examples) == want,
                 f"{len(rec.examples)} example(s)"))
        elif key == "cited":
            add(("cites a textbook page", bool(rec.citations) == want, f"{len(rec.citations)} citation(s)"))
        elif key == "verdict":
            add((f"quiz verdict {want}", rec.verdict == want, f"got {rec.verdict}"))
        elif key == "quiz_next":
            add((f"then quiz {want}", rec.quiz_next == want, f"got {rec.quiz_next}"))
        elif key == "same_as_turn":
            ref = earlier[want] if 0 <= want < len(earlier) else None
            same = ref is not None and rec.replayed == ref.sentences_spoken and bool(rec.replayed)
            add((f"replays turn {want + 1}", same,
                 f"{len(rec.replayed)} sentence(s) replayed" if rec.replayed else "nothing replayed"))
        elif key == "differs_from_turn":
            ref = earlier[want] if 0 <= want < len(earlier) else None
            a = _words(" ".join(ref.sentences_en)) if ref else set()
            b = _words(text_en)
            share = len(a & b) / max(1, min(len(a), len(b)))
            ok = bool(a) and bool(b) and share < SAME_WORDS
            add((f"differs from turn {want + 1}", ok, f"{share:.0%} of words shared (bar {SAME_WORDS:.0%})"))
        elif key == "example_differs_from_turn":
            ref = earlier[want] if 0 <= want < len(earlier) else None
            a, b = _words(" ".join(ref.examples)) if ref else set(), _words(" ".join(rec.examples))
            share = len(a & b) / max(1, min(len(a), len(b)))
            ok = bool(a) and bool(b) and share < SAME_WORDS
            add((f"example differs from turn {want + 1}'s", ok,
                 "no example in one of them" if not (a and b) else f"{share:.0%} of words shared"))
        else:
            add((f"unknown expectation {key!r}", False, "fix journeys.json"))


# --------------------------------------------------------------------------- statuses
def variant_status(turns: List[TurnRecord], specs: List[dict]) -> str:
    if all(t.passed for t in turns):
        return "PASS"
    if any(not t.passed and s.get("core") for t, s in zip(turns, specs)) or not any(t.passed for t in turns):
        return "FAIL"
    return "PARTIAL"


def journey_status(statuses: List[str], capped: bool) -> str:
    if not statuses:
        return "NOT RUN"
    if all(s == "PASS" for s in statuses):
        status = "PASS"
    elif all(s == "FAIL" for s in statuses):
        status = "FAIL"
    else:
        status = "PARTIAL"
    return "PARTIAL" if capped and status == "PASS" else status


# --------------------------------------------------------------------------- report
def _q(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")


def _turn_lines(i: int, t: TurnRecord, spec: dict) -> List[str]:
    mark = "ok" if t.passed else "FAILED"
    core = " (core)" if spec.get("core") else ""
    first = f", first sentence {t.first_sentence_seconds:.1f} s" if t.first_sentence_seconds is not None else ""
    lines = [f"{i + 1}. **{mark}{core}** — user: “{t.said}”  ",
             f"   kind `{t.kind}`, {t.seconds:.1f} s{first}, language {t.lang_in}→{t.lang_out}  "]
    if spec.get("why"):
        lines.append(f"   checks: {spec['why']}  ")
    if t.query_en and t.query_en != t.said:
        lines.append(f"   translated: “{t.query_en}”  ")
    if t.rewrite:
        lines.append(f"   understood as: “{t.rewrite}”  ")
    said = t.replayed if t.kind == "replay" else t.sentences_spoken
    if said:
        lines.append("   device said:")
        for s in said:
            lines.append(f"   > {s}  ")
        if t.lang_out != "en" and t.sentences_en and t.kind != "replay":
            lines.append("   in English: " + " ".join(t.sentences_en))
    elif t.kind != "replay":
        lines.append("   device said nothing")
    if t.reasons:
        lines.append(f"   refusal reason: {'; '.join(t.reasons)}  ")
    for name, ok, detail in t.checks:
        if not ok:
            lines.append(f"   - failed: {name} ({detail})")
    if t.error:
        lines.append("   ```\n" + "\n".join("   " + l for l in t.error.strip().splitlines()) + "\n   ```")
    return lines


def write_report(path: str, meta: dict, results: List[dict]) -> str:
    out = ["# User journey run", ""]
    out.append(f"Run {meta['when']} on {meta['host']} ({meta['machine']}), language `{meta['lang']}`. "
               f"Index `{meta['index']}`, answer model `{meta['model']}`, translation `{meta['mt']}`. "
               f"Setup took {meta['build_seconds']:.0f} s and the scripts {meta['run_seconds'] / 60:.1f} min.")
    out.append("")
    out.append(f"Index: {meta['index_classes']}.")
    out.append("")
    out.append("Each turn is typed into the voice loop's own harness, so it takes the device's real path "
               "after speech recognition. Checks are on behaviour (a lesson plan, a quiz question, an answer "
               "in Hindi), not on exact words. Scripts: `tests/journeys/journeys.json`.")
    out.append("")
    out.append("| Journey | Result | Variants | Main reason |")
    out.append("|---|---|---|---|")
    for j in results:
        counts = ", ".join(f"{v['id']} {v['status']}" for v in j["variants"]) or "none for this language"
        out.append(f"| {j['id']} {j['name']} | **{j['status']}** | {counts} | {_q(j['reason'])} |")
    out.append("")
    for j in results:
        out.append(f"## {j['id']} {j['name']}: {j['status']}")
        out.append("")
        out.append(j["description"])
        out.append("")
        if j.get("cap_note"):
            out.append(f"Capped: {j['cap_note']}")
            out.append("")
        for v in j["variants"]:
            out.append(f"### {v['id']}: {v['status']} — {v['title']}")
            out.append("")
            if v.get("probes"):
                out.append(f"What it probes: {v['probes']}")
                out.append("")
            failing = [(i, t, s) for i, (t, s) in enumerate(zip(v["turns"], v["specs"])) if not t.passed]
            if failing:
                out.append("Failing turns:")
                out.append("")
                for i, t, s in failing:
                    out.extend(_turn_lines(i, t, s))
                    out.append("")
            out.append("<details><summary>Whole conversation</summary>")
            out.append("")
            for i, (t, s) in enumerate(zip(v["turns"], v["specs"])):
                out.extend(_turn_lines(i, t, s))
                out.append("")
            out.append("</details>")
            out.append("")
    text = "\n".join(out).rstrip() + "\n"
    if path:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    return text


def _reason(variants: List[dict]) -> str:
    """The first failed check of each failing variant, briefly."""
    bits = []
    for v in variants:
        for t in v["turns"]:
            bad = [(n, d) for n, ok, d in t.checks if not ok]
            if bad:
                n, d = bad[0]
                bits.append(f"{v['id']}: “{t.said[:50]}” {n} ({d})")
                break
    return "; ".join(bits) if bits else "all checks passed"


# --------------------------------------------------------------------------- main
def _service_up(url: str) -> bool:
    import requests

    try:
        return requests.get(url, timeout=3).status_code == 200
    except requests.RequestException:
        return False


def main(argv=None) -> int:
    from local.corpus_index import resolve_index

    ap = argparse.ArgumentParser(description="Run the scripted user journeys through the real Specialist.")
    ap.add_argument("--lang", choices=("en", "hi"), default="en",
                    help="which variants to run: en (any machine) or hi (needs Bhashini, so the Jetson)")
    ap.add_argument("--journey", action="append", default=None,
                    help="J1..J4; repeat or comma-separate for several (default: all)")
    ap.add_argument("--variant", action="append", default=None, help="run only these variant ids")
    ap.add_argument("--out", default=None, help="write the Markdown report here")
    ap.add_argument("--json", default=None, help="also write every turn as JSON here")
    ap.add_argument("--scripts", default=SCRIPTS)
    ap.add_argument("--index", default=resolve_index())
    ap.add_argument("--llm", default="ollama", help="ollama|extractive")
    ap.add_argument("--ollama-model", default="qwen2.5:1.5b")
    ap.add_argument("--embed-model", default="intfloat/multilingual-e5-small")
    ap.add_argument("--mt", default=None, help="default: none for en, bhashini for hi")
    ap.add_argument("--speak", action="store_true", help="also speak every answer aloud (slow)")
    ap.add_argument("--tts", default="bhashini")
    opts = ap.parse_args(argv)
    opts.mt = opts.mt or ("bhashini" if opts.lang == "hi" else "none")

    if not os.path.exists(os.path.join(opts.index, "embeddings.npy")):
        print(f"No index at {opts.index}. Build it: python ingest/build_index.py")
        return 2
    if opts.mt == "bhashini" and not _service_up(BHASHINI_HEALTH):
        print(f"The Bhashini service is not answering at {BHASHINI_HEALTH}. Hindi needs it, so run "
              "--lang hi on the Jetson.")
        return 2
    if opts.llm == "ollama" and not _service_up(OLLAMA_TAGS):
        print("Ollama is not running.")
        return 2

    with open(opts.scripts, encoding="utf-8") as f:
        scripts = json.load(f)
    wanted = {j.strip().upper() for arg in (opts.journey or []) for j in arg.split(",") if j.strip()}
    journeys = [j for j in scripts["journeys"] if not wanted or j["id"].upper() in wanted]
    if not journeys:
        print(f"No journey matches {sorted(wanted)}")
        return 2

    print(f"Building the Specialist (index {opts.index}) ...", flush=True)
    runner = Runner(opts)
    print(f"Ready in {runner.build_seconds:.0f} s", flush=True)

    started = time.time()
    results = []
    for j in journeys:
        variants = [v for v in j["variants"] if v["lang"] == opts.lang
                    and (not opts.variant or v["id"] in opts.variant)]
        done = []
        for v in variants:
            print(f"\n== {v['id']}: {v['title']}", flush=True)
            runner.new_session()
            turns: List[TurnRecord] = []
            for spec in v["turns"]:
                rec = runner.turn(spec["say"])
                check(rec, spec.get("expect", {}), turns)
                turns.append(rec)
                print(f"  [{'ok' if rec.passed else 'FAIL'}] {rec.seconds:5.1f}s {rec.kind:14s} {spec['say'][:60]}",
                      flush=True)
                for s in (rec.replayed if rec.kind == "replay" else rec.sentences_spoken):
                    print(f"        > {s}", flush=True)
                for name, ok, detail in rec.checks:
                    if not ok:
                        print(f"        failed: {name} ({detail})", flush=True)
            status = variant_status(turns, v["turns"])
            print(f"  => {status}", flush=True)
            done.append({**v, "status": status, "turns": turns, "specs": v["turns"]})
        capped = bool(j.get("needs_lang")) and j["needs_lang"] != opts.lang
        status = journey_status([v["status"] for v in done], capped)
        reason = _reason(done)
        if capped:
            reason = f"{j['cap_note']} {reason}" if reason != "all checks passed" else j["cap_note"]
        if not done:
            reason = f"no {opts.lang} variants scripted"
        results.append({**j, "status": status, "variants": done, "reason": reason,
                        "cap_note": j.get("cap_note") if capped else None})

    meta = {
        "when": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "host": platform.node(), "machine": f"{platform.system()} {platform.machine()}",
        "lang": opts.lang, "index": os.path.relpath(opts.index, ROOT) if opts.index.startswith(ROOT) else opts.index,
        "model": opts.ollama_model if opts.llm == "ollama" else opts.llm, "mt": opts.mt,
        "build_seconds": runner.build_seconds, "run_seconds": time.time() - started,
        "index_classes": runner.index_classes(),
    }
    report = write_report(opts.out, meta, results)
    if opts.json:
        with open(opts.json, "w", encoding="utf-8") as f:
            json.dump({"meta": meta, "journeys": [
                {"id": j["id"], "status": j["status"], "variants": [
                    {"id": v["id"], "status": v["status"],
                     "turns": [{k: getattr(t, k) for k in TurnRecord.__dataclass_fields__} for t in v["turns"]]}
                    for v in j["variants"]]} for j in results]}, f, ensure_ascii=False, indent=1)
    print("\n" + "\n".join(line for line in report.splitlines() if line.startswith("|")))
    if opts.out:
        print(f"\nReport: {opts.out}")
    return 0 if all(j["status"] == "PASS" for j in results) else 1


if __name__ == "__main__":
    sys.exit(main())
