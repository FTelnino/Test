# PRD — HDFC Mutual Funds FAQ Assistant (RAG Chatbot)

| Field | Value |
|---|---|
| Document type | Product Requirements Document (PRD) |
| Version | v1.0 |
| Status | Draft — for class demo / milestone brief |
| Date | 2026-09-27 |
| Source | `Problemstatement.txt` |

---

## 1. Problem Statement

Retail investors and support teams repeatedly ask the same factual questions about mutual
fund schemes — expense ratio, exit load, minimum SIP, ELSS lock-in, riskometer, benchmark,
and how to download statements. These answers already exist in public scheme pages,
factsheets and scheme FAQs, but finding them requires reading long documents manually.

A general-purpose LLM can answer these questions, but it **hallucinates** specific numbers
(expense ratios, exit-load slabs) that change over time, and it will happily give investment
advice it should never give. We need a grounded assistant that answers **only** from a curated
set of official public pages, **always** cites a source, and **refuses** advice questions.

**Solution:** a small, well-scoped RAG (Retrieval-Augmented Generation) chatbot over a
corpus of official HDFC Asset Management pages for 5 schemes, built as a working prototype for
a class demo.

---

## 2. Goals & Non-Goals

### Goals

1. Answer factual scheme-level questions using **only** retrieved context from official sources.
2. Attach **one clear citation link** to every answer.
3. Refuse opinionated/portfolio questions politely, with a facts-only message and an educational link.
4. Demonstrate the **full RAG pipeline** end-to-end: Loading → Chunking → Embedding → Vector Store → Retrieval → Generation.
5. Ship a tiny, honest UI with a disclaimer, and a documented README + sample Q&A file.

### Non-Goals

- No investment advice, recommendations, or "should I buy/sell?" answers.
- No return/performance computation or comparison.
- No NAV history, portfolio tracking, transaction execution, or user accounts.
- No PII collection of any kind (PAN, Aadhaar, account numbers, OTPs, emails, phone numbers).
- No multi-AMC scope for v1 (HDFC only).
- No production-scale concerns (auth, rate limiting, horizontal scaling) — this is a demo.

---

## 3. Target Users

| User | Need |
|---|---|
| Retail investor comparing HDFC schemes | Quick, cited factual answers (fees, minimums, tax rules) before reading the factsheet |
| Support / content team | Automate repetitive MF questions with consistent, sourced answers |

---

## 4. Scope

### 4.1 AMC & Schemes (Locked for v1)

**AMC:** HDFC Asset Management (HDFC AMC)
**Plan type:** Direct – Growth for all schemes
**Source platform:** Groww public scheme pages (linked below)

| # | Category | Scheme | URL |
|---|---|---|---|
| 1 | Large Cap | HDFC Large Cap Fund | https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth |
| 2 | Flexi Cap | HDFC Equity Fund | https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth |
| 3 | ELSS | HDFC ELSS Tax Saver Fund | https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth |
| 4 | Small Cap | HDFC Small Cap Fund | https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth |
| 5 | Balanced Advantage (Hybrid) | HDFC Balanced Advantage Fund | https://groww.in/mutual-funds/hdfc-balanced-advantage-fund-direct-growth |

The link list is the authoritative source list deliverable. Where an official HDFC factsheet,
KIM/SID or SEBI/AMFI page is needed to answer a question that Groww does not carry, it may be
added as a supplementary source **only if** it is a public first-party/regulator URL — recorded
in the source list. No third-party blogs ever.

### 4.1a Supplementary regulator sources (added during P1, 2026-09-27)

Ingest testing showed the five Groww pages do **not** carry three fact classes that §11
criterion 2 requires the assistant to answer: the ELSS lock-in period (zero mentions),
the riskometer scale, and how to obtain an account statement or CAS. The first-party AMC
site `hdfcfund.com` returns HTTP 403 to every automated client and `sebi.gov.in` is
unreachable from the project network, so the §6.1 fallback landed on AMFI, the umbrella
body for SEBI-registered mutual funds. Two public AMFI pages were added:

