#!/usr/bin/env python3
"""P5 gate: run real generation through the full prompt → generate → verify path.

The spec's gate is empirical: "print the raw model output verbatim" and check it
stays inside the constraints. That matters because a system prompt is a request,
not a guarantee — this script shows what actually comes back, then shows what the
verifier did to it, so a prompt that has quietly stopped working is visible.

No network mocking. If the key or the network is down this fails loudly, which is
the point: a green gate that never called a model is not a gate.

Usage:
    python scripts/probe_generation.py
    python scripts/probe_generation.py --write     # also write reports/generation_gate.md
    python scripts/probe_generation.py --question "What is the exit load on HDFC Small Cap Fund?"
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from rag import generator, guards, verifier  # noqa: E402
from rag.prompts import NOT_FOUND_TEXT, build_context_prompt, system_prompt  # noqa: E402
from rag.retriever import search  # noqa: E402

DEFAULT_QUESTIONS = [
    "What is the expense ratio of HDFC Large Cap Fund?",
    "What is the exit load on HDFC Small Cap Fund?",
    "What is the minimum SIP amount for HDFC Balanced Advantage Fund?",
    "What is the lock-in period for HDFC ELSS Tax Saver Fund?",
    "What is the riskometer level and benchmark of HDFC Equity Fund?",
    "How do I download a capital gains statement?",
]

# Questions with no support in the corpus. Verified absent, not assumed: each of
# these was checked against the indexed chunks before being listed. An earlier
# draft used "who is the fund manager", which looked absent but is in the corpus
# (the manager block is in the general chunks) -- the model was right to answer.
ABSENT_QUESTIONS = [
    "What is the SEBI registration code of HDFC Equity Fund?",
    "What is the custodian name of HDFC Large Cap Fund?",
    "What is the AMC's toll-free customer care number?",
]


def _checks(raw: str) -> list:
    """Constraint-by-constraint verdict on the raw model output."""
    sentences = verifier._sentence_split(raw)
    out = []
    out.append((f"<= {config.MAX_ANSWER_SENTENCES} sentences",
                len(sentences) <= config.MAX_ANSWER_SENTENCES,
                f"{len(sentences)} sentence(s)"))
    out.append(("no advice phrasing",
                not verifier._ADVICE_RE.search(raw),
                "clean" if not verifier._ADVICE_RE.search(raw) else "ADVICE FOUND"))
    out.append(("no return figure",
                not verifier._RETURN_RE.search(raw),
                "clean" if not verifier._RETURN_RE.search(raw) else "RETURN FIGURE FOUND"))
    invented = [u for u in verifier._URL_RE.findall(raw)
                if not verifier._is_approved(u, verifier.approved_urls())]
    out.append(("no invented URL", not invented, "clean" if not invented else f"{invented}"))
    out.append(("no reasoning trace leaked",
                "Need to" not in raw and "reasoning" not in raw.lower()[:40],
                "clean"))
    return out


def run_question(question: str) -> dict:
    """Retrieve → prompt → generate → verify, returning every stage for display."""
    record = {"question": question}
    hits = search(question)
    record["hits"] = hits
    if not hits:
        record["raw"] = None
        record["final"] = verifier.refusal_text(verifier.NOT_FOUND) if hasattr(
            verifier, "refusal_text") else ""
        record["status"] = verifier.NOT_FOUND
        record["note"] = "no evidence above MIN_SCORE; generation correctly skipped"
        return record

    prompt = build_context_prompt(question, hits)
    started = time.time()
    # The free tier allows 8000 tokens/min and these prompts are large, so the
    # gate paces itself. generator._post also retries a 429, but pacing is
    # cheaper than waiting out a backoff.
    time.sleep(config.GROQ_RATE_LIMIT_PAUSE)
    raw = generator.generate(prompt, system=system_prompt())
    record["raw"] = raw
    record["seconds"] = time.time() - started
    record["final"], record["status"] = verifier.verify(raw, hits)
    return record


def build_report() -> tuple:
    lines = []
    add = lines.append
    failures = 0

    add("# P5 generation gate")
    add("")
    add(f"Backend: `{generator.backend()}`, temperature={config.LLM_TEMPERATURE}, "
        f"max_tokens={config.LLM_MAX_TOKENS}, reasoning_effort="
        f"{config.GROQ_REASONING_EFFORT}.")
    add("")
    add("Every `raw` block below is the model's unmodified output. The `final` block")
    add("is what the user would see after the verifier.")
    add("")

    for question in DEFAULT_QUESTIONS:
        record = run_question(question)
        add(f"## {question}")
        add("")
        if record.get("note"):
            add(f"> {record['note']}")
            add("")
            continue
        hits = record["hits"]
        add(f"Evidence: {len(hits)} chunk(s), top score {hits[0].score:.4f} "
            f"[{hits[0].chunk.scheme} | {hits[0].chunk.section}]")
        add("")
        add("**raw**")
        add("")
        add("```")
        add(record["raw"])
        add("```")
        add("")
        for label, ok, detail in _checks(record["raw"]):
            failures += not ok
            add(f"- {'PASS' if ok else 'FAIL'} {label} — {detail}")
        add("")
        add(f"**final** (`{record['status']}`)")
        add("")
        add("```")
        add(record["final"])
        add("```")
        add("")

    add("## Absent from the corpus — must return the exact string")
    add("")
    for question in ABSENT_QUESTIONS:
        record = run_question(question)
        add(f"## {question}")
        add("")
        if record.get("note"):
            add(f"> {record['note']}")
            add("")
            continue
        add("**raw**")
        add("")
        add("```")
        add(record["raw"])
        add("```")
        add("")
        exact = record["raw"].strip() == NOT_FOUND_TEXT
        failures += not exact
        add(f"- {'PASS' if exact else 'FAIL'} exact NOT_FOUND string")
        add(f"- verifier status: `{record['status']}` (must be NOT_FOUND, not ANSWERED)")
        add("")

    add("## Refusals still hold end to end")
    add("")
    for question, note in [
        ("Should I buy HDFC Small Cap Fund?", "expect ADVICE, no generation"),
        ("What is the CAGR of HDFC Large Cap Fund?", "expect RETURNS, no generation"),
        ("Expense ratio of Parag Parikh Flexi Cap Fund?", "expect OUT_OF_SCOPE"),
        ("my PAN is ABCDE1234F", "expect PII, value never forwarded"),
    ]:
        screen = guards.screen(question)
        blocked = screen["blocked_by"] is not None
        failures += not blocked
        add(f"- {'PASS' if blocked else 'FAIL'} `{screen['intent'] or screen['pii_pattern']}` "
            f"blocked={screen['blocked_by']} — {note}")
    add("")

    add("---")
    add("")
    add(f"**{'all constraints held' if not failures else f'{failures} FAILING'}**")
    return "\n".join(lines), failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true",
                        help="write reports/generation_gate.md")
    parser.add_argument("--question", help="run one question and exit")
    args = parser.parse_args()

    if args.question:
        record = run_question(args.question)
        print(record.get("note") or record["raw"])
        print("---")
        print(record["final"])
        return 0

    report, failures = build_report()
    print(report)
    if args.write:
        out = ROOT / "reports" / "generation_gate.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report + "\n", encoding="utf-8")
        print(f"\nwrote {out.relative_to(ROOT)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
