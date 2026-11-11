"""Build-time, online, once: harvest a grade+subject slice of DIKSHA.

Runs OFF the device. Produces the ``raw/chapters.json`` that the rest of the
pipeline chunks, embeds, and bakes into the device image. At run time the device
never calls DIKSHA — it only reads the baked index.

Three modes:
  * local  (default) — load an already-harvested ``raw/chapters.json``.
  * online           — query the public DIKSHA content API for a
    board/grade/subject slice, download the PDF artifacts, and extract text.
    Add ``--dry-run`` to list what would be downloaded (counts and sizes from the
    metadata) without fetching any PDF.
  * repair           — re-check an existing ``raw/chapters.json``: OCR the pages that
    fail the readability check from their source PDFs, and drop what still fails.

How the online harvest works (verified against the live API):
  1. Composite search  POST {base}/api/content/v1/search  with filters
     (gradeLevel/subject/medium/board/license/mimeType, each a list) -> every
     matching content item, each with an ``artifactUrl`` (the PDF), a ``license``
     and a ``size``. The whole list is fetched before any download starts.
  2. Download each PDF and extract its text layer with PyMuPDF (per page).
     Many DIKSHA PDFs are scanned images with no text layer, and many others have
     a text layer that is junk. Pass ``--ocr`` (requires Tesseract) to OCR both;
     a book's bad pages are OCR'd in parallel (``--workers``).
  3. Emit one document per readable page, in the chapters.json schema
     (content_id, chapter, section, page, text, source, license, board, grade,
     subject, medium). ``grade`` and ``subject`` are lists, because DIKSHA tags
     some items with several. Unreadable pages are left out and counted by reason.

A harvest with no ``--subject`` would pull every subject (about 20 GB of PDFs a
class), so it needs ``--all-subjects`` as well.

Licensing: only harvest content you are cleared to redistribute offline. Pass
``--license "CC BY 4.0"`` to filter at the source; each item's licence is checked
again before it is downloaded. Keep attribution.

    python3 ingest/diksha_pull.py --mode online --dry-run \\
        --grade "Class 6" "Class 7" "Class 8" \\
        --subject Science Physics Biology "Physical Science" Chemistry \\
        --license "CC BY 4.0"
"""

from __future__ import annotations

import argparse
import collections
import json
import logging
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional, Tuple

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ingest.page_quality import page_problem, remove_unwanted  # noqa: E402

logger = logging.getLogger(__name__)

_OCR_DPI = 200
# Pages rendered at a time for OCR, per worker, so a long scanned book stays within memory.
_OCR_BATCH_PER_WORKER = 4
DEFAULT_WORKERS = 6
DIKSHA_BASE = "https://diksha.gov.in"
SEARCH_URL = f"{DIKSHA_BASE}/api/content/v1/search"
HEADERS = {"Content-Type": "application/json", "User-Agent": "specialist-harvester/1.0"}
_FIELDS = ["identifier", "name", "artifactUrl", "board", "gradeLevel",
           "subject", "medium", "license", "contentType", "mimeType", "size"]


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


def _as_list(v) -> List[str]:
    """A filter or metadata value as a list of non-empty strings."""
    if v is None:
        return []
    values = v if isinstance(v, (list, tuple, set)) else [v]
    return [str(x).strip() for x in values if x is not None and str(x).strip()]


def build_filters(grades, subjects=None, boards=None, medium="English",
                  licenses=None) -> Dict[str, list]:
    """The search filters. Every value is a list; an empty one is left out (= any)."""
    filters: Dict[str, list] = {"gradeLevel": _as_list(grades), "mimeType": ["application/pdf"]}
    for key, value in (("medium", medium), ("subject", subjects),
                       ("board", boards), ("license", licenses)):
        if _as_list(value):
            filters[key] = _as_list(value)
    return filters