| # | Purpose | Category | URL |
|---|---|---|---|
| 6 | ELSS 3-year lock-in, Sec 80C limit, riskometer levels | Regulator (AMFI) | https://www.amfiindia.com/Themes/Theme1/downloads/InvestorsAwarenessProgrampresentation.pdf |
| 7 | Account statements and Consolidated Account Statement (CAS) | Regulator (AMFI) | https://www.amfiindia.com/investor/become-mf-distributor?zoneName=InvestorService |

These are regulator-grade, not third-party blogs, so they satisfy the §4.1 rule. The
corpus is therefore **7 sources, not 5**; the "5 public pages" figure from the brief is
preserved as the 5 scheme pages. Evidence and the full reachability table are in
`reports/ingest_decisions.md`. Because sources 6–7 are not scheme-specific, they are
tagged `scope=general` and must remain retrievable even when a question names a scheme —
see `architecture.md` §6.5.

### 4.2 In-Scope Question Types

- Expense ratio / TER
- Exit load
- Minimum SIP / minimum lump-sum
- ELSS lock-in period and Section 80C relevance
- Riskometer / risk category
- Benchmark
- How to download statements / capital-gains statements
- Fund objective, category, and AUM (factual framing only)

### 4.3 Out-of-Scope / Must-Refuse

- "Should I buy/sell HDFC Large Cap?" / "Is this a good fund?"
- Portfolio allocation, asset allocation, or tax-planning advice
- Return projections, expected returns, return comparison across schemes
- "Which is the best fund among these five?"
- Anything requiring a personal financial fact about the user (e.g., "my age is 30, what should I buy?")

---

## 5. Product Requirements

### 5.1 Functional Requirements

| ID | Requirement | Priority |
|---|---|---|
| FR-1 | Ingest and clean the 5 source pages, preserving scheme name and page URL as metadata on every chunk | Must |
| FR-2 | Chunk the corpus using a strategy chosen by inspecting the data (see §6.3) | Must |
| FR-3 | Embed all chunks with `sentence-transformers/all-MiniLM-L6-v2` | Must |
| FR-4 | Persist vectors + metadata in ChromaDB, keyed by collection name | Must |
| FR-5 | On a user question: embed the query, retrieve top-k chunks, return chunks + source URL | Must |
| FR-6 | Generate an answer of **≤ 3 sentences** using **only** the retrieved context | Must |
| FR-7 | Show exactly **one citation link** under every answer, linking to the page the answer came from | Must |
| FR-8 | Display a `Last updated from sources: <date>` line on every answer | Must |
| FR-9 | Classify intent; if advice/opinion/ranking/return-related, refuse with a polite facts-only message + educational link | Must |
| FR-10 | If retrieval confidence is low (no relevant chunk found), say so instead of guessing, and suggest an official source | Must |
| FR-11 | Tiny UI: welcome line, 3 example question chips, input box, and the note "Facts-only. No investment advice." | Must |
| FR-12 | PII filter: block and log (without storing) any input matching PAN / Aadhaar / account-number / OTP / email / phone patterns | Must |
| FR-13 | Never compute or compare returns; route return questions to the official factsheet link | Must |
| FR-14 | Allow an optional "show sources" expander revealing the retrieved chunk text for demo transparency | Should |

### 5.2 Non-Functional Requirements

| ID | Requirement |
|---|---|
| NFR-1 | Answer latency ≤ 8 s on a demo laptop (local embeddings, cached model) |
| NFR-2 | Deterministic, re-runnable ingest: re-running the pipeline must not duplicate documents |
| NFR-3 | Works offline after first model download (no paid/external API required) |
| NFR-4 | Zero secrets in the repo; all source URLs listed in a single config file |
| NFR-5 | Code readable by a classmate: one module per pipeline stage, no hidden magic |
| NFR-6 | No PII persisted to disk or logs |

---

## 6. System Design — RAG Pipeline

Each stage is a separate, inspectable module. Data flows one way.

