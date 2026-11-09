"""Score the answer check on the blind set: does it keep supported sentences and drop the rest?"""

import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if "--set" in sys.argv:
    HERE = os.path.join(HERE, "..", sys.argv[sys.argv.index("--set") + 1])
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "python"))

from pocketinfer.models.embed import Embed  # noqa: E402
from pocketinfer.models.verify import Verifier  # noqa: E402
from pocketinfer.specialist.answer_guard import QUESTION_SIMILARITY, contradicts  # noqa: E402
from pocketinfer.specialist.vector_index import Retrieved  # noqa: E402


def load(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def main():
    passages = {p["pid"]: p for p in load("passages.jsonl")}
    verifier = Verifier()
    embedder = Embed()
    guarded = collections.Counter()
    on_topic = collections.Counter()
    kept = collections.Counter()
    total = collections.Counter()
    mistakes = []
    for item in load("answer_check.jsonl"):
        p = passages[item["pid"]]
        source = Retrieved(1.0, p["text"], p["content_id"], p["chapter"], p["section"], p["page"], "")
        was_kept = verifier.supporting_passage(item["sentence"], [source]) is not None
        question = embedder.embed_one(item["question"], kind="query")
        similar = float(question @ embedder.embed_one(item["sentence"], kind="passage")) >= QUESTION_SIMILARITY
        passes_guard = was_kept and not contradicts(item["sentence"], [p["text"]])
        guarded[item["type"]] += passes_guard
        on_topic[item["type"]] += passes_guard and similar
        total[item["type"]] += 1
        kept[item["type"]] += was_kept
        if was_kept != item["supported"]:
            mistakes.append(("kept" if was_kept else "dropped", item["id"], item["sentence"]))

    print(f"{'type':18} kept")
    for t in total:
        print(f"{t:18} {kept[t]}/{total[t]}")
    good = [t for t in total if t in ("paraphrase", "simplified", "off_question")]
    bad = [t for t in total if t not in good]
    print(f"\n{'type':18} model only | + fact checks | + question relevance")
    for t in total:
        print(f"{t:18} {kept[t]}/{total[t]} | {guarded[t]}/{total[t]} | {on_topic[t]}/{total[t]}")
    print(f"\nSupported sentences kept:   {sum(kept[t] for t in good)}/{sum(total[t] for t in good)}")
    print(f"Unsupported sentences let through: {sum(kept[t] for t in bad)}/{sum(total[t] for t in bad)}")
    print(f"Agreement with the labels:  {sum(total.values()) - len(mistakes)}/{sum(total.values())}")
    print("\nDisagreements:")
    for verdict, item_id, sentence in mistakes:
        print(f"  {verdict:7} {item_id:22} {sentence}")


if __name__ == "__main__":
    main()
