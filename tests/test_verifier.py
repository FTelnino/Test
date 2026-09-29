"""Stage 7 verifier tests.

The interesting cases here are the near-misses. A verifier that only catches
obvious violations is theatre: the whole point is that it holds when the model is
being plausible. So the return-figure tests are built around the fact that
"0.5%" must survive, and the URL tests around the difference between an invented
link and a genuine one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from rag import verifier
from rag.prompts import refusal_text
from sources import load_sources


def _approved_url() -> str:
    """A real scheme-page URL, the case a citation should survive."""
    return next(s.url for s in load_sources() if s.url)


# --- sentence counting ----------------------------------------------------

def test_a_four_sentence_answer_is_truncated_to_three():
    answer = (
        "The exit load is 1%. It falls to 0.5% after 12 months. "
        "It becomes 0% after 18 months. This is a fourth sentence that must go."
    )
    final, status = verifier.verify(answer)
    assert status == verifier.ANSWERED
    assert len(verifier._sentence_split(final)) == 3
    assert "fourth sentence" not in final


def test_three_sentences_are_left_alone():
    answer = "One. Two. Three."
    final, status = verifier.verify(answer)
    assert final == answer
    assert status == verifier.ANSWERED


def test_a_single_sentence_survives():
    final, status = verifier.verify("The lock-in period is 3 years.")
    assert status == verifier.ANSWERED
    assert final == "The lock-in period is 3 years."


def test_decimals_do_not_count_as_sentence_ends():
    """A naive split on "." turns "0.5%" into two sentences."""
    answer = "The direct plan expense ratio is 0.53%. The minimum SIP is Rs 500."
    final, status = verifier.verify(answer)
    assert status == verifier.ANSWERED
    assert "0.53%" in final


# --- advice ---------------------------------------------------------------

@pytest.mark.parametrize(
    "answer",
    [
        "You should invest in HDFC Large Cap Fund for the long term.",
        "I would recommend HDFC Small Cap Fund for better growth.",
        "Consider investing in HDFC Equity Fund with a 5 year horizon.",
        "It is recommended to start with a large cap fund.",
        "My recommendation is to pick a single fund and stay invested.",
    ],
)
def test_advice_is_replaced(answer):
    final, status = verifier.verify(answer)
    assert status == verifier.REFUSED_ADVICE
    assert "you should" not in final.lower()
    assert config.EDUCATION_LINK in final


def test_a_factual_answer_is_not_caught_as_advice():
    final, status = verifier.verify(
        "The direct growth plan expense ratio is 1.03% as on 25 Sep 2026."
    )
    assert status == verifier.ANSWERED
    assert "1.03%" in final


def test_advice_phrase_triggers_in_the_fourth_sentence_even_though_it_is_truncated():
    """The violation is in a sentence that will not survive truncation. It must
    still be caught, which is why advice is checked before truncation."""
    answer = "One. Two. Three. You should invest in this fund now."
    final, status = verifier.verify(answer)
    assert status == verifier.REFUSED_ADVICE


# --- return figures -------------------------------------------------------

@pytest.mark.parametrize(
    "answer",
    [
        "The fund has delivered a CAGR of 18% over 5 years.",
        "Returns of 22% were reported in the last year.",
        "You can expect a gain of 15% annually.",
        "The performance was 12% on average.",
        "It gives 3x returns over a decade.",
    ],
)
def test_return_figures_are_replaced(answer):
    final, status = verifier.verify(answer)
    assert status == verifier.REFUSED_RETURNS
    assert "factsheet" in final.lower()


def test_an_expense_ratio_is_not_a_return():
    """The spec's named false-positive case."""
    for answer in (
        "The expense ratio is 0.5%.",
        "The direct plan expense ratio is 1.03% and the benchmark is Nifty 100.",
        "The exit load is 1%, reducing to 0.5% after 12 months.",
        "The fund's AUM is 50000 crore.",
    ):
        final, status = verifier.verify(answer)
        assert status == verifier.ANSWERED, answer
        assert "0.5%" in final or "1.03%" in final or "1%" in final or "50000" in final


def test_a_percentage_without_a_return_word_survives():
    final, status = verifier.verify("The equity allocation is 65% and debt is 35%.")
    assert status == verifier.ANSWERED
    assert "65%" in final


def test_cagr_is_caught_even_without_a_number():
    final, status = verifier.verify("The fund has a strong CAGR history.")
    assert status == verifier.REFUSED_RETURNS


# --- URLs -----------------------------------------------------------------

def test_an_invented_url_is_stripped():
    answer = f"The exit load is 1%. See {_approved_url()} for details."
    final, status = verifier.verify(answer)
    assert status == verifier.ANSWERED
    assert "example.com" not in final


def test_an_approved_url_is_kept():
    url = _approved_url()
    final, status = verifier.verify(f"The expense ratio is 1.03%. Source: {url}")
    assert status == verifier.ANSWERED
    assert url in final


