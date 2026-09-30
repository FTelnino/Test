"""Stage 4 tests: the index is persistent, idempotent, and built only by us.

Most tests here run against a temporary Chroma directory with deterministic fake
vectors, so they are fast and cannot damage the real index. The two tests that do
touch the real collection only read from it.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from rag import vectorstore
from rag.chunker import Chunk

FAKE_DIM = 384


def fake_vector(seed: int) -> list:
    """A deterministic unit vector, so scores are predictable in tests."""
    vector = [0.0] * FAKE_DIM
    vector[seed % FAKE_DIM] = 1.0
    return vector


def make_chunk(ordinal: int, **overrides) -> Chunk:
    fields = {
        "chunk_id": f"hdfc-large-cap-fund:deadbeef:{ordinal}",
        "text": f"Exit load of 1% if redeemed within 1 year. Chunk {ordinal}.",
        "scheme": "HDFC Large Cap Fund",
        "category": "Large Cap",
        "scope": "scheme",
        "section": "Exit load",
        "source_url": "https://example.test/scheme",
        "ordinal": ordinal,
        "token_estimate": 20 + ordinal,
        "kind": "prose",
        "strategy": "table_aware",
    }
    fields.update(overrides)
    return Chunk(**fields)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A vectorstore bound to a throwaway Chroma directory."""
    monkeypatch.setattr(config, "CHROMA_DIR", tmp_path / "chroma")
    monkeypatch.setattr(vectorstore, "_client", None)
    monkeypatch.setattr(vectorstore, "_collection", None)
    yield vectorstore
    monkeypatch.setattr(vectorstore, "_client", None)
    monkeypatch.setattr(vectorstore, "_collection", None)


# --- collection setup ----------------------------------------------------

def test_collection_uses_cosine_space(store):
    assert store.get_collection().metadata.get("hnsw:space") == "cosine"
    assert store.get_collection().metadata.get("hnsw:space") == config.HNSW_SPACE


def test_chromas_default_embedding_function_is_never_attached(store):
    """Load-bearing: if Chroma embedded the text itself, stored and query vectors
    would come from two different models and every score would be meaningless."""
    collection = store.get_collection()
    assert getattr(collection, "_embedding_function", None) is None


def test_get_collection_is_a_singleton(store):
    assert store.get_collection() is store.get_collection()


def test_get_client_is_a_singleton(store):
    assert store.get_client() is store.get_client()


def test_client_settings_are_shared(store):
    """Chroma refuses a second client on one path with different settings, so
    anything needing its own client must take these settings."""
    settings = store.client_settings()
    assert settings.anonymized_telemetry is False
    assert settings.allow_reset is True


# --- metadata contract ---------------------------------------------------

def test_metadata_contains_exactly_the_declared_fields(store):
    metadata = store.chunk_metadata(make_chunk(0), "2026-09-27")
    assert set(metadata) == set(store.METADATA_FIELDS)


def test_metadata_values_are_all_scalars(store):
    """Chroma rejects nested dicts and lists, and the error is unhelpful, so the
    constraint is checked before the write."""
    metadata = store.chunk_metadata(make_chunk(0), "2026-09-27")
    for key, value in metadata.items():
        assert isinstance(value, (str, int, float, bool)), key
        assert not isinstance(value, (list, dict, tuple, set)), key


def test_metadata_carries_the_retrieval_fields(store):
    metadata = store.chunk_metadata(make_chunk(3), "2026-09-27")
    assert metadata["scheme"] == "HDFC Large Cap Fund"
    assert metadata["scope"] == "scheme"
    assert metadata["section"] == "Exit load"
    assert metadata["chunk_id"].endswith(":3")
    assert metadata["ordinal"] == 3
    assert metadata["ingested_at"] == "2026-09-27"


def test_metadata_rejects_a_non_scalar(store, monkeypatch):
    chunk = make_chunk(0)
    monkeypatch.setattr(chunk, "section", ["not", "a", "scalar"])
    with pytest.raises(TypeError, match="only"):
        store.chunk_metadata(chunk, "2026-09-27")


