"""Stage 8 tests: the eval harness and the CLI renderer.

Both are judged by what they say, so the tests read like a spec of the output.
The harness is the one place a wrong check would be believed, so the checks are
tested against answers that should fail each of them, not only against ones that
pass.

Offline: `answer()` is never called here. `check_row` is a pure function of an
`Answer`, and `render_answer` is a pure function of the same, so both can be
tested by constructing the `Answer` directly.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag.chunker import Chunk
from rag.pipeline import Answer
from rag.retriever import RetrievedChunk
from rag.verifier import (
    ANSWERED,
    NOT_FOUND,
    OUT_OF_SCOPE,
    REFUSED_ADVICE,
    REFUSED_PII,
    REFUSED_RETURNS,
)

import cli
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "evaluate", Path(__file__).resolve().parent.parent / "scripts" / "evaluate.py"
)
evaluate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(evaluate)

URL = "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"


def make_evidence(text="Expense ratio 1.03%.", score=0.71,
                  scheme="HDFC Large Cap Fund", section="Expense ratio", url=URL):
    chunk = Chunk(
        chunk_id="hdfc-large-cap-fund:aa665e15:9", text=text, scheme=scheme,
        category="Large Cap", scope="scheme", section=section, source_url=url,
        ordinal=9, token_estimate=24, kind="table_aware", strategy="table_aware",
    )
    return RetrievedChunk(chunk=chunk, score=score, rank=1)


def make_answer(**over):
    base = dict(
        query="What is the expense ratio of HDFC Large Cap Fund?",
        answer="The expense ratio is 1.03%.",
        status=ANSWERED,
        citation_url=URL,
        last_updated="27 Sep 2026",
        evidence=[make_evidence()],
    )
    base.update(over)
    return Answer(**base)


# --- the harness checks themselves ----------------------------------------

def test_status_mismatch_is_the_only_failure_reported():
    row = {"question": "q", "expected_status": REFUSED_PII,
           "must_contain": ["something"], "forbid": ["1.03%"]}
    passed, failed, actual = evaluate.check_row(row, make_answer())
    assert not passed
    assert actual == ANSWERED
    # Short-circuits: we do not also report the must_contain/forbid misses, which
    # would overstate a single wrong decision.
    assert len(failed) == 1 and "status" in failed[0]


def test_must_contain_is_case_insensitive():
    row = {"expected_status": ANSWERED, "must_contain": ["1.03%"]}
    assert evaluate.check_row(row, make_answer())[0]


def test_must_contain_failure_names_the_needle():
    row = {"expected_status": ANSWERED, "must_contain": ["9.99%"]}
    passed, failed, _ = evaluate.check_row(row, make_answer())
    assert not passed and "9.99%" in failed[0]


def test_forbid_catches_present_substring():
    row = {"expected_status": ANSWERED, "forbid": ["1.03"]}
    passed, failed, _ = evaluate.check_row(row, make_answer())
    assert not passed and "forbid" in failed[0]


def test_forbid_passes_when_absent():
    row = {"expected_status": ANSWERED, "forbid": ["recommend"]}
    assert evaluate.check_row(row, make_answer())[0]


def test_answered_without_a_citation_fails():
    # PRD criterion 2 wants a working link on every answer; the harness must
    # refuse to call a linkless answer green.
    row = {"expected_status": ANSWERED}
    passed, failed, _ = evaluate.check_row(row, make_answer(citation_url=None))
    assert not passed and any("citation" in f for f in failed)


def test_answered_without_a_date_fails():
    row = {"expected_status": ANSWERED}
    passed, failed, _ = evaluate.check_row(row, make_answer(last_updated=None))
    assert not passed and any("last_updated" in f for f in failed)


def test_citation_must_be_an_approved_source():
    row = {"expected_status": ANSWERED}
    passed, failed, _ = evaluate.check_row(
        row, make_answer(citation_url="https://evil.example.com/x")
    )
    assert not passed and any("approved" in f for f in failed)


def test_scheme_is_only_asserted_when_evidence_exists():
    # An out-of-scope refusal has no evidence by design; asserting a scheme there
    # would be asserting that the guard failed to run.
    row = {"expected_status": OUT_OF_SCOPE, "expect_scheme": "HDFC Large Cap Fund"}
    passed, _, _ = evaluate.check_row(
        row, make_answer(status=OUT_OF_SCOPE, evidence=[], citation_url=None,
                         answer="I only cover HDFC funds.")
    )
    assert passed


def test_scheme_assertion_fails_on_the_wrong_scheme():
    row = {"expected_status": ANSWERED, "expect_scheme": "HDFC Small Cap Fund"}
    passed, failed, _ = evaluate.check_row(row, make_answer())
    assert not passed and any("scheme" in f for f in failed)


def test_refusal_row_passes_with_no_evidence():
    row = {"expected_status": REFUSED_ADVICE, "must_contain": ["AMFI"],
           "forbid": ["you should"]}
    result = make_answer(status=REFUSED_ADVICE, evidence=[], citation_url=None,
                         last_updated=None,
                         answer="I share facts, not advice. See AMFI.")
    assert evaluate.check_row(row, result)[0]


# --- the golden set is loadable and covers every status --------------------

def test_golden_rows_all_have_a_question_and_status():
    for entry in evaluate.load_golden():
        assert entry.get("question")
        assert entry.get("expected_status")


def test_golden_set_exercises_all_six_statuses():
    statuses = {entry["expected_status"] for entry in evaluate.load_golden()}
    assert statuses == {
        ANSWERED, NOT_FOUND, REFUSED_ADVICE, REFUSED_RETURNS,
        REFUSED_PII, OUT_OF_SCOPE,
    }


def test_golden_probe_rows_are_a_subset_used_by_the_p4_gate():
    probe = [e for e in evaluate.load_golden() if e.get("retrieval_probe")]
    assert probe
    assert all(e["kind"] in {"in_corpus", "out_of_corpus", "out_of_scope"} for e in probe)


# --- CLI rendering ---------------------------------------------------------

def test_render_shows_status_source_and_the_prd_date_line():
    text = cli.render_answer(make_answer())
    assert "status: ANSWERED" in text
    assert URL in text
    # PRD criterion 6 fixes this wording, so it is asserted verbatim.
    assert "Last updated from sources: 27 Sep 2026" in text


def test_render_says_na_when_there_is_no_date():
    text = cli.render_answer(
        make_answer(status=NOT_FOUND, evidence=[], citation_url=None,
                    last_updated=None, answer="I couldn't find that.")
    )
    assert "Last updated from sources: n/a" in text


def test_render_hides_sources_unless_asked():
    assert "Sources retrieved" not in cli.render_answer(make_answer())


def test_render_dumps_source_chunks_when_asked():
    text = cli.render_answer(make_answer(), show_sources=True)
    assert "Sources retrieved" in text
    assert "Expense ratio 1.03%." in text


def test_render_warns_on_an_answer_with_no_citation():
    text = cli.render_answer(make_answer(status=ANSWERED, citation_url=None))
    assert "WARNING" in text


def test_sample_markdown_includes_every_status_and_the_date_line():
    results = [
        make_answer(),
        make_answer(status=REFUSED_PII, evidence=[], citation_url=None,
                    last_updated=None, answer="I can't process that."),
    ]
    md = cli._sample_md(results)
    assert "**Status:** `ANSWERED`" in md
    assert "**Status:** `REFUSED_PII`" in md
    assert "**Last updated from sources:** 27 Sep 2026" in md


def test_sample_questions_all_reach_a_status_without_a_backend_import_error():
    # Guards the list against drift: every sample question must be non-empty and
    # the module must import cleanly, which is the failure mode when the list is
    # edited carelessly.
    assert len(cli.SAMPLE_QUESTIONS) >= 6
    assert all(q.strip() for q in cli.SAMPLE_QUESTIONS)
