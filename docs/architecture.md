# ARCHITECTURE — HDFC Mutual Funds FAQ Assistant (RAG Chatbot)

| Field | Value |
|---|---|
| Document type | Technical Architecture Document |
| Version | v1.0 |
| Status | Draft — companion to `PRD.md` v1.0 |
| Date | 2026-09-27 |
| Audience | Team implementing the demo; reviewers grading the pipeline walkthrough |

This document turns the requirements in `PRD.md` into a concrete system: module boundaries,
data contracts, runtime flows, design decisions with trade-offs, failure handling, and a
test strategy. It does not restate product intent — see `PRD.md` for that.

---

## 1. Scope of This Document

**In scope:** component design, data flow, interfaces, storage layout, retrieval strategy,
prompt/guardrail design, failure modes, testing, observability, extension points.

**Out of scope:** product requirements (`PRD.md`), UI visual design (PRD §7), deployment
infrastructure, multi-tenancy, anything about returns or advice (explicitly excluded by
PRD §2).

---

## 2. Design Principles

| # | Principle | Consequence in the design |
|---|---|---|
| P1 | **One module per pipeline stage** | Stages are importable and independently testable; a classmate can read one file per arrow in the diagram |
| P2 | **Plain Python, no framework lock-in** | No LangChain agent magic. Retrieval and prompt assembly are explicit code, so the demo can explain every line |
| P3 | **Grounding is structural, not requested** | The generator physically cannot see text outside retrieved chunks; advice and returns are intercepted before generation |
| P4 | **Fail closed, not open** | No evidence → refusal, never a guess. Uncertain retrieval → "not found", not a plausible sentence |
| P5 | **Metadata is first-class** | Every chunk carries scheme/section/URL so filtering, citations, and freshness are possible without re-parsing text |
| P6 | **Determinism and idempotency** | Same corpus + same model → same index. Re-ingest never duplicates; chunk IDs are content-addressed |
| P7 | **Demo-safe** | Local models, cached, offline-capable; a recorded video is the guaranteed fallback |

---

## 3. System Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              PRESENTATION LAYER                              │
│                     Streamlit single screen (app.py)                         │
│  welcome line · 3 fixed chips · input box · "Facts-only. No advice." note     │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    │  user question (str)
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                              ORCHESTRATION                                  │
│                            pipeline.answer(query)                            │
│   1 PII gate → 2 intent gate → 3 retrieve → 4 generate → 5 verify → 6 render  │
└──┬──────────────┬───────────────┬───────────────┬───────────────┬────────────┘
   │              │               │               │               │
   ▼              ▼               ▼               ▼               ▼
 guards.py    guards.py       retriever.py    generator.py     verifier.py
 (regex PII)  (intent label)   (top-k+filter)  (LLM call)      (rule check)
                                   │
                    ┌──────────────┴──────────────┐
                    ▼                             ▼
            embedder.py (query)          chroma (persistent)
                    │                             ▲
                    └────────► vector store ◄─────┘
                                 (chroma)
                                    ▲
                                    │  ingest path (offline, one command)
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
            embedder.py (batch)              chromadb store
                    ▲                               ▲
                    │                               │
              chunker.py ◄─── loader.py ◄─── sources.py (5 URLs)
              (strategy from                    (single source of truth)
               chunking_decision.md)
```

### 3.1 Two Execution Paths

| Path | When | Stages | Output |
|---|---|---|---|
| **Ingest (build)** | Once, or on demand to refresh sources | Loading → Chunking → Embedding → Vector Store | Persistent ChromaDB collection |
| **Query (serve)** | Per user question | Guards → Retrieval → Generation → Verify → Render | Answer + 1 citation + last-updated date |

The two paths share `embedder.py` and the ChromaDB collection, and nothing else. This
separation is what makes the build reproducible and the query path fast.

---

## 4. Repository Layout

```
.
├── docs/
│   ├── PRD.md                      product requirements
│   ├── architecture.md             this document
│   └── implementation.md           phase-by-phase build log
├── README.md                   setup, scope, chunking rationale, known limits
├── DISCLAIMER.md               exact disclaimer text; the UI shows its first line
├── requirements.txt            pinned dependencies
├── .env.example                keys, if an API LLM is used (no real values)
├── run_ingest.py               entrypoint: build the index (one command)
├── app.py                      Streamlit UI (§6.9)
├── cli.py                      terminal Q&A, used for debugging and sample generation
├── config.py                   all tunables + paths
├── sources.py                  the approved URLs (single source of truth)
├── rag/
│   ├── __init__.py
│   ├── loader.py               [1] LOADING
│   ├── chunker.py              [2] CHUNKING
│   ├── embedder.py             [3] EMBEDDING (shared by both paths)
│   ├── vectorstore.py          [4] VECTOR STORE
│   ├── retriever.py            [5] RETRIEVAL
│   ├── generator.py            [6] GENERATION
│   ├── verifier.py             post-generation rule checks
│   ├── guards.py               PII + intent gates
│   ├── pipeline.py             orchestration, renders the final Answer
│   └── prompts.py              system prompt + refusal copy, all in one place
├── data/
│   ├── raw/                    cleaned page text, one .txt per source + fetched_at
│   ├── chroma/                 persistent ChromaDB directory
│   └── eval/                   golden questions + expected behaviour
├── reports/
│   ├── chunking_decision.md    data inspection notes → chosen strategy + rationale
│   ├── sample_qa.md            deliverable: 5–10 queries with answers + links
│   └── sources.md / sources.csv  deliverable source list
└── scripts/                    gate probes, evaluator, report generators
```

One file per arrow in the diagram. `rag/pipeline.py` holds the ordering and nothing else.

---

## 5. Data Contracts

These types are the contract between stages. They are plain dataclasses (or TypedDicts) —
no ORM, no framework models.

```python
@dataclass
class Source:
    scheme: str            # "HDFC Large Cap Fund", or a descriptor for general sources
    category: str          # "Large Cap", or "Regulator (AMFI)"
    url: str
    slug: str
    url_type: str = "scheme_page"    # scheme_page | factsheet | regulator
    scope: str = "scheme"            # scheme | general. See 6.5: general sources
                                      # must survive the scheme metadata filter
    content_type: str = "html"       # html | pdf

