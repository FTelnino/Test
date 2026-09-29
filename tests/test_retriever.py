"""Stage 5 tests: scheme detection, the filter shape, and the refusal threshold.

The threshold tests are the important ones. A retriever that always returns hits
cannot express "not found", so the behaviour being pinned here is that it returns
nothing when nothing is close enough — and that the two kinds of "nothing" are
distinguished, because only one of them is the retriever's job.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from rag import retriever


# --- scheme detection ----------------------------------------------------

@pytest.mark.parametrize(
    "query,expected",
    [
        ("What is the exit load on HDFC Large Cap Fund?", "HDFC Large Cap Fund"),
        ("exit load on hdfc small cap", "HDFC Small Cap Fund"),
        ("What is the lock-in for HDFC ELSS Tax Saver Fund?", "HDFC ELSS Tax Saver Fund"),
        ("minimum SIP for HDFC Balanced Advantage Fund", "HDFC Balanced Advantage Fund"),
        ("benchmark of HDFC Equity Fund", "HDFC Equity Fund"),
        ("hdfc elss", "HDFC ELSS Tax Saver Fund"),
        ("tell me about large cap hdfc fund", "HDFC Large Cap Fund"),
    ],
)
def test_detects_a_named_scheme(query, expected):
    assert retriever.detect_scheme(query) == expected


def test_longest_alias_wins():
    """'balanced advantage' must beat the shorter alias it contains, and must not
    be stolen by any other scheme's alias."""
    assert retriever.detect_scheme("hdfc balanced advantage fund") == (
        "HDFC Balanced Advantage Fund"
    )
    assert retriever.detect_scheme("what about hdfc balanced advantage?") == (
        "HDFC Balanced Advantage Fund"
    )
    # 'hdfc elss' must not be shadowed by the bare 'elss' alias of the same scheme
    assert retriever.detect_scheme("hdfc elss tax saver fund") == (
        "HDFC ELSS Tax Saver Fund"
    )


def test_a_bare_category_phrase_is_not_detected():
    """Distinctive or not, a category without an HDFC cue is ambiguous, so it
    returns None and lets MIN_SCORE decide rather than guessing a fund."""
    assert retriever.detect_scheme("balanced advantage fund") is None


@pytest.mark.parametrize(
    "query",
    [
        "What is the expense ratio of a mutual fund?",
        "How do I download a capital gains statement?",
        "What is the boiling point of water?",
    ],
)
def test_returns_none_when_no_scheme_is_named(query):
    assert retriever.detect_scheme(query) is None


def test_a_category_word_alone_is_not_a_scheme():
    """A category does not identify a fund. Matching bare 'flexi cap' filtered a
    Parag Parikh question to HDFC Equity Fund and manufactured a wrong answer."""
    assert retriever.detect_scheme("expense ratio of Parag Parikh Flexi Cap Fund") is None
    assert retriever.detect_scheme("exit load on Mirae Asset Large Cap Fund") is None


def test_a_category_word_with_an_hdfc_cue_is_a_scheme():
    assert retriever.detect_scheme("hdfc flexi cap fund") == "HDFC Equity Fund"
    assert retriever.detect_scheme("small cap or large cap?") is None


# --- the filter shape ----------------------------------------------------

def test_filter_always_includes_general_scope():
    """The ELSS lock-in is only on an AMFI page tagged scope=general, so a plain
    scheme filter would make the demo's own question unanswerable."""
    where = retriever.build_filter("HDFC ELSS Tax Saver Fund")
    assert where == {
        "$or": [{"scheme": "HDFC ELSS Tax Saver Fund"}, {"scope": "general"}]
    }
    assert {"scope": "general"} in where["$or"]


def test_no_scheme_means_no_filter():
    assert retriever.build_filter(None) is None


def test_query_expansion_only_when_a_scheme_was_detected():
    assert retriever.expand_query("exit load?", "HDFC Large Cap Fund") == (
        "exit load? HDFC Large Cap Fund"
    )
    assert retriever.expand_query("exit load?", None) == "exit load?"


def test_scheme_url_is_the_citation_default():
    assert retriever.scheme_url("HDFC Large Cap Fund") == (
        "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"
    )
    assert retriever.scheme_url(None) is None


# --- deduplication -------------------------------------------------------

def _hit(scheme, section, url, score, rank=1):
    from rag.chunker import Chunk
    from rag.vectorstore import RetrievedChunk

    return RetrievedChunk(
        rank=rank,
        score=score,
        chunk=Chunk(
            chunk_id=f"{scheme}:x:{rank}",
            text="t",
            scheme=scheme,
            category="c",
            scope="scheme",
            section=section,
            source_url=url,
            ordinal=rank,
            token_estimate=1,
            kind="prose",
        ),
    )


