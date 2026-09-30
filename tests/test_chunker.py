"""Stage 2 tests: chunk invariants, strategy behaviour, and the bugs this build hit.

Several tests here are regressions for defects found while building P2, each of
which produced *valid-looking* output. The worst one silently deleted the graded
answers from the corpus, so the data-loss tests assert on real corpus text
rather than only on synthetic input.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from rag import chunker
from rag.chunker import Segment, _is_numeric_run, _overlap_tail, _split_table_block
from rag.loader import Document
from sources import Source

STRATEGIES = ("recursive", "section", "table_aware")


@pytest.fixture(scope="module")
def documents() -> list[Document]:
    return chunker.load_documents()


@pytest.fixture(scope="module")
def chunked(documents) -> dict[str, list[chunker.Chunk]]:
    return {name: chunker.split_all(documents, name) for name in STRATEGIES}


def make_document(text: str, **overrides) -> Document:
    base = {
        "scheme": "HDFC Large Cap Fund",
        "category": "Large Cap",
        "url": "https://example.test/scheme",
        "slug": "hdfc-large-cap-fund-direct-growth",
    }
    base.update(overrides)
    return Document(
        source=Source(**base),
        text=text,
        fetched_at="2026-09-27T00:00:00Z",
        text_hash="deadbeefcafe0000",
    )


# --- invariants ----------------------------------------------------------

@pytest.mark.parametrize("strategy", STRATEGIES)
def test_no_invariant_violations_on_the_real_corpus(chunked, strategy):
    """The P2 gate: validate_chunks() must return [] for every strategy."""
    assert chunker.validate_chunks(chunked[strategy]) == []


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_every_chunk_respects_the_size_budget(chunked, strategy):
    for chunk in chunked[strategy]:
        assert len(chunk.text) <= config.CHUNK_SIZE + chunker._size_tolerance(chunk)


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_no_chunk_is_undersized_or_empty(chunked, strategy):
    for chunk in chunked[strategy]:
        assert len(chunk.text) >= config.MIN_CHUNK_CHARS, chunk.chunk_id
        assert chunk.text.strip()


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_metadata_is_always_populated(chunked, strategy):
    for chunk in chunked[strategy]:
        assert chunk.scheme and chunk.category and chunk.scope
        assert chunk.section.strip(), f"{chunk.chunk_id} has no section"
        assert chunk.source_url.startswith("http")
        assert chunk.kind in {"prose", "table"}
        assert chunk.strategy == strategy
        assert chunk.token_estimate >= 1


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_chunk_ids_are_unique_and_content_addressed(chunked, strategy):
    ids = [chunk.chunk_id for chunk in chunked[strategy]]
    assert len(set(ids)) == len(ids)
    for chunk in chunked[strategy]:
        # f"{scheme_slug}:{text_hash[:8]}:{ordinal}"
        scheme_slug, hash_prefix, ordinal = chunk.chunk_id.rsplit(":", 2)
        assert len(hash_prefix) == 8
        assert int(ordinal) == chunk.ordinal


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_ordinals_are_sequential_per_document(chunked, strategy):
    seen: dict[tuple[str, str], list[int]] = {}
    for chunk in chunked[strategy]:
        seen.setdefault((chunk.scheme, chunk.scope), []).append(chunk.ordinal)
    for key, ordinals in seen.items():
        assert ordinals == list(range(len(ordinals))), key


# --- data loss: the regression that mattered -----------------------------

def _comparable(text: str) -> str:
    """Normalise for a cross-chunk containment check.

    A chunk seam consumes the separator that was split on, so a sentence cut at a
    boundary loses its terminating period ("... Min" + "for SIP ..."). Punctuation
    is therefore dropped on both sides; the assertion is about words surviving,
    not about which chunk a period ended up in.
    """
    return re.sub(r"\s+", " ", text.replace(".", "")).strip()


def test_no_fact_sentence_is_lost_from_the_corpus(documents, chunked):
    """Every prose fact sentence must survive into some chunk of its document.

    Regression. A window under MIN_CHUNK_CHARS used to be discarded on flush. The
    Groww pages put fact sentences in short prose islands between two tables, so
    "Minimum SIP Investment is set to Rs.100." and "is rated Very High risk." were
    dropped outright while the chunker still reported zero violations, because
    the lost text was never a chunk.
    """
    for strategy in STRATEGIES:
        for document in documents:
            blob = _comparable(
                " ".join(
                    chunk.text
                    for chunk in chunked[strategy]
                    if chunk.scheme == document.source.scheme
                    and chunk.scope == document.source.scope
                )
            )
            for segment in chunker.segment_document(document):
                for sentence in chunker.SENTENCE_SPLIT.split(segment.text):
                    sentence = re.sub(r"\s+", " ", sentence).strip()
                    if len(sentence) < 25:
                        continue
                    assert _comparable(sentence) in blob, (
                        f"{strategy} dropped from {document.source.scheme}: {sentence[:90]!r}"
                    )


def test_graded_facts_are_retrievable_by_text(chunked):
    """The demo's headline answers must be present verbatim in the chosen output."""
    blob = " ".join(chunk.text for chunk in chunked["table_aware"])
    for fact in (
        "Minimum SIP Investment is set to Rs.100.",
        "The HDFC Large Cap Fund Direct Growth is rated Very High risk.",
        "Exit load of 1% if redeemed within 1 year",
        "Fund benchmark NIFTY 100 Total Return Index",
    ):
        assert fact in blob, fact


