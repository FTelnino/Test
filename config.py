"""All tunables for the RAG chatbot. No stage module may hard-code a path,
threshold, or model name; everything is read from here."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_env() -> None:
    """Load a local .env if one exists, so the API key is never hard-coded.

    Two candidate locations, because the key currently lives in `docs/.env` next
    to the specs rather than at the project root. Silently does nothing when
    python-dotenv is absent or no file is found -- the backend then fails loudly
    with a clear message about the missing key, which is the right place for that
    error to surface.

    `override=False` means a variable already present in the real environment
    wins. That ordering is what makes deployment work: Render, Heroku, and
    Docker inject secrets as environment variables and ship no .env file at all,
    so the file is a local-development convenience that must never be able to
    mask the value the platform actually configured.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for candidate in (BASE_DIR / ".env", BASE_DIR / "docs" / ".env"):
        if candidate.exists():
            load_dotenv(candidate, override=False)


_load_env()


def _env_str(name: str, default: str) -> str:
    """Read a string setting from the environment, falling back to `default`.

    Empty and whitespace-only values are treated as unset, because a dashboard
    that renders a text field as blank should leave the default in place rather
    than configure the empty string. This is the same rule `python-dotenv` applies
    to a blank line in a .env file.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip()


def _env_int(name: str, default: int) -> int:
    """Read an integer setting from the environment, falling back to `default`.

    Falls back rather than raising on a non-numeric value: a bad integer in a
    platform dashboard should not take down import time for a setting the running
    service may not even use.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default

# --- Sources -------------------------------------------------------------
SOURCES_FILE = BASE_DIR / "sources.py"
EDUCATION_LINK = "https://www.amfiindia.com/investor-educational-resources"
FACTSHEET_LINK_TEMPLATE = "https://groww.in/mutual-funds/{slug}"

# --- Ingest --------------------------------------------------------------
HTTP_TIMEOUT = 30
HTTP_RETRIES = 1
RETRY_BACKOFF_SECONDS = 2.0
FETCH_DELAY_SECONDS = 1.5
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
EXTRACTOR = "trafilatura"
MIN_TEXT_CHARS = 500
RAW_DIR = BASE_DIR / "data" / "raw"

# --- Chunking ------------------------------------------------------------
CHUNK_STRATEGY = "table_aware"
CHUNK_SIZE = 600
CHUNK_OVERLAP = 100
MIN_CHUNK_CHARS = 80

# --- Embedding -----------------------------------------------------------
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
BATCH_SIZE = 32
MODEL_CACHE_DIR = BASE_DIR / "data" / "models"

# --- Vector store --------------------------------------------------------
CHROMA_DIR = BASE_DIR / "data" / "chroma"
COLLECTION_NAME = "hdfc_mf_faq"
HNSW_SPACE = "cosine"
CHROMA_BATCH_SIZE = 200

# --- Retrieval -----------------------------------------------------------
TOP_K = 5
RERANK_TOP_N = 10
ENABLE_RERANK = False
RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
# Calibrated on data/eval/golden.jsonl during P4; see
# reports/retrieval_calibration.md. The corpus sits in a narrow cosine band
# (0.62-0.78 for in-corpus questions, 0.12-0.19 for unrelated ones), so this
# only has to separate those two populations. A question about a *different
# AMC on the same topic* lands at 0.50-0.64 and is NOT separable by score --
# guards.classify_intent() refuses those as OUT_OF_SCOPE before retrieval.
MIN_SCORE = 0.40

# --- Generation ----------------------------------------------------------
# Backend is Groq (OpenAI-compatible). Ollama is kept as a working alternative for
# offline use; switch LLM_BACKEND to "ollama" and it will use the settings below.
#
# Both traps below were measured, not guessed -- see reports/generation_gate.md.
#
# 1. `reasoning_effort` is REQUIRED on gpt-oss. Without it the model spends the
#    whole token budget thinking and the answer arrives truncated mid-sentence.
#    Measured: 68 completion tokens with "low" vs 160 (all reasoning, no answer)
#    without. That is the difference between a working demo and a broken one.
# 2. gpt-oss returns a separate `reasoning` field alongside `content`. Only
#    `content` is ever read. The reasoning trace is the model's scratchpad, not an
#    answer, and rendering it would put "Need to answer using only context" on
#    screen and straight into the verifier.
# Overridable so a deployment can switch backends from its dashboard without a
# rebuild; local development leaves it on groq.
LLM_BACKEND = _env_str("LLM_BACKEND", "groq")
LLM_TEMPERATURE = 0
LLM_MAX_TOKENS = 160
LLM_TIMEOUT = 60
# Rate-limit handling. Retrying is safe because it cannot change the answer: the
# same prompt goes back with temperature=0, so the retry is the same answer later.
# Measured need: the free Groq tier allows 8000 tokens/min, and this project's
# prompts are large enough that 6-8 back-to-back generations trip it.
LLM_MAX_ATTEMPTS = 4
LLM_BACKOFF_SECONDS = 20.0

