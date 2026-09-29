"""Stage 8 pipeline tests.

The pipeline's value is almost entirely in its *order*, so these tests check the
order rather than just the outputs. A pipeline that returns the right status for
the wrong reason — one that embeds a query containing a PAN, or asks the model
about a fund we do not cover, then refuses afterwards — is still a privacy or
accuracy bug even though every field looks correct.

Every test here is offline: `search` and the generator are replaced, and a
`no_llm_calls` guard fails any test that reaches the backend unexpectedly.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from rag import generator, pipeline
from rag.loader import FetchError
from rag.pipeline import Answer, answer
from rag.prompts import NOT_FOUND_TEXT
from rag.retriever import RetrievedChunk
from rag.chunker import Chunk
from rag.verifier import (
    ANSWERED,
    NOT_FOUND,
    OUT_OF_SCOPE,
    REFUSED_ADVICE,
    REFUSED_PII,
    REFUSED_RETURNS,
)

URL = "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"


def make_hit(text="The exit load is 1% within the first year.",
             score=0.71, section="Fees > Exit load",
             scheme="HDFC Large Cap Fund", chunk_id="hdfc-large-cap-fund:aa665e15:9",
             url=URL):
    chunk = Chunk(
        chunk_id=chunk_id, text=text, scheme=scheme, category="Large Cap",
        scope="scheme", section=section, source_url=url, ordinal=9,
        token_estimate=24, kind="table_aware", strategy="table_aware",
    )
    return RetrievedChunk(chunk=chunk, score=score, rank=1)


class Spy:
    """Records calls so a test can assert what was *not* reached."""

    def __init__(self, hits=None, text="The exit load is 1%.", fail=False):
        self.hits = hits if hits is not None else [make_hit()]
        self.text = text
        self.fail = fail
        self.searched = []
        self.generated = []

    def search(self, query, **kwargs):
        self.searched.append(query)
        return list(self.hits)

    def generate_ex(self, prompt, system=None):
        self.generated.append(prompt)
        if self.fail:
            raise generator.LLMUnavailable("backend unreachable")
        return generator.Completion(text=self.text, ms=12.0, tokens_out=9,
                                   model="fake-model")


@pytest.fixture
def spy(monkeypatch):
    def install(hits=None, text="The exit load is 1%.", fail=False):
        s = Spy(hits=hits, text=text, fail=fail)
        monkeypatch.setattr(pipeline, "search", s.search)
        monkeypatch.setattr(pipeline.generator, "generate_ex", s.generate_ex)
        return s
    return install


@pytest.fixture(autouse=True)
def logs():
    """Collect log lines off the pipeline logger.

    A dedicated handler rather than `caplog`, because `setup_logging()` sets
    `propagate = False` -- caplog sits on the root logger and would see nothing,
    which would have made every logging assertion below vacuously true.
    """
    for handler in list(pipeline.logger.handlers):
        pipeline.logger.removeHandler(handler)

    collected = []

    class Collector(logging.Handler):
        def emit(self, record):
            collected.append(record.getMessage())

    pipeline.logger.addHandler(Collector())
    pipeline.logger.setLevel(logging.INFO)
    pipeline.logger.propagate = False
    return collected


# --- the happy path -------------------------------------------------------

def test_a_factual_question_is_answered(spy):
    spy()
    result = answer("What is the exit load on HDFC Large Cap Fund?")
    assert result.status == ANSWERED
    assert result.answer
    assert result.citation_url == URL
    assert result.last_updated
    assert result.evidence


def test_the_gate_query_from_the_spec_works(spy):
    spy()
    result = answer("What is the exit load on HDFC Large Cap?")
    assert result.status == ANSWERED
    assert result.citation_url
    assert result.last_updated


def test_answer_is_fully_populated(spy):
    spy()
    result = answer("What is the exit load on HDFC Large Cap Fund?")
    assert isinstance(result, Answer)
    assert result.query == "What is the exit load on HDFC Large Cap Fund?"
    assert isinstance(result.answer, str) and result.answer
    assert result.status in (
        ANSWERED, NOT_FOUND, REFUSED_ADVICE, REFUSED_RETURNS, REFUSED_PII, OUT_OF_SCOPE)
    assert result.citation_url is None or result.citation_url.startswith("https://")
    assert isinstance(result.last_updated, str)
    assert isinstance(result.evidence, list)


def test_refused_property(spy):
    spy()
    assert answer("What is the exit load on HDFC Large Cap Fund?").refused is False
    assert answer("Should I buy HDFC Small Cap Fund?").refused is True


# --- ordering: the whole point -------------------------------------------

def test_a_pii_question_never_reaches_retrieval(spy):
    s = spy()
    result = answer("My PAN is ABCDE1234F, what is the exit load?")
    assert result.status == REFUSED_PII
    assert s.searched == [], "retrieval ran on a string containing a PAN"
    assert s.generated == [], "the LLM was called with a PAN in the prompt"


def test_an_advice_question_never_reaches_the_llm(spy):
    s = spy()
    result = answer("Should I buy HDFC Small Cap Fund?")
    assert result.status == REFUSED_ADVICE
    assert s.generated == []


def test_a_returns_question_never_reaches_the_llm(spy):
    s = spy()
    assert answer("What is the CAGR of HDFC Large Cap?").status == REFUSED_RETURNS
    assert s.generated == []


def test_an_out_of_scope_question_never_reaches_the_llm(spy):
    s = spy()
    assert answer("Expense ratio of Parag Parikh Flexi Cap?").status == OUT_OF_SCOPE
    assert s.generated == []


def test_a_question_with_no_evidence_never_reaches_the_llm(spy):
    s = spy(hits=[])
    result = answer("What is the Sharpe ratio of HDFC Large Cap Fund?")
    assert result.status == NOT_FOUND
    assert result.answer == NOT_FOUND_TEXT
    assert s.generated == [], "the LLM was asked to answer with no evidence"


def test_pii_is_checked_before_intent(spy):
    """A question that is both advice and carries a PAN is refused on PII, because
    PII is the more specific and more urgent of the two."""
    s = spy()
    result = answer("Should I buy this? My PAN is ABCDE1234F")
    assert result.status == REFUSED_PII
    assert s.searched == []


def test_the_stages_run_in_the_documented_order(spy, logs):
    s = spy()
    answer("What is the exit load on HDFC Large Cap Fund?")
    stages = [r.split("stage=")[1].split()[0] for r in logs if "stage=" in r]
    assert stages == ["retrieve", "generate", "verify", "render"]
    assert len(s.searched) == 1
    assert len(s.generated) == 1


def test_a_guard_refusal_logs_only_the_guard_and_render_stages(spy, logs):
    spy()
    answer("Should I buy HDFC Small Cap Fund?")
    stages = [r.split("stage=")[1].split()[0] for r in logs if "stage=" in r]
    assert stages == ["guard", "render"]


# --- the six statuses -----------------------------------------------------

def test_all_six_statuses_are_reachable(spy):
    """P7's done-when, in one test."""
    seen = set()

    spy()
    seen.add(answer("What is the exit load on HDFC Large Cap Fund?").status)
    seen.add(answer("Should I buy HDFC Small Cap Fund?").status)
    seen.add(answer("What is the CAGR of HDFC Large Cap Fund?").status)
    seen.add(answer("Expense ratio of Parag Parikh Flexi Cap?").status)
    seen.add(answer("My PAN is ABCDE1234F").status)

    spy(hits=[])
    seen.add(answer("What is the Sharpe ratio of HDFC Large Cap Fund?").status)

    assert seen == {
        ANSWERED, REFUSED_ADVICE, REFUSED_RETURNS, REFUSED_PII, OUT_OF_SCOPE, NOT_FOUND,
    }