@dataclass
class Document:
    source: Source
    text: str
    fetched_at: str        # ISO-8601 date, feeds "Last updated from sources"
    text_hash: str         # content hash, for idempotency and staleness checks

@dataclass
class Chunk:
    chunk_id: str          # f"{scheme_slug}:{text_hash[:8]}:{ordinal}"  content-addressed
    text: str
    scheme: str
    category: str
    scope: str             # carried from Source so retrieval can filter on it
    section: str           # heading path, e.g. "Fees > Exit load"
    source_url: str
    ordinal: int
    token_estimate: int

@dataclass
class RetrievedChunk:
    chunk: Chunk
    score: float
    rank: int

@dataclass
class Answer:
    query: str
    answer: str            # <= 3 sentences, or refusal copy
    status: str            # ANSWERED | REFUSED_ADVICE | REFUSED_RETURNS | REFUSED_PII | NOT_FOUND | OUT_OF_SCOPE
    citation_url: str | None
    last_updated: str
    evidence: list[RetrievedChunk]
```

`status` is the single field the UI switches on, and it is also what the eval script asserts
against. Keeping it explicit means refusal behaviour is testable rather than eyeballed.

---

## 6. Stage Specifications

### 6.1 Stage 1 — Loading (`rag/loader.py`)

| | |
|---|---|
| **Input** | `list[Source]` from `sources.py` |
| **Output** | `list[Document]` + files in `data/raw/` |
| **Config** | `HTTP_TIMEOUT`, `USER_AGENT`, `MIN_TEXT_CHARS`, `EXTRACTOR` (`trafilatura` or `bs4`) |

Flow per URL:

1. `GET` with a real `User-Agent`, explicit timeout, and a single retry. No headless browser.
2. Extract main content: prefer `trafilatura` (boilerplate removal is its job); fall back to
   `beautifulsoup4` with `script/style/nav/header/footer/aside` stripped.
3. Normalise: collapse whitespace, drop cookie/consent copy, drop zero-width characters,
   preserve line breaks only where they separate table rows or bullet items.
4. Attach `fetched_at` (today, at ingest) and `text_hash`.
5. **Validate before persisting:** if `len(text) < MIN_TEXT_CHARS`, raise a named
   `IngestError` for that URL and record it in `data/raw/_ingest_report.json`.

**Why the validation matters:** PRD risk #1 — a JS-rendered page returns a 200 with an empty
shell. A silent empty document would produce a confident, wrong "I couldn't find that" for
every question, and the demo would die with no visible cause. The ingest command must fail
loudly, listing which URLs produced no text, and the README documents the fallback
(first-party HDFC factsheet PDFs, added to `sources.py` with their own URL).

Fallback order if static fetch fails, in priority:

1. `trafilatura` on the raw HTML.
2. HDFC AMC factsheet PDF for that scheme (first-party, added to `sources.py`).
3. AMFI/SEBI public page (regulator, recorded in the source list).

**Resolved during P1 (see `reports/ingest_decisions.md`).** All 5 Groww pages extract
cleanly, so fallback 1 held. The Groww pages omit ELSS lock-in, the riskometer scale, and
statement guidance, so fallback 3 was used: two AMFI pages were added, taking the corpus
to 7 sources. P15 added 10 further Groww scheme pages for the same reason
fallback 1 did not suffice -- broader category coverage, now 17 sources.

**Extractor choice is evidence-based, and it is not "longest result wins".** On the same
page, trafilatura returned 5,841 chars of scheme content with tables preserved as markdown,
while `BeautifulSoup.get_text` returned 17,677 chars beginning with the site-wide product
menu. So: **trafilatura is primary; BeautifulSoup is a fallback used only when trafilatura
returns less than `MIN_TEXT_CHARS`.** The longer result is usually the navigation. Computing
both and reporting which was used is still worthwhile for the ingest report.

`Source` carries `content_type` (`html` | `pdf`). The PDF branch uses `pypdf` and collapses
to prose, because PDF text arrives hard-wrapped from page layout rather than as rows.

`Source` also carries `scope` (`scheme` | `general`), which is load-bearing: see §6.5.

### 6.2 Stage 2 — Chunking (`rag/chunker.py`)

| | |
|---|---|
| **Input** | `list[Document]` |
| **Output** | `list[Chunk]` |
| **Config** | `strategy`, `chunk_size`, `chunk_overlap`, `min_chunk_chars` |

**The strategy is a decision, made from data, and written down** (PRD FR-2). The code
supports three strategies behind one interface so the decision is a config value, not a
rewrite:

```python
class ChunkStrategy(Protocol):
    name: str
    def split(self, doc: Document) -> list[Chunk]: ...