# --- upsert and idempotency (NFR-2) --------------------------------------

def test_upsert_writes_one_row_per_chunk(store):
    chunks = [make_chunk(i) for i in range(5)]
    written = store.upsert(chunks, [fake_vector(i) for i in range(5)], "2026-09-27")
    assert written == 5
    assert store.count() == 5


def test_upsert_updates_rather_than_appends_on_content_change(store):
    chunks = [make_chunk(0)]
    store.upsert(chunks, [fake_vector(0)], "2026-09-27")
    store.upsert(
        [make_chunk(0, text="Amended text.")], [fake_vector(0)], "2026-09-28"
    )
    assert store.count() == 1
    stored = store.get_collection().get(include=["documents", "metadatas"])
    assert stored["documents"] == ["Amended text."]
    assert stored["metadatas"][0]["ingested_at"] == "2026-09-28"


def test_upsert_batches_at_the_configured_size(store, monkeypatch):
    """199 chunks exceed one batch, so the batching path must actually run."""
    monkeypatch.setattr(config, "CHROMA_BATCH_SIZE", 10)
    chunks = [make_chunk(i) for i in range(35)]
    written = store.upsert(chunks, [fake_vector(i) for i in range(35)], "2026-09-27")
    assert written == 35
    assert store.count() == 35


def test_upsert_rejects_a_chunk_vector_count_mismatch(store):
    with pytest.raises(ValueError, match="chunks but"):
        store.upsert([make_chunk(0), make_chunk(1)], [fake_vector(0)], "2026-09-27")


def test_upsert_of_nothing_is_a_no_op(store):
    assert store.upsert([], [], "2026-09-27") == 0
    assert store.count() == 0


# --- rebuild -------------------------------------------------------------

def test_rebuild_wipes_and_recreates(store):
    store.upsert([make_chunk(0)], [fake_vector(0)], "2026-09-27")
    assert store.count() == 1
    store.rebuild()
    assert store.count() == 0


def test_rebuild_removes_orphans_from_a_removed_source(store):
    """The reason the ingest path rebuilds: a source that disappears must not
    leave its vectors behind to be retrieved forever."""
    store.upsert(
        [make_chunk(0), make_chunk(1, chunk_id="gone:deadbeef:0")],
        [fake_vector(0), fake_vector(1)],
        "2026-09-27",
    )
    store.rebuild([make_chunk(0)], [fake_vector(0)], "2026-09-27")
    ids = store.get_collection().get(include=[])["ids"]
    assert ids == ["hdfc-large-cap-fund:deadbeef:0"]


def test_rebuild_with_arguments_upserts(store):
    written = store.rebuild([make_chunk(0)], [fake_vector(0)], "2026-09-27")
    assert written == 1
    assert store.count() == 1


def test_rebuild_on_a_missing_collection_does_not_raise(store):
    """First run: there is nothing to delete, and that is not an error."""
    store.rebuild()
    assert store.count() == 0


# --- search --------------------------------------------------------------

def test_search_returns_nothing_on_an_empty_collection(store):
    assert store.search(fake_vector(0), top_k=5) == []


def test_search_ranks_from_one_and_sorts_by_score(store):
    chunks = [make_chunk(i) for i in range(5)]
    store.upsert(chunks, [fake_vector(i) for i in range(5)], "2026-09-27")
    hits = store.search(fake_vector(2), top_k=3)
    assert [hit.rank for hit in hits] == [1, 2, 3]
    scores = [hit.score for hit in hits]
    assert scores == sorted(scores, reverse=True)
    assert hits[0].chunk.chunk_id == "hdfc-large-cap-fund:deadbeef:2"
    assert hits[0].score == pytest.approx(1.0, abs=1e-6)


