#!/usr/bin/env python3
"""Export a fine-tuned NLI model to quantised ONNX, in the layout the device loads.

    python3.12 training/export_nli.py --run training/runs/v1 --out training/runs/v1/onnx
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

import numpy as np
import onnxruntime as ort
import torch
from onnxruntime.quantization import QuantType, quantize_dynamic
from transformers import AutoModelForSequenceClassification, AutoTokenizer


def main() -> int:
    ap = argparse.ArgumentParser(description="Export a fine-tuned NLI model to quantised ONNX.")
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    tok = AutoTokenizer.from_pretrained(args.run)
    model = AutoModelForSequenceClassification.from_pretrained(args.run).eval()
    os.makedirs(args.out, exist_ok=True)
    enc = tok("Heat flows from hot to cold.", "Heat moves to the colder side.", return_tensors="pt")
    names = [k for k in ("input_ids", "attention_mask", "token_type_ids") if k in enc]
    raw = os.path.join(args.out, "raw.onnx")
    torch.onnx.export(model, tuple(enc[k] for k in names), raw, input_names=names, output_names=["logits"],
                      dynamic_axes={k: {0: "batch", 1: "tokens"} for k in names}, opset_version=17, dynamo=False)
    final = os.path.join(args.out, "model.onnx")
    quantize_dynamic(raw, final, weight_type=QuantType.QInt8)
    for f in os.listdir(args.out):
        if f.startswith("raw"):
            os.remove(os.path.join(args.out, f))
    shutil.copy(os.path.join(args.run, "config.json"), args.out)
    shutil.copy(os.path.join(args.run, "tokenizer.json"), args.out)
    session = ort.InferenceSession(final, providers=["CPUExecutionProvider"])
    used = {i.name for i in session.get_inputs()}
    logits = session.run(None, {k: enc[k].numpy() for k in names if k in used})[0][0]
    with torch.no_grad():
        expected = model(**enc).logits[0].numpy()
    print(f"{os.path.getsize(final) / 1e6:.0f} MB; ONNX vs PyTorch logits differ by at most "
          f"{np.abs(logits - expected).max():.2f}; inputs {sorted(used)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