```

Implementations: `RecursiveSplitter` (separator ladder: headings → blank line → sentence →
word, then recursive merge to size), `SectionSplitter` (heading-path based, recursed to size),
`TableAwareSplitter` (keeps `<tr>`/fee slabs intact, never splits a row).

**Decision procedure** (executed in M2, output committed to `reports/chunking_decision.md`):

1. Dump per-document stats: char count, heading structure, table density (rows/1000 chars),
   sentence-length distribution, presence of boilerplate residue.
2. Sample 3 chunks per strategy on one Large Cap doc and one ELSS doc.
3. Score each strategy on: does a fee/exit-load figure stay intact with its label? is
   `section` metadata meaningful? does the chunk still read as a complete thought?
4. Pick one, document the reason and the rejected alternatives.

Because Groww scheme pages are tabular and section-structured, `SectionSplitter` with
`TableAwareSplitter` row protection was the predicted winner; `chunk_size ≈ 600` chars and
`chunk_overlap ≈ 100` are the starting point, and overlap exists so an exit-load slab split
across a boundary is still retrievable from either side.

**Measured outcome (P2, `reports/chunking_decision.md`):** the prediction was half right.
The corpus turned out to have **zero headings** in all 7 documents, so `SectionSplitter`
produced output **byte-identical to `RecursiveSplitter`** and inherited its table damage
without adding anything. Plain `TableAwareSplitter` won on the evidence: table rows intact
691/700 (98.7%) vs 639/727 (87.9%), orphan row fragments 7 vs 55, same prose-fact
retention. `chunk_size=600` and `chunk_overlap=100` were kept as predicted.

Post-chunk invariants asserted in code and in the ingest report:

- every chunk has non-empty `section` and `source_url`
- no chunk exceeds `chunk_size` by more than the tolerance
- chunks with fewer than `min_chunk_chars` characters are dropped (nav crumbs, "read more")
- `chunk_id` is unique across the corpus

### 6.3 Stage 3 — Embedding (`rag/embedder.py`)

| | |
|---|---|
| **Input** | `list[str]` (chunk texts, or one query) |
| **Output** | `list[list[float]]` — 384-dim |
| **Config** | `EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"`, `BATCH_SIZE = 32` |

- Lazy singleton `SentenceTransformer`, `normalize_embeddings=True` so cosine similarity
  reduces to a dot product and Chroma's default space behaviour is predictable.
- One instance per process, loaded once, reused by both ingest and query paths.
- Model cached under `data/models/` (or the default HF cache) so the demo runs offline after
  the first run (NFR-3).
- `embed_query` and `embed_documents` are the same model — a mismatch here silently
  destroys retrieval quality, so the module exposes no way to pass a different model.
- `get_model()` asserts the model's real width against `EXPECTED_DIM = 384` and raises
  otherwise, so a model swap that changed the dimensionality fails at load time rather than
  mid-index.

### 6.4 Stage 4 — Vector Store (`rag/vectorstore.py`)

| | |
|---|---|
| **Input** | `list[Chunk]` (ingest) / `str` query (serve) |
| **Output** | persistent collection / `list[RetrievedChunk]` |
| **Config** | `COLLECTION_NAME = "hdfc_mf_faq"`, `CHROMA_DIR = "data/chroma"`, `TOP_K = 5` |

- `chromadb.PersistentClient(path=CHROMA_DIR)`. The collection is created with
  `metadata={"hnsw:space": "cosine"}`.
- **Chroma's default embedding function is not used.** Vectors computed in Stage 3 are
  passed explicitly via `embeddings=`, so ingest and query are guaranteed to share a model.
  The collection is additionally created with `embedding_function=None`, so Chroma's
  embedder is not merely bypassed but unattached — otherwise a future call that omits
  `embeddings=` would silently embed through a second, different model.
- Metadata per chunk: `scheme`, `category`, `scope`, `section`, `source_url`, `chunk_id`,
  `ordinal`, `ingested_at`, plus `kind` and `token_estimate`. Only scalar types — no nested
  dicts, no lists (Chroma constraint), enforced in `chunk_metadata()` because Chroma's own
  error message for a list value is unhelpful. `scope` is carried through from `Source`
  because retrieval filters on it (§6.5).
- **Idempotency (NFR-2):** `rebuild()` deletes the collection and recreates it; `upsert()`
  writes by `chunk_id`, so a partial re-run converges instead of duplicating.
- `upsert()` batches at 200 to stay well inside Chroma's batch limits.
- **One client per path per process.** Chroma caches a system per path and refuses a second
  client with different settings (`An instance of Chroma already exists for … with different
  settings`), so `client_settings()` is the single source of truth for that configuration.
  Tests and maintenance scripts must use it rather than building `Settings(...)` themselves.


### 6.5 Stage 5 — Retrieval (`rag/retriever.py`)

Query-side sequence:

1. **Scheme detection** — match scheme aliases against the question
   (`hdfc elss`, `tax saver`, `elss`, `large cap`, `hdfc equity`, `small cap`,
   `balanced advantage`). Produces an optional `scheme` filter, and the scheme's URL is
   remembered for the citation default.
2. **Query expansion** — append the detected scheme name to the embedded query text when a
   scheme was named, so the embedding carries the entity the chunks mention. Low risk, since
   the filter already constrains candidates.
3. **Vector search** — `collection.query(query_embeddings=[q], n_results=TOP_K, where=filter)`.
   **The filter must be `{"$or": [{"scheme": X}, {"scope": "general"}]}`, not
   `{"scheme": X}`.** This was found during P1: the ELSS lock-in is stated only on an AMFI
   page tagged `scope=general`, so a plain scheme filter would make the correct answer
   unreachable for exactly the question the demo is judged on. Both filters include
   `scope=general` unconditionally; the second element is what makes general regulator
   material reachable from a scheme-specific question.
4. **Threshold** — if `top_score < MIN_SCORE`, return an empty evidence list; the pipeline
   turns that into `NOT_FOUND` rather than generating (FR-10, principle P4). `MIN_SCORE` is
   calibrated on the golden set, not guessed.
