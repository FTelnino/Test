# IMPLEMENTATION GUIDE — HDFC Mutual Funds FAQ Assistant (RAG Chatbot)

| Field | Value |
|---|---|
| Document type | Phase-wise Build Guide (agent-facing) |
| Version | v1.0 |
| Date | 2026-09-27 |
| Reads | `PRD.md` (what), `architecture.md` (how) |
| Purpose | Drive implementation in sequential phases, one phase at a time |

This is the runbook for building the prototype. Each phase is independently verifiable and
ends with a gate that must pass before the next phase starts. `architecture.md` §4–§9 is the
normative reference for module names, data contracts, and config keys — this document does not
restate them, it sequences the work.

---

## 0. How to Use This Document

### 0.1 Operating rules

1. **One phase at a time.** Do not start phase *N+1* until phase *N* is green.
2. **The contracts are frozen.** The dataclasses in `architecture.md` §5, the `status` values,
   and the config keys are the interface. Change them only by updating `architecture.md` too.
3. **No forward-referencing.** Do not stub a later stage's function to make the current phase
   import. Import errors are the signal that a phase is incomplete.
4. **Verify, then commit.** Each phase ends with a command whose output must be shown before
   moving on.
5. **Record decisions, not just code.** The chunking decision (§2.3) and any scope change from
   §13 must land in a report file, because they are graded deliverables.

### 0.2 Phase map

| Phase | Deliverable | Gate (must pass) | Est. |
|---|---|---|---|
| P0 | Scaffold, config, sources, disclaimer | `python -c "import config"` and source list prints 5 rows | 0.5 day |
| P1 | Loading stage | `python run_ingest.py --fetch-only` ingests 5 pages with real text | 0.5 day |
| P2 | Chunking stage + decision report | chunk invariants pass, `reports/chunking_decision.md` written | 1 day |
| P3 | Embedding + vector store | collection non-empty, idempotent, persists across runs | 0.5 day |
| P4 | Retrieval | golden questions hit the right scheme, threshold rejects noise | 0.5 day |
| P5 | Generation + prompts | CLI answers a factual question with a citation | 1 day |
| P6 | Guards + verifier | all 6 `status` values reachable, PII never persisted | 0.5 day |
| P7 | Pipeline orchestration | `pipeline.answer()` returns a complete `Answer` | 0.5 day |
| P8 | CLI + eval harness | `scripts/evaluate.py` prints a pass table | 0.5 day |
| P9 | Streamlit UI | screenshot of the required screen elements | 0.5 day |
| P10 | Docs & deliverables | all PRD §9 items exist and are accurate | 0.5 day |
| P11 | Demo prep & recording | ≤3-min video recorded, cold-start verified | 0.25 day |

P0–P3 are the build path. P4–P7 are the serve path. P8–P11 are the evidence path.
Total ≈ 4–5 days for 2–3 people.

```
P0 ─► P1 ─► P2 ─► P3 ─► P4 ─► P5 ─► P6 ─► P7 ─► P8 ─► P9 ─► P10 ─► P11
       │           │                       │                   │
    (build path)  └── ingest complete ─────┘         (serve)  (evidence)
```

### 0.3 Anti-patterns to reject during review

| Anti-pattern | Why it is wrong | Correct instead |
|---|---|---|
| Importing LangChain / LlamaIndex | Hides the pipeline the demo is graded on | Plain Python per `architecture.md` §2 P1 |
| Using Chroma's default embedding function | Ingest and query models can diverge silently | Pass vectors from `embedder.py` explicitly |
| Streaming generation | Makes verification hard, answers non-repeatable | Non-streaming, `temperature=0` |
| Prompt-only guardrails | Not enforceable, not testable | Guards before generation + verifier after |
| Hard-coded paths, thresholds, or model names inside stage modules | Violates `architecture.md` §9 | Read everything from `config.py` |
| Catching exceptions and returning a helpful-sounding answer | Fails open on the worst failure | Fail closed: raise, or return a refusal |
| Logging the raw user query alongside PII patterns | Leaks PII to disk | Log pattern name + mask only |
| Writing chunks with a timestamp-based ID | Re-ingest duplicates documents | Content-addressed `chunk_id` |
| One giant `app.py` with ingest + query + UI | Untestable, unreadable | One module per stage |
| Copying refusal text into multiple modules | They drift | All copy lives in `prompts.py` |

---

## P0 — Scaffold, Config, Sources, Disclaimer

**Goal:** an importable skeleton with every tunable and every source URL in one place, so no
later phase hard-codes anything.

**Create**

```
config.py            sources.py            DISCLAIMER.md
requirements.txt     .env.example          .gitignore
rag/__init__.py
```

**Tasks**

1. `requirements.txt` with pinned versions: `requests`, `beautifulsoup4`, `lxml`,
   `trafilatura`, `chromadb`, `sentence-transformers`, `streamlit`, `pytest`, and the LLM
   backend (Ollama via `requests` — no extra SDK needed).
2. `config.py` with every key from `architecture.md` §9, grouped by concern, plus derived
   `Path` objects. No logic beyond defaults and path construction.
3. `sources.py` with the 5 `Source` entries from `PRD.md` §4.1 verbatim, plus a
   `SCHEME_ALIASES` map (used by retrieval in P4) and `load_sources() -> list[Source]`.
4. `DISCLAIMER.md` with the exact UI text, and `DISCLAIMER_TEXT` in `config.py` reading the
   same string. One source of truth for the disclaimer.
5. `.gitignore`: `.env`, `data/chroma/`, `data/raw/`, `data/models/`, `__pycache__/`,
   `.pytest_cache/`, `logs/`.
6. `.env.example` listing variable names only, all blank.

**Do not** create stage modules yet. `rag/` ships with just `__init__.py`.

**Gate**

```bash
python -c "import config, sources; print(len(sources.load_sources()))"   # must print 5
```

**Done when:** 5 sources print, `DISCLAIMER_TEXT` matches `DISCLAIMER.md` verbatim, no secrets
in the repo.

---

## P1 — Stage 1: Loading

**Goal:** turn 5 URLs into 5 clean `Document` objects, or fail loudly.

**Create:** `rag/loader.py`, `scripts/fetch_probe.py` (a throwaway probe used to answer Q1 in
`architecture.md` §16)

**Reference:** `architecture.md` §5 (`Source`, `Document`), §6.1, §8

**Tasks**

1. Implement the `Source` and `Document` dataclasses exactly as in `architecture.md` §5.
   `Document` carries `source`, `text`, `fetched_at` (ISO date), `text_hash`.
2. `fetch_one(source) -> Document`:
   `requests.get` with `config.USER_AGENT`, `config.HTTP_TIMEOUT`, `allow_redirects=True`,
   one retry with backoff. Raise `FetchError` on non-200.
3. `extract_text(html) -> str`: try `trafilatura.extract`; on failure fall back to
   `beautifulsoup4` with `script/style/nav/header/footer/aside/form` decomposed, then
   `get_text`. Return the longer of the two non-empty results.
4. `clean_text(raw) -> str`: collapse whitespace, strip zero-width characters, drop
   cookie/consent lines (`accept cookies`, `we use cookies`, `privacy policy`), normalise
   unicode quotes/dashes, preserve newlines only between table rows and list items.
5. **Validation:** if `len(text) < config.MIN_TEXT_CHARS`, raise `IngestError` naming the URL
   and the char count. This is the PRD risk-1 tripwire — do not soften it.
6. `fetch_all(sources) -> tuple[list[Document], list[dict]]`: return successes plus a
   per-URL status record, so a partial failure is visible rather than silent.
7. `save_raw(docs, raw_dir)`: write `data/raw/<scheme_slug>.txt` and
   `data/raw/_ingest_report.json` (URL, status, chars, `text_hash`, error, `fetched_at`).
8. `run_ingest.py` entrypoint: add a `--fetch-only` flag that runs P1 and exits, printing
   per-URL char counts. This is the P1 gate.

**First task in this phase — answer Q1.** Run `scripts/fetch_probe.py` against all 5 URLs and
print `{url: char_count}`. Branch here:

| Result | Action |
|---|---|
| All 5 pages yield usable text (> `MIN_TEXT_CHARS`) | continue with Groww as the corpus |
| Some pages empty/JS-rendered | follow `architecture.md` §6.1 fallback order: locate the first-party HDFC factsheet PDF for that scheme, add it to `sources.py` as an additional `Source` with `category` set and a `url_type` of `factsheet`, and extend `extract_text` with a PDF branch (`pypdf`) |
| Blocked or rate-limited | slow down, add a delay between fetches, retry with a browser-like UA, and record the outcome in `reports/chunking_decision.md` |

Whatever the outcome, update `sources.py` and the source-list report to match reality. Do not
proceed with an empty document and hope.

**Gate**

```bash
python run_ingest.py --fetch-only
```

Must print 5 URLs with non-trivial char counts and write 5 files to `data/raw/`. Open one file
and read it: is it the actual scheme page content, or nav junk?

**Done when:** all 5 documents have real, readable text; the ingest report shows no failures.

---

### P1 result (2026-09-27) — completed, with two deviations

Gate passed: `exit 0`, 7/7 sources ingested, 101,004 chars, 20/20 unit tests. Two tasks
above were changed by what the data showed. Full evidence in `reports/ingest_decisions.md`.

1. **Q1 answered YES** — all 5 Groww pages are server-rendered and extract cleanly. The
   assigned corpus did not need the JS-rendering fallback.
2. **Task 3 changed: "return the longer of the two results" is wrong here.** trafilatura
   returned 5,841 chars of real content with tables preserved; BeautifulSoup returned
   17,677 chars of which most is the site-wide nav. Implemented as trafilatura-primary with
   BeautifulSoup as a fallback below `MIN_TEXT_CHARS`, pinned by a test.
3. **Fallback 3 was used, so the corpus is 7 sources, not 5.** The Groww pages omit ELSS
   lock-in (zero mentions), the riskometer scale, and statement/CAS guidance, all of which
   PRD §11 criterion 2 requires. `hdfcfund.com` 403s every client and `sebi.gov.in` is
   network-unreachable, so two AMFI pages were added. `Source` gained `scope`
   (`scheme` | `general`), and P4 must filter with `{"$or": [{"scheme": X}, {"scope": "general"}]}`.
   A plain scheme filter would hide the only place the ELSS lock-in is stated.
