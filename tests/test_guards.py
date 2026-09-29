"""Stage 7 guard tests.

Two failure directions matter and they are not symmetric:

- A **false positive** blocks a legitimate question, which is embarrassing but safe.
- A **false negative** ships a user's PAN to an LLM, which is a privacy incident.

So the PII tests are written as positive *and* negative pairs — every pattern has
to fire on a sample and stay quiet on a clean question — and the intent tests
check that the real example questions in `config.EXAMPLE_QUESTIONS` are not
misfiled, because those are the questions the demo will actually ask.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from rag import guards


# --- PII: every pattern must fire ----------------------------------------

PII_SAMPLES = {
    "pan": "my PAN is ABCDE1234F",
    "aadhaar": "aadhaar number 2345 6789 0123",
    "email": "reach me at ravi.kumar@example.com",
    "phone": "call me on 9876543210",
    "account_number": "my account number is 123456789012",
    "otp": "the otp is 482913",
}


@pytest.mark.parametrize("expected,sample", sorted(PII_SAMPLES.items()))
def test_each_pattern_fires(expected, sample):
    assert guards.check_pii(sample) == expected


@pytest.mark.parametrize("expected", sorted(PII_SAMPLES))
def test_each_pattern_has_a_positive_sample(expected):
    """Guards against a pattern being renamed in config and the test going stale."""
    assert expected in dict(config.PII_PATTERNS)


@pytest.mark.parametrize(
    "clean",
    [
        "What is the expense ratio of HDFC Large Cap Fund?",
        "What is the exit load on HDFC Small Cap Fund?",
        "What is the minimum SIP amount for HDFC Balanced Advantage Fund?",
        "What is the lock-in period for HDFC ELSS Tax Saver Fund?",
        "What is the riskometer level and benchmark of HDFC Equity Fund?",
        "How do I download a capital gains statement from my mutual fund account?",
    ],
)
def test_clean_questions_pass_the_pii_gate(clean):
    assert guards.check_pii(clean) is None


def test_aadhaar_leading_digit_rule_avoids_false_positives():
    """A 12-digit run starting 0 or 1 is not an Aadhaar number. Without the
    [2-9] rule this would refuse innocuous numbers."""
    assert guards.check_pii("reference number 012345678901") is None
    assert guards.check_pii("order id 112345678901") is None
    assert guards.check_pii("aadhaar 234567890123") == "aadhaar"


def test_account_number_needs_a_keyword():
    """A bare long digit run is usually a date or a figure, not an account."""
    assert guards.check_pii("the figure 123456789012 appeared") is None
    assert guards.check_pii("folio 123456789012") == "account_number"
    assert guards.check_pii("a/c no 123456789012") == "account_number"


def test_otp_needs_a_keyword():
    assert guards.check_pii("there were 482913 transactions") is None
    assert guards.check_pii("verification code 482913") == "otp"


def test_pan_is_case_insensitive():
    assert guards.check_pii("pan abcde1234f") == "pan"


def test_empty_query_is_not_pii():
    assert guards.check_pii("") is None
    assert guards.check_pii("   ") is None


def test_check_pii_never_returns_the_value():
    """The return value is a pattern name. A value here would end up in a log."""
    result = guards.check_pii("my PAN is ABCDE1234F")
    assert result == "pan"
    assert "ABCDE" not in result
    assert "1234" not in result


def test_mask_does_not_echo_the_value():
    masked = guards.mask("ABCDE1234F")
    assert "ABCDE" not in masked and "1234" not in masked
    assert set(masked) == {"*"}


def test_mask_is_constant_width():
    """A length-preserving mask still leaks the length of the value."""
    assert guards.mask("ABCDE1234F") == guards.mask("1")


# --- intent --------------------------------------------------------------

@pytest.mark.parametrize(
    "query",
    [
        "Should I buy HDFC Small Cap Fund?",
        "which fund should i choose",
        "is it good to invest in HDFC Large Cap?",
        "do you recommend HDFC ELSS Tax Saver Fund",
        "what is the best fund for me",
        "is HDFC Balanced Advantage Fund suitable for my age?",
        "help me choose a mutual fund",
        "Is now a good time to buy?",
        "shall i buy hdfc large cap",
        "which is better, HDFC Large Cap or HDFC Small Cap?",
        "is it worth it",
    ],
)
def test_advice_questions_are_caught(query):
    assert guards.classify_intent(query) == guards.ADVICE


@pytest.mark.parametrize(
    "query",
    [
        "What is the CAGR of HDFC Large Cap Fund?",
        "how much can i earn from HDFC Small Cap Fund?",
        "what are the returns of HDFC Equity Fund?",
        "which fund has the best performance?",
    ],
)
def test_returns_questions_are_caught(query):
    assert guards.classify_intent(query) == guards.RETURNS


def test_advice_wins_a_tie_against_returns():
    """"Should I buy X for its returns?" trips both; advice is the safer refusal."""
    assert guards.classify_intent("Should I buy HDFC Small Cap for good returns?") == (
        guards.ADVICE
    )


@pytest.mark.parametrize(
    "query",
    [
        "What is the expense ratio of HDFC Large Cap Fund?",
        "What is the exit load on HDFC Small Cap Fund?",
        "What is the minimum SIP amount for HDFC Balanced Advantage Fund?",
        "What is the lock-in period for HDFC ELSS Tax Saver Fund?",
        "What is the riskometer level and benchmark of HDFC Equity Fund?",
        "How do I download a capital gains statement?",
    ],
)
def test_factual_questions_are_not_misfiled(query):
    assert guards.classify_intent(query) == guards.FACTUAL


def test_timing_questions_count_as_advice():
    """"is now a good time to buy" is "should I buy" in other words, and the
    first cut of the rules missed it. A measured false negative, so it is pinned."""
    for query in (
        "Is now a good time to buy?",
        "is this a good time to invest",
        "shall i buy hdfc large cap",
        "which is better, HDFC Large Cap or HDFC Small Cap?",
    ):
        assert guards.classify_intent(query) == guards.ADVICE, query


def test_config_example_questions_all_pass_the_intent_gate():
    """These are the three questions the UI offers. If the guard misfiles one, the
    demo opens with a refusal."""
    for question in config.EXAMPLE_QUESTIONS:
        assert guards.classify_intent(question) == guards.FACTUAL, question


@pytest.mark.parametrize(
    "query",
    [
        "What is the expense ratio of Parag Parikh Flexi Cap Fund?",
        "exit load on Mirae Asset Large Cap Fund",
        "should I buy Kotak Flexicap Fund?",
        "tell me about ICICI Prudential Bluechip Fund",
    ],
)
def test_other_amcs_are_out_of_scope(query):
    assert guards.classify_intent(query) == guards.OUT_OF_SCOPE


def test_hdfc_is_never_out_of_scope():
    for scheme in ("HDFC Large Cap Fund", "HDFC Equity Fund", "HDFC ELSS Tax Saver Fund"):
        assert guards.classify_intent(f"What is the expense ratio of {scheme}?") == (
            guards.FACTUAL
        )


def test_detect_other_amc_returns_the_name():
    assert guards.detect_other_amc("Parag Parikh Flexi Cap") == "parag parikh"
    assert guards.detect_other_amc("HDFC Large Cap Fund") is None


def test_empty_query_is_factual():
    assert guards.classify_intent("") == guards.FACTUAL


# --- the LLM backend fails closed ----------------------------------------

def test_rules_backend_is_the_default():
    assert config.INTENT_BACKEND == "rules"


def test_llm_backend_falls_back_to_rules_when_generator_is_absent(monkeypatch):
    """P5 has now landed, so the module is importable. Simulate it being absent
    anyway: `from rag import generator` yields None, the attribute access raises,
    and the classifier must still work.

    Patched on the *package attribute*, not sys.modules, because `from rag import
    generator` resolves the attribute and would otherwise reach the real
    generator and make this a live network test.
    """
    import rag

    monkeypatch.setattr(rag, "generator", None, raising=False)
    monkeypatch.setattr(config, "INTENT_BACKEND", "llm")
    assert guards.classify_intent("What is the exit load on HDFC Small Cap Fund?") == (
        guards.FACTUAL
    )
    assert guards.classify_intent("Should I buy HDFC Small Cap Fund?") == guards.ADVICE


def test_llm_backend_falls_back_when_the_call_raises(monkeypatch):
    import rag
    import types

    module = types.ModuleType("rag.generator")

    def boom(prompt, *args, **kwargs):
        raise RuntimeError("backend unreachable")

    module.generate = boom
    monkeypatch.setattr(rag, "generator", module, raising=False)
    monkeypatch.setattr(config, "INTENT_BACKEND", "llm")
    assert guards.classify_intent("Should I buy HDFC Small Cap Fund?") == guards.ADVICE


def test_llm_backend_falls_back_on_an_unparseable_reply(monkeypatch):
    import rag
    import types

    module = types.ModuleType("rag.generator")
    module.generate = lambda prompt, *a, **k: "I think this is probably a question"
    monkeypatch.setattr(rag, "generator", module, raising=False)
    monkeypatch.setattr(config, "INTENT_BACKEND", "llm")
    assert guards.classify_intent("What is the exit load on HDFC Small Cap Fund?") == (
        guards.FACTUAL
    )


def test_llm_backend_uses_a_strictly_parsed_label(monkeypatch):
    import rag
    import types

    module = types.ModuleType("rag.generator")
    module.generate = lambda prompt, *a, **k: "ADVICE."
    monkeypatch.setattr(rag, "generator", module, raising=False)
    monkeypatch.setattr(config, "INTENT_BACKEND", "llm")
    assert guards.classify_intent("anything at all") == guards.ADVICE


def test_the_llm_backend_tests_never_call_the_network(monkeypatch):
    """Regression on the test suite itself: an earlier version patched
    `sys.modules`, which `from rag import generator` ignores, so the mock was
    bypassed and the suite was quietly calling the live Groq backend while
    claiming to be offline. Patching the package attribute is what actually
    intercepts the import.
    """
    import rag
    import types

    module = types.ModuleType("rag.generator")
    calls = []
    module.generate = lambda prompt, *a, **k: calls.append(prompt) or "FACTUAL"

    monkeypatch.setattr(rag, "generator", module, raising=False)
    monkeypatch.setattr(config, "INTENT_BACKEND", "llm")

    assert guards.classify_intent("some question") == guards.FACTUAL
    assert len(calls) == 1, "the real generator was called; the mock was bypassed"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("ADVICE", guards.ADVICE),
        ("advice", guards.ADVICE),
        ("  ADVICE.  ", guards.ADVICE),
        ("OUT_OF_SCOPE", guards.OUT_OF_SCOPE),
        ("FACTUAL\nsome trailing noise", guards.FACTUAL),
        ("", None),
        (None, None),
        ("MAYBE", None),
        ("ADVICE OR FACTUAL", None),
    ],
)
def test_label_parsing_is_strict(raw, expected):
    assert guards._parse_llm_label(raw) == expected


# --- screen() ------------------------------------------------------------

def test_screen_blocks_pii_before_looking_at_intent():
    """A question carrying a PAN is refused on PII even if it also asks for advice."""
    result = guards.screen("Should I buy HDFC Small Cap? My PAN is ABCDE1234F")
    assert result["blocked_by"] == "pii"
    assert result["pii_pattern"] == "pan"
    assert result["intent"] is None
    assert result["query_loggable"] is False


def test_screen_marks_the_query_unloggable_only_for_pii():
    assert guards.screen("What is the exit load on HDFC Small Cap?")["query_loggable"] is True
    assert guards.screen("Should I buy HDFC Small Cap?")["query_loggable"] is True


def test_screen_blocks_the_three_refusable_intents():
    assert guards.screen("Should I buy HDFC Small Cap?")["blocked_by"] == "intent"
    assert guards.screen("What is the CAGR of HDFC Large Cap?")["blocked_by"] == "intent"
    assert guards.screen("Expense ratio of Parag Parikh Flexi Cap?")["blocked_by"] == "intent"


def test_screen_passes_a_factual_question():
    result = guards.screen("What is the expense ratio of HDFC Large Cap Fund?")
    assert result["blocked_by"] is None
    assert result["intent"] == guards.FACTUAL


# --- the module's own invariants -----------------------------------------

def test_every_pattern_compiles():
    for name, pattern in guards.PII_PATTERNS:
        assert isinstance(pattern, re.Pattern), name
        assert config.PII_PATTERNS[name], name


def test_config_patterns_and_module_patterns_agree():
    assert [name for name, _ in guards.PII_PATTERNS] == list(config.PII_PATTERNS)


def test_intents_are_the_documented_four():
    assert guards.INTENTS == ("FACTUAL", "ADVICE", "RETURNS", "OUT_OF_SCOPE")
