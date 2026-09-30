# Source list

Generated 2026-09-27 by `scripts/gen_sources_report.py` from `sources.py`. Do not hand-edit.

7 documents ingested, 0 failed, 101047 characters total, 199 chunks (`table_aware`).

## Sources

| # | Scheme | Category | Scope | Type | Fetched | Chars | Chunks | URL |
|---|---|---|---|---|---|---|---|---|
| 1 | HDFC Large Cap Fund | Large Cap | scheme | html | 2026-09-27 | 5617 | 11 | <https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth> |
| 2 | HDFC Equity Fund | Flexi Cap | scheme | html | 2026-09-27 | 7999 | 16 | <https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth> |
| 3 | HDFC ELSS Tax Saver Fund | ELSS | scheme | html | 2026-09-27 | 6072 | 12 | <https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth> |
| 4 | HDFC Small Cap Fund | Small Cap | scheme | html | 2026-09-27 | 7897 | 16 | <https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth> |
| 5 | HDFC Balanced Advantage Fund | Balanced Advantage | scheme | html | 2026-09-27 | 28958 | 53 | <https://groww.in/mutual-funds/hdfc-balanced-advantage-fund-direct-growth> |
| 6 | AMFI Investor Awareness Programme | Regulator (AMFI) | general | pdf | 2026-09-27 | 27190 | 55 | <https://www.amfiindia.com/Themes/Theme1/downloads/InvestorsAwarenessProgrampresentation.pdf> |
| 7 | AMFI Account Statements and CAS | Regulator (AMFI) | general | html | 2026-09-27 | 17314 | 36 | <https://www.amfiindia.com/investor/become-mf-distributor?zoneName=InvestorService> |

## Provenance and rules

- 5 scheme pages on groww.in, Direct-Growth plans, as assigned.
- 2 pages on amfiindia.com, the Association of Mutual Funds in India, added in P1
  because the Groww scheme pages do not carry ELSS lock-in, the riskometer
  scale, or statement guidance, which PRD section 11 criterion 2 requires the
  assistant to answer. PRD section 4.1 permits supplementary sources only when
  they are public first-party or regulator URLs.
- No third-party blogs, aggregators, or SEO content sites are used.
- hdfcfund.com and sebi.gov.in were rejected as sources because neither is
  reachable by an automated fetch from this network; see
  `reports/ingest_decisions.md`.

## Refresh

```bash
python run_ingest.py --fetch-only
python scripts/gen_sources_report.py
```

Answers show `Last updated from sources:` from the fetch date above, so a
stale corpus is visible to the user rather than silent.