def test_a_load_bearing_invented_url_downgrades_to_not_found():
    """Stripping the only content would leave an empty answer, so we admit we
    found nothing rather than render a blank sentence."""
    final, status = verifier.verify("See https://example.com/fund for details.")
    assert status == verifier.NOT_FOUND
    assert final == refusal_text(verifier.NOT_FOUND)


def test_a_bare_citation_sentence_does_not_discard_a_real_answer():
    """Regression: a bad URL in a link-only sentence used to downgrade the whole
    answer to NOT_FOUND, throwing away a perfectly good first sentence. The link
    goes; the fact stays."""
    answer = (
        "The expense ratio is 1.03% and the benchmark is Nifty 100. "
        "See https://example.com/x for more."
    )
    final, status = verifier.verify(answer)
    assert status == verifier.ANSWERED
    assert "1.03%" in final
    assert "Nifty 100" in final
    assert "example.com" not in final
    assert "See for more" not in final


def test_an_evidence_url_is_allowed():
    """A link the retrieval actually returned is not an invented citation."""
    url = "https://www.amfiindia.com/investor-educational-resources"

    class FakeEvidence:
        source_url = url

    final, status = verifier.verify(f"More detail is at {url}.", [FakeEvidence()])
    assert status == verifier.ANSWERED
    assert url in final


# --- empty ----------------------------------------------------------------

def test_an_empty_answer_becomes_not_found():
    for empty in ("", "   ", "\n\n"):
        final, status = verifier.verify(empty)
        assert status == verifier.NOT_FOUND
        assert final == refusal_text(verifier.NOT_FOUND)


def test_the_models_own_not_found_string_gets_the_not_found_status():
    """Regression from the P5 gate. The model correctly returned the exact
    NOT_FOUND text and the verifier reported ANSWERED, because it only knew how
    to produce NOT_FOUND from an empty answer. The UI switches on `status`, so
    that renders a refusal labelled as a success."""
    from rag.prompts import NOT_FOUND_TEXT

    final, status = verifier.verify(NOT_FOUND_TEXT)
    assert status == verifier.NOT_FOUND
    assert final == refusal_text(verifier.NOT_FOUND)

    # Also when the model adds its own lead-in around the string.
    _, status = verifier.verify(f"Sorry, {NOT_FOUND_TEXT}")
    assert status == verifier.NOT_FOUND


def test_an_answer_that_is_only_a_bad_url_becomes_not_found():
    final, status = verifier.verify("https://example.com/x")
    assert status == verifier.NOT_FOUND


def test_none_answer_does_not_crash():
    final, status = verifier.verify(None)
    assert status == verifier.NOT_FOUND


# --- the phase gate -------------------------------------------------------

def test_every_status_is_producible():
    """P6's done-when: each of the six statuses is reachable."""
    assert verifier.STATUSES == (
        "ANSWERED",
        "NOT_FOUND",
        "REFUSED_ADVICE",
        "REFUSED_RETURNS",
        "REFUSED_PII",
        "OUT_OF_SCOPE",
    )


def test_five_of_six_statuses_come_from_verify():
    """verify() cannot return REFUSED_PII or OUT_OF_SCOPE: those are decided
    upstream by the guards, before anything is generated. This test pins that
    division of labour so nobody later 'fixes' it by generating first."""
    produced = set()
    for answer in (
        "",
        "The expense ratio is 1.03%.",
        "You should invest in this fund.",
        "The fund has delivered a CAGR of 18%.",
        "See https://example.com/fund for details.",
    ):
        produced.add(verifier.verify(answer)[1])
    assert produced == {
        verifier.ANSWERED,
        verifier.NOT_FOUND,
        verifier.REFUSED_ADVICE,
        verifier.REFUSED_RETURNS,
    }


def test_refusal_text_covers_the_guarded_statuses():
    """The two statuses the guards produce must have copy to render."""
    for status in (verifier.REFUSED_PII, verifier.OUT_OF_SCOPE):
        assert refusal_text(status)


def test_pii_refusal_never_echoes_a_value():
    text = refusal_text("REFUSED_PII")
    assert "ABCDE" not in text and "1234" not in text
    assert "personal identifiers" in text


def test_refusal_text_rejects_an_unknown_status():
    with pytest.raises(KeyError):
        refusal_text("SOMETHING_ELSE")


def test_refusal_links_come_from_config():
    assert config.EDUCATION_LINK in refusal_text("REFUSED_ADVICE")
    assert "factsheet" in refusal_text("REFUSED_RETURNS").lower()


def test_max_answer_sentences_is_three():
    assert config.MAX_ANSWER_SENTENCES == 3


def test_approved_urls_come_from_sources():
    urls = verifier.approved_urls()
    assert urls
    for url in urls:
        assert url.startswith("https://")