def test_dedupe_collapses_the_same_scheme_section_and_url():
    hits = [
        _hit("A", "Exit load", "u", 0.9, 1),
        _hit("A", "Exit load", "u", 0.8, 2),
        _hit("A", "AUM", "u", 0.7, 3),
    ]
    kept = retriever._dedupe(hits)
    assert [h.chunk.section for h in kept] == ["Exit load", "AUM"]
    assert [h.score for h in kept] == [0.9, 0.7]


def test_dedupe_keeps_the_best_scoring_member():
    hits = [
        _hit("A", "Exit load", "u", 0.5, 1),
        _hit("A", "Exit load", "u", 0.9, 2),
    ]
    assert retriever._dedupe(hits)[0].score == 0.9


def test_dedupe_renumbers_so_rank_matches_position():
    kept = retriever._dedupe([_hit("A", f"S{i}", "u", 1.0 - i / 10, i) for i in range(5)])
    assert [h.rank for h in kept] == [1, 2, 3, 4, 5]


def test_dedupe_does_not_merge_different_schemes_or_sections():
    hits = [
        _hit("A", "Exit load", "u", 0.9, 1),
        _hit("B", "Exit load", "u", 0.8, 2),
        _hit("A", "AUM", "u2", 0.7, 3),
    ]
    assert len(retriever._dedupe(hits)) == 3


# --- the live index ------------------------------------------------------

@pytest.fixture(scope="module")
def indexed():
    from rag import vectorstore

    if vectorstore.count() == 0:
        pytest.skip("no index; run: python run_ingest.py --index")
    return True


def test_acceptance_questions_retrieve_from_the_right_scheme(indexed):
    golden = [
        json.loads(line)
        for line in config.GOLDEN_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for entry in golden:
        if entry.get("kind") != "in_corpus" or not entry.get("expect_scheme"):
            continue
        hits = retriever.search(entry["question"])
        assert hits, f"no evidence for {entry['question']!r}"
        assert hits[0].chunk.scheme == entry["expect_scheme"], (
            f"{entry['question']!r} retrieved {hits[0].chunk.scheme!r}, "
            f"expected {entry['expect_scheme']!r}"
        )


def test_unrelated_questions_return_nothing(indexed):
    for question in (
        "What is the boiling point of water at sea level?",
        "How do I change the font size on my iPhone?",
    ):
        assert retriever.search(question) == [], question


def test_evidence_is_ranked_and_bounded(indexed):
    hits = retriever.search("What is the exit load on HDFC Small Cap Fund?")
    assert 0 < len(hits) <= config.TOP_K
    assert [h.rank for h in hits] == list(range(1, len(hits) + 1))
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)


def test_threshold_is_actually_load_bearing(indexed, monkeypatch):
    """Raise MIN_SCORE above the best score and the retriever must go silent.
    Without this, a passing threshold test could just mean the code ignores it."""
    monkeypatch.setattr(config, "MIN_SCORE", 0.99)
    assert retriever.search("What is the exit load on HDFC Small Cap Fund?") == []


def test_evidence_reaches_general_scope_material(indexed):
    """The ELSS lock-in question must be able to reach the AMFI page."""
    hits = retriever.search("What is the lock-in period for HDFC ELSS Tax Saver Fund?")
    assert hits
    assert any(h.chunk.scope == "general" for h in hits) or any(
        "lock" in h.chunk.section.lower() for h in hits
    )


def test_golden_set_is_well_formed():
    """The golden file is shared by two consumers, so assert both contracts.

    `retrieval_probe` rows drive the P4 calibration and must keep the retrieval
    `kind`s and the six PRD acceptance topics. Every row also drives the
    end-to-end evaluator and must carry an `expected_status` the pipeline can
    return. The split matters: a row with no `retrieval_probe` is answered by a
    guard before retrieval, so including it in the P4 gate would score it against
    a threshold that does not apply to it.
    """
    entries = [
        json.loads(line)
        for line in config.GOLDEN_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(entries) >= 7

    probe = [entry for entry in entries if entry.get("retrieval_probe")]
    assert probe, "no retrieval_probe rows; the P4 gate would silently run empty"
    kinds = {entry.get("kind") for entry in probe}
    assert kinds <= {"in_corpus", "out_of_corpus", "out_of_scope"}, kinds
    topics = [entry["topic"] for entry in probe if entry["kind"] == "in_corpus"]
    for required in (
        "expense ratio",
        "exit load",
        "minimum SIP",
        "ELSS lock-in",
        "riskometer/benchmark",
        "capital-gains statement",
    ):
        assert required in topics, f"golden set is missing the {required!r} acceptance question"

    valid_statuses = {
        "ANSWERED", "NOT_FOUND", "REFUSED_ADVICE",
        "REFUSED_RETURNS", "REFUSED_PII", "OUT_OF_SCOPE",
    }
    for entry in entries:
        assert entry.get("expected_status") in valid_statuses, entry["question"]
    assert {entry["expected_status"] for entry in entries} == valid_statuses
    assert len(entries) == len({entry["question"] for entry in entries}), "duplicate question"