def list_items(filters: dict, limit: int = 100000, page_size: int = 100,
               search: Optional[Callable[[dict, int, int], Tuple[int, List[dict]]]] = None
               ) -> Tuple[int, List[dict]]:
    """Every matching item, deduplicated by identifier, up to ``limit``.

    Fetching the whole list first keeps the order fixed during a long harvest, and lets a
    dry run show what would be downloaded.
    """
    search = search or search_content
    items: List[dict] = []
    seen: set = set()
    offset, count = 0, 0
    while len(items) < limit:
        count, page = search(filters, min(page_size, limit - len(items)), offset)
        if not page:
            break
        for c in page:
            cid = c.get("identifier")
            if cid and cid not in seen and len(items) < limit:
                seen.add(cid)
                items.append(c)
        offset += len(page)
        if offset >= count:
            break
    if count > limit:
        logger.warning("Search matched %d items but --limit stops at %d", count, limit)
    return count, items


def licence_allowed(item: dict, licenses: Optional[List[str]]) -> bool:
    """The per-item check, in case the search filter lets something through."""
    return not licenses or (item.get("license") or "").strip() in set(_as_list(licenses))


def summarize(items: List[dict], licenses: Optional[List[str]] = None) -> dict:
    """What a harvest of these items would download: counts and sizes, without fetching."""
    keep = [c for c in items
            if (c.get("artifactUrl") or "").lower().endswith(".pdf") and licence_allowed(c, licenses)]

    def tally(key) -> Dict[str, list]:
        out: Dict[str, list] = collections.defaultdict(lambda: [0, 0])
        for c in keep:
            for value in _as_list(c.get(key)) or ["(none)"]:
                out[value][0] += 1
                out[value][1] += int(c.get("size") or 0)
        return {k: v for k, v in sorted(out.items(), key=lambda kv: -kv[1][0])}

    return {
        "items": len(keep),
        "skipped": len(items) - len(keep),
        "bytes": sum(int(c.get("size") or 0) for c in keep),
        "missing_size": sum(1 for c in keep if not c.get("size")),
        "by_grade": tally("gradeLevel"),
        "by_subject": tally("subject"),
        "by_license": tally("license"),
        "by_board": tally("board"),
    }


def print_summary(count: int, summary: dict) -> None:
    gb = summary["bytes"] / 1e9
    print(f"Search matched {count} items; {summary['items']} PDFs to download, "
          f"{gb:.2f} GB ({summary['skipped']} left out: not a PDF or licence not allowed; "
          f"{summary['missing_size']} with no size listed)")
    for title, key in (("grade (an item can carry several)", "by_grade"),
                       ("subject", "by_subject"), ("licence", "by_license"), ("board", "by_board")):
        print(f"\nBy {title}:")
        for name, (n, size) in summary[key].items():
            print(f"  {name:40s} {n:6d}  {size / 1e6:9.0f} MB")


def _open_pdf(pdf_bytes: bytes):
    import fitz  # PyMuPDF

    # Silence non-fatal MuPDF graphics warnings (bad color spaces / shading in some
    # DIKSHA PDFs). They don't affect text extraction, they just spam stderr.
    try:
        fitz.TOOLS.mupdf_display_errors(False)
    except Exception:  # noqa: BLE001
        pass
    return fitz.open(stream=pdf_bytes, filetype="pdf")


def _render_png(page) -> Optional[bytes]:
    """Render a page for OCR, or None if the page itself is corrupt."""
    try:
        return page.get_pixmap(dpi=_OCR_DPI).tobytes("png")
    except Exception as e:  # noqa: BLE001
        logger.warning("could not render page %d: %s", page.number + 1, e)
        return None


def _ocr_png(png: bytes) -> str:
    """OCR a page image with Tesseract, one thread per page so pages can run in parallel."""
    try:
        result = subprocess.run(["tesseract", "stdin", "stdout", "-l", "eng"], input=png,
                                capture_output=True, timeout=180,
                                env={**os.environ, "OMP_THREAD_LIMIT": "1"})
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("OCR failed: %s", e)
        return ""
    return result.stdout.decode("utf-8", errors="replace").strip()


