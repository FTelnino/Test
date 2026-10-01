# P4 retrieval calibration

`MIN_SCORE` is set to **0.4**.

## Why a threshold at all

A retriever that always returns its top *k* chunks cannot say "not found", so the
generator would have to answer from an irrelevant passage or invent one. Both are worse
than refusing, so `search()` returns `[]` when the best surviving hit scores below
`MIN_SCORE` and the pipeline turns that into `NOT_FOUND` (FR-10).

## Score table

| question | kind | top score | detected | top hit scheme | section | verdict |
|---|---|---|---|---|---|---|
| What is the expense ratio of HDFC Large Cap Fund? | in_corpus | 0.7103 | HDFC Large Cap Fund | HDFC Large Cap Fund | Riskometer | PASS |
| What is the exit load on HDFC Small Cap Fund? | in_corpus | 0.7469 | HDFC Small Cap Fund | HDFC Small Cap Fund | Exit load | PASS |
| What is the minimum SIP amount for HDFC Balanced Advantage Fund? | in_corpus | 0.7772 | HDFC Balanced Advantage Fund | HDFC Balanced Advantage Fund | Exit load | PASS |
| What is the lock-in period for HDFC ELSS Tax Saver Fund? | in_corpus | 0.6704 | HDFC ELSS Tax Saver Fund | HDFC ELSS Tax Saver Fund | ELSS lock-in and tax | PASS |
| What is the riskometer level and benchmark of HDFC Equity Fund? | in_corpus | 0.6314 | HDFC Equity Fund | HDFC Equity Fund | Riskometer | PASS |
| How do I download a capital gains statement from my mutual fund account? | in_corpus | 0.6254 | — | AMFI Investor Awareness Programme | Benchmark | PASS |
| What is the boiling point of water at sea level? | out_of_corpus | 0.1875 | — | AMFI Investor Awareness Programme | Riskometer | PASS |
| How do I change the font size on my iPhone? | out_of_corpus | 0.1239 | — | HDFC Small Cap Fund | NAV and pricing | PASS |
| What is the expense ratio of Parag Parikh Flexi Cap Fund? | out_of_scope | 0.6386 | — | HDFC ELSS Tax Saver Fund | Expense ratio | not caught by score |
| What is the exit load on HDFC Flexi Cap Fund? | in_corpus | 0.6797 | HDFC Equity Fund | HDFC Equity Fund | Riskometer | PASS |

The last row is a category alias: "Flexi Cap" is the Groww category of HDFC Equity
Fund, and `detect_scheme` resolves it to that scheme. It was added in P4 after the
first run, so the gate counts 10 rows rather than 9.

## The three populations

| population | n | range | separable by `MIN_SCORE`? |
|---|---|---|---|
| in-corpus | 7 | 0.6254–0.7772 | — |
| out-of-corpus (unrelated domain) | 2 | 0.1239–0.1875 | yes, cleanly |
| out-of-scope (other AMC, same topic) | 1 | 0.6386–0.6386 | **no** — overlaps in-corpus |

The chosen threshold sits inside the gap between the first two:
**(0.1875, 0.6254]**, giving +0.2125 of margin above the
highest out-of-corpus score and +0.2254 below the lowest in-corpus score.

### The out-of-scope row is not a threshold problem

"What is the expense ratio of Parag Parikh Flexi Cap Fund?" scores 0.6386 — above the lowest
in-corpus question. That is not a tuning failure, it is the honest behaviour of a
similarity search over a corpus that genuinely contains an expense ratio for *a* flexi
cap fund (HDFC Equity Fund is one). No threshold on this score can tell "wrong fund"
from "right fund", because the evidence looks the same.

It is caught one layer earlier instead: `guards.classify_intent()` maps a non-HDFC AMC
name to `OUT_OF_SCOPE` and the pipeline refuses before retrieval runs (architecture.md
§6.7, PRD acceptance criterion 4). This is why `detect_scheme` will not treat a bare
category word as a scheme: matching "flexi cap" filtered the question to HDFC Equity
Fund and *manufactured* a confident wrong answer.

## Scheme and section accuracy

- top hit from the expected scheme: **6/6**
- top hit from the expected section: **3/6**

Scheme accuracy is what the P4 gate asks for and it is met, including the alias row.
Section accuracy is lower, and the cause is upstream in P2 rather than in the
retriever: the Groww stats line packs NAV, 1-day change, minimum SIP, AUM, **expense
ratio** and rating into one 600-character chunk. The expense-ratio answer *is* in the
chunk — it is just outnumbered by NAV numbers, so a question about expense ratios
matches the `Riskometer` chunk first. Fixing it means revisiting the P2 chunk-size
decision, not the retrieval code.

The three misses, all of them the same failure of a stats line outranking the fact
it carries:

| question | asked about | top hit section |
|---|---|---|
| What is the expense ratio of HDFC Large Cap Fund? | expense ratio | Riskometer |
| What is the minimum SIP amount for HDFC Balanced Advantage Fund? | minimum SIP | Exit load |
| What is the exit load on HDFC Flexi Cap Fund? | category alias → Flexi Cap | Riskometer |

Note that the retriever still returns the right answer in the miss cases: on the alias
row the `Exit load` chunk is retrieved at **#2** (0.5844), below the stats chunk at
0.6797. This costs answer *precision in the citation line*, not correctness of the
answer, because generation sees `RERANK_TOP_N` candidates rather than only the top one.

### P15: wider coverage multiplied this defect, it did not create it

Growing the corpus to 15 scheme pages (P15) makes the same failure show up on many more
schemes, because each new page has its own Riskometer block competing with its own
factual chunks. Measured on HDFC Gilt Fund, a page added in P15:

| rank | score | section |
|---|---|---|
| 1 | 0.7367 | Riskometer |
| 2 | 0.7126 | Riskometer |
| 35 | 0.5780 | **Expense ratio** |

So an expense-ratio question on a debt scheme retrieves no factual chunk inside `TOP_K`
and returns `NOT_FOUND` — truthful given what generation was shown, but unhelpful. The
same three mitigation candidates as below apply; only the chunking one fixes this class
rather than individual rows, because the cause is that the scheme-description block
swallows the Riskometer section and produces two near-duplicate high-similarity chunks
per scheme.

## Deduplication effect

| question | raw hits | after dedupe |
|---|---|---|
| What is the expense ratio of HDFC Large Cap Fund? | 5 | 5 |
| What is the exit load on HDFC Small Cap Fund? | 5 | 5 |
| What is the minimum SIP amount for HDFC Balanced Advantage Fund? | 5 | 5 |
| What is the lock-in period for HDFC ELSS Tax Saver Fund? | 5 | 3 |
| What is the riskometer level and benchmark of HDFC Equity Fund? | 5 | 3 |
| How do I download a capital gains statement from my mutual fund account? | 5 | 4 |
| What is the boiling point of water at sea level? | 5 | 3 |
| How do I change the font size on my iPhone? | 5 | 5 |
| What is the expense ratio of Parag Parikh Flexi Cap Fund? | 5 | 5 |
| What is the exit load on HDFC Flexi Cap Fund? | 5 | 4 |

## Reproduce

```bash
python scripts/probe_retrieval.py     # gate: 10/10, scheme 6/6, section 3/6
```

Offline: reads the committed index and the on-disk embedder, makes no network calls.