def test_search_reconstructs_the_chunk(store):
    original = make_chunk(7, kind="table", section="Portfolio holdings")
    store.upsert([original], [fake_vector(1)], "2026-09-27")
    rebuilt = store.search(fake_vector(1), top_k=1)[0].chunk
    assert rebuilt.chunk_id == original.chunk_id
    assert rebuilt.text == original.text
    assert rebuilt.scheme == original.scheme
    assert rebuilt.category == original.category
    assert rebuilt.scope == original.scope
    assert rebuilt.section == original.section
    assert rebuilt.source_url == original.source_url
    assert rebuilt.ordinal == original.ordinal
    assert rebuilt.token_estimate == original.token_estimate
    assert rebuilt.kind == original.kind


def test_search_clamps_top_k_to_the_collection_size(store):
    store.upsert([make_chunk(0)], [fake_vector(0)], "2026-09-27")
    assert len(store.search(fake_vector(0), top_k=50)) == 1


def test_search_uses_the_configured_top_k_by_default(store, monkeypatch):
    monkeypatch.setattr(config, "TOP_K", 2)
    store.upsert([make_chunk(i) for i in range(6)], [fake_vector(i) for i in range(6)], "2026-09-27")
    assert len(store.search(fake_vector(0))) == 2


def test_search_honours_a_where_filter(store):
    """The P4 shape: a scheme question must still reach general regulator text."""
    scheme = make_chunk(0)
    general = make_chunk(
        1,
        chunk_id="amfi-awareness:deadbeef:0",
        scheme="AMFI Investor Awareness Programme",
        scope="general",
        section="ELSS lock-in and tax",
    )
    other = make_chunk(2, chunk_id="hdfc-small-cap:deadbeef:0", scheme="HDFC Small Cap Fund")
    store.upsert(
        [scheme, general, other], [fake_vector(1), fake_vector(1), fake_vector(1)], "2026-09-27"
    )
    where = {"$or": [{"scheme": "HDFC Large Cap Fund"}, {"scope": "general"}]}
    hits = store.search(fake_vector(1), top_k=5, where=where)
    assert {hit.chunk.scheme for hit in hits} == {
        "HDFC Large Cap Fund",
        "AMFI Investor Awareness Programme",
    }


def test_search_score_is_cosine_similarity(store):
    """score == 1 - cosine distance, and equals the dot product of two
    normalised vectors, which is what the P2 decision assumed."""
    store.upsert([make_chunk(0)], [fake_vector(3)], "2026-09-27")
    other = fake_vector(4)
    other[0] = 0.5
    norm = math.sqrt(sum(x * x for x in other))
    other = [x / norm for x in other]
    hit = store.search(other, top_k=1)[0]
    expected = sum(x * y for x, y in zip(fake_vector(3), other))
    assert hit.score == pytest.approx(expected, abs=1e-5)


# --- the real index (read-only) ------------------------------------------

@pytest.fixture(scope="module")
def live_collection():
    if not config.CHROMA_DIR.exists():
        pytest.skip("no index built; run: python run_ingest.py --index")
    try:
        return vectorstore.get_collection()
    except Exception as error:  # noqa: BLE001
        pytest.skip(f"index unavailable: {type(error).__name__}: {error}")


def test_live_index_is_non_empty(live_collection):
    assert live_collection.count() > 0


def test_live_index_matches_the_p2_chunks(live_collection):
    """Every indexed row is a chunk the P2 stage actually produced."""
    expected = {
        chunk["chunk_id"]
        for chunk in json.loads(
            (config.RAW_DIR / f"chunks_{config.CHUNK_STRATEGY}.json").read_text(
                encoding="utf-8"
            )
        )
    }
    assert set(live_collection.get(include=[])["ids"]) == expected


def test_live_index_vectors_are_384d_from_the_pinned_model(live_collection):
    stored = live_collection.get(limit=5, include=["embeddings"])
    assert {len(vector) for vector in stored["embeddings"]} == {384}
    for vector in stored["embeddings"]:
        assert math.sqrt(sum(x * x for x in vector)) == pytest.approx(1.0, abs=1e-3)


def test_live_index_has_no_duplicate_ids(live_collection):
    ids = live_collection.get(include=[])["ids"]
    assert len(set(ids)) == len(ids)