def test_every_refusal_text_is_distinct_and_non_empty(spy):
    spy()
    texts = [
        answer("Should I buy HDFC Small Cap Fund?").answer,
        answer("What is the CAGR of HDFC Large Cap Fund?").answer,
        answer("Expense ratio of Parag Parikh Flexi Cap?").answer,
        answer("My PAN is ABCDE1234F").answer,
    ]
    assert all(texts)
    assert len(set(texts)) == len(texts)


# --- render contract ------------------------------------------------------

def test_pre_retrieval_refusals_cite_nothing(spy):
    spy()
    for question in ("Should I buy HDFC Small Cap Fund?",
                     "My PAN is ABCDE1234F",
                     "Expense ratio of Parag Parikh Flexi Cap?"):
        result = answer(question)
        assert result.citation_url is None, question
        assert result.evidence == [], question


def test_evidence_is_populated_even_without_retrieval(spy):
    """The spec requires the field to always exist, so a UI can render the expander
    unconditionally instead of guarding for None."""
    spy()
    assert answer("Should I buy HDFC Small Cap?").evidence == []


def test_citation_is_the_rank_one_chunk(spy):
    low = make_hit(text="second", score=0.4, url="https://groww.in/other")
    high = make_hit(text="first", score=0.9, url=URL)
    spy(hits=[high, low])
    assert answer("What is the exit load on HDFC Large Cap Fund?").citation_url == URL


