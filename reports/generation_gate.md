# P5 generation gate

Backend: `groq:openai/gpt-oss-20b`, temperature=0, max_tokens=160, reasoning_effort=low.

Every `raw` block below is the model's unmodified output. The `final` block
is what the user would see after the verifier.

## What is the expense ratio of HDFC Large Cap Fund?

Evidence: 5 chunk(s), top score 0.7103 [HDFC Large Cap Fund | Riskometer]

**raw**

```
The expense ratio of HDFC Large Cap Fund Direct Growth is 1.03%.
```

- PASS <= 3 sentences — 1 sentence(s)
- PASS no advice phrasing — clean
- PASS no return figure — clean
- PASS no invented URL — clean
- PASS no reasoning trace leaked — clean

**final** (`ANSWERED`)

```
The expense ratio of HDFC Large Cap Fund Direct Growth is 1.03%.
```

## What is the exit load on HDFC Small Cap Fund?

Evidence: 5 chunk(s), top score 0.7469 [HDFC Small Cap Fund | Exit load]

**raw**

```
The exit load on the HDFC Small Cap Fund Direct Growth is 1% if redeemed within one year.
```

- PASS <= 3 sentences — 1 sentence(s)
- PASS no advice phrasing — clean
- PASS no return figure — clean
- PASS no invented URL — clean
- PASS no reasoning trace leaked — clean

**final** (`ANSWERED`)

```
The exit load on the HDFC Small Cap Fund Direct Growth is 1% if redeemed within one year.
```

## What is the minimum SIP amount for HDFC Balanced Advantage Fund?

Evidence: 5 chunk(s), top score 0.7772 [HDFC Balanced Advantage Fund | Exit load]

**raw**

```
The minimum SIP amount for HDFC Balanced Advantage Fund Direct Growth is Rs.100.
```

- PASS <= 3 sentences — 1 sentence(s)
- PASS no advice phrasing — clean
- PASS no return figure — clean
- PASS no invented URL — clean
- PASS no reasoning trace leaked — clean

**final** (`ANSWERED`)

```
The minimum SIP amount for HDFC Balanced Advantage Fund Direct Growth is Rs.100.
```

## What is the lock-in period for HDFC ELSS Tax Saver Fund?

Evidence: 3 chunk(s), top score 0.6704 [HDFC ELSS Tax Saver Fund | ELSS lock-in and tax]

**raw**

```
The lock‐in period for HDFC ELSS Tax Saver Fund is 3 years.
```

- PASS <= 3 sentences — 1 sentence(s)
- PASS no advice phrasing — clean
- PASS no return figure — clean
- PASS no invented URL — clean
- PASS no reasoning trace leaked — clean

**final** (`ANSWERED`)

```
The lock‐in period for HDFC ELSS Tax Saver Fund is 3 years.
```

## What is the riskometer level and benchmark of HDFC Equity Fund?

Evidence: 3 chunk(s), top score 0.6314 [HDFC Equity Fund | Riskometer]

**raw**

```
The HDFC Flexi Cap Direct Plan Growth is rated Very High risk.  
Its benchmark is the NIFTY 500 Total Return Index.
```

- PASS <= 3 sentences — 2 sentence(s)
- PASS no advice phrasing — clean
- PASS no return figure — clean
- PASS no invented URL — clean
- PASS no reasoning trace leaked — clean

**final** (`ANSWERED`)

```
The HDFC Flexi Cap Direct Plan Growth is rated Very High risk. Its benchmark is the NIFTY 500 Total Return Index.
```

## How do I download a capital gains statement?

Evidence: 3 chunk(s), top score 0.4102 [HDFC Balanced Advantage Fund | Portfolio holdings]

**raw**

```
I couldn't find that in the official sources.
```

- PASS <= 3 sentences — 1 sentence(s)
- PASS no advice phrasing — clean
- PASS no return figure — clean
- PASS no invented URL — clean
- PASS no reasoning trace leaked — clean

**final** (`NOT_FOUND`)

```
I couldn't find that in the official sources.
```

## Absent from the corpus — must return the exact string

## What is the SEBI registration code of HDFC Equity Fund?

**raw**

```
I couldn't find that in the official sources.
```

- PASS exact NOT_FOUND string
- verifier status: `NOT_FOUND` (must be NOT_FOUND, not ANSWERED)

## What is the custodian name of HDFC Large Cap Fund?

**raw**

```
I couldn't find that in the official sources.
```

- PASS exact NOT_FOUND string
- verifier status: `NOT_FOUND` (must be NOT_FOUND, not ANSWERED)

## What is the AMC's toll-free customer care number?

**raw**

```
I couldn't find that in the official sources.
```

- PASS exact NOT_FOUND string
- verifier status: `NOT_FOUND` (must be NOT_FOUND, not ANSWERED)

## Refusals still hold end to end

- PASS `ADVICE` blocked=intent — expect ADVICE, no generation
- PASS `RETURNS` blocked=intent — expect RETURNS, no generation
- PASS `OUT_OF_SCOPE` blocked=intent — expect OUT_OF_SCOPE
- PASS `pan` blocked=pii — expect PII, value never forwarded

---

**all constraints held**
