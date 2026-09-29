"""Stage 7 guards: two cheap, deterministic gates that run before retrieval.

Both gates exist because the worst outcomes in this product are not bad answers,
they are *confidently wrong* ones and *leaked personal data*:

- **PII gate (FR-12, NFR-6).** A PAN, Aadhaar number, phone number, email,
  account number, or OTP is refused before it can reach the LLM. `check_pii`
  returns the **pattern name**, never the matched value, so the refusal path
  cannot leak the value by accident — and there is nowhere in this module that
  formats a match into a log line or an exception message.
- **Intent gate (FR-9).** Labels a question `FACTUAL`, `ADVICE`, `RETURNS`, or
  `OUT_OF_SCOPE` so the pipeline can refuse instead of answering. The classifier
  is biased toward `ADVICE` on ties: an advice question answered with facts is a
  regulatory problem, whereas a factual question misfiled as advice is an
  unhelpful answer.

Both are rule-based by default and cost nothing. The LLM backend is opt-in and
**fails closed** to the rules, never the other way round (architecture.md §6.7).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

FACTUAL = "FACTUAL"
ADVICE = "ADVICE"
RETURNS = "RETURNS"
OUT_OF_SCOPE = "OUT_OF_SCOPE"

INTENTS = (FACTUAL, ADVICE, RETURNS, OUT_OF_SCOPE)

#: Tie-break order. A tie resolves to the earliest intent here, which is why
#: ADVICE comes before RETURNS: "should I buy X for its returns?" is refused for
#: giving advice, which is the safer of the two refusals.
_TIE_BREAK = (ADVICE, RETURNS, FACTUAL)

#: Fixed-width mask. Deliberately not derived from the input: a length-preserving
#: mask still leaks the length of a PAN or an account number, and this value is
#: only ever used for logs.
_MASK = "*" * 8


def _compile(patterns: Dict[str, str]) -> List[Tuple[str, "re.Pattern"]]:
    return [(name, re.compile(pattern, re.IGNORECASE)) for name, pattern in patterns.items()]


def _compile_rules(rules: Dict[str, int]) -> List[Tuple["re.Pattern", int]]:
    """Intent rules carry a weight, so they compile to (pattern, weight) pairs."""
    return [
        (re.compile(pattern, re.IGNORECASE), weight) for pattern, weight in rules.items()
    ]


#: (name, compiled_regex) pairs, as the spec requires.
PII_PATTERNS = _compile(config.PII_PATTERNS)

_COMPILED_INTENT_RULES = {
    intent: _compile_rules(rules) for intent, rules in config.INTENT_RULES.items()
}

_OTHER_AMCS = tuple(amc.lower() for amc in config.OTHER_AMCS)


def mask(value: str) -> str:
    """A loggable stand-in for a matched value. Never echoes the input."""
    return _MASK


def check_pii(query: str) -> Optional[str]:
    """The name of the PII pattern found in `query`, or None.

    Returns the name and not the value on purpose. Every caller — the pipeline,
    the logs, the refusal message — receives only the pattern name, so there is
    no code path that can accidentally echo a PAN back to the user or to disk.
    """
    if not query:
        return None
    for name, pattern in PII_PATTERNS:
        if pattern.search(query):
            return name
    return None


def detect_other_amc(query: str) -> Optional[str]:
    """A non-HDFC AMC named in the query, or None.

    Separate from the weighted rules because it is a fact about the corpus, not a
    judgement about the phrasing: if the question is about a fund we do not
    cover, no amount of relevant HDFC text answers it.
    """
    lowered = query.lower()
    for amc in _OTHER_AMCS:
        if amc in lowered:
            return amc
    return None


def _score_intents(query: str) -> Dict[str, int]:
    scores = {intent: 0 for intent in _COMPILED_INTENT_RULES}
    for intent, rules in _COMPILED_INTENT_RULES.items():
        for pattern, weight in rules:
            if pattern.search(query):
                scores[intent] += weight
    return scores


def classify_intent_rules(query: str) -> str:
    """The rule-based classifier. Deterministic, inspectable, zero latency."""
    if not query or not query.strip():
        return FACTUAL

    # Checked before the weighted rules: a question naming a fund we do not cover
    # is out of scope whatever else it says, and saying so is more useful than
    # refusing it as advice.
    if detect_other_amc(query):
        return OUT_OF_SCOPE

    scores = _score_intents(query)
    best = max(scores.values())
    if best == 0:
        return FACTUAL
    for intent in _TIE_BREAK:
        if intent in scores and scores[intent] == best:
            return intent
    return FACTUAL


def _parse_llm_label(raw: str) -> Optional[str]:
    """Strictly parse one label out of an LLM reply, or return None.

    Deliberately unforgiving. A reply we have to interpret is a reply we cannot
    trust, and the fallback is `ADVICE`, so anything ambiguous must return None
    rather than guess.
    """
    if not raw:
        return None
    token = raw.strip().strip(".").strip().upper().replace(" ", "_")
    token = token.split("\n")[0].strip()
    return token if token in INTENTS else None


def classify_intent_llm(query: str) -> str:
    """Ask the LLM for one label. Falls back to the rules on any failure.

    Fails closed in both directions: an unreachable LLM and an unparseable reply
    both land on `classify_intent_rules`, so a broken backend degrades the
    classifier's sophistication, never its safety.
    """
    try:
        from rag import generator  # noqa: WPS433 - optional, absent until P5
    except Exception:  # noqa: BLE001 - generator is P5; rules are the fallback
        return classify_intent_rules(query)

    prompt = (
        "Classify the user's question about mutual funds into exactly one label.\n"
        "Labels: FACTUAL, ADVICE, RETURNS, OUT_OF_SCOPE.\n"
        "FACTUAL = asks a documented fact. ADVICE = asks what to buy or whether to buy.\n"
        "RETURNS = asks about returns, CAGR, or profit. OUT_OF_SCOPE = about a fund "
        "provider we do not cover, or not about mutual funds.\n"
        "Reply with the label only, nothing else.\n\n"
        f"Question: {query}\n"
        "Label:"
    )
    try:
        raw = generator.generate(prompt)
    except Exception:  # noqa: BLE001 - any backend failure falls back
        return classify_intent_rules(query)

    label = _parse_llm_label(raw)
    if label is None:
        return classify_intent_rules(query)
    return label


def classify_intent(query: str) -> str:
    """The intent gate. Backend chosen by `config.INTENT_BACKEND`."""
    if config.INTENT_BACKEND == "llm":
        return classify_intent_llm(query)
    return classify_intent_rules(query)


def screen(query: str) -> dict:
    """Run both gates and report what happened, without deciding a response.

    The pipeline turns this into a status; this module stays free of user-facing
    copy, which lives in `prompts.py` (P5). Order matters: PII is checked first so
    a question carrying a PAN is refused before anything else looks at its wording.
    """
    pii = check_pii(query)
    if pii:
        return {"blocked_by": "pii", "pii_pattern": pii, "intent": None, "query_loggable": False}
    intent = classify_intent(query)
    return {
        "blocked_by": "intent" if intent in (ADVICE, RETURNS, OUT_OF_SCOPE) else None,
        "pii_pattern": None,
        "intent": intent,
        "query_loggable": True,
    }
