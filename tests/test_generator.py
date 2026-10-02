"""Stage 6 prompt + generator tests.

No network in these tests. Generation is verified against the live backend by
`scripts/probe_generation.py` (see `reports/generation_gate.md`); what is pinned
here is the part that must not drift silently — the prompt contract, the key
handling, and the two failure modes measured on the real backend.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from rag import generator
from rag.prompts import (
    EXAMPLE_QUESTIONS,
    NOT_FOUND_TEXT,
    build_context_prompt,
    system_prompt,
)


class FakeChunk:
    def __init__(self, scheme="HDFC Large Cap Fund", section="Fees > Exit load",
                 url="https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
                 text="The exit load is 1% within the first year."):
        self.scheme = scheme
        self.section = section
        self.source_url = url
        self.text = text


class FakeHit:
    def __init__(self, chunk=None, score=0.71):
        self.chunk = chunk or FakeChunk()
        self.score = score


# --- the prompt contract --------------------------------------------------

def test_system_prompt_states_every_constraint():
    prompt = system_prompt()
    for phrase in (
        "only facts", "exactly", "sentence", "advice", "returns", "URL", "CONTEXT",
    ):
        assert phrase.lower() in prompt.lower(), phrase


def test_system_prompt_carries_the_configured_sentence_limit():
    assert f"at most {config.MAX_ANSWER_SENTENCES} sentences" in system_prompt()


def test_system_prompt_names_the_exact_not_found_string():
    assert NOT_FOUND_TEXT in system_prompt()


def test_context_prompt_labels_every_chunk():
    prompt = build_context_prompt("What is the exit load?", [FakeHit()])
    assert "[HDFC Large Cap Fund | Fees > Exit load | https://groww.in/" in prompt
    assert "The exit load is 1% within the first year." in prompt
    assert "What is the exit load?" in prompt


def test_context_prompt_makes_inventing_a_url_pointless():
    """Every URL in the prompt is already an approved one, so a URL the model
    writes that is not in a header is by definition invented."""
    from rag import verifier

    prompt = build_context_prompt("q", [FakeHit()])
    urls = verifier._URL_RE.findall(prompt)
    assert urls
    approved = verifier.approved_urls()
    for url in urls:
        assert verifier._is_approved(url, approved), url


def test_context_prompt_with_no_evidence_asks_for_the_exact_string():
    prompt = build_context_prompt("What is the ISIN?", [])
    assert NOT_FOUND_TEXT in prompt
    assert "CONTEXT: (none)" in prompt


def test_context_prompt_skips_empty_chunks():
    prompt = build_context_prompt("q", [FakeHit(chunk=FakeChunk(text=""))])
    assert "CONTEXT: (none)" in prompt


def test_context_prompt_accepts_bare_chunks_and_hits():
    """Evidence arrives as RetrievedChunk in the pipeline and as bare chunks in
    some tests; both must work."""
    a = build_context_prompt("q", [FakeHit()])
    b = build_context_prompt("q", [FakeChunk()])
    assert a == b


def test_example_questions_are_the_three_approved_ones():
    assert len(EXAMPLE_QUESTIONS) == 3
    assert EXAMPLE_QUESTIONS == config.EXAMPLE_QUESTIONS


def test_every_example_question_retrieves_evidence():
    """The three chips must not open on a refusal."""
    from rag.retriever import search

    for question in EXAMPLE_QUESTIONS:
        assert search(question), question


def test_every_scheme_in_the_corpus_is_retrievable_by_name():
    """A question naming any ingested scheme must retrieve evidence.

    Broader than `test_every_example_question_retrieves_evidence`, which only
    covers the two schemes the chips happen to name. That narrowness is exactly why
    the P17 bug survived: ten of fifteen schemes were invisible to the retriever
    and none of the chips mentioned one. Keep both — the chips are what the user
    sees on first paint, this is the corpus-wide guarantee behind them.
    """
    from rag.retriever import search
    from sources import load_sources

    for source in load_sources():
        if source.scope == "general":
            continue
        assert search(f"What is the exit load on {source.scheme}?"), source.scheme


# --- backend configuration ------------------------------------------------

def test_backend_is_groq():
    assert config.LLM_BACKEND == "groq"
    assert "gpt-oss" in config.GROQ_MODEL


def test_temperature_is_zero():
    """Same question, same answer. Anything else makes the gate unreproducible."""
    assert config.LLM_TEMPERATURE == 0


def test_max_tokens_fits_three_sentences():
    assert config.LLM_MAX_TOKENS == 160


def test_reasoning_effort_is_set():
    """Measured: without it the model spends all 160 tokens reasoning and the
    answer arrives truncated."""
    assert config.GROQ_REASONING_EFFORT == "low"


def test_ollama_stays_available_as_an_offline_alternative():
    assert config.OLLAMA_MODEL == "llama3.2"
    assert config.OLLAMA_BASE_URL.startswith("http://localhost")


def test_backend_description_names_the_model():
    assert "gpt-oss-20b" in generator.backend()


# --- key handling ---------------------------------------------------------

def test_a_missing_key_raises_a_clear_error(monkeypatch):
    monkeypatch.delenv(config.GROQ_KEY_ENV, raising=False)
    with pytest.raises(generator.LLMUnavailable) as exc:
        generator.generate("hi")
    assert config.GROQ_KEY_ENV in str(exc.value)


def test_no_module_attribute_holds_the_key():
    """The key is read from the environment at call time, so it is not a module
    attribute that anything could log by accident."""
    for name in dir(generator):
        assert "key" not in name.lower() or name in {
            "GROQ_KEY_ENV",
        }
    assert config.GROQ_KEY_ENV == "GROQ_API_KEY"


def test_unknown_backend_raises():
    original = config.LLM_BACKEND
    try:
        config.LLM_BACKEND = "not-a-backend"
        with pytest.raises(generator.LLMUnavailable):
            generator.generate("hi")
    finally:
        config.LLM_BACKEND = original


def test_an_http_error_never_includes_the_key(monkeypatch):
    secret = "gsk_fake_secret_value_for_testing"
    monkeypatch.setenv(config.GROQ_KEY_ENV, secret)
    config.LLM_MAX_ATTEMPTS = 1

    class Boom:
        status_code = 500
        headers = {}
        text = "upstream exploded"

        def json(self):
            return {}

    import requests

    def fake_post(*args, **kwargs):
        return Boom()

    monkeypatch.setattr(requests, "post", fake_post)
    with pytest.raises(generator.LLMUnavailable) as exc:
        generator.generate("hi")
    assert secret not in str(exc.value)


def test_a_rate_limit_is_retried_and_never_silently_changes_the_answer(monkeypatch):
    """A 429 must be waited out, not swallowed and not turned into a fallback
    answer. The retry resends the identical payload, so the answer cannot change."""
    monkeypatch.setenv(config.GROQ_KEY_ENV, "gsk_fake")
    monkeypatch.setattr(generator, "LLMUnavailable", generator.LLMUnavailable)
    monkeypatch.setattr(config, "LLM_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(config, "LLM_BACKOFF_SECONDS", 0.0)

    sent = []

    class Resp:
        def __init__(self, status):
            self.status_code = status
            self.headers = {}
            self.text = "rate limited"

        def json(self):
            return {"choices": [{"message": {"content": "The exit load is 1%."}}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.append(json)
        return Resp(429 if len(sent) < 3 else 200)

    import requests

    monkeypatch.setattr(requests, "post", fake_post)
    assert generator.generate("hi") == "The exit load is 1%."
    assert len(sent) == 3
    # Every attempt sent an identical payload: no resampling.
    assert sent[0] == sent[1] == sent[2]
    assert sent[0]["temperature"] == 0


# --- text normalisation ---------------------------------------------------

def test_unicode_spaces_are_folded():
    """gpt-oss-120b emitted U+202F inside "1 year", which would break the
    verifier's ASCII-space regexes."""
    out = generator._normalise("locked for 3\u202fyears and 3\u00a0years")
    assert "\u202f" not in out
    assert "\u00a0" not in out
    assert "3 years and 3 years" in out