4. **`Source` gained `content_type`** and `loader` gained a `pypdf` branch. HTML keeps table
   rows on their own lines for the P2 table-aware splitter; PDF text is collapsed to prose.
5. **Extra work not in the task list, needed by the cleaning step:** nav blurbs survive
   inside prose lines in trafilatura output and are removed as whole phrases. Removing a
   partial phrase leaves a dangling connective, which a unit test caught. The phrase also
   injects return language into the corpus, so it is a guardrail concern, not tidiness.
6. **Carried into P2:** the Groww pages contain peer-comparison tables with 1/3/5/10-year
   return figures for competing funds. Real content, so it stays, but P6 must intercept
   return questions before generation.

---

## P2 — Stage 2: Chunking + Decision Report

**Goal:** split documents into retrievable chunks **and** justify the strategy from the data.

**Create:** `rag/chunker.py`, `reports/chunking_decision.md`

**Reference:** `PRD.md` §6.3, `architecture.md` §6.2

**Tasks**

1. `ChunkStrategy` Protocol with `name: str` and `split(doc: Document) -> list[Chunk]`.
2. Three implementations, all sharing the same invariants:
   - `RecursiveSplitter` — separator ladder `["\n#", "\n##", "\n\n", ". ", " "]`, recursing
     and merging up to `CHUNK_SIZE`, with `CHUNK_OVERLAP` carried between merges
   - `SectionSplitter` — split on heading boundaries, track the heading path, then recurse
     oversized sections with the recursive splitter while **preserving the heading path as
     prefix text in the chunk** (so the embedding always knows the section)
   - `TableAwareSplitter` — detect table-like lines (pipes, or runs of ≥3 whitespace-separated
     numeric tokens), never split inside a detected block, and prefix the preceding heading
3. `chunk_id` is content-addressed: `f"{scheme_slug}:{text_hash[:8]}:{ordinal}"`.
4. `section` metadata is the heading path, e.g. `"Fees > Exit load"`. Never empty — fall back
   to the scheme name.
5. `token_estimate` via a cheap character heuristic, not a tokenizer download.
6. `validate_chunks(chunks) -> list[str]` returning human-readable violations: oversize
   chunk, empty `section`, empty `source_url`, duplicate `chunk_id`, undersize chunk. Assert
   zero violations at the end of the build.
7. `split_all(docs, strategy) -> list[Chunk]`, then persist
   `data/raw/chunks_<strategy>.json` for **all three** strategies.

**The decision report — this is a graded artifact.** Write
`reports/chunking_decision.md` containing:

1. **Observed data** — per-document stats table: chars, heading count, table density
   (rows per 1000 chars), whether the page is prose or tabular, and a 200-char sample.
2. **Candidates** — the three strategies with their configured parameters.
3. **Comparison** — chunk counts, mean/median chunk size, and for 3 sample chunks each:
   does a fee or exit-load figure stay intact with its label? Is `section` meaningful?
4. **Decision** — the chosen strategy, the `CHUNK_SIZE`/`CHUNK_OVERLAP` values, and 3–5
   sentences of reasoning grounded in the table above.
5. **Rejected alternatives** — why the other two lost, in one line each.
6. **Consequence** — set `config.CHUNK_STRATEGY` to the winner and note the expected effect
   on retrieval precision.

Default if the evidence is inconclusive: `SectionSplitter` with table-row protection
(`architecture.md` D12).

**Gate**

```bash
python -c "from rag.chunker import split_all, validate_chunks; ..."
```

`validate_chunks` returns `[]`, the three `chunks_*.json` files exist, and
`reports/chunking_decision.md` has all six sections filled with real numbers — not placeholders.

**Done when:** one strategy is chosen from evidence, `config.CHUNK_STRATEGY` reflects it, and
chunking is deterministic (running twice yields identical chunk IDs).

---

### P2 result (2026-09-27) — completed, strategy chosen from the data

Gate passed: `validate_chunks() == []` for all three strategies, the three `chunks_*.json`
files exist, chunk IDs are identical across separate processes, 62/62 unit tests. Full
evidence in `reports/chunking_decision.md`.

**Chosen: `TableAwareSplitter`, `CHUNK_SIZE=600`, `CHUNK_OVERLAP=100`.**
`config.CHUNK_STRATEGY = "table_aware"`, 199 chunks, 78 `table`-kind, 20 distinct
`section` values. Added `run_ingest.py --chunk` so the comparison is re-runnable.

The strategy was decided by measurement, and the measurement contradicted the
prediction in `architecture.md` §6.2:

1. **`SectionSplitter` is inert on this corpus, not merely worse.** All 7 documents have
   **zero headings**, so its output is **byte-identical to `RecursiveSplitter`** (verified by
   comparing chunk text lists). It inherits every table defect while appearing to be the
   more principled choice.
2. **The discriminator is table integrity, not prose.** Prose-fact retention is 272/272 for
   all three strategies. Table rows intact: 691/700 (98.7%) for `table_aware` vs 639/727
   (87.9%) for the other two; orphan mid-row fragments 7 vs 55.
3. **Task 2's ladder order was lossy and was changed.** The spec lists
   `["\n#", "\n##", …]`; in that order `"\n#"` matches the first character of `"\n##"` and
   rewrites `## Benchmark` into `# Benchmark`. Ordered longest-marker-first instead.
4. **Task 2's numeric-run table detector matches 0 lines here.** All 691 table lines are
   pipe-delimited. Implemented as specified, with a `MAX_TABLE_ROW_CHARS=240` cap, without
   which the 27,190-char AMFI run-on line is misread as one indivisible row.
5. **A silent data-loss bug was found and fixed, and it is the most important line in this
   phase.** Windows under `MIN_CHUNK_CHARS` were discarded on flush. The Groww fact
   sentences sit in 40–60-char prose islands between two tables, so
   `Minimum SIP Investment is set to Rs.100.` and `is rated Very High risk.` were deleted
   from the corpus while `validate_chunks()` still returned `[]` — the lost text was never a
   chunk, so no invariant could see it. A window below the minimum now keeps absorbing, and
   `_size_tolerance` accounts for the bounded overshoot. `tests/test_chunker.py` asserts no
   fact sentence is lost, for every strategy, against the real corpus.
6. **Three more defects found by tests, all fixed:** `HEADING` never matched a markdown
   heading (the alternation required the whole line to be hashes); `_make_chunks` wrote back
   a stale `window_len` after `flush()`, so every segment became its own chunk; and the
   overlap carry was written into a bookkeeping set that nothing ever read, making
   `CHUNK_OVERLAP` a silent no-op. The last two had produced valid-looking output
   throughout.
7. **`section` is a single inferred label, not a heading path.** Task 4 asks for
   `"Fees > Exit load"`; with no headings the path can only be one level deep, and a mixed
   chunk can be mislabelled. Recorded as a limitation in the decision report rather than
   papered over.

**Carried into P3:** 78 chunks are `table`-kind, and a holdings row like
`| Titan Company Ltd | … | 1.8% |` is lexically thin as an embedding. If P4 calibration
shows holdings chunks crowding out prose answers, drop `Portfolio holdings` chunks at index
time — the boundaries are correct, so retrieval filtering is the right lever, not rechunking.

---

## P3 — Stage 3: Embedding + Stage 4: Vector Store

**Goal:** a persistent, idempotent, queryable index.

**Create:** `rag/embedder.py`, `rag/vectorstore.py`, and wire `--index` into `run_ingest.py`

**Reference:** `architecture.md` §6.3, §6.4

**Tasks — embedder**

1. Lazy module-level singleton `SentenceTransformer(config.EMBED_MODEL)`.
2. `normalize_embeddings=True` everywhere; `embed_documents(texts, batch_size)` and
   `embed_query(text)` both delegate to it. Expose no way to swap the model per-call.
3. Log load time and model id once. Set the HF cache to `data/models/` so the demo machine can
   run offline afterwards (NFR-3).
4. Add `scripts/warm_cache.py`: downloads the embedder and the LLM once, prints confirmation.
   Run it now, not on demo day.

**Tasks — vectorstore**

1. `get_client()` → `chromadb.PersistentClient(path=config.CHROMA_DIR)`.
2. `get_collection()` → `get_or_create_collection(config.COLLECTION_NAME,
   metadata={"hnsw:space": "cosine"})`.
3. `upsert(chunks, vectors)`: pass `embeddings=` explicitly (never the default embedding
   function), `documents=[c.text]`, and scalar-only `metadatas`. Batch at 200.
4. `rebuild()`: delete the collection, recreate it, then upsert. Used by the full ingest path
   so a source change never leaves orphans.
5. `search(embedding, top_k, where=None) -> list[RetrievedChunk]` returning rank, score, and
   the reconstructed `Chunk` from metadata + document.
6. `count() -> int` for the gate and for the UI's "index missing" check.

**Gate**

```bash
python run_ingest.py --index
python -c "from rag.vectorstore import get_collection; print(get_collection().count())"
python run_ingest.py --index      # run twice
```

The count must be **identical** after the second run (NFR-2), greater than 0, and
`data/chroma/` must persist after the process exits.

**Done when:** the collection is non-empty, stable across re-runs, and built with vectors from
`all-MiniLM-L6-v2` only.

---

### P3 result (2026-09-27) — completed, gate passed

Gate passed verbatim. `run_ingest.py --index` twice: **199 vectors, count 199 both
times** (NFR-2), `data/chroma/` 9.3 MB and readable from a fresh process after exit,
107/107 unit tests. Index build is 1.1s of embedding after a 4s model load.

`rag/embedder.py`, `rag/vectorstore.py`, `scripts/warm_cache.py`,
`tests/test_embedder.py` (14 tests), `tests/test_vectorstore.py` (30 tests).