def test_short_prose_island_is_not_dropped():
    """A short sentence between two tables must be kept, not discarded."""
    text = (
        "| Name | Weight |\n|---|---|\n" + "| Acme | 5.0% |\n" * 40
        + "\nMinimum SIP Investment is set to Rs.100.\n"
        + "| Name | Weight |\n|---|---|\n" + "| Beta | 1.0% |\n" * 40
    )
    chunks = chunker.split_all([make_document(text)], "table_aware")
    assert "Minimum SIP Investment is set to Rs.100." in " ".join(c.text for c in chunks)
    assert chunker.validate_chunks(chunks) == []


# --- table handling ------------------------------------------------------

def test_table_rows_are_never_split_mid_row(chunked):
    """A cut row shows up as a line that starts mid-row, i.e. text then a pipe."""
    def orphans(chunks) -> int:
        return sum(
            1
            for chunk in chunks
            for line in chunk.text.split("\n")
            if line.strip()
            and not line.strip().startswith("|")
            and re.search(r"\|\s*[A-Za-z0-9]", line)
        )

    assert orphans(chunked["table_aware"]) < orphans(chunked["recursive"]) / 5


def test_table_aware_marks_and_keeps_rows_intact(chunked):
    chunks = chunked["table_aware"]
    rows = [line for c in chunks for line in c.text.split("\n") if line.strip().startswith("|")]
    assert rows, "expected markdown table rows in the corpus"
    intact = [row for row in rows if row.count("|") >= 4]
    assert len(intact) / len(rows) > 0.95
    assert any(c.kind == "table" for c in chunks)


def test_split_table_block_never_exceeds_the_budget():
    rows = [f"| Company {i} | Sector | Equity | {i}.{i}% |" for i in range(200)]
    segment = Segment("\n".join(rows), "table")
    for piece in _split_table_block(segment, config.CHUNK_SIZE):
        assert len(piece) <= config.CHUNK_SIZE
        assert all(line.strip().startswith("|") for line in piece.split("\n"))


def test_single_line_block_falls_back_to_word_splitting():
    """A block with no row boundary must still be cut, or it exceeds the budget."""
    segment = Segment("word " * 900, "table")
    for piece in _split_table_block(segment, config.CHUNK_SIZE):
        assert len(piece) <= config.CHUNK_SIZE


# --- oversized prose, the AMFI run-on line --------------------------------

def test_oversized_prose_segment_is_split(documents):
    """The AMFI PDF body is one 27k line with no sentence punctuation."""
    amfi = [d for d in documents if "AMFI" in d.source.scheme and d.source.content_type == "pdf"]
    assert amfi, "expected the AMFI PDF in the corpus"
    segments = chunker._normalize_segments(
        chunker.segment_document(amfi[0]), config.CHUNK_SIZE
    )
    assert all(len(s) <= config.CHUNK_SIZE for s in segments)


def test_run_on_line_without_punctuation_is_still_chunked():
    text = "Trustee Sponsor Investors " * 400
    chunks = chunker.split_all([make_document(text)], "table_aware")
    assert chunker.validate_chunks(chunks) == []
    assert len(chunks) > 1


# --- overlap -------------------------------------------------------------

