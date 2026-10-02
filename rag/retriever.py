"""Stage 5: retrieval. Given a question, return the right chunks — or nothing.

The whole design here is one claim: **an empty result is a valid result.** A
retriever that always returns its top 5 chunks forces the generator to either
answer from an irrelevant passage or invent something, and both failure modes
are worse than saying "I couldn't find that in the official sources." So the
threshold in step 4 is load-bearing, not a tuning knob, and `search()` returns
`[]` rather than a weak hit when nothing clears it.

Two ordering decisions worth stating, because both were measured rather than
assumed:

- **The filter is `{"$or": [{"scheme": X}, {"scope": "general"}]}`, never
  `{"scheme": X}`.** The ELSS lock-in is stated only on an AMFI page tagged
  `scope=general`, so a plain scheme filter makes the answer unreachable for
  exactly the question the demo is judged on (architecture.md §6.5). P3 measured
  the same thing: unfiltered, "exit load on HDFC Large Cap" ranks Small Cap and
  Equity Fund chunks above Large Cap's own, because every scheme has an exit load.
- **Deduplication runs after scoring, not before.** Collapsing on
  `(scheme, section, source_url)` is what stops five chunks of one section from
  filling the evidence set, but doing it first would throw away the ranking that
  tells us which section actually answered the question.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from rag.vectorstore import RetrievedChunk, search as vector_search
from sources import SCHEME_ALIASES, load_sources

# Longest alias first, so "balanced advantage" is tested before "hdfc" would
# match inside some other phrase, and "hdfc elss" before the bare "elss".
# Sorted once at import; SCHEME_ALIASES itself is left in its readable order.
_ORDERED_ALIASES: List[Tuple[str, str]] = sorted(
    SCHEME_ALIASES.items(), key=lambda item: (-len(item[0]), item[0])
)

_SCHEME_URLS = {source.scheme: source.url for source in load_sources()}

_HDFC_CUE = "hdfc"

# Hyphen, en dash, em dash and the minus sign, all of which appear in scheme names
# or get typed in place of a space.
_DASHES = ("-", "\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2212")


def _normalise(text: str) -> str:
    """Lowercase and flatten dashes, so spelling variants match the alias table.

    Scheme names carry hyphens that people do not type and do not need to:
    "HDFC Mid-Cap Opportunities Fund" versus an alias written "mid cap
    opportunities". Matching on the raw string silently failed for every
    hyphenated name, which is exactly the kind of miss that looks like a
    retrieval-quality problem and is not one.
    """
    lowered = text.lower()
    for dash in _DASHES:
        lowered = lowered.replace(dash, " ")
    return lowered


def detect_scheme(query: str) -> Optional[str]:
    """The scheme named in `query`, or None.

    Two rules, both learned the hard way from the P4 gate:

    - **Longest alias wins.** "hdfc balanced advantage fund" contains "hdfc", so a
      naive scan testing short aliases first can resolve it to the wrong scheme.
    - **A category word is not a scheme name.** Aliases like "flexi cap", "large cap"
      and "small cap" are categories, and a category on its own does not identify a
      fund. The gate caught this: "What is the expense ratio of Parag Parikh Flexi Cap
      Fund?" matched "flexi cap" and was filtered to HDFC Equity Fund, then scored
      0.68 against HDFC Equity Fund's real expense-ratio chunk. Answering a question
      about one AMC with another AMC's number is a factual error, so a category
      alias only counts when the query also says "hdfc".

    Returning None is always safe: it widens the candidate set and lets `MIN_SCORE`
    decide, whereas a wrong detection silently narrows it to the wrong fund.
    """
    lowered = _normalise(query)
    for alias, scheme in _ORDERED_ALIASES:
        if alias not in lowered:
            continue
        if _HDFC_CUE not in alias and _HDFC_CUE not in lowered:
            continue
        return scheme
    return None


def build_filter(scheme: Optional[str]) -> Optional[dict]:
    """The metadata filter for a detected scheme.

    Always includes `scope=general`, so a scheme question can still reach the
    AMFI regulator material. With no scheme detected, no filter is applied at
    all: the question is either general or out of corpus, and `MIN_SCORE` is what
    decides (architecture.md §6.5).
    """
    if not scheme:
        return None
    return {"$or": [{"scheme": scheme}, {"scope": "general"}]}


def expand_query(query: str, scheme: Optional[str]) -> str:
    """Append the canonical scheme name so the embedding carries the entity.

    The question says "large cap"; the chunks say "HDFC Large Cap Fund". Low risk
    because the filter has already constrained the candidates, so this can only
    reorder within the right scheme.
    """
    return f"{query} {scheme}" if scheme else query


def scheme_url(scheme: Optional[str]) -> Optional[str]:
    """The default citation URL for a scheme (architecture.md §6.5, citation rule)."""
    return _SCHEME_URLS.get(scheme) if scheme else None


def _dedupe(hits: Sequence[RetrievedChunk]) -> List[RetrievedChunk]:
    """Collapse hits sharing `(scheme, section, source_url)`, keeping the best.

    Order-independent on purpose: it keeps the highest-scoring member of each
    group and re-sorts, rather than trusting that its input arrived ranked.
    Vector search does return score-descending, but a reranked or hand-built list
    would otherwise silently promote the worse of two identical passages.
    """
    best: dict = {}
    for hit in hits:
        key = (hit.chunk.scheme, hit.chunk.section, hit.chunk.source_url)
        current = best.get(key)
        if current is None or hit.score > current.score:
            best[key] = hit
    kept = sorted(best.values(), key=lambda hit: hit.score, reverse=True)
    return [
        RetrievedChunk(rank=position, score=hit.score, chunk=hit.chunk)
        for position, hit in enumerate(kept, start=1)
    ]


def _rerank(query: str, hits: List[RetrievedChunk], top_k: int) -> List[RetrievedChunk]:
    """Rescore with a cross-encoder. Optional; off by default.

    Lazy-imported so the demo never depends on the model being present. If the
    flag is on but the model cannot load, this warns and returns the original
    order rather than failing the query — a degraded ranking beats no answer,
    and `ENABLE_RERANK` defaults to False anyway.
    """
    try:
        from sentence_transformers import CrossEncoder
    except ImportError:
        print("[rerank] sentence-transformers unavailable; keeping vector order")
        return hits[:top_k]

    try:
        model = CrossEncoder(config.RERANK_MODEL)
    except Exception as error:  # noqa: BLE001 - any load failure degrades, not crashes
        print(f"[rerank] could not load {config.RERANK_MODEL}: {error}")
        return hits[:top_k]

    scores = model.predict([(query, hit.chunk.text) for hit in hits])
    order = sorted(range(len(hits)), key=lambda i: scores[i], reverse=True)[:top_k]
    rescored = [
        RetrievedChunk(rank=0, score=round(float(scores[i]), 6), chunk=hits[i].chunk)
        for i in order
    ]
    return [
        RetrievedChunk(rank=position, score=hit.score, chunk=hit.chunk)
        for position, hit in enumerate(rescored, start=1)
    ]


def search(query: str, top_k: Optional[int] = None) -> List[RetrievedChunk]:
    """Ranked evidence for `query`, or `[]` when nothing clears `MIN_SCORE`.

    The steps, in order: detect the scheme, expand the query with it, vector
    search under the filter, optionally rerank, deduplicate, then threshold.
    The threshold is applied to the *deduplicated* top hit, so a strong score on
    a chunk that was then collapsed away cannot smuggle a weak answer through.
    """
    from rag import embedder

    limit = top_k or config.TOP_K
    scheme = detect_scheme(query)
    where = build_filter(scheme)

    expanded = expand_query(query, scheme)
    vector = embedder.embed_query(expanded)

    fetch = config.RERANK_TOP_N if config.ENABLE_RERANK else limit
    hits = vector_search(vector, top_k=fetch, where=where)
    if not hits:
        return []

    if config.ENABLE_RERANK:
        hits = _rerank(expanded, hits, limit)
    else:
        hits = hits[:limit]

    hits = _dedupe(hits)
    if not hits:
        return []

    if hits[0].score < config.MIN_SCORE:
        return []
    return hits


def explain(query: str, top_k: Optional[int] = None) -> dict:
    """Retrieval plus the intermediate decisions, for calibration and debugging.

    Kept separate from `search()` so the pipeline never pays for it and the
    answer path stays a pure function of the index.
    """
    from rag import embedder

    scheme = detect_scheme(query)
    where = build_filter(scheme)
    expanded = expand_query(query, scheme)
    vector = embedder.embed_query(expanded)
    raw = vector_search(vector, top_k=top_k or config.TOP_K, where=where)
    deduped = _dedupe(raw)
    return {
        "question": query,
        "scheme": scheme,
        "where": where,
        "expanded": expanded,
        "raw": raw,
        "deduped": deduped,
        "top_score": deduped[0].score if deduped else 0.0,
        "passed": bool(deduped) and deduped[0].score >= config.MIN_SCORE,
    }
