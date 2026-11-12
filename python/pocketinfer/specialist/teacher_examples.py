"""Everyday examples a teacher writes down, for the example after an explanation.

A teacher fills in a plain text file with 30 to 40 things the children see and do, one
per line, in English (the template is data/teacher_examples.txt). When it has lines, the
answer model is shown the few closest to the topic and asked to turn the best one into an
example of a checked fact (see pipeline._example). The example still goes through every
check a village example does, including the full safety check.

A line reaches the prompt only if it is short, has no digits, is written in English
letters and passes the check for the teacher's own lines (SafetyCheck.check_line, looser
than the check on a child's request), so a phone number or a line about harm never reaches
the answer model. An empty or missing file changes nothing.

The checked lines and their e5 vectors are cached (see load_cached), keyed by the file's
contents, the check version and the embedder, so a restart does not redo them.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Set

import numpy as np

from .safety import check_teacher_line

logger = logging.getLogger(__name__)

ENV = "SPECIALIST_TEACHER_EXAMPLES"
DEVICE_PATH = "/opt/specialist/teacher_examples.txt"
TEMPLATE_PATH = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                              "..", "..", "..", "data", "teacher_examples.txt"))
CACHE_ENV = "SPECIALIST_CACHE_DIR"
DEVICE_CACHE = "/opt/specialist/cache"
# Raise this whenever the cleaning, the line check or the vectors change, so cached lines
# are checked again.
CHECK_VERSION = 3

# A line longer than this is cut at a word boundary, so one long line cannot crowd the
# prompt of a 1.5B model.
MAX_CHARS = 100
# Lines read from the file. The template asks for 30 to 40.
MAX_LINES = 60
# Lines shown to the answer model for one example.
PICKS = 3
# A line is shown only if it is closer to the question and its kept sentences than the
# teacher's lines are on average, by at least this much (e5 cosine). e5 scores every line
# high, and how high depends on the question, so a fixed floor did not work: lines that fit
# conduction scored 0.80, below lines that fit nothing for photosynthesis, at 0.81. On 11
# explanations and 33 lines (11 November), lines that fit stood 0.035 to 0.057 above the
# mean (sowing seeds for germination, ice melting in nimbu pani for melting, weighing onions
# for a balance, earthworms for soil, covering the water pot), and the best line for topics
# none fitted, such as acids, wool and respiration, 0.026 to 0.033. One line, "Putting wet
# clothes on a line to dry", stood high for most topics, so the model must still say NONE
# when nothing fits. With no line this close, the village prompt is used straight away.
# A file of only a few lines has a mean that its best line pulls up, so it is rarely used.
MARGIN = 0.035

# "1.", "2)", "-", "*" or "•" at the start of a line, as a teacher typing a list writes them.
_LIST_MARK = re.compile(r"^\s*(?:\d{1,2}\s*[.)]|[-*•])\s*")
_DIGIT = re.compile(r"\d")
# The same shape of name answer_guard.unseen_name looks for.
_NAME = re.compile(r"\b[A-Z][a-z]{2,}\b")


def resolve_path() -> Optional[str]:
    """The examples file: $SPECIALIST_TEACHER_EXAMPLES, else the device copy, else the template."""
    env = os.environ.get(ENV)
    if env:
        return env
    for path in (DEVICE_PATH, TEMPLATE_PATH):
        if os.path.isfile(path):
            return path
    return None


def _cut(line: str) -> str:
    if len(line) <= MAX_CHARS:
        return line
    return line[:MAX_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:-")


def as_example(line: str) -> str:
    return "For example, " + line[0].lower() + line[1:]


def clean_lines(raw: str, flagged: Callable[[str], object] = check_teacher_line,
                skipped: Optional[List[str]] = None) -> List[str]:
    """The usable lines of a teacher's file, in order.

    ``flagged`` is the line check: a line it returns anything truthy for is dropped. Each
    dropped line is logged, and added to ``skipped`` with the reason, if given.
    """
    def skip(reason: str, line: str):
        logger.info("Teacher example skipped, %s: %s", reason, line)
        if skipped is not None:
            skipped.append(f"{reason}: {line}")

    kept, seen = [], set()
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = _cut(" ".join(_LIST_MARK.sub("", line).split()).rstrip(".!;, "))
        if not line:
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        if _DIGIT.search(line):
            skip("it has a number", line)
            continue
        if any(ch.isalpha() and not ch.isascii() for ch in line):
            skip("it is not in English letters", line)
            continue
        found = flagged(line)
        if found:
            skip(f"the safety check flagged it ({found})" if isinstance(found, str) else "the safety check flagged it", line)
            continue
        kept.append(line)
        if len(kept) == MAX_LINES:
            break
    return kept


def _read(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError as e:
        logger.warning("Could not read the teacher examples at %s (%s)", path, e)
        return None


def load_teacher_examples(flagged: Callable[[str], object] = check_teacher_line,
                          path: Optional[str] = None) -> List[str]:
    """Read the teacher's file. Missing or unreadable gives [], which leaves examples as before."""
    path = path or resolve_path()
    raw = _read(path)
    if raw is None:
        return []
    lines = clean_lines(raw, flagged)
    if lines:
        logger.info("Loaded %d teacher examples from %s", len(lines), path)
    return lines


