#!/usr/bin/env python3
"""Fine-tune nli-deberta-v3-small on the data from build_nli_data.py.

AdamW with a cosine learning-rate schedule and a short warm-up, at a low rate because
the base model is already good at general entailment and only needs nudging. A share of
the data is held out by passage or question, so nothing in validation was trained on.

    python3.12 training/train_nli.py --data training/data --out training/runs/v1
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import random
import sys
import time

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

BASE = "cross-encoder/nli-deberta-v3-small"


def empty_cache(device: str) -> None:
    if device == "mps":
        torch.mps.empty_cache()


def load(data_dir: str):
    rows = []
    for path in sorted(glob.glob(os.path.join(data_dir, "*.jsonl"))):
        with open(path, encoding="utf-8") as f:
            rows.extend(json.loads(line) for line in f if line.strip())
    return rows


def split(rows, seed: int, share: float = 0.1):
    """Hold out whole premises, so one passage never sits on both sides."""
    premises = sorted({r["premise"] for r in rows})
    random.Random(seed).shuffle(premises)
    held = set(premises[: int(len(premises) * share)])
    return [r for r in rows if r["premise"] not in held], [r for r in rows if r["premise"] in held]


def evaluate(model, tok, rows, device, batch: int = 8):
    model.eval()
    by = {}
    with torch.no_grad():
        for i in range(0, len(rows), batch):
            b = rows[i:i + batch]
            enc = tok([r["premise"] for r in b], [r["hypothesis"] for r in b], padding=True, truncation=True,
                      max_length=256, return_tensors="pt").to(device)
            pred = model(**enc).logits.argmax(-1).tolist()
            for r, p in zip(b, pred):
                k = by.setdefault(r["source"], [0, 0])
                k[0] += int(p == r["label"])
                k[1] += 1
    empty_cache(device)
    total = sum(v[1] for v in by.values())
    return sum(v[0] for v in by.values()) / max(total, 1), {k: round(v[0] / v[1], 3) for k, v in sorted(by.items())}


def main() -> int:
    ap = argparse.ArgumentParser(description="Fine-tune the NLI model.")
    ap.add_argument("--data", default="training/data")
    ap.add_argument("--out", default="training/runs/v1")
    ap.add_argument("--epochs", type=float, default=1)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--accumulate", type=int, default=2, help="batches per update, to fit in GPU memory")
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--warmup", type=float, default=0.05)
    ap.add_argument("--max-steps", type=int, default=0, help="stop early, for a smoke test")
    ap.add_argument("--seed", type=int, default=17)
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    rows = load(args.data)
    train, val = split(rows, args.seed)
    print(f"{len(train)} train, {len(val)} validation, device {device}", flush=True)

    tok = AutoTokenizer.from_pretrained(BASE)
    model = AutoModelForSequenceClassification.from_pretrained(BASE).to(device)
    acc, per = evaluate(model, tok, val, device)
    print(f"before training: validation accuracy {acc:.3f} {per}", flush=True)

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    steps = args.max_steps or math.ceil(args.epochs * math.ceil(len(train) / (args.batch * args.accumulate)))
    warm = max(1, int(steps * args.warmup))
    checkpoints = {steps * k // 4 for k in range(1, 5)}
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: (s + 1) / warm if s < warm else 0.5 * (1 + math.cos(math.pi * (s - warm) / max(1, steps - warm))))

    os.makedirs(args.out, exist_ok=True)
    rng = random.Random(args.seed)
    step, started, pending = 0, time.time(), 0
    while step < steps:
        rng.shuffle(train)
        for i in range(0, len(train), args.batch):
            if step >= steps:
                break
            model.train()
            b = train[i:i + args.batch]
            enc = tok([r["premise"] for r in b], [r["hypothesis"] for r in b], padding=True, truncation=True,
                      max_length=256, return_tensors="pt").to(device)
            loss = model(**enc, labels=torch.tensor([r["label"] for r in b], device=device)).loss
            (loss / args.accumulate).backward()
            pending += 1
            if pending < args.accumulate:
                continue
            pending = 0
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            opt.zero_grad()
            step += 1
            if step % 100 == 0:
                empty_cache(device)
                rate = step / (time.time() - started)
                print(f"step {step}/{steps} loss {loss.item():.3f} lr {sched.get_last_lr()[0]:.2e} "
                      f"{rate:.1f} steps/s, about {(steps - step) / rate / 60:.0f} min left", flush=True)
            if step in checkpoints:
                acc, per = evaluate(model, tok, val, device)
                print(f"step {step}: validation accuracy {acc:.3f} {per}", flush=True)
                # Keep them all: this validation is rule-based and rose while real cases got
                # worse, so it cannot choose. Compare them with eval_nli.py instead.
                path = os.path.join(args.out, f"step{step}")
                model.save_pretrained(path)
                tok.save_pretrained(path)
                print(f"saved {path}", flush=True)
    print("ALLDONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