5. **Rerank (Should-tier, FR-14 optional)** — if enabled, take top 10 and rescore with a
   lightweight cross-encoder (`cross-encoder/ms-marco-MiniLM-L-6-v2`), then keep top 5.
   Behind a config flag so the demo can show retrieval quality with and without it.
6. **De-duplicate** — collapse chunks from the same `section`+`source_url` so the evidence
   set is diverse, and so the citation is unambiguous.

**Citation rule:** the citation is the `source_url` of the rank-1 evidence chunk that
actually contains the answer. If rank 1 and rank 2 disagree on `source_url`, the rank-1 URL
wins and both chunks appear in the "show sources" expander.

### 6.6 Stage 6 — Generation (`rag/generator.py`, `rag/prompts.py`)

All prompt text and all refusal copy live in `prompts.py` — one file the team can point at
during the demo to show where the constraints are enforced.

System prompt contract:

- Answer **only** from the supplied context blocks. Each block is prefixed with its
  `[scheme | section | url]` header so the model can attribute the answer.
- If the answer is not present in the context, reply exactly
  `I couldn't find that in the official sources.` and nothing else.
- **Maximum 3 sentences.** No bullet lists, no headers.
- No advice language: never "you should", "I recommend", "is a good choice", "suitable for you".
- No return, performance, or projection figures. If asked, point to the factsheet link.
- No claims about anything outside the context, including general finance knowledge.
- Do not invent URLs; the only URL to emit is the one in the citation header.

Generation parameters: `temperature=0` (repeatable answers for a recorded demo),
`max_tokens ≈ 160`, single attempt, no streaming for v1 (streaming makes the verifier's job
harder for no demo benefit).

**Provider abstraction (P2):** `generator.py` exposes `generate(prompt, context) -> str` with
a local backend (Ollama, e.g. `llama3.2` / `qwen2.5`) and an HTTP backend, selected by
`LLM_BACKEND` in `config.py`. Both are non-streaming, temperature 0. The swap is a config
change, not a code change — this is the substitution point named in PRD §6.6.

### 6.7 Guards (`rag/guards.py`)

Two gates, both before retrieval, both cheap and deterministic.

**PII gate (FR-12, NFR-6)** — compiled regex set:

| Pattern | Regex (illustrative) |
|---|---|
| PAN | `\b[A-Z]{5}[0-9]{4}[A-Z]\b` |
| Aadhaar | `\b[2-9]\d{3}\s?\d{4}\s?\d{4}\b` |
| Email | `\b[\w.+-]+@[\w-]+\.[\w.]+\b` |
| Phone | `\b(?:\+91[\s-]?)?[6-9]\d{9}\b` |
| Account no. | `\b\d{9,18}\b` (only when co-occurring with account/folio keywords) |
| OTP | 4–8 digit string following an `otp`/`verification code` keyword |

On match: return `REFUSED_PII` immediately, before retrieval, so no PII reaches the LLM.
The matched input is **not** logged (NFR-6); only the pattern name and a redacted mask
(`ABCDE1234F` → `*******`) are written to the log.

**Intent gate (FR-9)** — labels `FACTUAL` | `ADVICE` | `RETURNS` | `OUT_OF_SCOPE`.

Default implementation is a weighted keyword/regex classifier (deterministic, zero latency,
inspectable in the demo) with a high-recall-for-advice bias, because a missed advice
question is the worst failure mode. Patterns include `should i`, `is it good`, `recommend`,
`best fund`, `worth buying`, `which one should`, `my age`, `suitable for me`, and the returns
family `returns`, `cagr`, `performance`, `profit`, `how much can i earn`.

If the LLM is reachable, an optional `LLM_INTENT` mode asks for a single label with a strict
output contract and falls back to the rule classifier on any parse failure (fail closed:
`ADVICE`).

Refusal copy lives in `prompts.py` per PRD §8, and each refusal carries the educational or
factsheet link defined in `config.py` (`EDUCATION_LINK`, `FACTSHEET_LINK_TEMPLATE`).

### 6.8 Verifier (`rag/verifier.py`)

Runs on the generated string, cheap regex checks, no second LLM call:

- sentence count ≤ 3 → else truncate to 3 sentences (never silently loses the citation; the
  citation is rendered separately, not part of `answer`)
- advice phrasing present → replace with the `REFUSED_ADVICE` response
- return/performance figure present (percentage near a return keyword, `CAGR`, `x`, `annually
  returns`) → replace with the `REFUSED_RETURNS` response
- a URL appears that is not in `sources.py` → strip it (prevents invented citations)
- answer empty after generation → downgrade to `NOT_FOUND`

The verifier is what turns "we asked the model nicely" into "the output provably satisfies the
constraint", and it is a good 30 seconds of the demo.

### 6.9 Stage 7 — Citation + UI (`rag/pipeline.py`, `app.py`)

The last stage of the query path. It has two halves that must not be confused: the
`Answer` is *assembled* in `pipeline.render()`, and it is *displayed* in `app.py`. The
UI is a pure renderer and holds no decision logic, which is why the Streamlit screen,
`cli.py`, and `scripts/evaluate.py` cannot disagree — all three read the same object.

#### 6.9.1 One citation, by rule

`citation_url` is the **rank-1 evidence chunk's** `source_url`, and `None` when there
is no evidence. A single rule covers both cases the spec cares about:

| case | evidence | result |
|---|---|---|
| pre-retrieval refusal (PII, advice, returns, other AMC) | none | `citation_url = None`, nothing cited |
| `NOT_FOUND` | none | no citation |
| answered question | present | cites the page the top-ranked chunk came from |
| LLM outage | present | still cites the page, so the user can read the facts themselves |