def _extract_pages(pdf_bytes: bytes, ocr: bool, pool: Optional[ThreadPoolExecutor] = None,
                   workers: int = 1) -> List[Tuple[int, str]]:
    """Return [(page_number, text)] for a PDF, OCR-ing pages with an unusable text layer.

    Pages are rendered one at a time (MuPDF is not thread-safe) and OCR'd on ``pool``,
    a few batches at a time, so a long scanned book neither runs serially nor fills memory.
    """
    with _open_pdf(pdf_bytes) as doc:
        texts = [page.get_text("text").strip() for page in doc]
        if ocr:
            failing = [i for i, text in enumerate(texts) if page_problem(text)]
            batch = max(1, workers) * _OCR_BATCH_PER_WORKER
            for start in range(0, len(failing), batch):
                part = failing[start:start + batch]
                images = [_render_png(doc[i]) for i in part]
                run = lambda png: _ocr_png(png) if png else ""  # noqa: E731
                recovered = list(pool.map(run, images)) if pool else [run(png) for png in images]
                for i, text in zip(part, recovered):
                    if not page_problem(text):
                        texts[i] = text
    return list(enumerate(texts, start=1))


def _download(url: str) -> bytes:
    response = requests.get(url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=120)
    response.raise_for_status()
    return response.content


def _page_key(doc: dict) -> str:
    return f"{doc.get('source', '')}#{doc.get('page', 0)}"


def _load_progress(path: Optional[str]) -> Dict[str, str]:
    """OCR results saved by an earlier run that stopped part-way."""
    progress: Dict[str, str] = {}
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                progress.update(json.loads(line))
        logger.info("Resuming: %d pages already done", len(progress))
    return progress


def repair_documents(docs: List[dict], fetch: Callable[[str], bytes] = _download,
                     workers: int = 6, progress_path: Optional[str] = None) -> Tuple[List[dict], dict]:
    """OCR the pages that fail the readability check, and drop what still fails,
    along with lesson plans, front matter and repeats.

    Progress is saved after each book to ``progress_path``, so a stopped run resumes
    where it left off. A source that cannot be fetched is retried on the next run.

    Returns (readable documents in their original order, counts for the report).
    """
    done = _load_progress(progress_path)
    failing: Dict[str, List[int]] = collections.defaultdict(list)
    for i, doc in enumerate(docs):
        if page_problem(doc.get("text", "")) and _page_key(doc) not in done:
            failing[doc.get("source", "")].append(i)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for book, (source, indexes) in enumerate(failing.items(), start=1):
            logger.info("book %d/%d: %d pages to OCR", book, len(failing), len(indexes))
            try:
                pages = _open_pdf(fetch(source))
            except Exception as e:  # noqa: BLE001
                logger.warning("could not read %s: %s", source, e)
                continue
            images: Dict[int, Optional[bytes]] = {}
            with pages:
                for i in indexes:
                    number = docs[i].get("page", 0)
                    images[i] = _render_png(pages[number - 1]) if 0 < number <= len(pages) else None
            texts = pool.map(lambda png: _ocr_png(png) if png else "", images.values())
            finished = {_page_key(docs[i]): text for i, text in zip(images, texts)}
            done.update(finished)
            if progress_path:
                with open(progress_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(finished, ensure_ascii=False) + "\n")

    kept: List[dict] = []
    report: collections.Counter = collections.Counter()
    for doc in docs:
        problem = page_problem(doc.get("text", ""))
        recovered = done.get(_page_key(doc))
        if problem is None:
            report["readable as harvested"] += 1
            kept.append(doc)
        elif recovered is None:
            report["dropped: source unavailable"] += 1
        elif page_problem(recovered):
            report[f"dropped: {problem}"] += 1
        else:
            report["recovered by OCR"] += 1
            kept.append({**doc, "text": recovered})
    kept, removed = remove_unwanted(kept)
    return kept, {**report, **removed}


def _first(v, default=None):
    """DIKSHA metadata fields are often lists; take the first value."""
    if isinstance(v, list):
        return v[0] if v else default
    return v if v not in (None, "") else default


