"""Prompt construction and user-facing response text (Stage 6 / P5).

Two separate jobs live here and they are deliberately not mixed:

- **The system prompt** asks the model to stay inside the constraints. It is a
  *request*, and a model can ignore it. `verifier.py` is the check.
- **The response constants** are what we show when the verifier or the guards
  reject something. These are not negotiable by the model, which is exactly why
  they are module constants and not prompt text.

The context format is the load-bearing part of the system prompt. Every chunk is
prefixed with `[scheme | section | url]`, which gives the model the attribution it
needs and makes inventing a URL pointless: any URL it writes is either in a header
or a fabrication that the verifier strips.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

#: Exact string required by the PRD when the corpus has no supporting chunk.
NOT_FOUND_TEXT = "I couldn't find that in the official sources."

REFUSED_ADVICE = (
    "I share facts from the official scheme sources, not investment advice. "
    "For guidance on choosing a fund, see AMFI's investor education page: {link}."
)

REFUSED_RETURNS = (
    "I don't compute or compare returns. Please see the official factsheet: {link}."
)

#: No echo, no partial mask, no apology that repeats the input. The matched value
#: is never available to this module either, so it cannot be leaked from here.
REFUSED_PII = (
    "I can't process personal identifiers like PAN, Aadhaar, account, or OTP "
    "numbers. Please remove it and ask your question again."
)

OUT_OF_SCOPE = (
    "I only cover HDFC mutual fund schemes from their official pages, so I can't "
    "answer questions about other fund providers."
)

_DISCLAIMER = "Facts-only. No investment advice."

#: The single instruction block sent with every request. Every clause here maps to
#: a specific failure the verifier also checks, because a constraint that is only in
#: the prompt is a constraint that is only a hope.
SYSTEM_PROMPT = """You are a factual assistant for HDFC mutual fund scheme pages.

Answer ONLY from the CONTEXT blocks provided. Follow every rule:

1. Use only facts present in the CONTEXT. If the answer is not there, reply with
   exactly: I couldn't find that in the official sources.
2. Write at most {max_sentences} sentences. Stop after that, even mid-thought.
3. Give no investment advice. Never say "you should", "I recommend",
   "consider investing", or "it is advised to". State facts, not opinions.
4. Give no returns, performance figures, CAGR, projections, or predictions. Facts
   like expense ratio, exit load, lock-in period, and AUM are fine; a percentage
   attached to a return word is not.
5. Use no URL other than one that appears in a CONTEXT header. If you cite a
   source, copy its URL exactly.
6. No preamble, no sign-off, no questions back to the user. Answer and stop.

Be terse. Facts only."""


def system_prompt() -> str:
    """`SYSTEM_PROMPT` with the sentence limit filled from config."""
    return SYSTEM_PROMPT.format(max_sentences=config.MAX_ANSWER_SENTENCES)


def _chunk_parts(evidence: Iterable) -> Sequence:
    parts = []
    for item in evidence or []:
        chunk = getattr(item, "chunk", None)
        if chunk is None:
            parts.append(item)
        else:
            parts.append(chunk)
    return parts


def build_context_prompt(question: str, evidence: Sequence) -> str:
    """The user turn: the question, then the evidence blocks it may be answered from.

    Each block carries its `[scheme | section | url]` header. That header is doing
    the attribution work and the anti-fabrication work at once: the model can name
    a source because the source is labelled, and a URL it invents will not match
    any header, so `verifier.py` will strip it.
    """
    blocks = []
    for item in _chunk_parts(evidence):
        scheme = getattr(item, "scheme", None) or "unknown scheme"
        section = getattr(item, "section", None) or "unspecified section"
        url = getattr(item, "source_url", None) or ""
        text = (getattr(item, "text", None) or "").strip()
        if not text:
            continue
        blocks.append(f"[{scheme} | {section} | {url}]\n{text}")

    if not blocks:
        return (
            f"Question: {question}\n\n"
            "CONTEXT: (none)\n\n"
            f"Reply with exactly: {NOT_FOUND_TEXT}"
        )

    context = "\n\n".join(blocks)
    return (
        f"CONTEXT:\n{context}\n\n"
        "---\n"
        f"Question: {question}\n\n"
        f"Answer in at most {config.MAX_ANSWER_SENTENCES} sentences, using only the "
        "CONTEXT above. If the CONTEXT does not contain the answer, reply with exactly: "
        f"{NOT_FOUND_TEXT}"
    )


def education_link() -> str:
    return config.EDUCATION_LINK


def factsheet_link(slug: str) -> str:
    return config.FACTSHEET_LINK_TEMPLATE.format(slug=slug)


def refusal_text(status: str, slug: str = "") -> str:
    """The rendered refusal body for a status, with `{link}` filled from config."""
    templates = {
        "NOT_FOUND": NOT_FOUND_TEXT,
        "REFUSED_ADVICE": REFUSED_ADVICE,
        "REFUSED_RETURNS": REFUSED_RETURNS,
        "REFUSED_PII": REFUSED_PII,
        "OUT_OF_SCOPE": OUT_OF_SCOPE,
    }
    if status not in templates:
        raise KeyError(f"no refusal text for status {status!r}")
    text = templates[status]
    if "{link}" in text:
        link = factsheet_link(slug) if slug else education_link()
        text = text.format(link=link)
    return text


def disclaimer() -> str:
    return _DISCLAIMER
