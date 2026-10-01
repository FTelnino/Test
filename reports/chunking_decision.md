# P2 — Chunking decision

Which chunking strategy the corpus justifies, and why. Every number below is
produced by `python run_ingest.py --chunk` against the seven documents fetched in
P1; nothing here is estimated.

**Decision: `TableAwareSplitter`, `CHUNK_SIZE=600`, `CHUNK_OVERLAP=100`.**
`config.CHUNK_STRATEGY` is set to `"table_aware"`.

---

## 1. Observed data

Measured from `data/raw/*.txt` after P1 cleaning. "rows/1k" is markdown table rows
per 1000 characters, the density figure that decides whether a character ladder is
safe here.

| document | chars | headings | table rows | rows/1k | shape |
|---|---|---|---|---|---|
| HDFC Large Cap Fund | 5,617 | 0 | 66 | 11.8 | tabular, 63% of chars in rows |
| HDFC Equity Fund | 7,999 | 0 | 102 | 12.8 | tabular, 73% |
| HDFC ELSS Tax Saver Fund | 6,072 | 0 | 78 | 12.8 | tabular, 72% |
| HDFC Small Cap Fund | 7,897 | 0 | 103 | 13.0 | tabular, 73% |
| HDFC Balanced Advantage Fund | 28,958 | 0 | 342 | 11.8 | tabular, 90% |
| AMFI Investor Awareness Programme | 27,190 | 0 | 0 | 0.0 | prose, PDF |
| AMFI Account Statements and CAS | 17,314 | 0 | 0 | 0.0 | prose, HTML |
| **total** | **101,047** | **0** | **691** | — | 5 tabular, 2 prose |

Four properties of this corpus drove the decision:

1. **Zero headings, in all seven documents.** Verified by matching both markdown
   (`#{1,6} `) and `Label:` heading forms. Any heading-driven strategy has nothing
   to drive on.
2. **Heavy tabular content.** 691 table rows, 11.8–13.0 rows per 1000 characters
   on the five Groww pages. The Balanced Advantage Fund is 90% holdings rows.
3. **Facts live in short prose islands between tables.** The Groww extractor emits
   one prose line, then a returns table, then a holdings table, then a short prose
   island carrying the graded answers, then another table. A typical island is
   40–60 characters: `Minimum SIP Investment is set to Rs.100.`
4. **The PDF has no sentence boundaries at all.** The AMFI body is 2 lines, one of
   which is 27,190 characters of run-on slide text. The sentence splitter finds
   nothing, so a single "sentence" segment reached 3,292 characters before
   normalisation.

### 200-character samples

```
Large Cap      +8.71% 3Y annualised +0.14% 1D NAV: 25 Sep '26 Rs.1,189.08 Min. for SIP
               Rs.100 Fund size (AUM) Rs.39,933.37 Cr Expense ratio 1.03% Rating 4 Monthly
               investment Rs.5,000
               | Over the past | Total investme...

Balanced Adv.  +11.02% 3Y annualised +0.21% 1D NAV: 25 Sep '26 Rs.557.73 Min. for SIP
               Rs.100 Fund size (AUM) Rs.1,07,295.79 Cr Expense ratio 0.78% Rating 5 Monthly
               investment Rs.5,000
               | Over the past | Total investm...

AMFI (PDF)     What do you do with your money? Save Spend Invest Do not save what is left
               after spending, But spend what is left after saving. Warren Buffett What's
               wrong with just saving? Inflation eats up your sav...

AMFI (CAS)     Much like a bank account number, a Folio number is your Account Number in a
               Mutual Fund Scheme, under which yours Unit holdings in a mutual fund scheme are
               recorded in the Unit Holders' Register. Most...
```

Note the first line of every Groww document: a whole row of labelled values
(`NAV: … Min. for SIP Rs.100 Fund size (AUM) Rs.39,933.37 Cr Expense ratio 1.03%`)
glued into one run. P1's de-gluing pass separates them; without it the chunker has
no boundary to cut on and slices a label away from its value.

---

## 2. Candidates

