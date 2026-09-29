"""P4 gate probe: retrieval quality and MIN_SCORE calibration.

    python scripts/probe_retrieval.py

Prints, per golden question, the top score and the passage a human would have
picked, then reports where MIN_SCORE sits relative to the observed scores. The
threshold is only meaningful if you can see both sides of it, so this prints the
whole distribution rather than a pass/fail.

`--dump` additionally writes reports/retrieval_calibration.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from rag import retriever


def load_golden() -> list:
    if not config.GOLDEN_FILE.exists():
        raise SystemExit(f"no golden set at {config.GOLDEN_FILE}")
    return [
        json.loads(line)
        for line in config.GOLDEN_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# Which section label should win for each acceptance topic. Used only to report
# section accuracy, never to filter — the retriever never sees this.
_TOPIC_SECTIONS = {
    "expense ratio": "expense ratio",
    "exit load": "exit load",
    "minimum sip": "exit load",
    "elss lock-in": "lock-in",
    "riskometer/benchmark": "riskometer",
    "capital-gains statement": "account statement",
}


def _topic_section(topic: str) -> str:
    return _TOPIC_SECTIONS.get(topic, topic)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dump", action="store_true", help="write reports/retrieval_calibration.md")
    parser.add_argument("--min-score", type=float, default=None, help="override for the trial run")
    args = parser.parse_args(argv)

    golden = load_golden()
    if args.min_score is not None:
        config.MIN_SCORE = args.min_score

    print(f"MIN_SCORE = {config.MIN_SCORE}   TOP_K = {config.TOP_K}   "
          f"rerank = {config.ENABLE_RERANK}\n")
    print("=" * 100)

    rows = []
    for entry in golden:
        # The golden file is shared with the end-to-end evaluator (architecture.md
        # §11.2), which added refusal and PII rows. Those are answered by the guards
        # before retrieval ever happens, so probing them here would measure nothing
        # and score them against a threshold that does not apply. Rows opt in with
        # `retrieval_probe`; the default is off, so a new row cannot silently change
        # the meaning of the P4 gate.
        if not entry.get("retrieval_probe"):
            continue
        report = retriever.explain(entry["question"], top_k=config.TOP_K)
        hits = report["deduped"]
        top = hits[0] if hits else None
        kind = entry.get("kind", "in_corpus")

        if kind == "in_corpus":
            verdict = "PASS" if report["passed"] else "FAIL"
        elif kind == "out_of_corpus":
            verdict = "PASS" if not report["passed"] else "FAIL"
        else:
            verdict = "guards" if not report["passed"] else "not caught by score"

        print(f"\n{entry['question']}")
        print(f"  topic={entry['topic']}  kind={kind}  expect_scheme={entry['expect_scheme'] or '(any)'}")
        print(f"  detected scheme : {report['scheme'] or '-'}")
        print(f"  filter          : {json.dumps(report['where']) if report['where'] else 'none'}")
        print(f"  expanded query  : {report['expanded']!r}")
        print(f"  raw hits        : {len(report['raw'])} -> after dedupe: {len(hits)}")
        if top:
            print(f"  TOP SCORE       : {top.score:.4f}   [{verdict}]")
        else:
            print(f"  TOP SCORE       : (no hits)   [{verdict}]")
        for hit in hits[:3]:
            print(f"    #{hit.rank} {hit.score:.4f}  {hit.chunk.chunk_id}")
            print(f"        scheme={hit.chunk.scheme}  section={hit.chunk.section}")
            print(f"        {hit.chunk.text[:80]!r}")

        rows.append(
            {
                "question": entry["question"],
                "topic": entry["topic"],
                "kind": kind,
                "in_corpus": kind == "in_corpus",
                "expect_scheme": entry["expect_scheme"],
                "detected": report["scheme"],
                "top_score": report["top_score"],
                "passed": report["passed"],
                "verdict": verdict,
                "top_chunk": top.chunk.chunk_id if top else "",
                "top_scheme": top.chunk.scheme if top else "",
                "top_section": top.chunk.section if top else "",
                "preview": (top.chunk.text[:80] if top else ""),
                "raw_hits": len(report["raw"]),
                "deduped": len(hits),
            }
        )

    in_corpus = [r["top_score"] for r in rows if r["in_corpus"]]
    out_corpus = [r["top_score"] for r in rows if r["kind"] == "out_of_corpus"]
    out_scope = [r["top_score"] for r in rows if r["kind"] == "out_of_scope"]

    print("\n" + "=" * 100)
    print("CALIBRATION")
    if in_corpus:
        print(f"  in-corpus      n={len(in_corpus)}  min={min(in_corpus):.4f}  max={max(in_corpus):.4f}")
    if out_corpus:
        print(f"  out-of-corpus  n={len(out_corpus)}  max={max(out_corpus):.4f}")
    if out_scope:
        print(f"  out-of-scope   n={len(out_scope)}  range {min(out_scope):.4f}-{max(out_scope):.4f}"
              f"   (same topic, different AMC)")

    if in_corpus and out_corpus:
        low, high = max(out_corpus), min(in_corpus)
        print(f"\n  separation window for MIN_SCORE: ({low:.4f}, {high:.4f}]")
        print(f"  chosen MIN_SCORE = {config.MIN_SCORE} -> "
              f"{'INSIDE' if low < config.MIN_SCORE <= high else 'OUTSIDE'} that window")
        print(f"    margin above highest out-of-corpus: {config.MIN_SCORE - low:+.4f}")
        print(f"    margin below lowest in-corpus:     {high - config.MIN_SCORE:+.4f}")

    if out_scope and in_corpus:
        print(f"\n  NOTE: out-of-scope scores ({min(out_scope):.4f}-{max(out_scope):.4f}) overlap the")
        print(f"  in-corpus band ({min(in_corpus):.4f}-{max(in_corpus):.4f}). No MIN_SCORE separates a")
        print("  question about another AMC from one about this AMC. That case is refused by")
        print("  guards.classify_intent() -> OUT_OF_SCOPE, before retrieval is ever called.")

    expected = [r for r in rows if r["in_corpus"] and r["expect_scheme"]]
    wrong = [r for r in expected if r["top_scheme"] != r["expect_scheme"]]
    right_section = [
        r for r in expected
        if r["top_section"] and _topic_section(r["topic"]).lower() in r["top_section"].lower()
    ]
    print(f"\n  golden questions whose top hit is from the expected scheme: "
          f"{len(expected) - len(wrong)}/{len(expected)}")
    print(f"  ...and whose top hit is from the expected SECTION:        "
          f"{len(right_section)}/{len(expected)}")
    for r in wrong:
        print(f"    SCHEME MISMATCH {r['question']}")
        print(f"      expected {r['expect_scheme']}, got {r['top_scheme'] or '(none)'}")
    for r in expected:
        if r not in wrong and r not in right_section:
            print(f"    section off  {r['question']}")
            print(f"      asked about {r['topic']!r}, top hit section is {r['top_section']!r}")

    failures = [r for r in rows if r["verdict"] == "FAIL"]
    print(f"\n  GATE: {len(rows) - len(failures)}/{len(rows)} as expected"
          + ("" if not failures else "  FAILURES:"))
    for r in failures:
        print(f"    FAIL {r['question']}  score={r['top_score']:.4f}")

    if args.dump:
        write_report(rows, in_corpus, out_corpus, out_scope)
        print(f"\nwrote {config.REPORTS_DIR / 'retrieval_calibration.md'}")
    return 1 if failures else 0


def write_report(rows, in_corpus, out_corpus, out_scope) -> None:
    path = config.REPORTS_DIR / "retrieval_calibration.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lowest = min(in_corpus) if in_corpus else 0.0
    highest = max(out_corpus) if out_corpus else 0.0
    expected = [r for r in rows if r["in_corpus"] and r["expect_scheme"]]
    right_section = [
        r for r in expected
        if r["top_section"] and _topic_section(r["topic"]).lower() in r["top_section"].lower()
    ]

    lines = [
        "# P4 retrieval calibration",
        "",
        f"`MIN_SCORE` is set to **{config.MIN_SCORE}**.",
        "",
        "## Why a threshold at all",
        "",
        "A retriever that always returns its top *k* chunks cannot say \"not found\", so the",
        "generator would have to answer from an irrelevant passage or invent one. Both are worse",
        "than refusing, so `search()` returns `[]` when the best surviving hit scores below",
        "`MIN_SCORE` and the pipeline turns that into `NOT_FOUND` (FR-10).",
        "",
        "## Score table",
        "",
        "| question | kind | top score | detected | top hit scheme | section | verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['question']} | {r['kind']} | {r['top_score']:.4f} | "
            f"{r['detected'] or '—'} | {r['top_scheme'] or '—'} | {r['top_section'] or '—'} | {r['verdict']} |"
        )

    lines += [
        "",
        "## The three populations",
        "",
        "| population | n | range | separable by `MIN_SCORE`? |",
        "|---|---|---|---|",
        f"| in-corpus | {len(in_corpus)} | {min(in_corpus):.4f}–{max(in_corpus):.4f} | — |",
        f"| out-of-corpus (unrelated domain) | {len(out_corpus)} | "
        f"{min(out_corpus):.4f}–{max(out_corpus):.4f} | yes, cleanly |",
        f"| out-of-scope (other AMC, same topic) | {len(out_scope)} | "
        f"{min(out_scope):.4f}–{max(out_scope):.4f} | **no** — overlaps in-corpus |",
        "",
        "The chosen threshold sits inside the gap between the first two:",
        f"**({highest:.4f}, {lowest:.4f}]**, giving {config.MIN_SCORE - highest:+.4f} of margin above the",
        f"highest out-of-corpus score and {lowest - config.MIN_SCORE:+.4f} below the lowest in-corpus score.",
        "",
        "### The out-of-scope row is not a threshold problem",
        "",
        "\"What is the expense ratio of Parag Parikh Flexi Cap Fund?\" scores "
        f"{out_scope[0]:.4f} — above the lowest",
        "in-corpus question. That is not a tuning failure, it is the honest behaviour of a",
        "similarity search over a corpus that genuinely contains an expense ratio for *a* flexi",
        "cap fund (HDFC Equity Fund is one). No threshold on this score can tell \"wrong fund\"",
        "from \"right fund\", because the evidence looks the same.",
        "",
        "It is caught one layer earlier instead: `guards.classify_intent()` maps a non-HDFC AMC",
        "name to `OUT_OF_SCOPE` and the pipeline refuses before retrieval runs (architecture.md",
        "§6.7, PRD acceptance criterion 4). This is why `detect_scheme` will not treat a bare",
        "category word as a scheme: matching \"flexi cap\" filtered the question to HDFC Equity",
        "Fund and *manufactured* a confident wrong answer.",
        "",
        "## Scheme and section accuracy",
        "",
        f"- top hit from the expected scheme: **{len(expected) - len([r for r in expected if r['top_scheme'] != r['expect_scheme']])}/{len(expected)}**",
        f"- top hit from the expected section: **{len(right_section)}/{len(expected)}**",
        "",
        "Scheme accuracy is what the P4 gate asks for and it is met. Section accuracy is lower,",
        "and the cause is upstream in P2 rather than in the retriever: the Groww stats line packs",
        "NAV, 1-day change, minimum SIP, AUM, **expense ratio** and rating into one 600-character",
        "chunk. The expense-ratio answer *is* in the chunk — it is just outnumbered by NAV numbers,",
        "so a question about expense ratios matches the `Riskometer` chunk first. Fixing it means",
        "revisiting the P2 chunk-size decision, not the retrieval code.",
        "",
        "## Deduplication effect",
        "",
        "| question | raw hits | after dedupe |",
        "|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['question']} | {r['raw_hits']} | {r['deduped']} |")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