```
  URLs (5)
    │
    ▼
[1] LOADING ─────────► fetch HTML → strip boilerplate/nav → clean text + keep URL
    │
    ▼
[2] CHUNKING ────────► strategy decided by data inspection → chunks + overlap
    │
    ▼
[3] EMBEDDING ───────► all-MiniLM-L6-v2 → 384-dim vectors
    │
    ▼
[4] VECTOR STORE ────► ChromaDB (collection, metadata: scheme, source_url, chunk_id)
    │
    ▼
[5] RETRIEVAL ───────► embed query → top-k similarity search (+ optional rerank/filter)
    │
    ▼
[6] GENERATION ──────► intent guard → facts-only prompt → ≤3 sentence answer
    │
    ▼
[7] CITATION + UI ───► 1 source link + "Last updated from sources:" + disclaimer
```

### 6.1 Stage 1 — Loading

- Input: the 5 URLs from a single `sources.py` / config file (single source of truth).
- Fetch: plain HTTP GET with a real User-Agent and a timeout; no headless browser.
- Extract: strip `script`, `style`, `nav`, `header`, `footer`; normalise whitespace; drop
  cookie/consent banners.
- Output: a list of documents, each `{ scheme, category, source_url, text, fetched_at }`.
- Store raw cleaned text to `data/raw/` so the demo can show what was ingested.

### 6.2 Stage 2 — Chunking Strategy (to be decided by data inspection)

The strategy is **not** fixed in advance. Inspect the ingested text and record the decision
in the README. Decision criteria and the expected outcome:

| Signal observed in the data | Chosen strategy | Rationale |
|---|---|---|
| Long prose paragraphs, no stable table structure, topic drift inside a page | **Recursive character splitting** (~500–800 chars, ~100 overlap) | Safe default; respects paragraph/sentence boundaries without corrupting meaning |
| Pages dominated by tabular data (holdings, fees, exit-load slabs) where row integrity matters | **Structure-aware / semantic split** (split on table rows and heading boundaries, group by section heading) | Keeps a fee row or exit-load slab intact instead of cutting mid-row |
| Each page cleanly segmented by markdown/HTML headings | **Heading-based split, recursed to size** | Scheme name + section heading become metadata, boosting retrieval precision |

Every chunk carries `scheme`, `category`, `section` (heading path), `source_url`, `chunk_id`.
Retrieval adds a short `scheme` filter when the user names a scheme (e.g. "ELSS lock-in?" →
filter to the ELSS scheme), which improves precision noticeably.

### 6.3 Stage 3 — Embedding

- Model: `sentence-transformers/all-MiniLM-L6-v2` (fast, 384-dim, CPU-friendly).
- Model is downloaded once and cached locally; batch-embed chunks at ingest time.
- Same model instance must be used at query time (embeddings must be comparable).

### 6.4 Stage 4 — Vector Store

- ChromaDB, persistent client, single collection (e.g. `hdfc_mf_faq`).
- Document metadata per chunk: `scheme`, `category`, `section`, `source_url`, `chunk_id`, `ingested_at`.
- Embedding function: ChromaDB's default embedding function is **not** used; the
  `all-MiniLM-L6-v2` embeddings computed in Stage 3 are passed in explicitly.
- Re-ingest is idempotent: delete-by-`source_url` before inserting, or upsert by `chunk_id`.

### 6.5 Stage 5 — Retrieval

- Embed the question with the same model; cosine similarity top-k (`k = 4–6`).
- Optional (if time allows, Should-tier): a similarity threshold below which the answer is
  refused as "not found in the corpus", plus a lightweight cross-encoder rerank of the top 10.
- Query preprocessing: if a scheme is named in the question, apply the `scheme` metadata filter.

### 6.6 Stage 6 — Generation & Guardrails

Pipeline order for every turn: **PII check → intent check → retrieve → generate → verify.**

1. **PII check** — regex for PAN (`[A-Z]{5}\d{4}[A-Z]`), Aadhaar (12 digits), account numbers,
   OTPs, emails, phone numbers. If matched: refuse, do not store, do not log the raw value.