def harvest_online(board, grade, subject, medium: str = "English",
                   limit: int = 200, max_docs: int = 100000, min_chars: int = 120,
                   ocr: bool = False, page_size: int = 100,
                   progress_path: Optional[str] = None, licenses=None,
                   workers: int = DEFAULT_WORKERS,
                   items: Optional[List[dict]] = None,
                   fetch: Callable[[str], bytes] = _download) -> List[dict]:
    """Search DIKSHA, download PDFs, and extract text into chapters.json docs.

    With ``progress_path``, every finished book is appended there straight away, so a
    stopped harvest loses at most one book and resumes where it left off.

    ``grade``, ``subject``, ``board`` and ``licenses`` each take one value or a list;
    ``subject``/``board``/``licenses`` empty means any. ``limit`` caps how many content
    items to scan; ``max_docs`` caps output pages; ``ocr`` recovers scanned pages, on
    ``workers`` threads. ``items`` skips the search (the caller already listed them).
    Each document is labelled with its OWN metadata from DIKSHA: every grade and
    subject it is tagged with (lists), its board, medium and licence.
    """
    filters = build_filters(grade, subject, board, medium, licenses)
    if items is None:
        count, items = list_items(filters, limit=limit, page_size=page_size)
        logger.info("search: %d matched, %d listed", count, len(items))

    docs: List[dict] = []
    seen: set = set()
    scanned_skipped = 0
    scanned = 0
    dropped: collections.Counter = collections.Counter()
    finished: set = set()
    if progress_path and os.path.exists(progress_path):
        with open(progress_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    book = json.loads(line)
                except ValueError:      # a line cut short by the crash being resumed from
                    continue
                finished.add(book["content_id"])
                docs.extend(book["pages"])
        logger.info("Resuming: %d books and %d pages already done", len(finished), len(docs))
    progress = open(progress_path, "a", encoding="utf-8") if progress_path else None
    pool = ThreadPoolExecutor(max_workers=max(1, workers)) if ocr else None

    try:
        for n, c in enumerate(items, start=1):
            if len(docs) >= max_docs:
                break
            cid = c.get("identifier")
            url = c.get("artifactUrl") or ""
            if not cid or cid in seen:
                continue
            seen.add(cid)
            if cid in finished or not url.lower().endswith(".pdf"):
                continue
            if not licence_allowed(c, licenses):
                dropped["licence not allowed"] += 1
                continue
            try:
                pages = _extract_pages(fetch(url), ocr=ocr, pool=pool, workers=workers)
            except Exception as e:  # noqa: BLE001
                logger.warning("skip %s (%s): %s", cid, (c.get("name") or "")[:30], e)
                continue

            grades = _as_list(c.get("gradeLevel")) or _as_list(grade)
            subjects = _as_list(c.get("subject")) or _as_list(subject)
            kept = 0
            first_kept = len(docs)
            for pnum, text in pages:
                if len(text) < min_chars:
                    continue
                problem = page_problem(text)
                if problem:
                    dropped[problem] += 1
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
                    "board": _first(c.get("board"), _first(_as_list(board))),
                    "grade": grades,
                    "subject": subjects,
                    "medium": _first(c.get("medium"), medium),
                })
                kept += 1
                if len(docs) >= max_docs:
                    break
            if kept == 0:
                scanned_skipped += 1
            else:
                scanned += 1
            logger.info("  [%d/%d] %s -> %d text pages  (%s)", n, len(items), cid, kept,
                        (c.get("name") or "")[:40])
            if progress:
                progress.write(json.dumps({"content_id": cid, "pages": docs[first_kept:]},
                                          ensure_ascii=False) + "\n")
                progress.flush()
            time.sleep(0.2)  # be polite
    finally:
        if pool:
            pool.shutdown()
        if progress:
            progress.close()
    logger.info("Harvested %d text pages from %d content items (%d yielded text, %d had no text layer%s)",
                len(docs), len(seen), scanned, scanned_skipped,
                " — pass --ocr to recover scans" if scanned_skipped and not ocr else "")
    docs, removed = remove_unwanted(docs)
    logger.info("Left out: %s", {**dropped, **removed})
    if not docs:
        logger.warning("No text extracted. Most DIKSHA PDFs are scanned images; "
                       "install Tesseract and re-run with --ocr, or widen the filters.")
    return docs


