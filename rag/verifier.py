"""Stage 7 verifier: the backstop on the generated string (architecture.md §6.8).

The prompt asks the model to stay inside the constraints. The verifier *checks*
that it did, so the guarantee does not depend on the model cooperating. It runs on
the finished text with cheap regex and no second LLM call, because the failure
this defends against — a fabricated URL, a projected return — is exactly the kind
of thing a model will do cheerfully when asked politely.

Order is deliberate. Advice and return figures are checked **before**
truncation, because a violation in sentence four must be caught even though only
the first three sentences survive; and URL stripping runs last, on the text that
will actually be shown.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from rag.prompts import NOT_FOUND_TEXT, refusal_text
from sources import load_sources

ANSWERED = "ANSWERED"
NOT_FOUND = "NOT_FOUND"
REFUSED_ADVICE = "REFUSED_ADVICE"
REFUSED_RETURNS = "REFUSED_RETURNS"
REFUSED_PII = "REFUSED_PII"
OUT_OF_SCOPE = "OUT_OF_SCOPE"

STATUSES = (
    ANSWERED,
    NOT_FOUND,
    REFUSED_ADVICE,
    REFUSED_RETURNS,
    REFUSED_PII,
    OUT_OF_SCOPE,
)

# Advice phrasing. Kept to unambiguous second-person steering; "is HDFC Large Cap
# a good fund" is a ranking question but reads as a fact request, and refusing it
# would make the demo feel broken.
_ADVICE_RE = re.compile(
    r"\b(?:you\s+should|you\s+can\s+consider|i\s+(?:would\s+)?recommend|"
    r"it\s+is\s+(?:recommended|advised)\s+to|we\s+recommend|"
    r"my\s+recommendation\s+is|consider\s+investing\s+in)\b",
    re.IGNORECASE,
)

# A return figure is a *percentage attached to a return word*, or CAGR, or an "x
# returns" multiple. The adjacency requirement is what stops "the expense ratio is
# 0.5%" from tripping this, which the spec calls out explicitly.
_RETURN_RE = re.compile(
    # "3x returns" needs the number inside the pattern: a leading \b before "x"
    # can never match there, because the digit before it is also a word character.
    r"\bcagr\b"
    r"|\b[0-9]+(?:\.[0-9]+)?\s*x\s*returns?\b"
    r"|\b(?:returns?|performance|profit|gain|yield|appreciation|upside)\b"
    r"[^.!?]{0,40}?\b[0-9]+(?:\.[0-9]+)?\s*%",
    re.IGNORECASE,
)

_URL_RE = re.compile(r"https?://[^\s)\]\"'>]+")


def approved_urls() -> set:
    """Every URL the corpus is allowed to cite.

    Built from `load_sources()` rather than a literal list, so adding a scheme or
    a regulator page to `sources.py` widens the whitelist automatically. A
    hand-maintained copy here would drift and start stripping real citations.
    """
    return {source.url.rstrip("/") for source in load_sources() if source.url}


def _sentence_split(text: str) -> List[str]:
    """Split into sentences, keeping the terminator on each.

    A plain `split(".")` mangles decimals, which matters here: "0.5%" and "3.2"
    would become two sentences and the fragment "5% would be..." would read as
    advice-ish noise. Splitting on a terminator followed by whitespace keeps
    numbers intact.
    """
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p.strip()]


def _truncate(text: str, limit: int) -> str:
    sentences = _sentence_split(text)
    return " ".join(sentences[:limit]).strip()


def _evidence_urls(evidence: Iterable) -> set:
    urls = set()
    for item in evidence or []:
        url = getattr(item, "source_url", None) or getattr(item, "url", None)
        if url:
            urls.add(url.rstrip("/"))
    return urls


def _is_approved(url: str, allowed: set) -> bool:
    return url.rstrip(".,;:/").rstrip("/") in allowed


#: Phrases that exist only to introduce a link. They carry no information, so once
#: the link is gone they leave a sentence that says nothing.
_REFERENCE_RE = re.compile(
    r"\b(?:see|refer(?:ring)?\s+to|check|read|visit|at|on|source|src|link|"
    r"available\s+(?:at|on)|for\s+(?:more\s+)?(?:details?|info|information|"
    r"reference)?|more)\b",
    re.IGNORECASE,
)

#: Below this many word characters a sentence is a stub, not an answer.
_MIN_RESIDUE_CHARS = 12


def _strip_unapproved_urls(text: str, allowed: set) -> Tuple[str, bool]:
    """Remove URLs not backed by the corpus, and any sentence left meaningless.

    Returns the cleaned text and whether anything was removed. A sentence that
    existed only to host an invented link is dropped rather than rendered as a
    stub like "See for more."; a sentence with real content keeps it and loses
    just the link. This distinction is the difference between losing a citation
    and losing an answer.
    """
    removed_any = False
    kept: List[str] = []

    for sentence in _sentence_split(text):
        urls = _URL_RE.findall(sentence)
        if not any(not _is_approved(url, allowed) for url in urls):
            kept.append(sentence)
            continue

        removed_any = True
        without_urls = _URL_RE.sub(" ", sentence)
        cleaned = _REFERENCE_RE.sub(" ", without_urls)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,;:-")
        residue = re.sub(r"[^\w]", "", cleaned)
        if len(residue) >= _MIN_RESIDUE_CHARS:
            kept.append(cleaned)

    return " ".join(part for part in kept if part).strip(), removed_any


def verify(answer: str, evidence: Optional[Sequence] = None) -> Tuple[str, str]:
    """Check a generated answer and return `(final_answer, status)`.

    `evidence` is the retrieval result the answer was built from; its URLs widen
    the set of citable links beyond the static scheme list, so a citation the
    pipeline genuinely used is not stripped as invented.
    """
    evidence = evidence or []
    text = (answer or "").strip()

    if not text:
        return refusal_text(NOT_FOUND), NOT_FOUND

    # The model saying "I couldn't find that in the official sources" is the
    # intended NOT_FOUND behaviour, so it must carry the NOT_FOUND status and not
    # slip through as ANSWERED. `status` is what the UI switches on and what the
    # eval script asserts; returning the refusal text under ANSWERED would render
    # a contradiction and fail a golden-set status check for the right answer.
    if NOT_FOUND_TEXT in text:
        return refusal_text(NOT_FOUND), NOT_FOUND

    # Advice and returns are checked on the full text, before truncation, so a
    # violation in a later sentence cannot hide behind the 3-sentence cap.
    if _ADVICE_RE.search(text):
        return refusal_text(REFUSED_ADVICE), REFUSED_ADVICE

    if _RETURN_RE.search(text):
        return refusal_text(REFUSED_RETURNS), REFUSED_RETURNS

    allowed = approved_urls() | _evidence_urls(evidence)
    final, removed = _strip_unapproved_urls(text, allowed)

    # The answer was *about* an invented link, so there is nothing left to say.
    if removed and not final:
        return refusal_text(NOT_FOUND), NOT_FOUND

    final = _truncate(final, config.MAX_ANSWER_SENTENCES)

    if not final:
        return refusal_text(NOT_FOUND), NOT_FOUND

    return final, ANSWERED


def producible_statuses() -> List[str]:
    """Statuses this module can return, used by the phase gate."""
    return list(STATUSES)