2. **Intent check** — a small classifier (keyword/rule-based, or the LLM itself with a strict
   label set) with labels: `FACTUAL` / `ADVICE` / `RETURNS` / `OUT_OF_SCOPE`.
   - `ADVICE` → "I'm a facts-only assistant and can't give investment advice. Here's an
     educational page on how to evaluate a fund: <link>."
   - `RETURNS` → decline to compute; link the official factsheet.
   - `FACTUAL` → proceed to retrieval.
3. **Generate** with a strict system prompt:
   - Use only the provided context; if the answer is not in the context, reply
     "I couldn't find that in the official sources."
   - Hard limit: ≤ 3 sentences.
   - No advice language, no return figures, no predictions, no "you should".
   - Must be able to name the source page it used.
4. **Post-verify** — a cheap check that the answer contains no numeric return/performance
   claim and no advice phrasing; if violated, fall back to the facts-only message.

LLM for generation: any small instruct model available to the team (local via Ollama/
llama.cpp, or a free-tier API). The RAG architecture is identical either way — this is a
deliberate substitution point, documented in the README.

### 6.7 Stage 7 — Citation + UI

The final stage of the query path. It assembles one result object per turn and renders
it. See `architecture.md` §6.9 for the implementation.

- **Exactly one citation link** per answer (FR-7), pointing at the page the answer was
  read from. Never more than one, so provenance is unambiguous.
- **`Last updated from sources: <date>`** on every answer that retrieved evidence, taken
  from the fetch date recorded at ingest. This is what makes a snapshot corpus visible
  rather than silently mistaken for live data.
- **No evidence, no citation.** A pre-retrieval refusal cites nothing, and the date line
  reads *not applicable* rather than printing a label with a blank value.
- **Degradation, not failure.** If generation is unavailable but retrieval succeeded, the
  answer still cites the page so the user can read the facts themselves.
- **The UI renders, it does not decide.** No answer post-processing, no second pipeline
  call, no per-widget filtering. The CLI and the evaluator read the same result object,
  so the screen and the eval table cannot disagree.

---

## 7. UI / UX

Minimal, single-screen. Streamlit (or a single-page FastAPI + HTML app) is sufficient.

```
┌──────────────────────────────────────────────────┐
│  HDFC Mutual Funds FAQ Assistant                  │
│  Facts-only. No investment advice.                │
│                                                  │
│  Ask e.g.                                       │
│   • What is the exit load on HDFC Large Cap?     │
│   • What is the minimum SIP for HDFC ELSS?       │
│   • What is the lock-in period for ELSS?         │
│                                                  │
│  [ Ask a question............................. ]  │
│                                                  │
│  ── answer (≤3 sentences) ──────────────────────  │
│  Source: https://groww.in/...                    │
│  Last updated from sources: 2026-09-27            │
│  [ ▸ show sources ]                              │
└──────────────────────────────────────────────────┘
```

Required elements: welcome line, 3 example questions, input box, the note
**"Facts-only. No investment advice."**, and the same disclaimer in the app footer and README.

---

## 8. Out-of-Scope Behaviours (Refusal Copy)

| Trigger | Response shape |
|---|---|
| Advice / "should I buy" | Facts-only refusal + educational link (e.g. an AMFI/SEBI investor-education page) |
| Return / performance | "I don't compute or compare returns. Please see the official factsheet: <link>" |
| PII submitted | Immediate refusal; value not stored or logged |
| Not found in corpus | "I couldn't find that in the official sources I have." + pointer to the scheme page |
| Out-of-scope AMC (non-HDFC) | Politely state the assistant covers HDFC schemes only |

---

## 9. Deliverables (Submission Checklist)

Verified against real files in P10. Every path below was opened and checked; nothing
here is aspirational.

- [x] **Working prototype** — `app.py`, Streamlit UI. Repo <https://github.com/FTelnino/Test>,
      run with `streamlit run app.py`. No demo video was recorded: the checklist offers
      it only as a fallback for when no link exists, and the prototype is the deliverable.
      8 UI tests in `tests/test_app.py`; screenshot in `docs/screenshot.png` is a real
      captured run.