The fourth row is why the rule is written against `evidence` rather than against
`status`. A backend that is down should degrade, not lose the user's ability to find
the source. Exactly one link is ever rendered (FR-7); the other evidence chunks appear
in the expander as URLs rather than as competing citations, so the answer has a single
attributable provenance.

#### 6.9.2 `Last updated from sources`

`Chunk` carries no timestamp and the raw chunk table has no such column, so the mapping
`source_url -> fetched_at` is read from `data/raw/_ingest_report.json` — the only place
it survives, since that file is written by the loader at fetch time.

Three properties matter:

- **Read lazily and cached.** `_fetched_at_map()` memoises on first use; it cannot
  change during a process, so re-reading per question would be a disk hit on the hot
  path for nothing.
- **Newest date wins, not the top chunk's date.** `_last_updated()` takes `max()` over
  every evidence chunk's date. ISO `YYYY-MM-DD` sorts lexicographically, so `max()` on
  the raw strings is the newest date and the formatting happens once afterwards.
- **Degrades, never fails.** If the ingest report is missing or unreadable, the map is
  empty, `last_updated` is `""`, and the pipeline still answers. Losing the date is
  acceptable; refusing to answer because a date file is corrupt is not.

The date exists to make a stale corpus *visible* rather than silent: an answer reading
`Last updated from sources: 27 Sep 2026` tells the user how old the evidence is, which
is the whole defence against a snapshot source set being mistaken for live data.

#### 6.9.3 The screen (FR-14, PRD §7)

One screen, in this order, from `app.py::main()`:

1. `st.set_page_config` → title, then the disclaimer line, in that order, both always
   visible. The disclaimer is `config.disclaimer_text()`, which reads the **first line**
   of `DISCLAIMER.md` — the full text lives there, the UI shows its headline, so the
   copy has one source and cannot drift.
2. Three fixed example chips from `config.EXAMPLE_QUESTIONS`, each populating the input.
3. Text input + submit → **one** `pipeline.answer()` call, wrapped in a spinner.
4. Status line, then the answer. `ANSWERED` renders as markdown; every non-answer
   status renders via `st.info`, so a refusal cannot be mistaken for a result.
5. `**Source:**` link, when `citation_url` is set.
6. `**Last updated from sources:**`, or the literal
   `not applicable (no source retrieved)` when there is no evidence. Printing the label
   with a blank value would be worse than saying so.
7. `Show sources (n)` expander: for each evidence chunk, scheme · section · score, its
   URL, and its text. This is what makes retrieval visible instead of magical — the
   claim in the README that the right chunk is often retrieved at rank #2 is checkable
   by the grader in the expander.
8. The disclaimer again in the footer.

#### 6.9.4 Startup failure is a message, not a traceback

`warm_runtime()` opens the embedder and the Chroma handle inside `@st.cache_resource`
(Streamlit re-runs the whole script per interaction, so without the cache the model
would reload on every keystroke and NFR-1 fails). Any exception there, or a count of 0,
renders `python run_ingest.py` as an instruction instead of a stack trace — which is the
first thing a fresh clone hits, since `data/` is git-ignored.

#### 6.9.5 What this stage deliberately does not do

- **No answer post-processing.** Truncation and refusal substitution are §6.8's job.
  Rendering never edits the text, so what is displayed is what the verifier passed.
- **No second `pipeline.answer()` call**, no per-widget filtering, no client-side
  scoring. One question in, one `Answer` out.
- **No streaming.** `temperature=0` and non-streaming were chosen (D6) for repeatable
  answers in a recorded demo; the cost is a slower perceived first token.

---

## 7. Runtime Flows

### 7.1 Ingest (build) — `python run_ingest.py`

```
config → sources.load_sources()
       → loader.fetch_all()            [1]  → data/raw/*.txt + _ingest_report.json
       → chunker.split_all()           [2]  → list[Chunk]  (+ invariant checks)
       → embedder.embed_documents()    [3]  → 384-dim vectors
       → vectorstore.upsert()          [4]  → data/chroma/hdfc_mf_faq
       → write reports/sources.md, sources.csv
       → print summary: docs, chars, chunks, strategy, elapsed
```

Exit code non-zero if any URL yielded no text, so a broken fetch cannot masquerade as a
successful build. The printed summary is the demo's ingestion evidence.

### 7.2 Query (serve) — one user turn

```
question
  → guards.check_pii()          ── hit ──→ REFUSED_PII           (no LLM call)
  → guards.classify_intent()   ── ADVICE ─→ REFUSED_ADVICE + link
                              ── RETURNS ─→ REFUSED_RETURNS + factsheet
                              ── OOS ─────→ OUT_OF_SCOPE message
  → retriever.search()         ── empty ─→ NOT_FOUND
  → generator.generate(ctx)              ← the only LLM call for factual questions
  → verifier.verify()          ── fail ───→ fallback refusal
  → pipeline.render()          → Answer{answer, status, citation_url, last_updated, evidence}
```

Only `FACTUAL` questions reach the LLM. Refusals are template strings, so they are instant and
cannot be wrong — which also means a refused question costs no tokens and cannot be flaky on
demo day.

Latency budget (NFR-1, ≤ 8 s on a demo laptop):

| Step | Budget |
|---|---|
| PII + intent guards | < 10 ms |
| Query embedding | 40–120 ms |
| Chroma query | 5–30 ms |
| LLM generation (local, ~3 sentences) | 3–6 s |
| Verification + render | < 20 ms |

Streamlit `st.cache_resource` holds the embedder, Chroma client, and generator across
reruns, so the model is not reloaded per question. First question after app start pays the
model load (~2–5 s) once.

### 7.3 Failure Modes

