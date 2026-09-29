"""Stage 8 pipeline: one user question in, one renderable `Answer` out.

The order of the checks below is the design, and it is load-bearing. Each step can
only be reached by passing the previous one, and each is cheaper than the next:

    check_pii         -> REFUSED_PII          (no retrieval, no LLM)
    classify_intent   -> ADVICE/RETURNS/OOS  (no retrieval, no LLM)
    retriever.search  -> NOT_FOUND           (no LLM)
    generator.generate                        (the only LLM call)
    verifier.verify                           (enforce, do not request)
    render

Consequences that are worth stating, because they are the reason the order is not
negotiable: a PAN is refused before it is embedded or logged, an advice question
costs zero tokens and cannot be flaky on demo day, and a question with no supporting
chunk never reaches a model that might improvise something.

Refusals are template strings from `prompts.py`, so they are instant, free, and
cannot be wrong in the way a generated one can.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from rag import generator, guards, verifier
from rag.loader import FetchError
from rag.prompts import NOT_FOUND_TEXT, refusal_text
from rag.retriever import RetrievedChunk, detect_scheme, search, scheme_url
from rag.verifier import (
    ANSWERED,
    NOT_FOUND,
    OUT_OF_SCOPE,
    REFUSED_ADVICE,
    REFUSED_PII,
    REFUSED_RETURNS,
)

logger = logging.getLogger("rag.pipeline")


# --- Answer ---------------------------------------------------------------


@dataclass
class Answer:
    """The complete result of one user turn.

    `status` is the field everything else switches on: the UI branches on it, the
    eval script asserts it, and the log records it. Keeping it explicit is what
    makes refusal behaviour testable rather than eyeballed.
    """

    query: str
    answer: str
    status: str
    citation_url: Optional[str] = None
    last_updated: str = ""
    evidence: List[RetrievedChunk] = field(default_factory=list)

    @property
    def refused(self) -> bool:
        return self.status != ANSWERED

    def summary(self) -> str:
        """One line for a CLI, and the shape the gate prints."""
        return f"{self.status} | {self.answer}"


# --- logging --------------------------------------------------------------


def setup_logging() -> logging.Logger:
    """File + optional stderr, created once. Never logs the query text.

    The query is in the UI transcript, so a log line does not need it. To correlate
    lines from one question without storing what was asked, each line carries a short
    hash of the query. A hash is enough to group a query's six lines and is not
    reversible into text, which full query logging would be.
    """
    if logger.handlers:
        return logger

    config.ensure_dirs()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(message)s")

    file_handler = logging.FileHandler(config.LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if config.LOG_TO_STDERR:
        import sys as _sys

        stderr_handler = logging.StreamHandler(_sys.stderr)
        stderr_handler.setFormatter(formatter)
        logger.addHandler(stderr_handler)

    logger.propagate = False
    return logger


#: Start time of the current turn, for the total-latency field on the render line.
_STARTED = [0.0]


def _query_hash(query: str) -> str:
    """8 hex chars of sha256. Correlation, not a stored question."""
    return hashlib.sha256((query or "").encode("utf-8")).hexdigest()[:8]


# --- fetched_at lookup ----------------------------------------------------

_FETCHED_AT: Optional[Dict[str, str]] = None


def _fetched_at_map() -> Dict[str, str]:
    """`source_url -> fetched_at`, read once from the ingest report.

    `Chunk` carries no timestamp and the raw chunk table has no such column, so the
    ingest report is the only place the mapping survives. Read lazily and cached:
    it cannot change during a process, and re-reading it per question would be a
    disk hit on the hot path for nothing.
    """
    global _FETCHED_AT
    if _FETCHED_AT is not None:
        return _FETCHED_AT

    mapping: Dict[str, str] = {}
    try:
        report = json.loads(config.INGEST_REPORT.read_text(encoding="utf-8"))
        for entry in report.get("sources", []):
            url, fetched = entry.get("url"), entry.get("fetched_at")
            if url and fetched:
                mapping[url] = fetched
    except (OSError, ValueError, AttributeError) as exc:
        # Not fatal: last_updated degrades to empty rather than the pipeline
        # refusing to answer. The answer is still traceable by citation_url.
        logger.info(f"stage=render warn=ingest_report_unreadable detail={type(exc).__name__}")

    _FETCHED_AT = mapping
    return mapping


def _last_updated(evidence: Sequence[RetrievedChunk]) -> str:
    """The newest fetch date across the evidence, formatted for display.

    ISO dates sort lexicographically, so a max() over the raw strings is the newest
    date; formatting happens once afterwards.
    """
    mapping = _fetched_at_map()
    dates = [mapping[hit.chunk.source_url] for hit in evidence
             if hit.chunk.source_url in mapping]
    if not dates:
        return ""
    try:
        newest = datetime.strptime(max(dates), "%Y-%m-%d")
    except ValueError:
        return max(dates)
    return newest.strftime(config.LAST_UPDATED_FORMAT)


# --- render ---------------------------------------------------------------


def render(query: str, answer_text: str, status: str,
           evidence: Optional[Sequence[RetrievedChunk]] = None) -> Answer:
    """Assemble the final `Answer`.

    `citation_url` is the rank-1 evidence chunk's source URL, and `None` when there
    is no evidence. That single rule covers both cases the spec cares about: a
    pre-retrieval refusal has none and cites nothing, and an LLM-outage answer does
    have evidence and should still point the user at the real page so they can read
    the facts themselves.
    """
    evidence = list(evidence or [])
    citation = evidence[0].chunk.source_url if evidence else None
    updated = _last_updated(evidence)

    result = Answer(
        query=query,
        answer=answer_text,
        status=status,
        citation_url=citation,
        last_updated=updated,
        evidence=evidence,
    )
    logger.info(
        f"q={_query_hash(query)} stage=render status={status} "
        f"citation={citation or 'none'} "
        f"last_updated={updated or 'none'} n={len(evidence)} ms={(time.time() - _STARTED[0]) * 1000:.0f}"
    )
    return result


# --- the pipeline ---------------------------------------------------------


def _outage(query: str, evidence: Optional[Sequence[RetrievedChunk]] = None) -> Answer:
    """The single non-fabricated response for a dependency failure.

    Says the assistant is unavailable rather than that the corpus had no answer,
    because those are different claims and conflating them hides a failure behind a
    polite answer. Keeps any evidence retrieved, so the citation still points at the
    real page; when there is none, falls back to the page for the scheme the question
    named. Leaves `citation_url` as `None` when no scheme was named, because guessing
    a page is exactly what this product is built not to do.
    """
    result = render(query, config.LLM_UNAVAILABLE_TEXT, NOT_FOUND, evidence)
    if not result.citation_url:
        result.citation_url = scheme_url(detect_scheme(query) or None)
    return result


def answer(query: str) -> Answer:
    """Answer one question. Never raises for an expected condition.

    The only exception that escapes is a bug; refusals, low confidence, and a dead
    backend are all values, because a UI needs a row to render in every one of them.
    """
    setup_logging()
    qhash = _query_hash(query)
    # A one-slot list so render() can log total latency for the turn; an int would
    # be a closure variable and would need `nonlocal` to be readable here.
    _STARTED[0] = time.time()

    # 1. PII. First, before anything touches the string. The pattern name and a mask
    #    are the only things logged; the value never leaves check_pii.
    pii = guards.check_pii(query)
    if pii:
        logger.info(
            f"q={qhash} stage=guard status=REFUSED_PII pattern={pii} "
            f"mask={guards.mask(query)} ms=0"
        )
        return render(query, refusal_text(REFUSED_PII), REFUSED_PII)

    # 2. Intent. Also before retrieval: a refusal here costs no tokens.
    intent = guards.classify_intent(query)
    if intent == guards.ADVICE:
        logger.info(f"q={qhash} stage=guard status=REFUSED_ADVICE intent=ADVICE ms=0")
        return render(query, refusal_text(REFUSED_ADVICE), REFUSED_ADVICE)
    if intent == guards.RETURNS:
        logger.info(f"q={qhash} stage=guard status=REFUSED_RETURNS intent=RETURNS ms=0")
        return render(query, refusal_text(REFUSED_RETURNS), REFUSED_RETURNS)
    if intent == guards.OUT_OF_SCOPE:
        logger.info(f"q={qhash} stage=guard status=OUT_OF_SCOPE intent=OUT_OF_SCOPE ms=0")
        return render(query, refusal_text(OUT_OF_SCOPE), OUT_OF_SCOPE)

    # 3. Retrieval. Empty means every chunk scored below MIN_SCORE, which is the
    #    signal to say so rather than to ask a model to improvise.
    try:
        evidence = search(query)
    except FetchError as exc:
        # Structurally unreachable here: retrieval reads the local index and never
        # fetches (architecture.md D11). Handled anyway because the spec asks for an
        # explicit, non-fabricated response rather than a traceback, and a future
        # retriever that fetched must not 500 the UI.
        logger.info(f"q={qhash} stage=retrieve error=FetchError detail={exc}")
        return _outage(query)
    except Exception as exc:  # noqa: BLE001 - a broken index must not 500 the UI
        logger.info(f"q={qhash} stage=retrieve error={type(exc).__name__}")
        return render(query, NOT_FOUND_TEXT, NOT_FOUND)

    if not evidence:
        logger.info(f"q={qhash} stage=retrieve top_score=none n=0 schemes=[]")
        # FR-10 wants a "not found" to point somewhere useful. With no evidence
        # there is no rank-1 chunk to cite, so fall back to the page for the
        # scheme the question named -- the user can read the facts themselves,
        # which is more use than an apology. A question that named no scheme
        # still gets None, because inventing a page would be a guess.
        pointer = scheme_url(detect_scheme(query) or None)
        result = render(query, NOT_FOUND_TEXT, NOT_FOUND)
        if pointer:
            result.citation_url = pointer
        return result

    top = evidence[0]
    schemes = sorted({hit.chunk.scheme for hit in evidence})
    logger.info(
        f"q={qhash} stage=retrieve top_score={top.score:.4f} n={len(evidence)} "
        f"schemes={schemes} chunk_ids={[hit.chunk.chunk_id for hit in evidence]}"
    )

    # 4. Generation. The only LLM call, and only for a FACTUAL question with evidence.
    from rag.prompts import build_context_prompt, system_prompt

    prompt = build_context_prompt(query, evidence)
    try:
        completion = generator.generate_ex(prompt, system=system_prompt())
    except generator.LLMUnavailable as exc:
        # Never answer from memory. The message says the assistant is down, which is
        # the truth and is not the same claim as "the corpus had no answer".
        logger.info(f"q={qhash} stage=generate error=LLMUnavailable detail={exc}")
        return _outage(query, evidence)
    except FetchError as exc:
        # Same reasoning as the retrieval handler above: unreachable on this path,
        # handled because the spec asks for it and a fetching backend would land here.
        logger.info(f"q={qhash} stage=generate error=FetchError detail={exc}")
        return _outage(query, evidence)

    logger.info(
        f"q={qhash} stage=generate ms={completion.ms:.0f} tokens_out={completion.tokens_out} "
        f"model={completion.model}"
    )

    # 5. Verify. The model asked nicely; this is the check.
    final_text, status = verifier.verify(completion.text, evidence)
    logger.info(f"q={qhash} stage=verify status={status} ms=0")

    # 6. Render.
    return render(query, final_text, status, evidence)


def ask(query: str) -> Answer:
    """Alias kept for readability at call sites that read as a question."""
    return answer(query)


def scheme_link(query: str) -> Optional[str]:
    """The official page for the scheme a query names, or None.

    Used by a UI to offer a "read the source" link even when generation failed.
    """
    return scheme_url(detect_scheme(query) or None)


def reset_state() -> None:
    """Clear the cached fetch dates. For tests that rewrite the ingest report."""
    global _FETCHED_AT
    _FETCHED_AT = None
