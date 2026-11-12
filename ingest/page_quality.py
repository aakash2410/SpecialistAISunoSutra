"""Build-time: decide whether a harvested page belongs in the corpus.

Many DIKSHA PDFs have a text layer that does not map to real characters, so a page
can look fine to a person while extraction returns junk. A page that fails
``page_problem`` gets one OCR attempt, and is left out if it still fails.

English only for now, because answers are generated from English passages.

``remove_unwanted`` then drops readable pages a teacher's question should not land
on: lesson plans, front matter and repeats. It matches page headers only, so it is a
rough filter, and anything it misses stays in the corpus.

``clean_chunks`` runs when the index is built and checks each passage: OCR run over
Hindi or Gujarati pages, which reads as English letters but says nothing, plus front
matter anywhere on a page, every passage from a teacher's manual or lesson plan file, every
book whose title marks it as a teacher's handbook, and every passage that states lesson aims.
Other books that mix teacher instructions with explanations stay: removing every passage with
a teacher instruction in it lost real content and made answers no better.
"""

from __future__ import annotations

import collections
import re
from typing import List, Optional, Set, Tuple

_COMMON_WORDS = set(
    "the of and to in is a for that are it on with as this be by we an or from have "
    "you which at can their will not has they these your its was all what how".split()
)
# Real English runs well above this; OCR run over the wrong script sits near zero.
_MIN_COMMON_WORD_SHARE = 0.10
_MAX_BROKEN_SHARE = 0.01
_MIN_LETTER_SHARE = 0.5
_MIN_LATIN_SHARE = 0.9