| Failure | Detection | Behaviour |
|---|---|---|
| Page is JS-rendered / empty | `len(text) < MIN_TEXT_CHARS` | Ingest fails loudly, names the URL, prints fallback instructions (PRD risk #1) |
| Network error on one URL | exception in fetch | Retry once, then record as failed; other URLs still ingest |
| Model download unavailable at demo | load error | `embedder` raises a clear "run `python -m scripts.warm_cache` once with network" message; video fallback |
| Chroma dir deleted | collection missing | UI detects an empty/missing index and prints `run: python run_ingest.py` |
| LLM backend down | request error | Return a fixed "assistant is temporarily unavailable, see the source page" message with the scheme link; never answer from memory |
| Retrieval returns nothing relevant | `top_score < MIN_SCORE` | `NOT_FOUND` + pointer to the scheme page (FR-10) |
| LLM invents a URL | verifier regex | URL stripped; if the answer depended on it, downgrade to `NOT_FOUND` |
| Advice question phrased unusually | classifier miss | Mitigated by verifier on the output as a second line of defence |

---

## 8. Storage Layout

| Path | Contents | Lifetime |
|---|---|---|
| `data/raw/*.txt` | Cleaned page text, one per source | Refreshed on re-ingest; kept for demo inspection |
| `data/raw/_ingest_report.json` | Per-URL status, char count, hash, error | Latest run only |
| `data/chroma/` | Persistent ChromaDB (vectors + metadata) | Rebuilt by `run_ingest.py` |
| `data/eval/golden.jsonl` | Golden questions + expected `status` + must-contain terms | Hand-written, versioned |
| `logs/app.log` | Stage timings, top score, chosen status, redacted PII hits | Appended; no PII, no full user text beyond the query itself |
| `reports/` | Chunking decision, source list | Committed, part of deliverables |

No secrets, no PII, and no raw API keys on disk (NFR-4, NFR-6). `.env` is git-ignored;
`.env.example` documents the variable names.

---

## 9. Configuration

All tunables live in `config.py`; nothing hard-codes a path, threshold, or model name in
stage modules. Grouped by concern so a reviewer can see the whole decision surface at once.

| Group | Keys |
|---|---|
| Sources | `SOURCES_FILE`, `EDUCATION_LINK`, `FACTSHEET_LINK_TEMPLATE` |
| Ingest | `HTTP_TIMEOUT`, `USER_AGENT`, `EXTRACTOR`, `MIN_TEXT_CHARS`, `RAW_DIR` |
| Chunking | `CHUNK_STRATEGY`, `CHUNK_SIZE`, `CHUNK_OVERLAP`, `MIN_CHUNK_CHARS` |
| Embedding | `EMBED_MODEL`, `BATCH_SIZE`, `MODEL_CACHE_DIR` |
| Vector store | `CHROMA_DIR`, `COLLECTION_NAME`, `HNSW_SPACE`, `CHROMA_BATCH_SIZE` |
| Retrieval | `TOP_K`, `RERANK_TOP_N`, `ENABLE_RERANK`, `MIN_SCORE` |
| Generation | `LLM_BACKEND`, `LLM_MODEL`, `LLM_TEMPERATURE`, `LLM_MAX_TOKENS` |
| Guards | `PII_PATTERNS`, `INTENT_RULES`, `INTENT_BACKEND` |
| UI | `DISCLAIMER_TEXT`, `EXAMPLE_QUESTIONS`, `MAX_ANSWER_SENTENCES` |

`MIN_SCORE`, `CHUNK_SIZE`, and `MIN_SCORE`-adjacent thresholds are the values most likely to
need tuning after seeing real output; all three are meant to be calibrated against
`data/eval/golden.jsonl`, not chosen by feel.

---

## 10. Design Decisions and Trade-offs

| # | Decision | Alternatives | Why chosen | Cost accepted |
|---|---|---|---|---|
| D1 | Hand-rolled stage modules | LangChain / LlamaIndex | Every step is visible and explainable for the demo; no abstraction hides the pipeline (NFR-5) | More code we write ourselves |
| D2 | Pass embeddings to Chroma explicitly | Chroma default embedding function | Ingest and query cannot silently diverge models | We manage embedding ourselves |
| D3 | Cosine space, normalized vectors | L2 | Cosine matches sentence-embedding semantics; normalization makes the score interpretable | None material |
| D4 | Content-addressed `chunk_id`, delete-then-insert rebuild | Timestamp-based versioning | Deterministic, idempotent, no orphan duplicates (NFR-2) | Full rebuild on source change — fine at this corpus size |
| D5 | Guardrails before generation, template refusals | Rely on the prompt | Refusals are instant, free, and cannot hallucinate | Rule lists need maintenance |
| D6 | `temperature=0`, non-streaming | Streaming chat feel | Repeatable answers for a recorded demo; simpler verification | Slower perceived first token |
| D7 | Rule-based intent classifier as default | LLM classifier only | Zero latency, inspectable, and fails safe; LLM mode optional with rule fallback | Will miss phrased advice (documented limit) |
| D8 | Post-generation verifier | Trust the prompt | Makes the ≤3-sentence and no-advice constraints testable rather than aspirational | Occasional false positive on a legitimate number — tuned to be narrow |
| D9 | Metadata filter on scheme detection | Pure vector search over the whole corpus | One question about "ELSS lock-in" should not retrieve the Small Cap page | Fails if the user names a scheme ambiguously; mitigated by aliases |
| D10 | Streamlit UI | FastAPI + HTML | Fastest credible path to a demo; the deliverable is the pipeline, not the UI | Less control over rendering |
| D11 | Snapshot corpus, no live fetch at query time | Fetch source pages per question | Deterministic, fast, offline, and honest about `Last updated from sources` | Answers can go stale — stated as a known limit |
| D12 | `SectionSplitter` + table-row protection (expected) | Plain recursive splitting | Groww pages are tabular and section-structured; a cut mid-row destroys a fee figure | Slightly more code in `chunker.py` |

---

## 11. Testing & Evaluation

### 11.1 Unit tests (per module, fast, no network)

| Module | What is asserted | Status |
|---|---|---|
| `loader` | Boilerplate tags stripped; whitespace normalised; empty page raises `IngestError` | done (P1) |
| `chunker` | Chunk size bound; no row split; every chunk has `section` and `source_url`; `chunk_id` unique; deterministic for fixed input | done (P2) |
| `embedder` | Output is 384-dim; the same text embeds identically twice; the model id is `all-MiniLM-L6-v2` | done (P3) |
| `vectorstore` | Upsert twice yields the same count (idempotency); metadata filter returns only that scheme | done (P3) |
| `retriever` | A known question retrieves a chunk from the right scheme; threshold rejects an unrelated question | done (P4) |
| `guards` | Every PII pattern fires; a clean question passes; advice/returns phrases classify correctly | done (P6) |
| `verifier` | 4-sentence answer is truncated; "you should" is caught; a return figure is caught; an invented URL is stripped | done (P6) |
| `generator` | One factual question is answered from context inside every prompt constraint; the model returns the exact NOT_FOUND string when the context does not support an answer | done (P5) |
| `pipeline` | Each of the six `status` values is reachable and renders the required fields | done (P7) |
| `cli` | Answer, status, citation, and the `Last updated from sources:` line are all printed; `--sources` dumps chunks | done (P8) |
| `evaluate` | Every golden row's `expected_status`, `must_contain`, `forbid`, and scheme assertion is checked; the table is written to `reports/eval_results.md` | done (P8) |
| `app` | The single screen shows the title, disclaimer, 3 examples, input, one citation, the date line, and a sources expander; a missing index renders instructions, not a traceback | done (P9) |

As of P9: **345 unit tests**, in ~6s and fully offline — no test makes a network call. Stage 3
adds 14, Stage 4 adds 30, Stage 5 adds 28, P6 adds 114 (78 guard, 36 verifier), P5 adds 26,
P7 adds 40, P8 adds 21, P9 adds 8. The live gates are `scripts/probe_retrieval.py`,
`probe_guards.py`, `probe_generation.py`, `probe_pipeline.py`, and `scripts/evaluate.py`; their
reports are in `reports/`. The UI is exercised offline through Streamlit's own `AppTest`.

One more property is worth naming, because it is the one that is easiest to assert and hardest
to achieve. The pipeline's value is its **order**, and order is invisible in a return value: a
pipeline that embeds a query containing a PAN, or asks the model about a fund the corpus does
not cover and refuses afterwards, has every field looking correct and has already done the
damage. So the P7 tests assert what was *not* reached —

- `pipeline` — retrieval is never called for a PII question; the LLM is never called for a
  refusal or for a question with no evidence; a guard refusal logs exactly two stages
  (`guard`, `render`) and an answered question exactly four. Measured live: refusals cost
  0.0 ms and zero tokens, which is a consequence of the order rather than an optimisation.

Two properties are asserted that are worth naming, because the failure mode in both
cases is silent rather than loud:

- **`embedder`** — the module exposes no per-call model argument, and its source contains
  exactly one `SentenceTransformer(…)`. A test asserts both. If ingest and query could
  ever load different models, no test would fail; retrieval would just get quietly worse.
- **`vectorstore`** — the collection's `_embedding_function` is `None`, not merely unused.
  A test asserts that too, so a future `upsert()` that forgets `embeddings=` fails a test
  instead of writing vectors from a second model.


### 11.2 Golden set (end-to-end, the demo's evidence)

`data/eval/golden.jsonl` — one JSON object per line: `question`, `expected_status`,
`expect_scheme`, `must_contain` (e.g. `["0.5%", "Nifty"]`), `forbid` (e.g. `["should", "recommend"]`).

Coverage: the six PRD acceptance questions (expense ratio, exit load, minimum SIP, ELSS
lock-in, riskometer/benchmark, capital-gains statement), at least one advice refusal, one
returns refusal, one PII rejection, one out-of-scope AMC, one out-of-corpus question, and
one question naming a scheme to exercise the metadata filter.

`scripts/evaluate.py` runs the set and prints a pass/fail table plus overall accuracy. This
table is the artifact that turns "it seemed to work" into a claim — it is the natural
companion to the demo video.

### 11.3 Manual demo script

1. `python run_ingest.py` — show the ingestion summary (5 docs, chunk count, strategy).
2. `python scripts/evaluate.py` — show the golden table passing.
3. `python cli.py "What is the exit load on HDFC Large Cap?"` — answer + citation.
4. `python cli.py "Should I buy HDFC Small Cap?"` — refusal + link.
5. `streamlit run app.py` — the UI, including the "show sources" expander to reveal the
   retrieved chunk text, which makes the RAG visible rather than magical.

---

## 12. Observability

Enough logging to debug a bad answer live, and nothing that could leak PII.

- One structured log line per query: `stage=retrieve top_score=0.71 n=5 schemes=[ELSS]`,
  `stage=generate ms=3120 tokens_out=58`, `stage=verify status=ANSWERED`, `stage=render citation=...`.
- Log the retrieved `chunk_id`s, not the full user text, in the same line — the query is
  echoed in the UI transcript, so the log does not need it.
- PII hits log the pattern name and a mask only.
- Ingest logs per-URL char count, chunk count, and the strategy used.

---

## 13. Security & Privacy

| Concern | Control |
|---|---|
| PII submitted by a user | PII gate rejects before retrieval; never stored, never sent to the LLM, logged only as a mask (NFR-6) |
| Prompt injection via fetched page text | Sources are a fixed allowlist in `sources.py`; no user-supplied URL can enter the corpus; the generator prompt states that context is data, not instructions |
| Secrets | `.env` git-ignored; keys read from environment only (NFR-4) |
| Untrusted outbound requests | Fixed 5 hosts; timeout on every call; redirects not followed to unexpected hosts |
| Data retention | Corpus is public scheme data; no user data persisted at all — v1 is stateless |

---

## 14. Extension Points

| Change | Where | Effort |
|---|---|---|
| Add a scheme | `sources.py` + re-run ingest | Minutes |
| Add an AMC | `sources.py` + `EDUCATION_LINK`/`FACTSHEET_LINK_TEMPLATE` per AMC; `category` and `scheme` already generalise | Hours |
| Swap embedding model | `config.EMBED_MODEL` + full re-ingest (vectors are not comparable across models) | Minutes, plus a re-tune of `MIN_SCORE` |
| Swap vector DB | New module implementing `vectorstore.py`'s four functions | Hours — the interface is deliberately that small |
| Swap LLM | `config.LLM_BACKEND` | Minutes |
| Add a reranker | `config.ENABLE_RERANK` + a scorer in `retriever.py` | Hours |
| Add a second UI | `pipeline.answer()` is the only entry point; both CLI and Streamlit already use it | Hours |
| Add live data (NAV) | New stage between retrieval and generation — explicitly a product-scope change, not an engineering one (PRD §2) |

---

## 15. Traceability — PRD requirements to components

| Requirement | Component | Verified by |
|---|---|---|
| FR-1 ingest + metadata on every chunk | `loader.py`, `chunker.py` | unit: `loader`, `chunker` |
| FR-2 data-driven chunking decision | `chunker.py`, `reports/chunking_decision.md` | `reports/` review + unit: determinism |
| FR-3 `all-MiniLM-L6-v2` embeddings | `embedder.py` | unit: 384-dim, model id — **passed** |
| FR-4 ChromaDB persistence | `vectorstore.py` | unit: idempotency, persistence across runs — **passed**; stored vectors reproduce from the pinned model to `max|diff| ≈ 1e-7` |

| FR-5 query embedding + top-k + URL | `retriever.py` | golden: correct scheme retrieved |
| FR-6 ≤ 3 sentences, context-only | `prompts.py`, `verifier.py` | unit: `verifier`, golden: sentence count |
| FR-7 exactly one citation link | `pipeline.render()` | golden: citation present and in `sources.py` |
| FR-8 "Last updated from sources" | `pipeline.render()` + `Document.fetched_at` | golden: field present |
| FR-9 intent refusal + link | `guards.py`, `prompts.py` | golden: `REFUSED_ADVICE`, `REFUSED_RETURNS` |
| FR-10 no-evidence refusal | `retriever.py` threshold, `pipeline` | golden: `NOT_FOUND` |
| FR-11 tiny UI + 3 examples + note | `app.py`, `config.py` | manual demo |
| FR-12 PII block without storing | `guards.check_pii()` | unit: all patterns; golden: `REFUSED_PII` |
| FR-13 no return computation | `guards`, `verifier` | unit + golden `RETURNS` case |
| FR-14 "show sources" expander | `app.py`, `Answer.evidence` | manual demo |
| NFR-1 ≤ 8 s | caching in `app.py`, budget §7.2 | manual timing |
| NFR-2 idempotent ingest | `vectorstore.upsert()` | unit: double-run count — **passed**, 407 = 407 |
| NFR-3 offline after first run | local model cache | **passed**: `HF_HUB_OFFLINE=1` loads the embedder in 0.2s and the index is queryable. The LLM half is still blocked on Ollama (P5) |

| NFR-4 no secrets in repo | `.env` handling | repo review |
| NFR-5 one module per stage | repository layout §4 | repo review |
| NFR-6 no PII persisted | `guards`, logging §12 | log review |

---

## 16. Open Questions to Lock Before Implementation

| # | Question | Blocks | Default if unanswered |
|---|---|---|---|
| Q1 | Does `requests` + `trafilatura` extract usable text from the 5 Groww URLs? | M1, everything downstream | Assume not; pre-commit to HDFC factsheet PDFs as sources |
| Q2 | Which generation backend — local Ollama or a free-tier API? | M3 | Local Ollama, for offline demo safety (NFR-3) |
| Q3 | `MIN_SCORE` calibration value | M3 | Derive from the golden set's score distribution |
| Q4 | Is `SectionSplitter` or table-aware splitting the winner? | M2 | Per D12, section + row protection |
| Q5 | Cross-encoder rerank in scope for v1? | M3 | No — ship without, add if time allows (Should-tier) |
| Q6 | Streaming answers in the UI? | M5 | No (D6) |

---

## 17. Deliverables Mapping

| PRD §9 deliverable | Produced by |
|---|---|
| Working prototype | `app.py` (§6.9), `cli.py`, `run_ingest.py` (+ demo video fallback) |
| Source list | `reports/sources.md`, `reports/sources.csv` from `sources.py` |
| README | `README.md` — setup, scope, chunking rationale, known limits, disclaimer |
| Sample Q&A | `reports/sample_qa.md`, generated via `cli.py --sample` |
| Disclaimer snippet | `DISCLAIMER.md`, first line read by `config.disclaimer_text()` |
| PRD | `docs/PRD.md` |
| Architecture write-up | this document (`docs/architecture.md`) |