def test_overlap_tail_respects_the_budget():
    window = [Segment("a" * 60, "prose"), Segment("b" * 60, "prose"), Segment("c" * 60, "prose")]
    tail = _overlap_tail(window, 100)
    assert tail, "expected a carried tail"
    assert sum(len(s) for s in tail) + len(tail) - 1 <= 100


def test_overlap_is_actually_applied(chunked):
    """Adjacent chunks of a document should repeat a boundary sentence.

    Regression. The carry was written into a bookkeeping set that was never read,
    so CHUNK_OVERLAP was silently a no-op.
    """
    documents = chunked["table_aware"]
    by_doc: dict[tuple[str, str], list[chunker.Chunk]] = {}
    for chunk in documents:
        by_doc.setdefault((chunk.scheme, chunk.scope), []).append(chunk)
    shared = 0
    for chunks in by_doc.values():
        for left, right in zip(chunks, chunks[1:]):
            if set(left.text.split("\n")) & set(right.text.split("\n")):
                shared += 1
    assert shared > 0, "no adjacent chunks share text, so overlap is not applied"


def test_overlap_never_pushes_a_chunk_over_budget(chunked):
    """Regression: seeding the carry without accounting for the segment already
    in the window produced 824-character chunks."""
    for chunk in chunked["table_aware"]:
        assert len(chunk.text) <= config.CHUNK_SIZE + chunker._size_tolerance(chunk)


# --- section metadata ----------------------------------------------------

def test_section_is_meaningful_not_just_the_scheme_name(chunked):
    sections = {chunk.section for chunk in chunked["table_aware"]}
    assert len(sections) >= 8, sections
    for expected in ("Exit load", "Benchmark", "Portfolio holdings", "Minimum investment"):
        assert expected in sections, f"missing {expected!r} in {sorted(sections)}"


def test_infer_section_falls_back_to_the_scheme_name():
    assert chunker.infer_section("An unlabelled sentence.", "HDFC Small Cap Fund") == (
        "HDFC Small Cap Fund"
    )


# --- table-like line detection -------------------------------------------

def test_numeric_run_detection():
    assert _is_numeric_run("1.20 3.40 5.60") is True
    assert _is_numeric_run("2024 12.5% -3.1") is True
    # consecutive-ness matters: numbers separated by words is prose
    assert _is_numeric_run("NAV: 25 Sep '26 Rs.1,189.08 Min. for SIP Rs.100") is False
    # too long to be a row, however many numbers it holds
    assert _is_numeric_run("1 2 3 " + "x" * 400) is False


def test_heading_based_strategies_use_headings_when_present():
    """SectionSplitter is inert on this corpus because there are no headings.
    That is a property of the corpus, not a bug, so prove it works on input
    that does have headings. The bodies are padded past MIN_CHUNK_CHARS."""
    text = (
        "## Fees\nExit load is 1% if redeemed within one year of purchase. "
        + "The load is waived after the statutory lock-in period elapses. " * 8
        + "\n\n## Benchmark\nThe fund benchmark is the NIFTY 100 Total Return Index. "
        + "Returns are measured against this index over one and three year periods. " * 8
    )
    assert len(text) > 2 * config.CHUNK_SIZE
    chunks = chunker.split_all([make_document(text)], "section")
    assert len(chunks) >= 2
    # the heading is carried into the chunk text, not just recorded as metadata
    assert any("## Fees" in chunk.text for chunk in chunks)
    assert any("## Benchmark" in chunk.text for chunk in chunks)
    assert chunker.validate_chunks(chunks) == []


# --- API -----------------------------------------------------------------

def test_get_strategy_rejects_an_unknown_name():
    with pytest.raises(KeyError):
        chunker.get_strategy("nope")


def test_persist_chunks_round_trips(tmp_path):
    documents = chunker.load_documents()
    chunks = chunker.split_all(documents, "table_aware")
    path = chunker.persist_chunks(chunks, tmp_path / "chunks.json")
    import json

    written = json.loads(Path(path).read_text(encoding="utf-8"))
    assert len(written) == len(chunks)
    assert written[0]["chunk_id"] == chunks[0].chunk_id
    assert set(written[0]) == set(chunks[0].as_dict())


def test_strategy_stats_shape(chunked):
    stats = chunker.strategy_stats(chunked["table_aware"])
    for key in ("chunks", "mean_chars", "median_chars", "min_chars", "max_chars"):
        assert key in stats
    assert stats["min_chars"] <= stats["mean_chars"] <= stats["max_chars"]