def _write(payload: dict, out: Optional[str]) -> None:
    if not out:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)  # don't lose a long harvest to a missing dir
    tmp = out + ".part"                            # atomic write
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, out)
    logger.info("Wrote %d documents to %s", len(payload["documents"]), out)


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Harvest a DIKSHA grade+subject slice (build time).")
    ap.add_argument("--mode", choices=["local", "online", "repair"], default="local")
    ap.add_argument("--raw", default=None, help="path to raw/chapters.json (local and repair modes)")
    ap.add_argument("--out", default=None, help="where to write harvested documents JSON")
    ap.add_argument("--board", nargs="*", default=[], help="board filter, one or more (none = all boards)")
    ap.add_argument("--grade", nargs="+", default=["Class 7"], help="one or more, e.g. \"Class 6\" \"Class 7\"")
    ap.add_argument("--subject", nargs="*", default=[],
                    help="one or more subjects, e.g. Science Physics. Leaving it out needs --all-subjects")
    ap.add_argument("--all-subjects", action="store_true",
                    help="harvest every subject (about 20 GB of PDFs a class)")
    ap.add_argument("--license", nargs="+", default=None,
                    help="only these licences, e.g. \"CC BY 4.0\" (default: any)")
    ap.add_argument("--medium", default="English")
    ap.add_argument("--limit", type=int, default=6000, help="max content items to scan")
    ap.add_argument("--max-docs", type=int, default=100000, help="max text pages to output")
    ap.add_argument("--min-chars", type=int, default=120, help="drop pages shorter than this")
    ap.add_argument("--progress", default=None,
                    help="save each finished book here, so a stopped harvest can resume")
    ap.add_argument("--ocr", action="store_true",
                    help="OCR scanned pages and pages with a junk text layer (needs Tesseract)")
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                    help="pages OCR'd at once (Tesseract processes)")
    ap.add_argument("--dry-run", action="store_true",
                    help="online mode: list what would be downloaded, with sizes, and fetch nothing")
    args = ap.parse_args(argv)
    args.grade = _as_list(args.grade)
    args.subject = _as_list(args.subject)
    args.board = _as_list(args.board)
    args.license = _as_list(args.license) or None
    if args.mode == "online" and not args.dry_run and not args.subject and not args.all_subjects:
        ap.error("no --subject given. That would download every subject (about 20 GB of PDFs "
                 "a class); name the subjects, or pass --all-subjects if you mean it. "
                 "Try --dry-run first to see the size.")
    return args


def main(argv=None):
    args = _parse_args(argv)

    if args.mode == "repair":
        with open(args.raw, encoding="utf-8") as f:
            payload = json.load(f)
        progress_path = f"{args.out}.progress" if args.out else None
        docs, report = repair_documents(payload["documents"], progress_path=progress_path,
                                        workers=args.workers)
        logger.info("Repair result: %s", report)
        payload["documents"] = docs
        payload["collection"]["quality"] = report
        _write(payload, args.out)
        if progress_path and os.path.exists(progress_path):
            os.remove(progress_path)
        return

    if args.mode == "local":
        raw = args.raw or os.path.join(
            os.path.dirname(__file__), "..", "corpus", "diksha_g7_science", "raw", "chapters.json")
        docs = load_local(os.path.abspath(raw))
    else:
        filters = build_filters(args.grade, args.subject, args.board, args.medium, args.license)
        count, items = list_items(filters, limit=args.limit)
        if args.dry_run:
            print("Filters:", json.dumps(filters, ensure_ascii=False))
            print_summary(count, summarize(items, args.license))
            return
        logger.info("search: %d matched, %d listed", count, len(items))
        docs = harvest_online(args.board, args.grade, args.subject, medium=args.medium,
                              limit=args.limit, max_docs=args.max_docs, progress_path=args.progress,
                              min_chars=args.min_chars, ocr=args.ocr, licenses=args.license,
                              workers=args.workers, items=items)

    grades = ", ".join(args.grade)
    subjects = ", ".join(args.subject) or "all subjects"
    boards = ", ".join(args.board) or "all boards"
    payload = {
        "collection": {
            "name": f"DIKSHA {grades} {subjects} {boards} ({args.medium})",
            "board": args.board or "all", "gradeLevel": args.grade,
            "subject": args.subject or "all", "medium": args.medium,
            "license": args.license or "any",
            "source": DIKSHA_BASE + "/", "harvested_at": time.strftime("%Y-%m-%d"),
        },
        "documents": docs,
    }
    _write(payload, args.out)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    main()
