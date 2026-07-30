"""Build-time, online, once: harvest a grade+subject slice of DIKSHA.

Runs OFF the device. Produces the ``raw/chapters.json`` that the rest of the
pipeline chunks, embeds, and bakes into the device image. At run time the device
never calls DIKSHA — it only reads the baked index.

Two modes:
  * local  (default) — load an already-harvested ``raw/chapters.json``.
  * online           — query the public DIKSHA content API for a
    board/grade/subject slice, download the PDF artifacts, and extract text.

How the online harvest works (verified against the live API):
  1. Composite search  POST {base}/api/content/v1/search  with filters
     (gradeLevel/subject/medium/board/mimeType) -> a page of content items, each
     with an ``artifactUrl`` (the PDF) and a ``license``.
  2. Download each PDF and extract its text layer with PyMuPDF (per page).
     ~80% of DIKSHA PDFs are SCANNED images with no text layer — pass ``--ocr``
     (requires Tesseract) to OCR those, otherwise they are skipped.
  3. Emit one document per text-bearing page, in the chapters.json schema
     (content_id, chapter, section, page, text, source, license).

Licensing: only harvest content you are cleared to redistribute offline. The
Class-7 Science PDFs are CC BY 4.0, but confirm per asset and keep attribution
(this is a v1 open item).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

DIKSHA_BASE = "https://diksha.gov.in"
SEARCH_URL = f"{DIKSHA_BASE}/api/content/v1/search"
HEADERS = {"Content-Type": "application/json", "User-Agent": "specialist-harvester/1.0"}
_FIELDS = ["identifier", "name", "artifactUrl", "board", "gradeLevel",
           "subject", "medium", "license", "contentType", "mimeType"]


# --------------------------------------------------------------------- local
def load_local(raw_path: str) -> List[dict]:
    with open(raw_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    docs = data.get("documents", [])
    collection = data.get("collection", {})
    for d in docs:
        d.setdefault("board", collection.get("board"))
        d.setdefault("grade", collection.get("gradeLevel"))
        d.setdefault("subject", collection.get("subject"))
        d.setdefault("source", collection.get("source", DIKSHA_BASE + "/"))
        d.setdefault("license", collection.get("license"))
    logger.info("Loaded %d documents from %s", len(docs), raw_path)
    return docs


# -------------------------------------------------------------------- online
def search_content(filters: dict, limit: int, offset: int) -> Tuple[int, List[dict]]:
    """One page of the DIKSHA composite search."""
    body = {"request": {"filters": filters, "fields": _FIELDS, "limit": limit, "offset": offset}}
    r = requests.post(SEARCH_URL, json=body, headers=HEADERS, timeout=30)
    r.raise_for_status()
    result = r.json().get("result", {})
    return int(result.get("count", 0)), (result.get("content") or [])


def _extract_pages(pdf_bytes: bytes, ocr: bool) -> List[Tuple[int, str]]:
    """Return [(page_number, text)] for a PDF, optionally OCR-ing scanned pages."""
    import fitz  # PyMuPDF

    # Silence non-fatal MuPDF graphics warnings (bad color spaces / shading in some
    # DIKSHA PDFs). They don't affect text extraction, they just spam stderr.
    try:
        fitz.TOOLS.mupdf_display_errors(False)
    except Exception:  # noqa: BLE001
        pass

    out: List[Tuple[int, str]] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            if not text and ocr:
                try:  # PyMuPDF OCR shells out to Tesseract
                    tp = page.get_textpage_ocr(full=True)
                    text = page.get_text("text", textpage=tp).strip()
                except Exception as e:  # noqa: BLE001
                    logger.debug("OCR failed on page %d: %s", i, e)
            out.append((i, text))
    return out


def _first(v, default=None):
    """DIKSHA metadata fields are often lists; take the first value."""
    if isinstance(v, list):
        return v[0] if v else default
    return v if v not in (None, "") else default


def harvest_online(board: Optional[str], grade: str, subject: Optional[str], medium: str = "English",
                   limit: int = 200, max_docs: int = 100000, min_chars: int = 120,
                   ocr: bool = False, page_size: int = 100) -> List[dict]:
    """Search DIKSHA, download PDFs, and extract text into chapters.json docs.

    ``subject``/``board`` are optional — omit them (empty/None) to pull ALL
    subjects / boards for the grade+medium. ``limit`` caps how many content items
    to scan; ``max_docs`` caps output pages; ``--ocr`` recovers scanned PDFs.
    Each document is labelled with its OWN board/subject/grade/medium from DIKSHA.
    """
    filters: Dict[str, list] = {
        "gradeLevel": [grade], "medium": [medium], "mimeType": ["application/pdf"],
    }
    if subject:
        filters["subject"] = [subject]
    if board:
        filters["board"] = [board]

    docs: List[dict] = []
    seen: set = set()
    offset = 0
    scanned_skipped = 0
    scanned = 0

    while len(seen) < limit and len(docs) < max_docs:
        want = min(page_size, limit - len(seen))
        count, items = search_content(filters, want, offset)
        if not items:
            break
        logger.info("search: %d total, page of %d at offset %d", count, len(items), offset)
        for c in items:
            cid = c.get("identifier")
            url = c.get("artifactUrl") or ""
            if not cid or cid in seen:
                continue
            seen.add(cid)
            if not url.lower().endswith(".pdf"):
                continue
            try:
                data = requests.get(url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=60).content
                pages = _extract_pages(data, ocr=ocr)
            except Exception as e:  # noqa: BLE001
                logger.warning("skip %s (%s): %s", cid, (c.get("name") or "")[:30], e)
                continue

            kept = 0
            for pnum, text in pages:
                if len(text) < min_chars:
                    continue
                docs.append({
                    "content_id": cid,
                    "chapter": (c.get("name") or cid).strip(),
                    "section": f"page {pnum}",
                    "page": pnum,
                    "text": text,
                    "source": url,
                    "license": c.get("license"),
                    # Label with the content's OWN metadata (falls back to the query).
                    "board": _first(c.get("board"), board),
                    "grade": _first(c.get("gradeLevel"), grade),
                    "subject": _first(c.get("subject"), subject),
                    "medium": _first(c.get("medium"), medium),
                })
                kept += 1
                if len(docs) >= max_docs:
                    break
            if kept == 0:
                scanned_skipped += 1
            else:
                scanned += 1
            logger.info("  %s -> %d text pages  (%s)", cid, kept, (c.get("name") or "")[:40])
            if len(docs) >= max_docs:
                break
            time.sleep(0.2)  # be polite

        offset += len(items)
        if offset >= count:
            break

    logger.info("Harvested %d text pages from %d content items (%d yielded text, %d had no text layer%s)",
                len(docs), len(seen), scanned, scanned_skipped,
                " — pass --ocr to recover scans" if scanned_skipped and not ocr else "")
    if not docs:
        logger.warning("No text extracted. Most DIKSHA PDFs are scanned images; "
                       "install Tesseract and re-run with --ocr, or widen the filters.")
    return docs


def main():
    ap = argparse.ArgumentParser(description="Harvest a DIKSHA grade+subject slice (build time).")
    ap.add_argument("--mode", choices=["local", "online"], default="local")
    ap.add_argument("--raw", default=None, help="path to raw/chapters.json (local mode)")
    ap.add_argument("--out", default=None, help="where to write harvested documents JSON")
    ap.add_argument("--board", default="", help="board filter ('' = all boards)")
    ap.add_argument("--grade", default="Class 7")
    ap.add_argument("--subject", default="", help="subject filter ('' = all subjects)")
    ap.add_argument("--medium", default="English")
    ap.add_argument("--limit", type=int, default=6000, help="max content items to scan")
    ap.add_argument("--max-docs", type=int, default=5000, help="max text pages to output")
    ap.add_argument("--min-chars", type=int, default=120, help="drop pages shorter than this")
    ap.add_argument("--ocr", action="store_true", help="OCR scanned PDFs (needs Tesseract)")
    args = ap.parse_args()

    if args.mode == "local":
        raw = args.raw or os.path.join(
            os.path.dirname(__file__), "..", "corpus", "diksha_g7_science", "raw", "chapters.json")
        docs = load_local(os.path.abspath(raw))
    else:
        docs = harvest_online(args.board or None, args.grade, args.subject, medium=args.medium,
                              limit=args.limit, max_docs=args.max_docs,
                              min_chars=args.min_chars, ocr=args.ocr)

    payload = {
        "collection": {
            "name": f"DIKSHA {args.grade} {args.subject or 'all subjects'} "
                    f"{args.board or 'all boards'} ({args.medium})",
            "board": args.board or "all", "gradeLevel": args.grade,
            "subject": args.subject or "all", "medium": args.medium,
            "source": DIKSHA_BASE + "/", "harvested_at": time.strftime("%Y-%m-%d"),
        },
        "documents": docs,
    }
    if args.out:
        out_dir = os.path.dirname(os.path.abspath(args.out))
        os.makedirs(out_dir, exist_ok=True)          # don't lose a long harvest to a missing dir
        tmp = args.out + ".part"                       # atomic write
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, args.out)
        logger.info("Wrote %d documents to %s", len(docs), args.out)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    main()
