#!/usr/bin/env python3
"""Score the safety check on the frozen 84, the development set and the adversarial set.

    python3 tests/safety/score.py [results.json]
"""

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "python"))

from pocketinfer.specialist.safety import SafetyCheck, load_safety_model  # noqa: E402


def load(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return json.load(f)


def sets():
    frozen, dev, adv = load("frozen_84.json"), load("development.json"), load("adversarial.json")
    hold = load("holdout.json")
    flat = lambda d: [q for qs in d.values() for q in qs]  # noqa: E731
    return {
        "Frozen unsafe": (flat(frozen["unsafe"]), True), "Frozen safe": (frozen["safe"], False),
        "Development unsafe": (flat(dev["unsafe"]), True), "Development safe": (dev["safe"], False),
        "Adversarial, must flag": (flat(adv["must_flag"]), True), "Adversarial, must pass": (adv["must_pass"], False),
        "Holdout, must flag": (flat(hold["must_flag"]), True), "Holdout, must pass": (hold["must_pass"], False),
    }


def main():
    check = SafetyCheck(load_safety_model())
    results, n, t0 = {}, 0, time.time()
    for name, (questions, unsafe) in sets().items():
        flags = {q: check.check(q) for q in questions}
        n += len(questions)
        wrong = [q for q, f in flags.items() if bool(f) != unsafe]
        results[name] = {q: f and f"{f.category}/{f.source}" for q, f in flags.items()}
        print(f"{name:24} flagged {sum(bool(f) for f in flags.values())}/{len(questions)}")
        for q in wrong:
            print(f"     {'MISSED' if unsafe else 'FLAGGED'}: {q}  {'' if unsafe else flags[q].category + '/' + flags[q].source}")
    print(f"\n{(time.time() - t0) / n * 1000:.0f} ms per question")
    if len(sys.argv) > 1:
        with open(sys.argv[1], "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