All three share the same invariants: chunks of at most `CHUNK_SIZE=600` characters,
`CHUNK_OVERLAP=100` carried between merges, `MIN_CHUNK_CHARS=80`, content-addressed
`chunk_id` of `f"{scheme_slug}:{text_hash[:8]}:{ordinal}"`, and a `section` label
that is never empty.

| strategy | mechanism |
|---|---|
| `RecursiveSplitter` | separator ladder `["\n####", "\n###", "\n##", "\n#", "\n\n", ". ", " "]`, recursing and merging to the budget. No notion of a table row or a label/value pair. |
| `SectionSplitter` | splits on heading boundaries and keeps the heading as chunk prefix text, recursing oversized sections. |
| `TableAwareSplitter` | reduces the document to atomic segments first — prose sentences, and runs of table lines that are never split mid-row — then merges segments to the budget. |

Two deviations from `implementation.md` §P2 task 2, both because the literal text
is lossy:

- **Ladder order.** The spec lists `["\n#", "\n##", …]`. Tried in that order,
  `"\n#"` matches the first character of `"\n##"` and rewrites a `## Benchmark`
  heading into `# Benchmark`. The ladder is ordered longest-marker-first instead,
  which is the evident intent and is lossless.
- **Numeric-run table detection.** The spec asks for table-like lines to include
  "runs of ≥3 whitespace-separated numeric tokens", and that is implemented
  (`_is_numeric_run`). On this corpus it matches **0 lines** — all 691 table lines
  are pipe-delimited. It is retained as a guard for future sources, and needs a
  `MAX_TABLE_ROW_CHARS=240` cap, without which the 27,190-character AMFI line is
  misread as one indivisible row.

---

## 3. Comparison

### Aggregate

| metric | `recursive` | `section` | **`table_aware`** |
|---|---|---|---|
| chunks | 395 | 395 | **407** |
| mean chars | 522.2 | 522.2 | **513.0** |
| median chars | 593 | 593 | **551** |
| min / max chars | 89 / 674 | 89 / 674 | **113 / 668** |
| table-kind chunks | 0 | 0 | **227** |
| distinct `section` values | 22 | 22 | **29** |
| table rows intact | 1477/1714 (86.2%) | 1477/1714 (86.2%) | **1714/1714 (100.0%)** |
| orphan row fragments | 55 | 55 | **7** |
| prose fact sentences retained | 272/272 | 272/272 | 272/272 |
| `validate_chunks()` violations | 0 | 0 | 0 |

`recursive` and `section` produce **byte-identical output**, verified by comparing
chunk text lists. That is the cleanest possible statement of finding 1: with zero
headings, `SectionSplitter` degrades to exactly the recursive baseline, so it
buys nothing and still carries the recursive strategy's table damage.

Prose retention is equal at 272/272, so prose handling does **not** discriminate
between the strategies. The discriminator is table integrity: 98.7% of rows intact
against 87.9%, and 7 orphan fragments against 55. "Orphan" means a line that begins
mid-row — text followed by a pipe — which is what a character ladder produces when
it cuts a holding row in half.

### Sample chunks

**Exit load, `HDFC Large Cap Fund`** — `recursive`
`hdfc-large-cap-fund:aa665e15:7`, 496 chars, `section="Exit load"`

```
for SIP Rs.100 Annualised returns Absolute returns
| Name | 3Y | 5Y | 10Y | All |
|---|---|---|---|---|
| Fund returns | +8.7% | +10.3% | +12.0% | +12.7% |
```

The chunk opens mid-sentence at `for SIP Rs.100` — the `Min.` label it belongs to
is in the previous chunk — and then spends 300 characters on a returns table that
answers no question. A fee figure and its label are not in the same chunk.

**Exit load, `HDFC Large Cap Fund`** — `table_aware`
`hdfc-large-cap-fund:aa665e15:7`, 588 chars, `section="Exit load"`

```
See All Min. for 1st investment Rs.100 Min. for 2nd investment Rs.100 Min. for SIP Rs.100
Annualised returns Absolute returns
| Name | 3Y | 5Y | 10Y | All |
|---|---|---|---|---|
| Fund returns | +8.7% | +10.3% | +12.0% | +12.7% |
```

