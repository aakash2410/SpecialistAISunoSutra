#!/usr/bin/env python3
"""Judge an NLI model on hand-labelled cases that were never used for training.

    python3 training/eval_nli.py --model-dir local/models/nli-deberta-v3-small-onnx --cases DIR
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
from pocketinfer.models.verify import SUPPORT_THRESHOLD, Verifier  # noqa: E402
from pocketinfer.specialist import safety as S  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Judge an NLI model on held-out, hand-labelled cases.")
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--cases", required=True, help="dir with rows.json, cases.py and holdout.py")
    ap.add_argument("--unsupported", required=True, help="json: {row index: [sentence indexes]}")
    args = ap.parse_args()
    sys.path.insert(0, args.cases)
    from cases import CASES
    from holdout import SAFE, UNSAFE

    v = Verifier(args.model_dir)
    print("model:", args.model_dir)
    rows = json.load(open(os.path.join(args.cases, "rows.json")))
    bad = {int(k): set(x) for k, x in json.load(open(args.unsupported))["unsupported"].items()}
    based = set(json.load(open(args.unsupported))["passage_based"])
    tp = fn = tn = fp = 0
    missed, blocked = [], []
    t = time.time(); n = 0
    for i, r in enumerate(rows):
        faithful = r["group"] in ("covered", "pilot_normal", "pilot_counterfactual")
        sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", r["answer"].strip()) if s.strip()]
        for j, s in enumerate(sents):
            truth = (faithful and j not in bad.get(i, ())) or i in based
            hit = type("P", (), {"text": r["passages"][0]})
            ok = v.supporting_passage(s, [hit]) is not None
            n += 1
            if truth and ok: tp += 1
            elif truth: fn += 1; blocked.append(s)
            elif ok: fp += 1; missed.append(s)
            else: tn += 1
    print(f"ANSWER CHECK: caught {tn}/{tn + fp} bad sentences, wrongly blocked {fn}/{fn + tp} good, "
          f"accuracy {(tp + tn) / (tp + tn + fp + fn):.3f}, {(time.time() - t) / n * 1000:.0f} ms per sentence")
    for s in missed[:8]: print("   passed bad:", s[:95])
    for s in blocked[:6]: print("   blocked good:", s[:95])

    def nli_only(q):
        return S.SafetyCheck(v)._nli_flags(q)

    tune_u = [q for c, qs in CASES.items() if c != "safe" for q in qs]
    fresh_u = [q for qs in UNSAFE.values() for q in qs]
    for name, unsafe, safe in (("first set", tune_u, CASES["safe"]), ("fresh set", fresh_u, SAFE)):
        c = sum(nli_only(q) for q in unsafe); f = [q for q in safe if nli_only(q)]
        print(f"SAFETY, NLI ALONE, {name}: caught {c}/{len(unsafe)} unsafe, wrongly flagged {len(f)}/{len(safe)} safe")
        for q in [q for q in unsafe if not nli_only(q)][:6]: print("   missed:", q[:90])
        for q in f[:6]: print("   flagged:", q[:90])
    return 0


if __name__ == "__main__":
    sys.exit(main())
