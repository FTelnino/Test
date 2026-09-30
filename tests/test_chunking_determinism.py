"""P2 contract: the chunker is a pure function of its inputs.

Splitting is deterministic and content-addressed, which is what lets the whole
pipeline be re-run safely. If chunk boundaries or ids moved between runs, a
re-ingest would write new vectors beside the old ones instead of overwriting
them, and the index would silently grow duplicates on every refresh.

These tests live apart from test_chunker.py because they are about run-to-run
stability, not about chunk *quality*, which is what the rest of that file
covers.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from rag import chunker

STRATEGIES = ("recursive", "section", "table_aware")


@pytest.fixture(scope="module")
def documents() -> list:
    return chunker.load_documents()


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_chunking_is_deterministic(documents, strategy):
    """Two splits of the same corpus must agree on every id and every text."""
    first = [c.chunk_id + "|" + c.text for c in chunker.split_all(documents, strategy)]
    second = [c.chunk_id + "|" + c.text for c in chunker.split_all(documents, strategy)]
    assert first == second


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_chunk_ids_are_unique_and_stable(documents, strategy):
    """Ids are unique within a run and identical across runs.

    Uniqueness stops one chunk overwriting another; stability across runs is what
    makes upsert an overwrite rather than an append.
    """
    ids = [c.chunk_id for c in chunker.split_all(documents, strategy)]
    assert len(ids) == len(set(ids))
    assert ids == [c.chunk_id for c in chunker.split_all(documents, strategy)]


def test_persisted_chunks_match_a_fresh_split(documents):
    """The persisted chunk file must equal what the chunker produces now.

    `scripts/gen_sources_report.py` reports per-source chunk counts read from
    data/raw/chunks_<strategy>.json, and run_ingest embeds from the same split,
    so a stale file would make the source list disagree with the index. Skipped
    when data/ is absent, e.g. on a fresh clone before run_ingest.py.
    """
    path = config.RAW_DIR / f"chunks_{config.CHUNK_STRATEGY}.json"
    if not path.exists():
        pytest.skip("no persisted chunks; run: python run_ingest.py --chunk")
    persisted = json.loads(path.read_text(encoding="utf-8"))
    fresh = [c.as_dict() for c in chunker.split_all(documents, config.CHUNK_STRATEGY)]
    assert [c["chunk_id"] for c in persisted] == [c["chunk_id"] for c in fresh]
