"""The DIKSHA harvest and index build, with fakes in place of the network and the embedder.

Covers the parts that decide what goes into the index: the search filters, the licence
check, the metadata each passage carries, repeats across grades, and which index the
device answers from. Nothing here downloads anything.
"""

from __future__ import annotations

import json
import os
import threading

import numpy as np
import pytest

from ingest import diksha_pull
from ingest.chunk import chunk_documents, dedupe_documents
from local import corpus_index

READABLE = ("Plants make their own food by photosynthesis. The leaves of the plant use the "
            "light of the sun, water from the soil and air to make the food that it needs.")
SCIENCE = ["Science", "Physics", "Biology", "Physical Science", "Chemistry"]


def item(cid, licence="CC BY 4.0", grades=("Class 7",), subject=("Science",), size=1000):
    return {"identifier": cid, "name": f"Book {cid}", "artifactUrl": f"https://x/{cid}.pdf",
            "license": licence, "gradeLevel": list(grades), "subject": list(subject),
            "board": "NCERT", "medium": ["English"], "size": size}


class FakeSearch:
    """Serves items a page at a time, like the DIKSHA search, and records each request."""

    def __init__(self, items):
        self.items = items
        self.calls = []

    def __call__(self, filters, limit, offset):
        self.calls.append((filters, limit, offset))
        return len(self.items), self.items[offset:offset + limit]


# ------------------------------------------------------------------ the search
def test_filters_take_several_grades_subjects_and_licences():
    filters = diksha_pull.build_filters(["Class 6", "Class 7", "Class 8"], SCIENCE, [],
                                        "English", ["CC BY 4.0"])
    assert filters == {"gradeLevel": ["Class 6", "Class 7", "Class 8"],
                       "mimeType": ["application/pdf"], "medium": ["English"],
                       "subject": SCIENCE, "license": ["CC BY 4.0"]}


def test_filters_keep_the_old_single_value_form():
    filters = diksha_pull.build_filters("Class 7", "", None, "English")
    assert filters == {"gradeLevel": ["Class 7"], "mimeType": ["application/pdf"],
                       "medium": ["English"]}


def test_command_line_takes_lists_and_a_licence():
    args = diksha_pull._parse_args(["--mode", "online", "--grade", "Class 6", "Class 8",
                                    "--subject", "Science", "Physical Science",
                                    "--license", "CC BY 4.0"])
    assert args.grade == ["Class 6", "Class 8"]
    assert args.subject == ["Science", "Physical Science"]
    assert args.license == ["CC BY 4.0"]


def test_old_command_line_still_parses():
    args = diksha_pull._parse_args(["--mode", "online", "--grade", "Class 7", "--subject", "Science"])
    assert args.grade == ["Class 7"] and args.subject == ["Science"] and args.license is None


@pytest.mark.parametrize("subject", [[], ["--subject", ""]])
def test_harvest_without_a_subject_needs_all_subjects(subject):
    with pytest.raises(SystemExit):
        diksha_pull._parse_args(["--mode", "online", "--grade", "Class 6", *subject])
    assert diksha_pull._parse_args(["--mode", "online", "--grade", "Class 6", "--all-subjects"])
    assert diksha_pull._parse_args(["--mode", "online", "--grade", "Class 6", "--dry-run"])


def test_listing_pages_through_and_drops_repeats():
    search = FakeSearch([item("a"), item("b"), item("a"), item("c")])
    count, items = diksha_pull.list_items({}, page_size=2, search=search)
    assert count == 4
    assert [i["identifier"] for i in items] == ["a", "b", "c"]
    assert [offset for _, _, offset in search.calls] == [0, 2]


def test_dry_run_counts_and_sizes_without_downloading(monkeypatch, capsys):
    search = FakeSearch([item("a", size=2_000_000_000), item("b", "CC BY-NC-ND 4.0", size=5),
                         item("c", grades=("Class 6", "Class 7"), subject=("Physics",), size=500_000_000)])
    monkeypatch.setattr(diksha_pull, "search_content", search)
    monkeypatch.setattr(diksha_pull, "_download", lambda url: pytest.fail("dry run downloaded " + url))
    diksha_pull.main(["--mode", "online", "--dry-run", "--grade", "Class 6", "Class 7",
                      "--subject", *SCIENCE, "--license", "CC BY 4.0"])
    out = capsys.readouterr().out
    assert '"license": ["CC BY 4.0"]' in out
    assert "2 PDFs to download, 2.50 GB" in out
    assert "1 left out" in out

    summary = diksha_pull.summarize(search.items, ["CC BY 4.0"])
    assert summary["by_grade"] == {"Class 7": [2, 2_500_000_000], "Class 6": [1, 500_000_000]}
    assert summary["by_license"] == {"CC BY 4.0": [2, 2_500_000_000]}


