# Source list

Generated 2026-10-01 by `scripts/gen_sources_report.py` from `sources.py`. Do not hand-edit.

17 documents ingested, 0 failed, 206885 characters total, 407 chunks (`table_aware`).

## Sources

| # | Scheme | Category | Scope | Type | Fetched | Chars | Chunks | URL |
|---|---|---|---|---|---|---|---|---|
| 1 | HDFC Large Cap Fund | Large Cap | scheme | html | 2026-10-01 | 5614 | 11 | <https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth> |
| 2 | HDFC Equity Fund | Flexi Cap | scheme | html | 2026-10-01 | 7997 | 16 | <https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth> |
| 3 | HDFC ELSS Tax Saver Fund | ELSS | scheme | html | 2026-10-01 | 6072 | 12 | <https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth> |
| 4 | HDFC Small Cap Fund | Small Cap | scheme | html | 2026-10-01 | 7896 | 16 | <https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth> |
| 5 | HDFC Balanced Advantage Fund | Balanced Advantage | scheme | html | 2026-10-01 | 28956 | 53 | <https://groww.in/mutual-funds/hdfc-balanced-advantage-fund-direct-growth> |
| 6 | HDFC Mid-Cap Opportunities Fund | Mid Cap | scheme | html | 2026-10-01 | 7542 | 14 | <https://groww.in/mutual-funds/hdfc-mid-cap-opportunities-fund-direct-growth> |
| 7 | HDFC Value Fund | Value | scheme | html | 2026-10-01 | 7059 | 14 | <https://groww.in/mutual-funds/hdfc-value-fund-direct-plan-growth> |
| 8 | HDFC Liquid Fund | Liquid | scheme | html | 2026-10-01 | 16532 | 32 | <https://groww.in/mutual-funds/hdfc-liquid-fund-direct-growth> |
| 9 | HDFC Ultra Short Term Fund | Ultra Short Duration | scheme | html | 2026-10-01 | 14280 | 27 | <https://groww.in/mutual-funds/hdfc-ultra-short-term-fund-direct-growth> |
| 10 | HDFC Banking and PSU Debt Fund | Banking and PSU Debt | scheme | html | 2026-10-01 | 12618 | 25 | <https://groww.in/mutual-funds/hdfc-banking-and-psu-debt-fund-direct-growth> |
| 11 | HDFC Credit Risk Fund | Credit Risk | scheme | html | 2026-10-01 | 12875 | 25 | <https://groww.in/mutual-funds/hdfc-credit-risk-fund-direct-growth> |
| 12 | HDFC Medium Term Fund | Medium Term | scheme | html | 2026-10-01 | 8643 | 18 | <https://groww.in/mutual-funds/hdfc-medium-term-fund-direct-growth> |
| 13 | HDFC Gilt Fund | Gilt | scheme | html | 2026-10-01 | 6001 | 13 | <https://groww.in/mutual-funds/hdfc-gilt-fund-direct-growth> |
| 14 | HDFC Nifty Midcap 150 Index Fund | Index | scheme | html | 2026-10-01 | 11655 | 23 | <https://groww.in/mutual-funds/hdfc-nifty-midcap-150-index-fund-direct-growth> |
| 15 | HDFC Nifty 100 Index Fund | Index | scheme | html | 2026-10-01 | 8641 | 17 | <https://groww.in/mutual-funds/hdfc-nifty-100-index-fund-direct-growth> |
| 16 | AMFI Investor Awareness Programme | Regulator (AMFI) | general | pdf | 2026-10-01 | 27190 | 55 | <https://www.amfiindia.com/Themes/Theme1/downloads/InvestorsAwarenessProgrampresentation.pdf> |
| 17 | AMFI Account Statements and CAS | Regulator (AMFI) | general | html | 2026-10-01 | 17314 | 36 | <https://www.amfiindia.com/investor/become-mf-distributor?zoneName=InvestorService> |

## Provenance and rules

- 15 scheme pages on groww.in, Direct-Growth plans, as assigned.
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