- [x] **Source list** — `reports/sources.md` and `reports/sources.csv`, generated by
      `scripts/gen_sources_report.py` from `sources.py` plus the ingest report, so it
      cannot drift. Carries scheme, category, scope, type, fetch date, character count
      **and chunk count** (sums to 199, matching the index).
      *Deviation from the checklist wording:* 7 URLs, not 5 — the 5 assigned Groww
      scheme pages plus 2 amfiindia.com pages, added because the Groww pages omit the
      ELSS lock-in, the riskometer scale, and statement guidance. Justified in
      `reports/sources.md` and permitted by §4.1.
- [x] **README** — `README.md`: setup steps, scope (AMC + 5 schemes), architecture
      diagram, the chunking decision and its rationale with the measured comparison
      table, `MIN_SCORE` calibration, 14 known limits, and the exact disclaimer snippet.
      Includes a real UI screenshot and measured latency (5.79 s cold / 0.71 s warm).
- [x] **Sample Q&A file** — `reports/sample_qa.md`: 10 real runs, 6 answered with source
      link and source date, 4 refusals (advice, returns, other AMC, personal data).
      Regenerable with `python cli.py --sample`.
- [x] **Disclaimer snippet** — `DISCLAIMER.md` holds the full text; the UI renders its
      first line via `config.disclaimer_text()`, shown under the title and in the
      footer. Quoted verbatim in `README.md`.
- [x] **PRD** — this document (`docs/PRD.md`).
- [x] **Architecture write-up** — `docs/architecture.md`, stage by stage (§6.1–6.8),
      with the pipeline diagram, data contracts, failure modes, and a PRD-to-component
      traceability matrix (§15). `docs/implementation.md` is the phase-by-phase build log.

### Evidence summary

| Gate | Command | Result | Report |
|---|---|---|---|
| Unit tests | `python -m pytest -q` | 352 passed, offline | — |
| Retrieval + `MIN_SCORE` | `python scripts/probe_retrieval.py` | 10/10; scheme 6/6, section 3/6 | `reports/retrieval_calibration.md` |
| Guards | `python scripts/probe_guards.py` | 49/49 | `reports/guardrail_calibration.md` |
| Generation (live) | `python scripts/probe_generation.py` | all checks held | `reports/generation_gate.md` |
| Pipeline statuses | `python scripts/probe_pipeline.py` | 6/6 | `reports/pipeline_gate.md` |
| End-to-end eval | `python scripts/evaluate.py` | 15/15 | `reports/eval_results.md` |

### Not satisfied

Two PRD criteria are not met and are documented rather than papered over; both are in
`README.md` § Known limits.

- **§11.2 capital-gains statement download** — unsatisfiable from this corpus. All 199
  chunks grep clean for download / how to get / how to request. The assistant returns
  `NOT_FOUND` rather than inventing instructions.
- **Section accuracy 3/6** — a P2 chunking limitation, not a retrieval bug. The right
  chunk is usually still retrieved at rank #2, so answers survive, but the citation
  line can name the wrong section.

---

## 10. Milestones

| # | Milestone | Output | Estimate |
|---|---|---|---|
| M0 | Scope lock + PRD | This PRD, `sources.py` with the 5 URLs | 0.5 day |
| M1 | Ingest pipeline (Loading) | Cleaned docs for 5 pages + `data/raw/` dump | 0.5 day |
| M2 | Chunking + Embedding + ChromaDB | Persistent vector store, chunking rationale documented | 1 day |
| M3 | Retrieval + Generation | CLI that answers a question with a citation | 1 day |
| M4 | Guardrails | Intent refusal, PII filter, ≤3-sentence + last-updated enforcement | 0.5 day |
| M5 | UI | Streamlit screen with welcome line, 3 examples, disclaimer | 0.5 day |
| M6 | Docs & samples | README, source list, sample Q&A, demo recording | 0.5 day |

Total ≈ 4–5 days of work for a 2–3 person team, or ~1 week solo.