# --------------------------------------------------------------- the harvest
def test_harvest_checks_each_licence_and_keeps_all_metadata(monkeypatch):
    items = [item("open", grades=("Class 6", "Class 7", "Class 8"), subject=("Science", "Biology")),
             item("closed", licence="CC BY-NC-ND 4.0")]
    fetched = []
    monkeypatch.setattr(diksha_pull, "_extract_pages", lambda pdf, **kw: [(1, READABLE)])
    monkeypatch.setattr(diksha_pull.time, "sleep", lambda s: None)
    docs = diksha_pull.harvest_online([], ["Class 7"], ["Science"], licenses=["CC BY 4.0"],
                                      items=items, fetch=lambda url: fetched.append(url) or b"")
    assert fetched == ["https://x/open.pdf"]
    assert len(docs) == 1
    doc = docs[0]
    assert doc["license"] == "CC BY 4.0"
    assert doc["grade"] == ["Class 6", "Class 7", "Class 8"]
    assert doc["subject"] == ["Science", "Biology"]
    assert doc["board"] == "NCERT" and doc["medium"] == "English"


def test_harvest_resumes_from_its_progress_file(tmp_path, monkeypatch):
    monkeypatch.setattr(diksha_pull, "_extract_pages", lambda pdf, **kw: [(1, READABLE)])
    monkeypatch.setattr(diksha_pull.time, "sleep", lambda s: None)
    progress = str(tmp_path / "progress.jsonl")
    diksha_pull.harvest_online([], ["Class 7"], ["Science"], items=[item("a")],
                               progress_path=progress, fetch=lambda url: b"")
    docs = diksha_pull.harvest_online([], ["Class 7"], ["Science"], items=[item("a")],
                                      progress_path=progress,
                                      fetch=lambda url: pytest.fail("fetched a finished book"))
    assert [d["content_id"] for d in docs] == ["a"]


def test_bad_pages_are_ocrd_in_parallel(monkeypatch):
    """Every unreadable page goes to OCR on the pool, and readable pages are left alone."""
    fitz = pytest.importorskip("fitz")
    pdf = fitz.open()
    pdf.new_page().insert_textbox((40, 40, 550, 400), READABLE, fontsize=11)
    for _ in range(6):
        pdf.new_page()                                   # blank, like a scan with no text layer
    threads = set()
    barrier = threading.Barrier(3, timeout=10)

    def fake_ocr(png):
        threads.add(threading.get_ident())
        barrier.wait()                                   # passes only when 3 run at once
        return "OCR " + READABLE

    monkeypatch.setattr(diksha_pull, "_ocr_png", fake_ocr)
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(3) as pool:
        pages = diksha_pull._extract_pages(pdf.tobytes(), ocr=True, pool=pool, workers=3)
    assert pages[0][1].startswith("Plants make")
    assert len(pages) == 7
    assert all(text.startswith("OCR") for _, text in pages[1:])
    assert len(threads) == 3


# ---------------------------------------------------------- chunks and index
def test_chunks_carry_grade_subject_board_and_licence():
    docs = [{"content_id": "a", "chapter": "Food", "section": "page 1", "page": 1, "text": READABLE,
             "source": "a.pdf", "license": "CC BY 4.0", "board": "NCERT",
             "grade": ["Class 6", "Class 7"], "subject": ["Science"]},
            {"content_id": "old", "page": 1, "text": READABLE, "license": "CC BY 4.0",
             "grade": "Class 7", "subject": "Science", "board": "CBSE"}]   # older harvest schema
    chunks = chunk_documents(docs)
    assert chunks[0]["grade"] == ["Class 6", "Class 7"]
    assert chunks[0]["subject"] == ["Science"]
    assert chunks[0]["board"] == "NCERT" and chunks[0]["license"] == "CC BY 4.0"
    assert chunks[1]["grade"] == ["Class 7"] and chunks[1]["subject"] == ["Science"]


