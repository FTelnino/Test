"""Regenerate the source-list deliverable from sources.py + the ingest report.

    python scripts/gen_sources_report.py

Never hand-typed: the report cannot drift from the corpus.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from sources import load_sources


def main() -> int:
    report_path = config.RAW_DIR / "_ingest_report.json"
    if not report_path.exists():
        print("no ingest report yet; run: python run_ingest.py --fetch-only")
        return 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    by_url = {entry["url"]: entry for entry in report["sources"]}

    # Chunk counts come from the chosen strategy's persisted output, which is the
    # exact chunk set that produced the indexed vectors. Re-running the chunker
    # here could drift from the index if the splitter changed after ingestion.
    chunk_counts = {}
    chunks_path = config.RAW_DIR / f"chunks_{config.CHUNK_STRATEGY}.json"
    if chunks_path.exists():
        for chunk in json.loads(chunks_path.read_text(encoding="utf-8")):
            url = chunk.get("source_url", "")
            chunk_counts[url] = chunk_counts.get(url, 0) + 1

    rows = []
    for source in load_sources():
        entry = by_url.get(source.url, {})
        rows.append(
            {
                "scheme": source.scheme,
                "category": source.category,
                "url": source.url,
                "scope": source.scope,
                "content_type": source.content_type,
                "fetched_at": entry.get("fetched_at", ""),
                "chars": entry.get("chars", 0),
                "chunks": chunk_counts.get(source.url, 0),
                "text_hash": entry.get("text_hash", "") or "",
            }
        )

    lines = [
        "# Source list",
        "",
        f"Generated {report['fetched_at']} by `scripts/gen_sources_report.py` "
        f"from `sources.py`. Do not hand-edit.",
        "",
        f"{report['documents']} documents ingested, {report['failed']} failed, "
        f"{report['total_chars']} characters total, "
        f"{sum(row['chunks'] for row in rows)} chunks "
        f"(`{config.CHUNK_STRATEGY}`).",
        "",
        "## Sources",
        "",
        "| # | Scheme | Category | Scope | Type | Fetched | Chars | Chunks | URL |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for index, row in enumerate(rows, start=1):
        lines.append(
            f"| {index} | {row['scheme']} | {row['category']} | {row['scope']} | "
            f"{row['content_type']} | {row['fetched_at']} | {row['chars']} | "
            f"{row['chunks']} | <{row['url']}> |"
        )
    lines += [
        "",
        "## Provenance and rules",
        "",
        "- 5 scheme pages on groww.in, Direct-Growth plans, as assigned.",
        "- 2 pages on amfiindia.com, the Association of Mutual Funds in India, added in P1",
        "  because the Groww scheme pages do not carry ELSS lock-in, the riskometer",
        "  scale, or statement guidance, which PRD section 11 criterion 2 requires the",
        "  assistant to answer. PRD section 4.1 permits supplementary sources only when",
        "  they are public first-party or regulator URLs.",
        "- No third-party blogs, aggregators, or SEO content sites are used.",
        "- hdfcfund.com and sebi.gov.in were rejected as sources because neither is",
        "  reachable by an automated fetch from this network; see",
        "  `reports/ingest_decisions.md`.",
        "",
        "## Refresh",
        "",
        "```bash",
        "python run_ingest.py --fetch-only",
        "python scripts/gen_sources_report.py",
        "```",
        "",
        "Answers show `Last updated from sources:` from the fetch date above, so a",
        "stale corpus is visible to the user rather than silent.",
    ]
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (config.REPORTS_DIR / "sources.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    header = "scheme,category,scope,content_type,fetched_at,chars,chunks,text_hash,url"
    csv_lines = [header]
    for row in rows:
        csv_lines.append(
            ",".join(
                [
                    '"{}"'.format(row["scheme"].replace('"', '""')),
                    '"{}"'.format(row["category"]),
                    row["scope"],
                    row["content_type"],
                    row["fetched_at"],
                    str(row["chars"]),
                    str(row["chunks"]),
                    row["text_hash"],
                    row["url"],
                ]
            )
        )
    (config.REPORTS_DIR / "sources.csv").write_text(
        "\n".join(csv_lines) + "\n", encoding="utf-8"
    )

    print(f"wrote {config.REPORTS_DIR / 'sources.md'}")
    print(f"wrote {config.REPORTS_DIR / 'sources.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