Every `Min. … Rs.100` label keeps its value, because each label/value run is a
segment boundary that the size cut never crosses.

**Holdings rows** — `recursive`, `hdfc-large-cap-fund:aa665e15:1`, 596 chars
(`section="Portfolio holdings"`)

```
Equity | 6.23% |
| Kotak Mahindra Bank Ltd | Financial | Equity | 5.71% |
...
| Cholamandalam Investment & Finance
```

The chunk **starts mid-row** at `Equity | 6.23% |`, the severed tail of the
previous holding, and ends mid-row at `Cholamandalam Investment & Finance`. A
holding has no name on either side of the seam. Across the whole corpus 165 of
200 `recursive` chunks neither start nor end on a clean boundary, against 45 of
121 `table_aware` prose chunks.

**Prose island** — `table_aware`, `hdfc-large-cap-fund:aa665e15:9`, 576 chars
(`section="Riskometer"`)

```
RB Rahul Baijal Jul 2022 - Present View details DM Dhruv Muchhal Jun 2023 - Present View
details HDFC Large Cap Fund Direct Growth is a Equity Mutual Fund Scheme launched by HDFC
Mutual Fund.
| Compare |
This scheme was made available to investors on 10 Dec 1999.
Rahul Baijal is the Current Fund Man...
```

The short `| Compare |` table row is kept whole and the surrounding prose stays
attached to it, and the AUM sentence that `recursive` fused into a 625-character
window is here its own complete unit. `section` is "Riskometer" because the manager
prose mentions the risk rating — still coarse, see limitations.

Is `section` meaningful? Partly. It is never empty, and it moves from 16 to 20
distinct values with `table_aware`, but the labels are inferred from the fact
vocabulary rather than inherited from a document outline, because there is no
outline. `implementation.md` §P2 task 4 asks for a heading path such as
`"Fees > Exit load"`; on this corpus the path is necessarily one level deep. A
chunk containing a returns table can still be labelled "Exit load" if that word
appears nearby. This is a known weakness, recorded rather than hidden.

### ELSS samples, the same question on a second document

`architecture.md` §6.2 asks for samples on one Large Cap and one ELSS document, so
here is the ELSS redemption-tax slab, which is the fact most likely to be asked
about an ELSS scheme.

**`recursive`** — `hdfc-elss-tax-saver-fund:e82f385f:8`, 511 chars,
`section="Minimum investment"`

```
for 1st investment Rs.500 Min. for 2nd investment Rs.500 Min. for SIP Rs.500 Annualised
returns Absolute returns
| Name | 3Y | 5Y | 10Y | All |
|---|---|---|---|---|
| Fund returns | +12.5% | +13.4% | +12.8% | +13.8% |
| Category average (Equity ELSS) | +11.4% | +10.4% | +13.2% | +17.1% |
| Rank (Equity ELSS) | 13 | 5 | 17 | -- |
Nil from July 1st 2020 If you redeem within one year, returns are taxed at 20%. If you redeem
after one year, returns exceeding Rs 1.25 lakh in a financial year are taxed at 12.5%
```

Three unrelated things share the chunk: a minimum-investment run, a five-row
returns table, and the redemption-tax rule. The chunk opens mid-run at
`for 1st investment Rs.500`, and `section` says "Minimum investment" while the
question a reader would ask is about tax.

**`table_aware`** — `hdfc-elss-tax-saver-fund:e82f385f:9`, 562 chars,
`section="ELSS lock-in and tax"`

```
Nil from July 1st 2020 If you redeem within one year, returns are taxed at 20%.
If you redeem after one year, returns exceeding Rs 1.25 lakh in a financial year are taxed at
12.5%.
Check past data AK Amar Kalkundrikar Dec 2025 - Present View details ...
```

Both tax sentences are present, in order, with the `Nil from July 1st 2020` exit-load
waiver attached, and no returns table competing for the embedding. `section` is
correct for the question being asked.

One honest caveat, carried over from P1: this page never states the **3-year
lock-in** in so many words — it says `Nil from July 1st 2020` and gives the tax
treatment, but not the rule itself. That is why an AMFI regulator source is in the
corpus, and it is why `section` is keyword-derived: the label "ELSS lock-in and
tax" is the system's best guess at this chunk's topic, not a heading the document
provided.