def test_an_item_tagged_with_several_grades_is_kept_once():
    page = {"content_id": "a", "page": 1, "text": READABLE}
    docs = [{**page, "grade": ["Class 6"]}, {**page, "page": 2, "grade": ["Class 6"]},
            {**page, "grade": ["Class 7", "Class 6"]}, {"content_id": "b", "page": 1, "text": READABLE}]
    kept, removed = dedupe_documents(docs)
    assert removed == 1
    assert [(d["content_id"], d["page"]) for d in kept] == [("a", 1), ("a", 2), ("b", 1)]
    assert kept[0]["grade"] == ["Class 6", "Class 7"]


def test_build_writes_metadata_and_a_licence_summary(tmp_path, monkeypatch):
    from ingest import build_index

    class FakeEmbed:
        def __init__(self, model_name):
            pass

        def embed(self, texts, kind):
            v = np.ones((len(texts), 384), dtype=np.float32)
            return v / np.linalg.norm(v, axis=1, keepdims=True)

    monkeypatch.setattr(build_index, "Embed", FakeEmbed)
    base = {"chapter": "Food", "section": "page 1", "source": "x.pdf", "board": "NCERT",
            "subject": ["Science"], "text": READABLE}
    docs = [{**base, "content_id": "a", "page": 1, "license": "CC BY 4.0", "grade": ["Class 6"]},
            {**base, "content_id": "a", "page": 1, "license": "CC BY 4.0", "grade": ["Class 7"]},
            {**base, "content_id": "b", "page": 1, "license": "CC BY 4.0", "grade": ["Class 8"],
             "text": "Heat flows from a hotter object to a colder one, and that is how the "
                     "energy moves from the sun to the earth and to all of us."}]
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "chapters.json").write_text(json.dumps({"collection": {}, "documents": docs}))
    build_index.build(str(tmp_path), "fake", max_words=120, overlap=25)

    with open(tmp_path / "index" / "manifest.json") as f:
        manifest = json.load(f)
    with open(tmp_path / "index" / "chunks.jsonl") as f:
        chunks = [json.loads(line) for line in f]
    assert manifest["num_chunks"] == len(chunks) == 2
    assert manifest["removed"]["repeated pages"] == 1
    assert manifest["licenses"] == {"CC BY 4.0": 2}
    assert manifest["items_by_license"] == {"CC BY 4.0": 2}
    assert manifest["grades"] == {"Class 6": 1, "Class 7": 1, "Class 8": 1}
    assert chunks[0]["grade"] == ["Class 6", "Class 7"] and chunks[1]["grade"] == ["Class 8"]
    assert np.load(tmp_path / "index" / "embeddings.npy").shape == (2, 384)
    with open(tmp_path / "index" / "words.json") as f:
        assert json.load(f)["heat"] == 1


SCIENCE_PAGE = ("Plants make their food by photosynthesis. The leaves take in carbon dioxide and water, "
                "and with the energy of light they make food and give out oxygen.")
SOCIAL_PAGE = ("The Chola kings ruled a large empire. The emperor built temples, and inscriptions on the "
               "temple walls tell us about the trade, the taxes and the administration of the kingdom.")


def test_a_book_of_science_and_social_science_keeps_only_its_science_pages():
    from ingest.page_quality import drop_non_science_pages

    book = {"content_id": "tn", "subject": ["Science", "Social Science"]}
    docs = [{**book, "page": p, "text": SCIENCE_PAGE if p < 4 else SOCIAL_PAGE} for p in range(1, 8)]
    docs.append({"content_id": "ncert", "subject": ["Science"], "page": 1, "text": SOCIAL_PAGE})
    kept, counts = drop_non_science_pages(docs)
    assert [(d["content_id"], d["page"]) for d in kept] == [("tn", 1), ("tn", 2), ("tn", 3), ("ncert", 1)]
    assert counts == {"dropped: not science (mixed-subject book)": 4}


def test_books_for_other_classes_are_left_out():
    from ingest.page_quality import drop_other_classes

    docs = [{"content_id": "a", "grade": ["Class 6"]}, {"content_id": "b", "grade": ["Class 6", "Class 7", "Class 8"]},
            {"content_id": "c", "grade": ["Class 6", "Class 9", "Class 10"]}, {"content_id": "d"}]
    kept, counts = drop_other_classes(docs, ["Class 6", "Class 7", "Class 8"])
    assert [d["content_id"] for d in kept] == ["a", "b", "d"]
    assert counts == {"dropped: book for other classes": 1}