_NON_TEACHING_RE = re.compile(
    r"lesson plan|learning outcomes?\b|teaching[- ]learning|instructions? for (?:the )?teachers?"
    r"|how will i adapt|^\s*period \d|copyright|\bisbn\b|textbook development"
    r"|table of contents|^\s*contents\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_HEADER_CHARS = 400
_DUPLICATE_CHARS = 300

_TEACHER_FILE_RE = re.compile(r"teachh?er|lesson-plan|unit-plan|content-outline|post-course", re.IGNORECASE)
# Teacher's handbooks, judged from the DIKSHA title (the chunk's "chapter"). The file name
# rarely says so, as in "7.3_SCI_Heat_THB", whose lesson aims ("Students will also justify
# why they wear certain kinds of clothes in summer and winter") were read out as an answer on
# 12 November. THB is Andhra Pradesh's mark for a Teacher's Handbook. In the Class 6 to 8
# index this matches the AP Science Handbooks for Classes 6 to 8 and 9 THB chapters, all
# period-by-period teaching notes. "Activity Guide", "Lab Manual", "Teacher Corner" (a
# textbook) and a "Student Handbook" do not match.
_TEACHER_TITLE_RE = re.compile(
    r"(?<![a-z])(?:thb(?![a-z])|hand\s*-?\s*book|teach(?:er|ers|er's|ers')?\s*(?:guide|hand\s*book|manual|book))",
    re.IGNORECASE,
)
# Lesson aims and learning outcomes: "the learners will be able to explain the modes of heat
# transfer", "The teacher will prepare a list of questions". Textbooks speak to the student as
# "you", so in the Class 6 to 8 index these phrases are almost only in lesson plans and
# teacher's handbooks, and 24 of 25 passages sampled with one of them were teaching notes
# throughout, not only in the aim sentence. The whole passage goes, since cutting sentences out
# would leave its vector describing text it no longer holds. "Aim:" and "Objective:" are left
# alone, since textbook activities start with them. The answer check has a wider list
# (answer_guard.LESSON_AIM) for sentences that still get through.
_LESSON_AIM_RE = re.compile(
    r"\b(?:students?|learners?|pupils?|children|child)\s+(?:will|would|shall|should|can)\s+be\s+able\s+to\b"
    r"|\b(?:students|learners|pupils)\s+(?:will|would)\b"
    r"|\bchildren\s+(?:will|would)\s+(?:also\s+)?(?:learn|understand|know|explore|recall|discuss|identify|explain|"
    r"describe|justify|classify|appreciate|develop|be\s+introduced)\b"
    r"|\bthe\s+teachers?\s+(?:will|would)\b|\bteachers?\s+will\b"
    r"|\bby\s+the\s+end\s+of\s+(?:this|the)\s+(?:lesson|chapter|unit|period|class|session|topic)\b"
    r"|\blearning\s+(?:outcomes?|objectives?)\b|\b(?:lesson|teaching|instructional)\s+objectives?\b",
    re.IGNORECASE,
)
_FRONT_MATTER_RE = re.compile(
    r"acknowledge?ments?|textbook (?:review|development)|printed (?:on|in|at)\b|\bG\.?S\.?M\b|pledge"
    r"|jana[- ]gana|jaya jaya|free distribution|all rights reserved|foreword|review workshop",
    re.IGNORECASE,
)
# A word counts as recognised if it appears in this many books, so it needs a large corpus.
_VOCABULARY_MIN_BOOKS = 8
_GARBLE_CHECK_MIN_BOOKS = 50
_MIN_RECOGNISED_SHARE = 0.70
# Maths passages have few common words, and history passages have many rare names, so a
# passage has to fail both tests to count as garbled.
_MIN_COMMON_SHARE_IF_UNRECOGNISED = 0.20


def page_problem(text: str) -> Optional[str]:
    """Return why this page is unusable, or None if it is readable English."""
    total = len(text)
    if not total:
        return "empty"
    control = sum(1 for c in text if ord(c) < 32 and c not in "\n\t\r")
    if control / total > _MAX_BROKEN_SHARE:
        return "control characters"
    glyphs = sum(1 for c in text if 0xE000 <= ord(c) <= 0xF8FF or c == "�")
    if glyphs / total > _MAX_BROKEN_SHARE:
        return "private-use glyphs"
    letters = [c for c in text if c.isalpha()]
    if len(letters) / total < _MIN_LETTER_SHARE:
        return "too few letters"
    if sum(c.isascii() for c in letters) / len(letters) < _MIN_LATIN_SHARE:
        return "not English"
    words = re.findall(r"[A-Za-z]+", text.lower())
    if not words or sum(w in _COMMON_WORDS for w in words) / len(words) < _MIN_COMMON_WORD_SHARE:
        return "not English"
    return None


# Words that mark a page's subject, for books that bind science with other subjects, such as
# the Tamil Nadu term books (science and social science in one PDF). Only strong markers:
# words science pages rarely use. Checked by eye on the 11 mixed-subject books in the
# Class 6 to 8 harvest (12 November).
_SUBJECT_WORDS = {
    "social": set("""king kings kingdom kingdoms dynasty dynasties empire empires emperor emperors ruler rulers
        reign reigned sultan sultans sultanate mughal mughals chola cholas pandya pandyas rajput rajputs
        temple temples inscription inscriptions inscribed coins century centuries battle battles war wars
        invasion invaded conquered conquest throne court courts capital administration administrative
        merchants trade traders constitution constitutional democracy democratic government governments
        election elections vote voting voters party parties parliament minister ministers president
        governor citizen citizens equality rights panchayat municipal municipality economy economic
        production market markets labour entrepreneur tax taxes taxation bank banks income money
        latitude longitude continent continents settlement settlements population census map maps
        tourism tourist culture religion religious caste festival festivals monument monuments fort
        british portuguese colonial freedom independence movement revolt rebellion historians history
        historical archaeological civilisation civilization society social rural urban army navy military
        soldiers defence foreign treaty alignment nations countries prehistoric palaeolithic paleolithic
        neolithic megalithic america africa asia europe australia antarctica""".split()),
    "maths": set("""fraction fractions numerator denominator denominators integer integers equation equations
        perimeter triangle triangles angle angles polygon polygons algebra algebraic ratio ratios
        proportion decimals decimal multiply multiplication subtract subtraction divisor divisible lcm hcf
        geometry symmetry simplify quadrilateral rectangle parallelogram rhombus numerals digit digits
        percentage percent profit loss interest""".split()),
    "language": set("""noun nouns pronoun pronouns verb verbs adjective adjectives adverb adverbs tense grammar
        poem poet stanza vocabulary synonym synonyms antonym antonyms sentence sentences paragraph""".split()),
    "science": set("""cell cells plant plants animal animals energy force forces acid acids base bases salt
        salts light heat electric electricity current circuit magnet magnets magnetic temperature oxygen
        carbon dioxide hydrogen nitrogen matter atom atoms molecule molecules element elements compound
        compounds mixture mixtures organism organisms bacteria microorganisms fungi virus disease diseases
        reproduction flower flowers leaf leaves root roots stem seed seeds soil motion speed mass
        density chemical reaction experiment liquid liquids solid solids gas gases water digestion
        blood heart body organ organs tissue tissues nutrition photosynthesis respiration friction
        pressure sound waves mirror lens reflection fibre fibres metals metal acid fuel combustion
        germination pollination evaporation condensation vapour substance substances""".split()),
}
_SUBJECT_WINDOW = 2
_SUBJECT_TOKEN = re.compile(r"[a-z]+")


def _subject_hits(text: str) -> collections.Counter:
    words = _SUBJECT_TOKEN.findall(text.lower())
    hits: collections.Counter = collections.Counter()
    for subject, vocabulary in _SUBJECT_WORDS.items():
        hits[subject] = sum(w in vocabulary for w in words)
    return hits


def subject_of_pages(texts: List[str]) -> List[str]:
    """The subject of each page of one book, in page order: science, social, maths or language.

    A book's subjects come in runs of pages, so each page is judged with the pages either
    side of it (its own page counts twice). Science wins a tie, so a page with no markers
    stays in.
    """
    hits = [_subject_hits(t) for t in texts]
    labels = []
    for i in range(len(texts)):
        total: collections.Counter = collections.Counter()
        for j in range(max(0, i - _SUBJECT_WINDOW), min(len(texts), i + _SUBJECT_WINDOW + 1)):
            for subject, n in hits[j].items():
                total[subject] += n * (2 if j == i else 1)
        best = max(_SUBJECT_WORDS, key=lambda s: (total[s], s == "science"))
        labels.append(best if total[best] > total["science"] else "science")
    return labels


# Subjects DIKSHA uses for science books. A book tagged with any other subject as well, such
# as "Science, Social Science", has its pages checked one by one (see drop_non_science_pages).
SCIENCE_SUBJECTS = {"science", "physics", "biology", "chemistry", "physical science", "life science",
                    "environmental studies", "environmental science", "evs"}


def _subjects(doc: dict) -> Set[str]:
    value = doc.get("subject") or []
    values = value if isinstance(value, (list, tuple)) else [value]
    return {str(v).strip().lower() for v in values if v}


def drop_non_science_pages(docs: List[dict]) -> Tuple[List[dict], dict]:
    """Drop the pages of mixed-subject books that are not science. Returns (kept, counts).

    The Tamil Nadu term books bind science and social science in one PDF, and one of them
    is really a maths book, so their history, civics, maths and grammar pages would answer
    questions this science-only index should refuse. Books tagged only with science
    subjects are left alone.
    """
    pages = collections.defaultdict(list)
    for i, doc in enumerate(docs):
        if _subjects(doc) - SCIENCE_SUBJECTS:
            pages[doc.get("content_id")].append(i)
    drop = set()
    for rows in pages.values():
        rows.sort(key=lambda i: docs[i].get("page", 0))
        labels = subject_of_pages([docs[i].get("text", "") for i in rows])
        drop.update(i for i, label in zip(rows, labels) if label != "science")
    kept = [d for i, d in enumerate(docs) if i not in drop]
    return kept, {"dropped: not science (mixed-subject book)": len(drop)} if drop else {}


# A book tagged with more classes than this is a general resource, not a class's textbook.
MAX_CLASSES = 3


def drop_other_classes(docs: List[dict], classes) -> Tuple[List[dict], dict]:
    """Drop books tagged with a class outside ``classes``, or with more than MAX_CLASSES
    classes. Returns (kept, counts).

    DIKSHA tags some books with many classes, such as "Artificial Intelligence Integration in
    Science" for Classes 6 to 10. In the Class 6 to 8 index it won class searches for Classes
    6, 7 and 8 alike, and its pages are written for other classes too. The 63 such books
    were 1,555 of 54,065 passages and reached the top 3 for one of 125 test questions.
    """
    wanted = {str(c).strip() for c in classes}
    kept: List[dict] = []
    dropped = 0
    for doc in docs:
        grade = doc.get("grade") or []
        grades = {str(g).strip() for g in (grade if isinstance(grade, (list, tuple)) else [grade]) if g}
        if grades and (not grades <= wanted or len(grades) > MAX_CLASSES):
            dropped += 1
        else:
            kept.append(doc)
    return kept, {"dropped: book for other classes": dropped} if dropped else {}


def remove_unwanted(docs: List[dict]) -> Tuple[List[dict], dict]:
    """Drop lesson plans, front matter and repeated pages. Returns (kept, counts)."""
    kept: List[dict] = []
    seen = set()
    counts: collections.Counter = collections.Counter()
    for doc in docs:
        text = doc.get("text", "")
        opening = re.sub(r"\s+", " ", text[:_DUPLICATE_CHARS]).lower()
        if _NON_TEACHING_RE.search(text[:_HEADER_CHARS]):
            counts["dropped: lesson plan or front matter"] += 1
        elif opening in seen:
            counts["dropped: duplicate"] += 1
        else:
            seen.add(opening)
            kept.append(doc)
    return kept, dict(counts)


def is_teacher_handbook(chunk: dict) -> bool:
    title = chunk.get("chapter") or ""
    return bool(_TEACHER_TITLE_RE.search(title)) and "student" not in title.lower()


def states_lesson_aims(text: str) -> bool:
    return bool(_LESSON_AIM_RE.search(text))


def _book(chunk: dict) -> str:
    return (chunk.get("source") or "").rsplit("/", 1)[-1]


def _long_words(text: str) -> List[str]:
    return re.findall(r"[a-z]{3,}", text.lower())


def _is_garbled(text: str, vocabulary: set) -> bool:
    long_words = _long_words(text)
    if long_words and sum(w in vocabulary for w in long_words) / len(long_words) >= _MIN_RECOGNISED_SHARE:
        return False
    words = re.findall(r"[a-z]+", text.lower())
    return not words or sum(w in _COMMON_WORDS for w in words) / len(words) < _MIN_COMMON_SHARE_IF_UNRECOGNISED


def english_vocabulary(chunks: List[dict]) -> Set[str]:
    """Words that appear in at least 8 books, which scan damage rarely produces."""
    words_in_book = collections.defaultdict(set)
    for chunk in chunks:
        words_in_book[_book(chunk)].update(_long_words(chunk["text"]))
    if len(words_in_book) < _GARBLE_CHECK_MIN_BOOKS:
        return set()
    books_with_word = collections.Counter(w for words in words_in_book.values() for w in words)
    return {w for w, books in books_with_word.items() if books >= _VOCABULARY_MIN_BOOKS}


# Wikipedia articles uploaded as DIKSHA items keep their reference marks, such as "[3]".
# They are written for adults, and they answered off-topic questions such as "What is DNA
# replication?" and "How does a jet engine work?". Wikipedia is CC BY-SA, not CC BY, too.
# In the Class 6 to 8 harvest, 50 items have one mark per 10 passages or more and every
# other item has fewer than one per 50.
_CITATION_RE = re.compile(r"(?<![\d\]])\[(?:\d{1,3}|citation needed)\]")
_MIN_CITATIONS_PER_PASSAGE = 0.1
_MIN_CITATIONS = 3


def encyclopaedia_copies(chunks: List[dict]) -> Set[str]:
    """The content_ids of items whose passages carry Wikipedia reference marks."""
    passages: collections.Counter = collections.Counter()
    marks: collections.Counter = collections.Counter()
    for chunk in chunks:
        cid = chunk.get("content_id")
        passages[cid] += 1
        marks[cid] += len(_CITATION_RE.findall(chunk["text"]))
    return {cid for cid, n in passages.items()
            if marks[cid] >= _MIN_CITATIONS and marks[cid] / n >= _MIN_CITATIONS_PER_PASSAGE}


def clean_chunks(chunks: List[dict]) -> Tuple[List[dict], dict]:
    """Drop passages a teacher's question should not land on. Returns (kept, counts)."""
    vocabulary = english_vocabulary(chunks)
    check_garble = bool(vocabulary)
    copies = encyclopaedia_copies(chunks)

    kept: List[dict] = []
    counts: collections.Counter = collections.Counter()
    for chunk in chunks:
        text = chunk["text"]
        if _TEACHER_FILE_RE.search(_book(chunk)):
            counts["dropped: teacher's book"] += 1
        elif is_teacher_handbook(chunk):
            counts["dropped: teacher's handbook (title)"] += 1
        elif chunk.get("content_id") in copies:
            counts["dropped: encyclopaedia copy"] += 1
        elif _FRONT_MATTER_RE.search(text):
            counts["dropped: front matter"] += 1
        elif states_lesson_aims(text):
            counts["dropped: lesson aims"] += 1
        elif check_garble and _is_garbled(text, vocabulary):
            counts["dropped: garbled text"] += 1
        else:
            kept.append(chunk)
    return kept, dict(counts)
