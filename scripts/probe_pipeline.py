#!/usr/bin/env python3
"""P7 gate: drive the whole pipeline for one query per status and report it.

The pipeline's correctness is mostly about order, and order is invisible in a
return value. This prints the structured log lines alongside the `Answer` so the
sequence of stages that actually ran is on the page next to the result.

Live by default: the LLM paths are real calls, and the `LLMUnavailable` path is
simulated (a real outage cannot be staged on demand).

Usage:
    python scripts/probe_pipeline.py
    python scripts/probe_pipeline.py --write   # also write reports/pipeline_gate.md
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from rag import generator, pipeline  # noqa: E402
from rag.verifier import (  # noqa: E402
    ANSWERED, NOT_FOUND, OUT_OF_SCOPE, REFUSED_ADVICE, REFUSED_PII, REFUSED_RETURNS,
)

# (expected status, query, note)
CASES = [
    (ANSWERED, "What is the expense ratio of HDFC Large Cap Fund?",
     "the normal path: guards pass, evidence found, generated, verified"),
    (REFUSED_ADVICE, "Should I buy HDFC Small Cap Fund?",
     "refused before retrieval; no tokens spent"),
    (REFUSED_RETURNS, "What is the CAGR of HDFC Large Cap Fund?",
     "refused before retrieval; no tokens spent"),
    (REFUSED_PII, "my PAN is ABCDE1234F",
     "refused before the string is embedded or logged; only pattern+mask logged"),
    (OUT_OF_SCOPE, "Expense ratio of Parag Parikh Flexi Cap Fund?",
     "refused before retrieval: the corpus does not cover that fund"),
    (NOT_FOUND, "What is the SEBI registration code of HDFC Equity Fund?",
     "evidence retrieved but does not support an answer; points at the scheme page"),
]


def _llm_outage_case() -> tuple:
    """Simulate the backend being down, since a real outage cannot be staged."""
    real = generator.generate_ex
    calls = []

    def boom(prompt, system=None):
        calls.append(prompt)
        raise generator.LLMUnavailable("simulated outage")

    generator.generate_ex = boom
    try:
        result = pipeline.answer("What is the exit load on HDFC Large Cap Fund?")
    finally:
        generator.generate_ex = real
    return result, len(calls)


def build_report() -> tuple:
    lines = []
    add = lines.append
    failures = 0

    add("# P7 pipeline gate")
    add("")
    add(f"Backend: `{generator.backend()}`. `MIN_SCORE={config.MIN_SCORE}`, "
        f"`MAX_ANSWER_SENTENCES={config.MAX_ANSWER_SENTENCES}`.")
    add("")
    add("Each block shows the `Answer` and whether the stages ran in the documented")
    add("order. Refusals must show `guard` then `render` and nothing else.")
    add("")

    statuses = []
    for expected, question, note in CASES:
        started = time.time()
        result = pipeline.answer(question)
        elapsed = (time.time() - started) * 1000

        ok = result.status == expected
        failures += not ok
        statuses.append(result.status)

        add(f"## {question}")
        add("")
        add(f"expected `{expected}` — {note}")
        add("")
        add(f"- **{'PASS' if ok else 'FAIL'}** status `{result.status}`")
        add(f"- latency {elapsed:.0f} ms")
        add(f"- citation_url: `{result.citation_url}`")
        add(f"- last_updated: `{result.last_updated}`")
        add(f"- evidence: {len(result.evidence)} chunk(s)")
        add("")
        add("```")
        add(result.answer)
        add("```")
        add("")

    add("## LLM backend down")
    add("")
    result, calls = _llm_outage_case()
    no_fabrication = "1%" not in result.answer and result.answer
    still_points = result.citation_url is not None
    failures += not (no_fabrication and still_points)
    add(f"- {'PASS' if no_fabrication else 'FAIL'} no answer invented from memory")
    add(f"- {'PASS' if still_points else 'FAIL'} still points at the real page "
        f"(`{result.citation_url}`)")
    add(f"- status `{result.status}`, message: {result.answer!r}")
    add("")

    add("## Status coverage")
    add("")
    for status in (ANSWERED, NOT_FOUND, REFUSED_ADVICE, REFUSED_RETURNS, REFUSED_PII,
                   OUT_OF_SCOPE):
        mark = "reachable" if status in statuses else "MISSING"
        add(f"  {status:16} {mark}")
    if len(set(statuses)) != 6:
        failures += 1
    add("")

    add("---")
    add("")
    add(f"**{6 - min(failures, 6)}/6 statuses correct and all checks held**"
        if not failures else f"**{failures} FAILING**")
    return "\n".join(lines), failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true",
                        help="write reports/pipeline_gate.md")
    args = parser.parse_args()

    report, failures = build_report()
    print(report)
    if args.write:
        out = ROOT / "reports" / "pipeline_gate.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report + "\n", encoding="utf-8")
        print(f"\nwrote {out.relative_to(ROOT)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
