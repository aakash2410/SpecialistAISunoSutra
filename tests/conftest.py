"""Shared fixtures for the Specialist test suite.

The software tests are environment-independent: they build a fresh TF-IDF index
in a temp dir from the real corpus using the real ingestion pipeline, so they
pass identically on a laptop (no sentence-transformers) and on the device (where
sentence-transformers may be installed) — no network, no downloaded model.
"""

from __future__ import annotations

import os
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Import the device runtime + off-device tooling from the repo.
sys.path.insert(0, os.path.join(ROOT, "python"))
sys.path.insert(0, ROOT)

SHIPPED_INDEX = os.path.join(ROOT, "corpus", "diksha_g7_science", "index")
CORPUS_DIR = os.path.join(ROOT, "corpus", "diksha_g7_science")


@pytest.fixture(scope="session")
def shipped_index_dir():
    """The index baked into the repo (whatever embedder built it)."""
    if not os.path.exists(os.path.join(SHIPPED_INDEX, "embeddings.npy")):
        pytest.skip("No baked index. Run: python ingest/build_index.py")
    return SHIPPED_INDEX


@pytest.fixture(scope="session")
def tfidf_index_dir(tmp_path_factory):
    """A freshly built TF-IDF index — deterministic across environments.

    Exercises the real build pipeline (load -> chunk -> embed -> save).
    """
    from ingest.build_index import build

    dst = tmp_path_factory.mktemp("corpus") / "g7"
    shutil.copytree(CORPUS_DIR, dst)
    shutil.rmtree(dst / "index", ignore_errors=True)
    build(str(dst), model_name="intfloat/multilingual-e5-small", prefer="tfidf",
          max_words=120, overlap=25)
    return str(dst / "index")


@pytest.fixture
def make_engine(tfidf_index_dir):
    """Factory: a SpecialistEngine over the TF-IDF index, with optional hooks."""
    from pocketinfer.models.embed import Embed
    from pocketinfer.specialist import SpecialistEngine

    def _make(llm=None, mt=None, tts=None, **kw):
        return SpecialistEngine(
            index_dir=tfidf_index_dir,
            embedder=Embed(prefer="tfidf"),  # match the TF-IDF index regardless of env
            llm=llm, mt=mt, tts=tts, **kw,
        )

    return _make


@pytest.fixture
def engine(make_engine):
    return make_engine()
