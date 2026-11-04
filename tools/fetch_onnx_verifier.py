#!/usr/bin/env python3
"""Download the answer verifier (nli-deberta-v3-small, quantised for ARM) + tokenizer.

Run this once, online. The files then sit on local storage for fully offline use.

    python3 tools/fetch_onnx_verifier.py
    python3 tools/fetch_onnx_verifier.py --dest /opt/specialist/models/nli-deberta-v3-small-onnx

Default dest is local/models/nli-deberta-v3-small-onnx, which the Verifier finds
automatically in a dev checkout and scripts/make_module.sh packages for the device.

Source: cross-encoder/nli-deberta-v3-small on Hugging Face (Apache-2.0).
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.request

BASE = "https://huggingface.co/cross-encoder/nli-deberta-v3-small/resolve/main"
FILES = {
    "model.onnx": f"{BASE}/onnx/model_qint8_arm64.onnx",
    "tokenizer.json": f"{BASE}/tokenizer.json",
    "config.json": f"{BASE}/config.json",
}
DEFAULT_DEST = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "local", "models",
    "nli-deberta-v3-small-onnx")


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch the ONNX answer verifier + tokenizer.")
    ap.add_argument("--dest", default=os.path.abspath(DEFAULT_DEST))
    ap.add_argument("--force", action="store_true", help="re-download even if files exist")
    args = ap.parse_args()

    os.makedirs(args.dest, exist_ok=True)
    print(f"Fetching answer verifier into {args.dest}")
    for name, url in FILES.items():
        out = os.path.join(args.dest, name)
        if os.path.exists(out) and not args.force:
            print(f"  ok, already present: {name}")
            continue
        print(f"  downloading {name}")
        try:
            urllib.request.urlretrieve(url, out + ".part")
            os.replace(out + ".part", out)
        except OSError as e:
            print(f"  failed to fetch {name}: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