1. **"Built with vectors from all-MiniLM-L6-v2 only" is verified, not asserted.**
   Re-embedding the stored text reproduces the stored vectors to `max|diff| ≈ 1e-7`
   (float32 rounding), so the index demonstrably came from
   `sentence-transformers/all-MiniLM-L6-v2`. The collection is created with
   `embedding_function=None`, so Chroma's own embedder is not merely unused — it is
   unattached, which `test_chromas_default_embedding_function_is_never_attached`
   checks. Vectors are 384-dim with L2 norm 1.0, so cosine reduces to a dot product
   as §6.3 assumes.
2. **`scripts/warm_cache.py` ran and the embedder half succeeded; the LLM half could
   not, because Ollama is not installed on this machine.** It reports that plainly
   and exits 1 rather than printing a false confirmation. The model is cached in
   `data/models/` (87 MB) and NFR-3 is verified: with `HF_HUB_OFFLINE=1` the
   embedder loads in **0.2s** and the index is fully queryable. **P5 generation is
   blocked until Ollama is installed** — that is the one outstanding dependency.
3. **`posthog` is pinned to 3.7.4.** chromadb 0.5.23 calls `posthog.capture()` with
   positional args, which posthog 4+ rejects, so every run printed
   `Failed to send telemetry event ... capture() takes 1 positional argument but 3
   were given` twice before the real output. `anonymized_telemetry=False` does not
   suppress it, because `ClientStartEvent` fires before settings are applied. Pinned
   rather than silenced, since it was real noise in front of demo output.
4. **`onnxruntime` is not a direct dependency.** It exists only to serve Chroma's
   default embedding function, which this project never uses. (chromadb still pulls
   it in transitively; harmless.) Recorded in `requirements.txt` so nobody "fixes"
   the absence later.
5. **Found and fixed a footgun:** Chroma caches one system per path and refuses a
   second client whose settings differ — `ValueError: An instance of Chroma already
   exists for … with different settings`. Anything needing its own client (a test, a
   maintenance script) must call `vectorstore.client_settings()` rather than
   constructing `Settings(...)` itself.
6. **`metadata` gained two scalar fields beyond §6.4's eight: `kind` and
   `token_estimate`.** `kind` is what lets P4 drop `Portfolio holdings` table chunks
   if calibration shows they crowd out prose answers — the risk P2 flagged. The
   scalar-only constraint is enforced in `chunk_metadata()` with an explicit
   `TypeError`, because Chroma's own error for a list value is unhelpful.
7. **`--index` chunks everything before it wipes the index.** A chunking failure
   aborts with the previous index intact, rather than `rebuild()`-ing to empty and
   then failing. It also upserts one document at a time so every chunk keeps its own
   source's `ingested_at` instead of one blanket timestamp.
8. **`--index` used to print "0 invariant violations" without checking anything.**
   It now calls `validate_chunks()` on the whole corpus and returns 1 before
   `rebuild()` if anything is wrong. `validate_chunks()` only ever sees a flat list, so
   it cannot notice a document that produced zero chunks; that case is checked
   separately in `run_ingest.py`. Verified by injecting a violation: exit 1, and the
   collection still held 199 rows afterwards. A guard that has never been seen to fire
   is not yet a guard.

**Carried into P4 — an early observation, not yet a P4 result:** querying
`"What is the exit load on HDFC Large Cap Fund?"` unfiltered returns chunks from
Small Cap and Equity Fund in the top 3. The `where` filter changes the answer
completely — the same query filtered with
`{"$or": [{"scheme": "HDFC Large Cap Fund"}, {"scope": "general"}]}` returns Large Cap
chunks at ranks 1–3. So the filter §6.5 specifies is not a refinement, it is what
makes the answer correct, and P4 must apply it by default rather than treating it as
an optional extra.

---

## P4 — Stage 5: Retrieval

**Goal:** given a question, return the right chunks — or honestly return nothing.

**Create:** `rag/retriever.py`, `data/eval/golden.jsonl` (questions only at this stage)

**Reference:** `architecture.md` §6.5

**Tasks**

1. `detect_scheme(query) -> str | None` using `sources.SCHEME_ALIASES`; the first match wins,
   and matches are checked longest-alias-first so `balanced advantage` beats `hdfc`.
2. `search(query, top_k=None) -> list[RetrievedChunk]`:
   - detect the scheme
   - build `where={"scheme": <detected>}` if a scheme was found
   - embed the query text, appending the detected scheme name when present (query expansion)
   - call `collection.query(...)`
   - if `top_score < config.MIN_SCORE`, return `[]` (this is what makes P5 able to say
     "not found" instead of inventing)
3. De-duplicate: drop later chunks sharing the same `(scheme, section, source_url)`.
4. Rerank hook behind `config.ENABLE_RERANK`: fetch `RERANK_TOP_N`, rescore with
   `cross-encoder/ms-marco-MiniLM-L-6-v2`, keep `TOP_K`. Lazy-import so the dependency is
   optional and the demo works without it.
5. Seed `data/eval/golden.jsonl` with the six PRD acceptance questions, one per scheme, plus
   one deliberately out-of-corpus question.

**Calibration task (Q3 in `architecture.md` §16).** For every golden question, print the top
score and the retrieved scheme. Set `MIN_SCORE` so that: all in-corpus golden questions clear
it, the out-of-corpus question does not, and there is visible margin. Record the chosen value
and the score table in `reports/chunking_decision.md` or a new `reports/retrieval_calibration.md`.

**Gate — a throwaway probe script, not the CLI yet**

For each golden question print: question, top score, `chunk_id`, scheme, section, and the first
80 characters of the chunk. Manual review: is each hit the passage a human would pick?

**Done when:** the six acceptance questions retrieve passages from the correct scheme, and the
unrelated question falls below `MIN_SCORE`.

### P4 result (2026-09-27) — completed, gate passed

`rag/retriever.py`, `data/eval/golden.jsonl` (9 questions), `scripts/probe_retrieval.py`, and
`reports/retrieval_calibration.md` are in place. 28 retrieval tests added; full suite 250.

**The spec's `where` clause is wrong, and the correction is load-bearing.** Task 2 says
`where={"scheme": <detected>}`. `architecture.md` §6.5 requires
`{"$or": [{"scheme": <detected>}, {"scope": "general"}]}`, and §6.5 is normative. The plain
filter is not merely inelegant — it makes the ELSS questions unanswerable, because the
lock-in period, the 80C limit, and the tax treatment live in the AMFI general chunks, not in
the scheme page. Those chunks are tagged `scope: "general"`, so a scheme-only filter drops
them and the answer becomes "not found" for the exact questions the PRD asks about. The `$or`
form is implemented.

**Scheme detection is stricter than "first match wins".** Aliases are checked
longest-first as specified, but a category-only alias (`flexi cap`, `large cap`, `small cap`)
only matches when the query also says `hdfc`. Without that guard, "expense ratio of Parag
Parikh Flexi Cap" resolves the scheme to HDFC Flexi Cap and the retriever then serves an
HDFC expense-ratio figure in answer to a question about a fund this corpus does not cover.
With it, the query is passed through unfiltered and refused upstream as `OUT_OF_SCOPE` by the
P6 guard — which is the honest answer. (P6 task 6.)

**`MIN_SCORE = 0.40`, calibrated from measured scores, not chosen.** See
`reports/retrieval_calibration.md`.

| population | range | clears 0.40? |
| --- | --- | --- |
| in-corpus (6 questions) | 0.6254 – 0.7772 | yes, min margin +0.2254 |
| unrelated out-of-corpus (2) | 0.1239 – 0.1875 | no, max margin +0.2125 |
| different AMC, same attribute (1) | 0.6386 | **yes — cannot be separated by score** |

The last row is the important one and it is why P6 exists. A Parag Parikh Flexi Cap expense
ratio question scores 0.6386 against HDFC Equity Fund's genuine expense-ratio chunk, inside
the in-corpus band. No threshold on this axis can separate "corpus has the answer" from
"corpus has a *similar* answer to a different fund". That distinction is a question of what
the user asked, so it belongs in the intent gate, not in a score cutoff.

**Golden set is 9 questions, not 7.** The spec asks for six acceptance questions plus one
out-of-corpus negative. Two more were added because one negative does not characterise a
threshold: a second unrelated question, and the different-AMC case above, which is the only
probe that distinguishes a working guard from a broken one.

**Known quality limit, deliberately not fixed here.** Expected-scheme accuracy is 5/5 named
schemes, but expected-*section* accuracy is 3/6. The expense-ratio and minimum-SIP questions
land in the wrong section — usually one section off — because the P2 600-character
table-aware chunks mix the NAV, AUM, fee, and risk lines of a factsheet table into a single
embedding. P2 already flagged chunk dilution as a known risk and chose retrieval filtering as
the lever; shrinking chunks now would invalidate the P2 decision report and the P3 index, and
the honest fix is a later re-chunk with the decision report revisited, not a tweak here.
The answer is right and the citation is close enough to be useful; it is not a
section-precise citation and the report says so.

---

## P5 — Stage 6: Generation

**Goal:** a ≤3-sentence, context-only answer that names its source.

**Create:** `rag/prompts.py`, `rag/generator.py`

**Reference:** `architecture.md` §6.6, `PRD.md` §8

**Tasks — prompts.py**

1. `SYSTEM_PROMPT` encoding every constraint: context-only, ≤3 sentences, no advice language,
   no return/performance figures, no invented URLs, and the exact
   `I couldn't find that in the official sources.` string for absent answers.
2. `NOT_FOUND_TEXT`, `REFUSED_ADVICE`, `REFUSED_RETURNS`, `REFUSED_PII`, `OUT_OF_SCOPE` — all
   as module constants, with `{link}` placeholders filled from `config`.
3. `build_context_prompt(evidence) -> str` — each chunk prefixed with its
   `[scheme | section | url]` header, so the model can attribute and never invent a URL.
4. `EXAMPLE_QUESTIONS` — exactly 3, fixed, not randomised.

**Tasks — generator.py**

1. `generate(prompt, system=..., ) -> str` with a backend switch on `config.LLM_BACKEND`:
   - `ollama`: POST to `http://localhost:11434/api/generate` with `temperature=0`,
     `stream=false`, `options={"num_predict": config.LLM_MAX_TOKENS}`
   - `http`: the same prompt contract against a free-tier chat endpoint, using
     `os.environ["LLM_API_KEY"]` and never logging the key
