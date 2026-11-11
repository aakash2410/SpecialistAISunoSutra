#!/usr/bin/env python3
"""Build fine-tuning data for the NLI model, from free local sources only.

No Claude, no paid API and no generated text: examples come from DIKSHA passages and the
questions printed in them, rule-based edits, and two public datasets, Aegis 2.0 (CC BY 4.0)
and a suicide-prediction set (MIT). Hand-written test sets are never used here, so they
stay a fair test.

Each output line is {"premise", "hypothesis", "label", "task", "source"}, with labels
matching the model: 0 contradiction, 1 entailment, 2 neutral. Parts already built are
skipped, so a stopped run resumes.

    python3.12 training/build_nli_data.py --out training/data
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from local.corpus_index import full_index  # noqa: E402

CONTRADICTION, ENTAILMENT, NEUTRAL = 0, 1, 2
AEGIS = ("https://huggingface.co/datasets/nvidia/Aegis-AI-Content-Safety-Dataset-2.0/"
         "resolve/refs%2Fconvert%2Fparquet/default/train/0000.parquet")
SUICIDE = ("https://huggingface.co/datasets/vibhorag101/suicide_prediction_dataset_phr/"
           "resolve/refs%2Fconvert%2Fparquet/default/train/0000.parquet")

# Pairs where swapping one word reverses a fact, the kind of error the check must catch.
OPPOSITES = [("hot", "cold"), ("hotter", "colder"), ("increase", "decrease"), ("increases", "decreases"),
             ("large", "small"), ("larger", "smaller"), ("more", "less"), ("higher", "lower"),
             ("high", "low"), ("fast", "slow"), ("faster", "slower"), ("before", "after"),
             ("above", "below"), ("first", "last"), ("positive", "negative"), ("acid", "base"),
             ("acids", "bases"), ("absorb", "release"), ("absorbs", "releases"), ("gain", "lose"),
             ("gains", "loses"), ("north", "south"), ("east", "west"), ("add", "subtract"),
             ("sum", "difference"), ("multiply", "divide"), ("rich", "poor"), ("strong", "weak"),
             ("always", "never"), ("expand", "contract"), ("expands", "contracts"), ("day", "night"),
             ("inside", "outside"), ("oxygen", "carbon dioxide"), ("living", "non-living"),
             ("from", "to"), ("many", "few"), ("long", "short"), ("heavy", "light"), ("true", "false")]
SWAP = {a: b for a, b in OPPOSITES} | {b: a for a, b in OPPOSITES}


def clean_passages(path: str, rng: random.Random):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            c = json.loads(line)
            t = c["text"]
            letters = sum(ch.isalpha() and ch.isascii() for ch in t)
            if len(t) > 200 and letters / len(t) > 0.72:
                rows.append(c)
    rng.shuffle(rows)
    return rows


def sentences(text: str):
    out = []
    for s in re.split(r"(?<=[.!?])\s+", text.replace("\n", " ")):
        s = s.strip()
        words = s.split()
        if 7 <= len(words) <= 32 and s[0].isupper() and s[-1] in ".!?" and sum(c.isalpha() for c in s) / len(s) > 0.75:
            out.append(s)
    return out


def flip(sentence: str, rng: random.Random):
    """Reverse a fact with one swapped word or number. Returns None if nothing can flip."""
    numbers = re.findall(r"\b\d+\b", sentence)
    words = [w for w in re.findall(r"[A-Za-z-]+", sentence) if w.lower() in SWAP]
    if numbers and (not words or rng.random() < 0.3):
        n = rng.choice(numbers)
        return re.sub(rf"\b{n}\b", str(int(n) + rng.randint(2, 9)), sentence, count=1)
    if not words:
        return None
    w = rng.choice(words)
    new = SWAP[w.lower()]
    new = new.capitalize() if w[0].isupper() else new
    return re.sub(rf"\b{re.escape(w)}\b", new, sentence, count=1)


def write(path: str, rows) -> None:
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    print(f"wrote {len(rows)} to {os.path.basename(path)}", flush=True)


def support_rules(passages, rng, per: int):
    """Exact sentences, reversed facts, and related sentences from other pages."""
    by_book = {}
    for p in passages:
        by_book.setdefault(p["content_id"], []).append(p)
    rows = []
    for p in passages[:per]:
        sents = sentences(p["text"])
        if not sents:
            continue
        s = rng.choice(sents)
        if s.endswith("?"):
            continue
        rows.append(dict(premise=p["text"], hypothesis=s, label=ENTAILMENT, task="support", source="exact"))
        flipped = flip(s, rng)
        if flipped and flipped != s:
            rows.append(dict(premise=p["text"], hypothesis=flipped, label=CONTRADICTION, task="support", source="flip"))
        others = [q for q in by_book[p["content_id"]] if q["page"] != p["page"]] or rng.sample(passages, 3)
        other = sentences(rng.choice(others)["text"])
        other = [o for o in other if o not in p["text"]]
        if other:
            rows.append(dict(premise=p["text"], hypothesis=rng.choice(other), label=NEUTRAL, task="support", source="other_page"))
    return rows


CONCERN = {"abuse": ["A child is being beaten or abused.", "A child is being touched in a sexual way."],
           "self_harm": ["The speaker wants to die or hurt themselves.", "A person is thinking about suicide."],
           "medical": ["The speaker asks which medicine to take."],
           "violence": ["The speaker wants to hurt, kill or steal.", "The speaker wants to make a weapon, bomb or poison."],
           "adult": ["The speaker is asking about sex or pornography."],
           "hate": ["The speaker says one group of people is worse than another."]}
SAFE_LABELS = ["The speaker is asking about a school subject.", "The speaker is asking how something works.",
               "The speaker is asking a question about science, maths, history or language."]
ALL_CONCERNS = [h for hs in CONCERN.values() for h in hs]
def safety_pairs(request: str, category: str, source: str, rng):
    if category == "safe":
        return [dict(premise=request, hypothesis=rng.choice(SAFE_LABELS), label=ENTAILMENT, task="safety", source=source),
                dict(premise=request, hypothesis=rng.choice(ALL_CONCERNS), label=CONTRADICTION, task="safety", source=source)]
    wrong = [h for c, hs in CONCERN.items() if c != category for h in hs]
    return [dict(premise=request, hypothesis=rng.choice(CONCERN[category]), label=ENTAILMENT, task="safety", source=source),
            dict(premise=request, hypothesis=rng.choice(SAFE_LABELS), label=CONTRADICTION, task="safety", source=source),
            dict(premise=request, hypothesis=rng.choice(wrong), label=NEUTRAL, task="safety", source=source)]


HARD_TOPICS = re.compile(r"\b(war|battle|fought|caste|untouchab|revolt|revolution|empire|invad|killed|religion|women|poison|acid|blood|reproduc|puberty|disease|medicine|fire|violence|slave)", re.I)


def safety_textbook_questions(chunks_path: str, rng, limit: int):
    """Real questions printed in the textbooks are safe by definition, including the ones
    on topics that only sound alarming, so those are all kept."""
    questions = {}
    with open(chunks_path, encoding="utf-8") as f:
        for line in f:
            for s in re.split(r"(?<=[.!?])\s+|\n", json.loads(line)["text"]):
                s = re.sub(r"^\s*(Q\.?\s*\d*[.):]?|\d+[.)]|\(?[a-z]\)|[ivx]+\.)\s*", "", s.strip()).strip()
                ascii_letters = sum(c.isalpha() and c.isascii() for c in s)
                if s.endswith("?") and 4 <= len(s.split()) <= 25 and s[0].isupper() and ascii_letters / len(s) > 0.75:
                    questions[s] = True
    questions = list(questions)
    rng.shuffle(questions)
    hard = [q for q in questions if HARD_TOPICS.search(q)]
    rest = [q for q in questions if not HARD_TOPICS.search(q)][:limit]
    print(f"textbook questions used: {len(hard)} on alarming topics, {len(rest)} others", flush=True)
    return [r for q in hard for r in safety_pairs(q, "safe", "textbook_hard", rng)] + \
           [r for q in rest for r in safety_pairs(q, "safe", "textbook", rng)]


def safety_self_harm(rng, path: str, limit: int):
    """The public text has its small words stripped, so it reads roughly, but the topic is clear."""
    import pyarrow.parquet as pq

    if not os.path.exists(path):
        urllib.request.urlretrieve(SUICIDE, path)
    rows, used = [], 0
    table = pq.read_table(path, columns=["text", "label"]).to_pylist()
    rng.shuffle(table)
    for r in table:
        if r["label"] == "suicide" and 4 <= len(r["text"].split()) <= 30:
            rows.extend(safety_pairs(r["text"].strip(), "self_harm", "suicide_dataset", rng))
            used += 1
            if used >= limit:
                break
    print(f"self-harm texts used: {used}", flush=True)
    return rows


def safety_aegis(rng, path: str):
    import pyarrow.parquet as pq

    if not os.path.exists(path):
        urllib.request.urlretrieve(AEGIS, path)
    t = pq.read_table(path, columns=["prompt", "prompt_label", "violated_categories"]).to_pylist()
    rows, counts = [], {}
    for r in t:
        q = (r["prompt"] or "").strip()
        if q == "REDACTED" or not 3 <= len(q.split()) <= 35:
            continue
        v = (r["violated_categories"] or "").lower()
        if r["prompt_label"] == "safe":
            cat = "safe"
        elif "sexual" in v:
            cat = "adult"
        elif "hate" in v or "harassment" in v:
            cat = "hate"
        elif any(k in v for k in ("violence", "gun", "criminal", "threat", "weapon")):
            cat = "violence"
        else:
            continue
        counts[cat] = counts.get(cat, 0) + 1
        if counts[cat] <= 1200:
            rows.extend(safety_pairs(q, cat, "aegis", rng))
    print("aegis prompts used:", counts, flush=True)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Build NLI fine-tuning data from free local sources.")
    ap.add_argument("--out", default="training/data")
    ap.add_argument("--chunks", default=os.path.join(full_index(), "chunks.jsonl"))
    ap.add_argument("--rules", type=int, default=6000)
    ap.add_argument("--questions", type=int, default=1200)
    ap.add_argument("--self-harm", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=13)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    rng = random.Random(args.seed)
    passages = clean_passages(args.chunks, rng)
    print(f"{len(passages)} usable passages", flush=True)
    parts = {
        "support_rules": lambda: support_rules(passages, rng, args.rules),
        "safety_aegis": lambda: safety_aegis(rng, os.path.join(args.out, "aegis.parquet")),
        "safety_questions": lambda: safety_textbook_questions(args.chunks, rng, args.questions),
        "safety_self_harm": lambda: safety_self_harm(rng, os.path.join(args.out, "suicide.parquet"), args.self_harm),
    }
    for name, build in parts.items():
        path = os.path.join(args.out, name + ".jsonl")
        if os.path.exists(path):
            print("already built:", name, flush=True)
            continue
        write(path, build())
    print("ALLDONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
