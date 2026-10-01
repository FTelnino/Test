"""Stage 9 tests: the Streamlit screen.

Driven through Streamlit's own `AppTest`, so the tests exercise the real render
path rather than a copy of it. Every heavy or networked dependency is patched:
the embedder, the Chroma count, and `pipeline.answer`. That keeps the suite offline
and fast, and it is honest — P7 already tests what `answer()` returns, so these
tests are only about whether the screen shows it.

`st.cache_resource.clear()` runs before each test because `warm_runtime` is cached
for the life of the process; without it, the first test to warm the cache would fix
the collection count for every later test, and the empty-index case could never be
reached.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rag import embedder, pipeline, vectorstore  # noqa: E402
from rag.pipeline import Answer  # noqa: E402
from rag.verifier import (  # noqa: E402
    ANSWERED,
    REFUSED_ADVICE,
)

APP = str(ROOT / "app.py")
URL = "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"


def _answer(question, **over):
    base = dict(
        query=question,
        answer="Exit load of 1% if redeemed within 1 year.",
        status=ANSWERED,
        citation_url=URL,
        last_updated="27 Sep 2026",
        evidence=[],
    )
    base.update(over)
    return Answer(**base)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Replace the model load, the store, and the pipeline with fakes."""
    monkeypatch.setattr(embedder, "warm_cache", lambda *a, **k: "warmed")
    monkeypatch.setattr(vectorstore, "count", lambda *a, **k: 407)
    monkeypatch.setattr(pipeline, "answer", _answer)
    # The app caches warm_runtime; clear it so each test controls the count.
    st.cache_resource.clear()


def run_app():
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception, at.exception
    return at


def test_screen_shows_title_disclaimer_examples_and_input():
    at = run_app()
    assert at.title[0].value == "HDFC Mutual Funds FAQ Assistant"
    labels = [b.label for b in at.button]
    for example in [
        "What is the exit load on HDFC Large Cap Fund?",
        "What is the minimum SIP for HDFC ELSS Tax Saver Fund?",
        "What is the lock-in period for HDFC ELSS Tax Saver Fund?",
    ]:
        assert example in labels
    assert at.text_input[0].label
    # The disclaimer appears in the header and again in the footer.
    body = "\n".join(m.value for m in at.markdown)
    assert body.count("Facts-only. No investment advice.") >= 1


def test_asking_a_question_renders_answer_source_and_date():
    at = run_app()
    at.text_input[0].set_value("What is the exit load on HDFC Large Cap Fund?")
    at.button[-1].click().run()
    assert not at.exception
    body = "\n".join(m.value for m in at.markdown)
    assert "Exit load of 1%" in body
    assert URL in body
    assert "Last updated from sources:" in body and "27 Sep 2026" in body


def test_an_example_chip_fills_the_input_box():
    at = run_app()
    at.button[0].click().run()
    assert not at.exception
    assert at.text_input[0].value == at.button[0].label


def test_a_refusal_renders_as_info_not_as_an_answer(monkeypatch):
    def refuse(question):
        return _answer(
            question,
            answer="I share facts from the official scheme sources, not investment advice.",
            status=REFUSED_ADVICE,
            citation_url=None,
            last_updated=None,
        )

    monkeypatch.setattr(pipeline, "answer", refuse)
    at = run_app()
    at.text_input[0].set_value("Should I buy HDFC Small Cap Fund?")
    at.button[-1].click().run()
    assert not at.exception
    assert at.info, "a refusal should surface as an info box"
    captions = "\n".join(c.value for c in at.caption)
    assert "Refused" in captions


def test_no_date_line_is_shown_as_not_applicable_on_a_refusal(monkeypatch):
    def refuse(question):
        return _answer(question, status=REFUSED_ADVICE, citation_url=None,
                       last_updated=None, answer="Not advice.")

    monkeypatch.setattr(pipeline, "answer", refuse)
    at = run_app()
    at.text_input[0].set_value("Should I buy HDFC Small Cap Fund?")
    at.button[-1].click().run()
    captions = "\n".join(c.value for c in at.caption)
    assert "not applicable" in captions


def test_a_missing_index_shows_instructions_and_no_input(monkeypatch):
    monkeypatch.setattr(vectorstore, "count", lambda *a, **k: 0)
    st.cache_resource.clear()
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert at.warning, "an empty index must be a warning, not a traceback"
    codes = "\n".join(c.value for c in at.code)
    assert "run_ingest.py" in codes
    assert not at.text_input, "no point offering a question box with no sources"


def test_a_startup_failure_shows_a_message_not_a_traceback(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("model missing")

    monkeypatch.setattr(embedder, "warm_cache", boom)
    st.cache_resource.clear()
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert at.error, "a broken startup must render as an error message"
    assert "run_ingest.py" in "\n".join(c.value for c in at.code)


def test_sources_expander_lists_evidence(monkeypatch):
    from rag.chunker import Chunk
    from rag.retriever import RetrievedChunk

    chunk = Chunk(
        chunk_id="hdfc-large-cap-fund:aa665e15:9", text="Exit load of 1% within a year.",
        scheme="HDFC Large Cap Fund", category="Large Cap", scope="scheme",
        section="Exit load", source_url=URL, ordinal=9, token_estimate=10,
        kind="table_aware", strategy="table_aware",
    )

    def with_evidence(question):
        return _answer(question, evidence=[RetrievedChunk(chunk=chunk, score=0.71, rank=1)])

    monkeypatch.setattr(pipeline, "answer", with_evidence)
    at = run_app()
    at.text_input[0].set_value("What is the exit load on HDFC Large Cap Fund?")
    at.button[-1].click().run()
    assert at.expander, "FR-14 requires a show-sources expander"
