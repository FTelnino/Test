# P1 Ingest Decisions — Loading stage

Date: 2026-09-27
Phase: P1 (stage 1, loading)
Answers: Q1 from `architecture.md` §16, plus two deviations from `implementation.md` P1
Status: gate passed, 7/7 sources ingested (17/17 after P15)

---

## 1. Q1: does a static HTTP GET extract usable text from the 5 scheme pages?

**Yes.** `scripts/fetch_probe.py` fetched all five Groww URLs and extracted real content.

| Scheme | HTTP | HTML bytes | trafilatura chars | Verdict |
|---|---|---|---|---|
| HDFC Large Cap Fund | 200 | 453,470 | 5,841 | usable |
| HDFC Equity Fund | 200 | 493,789 | 8,223 | usable |
| HDFC ELSS Tax Saver Fund | 200 | 452,129 | 6,296 | usable |
| HDFC Small Cap Fund | 200 | 496,464 | 8,120 | usable |
| HDFC Balanced Advantage Fund | 200 | 815,225 | 29,182 | usable |

So the Groww pages are not JS-rendered shells, and the original PRD risk 1 did not
materialise for the assigned sources.

## 2. Deviation: the extractor is not "whichever result is longer"

`implementation.md` P1 task 3 said to return the longer of the trafilatura and
BeautifulSoup results. That rule is wrong for these pages, and the probe showed why:

| Extractor | Chars (Large Cap) | First 120 chars of output |
|---|---|---|
| trafilatura | 5,841 | `+8.71% 3Y annualised +0.14% 1D NAV: 25 Sep '26 Rs.1,189.08 Min. for SIP Rs.100 Fund size (AUM) Rs.39,933.37 Cr Expense ratio 1.03%` |
| bs4 `get_text` | 17,677 | `HDFC Large Cap Fund Direct Growth - NAV ... Invest in Stocks Intraday Monitor top intraday performers ETF Screener ... Demat Account Begin your stock market journey` |

The BeautifulSoup result is three times longer and almost entirely site-wide navigation.
Length is not a quality signal here.

**Decision:** trafilatura is the primary extractor; BeautifulSoup is a fallback used only
when trafilatura returns less than `MIN_TEXT_CHARS`. Both are still computed so
`_ingest_report.json` can show what happened. Implemented in `rag/loader.py::_extract_html`,
and pinned by `test_extract_html_prefers_trafilatura_over_longer_soup_text`.

Bonus: trafilatura preserved the page tables as markdown, which is what the table-aware
chunking strategy in P2 depends on. BeautifulSoup would have flattened them.

## 3. Deviation: two regulator sources were added, so the corpus is 7 sources not 5

After ingesting the five pages, the corpus was checked against every fact class that
`PRD.md` §11 criterion 2 requires the assistant to answer. Three were absent:

| Fact | In the Groww pages? |
|---|---|
| expense ratio | yes |
| exit load | yes, 4 of 5 schemes (ELSS has none, correctly) |
| minimum SIP | yes |
| benchmark | yes |
| ELSS lock-in period | **no, zero mentions on any page** |
| riskometer scale and levels | **no** (only the scheme's own "Very High risk" label) |
| how to get an account statement / CAS | **no** |

The fallback order in `architecture.md` §6.1 is HDFC factsheet, then SEBI/AMFI. Reachability
was measured first, because the first choice was not usable:

| Host | Result | Verdict |
|---|---|---|
| groww.in | 200, extractable | in corpus |
| hdfcfund.com (first-party AMC) | **403 for every client**, including a full browser header set and the `webfetch` tool | unusable |
| sebi.gov.in / investor.sebi.gov.in | **connection timeout** at a network middlebox, not a server error | unusable from this network |
| hdfcmf.com (legacy AMC domain) | 200, but a parked domain with a cookie-consent script | dead end |
| amfiindia.com | 200, extractable, 27k chars from PDF and 17k from HTML | in corpus |

AMFI is the umbrella body for all SEBI-registered mutual funds, so its material is
regulator-grade rather than a third party, which satisfies the `PRD.md` §4.1 rule that
supplementary sources must be first-party or regulator URLs.

**Decision:** two AMFI sources were added, both public and first-party:

1. `https://www.amfiindia.com/Themes/Theme1/downloads/InvestorsAwarenessProgrampresentation.pdf`
   — carries "Shortest lock-in period of 3 years", "Deduction from taxable income of up to
   Rs.1,50,000 under Sec 80C", and the riskometer level definitions
   (Low / Low to Moderate / Moderate / Moderately High / High / Very High).
2. `https://www.amfiindia.com/investor/become-mf-distributor?zoneName=InvestorService`
   — carries Consolidated Account Statement guidance, including the once-a-month CAS and the
   five-working-day statement rule.

After adding them, all eight fact classes are present. Verified by regex over
`data/raw/*.txt`.

**Consequence for the architecture.** These two sources are not tied to one scheme, so
`Source` gained a `scope` field: `scheme` for the five scheme pages, `general` for the
regulator pages. This is load-bearing, not cosmetic. `architecture.md` §6.5 plans a
`scheme` metadata filter when the user names a scheme, so "What is the lock-in period for
HDFC ELSS?" would filter to the Groww ELSS page — the one page that never states the lock-in
— and the correct answer would be unreachable. Retrieval must therefore filter with
`{"$or": [{"scheme": X}, {"scope": "general"}]}`. Pinned by
`test_general_sources_exist_so_scheme_filtering_cannot_hide_them`.

## 4. Cleaning decisions

`implementation.md` P1 task 4 required dropping consent and navigation. Two things the
task did not anticipate:

- **trafilatura merges nav blurbs into prose.** Dropping them line-by-line left the fragment
  "Invest in stocks, ETFs, IPOs with fast orders. Track returns on your stock holdings and
  view real-time P&L on your positions." embedded in the first line of every scheme page. It
  is now removed as complete substrings. Partially removing such a phrase leaves a dangling
  connective ("and ."), which a unit test caught during this phase, so the entries are whole
  phrases by design. This blurb also injects return language into the corpus, which is exactly
  what the PRD forbids the assistant from asserting, so removing it is a guardrail concern
  and not only tidiness.
- **PDF text needs a different shape.** HTML keeps table rows and list items on their own
  lines so P2's table-aware splitter can protect a fee row. The AMFI PDF is slide text with
  hard layout wraps, so it is collapsed to prose. `clean_text(raw, preserve_structure=False)`
  handles that branch.

## 5. Known issue carried into P2

The Groww pages contain a peer-comparison table with one-year, three-year, five-year and
ten-year return figures for competing funds, alongside absolute rupee return projections
("would've become Rs.1,83,700"). This is real content from the source page, so it stays in
the corpus, but it is a standing temptation for the generator to produce a return
comparison, which `PRD.md` §2 forbids.

P2 should note it when choosing chunking. P6's verifier and the retrieval of a single rank-1
citation are the real defences; a return question must be intercepted by the intent gate
before generation.

## 6. Gate result

```
python run_ingest.py --fetch-only     ->  exit 0, 7/7 ingested, 101,004 chars
python -m pytest tests/test_loader.py ->  20 passed
```

Idempotency check: two consecutive runs produced identical `text_hash` values for all seven
sources and identical character counts, so re-ingest cannot produce a divergent corpus.

Quality check: the largest fact block in `data/raw/hdfc-large-cap-fund.txt` reads "The HDFC
Large Cap Fund Direct Growth is rated Very High risk. Minimum SIP Investment is set to
Rs.100. Minimum Lumpsum Investment is Rs.100. Exit load of 1% if redeemed within 1 year...
Fund benchmark NIFTY 100 Total Return Index", which is the kind of passage the assistant
needs to cite in the demo.

## 7. Open items for the next phase

- P2 must record that the corpus is now **mixed**: section-structured and tabular
  (Groww) alongside run-on slide prose (AMFI PDF). A single strategy tuned for one of the two
  will underperform on the other.
- P3 must not use Chroma's default embedding function, and must carry `scope` into chunk
  metadata so the P4 filter can use it.
- P4 must implement the `$or` scheme/general filter described in §3.