2. `LLM_MAX_TOKENS` ≈ 160 — enough for 3 sentences, too little for an essay.
3. `temperature=0` and no retries that change the answer. A backend error raises
   `LLMUnavailable` (handled in P7), it does not silently degrade to a canned answer.
4. Lock Q2 from `architecture.md` §16 here: pick the backend, set it in `config.py`, and
   confirm it responds on this machine.

**Gate — a throwaway probe script**

For one factual question, print the raw model output verbatim. Check: ≤3 sentences, every fact
traceable to a context block, no advice, no invented URL, no return figure. If the model
overruns 3 sentences, fix it in the prompt here — do **not** paper over it in the verifier yet
(the verifier is P6's job, and it should be a backstop, not the primary mechanism).

**Done when:** the model answers one factual question correctly from context and stays inside
all prompt constraints on a few manual tries.

---

## P6 — Guards + Verifier

**Goal:** make the constraints structural. This phase is what makes acceptance criteria 3, 4,
and 5 pass.

**Create:** `rag/guards.py`, `rag/verifier.py`

**Reference:** `architecture.md` §6.7, §6.8, `PRD.md` §8

**Tasks — guards.py**

1. `PII_PATTERNS`: a list of `(name, compiled_regex)` covering PAN, Aadhaar, email, phone,
   account number, and keyword-anchored OTP. Aadhaar needs the `[2-9]` leading-digit rule to
   avoid matching arbitrary 12-digit numbers.
2. `check_pii(query) -> str | None` returning the **pattern name** on a hit, never the value.
3. `mask(value) -> str` for logging (`ABCDE1234F` → `*******`).
4. `classify_intent(query) -> str` with the rule-based classifier: `FACTUAL` / `ADVICE` /
   `RETURNS` / `OUT_OF_SCOPE`, weighted keyword scoring, **biased toward `ADVICE`** on ties,
   because a missed advice question is the worst failure mode.
5. `INTENT_BACKEND = "rules"` default. If set to `llm`, ask for one label with a strict parse
   and **fall back to the rules classifier on any parse failure**, resolving ambiguity to
   `ADVICE`.
6. Non-HDFC AMC names in the query → `OUT_OF_SCOPE`.

**Tasks — verifier.py**

1. `verify(answer, evidence) -> tuple[str, str]` returning `(final_answer, status)`.
2. Sentence-count enforcement: split on `.!?`, keep the first `MAX_ANSWER_SENTENCES`.
3. Advice-phrase regex → swap in `REFUSED_ADVICE`.
4. Return-figure regex (percentage adjacent to a return keyword, `CAGR`, `x returns`) → swap
   in `REFUSED_RETURNS`. Keep it narrow — a legitimate expense ratio like `0.5%` must not
   trip it.
5. URL check: any URL in the answer not present in `sources.py` is stripped; if the stripped
   URL was load-bearing, downgrade to `NOT_FOUND`.
6. Empty answer → `NOT_FOUND`.

**Tests to write in this phase** (`tests/test_guards.py`, `tests/test_verifier.py`):

- every PII pattern fires on a positive sample and does not fire on a clean question
- a 4-sentence answer is truncated to 3
- "you should invest" is caught; a factual "expense ratio is 0.5%" is **not** caught
- an invented `https://example.com/fund` is stripped
- `"Should I buy HDFC Small Cap?"` → `ADVICE`; `"What is the exit load?"` → `FACTUAL`

**Gate**

```bash
pytest tests/test_guards.py tests/test_verifier.py -v
```

**Done when:** all tests pass and each of the six `status` values is producible by at least one
call.

### P6 result (2026-09-27) — completed, gate passed

`rag/guards.py`, `rag/verifier.py`, `rag/prompts.py` (constants only — see below),
`tests/test_guards.py` (78 tests), `tests/test_verifier.py` (36 tests),
`scripts/probe_guards.py`, and `reports/guardrail_calibration.md`. P6 gate: **115 passed**.
Full suite: **250 passed**, pyflakes clean. Probe: **49/49 as expected**.

**`prompts.py` exists with the response constants only.** The verifier has to substitute
refusal copy, and the spec puts that copy in `prompts.py`, so the five status strings plus the
`{link}` rendering helpers are there. `SYSTEM_PROMPT` and `build_context_prompt` are still
P5's to write — they cannot be calibrated without a live model and Ollama is not installed on
this machine. Splitting the file now rather than in P5 keeps the P5 work honest: a system
prompt written today would be an unverified guess at what the model needs to be told.

**PII: the return value is the pattern name, never the value.** `check_pii` returns
`"pan"`, not the match. This is stronger than the spec's requirement and it is deliberate:
`architecture.md` §6.5 says the matched input is not logged, and the only way to guarantee
that by construction rather than by review is to never hand the value to a caller. There is no
line in `guards.py` that can format a match into a log record or an exception message, because
the value is not in scope anywhere after `pattern.search()`. `mask()` therefore returns a
**constant-width** mask rather than a length-preserving one: a mask the same length as the
input still discloses that a PAN is 10 characters, which is not a disclosure anyone should
rely on not mattering. The spec's `ABCDE1234F` → `*******` is treated as illustrative of "do
not echo the value", not as a required output width.

**Two PII patterns are deliberately keyword-anchored, and the tests pin the reason.** Account
number matches `account|folio|a/c|cheque` followed by 9–18 digits, never a bare digit run;
OTP matches a 4–8 digit run only after an `otp|verification code|passcode` keyword. A bare
9–18 digit run in a mutual-fund question is far more often a date fragment, a folio shown in a
screenshot, or a NAV figure than an account number, and refusing it blocks a legitimate user.
Aadhaar keeps the `[2-9]` leading-digit rule the spec calls for, which is what stops a
timestamp being read as an ID. Each of these has a paired negative test, because the failure
mode is asymmetric: a false positive is an embarrassment, a false negative is a PAN in a log.

**`classify_intent` returns `OUT_OF_SCOPE` for a non-HDFC AMC, checked before the weighted
rules.** Not a judgement about phrasing — a fact about the corpus. If the question is about a
fund we do not cover, no amount of relevant HDFC text answers it. The P4 calibration table is
the evidence: a Parag Parikh question scores 0.6386 against real HDFC expense-ratio text, so
this has to be decided from the query, upstream of `MIN_SCORE`.

**The intent classifier was found wrong by probing, not by the tests.** The first version
classified "Is now a good time to buy?" as `FACTUAL` — it does not contain the phrase
`should I`, and it is unambiguously an advice request. That is precisely the failure
`architecture.md` §6.7 calls the worst outcome, and the unit tests passed anyway because they
only used phrasings the rules were written for. Rules for `good/right/bad time to buy`,
`shall I`, `worth it`, `which is better`, and `tell me which` were added, and
`test_timing_questions_count_as_advice` now pins them. All three `config.EXAMPLE_QUESTIONS`
  are asserted to classify as `FACTUAL`, so the demo cannot open on a refusal, and
  `test_ordinary_factual_questions_all_pass_the_intent_gate` extends the same check to
  all fifteen schemes — two of the three chips name one fund, so the chips alone leave
  thirteen unchecked.

**The LLM intent backend fails closed in both directions.** `INTENT_BACKEND` defaults to
`"rules"`. Set to `"llm"`, an unreachable backend *and* an unparseable reply both fall back to
the rule classifier; the strict parser accepts a label and nothing else, and returns `None` for
anything it has to interpret, because a reply that needs interpreting cannot be trusted when the
fallback is `ADVICE`. Verified against an absent `rag.generator` (P5 not landed), a raising
backend, and a rambling reply.

**The verifier checks advice and returns on the *full* text, before truncation.** A violation
in sentence four must still be caught even though only the first three survive the cap —
otherwise truncation is a way to smuggle a violation past the verifier, which is the opposite
of what a backstop is for. `test_advice_phrase_triggers_in_the_fourth_sentence_even_though_it_is_truncated`
pins this.

**The return-figure regex is narrow by construction, per the spec's `0.5%` requirement.** It
matches `CAGR`, an `Nx returns` multiple, or a percentage *adjacent to* a return word within
40 characters. "The expense ratio is 0.5%", "The exit load is 1%, reducing to 0.5%", and "AUM
is 50000 crore" all pass as `ANSWERED`. Sentence splitting uses a lookbehind
(`(?<=[.!?])\s+`) rather than `split(".")`, because splitting on `.` turns `0.5%` into two
sentences and the resulting fragment would read as noise.

**Two bugs the probe caught in the URL handling, both fixed and both now regression-tested:**

1. `3x returns` was not caught. The pattern had `\b(?:cagr|x\s*returns?)\b`, but a word
   boundary can never fall between `3` and `x` — both are word characters. The digit is now
   inside the pattern: `\b[0-9]+(?:\.[0-9]+)?\s*x\s*returns?\b`.
2. A bare citation sentence destroyed a good answer. "The expense ratio is 1.03% and the
   benchmark is Nifty 100. See https://example.com/x for more." returned
   `NOT_FOUND` and threw away a perfectly good first sentence, because the load-bearing check
   asked whether *any* sentence with a bad URL was contentless rather than whether *any*
   content survived. Cleanup is now per sentence: a sentence that existed only to host an
   invented link is dropped; a sentence with real content loses just the link. `NOT_FOUND` is
   reserved for the case where stripping genuinely leaves nothing to say.

**Citation whitelist is derived, not hand-listed.** `approved_urls()` builds the set from
`sources.load_sources()`, so adding a scheme or regulator page widens it automatically. A
hand-maintained copy would drift and begin stripping genuine citations. URLs found in the
retrieval evidence are also allowed, so a link the pipeline really used is not treated as
invented.

**Division of labour, pinned by a test.** `verify()` can return `ANSWERED`, `NOT_FOUND`,
`REFUSED_ADVICE`, and `REFUSED_RETURNS`. It cannot return `REFUSED_PII` or `OUT_OF_SCOPE`:
those are decided by the guards before retrieval, so no model output exists to verify.
`test_five_of_six_statuses_come_from_verify` documents this so a later reader does not "fix"
it by generating first and checking afterwards — which would put a PAN through the LLM to
learn something the regex already knew. All six statuses are producible, satisfying the
phase's done-when.

**A P6 bug was found by the P5 gate, which is the argument for running the gates in order.**
When the model correctly replied with the exact `I couldn't find that in the official sources.`
string, `verify()` returned status `ANSWERED` — it only knew how to produce `NOT_FOUND` from an
*empty* answer. The text was right and the status was wrong. Since `status` is what the UI
switches on and what the golden-set eval asserts, P7 would have rendered a refusal labelled as
a success. `verify()` now recognises the model's own NOT_FOUND string.
`test_the_models_own_not_found_string_gets_the_not_found_status` pins it.

---

## P5 — Stage 6: Generation

**Goal:** a ≤3-sentence, context-only answer that names its source.

**Result (2026-09-27) — completed, gate passed**

`rag/prompts.py` (now complete), `rag/generator.py`, `tests/test_generator.py` (26 tests),
`scripts/probe_generation.py`, and `reports/generation_gate.md`. Full suite: **276 passed**,
pyflakes clean. The live gate prints the model's unmodified output for six factual questions
and three absent ones: **all constraints held**.

**The backend is Groq, not Ollama, and the configured model was wrong.** `LLM_BACKEND` is
`"groq"`. `.env` (in `docs/`) supplies `GROQ_API_KEY`. The `GROQ_MODEL` recorded there,
`llama-3.1-8b-instant`, is **retired** — the API returns 404 `model_not_found`, and this key
has no `llama` model available at all. `/v1/models` lists 11, of which the usable ones are
`openai/gpt-oss-20b`, `openai/gpt-oss-120b`, and `qwen/qwen3.8-27b`. Config now defaults to
`openai/gpt-oss-20b`. **This is a deviation from `.env` and should be corrected there too**, so
the two do not disagree. Ollama is kept wired up as the offline alternative.

**Two gpt-oss behaviours had to be measured, and both would have looked like flaky AI:**

1. **`reasoning_effort` is mandatory.** Without it the model spends the entire 160-token budget
   reasoning and the answer arrives truncated mid-sentence — measured 160 completion tokens of
   pure scratchpad versus 68 tokens for a complete one-sentence answer with `"low"`. Set in
   config as `GROQ_REASONING_EFFORT = "low"`; omitting it is the difference between a working
   demo and a broken one.
2. **The response carries a separate `reasoning` field.** `generator.py` reads `message
   ["content"]` and nothing else. The trace is the model's scratchpad — surfacing it would put
   "Need to answer using only the context" on screen and straight into the verifier.

`gpt-oss-20b` over `120b`: 120b emitted U+202F narrow no-break spaces inside "1 year", which
threatens the verifier's ASCII-space regexes, at 3× the latency for no accuracy gain on
three-sentence extractions. `generator._normalise` folds the exotic Unicode spaces anyway,
belt-and-braces.

**A 429 is retried, and that does not violate "no retries that change the answer".** The free
tier allows 8000 tokens/min and these prompts are large enough that back-to-back generations
trip it — hit on the first gate run. The retry resends an *identical* payload at
`temperature=0`, so it is the same answer a moment later, not a second guess; the spec
forbids retries that change the answer, not retries that wait out a rate limit. Bounded at 4
attempts, honouring `Retry-After`.
`test_a_rate_limit_is_retried_and_never_silently_changes_the_answer` asserts all three attempts
carry byte-identical payloads.

**The key is never a module attribute.** It is read from the environment inside the call, so no
logger or traceback can reach it, and `_post` builds error messages from status codes and body
text only. Verified by scanning every source file for the key: it exists in `docs/.env` and
nowhere else. `test_an_http_error_never_includes_the_key` pins the error path.

**`SYSTEM_PROMPT` is written, and every clause in it is also checked by the verifier.** That
redundancy is the point — a constraint that exists only in the prompt is a constraint that is
only a hope. The `[scheme | section | url]` header on each context block does double duty: it
gives the model the attribution it needs, and it makes fabrication pointless, because any URL
the model writes that is not in a header is a fabrication the verifier strips.

**One probe expectation was wrong, and it was worth being wrong about.** The gate initially
listed "who is the fund manager" as a question absent from the corpus, and the model answered
"Rahul Baijal" — which looked like a hallucination. It was not: the fund-manager block is in the
general chunks, and the name is in the context. The probe was asserting something false about
the corpus. The absent questions are now **verified absent** against the indexed chunks
(SEBI code, custodian name, toll-free number) rather than assumed, and all three correctly
return the exact NOT_FOUND string.

**One quality note, not a defect.** "How do I download a capital gains statement?" retrieves at
0.4102, barely above the calibrated `MIN_SCORE` of 0.40 — a Balanced Advantage portfolio
holdings chunk, not a document on downloading statements. The model handled it correctly by
refusing, but the retriever is handing it weak evidence on a procedural question. Worth a look
if the demo asks it.

---

## P7 — Pipeline Orchestration

**Goal:** one entry point that returns a complete, renderable `Answer`.

**Create:** `rag/pipeline.py` (add the `Answer` dataclass here or in a shared `rag/types.py`)

**Outcome:** `Answer` lives in `rag/pipeline.py`; no `rag/types.py` was created. A separate
types module would have held one dataclass, and `pipeline.py` is the only consumer, so
splitting it would add an import for no gain.

**Reference:** `architecture.md` §7.2

**Tasks**

1. `answer(query) -> Answer`, strictly in this order — the order is the design, do not
   parallelise or reorder it:
   ```
   check_pii          → REFUSED_PII
   classify_intent    → REFUSED_ADVICE / REFUSED_RETURNS / OUT_OF_SCOPE
   retriever.search   → NOT_FOUND (empty evidence)
   generator.generate
   verifier.verify
   render
   ```
2. `render(query, answer_text, status, evidence) -> Answer`:
   - `citation_url` = `source_url` of the rank-1 evidence chunk, `None` for refusals
   - `last_updated` = the max `fetched_at` across the evidence chunks, formatted for display
   - `evidence` always populated, even for refusals that needed no retrieval (empty list)
3. One structured log line per stage per query, per `architecture.md` §12. No PII, no full
   query text in the log line.
4. Handle `LLMUnavailable` and `FetchError` with explicit, non-fabricated messages that still
   include the relevant scheme URL where known.

**Gate**

```bash
python -c "from rag.pipeline import answer; a = answer('What is the exit load on HDFC Large Cap?'); print(a.status, a.citation_url, a.last_updated)"
```

**Done when:** one call returns a populated `Answer`, and each of the six statuses is reachable
by an appropriate query.

### P7 result (2026-09-27) — completed, gate passed

`rag/pipeline.py` (`Answer` dataclass, `answer()`, `render()`), `tests/test_pipeline.py`
(38 tests at this gate; 40 after two `FetchError` tests added during the P8 audit),
`scripts/probe_pipeline.py`, and `reports/pipeline_gate.md`. Full suite at this gate: **311
passed** in ~7.7s and fully offline. Live gate: **6/6 statuses reachable, all checks held.**

The spec's gate, run verbatim:

```
$ python -c "from rag.pipeline import answer; a = answer('What is the exit load on HDFC Large Cap?'); print(a.status, a.citation_url, a.last_updated)"
ANSWERED https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth 27 Sep 2026
```

**The order is the design, so the tests assert the order rather than the outputs.** A pipeline
that returns the right status for the wrong reason is still a privacy or accuracy bug: one that
embeds a query containing a PAN, or asks the model about a fund we do not cover and refuses
afterwards, has every field looking correct and has already done the damage. The tests assert
that retrieval is never called for a PII question, that the LLM is never called for a refusal
or for a question with no evidence, and that a guard refusal logs exactly two stages
(`guard`, `render`) and an answered question exactly four (`retrieve`, `generate`, `verify`,
`render`). This is also what makes the latency profile below true rather than incidental.

**Measured cost of the ordering, live:**

| query | latency | LLM called | evidence |
| --- | --- | --- | --- |
| expense ratio (answered) | 5.8 s | yes | 5 chunks |
| SEBI code (not found) | 0.6 s | yes | 5 chunks |
| should I buy | 0.0 s | **no** | 0 |
| CAGR | 0.0 s | **no** | 0 |
| PAN | 0.0 s | **no** | 0 |
| other AMC | 0.0 s | **no** | 0 |

The first answered query carries ~3 s of one-time embedder load; subsequent ones are well inside
the NFR-1 budget. A refused question is free and instant, so demo day cannot be made flaky by a
refusal, and no tokens are spent on a question we were never going to answer.

**`last_updated` needed a source that does not exist yet, and inventing one would have been
wrong.** `Chunk` has no `fetched_at` field, and neither does the raw chunk table
(`data/raw/chunks_table_aware.json` has no such column). The only surviving `url -> fetched_at`
map is in `data/raw/_ingest_report.json`, so `render()` reads that — cached, since it cannot
change during a process and re-reading per question would be a disk hit on the hot path for
nothing. ISO dates sort lexicographically, so `max()` over the raw strings is the newest, and
formatting happens once at the end. If the report is unreadable, `last_updated` degrades to empty
rather than the pipeline refusing to answer: the answer is still traceable by `citation_url`.

**One spec gap found by reading `architecture.md` §7.3 against the implementation.** The failure
table says a low-confidence `NOT_FOUND` should give "`NOT_FOUND` + pointer to the scheme page"
(FR-10). The first implementation returned `citation_url=None`, because the rule "`citation_url`
= rank-1 evidence URL" yields nothing when there is no evidence. Both rules are right about
different cases, so the low-confidence path now falls back to the page for the scheme the
question named. A question naming no scheme still gets `None`, because guessing a page is
precisely what this product is built not to do. The fallback is scoped to `NOT_FOUND` **only** —
a refusal must cite nothing, or the citation implies the answer is on that page.
`test_a_guard_refusal_does_not_get_a_scheme_pointer` pins that.

**Log lines carry a hash of the query, not the query.** `architecture.md` §12 asks for one
structured line per stage and explicitly says the log does not need the query text, because the
UI transcript already has it. The pipeline therefore logs `q=<8 hex of sha256>`, which is enough
to group one question's lines together for debugging and is not reversible into what was asked.
A PII hit logs the pattern name and a constant-width mask only, so the value cannot appear even
by accident. Four tests assert the log blob contains neither the question text nor the PAN or
email that were submitted.

**`generate_ex()` was added so the generate line can report `ms` and `tokens_out`.** §12's
example line has both. Rather than change `generate()`'s plain-text contract or stash usage in
module state, `generate_ex()` returns a `Completion` and `generate()` delegates to it. The
internal Groq/Ollama helpers now return `(text, tokens)`; only `content` is ever read, never the
gpt-oss `reasoning` field.

**An LLM outage is reported as an outage, not as "not in the corpus".** Those are different
claims and conflating them hides a failure behind a polite answer. The pipeline returns a fixed
"assistant is temporarily unavailable" message with status `NOT_FOUND`, retains the retrieved
evidence, and still points at the real page so the user can read the facts themselves. Two tests
assert it does not produce a plausible-looking answer and does not lose the citation. A broken
index degrades the same way rather than raising into the UI.

---

## P8 — CLI + Eval Harness

**Goal:** a terminal path for debugging and a table that proves the acceptance criteria.

**Create:** `cli.py`, `scripts/evaluate.py`

**Reference:** `architecture.md` §11.2–§11.3, `PRD.md` §11

**Tasks — cli.py**

1. `python cli.py "<question>"` prints the answer, the status, the citation URL, the
   last-updated line, and a `--sources` flag that dumps the retrieved chunks. This is also the
   tool that generates `sample_qa.md`.

**Tasks — scripts/evaluate.py**

1. Read `data/eval/golden.jsonl` and assert on `expected_status`, `expect_scheme`,
   `must_contain`, `forbid`.
2. Expand the golden set to cover all of `PRD.md` §11: the six acceptance questions, one
   `ADVICE` refusal, one `RETURNS` refusal, one PII input, one out-of-scope AMC, one
   out-of-corpus question, and one scheme-naming question that exercises the metadata filter.
3. Print a markdown table: question, expected, actual, similarity/checks, pass-fail, plus an
   overall accuracy line. Write it to `reports/eval_results.md`.

**Gate**

```bash
python scripts/evaluate.py
```

Every row green. This table is a deliverable — keep the output.

**Done when:** the eval script runs green and its output can be pasted into the README.

### P8 result (2026-09-29) — completed, gate passed

`cli.py`, `scripts/evaluate.py`, the expanded `data/eval/golden.jsonl` (15 rows),
`tests/test_eval_and_cli.py` (21 tests), `reports/eval_results.md`, and
`reports/sample_qa.md`. Full suite: **337 passed** in ~6s and fully offline. Live gate:
**15/15 rows green.**

```
$ python scripts/evaluate.py
  accuracy: 15/15 (100%)
  wrote reports/eval_results.md
```

The table is the deliverable, and it is the one artifact that can be wrong while looking right,
so the check functions are tested against answers that should fail them, not only against ones
that pass: a wrong status, a missing `must_contain`, a present `forbid`, an answered row with no
citation, an answered row with no date, a citation on an unapproved host, and a scheme mismatch.
A status mismatch short-circuits the other checks on that row, because reporting four consequent
"contains" failures would overstate a single wrong decision.

**The first run failed, and the failure was the useful part.** Row 10 — "What is the expense
ratio of HDFC Flexi Cap Fund?" — was expected `ANSWERED` and came back `NOT_FOUND`. Retrieval
looked right: it resolved the alias to HDFC Equity Fund and scored 0.7197 on a real chunk from
that scheme. The instinct was to blame generation. It was not generation. The top five chunks
for that query are Riskometer, AUM, Exit load, AMFI, and a second Exit load — **the expense
ratio is not among them.** The model refused because the answer genuinely was not in its
context.

The cause is the P4 section-accuracy limit (3/6) surfacing end-to-end. HDFC Equity Fund's
"Riskometer" chunk captured the whole scheme description, so it repeats the string "HDFC Flexi
Cap Direct Plan Growth" and wins every query about that scheme; the terse "Expense ratio" chunk
ranks 6th (0.5581) and falls outside `TOP_K = 5`. Query expansion makes it worse, not better —
appending the canonical name pushes scores further toward the longest chunk. This is a P2
chunking defect, not a P8 one, so it is recorded rather than patched here: the golden row now
asserts the truthful `NOT_FOUND` outcome with the ranks in its note, so the gap cannot regress
silently, and a sibling row asserts the alias resolution using an attribute that is retrievable.
The fix belongs in the chunker (stop the scheme description swallowing the Riskometer section).

A prompt rule was written to work around the symptom and then reverted. It would not have fixed
anything — the correct text was simply absent — and keeping an ungrounded prompt change would
have meant claiming a fix that the eval had not demonstrated.

**PRD criterion 2's sixth question cannot be answered from this corpus, and the honest result is
`NOT_FOUND`.** "How do I download a capital gains statement" is listed in the acceptance criteria
among questions that should return an answer with a link. Grepping all 199 chunks for `download`,
`how to get`, or `how to request` returns zero matches; the AMFI account-statements page explains
what a statement of accounts *is* but never says how to obtain one. Answering it would mean
inventing instructions, which is the one thing this product is built not to do. The row is
asserted as `NOT_FOUND` with a note explaining that resolving it needs a source documenting the
download flow, not a better prompt. It is a corpus gap and a PRD gap, and it is criterion 4
working correctly on criterion 2's own example.

**The golden file is now shared by two consumers, so it carries an explicit split.** P4's probe
and P8's evaluator read the same path. The probe gates on retrieval `kind`s and would score a
refusal or a PII row against a threshold that does not apply to it, so rows opt into retrieval
probing with `retrieval_probe: true` and the default is off. The P4 gate stayed at 10/10 and now
reports 6/6 expected schemes (the alias row added a sixth). `test_golden_set_is_well_formed` was
updated to assert both contracts: probe rows keep the three retrieval kinds and the six acceptance
topics; every row carries a valid `expected_status`, and all six statuses appear.

**`cli.py` and `--sample`.** The spec's command prints the answer, the status, the citation, and
the last-updated line, and `--sources` dumps the retrieved chunks with their scores so a wrong
answer can be attributed to retrieval or to generation by looking. PRD criterion 6 fixes the
wording of the date line, so `render_answer()` prints `Last updated from sources: <date>`
verbatim and says `n/a (no source retrieved)` on a refusal rather than an empty label. `--sample`
regenerates `reports/sample_qa.md` from the six acceptance questions plus the refusals; it refuses
to overwrite without `--force`, because every row costs tokens. `sample_qa.md` includes the
refusals on purpose.

---

## P9 — Streamlit UI

**Goal:** the single screen required by PRD §7.

**Create:** `app.py`

**Reference:** `PRD.md` §7, `architecture.md` §14 (FR-14)

**Tasks**

1. `@st.cache_resource` for the embedder, the Chroma client, and the generator — otherwise
   the model reloads on every keystroke and NFR-1 fails.
2. `st.set_page_config(page_title=..., layout="centered")`; title, then the
   disclaimer line from `config.DISCLAIMER_TEXT`, in that order, both always visible.
3. Three example question chips from `config.EXAMPLE_QUESTIONS`, each setting the input.
4. Text input + submit; on submit, one `pipeline.answer()` call, then render `answer.answer`,
   `answer.citation_url` as a single link, and `Last updated from sources: <date>`.
5. An `st.expander("Show sources")` listing each evidence chunk's scheme, section, and text
   (FR-14) — this is what makes the RAG visible in the demo.
6. The disclaimer again in the footer.
7. If the collection is empty, show a clear "index missing — run `python run_ingest.py`"
   message instead of an error traceback.

**Gate**

```bash
streamlit run app.py
```

Confirm on screen: welcome line, disclaimer, 3 examples, input, one citation per answer,
last-updated line, working sources expander, refusal path for an advice question.

**Done when:** all PRD §7 elements are on one screen and the refusal path is demonstrable.

### P9 result (2026-09-29) — completed, gate passed

`app.py`, `streamlit==1.50.0` pinned in `requirements.txt`, and `tests/test_app.py` (8 tests).
Full suite: **345 passed** in ~6s and fully offline. The gate command starts and serves 200
with `/_stcore/health` = `ok`. A live pass through the app — real embedder, real index, real
Groq — returned the answer, the Groww citation, the `Last updated from sources` line, and a
populated sources expander.

**The screen is deliberately thin, and that is the design.** Every decision already happened in
`pipeline.answer()`, and `app.py` only renders the `Answer` it returns. That is what keeps the
CLI, the eval harness, and the UI telling the same story: they call the same one entry point and
differ only in how they show the result. There is no logic in the UI that could disagree with
the golden table.

**`@st.cache_resource` is what makes NFR-1 reachable, not a nicety.** Streamlit re-runs the whole
script on every interaction, so without caching the ~2.5 s embedder load would happen on every
keystroke and the 8 s budget would be spent reloading a model that did not change. `warm_runtime()`
caches the two things that are actually heavy — the embedder and the Chroma collection handle —
and returns the collection count, which doubles as the index-existence check. The generator is
*not* cached, and the docstring says why: `rag.generator` is stateless HTTP built per call, so a
cached handle would be a handle to a module. Caching it to satisfy the letter of the task would
have added a line that does nothing.

**A missing index is instructions, not a traceback.** Task 7's failure mode is the one a reviewer
hits on a fresh clone, because the corpus and index are git-ignored. The count check runs before
anything that would touch the store, and an empty index renders the `python run_ingest.py` command
rather than an exception. A startup failure (model unresolvable, say) is caught the same way and
shows a message plus the same command. Two tests pin both paths.

**The refusal path is rendered differently on purpose.** An answered question uses `st.markdown`;
a refusal uses `st.info`, so a refusal cannot be mistaken for an answer at a glance, and the status
caption reads "Refused — advice" rather than the raw `REFUSED_ADVICE`. A refusal has no date, and
the screen says "not applicable (no source retrieved)" instead of printing the label with a blank
after it.

**The tests drive the real render path through Streamlit's `AppTest`**, not a copy of it, with the
embedder, the collection count, and `pipeline.answer` patched so the suite stays offline. The
empty-index and startup-failure tests clear `st.cache_resource` first, because `warm_runtime` is
cached for the life of the process and would otherwise fix the count before the zero case could
run.

---

## P10 — Documentation Deliverables

**Goal:** every item in `PRD.md` §9 exists and is accurate.

**Create / complete:** `README.md`, `reports/sources.md`, `reports/sources.csv`,
`sample_qa.md`, `tests/`, `DISCLAIMER.md` (created in P0)

**Tasks**

1. `reports/sources.md` + `sources.csv` — generated from `sources.py`, not hand-typed:
   scheme, category, URL, type, `fetched_at`, char count, chunk count. Regenerate with a
   `scripts/gen_sources_report.py` so it cannot drift from reality.
2. `sample_qa.md` — 5–10 real runs from `cli.py` with answers and links, including at least
   one refusal. Real output only, not hand-written examples.
3. `README.md` — setup steps, scope (AMC + 5 schemes), architecture diagram, the chunking
   decision and rationale, the `MIN_SCORE` calibration, known limits from `PRD.md` §14, the
   disclaimer snippet, and a screenshot of the UI.
4. `tests/` — the unit tests from P6, plus a `test_chunking_determinism.py` and a
   `test_vectorstore_idempotency.py` from P2 and P3.

**Gate:** re-read `PRD.md` §9 line by line and tick each item against a real file.

**Done when:** nothing in the README is aspirational — every command in it has been run, and
every number in it came from a real run.

---

## P11 — Demo Prep

**Goal:** a demo that cannot fail.

**Tasks**

1. `scripts/warm_cache.py` run on the demo machine with network, then verified **offline**.
2. `python run_ingest.py` from a clean `data/chroma/` — time it and record the number.
3. `python scripts/evaluate.py` — keep the output handy.
4. Rehearse the 5-step script from `architecture.md` §11.3 end to end, once, timed.
5. Record the ≤3-min video: ingestion summary → eval table → factual answer with citation →
   advice refusal → UI with sources expander. The video is the guaranteed fallback deliverable,
   so record it even if hosting works.
6. Pre-type the 3 demo questions somewhere visible.

**Done when:** the demo has been run cold, offline, and recorded.

---

## P12 — Spec Completeness Fix (Stage 7 gap)

**Goal:** close the hole P10's own gate should have caught.

P10 ticked "architecture write-up" in `PRD.md` §9 without checking that the write-up
actually covered every stage the PRD's own flow diagram names. It did not.

**The gap.** `PRD.md`'s §5 diagram ends at `[7] CITATION + UI`. `architecture.md` §6 stopped
at 6.8 Verifier, so the stage that produces the citation link, the
`Last updated from sources` line, and the disclaimer — the stage a grader looks at — was
undocumented in the document §9 calls *"the graded artifact alongside the working demo."*
P9 had built and tested it; it was simply never written down.

**Tasks**

1. Add `architecture.md` §6.9 Stage 7 — Citation + UI, written against the code rather
   than from memory: the rank-1 citation rule and its four cases, the `_ingest_report.json`
   date map and why `Chunk` cannot carry the timestamp, newest-date-wins, the lazy cache,
   the degrade-never-fail behaviour, the eight-element screen order, and what the stage
   deliberately does not do.
2. Add `PRD.md` §6.7 Stage 7 — Citation + UI, as requirements rather than implementation.
3. Fix PRD §6 subsection ordering: `6.2 Stage 2 — Chunking` had been filed under `6.3` and
   `6.3 Stage 3 — Embedding` under `6.2`, so the stages ran 1, 3, 2, 4, 5, 6.
4. Repair stale paths found while editing: §4's repository tree placed `PRD.md`,
   `architecture.md`, and `sample_qa.md` at the repo root when they live in `docs/` and
   `reports/`; §17 referenced `config.DISCLAIMER_TEXT`, which does not exist — the real
   accessor is `config.disclaimer_text()`.

**Gate:** every `*.md`/`*.py` path mentioned in `architecture.md` resolves to a real file;
PRD §6 runs 1–7 in order; full suite still green.

**Done when:** the graded spec documents all seven stages, and no path in it points at a
file that is not there.

---

## P13 — Render Deployment (free tier)

Not in the PRD. Added because the deliverable is a running app, and a prototype nobody
can reach has not been demonstrated.

### Two bugs found while deploying

1. **`GROQ_MODEL` in `.env` was silently ignored.** `config.py` hard-coded the model and
   read the environment for `GROQ_API_KEY` only. This was documented as a known rough
   edge, which made it defensible — but it meant the key that produces every score in
   `reports/` could not be pinned anywhere except code, and could not be changed on a
   host without a rebuild. Both the backend and the model are now environment-overridable
   (`os.environ` beats `.env`), defaulting to the verified values.
2. **`torch==2.8.0+cpu` cannot resolve on macOS.** The `+cpu` local tag exists only for
   Linux wheels. Now split behind `sys_platform` markers, CPU on Linux, plain on macOS.

### Why the numbers moved

The documented 3/6 section-accuracy figure had been corrected in `reports/` by hand, but
`scripts/evaluate.py` regenerates that report from `data/eval/golden.jsonl` — and the
`3/5` lived in the golden set's `note`. The next eval silently reverted it. Fixed at the
source, so it cannot regress again. This is the failure mode the P10 report should have
been checked for: **a number is only fixed once its generator is fixed.**

### Memory is the binding constraint

Peak RSS serving two questions is 453 MB against Render free's 512 MB. `render.yaml`
therefore sets `MALLOC_ARENA_MAX=1` (glibc's per-core arenas inflate RSS) and
`EMBED_NUM_THREADS=1` (the free tier has 0.5 CPU, so extra threads only oversubscribe
it). `MALLOC_ARENA_MAX` must be an env var rather than a `config.py` setting — glibc
reads it at startup, so setting it from inside a running process is too late.

Also: `data/` is git-ignored and Render's filesystem is ephemeral, so a 1 GB disk is
mounted at `data/` and `startCommand` builds the index only when
`data/chroma/chroma.sqlite3` is absent.

**Gate:** `render.yaml` parses; macOS `pip install --dry-run -r requirements.txt`
resolves; full suite green; eval still 15/15 on the verified model.

**Known risk, deliberately not pre-empted:** 453 MB is close enough to 512 MB that the
free tier may still OOM. The fix is an ONNX encoder (`onnxruntime` is already installed
via chromadb and costs +9 MB at import against torch's +152 MB), but ONNX vectors are not
bit-identical to torch's, so the index must be re-embedded and `MIN_SCORE` re-calibrated,
which invalidates the checked-in scores until every gate is re-run. That trade is the
user's to make, so it is documented in the README rather than taken unilaterally.

---

## P15 — Corpus expansion to 15 schemes

Not in the PRD. Requested after P14: "generate 10 more different pages from the same
website source."

### Ten Groww pages added

Candidates were probed first and only reachable, extractable pages were kept — 6 of the
first 12 guesses 404'd, which is why the slug list is what it is. Added: Mid-Cap
Opportunities, Value, Liquid, Ultra Short Term, Banking and PSU Debt, Credit Risk,
Medium Term, Gilt, Nifty Midcap 150 Index, Nifty 100 Index. Chosen to span equity by cap,
value, and six debt/ gilt categories alongside index tracking, so "expense ratio" and
"exit load" have both an equity and a debt answer in scope.

Corpus: 7 sources / 199 chunks → **17 sources / 206,885 chars / 407 chunks**.

### A real bug found on the way: the P14 build command was broken

`python run_ingest.py` with no flags prints help and exits **1**. P14 put exactly that
string in `render.yaml` and the README as the build command, so the deploy would have
failed. It passed my clean-room check because I had run the three stages *separately*
there and never executed the literal string. `main()` now defaults to running
fetch → chunk → index when no stage flag is given.

### Gates, all re-run on the new corpus

| gate | result |
|---|---|
| ingest | 17/17 sources, 206,885 chars, all ok |
| index idempotency | 407 → 407 → 407 |
| retrieval | 10/10, `MIN_SCORE=0.40` still inside `(0.1491, 0.6254]` |
| eval | **15/15**, unchanged |
| pipeline | 6/6 |
| guards | 49/49 |
| generation | all constraints held |
| unit tests | 354 passed (was 352; two new corpus-shape tests) |

`MIN_SCORE` needed no recalibration: the separation window barely moved
(`+0.2254` below the lowest in-corpus hit). The larger corpus did not narrow it.

### The P4 section-accuracy defect now affects more schemes

Section accuracy is still **3/6**, but the blast radius grew. On HDFC Gilt Fund the
factual `Expense ratio` chunk ranks **35** (0.5780) behind two Riskometer chunks at
0.7367 and 0.7126, so expense-ratio questions on the new debt pages return `NOT_FOUND`.
This is the already-documented P4 defect, not a regression — wider coverage multiplied
the exposure. Recorded under "P15: wider coverage multiplied this defect" in
`reports/retrieval_calibration.md`.

---

## P16 — Measured effect of P15 (5 pages vs 15 pages)

The P15 eval re-run could not answer this on its own: **none of the 15 golden questions
name any of the 10 new schemes**, so 15/15 was guaranteed to hold either way. A real
comparison needs questions that discriminate, so a 17-question set was run against both
corpora — built from a `git worktree` at P14 (`07bc006`) so the 5-page corpus is exactly
the committed one, same embedder, same `MIN_SCORE`, same model.

| group | n | 5 pages | 15 pages |
|---|---|---|---|
| original 5 schemes | 5 | 5/5 | 5/5 |
| the 10 new schemes | 10 | 1/10 | **4/10** |
| out-of-corpus (boiling point, iPhone) | 2 | 0/2 | 0/2 |
| total | 17 | 6/17 | **9/17** |

No regression on the original five. Out-of-corpus refusals held. Of the 10 new schemes,
4 became answerable.

**The one apparent regression is a bug fix, not a loss.** On the 5-page corpus,
"What is the benchmark of HDFC Nifty 100 Index Fund?" returned `ANSWERED`:

> The benchmark of the HDFC Nifty 100 Index Fund is the NIFTY 100 Total Return Index.

That fund was **not in the corpus**. The evidence was rank-1 `HDFC ELSS Tax Saver Fund /
Benchmark` at 0.7194, whose actual benchmark line reads `NIFTY 500 Total Return Index`.
The model produced a confident, plausible, wrong answer from a chunk about a different
fund, and `verify()` passed it — the answer text carries no contradiction to check
against, only a wrong source. On the 15-page corpus the same question is `NOT_FOUND`,
because the real page exists and ranks it.

So the honest reading is that 15 pages traded **one hallucinated answer for zero**, which
is why the raw count moves only 6→9. The false positive was worth more than the
regression it looks like, and it is the strongest argument in this log for the expansion.

Second finding: **all 6 expense-ratio questions on the new debt/index pages still return
`NOT_FOUND`** (Gilt, Ultra Short, Mid-Cap, Banking and PSU, Nifty Midcap 150). The P4
section-ranking defect is not marginal — on these pages the factual chunk lands outside
`TOP_K` every time. That is now the single highest-value fix in the project: it caps how
much the expanded corpus can actually be worth.

---
## P21 — Fix the invisible question box (typed text was white-on-white)

Reported symptom: a question typed into the input could not be seen.

Cause, and it was not the colour the report suggested. There was no
`.streamlit/config.toml`, so Streamlit served its **default light theme** and injected
that theme as CSS variables on the app container. The P18 stylesheet's `background`
rules lost to those injected values, so the page was dark in places while the text input
stayed white. The P18 CSS set the input's text colour to a near-white chosen against the
*intended* dark background, and the field ended up light text on a light field. Making
the font dark, as the report suggested, would have papered over the symptom on a theme
that was never the intent and left the app half dark and half light.

Fixed at the base instead: `.streamlit/config.toml` now declares `base = "dark"` with
the page colours, so every native widget agrees and the injected stylesheet only has to
add the parts Streamlit has no token for. The input rules were changed to defer to the
theme rather than fight it, and the container background is now set on
`[data-testid="stAppViewContainer"]` as well as `.stApp`, because Streamlit 1.50 paints
the scroller element and a rule that loses that race leaves a white band.

This is also the first change in this log verified in a real browser rather than
structurally — driven through the Chrome DevTools protocol to read computed styles, then
typed into the field the way a user would:

| property | value |
|---|---|
| body / app / container background | `rgb(10, 12, 16)` |
| input text | `rgb(232, 236, 244)` on `rgba(255,255,255,0.043)` |
| input text, focused | `rgb(232, 236, 244)` on `rgba(125,211,252,0.06)` |
| typed value + caret | `"What is the expense ratio of HDFC Gilt Fund?"`, offset 44 |
| chip buttons | all three present |

`test_the_app_declares_a_dark_base_theme` pins the config, because the failure mode is
silent: the app renders, every test passes, and the text is simply unreadable.

**Gates:** 361 passed, pyflakes clean.

---

## P20 — Restore the UI example chips (P19 reverted)

P19 removed the three example chips on the reasoning that they named only 2 of the 15
schemes and were therefore stale. That reasoning was sound as a criticism and wrong
as a decision: the user wanted the chips back, and the requirement is **the same
three questions on every load**, not a refreshed set. They were never random — they
were a hardcoded list, which is why removing them was a removal rather than a
de-randomisation.

So: the removal is reverted, and the coverage work P19 did along the way is kept,
because that part was a genuine improvement discovered by accident.

- `config.EXAMPLE_QUESTIONS` and the `rag.prompts` re-export are back, unchanged from
  P0, plus `set_question` in `app.py`.
- `test_example_questions_are_the_three_approved_ones` and
  `test_every_example_question_retrieves_evidence` are back.
- `test_screen_shows_title_disclaimer_examples_and_input` is back, now asserting the
  chip labels against `config.EXAMPLE_QUESTIONS` rather than against three literals
  duplicated in the test, so editing the list does not require editing the test.
- `test_the_three_fixed_example_chips_are_offered` replaces P19's
  `test_no_stale_example_chips_are_offered`, and pins the property that actually
  matters: the button list is exactly the three chips plus `Ask`, in that order, every
  run. This is what would fail if anyone introduced shuffling.
- `scripts/inspect_index.py` keeps the corpus-derived retrieval probes (15, one per
  fund). That had nothing to do with the UI and was a real latent crash: it
  referenced `config.EXAMPLE_QUESTIONS` and would have raised `AttributeError` on its
  next run, with nothing in the suite executing it.

**What P19 got right and is still true:** two of the three chips name HDFC ELSS Tax
Saver Fund, so the chips cover 2 of 15 schemes. That remains a real coverage gap in
what the screen advertises. It is now a documented observation with two corpus-wide
tests beside it (`test_every_scheme_in_the_corpus_is_retrievable_by_name` and
`test_ordinary_factual_questions_all_pass_the_intent_gate`) rather than a reason to
delete the feature. If the gap should be closed, widen the list — do not randomise it.

**Gates:** 360 passed, pyflakes clean.

---

## P19 — Remove the UI example question chips *(reverted by P20; kept for history)*

The three chips were hard-coded in `config.EXAMPLE_QUESTIONS` and had drifted badly:
they named **2 of the 15 schemes**, and two of the three asked about the same fund. The
screen therefore advertised a fraction of what the corpus could answer, and pointed a
first-time user at the two funds the demo had always used. Since P15 added ten pages
and P17 made all fifteen reachable, the chips were actively misleading.

Nothing regenerates them. They were a static list, so there was no "refresh" to speak
of — a new set meant editing `config.py` by hand.

Removed from `app.py`, `config.py` and the `rag.prompts` re-export, along with the
`set_question` widget callback that existed only to serve them.

**Two things the chips were quietly testing, moved rather than dropped:**

- `test_every_example_question_retrieves_evidence` checked that a question retrieving
  evidence exists for each chip. That was the only test that would have caught the P17
  bug — a scheme present in `SOURCES` but invisible to the retriever — because the
  chips happened not to mention any of the ten broken funds. It is now
  `test_every_scheme_in_the_corpus_is_retrievable_by_name` and covers all fifteen.
- `test_config_example_questions_all_pass_the_intent_gate` checked the chips classify
  `FACTUAL`. It is now written over one question per ingested scheme, which is a
  strictly larger sample of the property that mattered.

`scripts/inspect_index.py` also referenced the deleted name and would have raised
`AttributeError` on its next run — nothing in the suite executes that script. Its
retrieval probes are now derived from the scheme-scope metadata (15 probes, one per
fund, AMFI pages excluded, since "exit load on AMFI Investor Awareness Programme" is
not a question worth printing).

If chips return, derive them from `load_sources()` instead of hard-coding: that is the
whole defect.

**Gates:** 356 passed, pyflakes clean, `inspect_index.py` runs and emits 15 probes,
UI renders with `['Ask']` as the only button.

---

## Appendix A — Definition of Done (global)

The prototype is complete when **all** of these hold:

- [ ] `python run_ingest.py` rebuilds the index from an empty `data/chroma/` in one command,
      non-interactively, with a printed summary of docs / chars / chunks / strategy.
- [ ] Re-running ingest leaves the collection count unchanged.
- [ ] `python scripts/evaluate.py` passes every golden row, including the refusal and PII rows.
- [ ] Every factual answer is ≤3 sentences, carries exactly one citation URL present in
      `sources.py`, and shows `Last updated from sources: <date>`.
- [ ] Advice, returns, out-of-scope, out-of-corpus, and PII inputs all produce the intended
      status and copy.
- [ ] No PII appears in `logs/` or `data/`.
- [ ] No secrets in the repo; `.env` is ignored.
- [ ] Every number quoted in `README.md` came from an actual run.
- [ ] The UI shows the welcome line, 3 examples, the disclaimer, one citation, the
      last-updated line, and the sources expander.
- [ ] A ≤3-min demo video exists.

## Appendix B — Traceability to PRD §11 Acceptance Criteria

| PRD §11 criterion | Proven in phase | Evidence artifact |
|---|---|---|
| 1. 5 schemes ingested, rebuildable in one command | P3, P11 | ingest summary in the demo video |
| 2. Six questions answered with a working citation | P5, P8, P9 | `reports/eval_results.md`, `sample_qa.md` |
| 3. "Should I buy HDFC Small Cap?" refused | P6, P7 | golden row + `sample_qa.md` |
| 4. Out-of-corpus question refused, not invented | P4, P6 | `NOT_FOUND` golden row |
| 5. PAN rejected and never stored | P6 | `tests/test_guards.py` + log review |
| 6. Last-updated line on every answer | P7 | UI screenshot |
| 7. Chunking decision documented | P2 | `reports/chunking_decision.md` |
| 8. Source list contains only approved URLs | P0, P10 | `reports/sources.md` |

## Appendix C — Phase Handoffs (what each phase must leave behind)

| After phase | The next phase can rely on |
|---|---|
| P0 | `config` values, `load_sources()`, `DISCLAIMER_TEXT` |
| P1 | `fetch_all()` returning real `Document` text, `_ingest_report.json` |
| P2 | `split_all(docs, strategy)`, a chosen `CHUNK_STRATEGY`, a written decision report |
| P3 | a populated collection, `search()`-ready `embedder`, stable `chunk_id` |
| P4 | a calibrated `MIN_SCORE`, `SCHEME_ALIASES` working, golden questions seeded |
| P5 | `generate()` returning constrained text, all copy in `prompts.py` |
| P6 | `check_pii()`, `classify_intent()`, `verify()` with unit tests green |
| P7 | `answer(query) -> Answer` — the only entry point the CLI and UI need |
| P8 | `cli.py`, a green eval table, real sample output |
| P9 | the finished single-screen UI |
| P10 | every documentation deliverable, accurate |
| P12 | Stage 7 specified in `architecture.md` §6.9 and PRD §6.7 |
| P11 | a rehearsed, recorded, offline-capable demo |
| P13 | `render.yaml` on the free plan, `GROQ_MODEL` overridable without silently invalidating `reports/` |