def test_normalisation_strips_edges():
    assert generator._normalise("  hello  ") == "hello"


def test_only_content_is_ever_returned(monkeypatch):
    """The reasoning trace is the model's scratchpad. Returning it would put
    "Need to answer using only context" on screen."""
    monkeypatch.setenv(config.GROQ_KEY_ENV, "gsk_fake")

    class Resp:
        status_code = 200
        headers = {}
        text = ""

        def json(self):
            return {"choices": [{"message": {
                "content": "The lock-in period is 3 years.",
                "reasoning": "Need to answer using only the context provided.",
            }}]}

    import requests

    monkeypatch.setattr(requests, "post", lambda *a, **k: Resp())
    out = generator.generate("hi")
    assert out == "The lock-in period is 3 years."
    assert "Need to" not in out
    assert "reasoning" not in out.lower()


def test_a_missing_content_field_raises():
    monkeypatch_key = {"GROQ_API_KEY": "gsk_fake"}
    import os

    os.environ.update(monkeypatch_key)
    try:
        class Resp:
            status_code = 200
            headers = {}
            text = ""

            def json(self):
                return {"choices": [{"message": {"reasoning": "only reasoning"}}]}

        import requests

        original = requests.post
        requests.post = lambda *a, **k: Resp()
        try:
            with pytest.raises(generator.LLMUnavailable):
                generator.generate("hi")
        finally:
            requests.post = original
    finally:
        for key in monkeypatch_key:
            os.environ.pop(key, None)


def test_llm_unavailable_is_not_swallowed():
    """The spec is explicit: a backend error raises, it does not degrade to a
    canned answer. A fabricated official-looking answer is worse than an error."""
    assert issubclass(generator.LLMUnavailable, RuntimeError)