---

## 11. Acceptance Criteria

The demo is a pass if all of the following hold:

1. All 5 schemes are ingested; the ChromaDB collection is non-empty and rebuildable from
   scratch with one command.
2. Each of these questions returns a ≤3-sentence answer with a working citation link:
   expense ratio, exit load, minimum SIP, ELSS lock-in, riskometer/benchmark,
   how to download a capital-gains statement.
3. "Should I buy HDFC Small Cap?" is refused with the facts-only message and no advice.
4. A question with no answer in the corpus is refused rather than invented.
5. A PAN number submitted as input is rejected and never stored.
6. Every answer shows `Last updated from sources: <date>`.
7. The README documents the chunking strategy decision and its rationale.
8. The source list contains only the approved public URLs: the 5 HDFC scheme pages
   plus the 2 AMFI regulator pages added in P1 to cover ELSS lock-in, the
   riskometer, and account statements/CAS, each with a recorded reason in
   `reports/ingest_decisions.md`. No other host may appear in `sources.py`.

---

## 12. Tech Stack

| Layer | Choice | Note |
|---|---|---|
| Ingestion | `requests` + `beautifulsoup4` (or `trafilatura`) | No headless browser needed |
| Chunking | `langchain-text-splitters` or hand-rolled recursive splitter | Strategy decided in M2 |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` | Fixed by brief |
| Vector DB | `chromadb` (persistent) | Fixed by brief |
| Retrieval | Chroma similarity query + optional rerank | — |
| Generation | Local instruct LLM (Ollama) or free-tier API | Substitution point |
| Orchestration | Plain Python pipeline, one module per stage | No framework lock-in |
| UI | Streamlit | Fastest path to demo |
| Env/config | `.env` + `config.py` | No secrets in repo |

---

## 13. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Groww pages are JS-rendered; `requests` returns an empty shell | Ingest produces no text | Fall back to a static-text extractor or first-party HDFC factsheet PDFs; validate text length at ingest and fail loudly |
| Fee/rate data changes after ingest | Stale answers | Show "Last updated from sources"; document re-ingest as a one-command step |
| Hallucinated numbers slipping through | Factual error, worst failure mode | Grounded prompt + numeric/advice post-check + mandatory citation + threshold refusal |
| all-MiniLM retrieval returns the wrong scheme | Wrong citation | Scheme metadata filter when a scheme is named; rerank if time allows |
| LLM unavailable on demo machine | Demo fails | Cache model locally; ship a recorded video as the guaranteed fallback deliverable |

---

## 14. Known Limits (to state in the README)

1. Single AMC (HDFC) and 5 schemes only — not a market-wide assistant.
2. Snapshot corpus: answers reflect the source pages at ingest time, not live data.
3. No NAV, returns, or performance data at all, by design.
4. Retrieval quality depends on the chunking decision; small chunks lose context,
   large chunks dilute the embedding.
5. all-MiniLM-L6-v2 is a general-purpose sentence encoder, not finance-tuned.
6. Cost/NAV figures are read from public pages and are not independently verified.
7. The intent classifier is rule-based and will miss phrased advice questions.

---

## 15. Glossary

| Term | Meaning |
|---|---|
| AMC | Asset Management Company |
| RAG | Retrieval-Augmented Generation — retrieve relevant context, then generate an answer from it |
| Chunk | A slice of a source page small enough to embed and retrieve precisely |
| Embedding | A numeric vector representing text meaning, used for similarity search |
| ELSS | Equity Linked Savings Scheme — 80C tax benefit, 3-year lock-in |
| KIM / SID | Key Information Memorandum / Statement of Additional Information |
| Exit load | Fee charged on redeeming/selling units before the stated period |
| Riskometer | SEBI-mandated risk level indicator, published by the AMC |
| Benchmark | The index a scheme is measured against |
| Direct–Growth | Plan where fees are paid from the fund, no distributor commission, no commission to investor |
| TER | Total Expense Ratio — the yearly cost of running the scheme, as a % of assets |
