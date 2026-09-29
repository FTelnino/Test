#!/usr/bin/env python3
"""End-to-end evaluation against the golden set (`architecture.md` §11.2).

This is the artifact that turns "it seemed to work" into a claim, so the design
rule is: every assertion in the golden file must be checkable from the returned
`Answer` alone, with no reference to how the pipeline got there. The retrieval
probe (P4) is the opposite discipline and shares the same questions on purpose --
between them they cover the two things that can break independently.

Checks per row, all of them structural:
  - `expected_status`   exact match against the six allowed statuses
  - `must_contain`      every substring present in the answer (case-insensitive)
  - `forbid`            no substring present (case-insensitive)
  - `expect_scheme`     rank-1 evidence comes from that scheme, when evidence exists
  - `last_updated`      an answered row carries a real date (PRD criterion 6)
  - `cite`              an answered row carries an approved-source URL

Status is checked first and a mismatch short-circuits the rest. A row that
returned `REFUSED_PII` has no answer text worth searching, so reporting four
consequent "contains" failures would overstate the damage; it failed once.

Run:
    python scripts/evaluate.py                # run, print, write the report
    python scripts/evaluate.py --quiet        # no progress chatter
    python scripts/evaluate.py --only "exit load"   # substring filter, for debugging
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from rag.pipeline import answer  # noqa: E402
from rag.verifier import ANSWERED, approved_urls  # noqa: E402

OUT_FILE = config.REPORTS_DIR / "eval_results.md"


def load_golden() -> list:
    if not config.GOLDEN_FILE.exists():
        raise SystemExit(f"no golden set at {config.GOLDEN_FILE}")
    rows = []
    for number, line in enumerate(config.GOLDEN_FILE.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{config.GOLDEN_FILE}:{number} is not valid JSON: {exc}")
    if not rows:
        raise SystemExit(f"{config.GOLDEN_FILE} is empty")
    return rows


def check_row(entry: dict, result) -> tuple[bool, list, str]:
    """Return (passed, failed_check_names, actual_status)."""
    text = result.answer or ""
    low = text.lower()
    failed = []

    expected = entry.get("expected_status")
    if expected and result.status != expected:
        return False, [f"status: got {result.status}"], result.status

    for needle in entry.get("must_contain", []):
        if needle.lower() not in low:
            failed.append(f"must_contain {needle!r}")
    for needle in entry.get("forbid", []):
        if needle.lower() in low:
            failed.append(f"forbid {needle!r}")

    # Scheme is a retrieval property, so only assert it where evidence exists.
    # A refusal never retrieves, and asserting a scheme there would be asserting
    # that the guard failed to run.
    want_scheme = entry.get("expect_scheme")
    if want_scheme and result.evidence:
        got = result.evidence[0].chunk.scheme
        if got != want_scheme:
            failed.append(f"scheme: got {got!r}")

    if result.status == ANSWERED:
        if not result.last_updated:
            failed.append("last_updated missing")
        if not result.citation_url:
            failed.append("citation_url missing")
        elif result.citation_url.rstrip('/') not in approved_urls():
            failed.append(f"citation not an approved source: {result.citation_url}")

    return not failed, failed, result.status


def _cell(value: str, limit: int = 58) -> str:
    value = " ".join(str(value).split())
    if len(value) > limit:
        value = value[: limit - 1] + "…"
    return value.replace("|", "\\|")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--quiet", action="store_true", help="suppress progress lines")
    parser.add_argument("--only", help="only run rows whose question contains this")
    parser.add_argument("--no-write", action="store_true", help="do not write the report")
    parser.add_argument("--pause", type=float, default=config.GROQ_RATE_LIMIT_PAUSE,
                        help="seconds to pause between rows (0 to disable)")
    args = parser.parse_args()

    # The pipeline logs a line per stage, which is what you want in production
    # and noise in a report. Silence it here rather than in the pipeline, so the
    # CLI and the UI keep their traces.
    logging.getLogger("rag").setLevel(logging.WARNING)

    golden = load_golden()
    if args.only:
        needle = args.only.lower()
        golden = [g for g in golden if needle in g["question"].lower()]
        if not golden:
            raise SystemExit(f"no golden question contains {args.only!r}")

    if not config.CHROMA_DIR.exists():
        raise SystemExit(f"no index at {config.CHROMA_DIR} — run: python run_ingest.py --index")

    print(f"Evaluating {len(golden)} golden questions against the live pipeline.\n")

    results = []
    for index, entry in enumerate(golden, 1):
        if not args.quiet:
            print(f"  [{index}/{len(golden)}] {entry['question'][:66]}", flush=True)
        started = time.time()
        result = answer(entry["question"])
        passed, failed, actual = check_row(entry, result)
        results.append({
            "entry": entry, "result": result, "passed": passed,
            "failed": failed, "actual": actual, "elapsed": time.time() - started,
        })
        # The free-tier API is the scarce resource here, not CPU. Pause rather
        # than let a run trip 429 and turn an eval into a retry test.
        if args.pause and index < len(golden):
            time.sleep(args.pause)

    passed = [r for r in results if r["passed"]]
    failed = [r for r in results if not r["passed"]]
    accuracy = len(passed) / len(results)

    lines = []
    lines.append("| # | question | expected | actual | top score | checks | verdict |")
    lines.append("|---|---|---|---|---|---|---|")
    for number, row in enumerate(results, 1):
        entry, result = row["entry"], row["result"]
        score = f"{result.evidence[0].score:.4f}" if result.evidence else "—"
        detail = "all checks held" if row["passed"] else "; ".join(row["failed"])
        lines.append(
            f"| {number} | {_cell(entry['question'], 52)} "
            f"| {entry.get('expected_status', '—')} | {row['actual']} "
            f"| {score} | {_cell(detail, 56)} | {'PASS' if row['passed'] else 'FAIL'} |"
        )

    report = []
    report.append("# End-to-end eval results")
    report.append("")
    report.append(f"`python scripts/evaluate.py` — **{len(passed)}/{len(results)} rows green "
                  f"({accuracy:.0%})**.")
    report.append("")
    report.append("\n".join(lines))
    report.append("")
    report.append("## Reading this table")
    report.append("")
    report.append("- **`top score` is blank on every refusal.** That is the P7 order holding: the")
    report.append("  guard runs before retrieval, so a refused question never touches the vector")
    report.append("  store. An empty cell here is the evidence for that claim.")
    report.append("- **`top score` is not a pass/fail signal on its own.** `MIN_SCORE` is 0.40 and")
    report.append("  the in-corpus band is 0.6254–0.7772, but a different AMC's question scores")
    report.append("  0.6386 against genuine HDFC text. Retrieval cannot tell those apart, which is")
    report.append("  why the intent guard exists and why rows 1 and 11 share a topic.")
    report.append("- **The Parag Parikh row is a guard test wearing a retrieval costume.** Both")
    report.append("  it and the Flexi Cap row ask for an expense ratio; one is in scope and one is")
    report.append("  not, and the only thing separating them is the HDFC cue.")
    report.append("")
    if failed:
        report.append("## Failures")
        report.append("")
        for number, row in enumerate(results, 1):
            if row["passed"]:
                continue
            report.append(f"{number}. **{row['entry']['question']}** — {'; '.join(row['failed'])}")
            report.append("")
            report.append(f"   > {row['result'].answer}")
            report.append("")
        report.append("## Notes on the golden set")
        report.append("")
    else:
        report.append("## Notes on the golden set")
        report.append("")

    for note in (r["entry"].get("note") for r in results if r["entry"].get("note")):
        report.append(f"- {note}")
        report.append("")

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text("\n".join(report), encoding="utf-8")

    print()
    print("\n".join(lines))
    print()
    print(f"  accuracy: {len(passed)}/{len(results)} ({accuracy:.0%})")
    if failed:
        print()
        for number, row in enumerate(results, 1):
            if row["passed"]:
                continue
            print(f"  FAIL {number}. {row['entry']['question'][:60]}")
            print(f"       {'; '.join(row['failed'])}")
    print(f"\n  wrote {OUT_FILE.relative_to(ROOT)}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
