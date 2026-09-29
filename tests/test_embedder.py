"""Stage 3 tests: the embedder's two jobs — one model, and vectors of a fixed shape.

The invariant worth defending is that documents and queries go through the same
encoder with the same normalisation. A mismatch there produces no error and no
warning; it just returns worse answers, which is the hardest kind of bug to spot
in a demo. So these tests pin the model identity, the dimension, and the norm.
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from rag import embedder


# --- cache location (NFR-3) ---------------------------------------------

def test_hf_cache_points_into_the_project():
    """The model must land in data/models/ so the demo runs offline later."""
    assert os.environ["HF_HOME"] == str(config.MODEL_CACHE_DIR)
    assert os.environ["SENTENCE_TRANSFORMERS_HOME"] == str(config.MODEL_CACHE_DIR)
    assert config.MODEL_CACHE_DIR.name == "models"
    assert config.MODEL_CACHE_DIR.parent.name == "data"


def test_model_id_is_the_pinned_encoder():
    assert embedder.model_id() == "sentence-transformers/all-MiniLM-L6-v2"
    assert embedder.model_id() == config.EMBED_MODEL


# --- API surface ---------------------------------------------------------

def test_no_per_call_model_override():
    """The spec forbids swapping the model per call, because ingest and query
    would silently end up on different encoders."""
    import inspect

    for function in (embedder.embed_documents, embedder.embed_query):
        parameters = set(inspect.signature(function).parameters)
        assert "model" not in parameters, function.__name__
        assert "model_name" not in parameters, function.__name__


def test_module_exposes_no_other_encoder_constructor():
    """Only get_model() may build a SentenceTransformer."""
    import inspect

    source = inspect.getsource(embedder)
    assert source.count("SentenceTransformer(") == 1
    assert "from sentence_transformers import SentenceTransformer" in source


def test_embed_query_rejects_an_empty_query():
    with pytest.raises(ValueError):
        embedder.embed_query("")
    with pytest.raises(ValueError):
        embedder.embed_query("   ")


def test_embed_documents_accepts_an_empty_batch():
    assert embedder.embed_documents([]) == []


def test_expected_dimension_is_384():
    assert embedder.EXPECTED_DIM == 384


def test_dimension_verifier_rejects_a_wrong_width_model():
    """A model swap that changed the width must fail loudly, not at query time."""

    class FakeModel:
        def get_sentence_embedding_dimension(self):
            return 768

    with pytest.raises(RuntimeError, match="768"):
        embedder._verify_model(FakeModel())


# --- real model (requires the cache warmed by scripts/warm_cache.py) -------

@pytest.fixture(scope="module")
def model():
    try:
        return embedder.get_model()
    except Exception as error:  # noqa: BLE001
        pytest.skip(f"embedder unavailable: {type(error).__name__}: {error}")


def test_model_loads_once_and_is_reused(model):
    assert embedder.get_model() is model
    assert embedder.get_model() is embedder.get_model()
    assert embedder.load_seconds() >= 0.0


def test_documents_and_queries_share_one_model(model):
    before = model
    embedder.embed_query("a probe query")
    assert embedder.get_model() is before


def test_document_vectors_are_384d_and_normalised(model):
    vectors = embedder.embed_documents(
        ["Exit load of 1% if redeemed within 1 year.", "Minimum SIP is Rs.100."]
    )
    assert len(vectors) == 2
    for vector in vectors:
        assert len(vector) == embedder.EXPECTED_DIM
        norm = math.sqrt(sum(value * value for value in vector))
        # L2-normalised, so cosine similarity reduces to a dot product
        assert norm == pytest.approx(1.0, abs=1e-3)


def test_query_vector_has_the_same_shape_as_document_vectors(model):
    query = embedder.embed_query("What is the minimum SIP amount?")
    documents = embedder.embed_documents(["Minimum SIP Investment is set to Rs.100."])
    assert len(query) == len(documents[0]) == embedder.EXPECTED_DIM


def test_a_related_pair_scores_higher_than_an_unrelated_pair(model):
    """A sanity check on the vectors themselves, before P4 calibrates on them."""
    anchor = embedder.embed_documents(["Exit load of 1% if redeemed within 1 year."])[0]
    near = embedder.embed_documents(["Exit load is waived after one year."])[0]
    far = embedder.embed_documents(["The trustee oversees the fund's custody."])[0]
    dot = lambda a, b: sum(x * y for x, y in zip(a, b))
    assert dot(anchor, near) > dot(anchor, far)


def test_embedding_is_deterministic(model):
    first = embedder.embed_documents(["Minimum SIP Investment is set to Rs.100."])
    second = embedder.embed_documents(["Minimum SIP Investment is set to Rs.100."])
    assert first == second


def test_batch_size_argument_changes_nothing_about_the_vectors(model):
    """batch_size is the only per-call knob; it must not alter the output.

    Floating point differences across batch boundaries are tiny, so we assert
    with a tolerance rather than an exact list equality.
    """
    texts = [f"chunk number {i} about exit load and SIP amounts" for i in range(8)]
    batch8 = embedder.embed_documents(texts, batch_size=8)
    batch1 = embedder.embed_documents(texts, batch_size=1)
    assert len(batch8) == len(batch1)
    for a, b in zip(batch8, batch1):
        assert len(a) == len(b)
        for x, y in zip(a, b):
            assert math.isclose(x, y, abs_tol=1e-5, rel_tol=1e-5)