def test_last_updated_comes_from_the_ingest_report(spy):
    spy()
    result = answer("What is the exit load on HDFC Large Cap Fund?")
    assert result.last_updated
    assert "2026" in result.last_updated or result.last_updated != ""
    # Display format, not the raw ISO string.
    assert "-" not in result.last_updated


def test_a_low_confidence_not_found_points_at_the_scheme_page(spy):
    """FR-10 and architecture.md §7.3: `top_score < MIN_SCORE` should give
    NOT_FOUND *plus a pointer to the scheme page*. With no evidence there is no
    rank-1 chunk, so the pointer comes from the scheme the question named."""
    spy(hits=[])
    result = answer("What is the Sharpe ratio of HDFC Large Cap Fund?")
    assert result.status == NOT_FOUND
    assert result.citation_url == URL


def test_a_not_found_with_no_scheme_named_cites_nothing(spy):
    """A page would have to be guessed, and a guess is exactly what this product
    is built not to do."""
    spy(hits=[])
    result = answer("What is the Sharpe ratio of the market?")
    assert result.status == NOT_FOUND
    assert result.citation_url is None


def test_a_guard_refusal_does_not_get_a_scheme_pointer(spy):
    """The scheme page fallback is for NOT_FOUND only. A refusal must cite
    nothing, or the citation implies the answer is on that page."""
    spy()
    for question in ("Should I buy HDFC Small Cap Fund?",
                     "What is the CAGR of HDFC Large Cap Fund?",
                     "My PAN is ABCDE1234F"):
        assert answer(question).citation_url is None, question


def test_last_updated_is_empty_without_evidence(spy):
    spy(hits=[])
    assert answer("What is the Sharpe ratio of HDFC Large Cap?").last_updated == ""


def test_last_updated_uses_the_newest_evidence_date(monkeypatch, spy):
    monkeypatch.setattr(
        pipeline, "_fetched_at_map",
        lambda: {URL: "2026-01-01", "https://groww.in/other": "2026-09-09"},
    )
    spy(hits=[make_hit(url=URL), make_hit(url="https://groww.in/other", score=0.4)])
    assert "Sep 2026" in answer("What is the exit load on HDFC Large Cap?").last_updated


# --- failure handling -----------------------------------------------------

def test_an_llm_outage_never_answers_from_memory(spy):
    s = spy(fail=True)
    result = answer("What is the exit load on HDFC Large Cap Fund?")
    assert result.status == NOT_FOUND
    assert result.answer == config.LLM_UNAVAILABLE_TEXT
    assert "1%" not in result.answer, "an outage must not produce a plausible answer"
    assert len(s.generated) == 1


def test_an_llm_outage_still_points_at_the_real_page(spy):
    spy(fail=True)
    result = answer("What is the exit load on HDFC Large Cap Fund?")
    assert result.citation_url == URL, "the user can still read the facts themselves"
    assert result.evidence


def test_a_broken_index_degrades_to_not_found(monkeypatch, spy):
    spy()

    def boom(*args, **kwargs):
        raise RuntimeError("chroma exploded")

    monkeypatch.setattr(pipeline, "search", boom)
    result = answer("What is the exit load on HDFC Large Cap Fund?")
    assert result.status == NOT_FOUND