def names_in(lines: List[str]) -> Set[str]:
    """Names the teacher used, such as Holi, Diwali or the haat's village, in lower case.

    Every line starts with a capital, so a line's first word counts only if the teacher also
    wrote it with a capital in the middle of a line. These are allowed in an example (see
    pipeline._checked_example), and never in an answer.
    """
    return {w.lower() for line in lines for w in _NAME.findall(line.partition(" ")[2])}


# ------------------------------------------------------------------- the cache
def cache_dir() -> Optional[str]:
    """$SPECIALIST_CACHE_DIR, else /opt/specialist/cache on the device, else ~/.cache/specialist.
    None if none of them can be written."""
    env = os.environ.get(CACHE_ENV)
    if env:
        places = [env]
    else:
        places = [DEVICE_CACHE] if os.path.isdir(os.path.dirname(DEVICE_CACHE)) else []
        places.append(os.path.join(os.path.expanduser("~"), ".cache", "specialist"))
    for place in places:
        try:
            os.makedirs(place, exist_ok=True)
        except OSError:
            continue
        if os.access(place, os.W_OK):
            return place
    return None


def _key(raw: str, embedder_name: str, check_name: str) -> str:
    text = f"{CHECK_VERSION}\n{MAX_CHARS} {MAX_LINES}\n{check_name}\n{embedder_name}\n{raw}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_CACHE_PREFIX = "teacher_examples-"


def _read_cache(path: str, key: str):
    """(lines, vectors, skipped) from a cache file, or None if it is missing, stale or broken."""
    try:
        with np.load(path, allow_pickle=False) as data:
            if str(data["key"]) != key:
                return None
            lines = [str(x) for x in data["lines"]]
            vectors = np.asarray(data["vectors"], dtype=np.float32)
            skipped = [str(x) for x in data["skipped"]]
    except FileNotFoundError:
        return None
    except Exception as e:  # noqa: BLE001  any unreadable cache is recomputed
        logger.warning("Teacher examples cache %s is unreadable (%s), checking the lines again", path, e)
        return None
    if not lines or vectors.ndim != 2 or len(vectors) != len(lines):
        return None
    return lines, vectors, skipped


def _write_cache(folder: str, path: str, key: str, lines: List[str], vectors: np.ndarray, skipped: List[str]):
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "wb") as f:
            np.savez(f, key=np.array(key), lines=np.array(lines, dtype=str),
                     vectors=np.asarray(vectors, dtype=np.float32), skipped=np.array(skipped, dtype=str))
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("Could not cache the teacher examples in %s (%s)", folder, e)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return
    # Only the current file is useful, so older caches go.
    for name in os.listdir(folder):
        if name.startswith(_CACHE_PREFIX) and name.endswith(".npz") and os.path.join(folder, name) != path:
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass


@dataclass
class TeacherExamples:
    lines: List[str] = field(default_factory=list)
    vectors: Optional[np.ndarray] = None
    names: Set[str] = field(default_factory=set)
    from_cache: bool = False


def load_cached(embed: Callable[[List[str]], np.ndarray], embedder_name: str, path: Optional[str] = None,
                flagged: Callable[[str], object] = check_teacher_line) -> TeacherExamples:
    """The teacher's checked lines, their e5 passage vectors and the names in them.

    ``flagged`` is the line check (the engine passes SafetyCheck.check_line), and ``embed``
    turns lines into L2-normalised passage vectors. Both are slow on the device, so the result
    is kept in the cache folder (cache_dir) under a key made from the file's contents,
    CHECK_VERSION, the check and ``embedder_name``. An edited file, a new check or a new
    embedder is checked again, and a missing or unreadable cache is recomputed.
    """
    path = path or resolve_path()
    raw = _read(path)
    if raw is None:
        return TeacherExamples()
    key = _key(raw, embedder_name, getattr(flagged, "__qualname__", type(flagged).__name__))
    folder = cache_dir()
    cached = os.path.join(folder, f"{_CACHE_PREFIX}{key[:24]}.npz") if folder else None
    found = _read_cache(cached, key) if cached else None
    if found:
        lines, vectors, skipped = found
        for line in skipped:
            logger.info("Teacher example skipped, %s", line)
        logger.info("Loaded %d teacher examples from %s (cached)", len(lines), path)
        return TeacherExamples(lines, vectors, names_in(lines), from_cache=True)
    skipped: List[str] = []
    lines = clean_lines(raw, flagged, skipped)
    if not lines:
        return TeacherExamples()
    vectors = np.asarray(embed(lines), dtype=np.float32)
    logger.info("Loaded %d teacher examples from %s", len(lines), path)
    if cached:
        _write_cache(folder, cached, key, lines, vectors, skipped)
    return TeacherExamples(lines, vectors, names_in(lines))


def pick(query: np.ndarray, vectors: np.ndarray, lines: List[str], k: int = PICKS,
         margin: float = MARGIN) -> List[str]:
    """Up to k lines closest to the query, best first, that stand at least margin above the
    mean score of all the lines. Vectors are L2-normalised rows, so a dot product is cosine."""
    if not lines:
        return []
    scores = vectors @ query
    order = np.argsort(-scores, kind="stable")[:k]
    return [lines[i] for i in order if scores[i] - scores.mean() >= margin]