# Ollama (offline alternative)
OLLAMA_BASE_URL = _env_str("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = _env_str("OLLAMA_MODEL", "llama3.2")

# Groq. GROQ_MODEL is overridable from the environment or .env; the model list is
# account-specific, so the default cannot be the only allowed value. This key
# cannot reach the retired llama-3.1-8b-instant. 20b over 120b because 120b emitted
# U+202F narrow no-break spaces inside "1 year", which is a needless hazard for the
# verifier's regexes, at 3x the latency for no accuracy gain here.
#
# This was previously hard-coded, which meant a GROQ_MODEL line in .env was parsed,
# documented in .env.example, and then silently ignored.
GROQ_BASE_URL = _env_str("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
GROQ_MODEL = _env_str("GROQ_MODEL", "openai/gpt-oss-20b")
GROQ_REASONING_EFFORT = _env_str("GROQ_REASONING_EFFORT", "low")
GROQ_KEY_ENV = "GROQ_API_KEY"
#: Seconds the gate script waits between generations. Not a product setting -- the
#: product makes one call per user question -- so it lives beside the probe's other
#: rate-limit knobs rather than pretending to be a tunable.
GROQ_RATE_LIMIT_PAUSE = 8

# --- Guards --------------------------------------------------------------
# Pattern *strings* live here so they are tunable and reviewable without reading
# stage code; rag/guards.py compiles them into the (name, regex) pairs it uses.
# Everything here runs before retrieval, so a false positive blocks a legitimate
# question while a false negative ships a user's PAN to the LLM. Both directions
# were checked against the golden set; see tests/test_guards.py.
PII_PATTERNS = {
    # 5 letters + 4 digits + 1 letter. Case-insensitive because people type
    # "abcde1234f" as often as they type it correctly.
    "pan": r"\b[A-Za-z]{5}[0-9]{4}[A-Za-z]\b",
    # The [2-9] first-digit rule is what stops this matching an arbitrary 12-digit
    # number such as a timestamp or a folio with a leading zero.
    "aadhaar": r"\b[2-9][0-9]{3}[\s-]?[0-9]{4}[\s-]?[0-9]{4}\b",
    "email": r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b",
    # Indian mobile: 10 digits starting 6-9, optionally +91, optionally grouped.
    "phone": r"(?:\+91[\s-]?)?\b[6-9][0-9]{4}[\s-]?[0-9]{5}\b",
    # Keyword-anchored on purpose: a bare 9-18 digit run is far more likely to be a
    # date fragment or a figure than an account number, and refusing those would
    # block legitimate questions. The connector is optional because "account number
    # is 1234..." is the most natural way to type this, not "account number: 1234".
    "account_number": (
        r"\b(?:account|folio|a/c|acc\.?|cheque)\s*"
        r"(?:no\.?|number|num|details)?\s*"
        r"(?:is|was|:|=|no\.?)?\s*[0-9]{9,18}\b"
    ),
    # Anchored to the keyword too, so a 4-8 digit number in ordinary text is not
    # mistaken for a one-time password.
    "otp": (
        r"\b(?:otp|one[\s-]?time\s+(?:password|otp)|verification\s+code|passcode)"
        r"\s*(?:is|was|:|=|entered)?\s*[0-9]{4,8}\b"
    ),
}

# Weighted keyword rules for the intent gate. Weights are additive; the highest
# total wins, and a tie resolves to ADVICE because a missed advice question is the
# worst failure mode (architecture.md §6.7).
INTENT_RULES = {
    "ADVICE": {
        r"\bshould\s+(?:i|we)\b": 3,
        r"\bshould\s+.{0,24}\bbuy\b": 3,
        r"\bis\s+it\s+(?:good|safe|worth)\b": 3,
        r"\b(?:do\s+you\s+)?recommend\b": 3,
        r"\bbest\s+(?:fund|scheme|mutual\s+fund)\b": 3,
        r"\bworth\s+(?:buying|investing)\b": 3,
        r"\bwhich\s+(?:one|fund|scheme)\s+should\b": 3,
        r"\bsuitable\s+for\s+(?:me|my|us)\b": 3,
        r"\b(?:my|our)\s+(?:age|profile|horizon|risk\s+profile)\b": 2,
        r"\bcan\s+i\s+(?:buy|invest\s+in|start)\b": 2,
        r"\b(?:good|better)\s+(?:option|choice|fund)\b": 2,
        r"\bhelp\s+me\s+choose\b": 3,
        r"\bwhich\s+should\s+i\b": 3,
        # "is now a good time to buy" is the same request as "should I buy" and was
        # a measured false negative before this rule existed.
        r"\b(?:good|right|best|wrong|bad)\s+time\s+to\s+(?:buy|invest|enter|start)\b": 3,
        r"\bis\s+(?:now\s+)?a\s+(?:good|great|right|bad|wrong)\s+time\b": 3,
        r"\bshall\s+i\b": 2,
        r"\bworth\s+it\b": 2,
        r"\bwhich\s+(?:is|one\s+is)\s+better\b": 3,
        r"\b(?:tell|advise)\s+me\s+which\b": 3,
    },
    "RETURNS": {
        r"\bcagr\b": 3,
        r"\bhow\s+much\s+can\s+i\s+earn\b": 3,
        r"\b(?:expected|projected|likely)\s+returns?\b": 3,
        r"\bwill\s+(?:it|this)\s+(?:give|earn|return)\b": 3,
        r"\bhow\s+much\s+(?:profit|money)\b": 3,
        r"\b(?:best|highest)\s+(?:performing|returns?)\b": 2,
        r"\b(?:returns?|performance|profit|gain|yield)\b": 1,
    },
}

# Non-HDFC AMCs. Naming one of these means the question is about a fund this
# corpus does not cover, so it is refused as OUT_OF_SCOPE rather than answered
# from an HDFC page that happens to discuss the same attribute. P4 measured why
# this cannot be left to MIN_SCORE: a Parag Parikh expense-ratio question scores
# 0.64 against HDFC Equity Fund's real expense-ratio chunk, inside the in-corpus
# band. See reports/retrieval_calibration.md.
OTHER_AMCS = [
    "parag parikh", "mirae asset", "icici pru", "icici mutual", "axis mutual",
    "axis longterm", "kotak", "sbi mutual", "sbi bluechip", "lt mutual",
    "nippon", "motilal oswal", "quant mutual", "canara robeco", "uti mutual",
    "aditya birla", "sundaram", "bank of baroda", "idfc", "invesco",
    "franklin templeton", "hsbc mutual", "tata mutual", "bandhan", "jio blackrock",
    "groww index", "parag",
]

INTENT_BACKEND = "rules"

# --- UI ------------------------------------------------------------------
DISCLAIMER_FILE = BASE_DIR / "DISCLAIMER.md"
MAX_ANSWER_SENTENCES = 3
# The three example chips the UI offers. Fixed on purpose, not randomised: the same
# three appear on every load, so the screen is reproducible and a screenshot in the
# docs stays true. (They briefly went missing in P19 — see the P19 note in
# docs/implementation.md. Two of the three name only HDFC Large Cap and HDFC ELSS
# Tax Saver, which is a real coverage gap across 15 schemes; if that is ever worth
# fixing, widen this list rather than randomising it.)
EXAMPLE_QUESTIONS = [
    "What is the exit load on HDFC Large Cap Fund?",
    "What is the minimum SIP for HDFC ELSS Tax Saver Fund?",
    "What is the lock-in period for HDFC ELSS Tax Saver Fund?",
]

# --- Paths ---------------------------------------------------------------
LOG_DIR = BASE_DIR / "logs"
REPORTS_DIR = BASE_DIR / "reports"
EVAL_DIR = BASE_DIR / "data" / "eval"
GOLDEN_FILE = EVAL_DIR / "golden.jsonl"

# --- Pipeline -------------------------------------------------------------
# `fetched_at` is a date string recorded at ingest, and it is NOT carried on
# Chunk: the raw chunk table has no such field, so render() reads the url ->
# fetched_at map out of the ingest report. Kept in config because the path to
# that report is a path, and no stage module may hard-code one.
INGEST_REPORT = RAW_DIR / "_ingest_report.json"
#: Display format for `Answer.last_updated`. The stored value is ISO; the UI
#: shows something a person reads.
LAST_UPDATED_FORMAT = "%d %b %Y"
LOG_FILE = LOG_DIR / "app.log"
#: Echo structured log lines to stderr as well as the file, so a CLI run can be
#: watched live. The Streamlit UI leaves this off.
LOG_TO_STDERR = True
#: Fixed message when the LLM backend is unreachable. Deliberately says the
#: assistant is down rather than pretending the corpus had no answer -- the two
#: mean different things to a user, and conflating them hides an outage.
LLM_UNAVAILABLE_TEXT = (
    "The assistant is temporarily unavailable, so I can't answer from the sources "
    "right now. Please see the official scheme page for the facts."
)


def disclaimer_text() -> str:
    """Single source of truth for the UI disclaimer, read from DISCLAIMER.md."""
    return DISCLAIMER_FILE.read_text(encoding="utf-8").strip().splitlines()[0].strip()


def ensure_dirs() -> None:
    for path in (RAW_DIR, CHROMA_DIR, MODEL_CACHE_DIR, LOG_DIR, REPORTS_DIR, EVAL_DIR):
        path.mkdir(parents=True, exist_ok=True)
