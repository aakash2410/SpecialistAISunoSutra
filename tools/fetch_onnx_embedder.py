#!/usr/bin/env python3
"""Download the ONNX embedding model (multilingual-e5-small) + tokenizer.

The Specialist's ONNX backend runs this model on onnxruntime — no torch/sklearn,
so it avoids the Jetson dependency conflicts. Run this ONCE (online, off-device
or on-device with internet); the files then sit on local storage for fully
offline retrieval.

    python3 tools/fetch_onnx_embedder.py                 # quantized (~120 MB, default)
    python3 tools/fetch_onnx_embedder.py --fp32          # full precision (~470 MB)
    python3 tools/fetch_onnx_embedder.py --dest /opt/specialist/models/multilingual-e5-small-onnx

Default dest is local/models/multilingual-e5-small-onnx (picked up automatically
by Embed in a dev checkout). For the device, put it at
/opt/specialist/models/multilingual-e5-small-onnx or set SPECIALIST_ONNX_DIR.

Source: the Xenova ONNX export of intfloat/multilingual-e5-small (Apache-2.0).
Verify licensing before redistributing the weights.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.request

BASE = "https://huggingface.co/Xenova/multilingual-e5-small/resolve/main"
FILES_COMMON = {"tokenizer.json": f"{BASE}/tokenizer.json"}
MODEL_QUANT = ("model_quantized.onnx", f"{BASE}/onnx/model_quantized.onnx")
MODEL_FP32 = ("model.onnx", f"{BASE}/onnx/model.onnx")

DEFAULT_DEST = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "local", "models",
    "multilingual-e5-small-onnx")


def _download(url: str, dest: str) -> None:
    tmp = dest + ".part"
    print(f"  ↓ {os.path.basename(dest)}  <- {url}")

    def _progress(block, block_size, total):
        if total > 0:
            pct = min(100, block * block_size * 100 // total)
            sys.stdout.write(f"\r    {pct:3d}%  ({total/1e6:.0f} MB)")
            sys.stdout.flush()

    urllib.request.urlretrieve(url, tmp, _progress)
    os.replace(tmp, dest)
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch the ONNX embedding model + tokenizer.")
    ap.add_argument("--dest", default=os.path.abspath(DEFAULT_DEST))
    ap.add_argument("--fp32", action="store_true", help="download full-precision model instead of quantized")
    ap.add_argument("--base-url", default=None, help="override the source base URL (mirror / self-host)")
    ap.add_argument("--force", action="store_true", help="re-download even if files exist")
    args = ap.parse_args()

    base = args.base_url.rstrip("/") if args.base_url else None
    model_name, model_url = MODEL_FP32 if args.fp32 else MODEL_QUANT
    targets = dict(FILES_COMMON)
    targets[model_name] = model_url
    if base:  # rewrite URLs onto the mirror, preserving the sub-paths
        targets = {n: u.replace(BASE, base) for n, u in targets.items()}

    os.makedirs(args.dest, exist_ok=True)
    print(f"Fetching ONNX embedder into {args.dest}")
    for name, url in targets.items():
        out = os.path.join(args.dest, name)
        if os.path.exists(out) and not args.force:
            print(f"  ✓ {name} already present (use --force to re-download)")
            continue
        try:
            _download(url, out)
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ failed to fetch {name}: {e}", file=sys.stderr)
            return 1

    print("\nDone. Verify with:")
    print("  python3 -c \"import sys; sys.path.insert(0,'python'); "
          "from pocketinfer.models.embed import Embed; "
          f"print(Embed(prefer='onnx', onnx_model_dir='{args.dest}').verify("
          "{'prefer':'onnx','onnx_model_dir':'" + args.dest + "'}))\"")
    print("Then rebuild the index:  python3 ingest/build_index.py --prefer onnx")
    return 0


if __name__ == "__main__":
    sys.exit(main())
