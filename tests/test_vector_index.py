"""Local vector search over the baked index."""

import numpy as np
import pytest

from pocketinfer.models.embed import Embed
from pocketinfer.specialist.vector_index import Retrieved, VectorIndex


def test_loads_and_reports_shape(tfidf_index_dir):
    idx = VectorIndex(tfidf_index_dir)
    assert len(idx.chunks) == idx.embeddings.shape[0] > 0
    assert idx.dim == idx.embeddings.shape[1]
    assert idx.embedder_name  # from manifest


def test_search_returns_sorted_results(tfidf_index_dir):
    idx = VectorIndex(tfidf_index_dir)
    e = Embed(prefer="tfidf").load_state(tfidf_index_dir)
    results = idx.search(e.embed_one("what is photosynthesis", "query"), top_k=4)
    assert 1 <= len(results) <= 4
    assert all(isinstance(r, Retrieved) for r in results)
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True), "results must be ranked high->low"


def test_top_hit_for_photosynthesis_is_the_right_chapter(tfidf_index_dir):
    idx = VectorIndex(tfidf_index_dir)
    e = Embed(prefer="tfidf").load_state(tfidf_index_dir)
    top = idx.search(e.embed_one("what is photosynthesis", "query"), top_k=1)[0]
    assert "Photosynthesis" in top.section or "Nutrition in Plants" in top.chapter
    assert top.citation().startswith("DIKSHA ")


def test_off_topic_scores_near_zero(tfidf_index_dir):
    idx = VectorIndex(tfidf_index_dir)
    e = Embed(prefer="tfidf").load_state(tfidf_index_dir)
    top = idx.search(e.embed_one("who won the football world cup", "query"), top_k=1)[0]
    assert top.score == pytest.approx(0.0, abs=1e-6)


def test_dimension_mismatch_raises(tfidf_index_dir):
    idx = VectorIndex(tfidf_index_dir)
    with pytest.raises(ValueError):
        idx.search(np.zeros(idx.dim + 1, dtype=np.float32), top_k=1)