def test_encyclopaedia_copies_are_left_out_of_the_index():
    from ingest.page_quality import encyclopaedia_copies

    wiki = "Natural gas is a fossil fuel.[3] It is used for heating.[12]"
    chunks = [{"content_id": "w", "text": wiki}] * 3 + [{"content_id": "t", "text": "See Fig. [2] and table 3."}]
    assert encyclopaedia_copies(chunks) == {"w"}



def test_teachers_handbooks_and_lesson_aims_are_left_out_of_the_index():
    from ingest.page_quality import clean_chunks, is_teacher_handbook

    fact = "Heat flows from a hotter object to a colder object, and that is how a spoon in hot tea gets hot."
    aims = ("Objective Given the content (chapter) the learners will be able to explain the different modes "
            "of heat transfer. Students will be able understand wool keeps us warm in winters.")
    plan = "Teacher will prepare a list of questions about conduction and will ask them during discussions."
    book = {"content_id": "a", "source": "x/1.pdf"}
    chunks = [{**book, "chapter": "4_heat", "text": t} for t in (fact, aims, plan)]
    chunks.append({**book, "content_id": "b", "chapter": "7.3_SCI_Heat_THB", "text": fact})
    chunks.append({**book, "content_id": "c", "chapter": "6_Science_ Hand Book", "text": fact})
    kept, counts = clean_chunks(chunks)
    assert [(c["content_id"], c["text"]) for c in kept] == [("a", fact)]
    assert counts == {"dropped: lesson aims": 2, "dropped: teacher's handbook (title)": 2}
    for title in ("Teacher's Guide", "8_ Science_ Handbook", "Teachers' Handbook"):
        assert is_teacher_handbook({"chapter": title})
    for title in ("Heat", "DIY Stethoscope - Activity Guide", "APSCERT_8PS_TEACHER CORNER",
                  "VI_SCIENCE LAB MANUAL_EM", "Student Handbook", "Lesson 4"):
        assert not is_teacher_handbook({"chapter": title})


def test_a_textbook_activity_aim_stays():
    from ingest.page_quality import states_lesson_aims

    assert not states_lesson_aims("Aim: To confirm the presence of starch in potato. What you need: iodine.")
    assert not states_lesson_aims("Objective: to show that plant decay adds materials to soil.")
    assert not states_lesson_aims("Precautions: Students should be careful while heating the rod.")
    assert not states_lesson_aims("No child shall be held back in any class.")
    assert states_lesson_aims("In this concept, children will learn about the properties of magnets.")
    assert states_lesson_aims("By the end of this lesson you can name three fuels.")

# --------------------------------------------------- which index the device uses
def _bake(root, name):
    path = os.path.join(root, name, "index")
    os.makedirs(path)
    open(os.path.join(path, "embeddings.npy"), "wb").close()
    return path


def test_resolver_prefers_the_class_6_to_8_index(tmp_path):
    root = str(tmp_path)
    old = _bake(root, "diksha_g7_all")
    _bake(root, "diksha_g7_science")
    assert corpus_index.find_index(roots=(root,)) == old
    new = _bake(root, "diksha_g6_8_science")
    assert corpus_index.find_index(roots=(root,)) == new


def test_resolver_looks_on_the_device_first_and_honours_the_override(tmp_path, monkeypatch):
    device, repo = str(tmp_path / "device"), str(tmp_path / "repo")
    _bake(repo, "diksha_g6_8_science")
    on_device = _bake(device, "diksha_g7_all")
    assert corpus_index.find_index(roots=(device, repo)) == on_device
    monkeypatch.setenv("SPECIALIST_INDEX_DIR", "/somewhere/index")
    assert corpus_index.resolve_index() == "/somewhere/index"


def test_tests_and_training_use_the_real_harvest_not_the_demo(tmp_path):
    root = str(tmp_path)
    _bake(root, "diksha_g7_science")
    assert corpus_index.full_index(root) == os.path.join(root, "diksha_g7_all", "index")
    new = _bake(root, "diksha_g6_8_science")
    assert corpus_index.full_index(root) == new


def test_voice_loop_uses_the_shared_resolver(monkeypatch):
    voice_loop = pytest.importorskip("local.voice_loop")
    monkeypatch.setenv("SPECIALIST_INDEX_DIR", "/from/env")
    assert voice_loop._resolve_index() == "/from/env"
    monkeypatch.delenv("SPECIALIST_INDEX_DIR")
    assert voice_loop._resolve_index() == corpus_index.resolve_index()
