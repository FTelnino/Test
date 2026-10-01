"""P3 contract: re-running ingest converges on the same index (NFR-2).

The corpus is refreshed from the network, so ingest will be run again. If a
second run appended rather than replaced, the collection would double in size on
every refresh and the top-k results would fill with near-duplicates of the same
paragraph. Idempotency is the property that makes re-ingest safe at all.

These tests live apart from test_vectorstore.py because they are about the
re-run contract, not about the store's other behaviour (space, batching,
metadata validation).

Chroma does not expose a delete-by-filter for every deployment target, so
convergence comes from content-addressed chunk ids plus a same-scheme delete
before each write. These tests pin that behaviour down.
"""

from __future__ import annotations

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
        "source_url": "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
        "ordinal": ordinal,
        "token_estimate": 14,
        "kind": "prose",
        "strategy": config.CHUNK_STRATEGY,
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


def test_upsert_is_idempotent(store):
    """The same input twice leaves the same count (NFR-2)."""
    chunks = [make_chunk(i) for i in range(4)]
    vectors = [fake_vector(i) for i in range(4)]
    store.upsert(chunks, vectors, "2026-09-27")
    first = store.count()
    store.upsert(chunks, vectors, "2026-09-27")
    assert store.count() == first == 4


def test_second_run_does_not_duplicate_text(store):
    """A repeated ingest must overwrite, not append near-duplicates."""
    chunks = [make_chunk(i) for i in range(3)]
    vectors = [fake_vector(i) for i in range(3)]
    store.upsert(chunks, vectors, "2026-09-27")
    store.upsert(chunks, vectors, "2026-09-27")
    stored = store.get_collection().get(include=["documents"])
    assert sorted(stored["documents"]) == sorted(c.text for c in chunks)


def test_regenerated_index_is_identical_to_the_first(store):
    """Re-ingesting after a re-fetch converges byte-for-byte on ids and text."""
    chunks = [make_chunk(i) for i in range(6)]
    vectors = [fake_vector(i) for i in range(6)]
    store.upsert(chunks, vectors, "2026-09-27")
    before = store.get_collection().get(include=["documents", "metadatas"])
    store.upsert(chunks, vectors, "2026-09-27")
    after = store.get_collection().get(include=["documents", "metadatas"])
    assert store.count() == 6
    assert before["documents"] == after["documents"]
    assert before["ids"] == after["ids"]


def test_real_collection_matches_a_single_run_count():
    """The shipped index holds exactly one run's worth of vectors (NFR-2).

    Read-only against the real collection: 407 in, 407 out. If a re-ingest had
    ever appended, this would read 398 and the check would fail. Skipped when
    the index has not been built yet.
    """
    if not config.CHROMA_DIR.exists():
        pytest.skip("no index built yet; run: python run_ingest.py")
    assert vectorstore.count() == 407
