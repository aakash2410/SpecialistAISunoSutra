"""Embedding model: determinism, dimensions, fitted-state round-trip.

The critical property: the SAME embedder must produce comparable vectors at
build time and run time, including across separate processes (a bug we already
fixed once — Python's salted hash). These tests lock that down.
"""

import numpy as np
import pytest

from pocketinfer.models.embed import DEFAULT_MODEL_NAME, RECOMMENDED_MIN_SCORE, Embed

CORPUS = [
    "Photosynthesis is how green plants make food using sunlight and chlorophyll.",
    "Heat flows from a hotter object to a colder object by conduction and convection.",
    "The digestive system breaks food down in the stomach and small intestine.",
]


def _fitted():
    return Embed(prefer="tfidf").fit(CORPUS)


def test_tfidf_backend_selected_without_sentence_transformers():
    e = Embed(prefer="tfidf")
    assert e.backend == "tfidf"


def test_embeddings_are_l2_normalized():
    e = _fitted()
    vecs = e.embed(CORPUS, kind="passage")
    norms = np.linalg.norm(vecs, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)


def test_dimension_matches_vocab():
    e = _fitted()
    assert e.dim == len(e._vocab) > 0
    assert e.embed_one("photosynthesis", "query").shape[0] == e.dim


def test_same_text_is_deterministic():
    e = _fitted()
    a = e.embed_one("what is photosynthesis", "query")
    b = e.embed_one("what is photosynthesis", "query")
    assert np.array_equal(a, b)


def test_state_roundtrip_reproduces_vectors(tmp_path):
    """Save fitted state, reload in a fresh instance -> identical vectors.

    This is the cross-process guarantee: build time saves state, run time loads
    it, and query/index vectors stay comparable.
    """
    e1 = _fitted()
    v1 = e1.embed_one("how does heat move", "query")
    e1.save_state(str(tmp_path))

    e2 = Embed(prefer="tfidf")
    assert Embed.has_state(str(tmp_path))
    e2.load_state(str(tmp_path))
    v2 = e2.embed_one("how does heat move", "query")
    assert np.array_equal(v1, v2)


def test_on_topic_scores_above_off_topic():
    e = _fitted()
    M = e.embed(CORPUS, "passage")
    on = float((M @ e.embed_one("explain photosynthesis", "query")).max())
    off = float((M @ e.embed_one("who won the cricket match", "query")).max())
    assert off == pytest.approx(0.0, abs=1e-6)
    assert on > off


def test_recommended_threshold_is_backend_aware():
    assert Embed(prefer="tfidf").recommended_min_score == RECOMMENDED_MIN_SCORE["tfidf"]


def test_verify_classmethod_reports_backend():
    ok, msg = Embed.verify({"model_name": DEFAULT_MODEL_NAME, "prefer": "tfidf"})
    assert ok is True
    assert "tfidf" in msg


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("sentence_transformers") is None,
    reason="sentence-transformers not installed (neural embedder path)",
)
def test_neural_backend_if_available():
    # Only runs where the production embedder is installed AND cached.
    try:
        e = Embed(prefer="sentence-transformers")
    except Exception as exc:  # model not downloaded / offline
        pytest.skip(f"neural model unavailable: {exc}")
    v = e.embed_one("photosynthesis", "query")
    assert v.shape[0] == e.dim
    assert np.isclose(np.linalg.norm(v), 1.0, atol=1e-4)