def test_a_fetch_error_during_retrieval_is_reported_not_raised(monkeypatch, spy):
    # Structurally unreachable today (D11: no fetch at query time), but the spec
    # requires an explicit non-fabricated response, so the behaviour is pinned
    # rather than left to the day a fetching retriever is introduced.
    spy()

    def boom(*args, **kwargs):
        raise FetchError("source unreachable")

    monkeypatch.setattr(pipeline, "search", boom)
    result = answer("What is the exit load on HDFC Large Cap Fund?")
    assert result.status == NOT_FOUND
    assert result.answer == config.LLM_UNAVAILABLE_TEXT
    assert result.citation_url == URL, "still points at the named scheme's page"


def test_a_fetch_error_during_generation_is_reported_not_raised(monkeypatch, spy):
    spy()

    def boom(*args, **kwargs):
        raise FetchError("source unreachable")

    monkeypatch.setattr(pipeline.generator, "generate_ex", boom)
    result = answer("What is the exit load on HDFC Large Cap Fund?")
    assert result.status == NOT_FOUND
    assert result.answer == config.LLM_UNAVAILABLE_TEXT
    assert result.evidence, "evidence is kept so the citation survives"


def test_the_verifier_can_still_refuse_a_generated_answer(spy):
    """The generator is asked nicely; the verifier is what actually enforces."""
    s = spy(text="You should invest in HDFC Large Cap Fund for the long term.")
    result = answer("What is the exit load on HDFC Large Cap Fund?")
    assert result.status == REFUSED_ADVICE
    assert len(s.generated) == 1


def test_a_generated_not_found_is_not_reported_as_answered(spy):
    """Regression: the verifier used to return ANSWERED for the model's own NOT_FOUND
    string, which would render a refusal labelled as a success."""
    spy(text=NOT_FOUND_TEXT)
    result = answer("What is the ISIN code of HDFC Large Cap Fund?")
    assert result.status == NOT_FOUND


# --- logging privacy ------------------------------------------------------

def test_logs_never_contain_the_query_text(spy, logs):
    secret_question = "What is the exit load of my very specific fund choice"
    spy()
    answer(secret_question)
    for line in logs:
        assert secret_question not in line


def test_logs_never_contain_a_pii_value(spy, logs):
    spy()
    answer("my PAN is ABCDE1234F and my email is bob@example.com")
    blob = "\n".join(logs)
    assert "ABCDE1234F" not in blob
    assert "bob@example.com" not in blob


def test_a_pii_hit_logs_the_pattern_name_and_a_mask(spy, logs):
    spy()
    answer("my PAN is ABCDE1234F")
    blob = "\n".join(logs)
    assert "pattern=pan" in blob
    assert "mask=" in blob
    assert "ABCDE1234F" not in blob


def test_log_lines_are_structured(spy, logs):
    spy()
    answer("What is the exit load on HDFC Large Cap Fund?")
    blob = "\n".join(logs)
    for expected in ("stage=retrieve", "top_score=", "stage=generate", "tokens_out=",
                     "stage=verify", "status=", "stage=render", "citation="):
        assert expected in blob, expected


def test_the_generate_line_reports_latency_and_tokens(spy, logs):
    spy()
    answer("What is the exit load on HDFC Large Cap Fund?")
    blob = "\n".join(logs)
    assert "ms=12" in blob
    assert "tokens_out=9" in blob


def test_every_line_of_one_query_carries_the_same_hash(spy, logs):
    spy()
    answer("What is the exit load on HDFC Large Cap Fund?")
    hashes = {r.split("q=")[1].split()[0]
              for r in logs if "q=" in r}
    assert len(hashes) == 1, "lines from one query must be correlatable"


# --- small helpers --------------------------------------------------------

def test_summary_is_a_single_line(spy):
    spy()
    assert "\n" not in answer("What is the exit load on HDFC Large Cap Fund?").summary()


def test_reset_state_clears_the_cached_dates():
    pipeline._fetched_at_map()
    assert pipeline._FETCHED_AT is not None
    pipeline.reset_state()
    assert pipeline._FETCHED_AT is None


def test_ask_is_the_same_as_answer(spy):
    spy()
    question = "What is the exit load on HDFC Large Cap Fund?"
    assert pipeline.ask(question).status == answer(question).status


def test_scheme_link_points_at_the_named_scheme(spy):
    spy()
    assert pipeline.scheme_link("What is the exit load on HDFC Large Cap?") == URL
    assert pipeline.scheme_link("random unrelated text") is None
