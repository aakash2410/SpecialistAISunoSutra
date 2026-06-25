"""Build-time, online, once: harvest a grade+subject slice of DIKSHA.

This runs OFF the device. It produces the ``raw/chapters.json`` that the rest of
the pipeline chunks, embeds, and bakes into the device image. At run time the
device never calls DIKSHA — it only reads the baked index.

Two modes:
  * local  (default) — load an already-harvested ``raw/chapters.json``. This is
    what the starter corpus ships with so the pipeline is reproducible offline.
  * online           — query the public DIKSHA APIs for a board/grade/subject
    slice. Implemented as a documented, ready-to-fill path; flip it on once you
    have confirmed bulk-download + offline-redistribution licensing for the
    specific assets you intend to take (a v1 success criterion / open item).

DIKSHA reference (for the online path):
  Composite search:  POST https://diksha.gov.in/api/content/v1/search
  Content read:      GET  https://diksha.gov.in/api/content/v1/read/{do_id}
Respect robots/T&Cs and the per-asset licence before redistributing content.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from typing import List

logger = logging.getLogger(__name__)


def load_local(raw_path: str) -> List[dict]:
    with open(raw_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    docs = data.get("documents", [])
    collection = data.get("collection", {})
    for d in docs:
        d.setdefault("board", collection.get("board"))
        d.setdefault("grade", collection.get("gradeLevel"))
        d.setdefault("subject", collection.get("subject"))
        d.setdefault("source", collection.get("source", "https://diksha.gov.in/"))
        d.setdefault("license", collection.get("license"))
    logger.info("Loaded %d documents from %s", len(docs), raw_path)
    return docs


def harvest_online(board: str, grade: str, subject: str, limit: int = 200) -> List[dict]:
    """Harvest from DIKSHA. Intentionally gated until licensing is confirmed.

    Fill in the request bodies and field mapping for the assets you are licensed
    to redistribute, then remove the RuntimeError.
    """
    raise RuntimeError(
        "Online DIKSHA harvest is gated. Confirm bulk-download and "
        "offline-redistribution licensing for the target assets, implement the "
        "composite-search + content-read calls in harvest_online(), then re-run "
        "with --mode online. Until then use --mode local with a vetted "
        "raw/chapters.json."
    )
    # Sketch of the intended flow (kept for the implementer):
    # import requests
    # body = {"request": {"filters": {"board": [board], "gradeLevel": [grade],
    #          "subject": [subject], "contentType": ["Resource", "TextBook"]},
    #          "limit": limit}}
    # r = requests.post("https://diksha.gov.in/api/content/v1/search", json=body)
    # for item in r.json()["result"]["content"]:
    #     full = requests.get(f"https://diksha.gov.in/api/content/v1/read/{item['identifier']}")
    #     ... extract text, map to {content_id, chapter, section, page, text, ...}


def main():
    ap = argparse.ArgumentParser(description="Harvest a DIKSHA grade+subject slice (build time).")
    ap.add_argument("--mode", choices=["local", "online"], default="local")
    ap.add_argument("--raw", default=None, help="path to raw/chapters.json (local mode)")
    ap.add_argument("--out", default=None, help="where to write harvested documents JSON")
    ap.add_argument("--board", default="CBSE")
    ap.add_argument("--grade", default="Class 7")
    ap.add_argument("--subject", default="Science")
    args = ap.parse_args()

    if args.mode == "local":
        raw = args.raw or os.path.join(
            os.path.dirname(__file__), "..", "python", "pocketinfer", "specialist",
            "corpus", "diksha_g7_science", "raw", "chapters.json"
        )
        docs = load_local(os.path.abspath(raw))
    else:
        docs = harvest_online(args.board, args.grade, args.subject)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"documents": docs}, f, ensure_ascii=False, indent=2)
        logger.info("Wrote %d documents to %s", len(docs), args.out)
    else:
        print(json.dumps({"documents": docs}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    main()
