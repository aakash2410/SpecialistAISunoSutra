"""Which baked DIKSHA index the Specialist answers from.

One place for the choice, so the voice loop, the module build, the tests and the training
scripts all agree. Standard library only, so anything can import it cheaply.

Order: $SPECIALIST_INDEX_DIR, then the first index found in INDEX_PREFERENCE, looking in
the device install before the repo.
"""

from __future__ import annotations

import os
from typing import Iterable, Optional

INDEX_PREFERENCE = ("diksha_g6_8_science", "diksha_g7_all", "diksha_g7_science")
# diksha_g7_science is a 10-passage demonstration slice, not a real harvest.
DEMO_INDEX = "diksha_g7_science"
DEVICE_CORPUS = "/opt/specialist/corpus"
REPO_CORPUS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "corpus"))


def find_index(roots: Iterable[str] = (DEVICE_CORPUS, REPO_CORPUS),
               names: Iterable[str] = INDEX_PREFERENCE) -> Optional[str]:
    """The first <root>/<name>/index with embeddings.npy in it, or None."""
    names = tuple(names)
    for root in roots:
        for name in names:
            path = os.path.join(root, name, "index")
            if os.path.exists(os.path.join(path, "embeddings.npy")):
                return path
    return None


def resolve_index() -> str:
    """$SPECIALIST_INDEX_DIR, else the first baked index found, else the demo slice's path."""
    env = os.environ.get("SPECIALIST_INDEX_DIR")
    if env:
        return env
    return find_index() or os.path.join(REPO_CORPUS, "diksha_g7_science", "index")


def full_index(root: str = REPO_CORPUS) -> str:
    """The real harvest's index in the repo (not the demo slice), for tests and training.

    diksha_g6_8_science once it is built, until then diksha_g7_all.
    """
    names = [n for n in INDEX_PREFERENCE if n != DEMO_INDEX]
    return find_index(roots=(root,), names=names) or os.path.join(root, "diksha_g7_all", "index")