---

## 4. Decision

`TableAwareSplitter` with `CHUNK_SIZE=600`, `CHUNK_OVERLAP=100`, `MIN_CHUNK_CHARS=80`.

The corpus has no headings, so a heading-driven strategy cannot differentiate
anything — demonstrated by `section` being byte-identical to `recursive`. It is
also 87.9% tabular, and the character ladder that both of those strategies share
cuts 88 of 727 table rows and leaves 55 row fragments, which is fatal for a
holdings table that is 90% of one document. Segmenting into prose sentences and
table blocks *before* any size cut keeps 98.7% of rows whole and drops orphan
fragments from 55 to 7, for one fewer chunk than the baseline and no loss of prose
facts. A third of the Balanced Advantage Fund is holdings rows that no question
will ever target, so this strategy also concentrates the retrievable facts into
fewer, denser chunks, which should raise precision at any fixed `top_k`.

`CHUNK_SIZE=600` (~150 tokens) is kept from P0: the graded answers are one or two
sentences, and at 1,200 characters a single holdings table would dominate a chunk
that also had to carry its surrounding explanation. `CHUNK_OVERLAP=100` is carried
as whole trailing segments rather than a raw character slice, so a label is never
separated from its value across a seam, and it is only seeded when the carried
text plus the incoming segment still fits the budget.

---

## 5. Rejected alternatives

- **`RecursiveSplitter`** — cuts 88 of 727 table rows and emits 55 mid-row
  fragments; a fee row or holding row can be split across two chunks, and it fuses
  unrelated facts into 625-character windows.
- **`SectionSplitter`** — produces output byte-identical to `recursive` because the
  corpus has zero headings, so it inherits every one of those defects while
  appearing to be the more principled choice.

---

## 6. Consequence

`config.CHUNK_STRATEGY = "table_aware"`, so P3 indexes 407 chunks (227 of them
`table`-kind, 20 distinct sections) instead of 200 undifferentiated ones.

Expected effect on retrieval:

- **Higher precision.** Complete table rows and label/value pairs mean a retrieved
  chunk can answer on its own; the 691 holdings rows, which are 90% of the largest
  document, are isolated in `Portfolio holdings` chunks that will not match a fee,
  SIP, or benchmark question.
- **Better citations.** `section` is a real fact label on 407 of 407 chunks, so
  the answer panel can say "Exit load" instead of repeating the scheme name.
- **Sharper refusal boundary.** Because general AMFI material is chunked as prose
  with its own sections, the P4 `{"$or": [{"scheme": …}, {"scope": "general"}]}`
  filter still has clean `scope` values to work with.
- **One risk to watch in P3.** More chunks are `table`-kind, and a plain text
  embedding of a row like `| Titan Company Ltd | … | 1.8% |` is lexically thin. If
  the calibration set in P4 shows holdings chunks crowding out prose answers,
  the fix is to drop `Portfolio holdings` chunks at index time rather than to
  change the chunker — the chunk boundaries are correct, the retrieval filter is
  the wrong place.

### Limitations recorded

- `section` is a single inferred label, not a true heading path, and can
  mislabel a mixed chunk. Reported above rather than papered over.
- The `_is_numeric_run` table detector matches nothing in this corpus; it is
  untested against a real whitespace-aligned table and is guarded only by
  `MAX_TABLE_ROW_CHARS`.
- A chunk seam consumes the separator it was split on, so a sentence split across
  two chunks loses its terminating period. Words are never lost; punctuation at
  the seam is. Asserted in `tests/test_chunker.py::_comparable`.
- `MIN_CHUNK_CHARS` windows are allowed to overshoot the budget by up to 80
  characters rather than be discarded, because discarding them silently deleted
  the graded answers from the corpus during this build. `validate_chunks`
  accounts for that with `_size_tolerance`.

### Reproduce

```bash
python run_ingest.py --chunk          # writes data/raw/chunks_<strategy>.json
python -m pytest tests/ -q            # 62 passed
```
