"""Stage 1: LOADING. Turn approved URLs into clean Documents, or fail loudly.

The failure modes this module exists to prevent:
  * a JS-rendered page returning HTTP 200 with an empty shell, which would
    otherwise produce a confident "I couldn't find that" for every question
  * nav and consent boilerplate being embedded into the corpus, which pollutes
    every chunk and every answer
  * a partial fetch quietly becoming a partial corpus

Extractor choice is evidence-based, not a guess. On the same Groww page,
trafilatura returned 5,841 chars of real scheme content with tables preserved
as markdown, while a plain BeautifulSoup get_text returned 17,677 chars that
began with the site-wide product menu. Length is therefore not a quality signal
here: trafilatura is the primary extractor and BeautifulSoup is a fallback used
only when trafilatura returns too little to work with.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import time
import unicodedata
from datetime import date
from typing import Dict, List, Optional, Tuple

import config
from sources import Source, slugify

try:
    import requests
except ImportError as exc:  # pragma: no cover
    raise ImportError("requests is required: pip install -r requirements.txt") from exc

try:
    import trafilatura
except ImportError:  # trafilatura is preferred but the loader degrades to bs4
    trafilatura = None

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    PdfReader = None


MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
FETCHED_AT = date.today().isoformat()

ZERO_WIDTH = dict.fromkeys(
    [0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x00AD], None
)

UNICODE_FIXES = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201a": "'",
    "\u201b": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u201e": '"',
    "\u2013": "-",
    "\u2014": "-",
    "\u2212": "-",
    "\u00a0": " ",
    "\u2026": "...",
    "\u20b9": "Rs.",
}

CONSENT_PATTERNS = (
    r"\baccept (all )?cookies\b",
    r"\bwe use cookies\b",
    r"\bcookie (policy|settings|notice)\b",
    r"\bprivacy policy\b",
    r"\bterms (and|&) (conditions|of use)\b",
    r"\bby (continuing|browsing) you (agree|consent)\b",
    r"\bmanage cookies\b",
    r"\bgdpr\b",
    r"\bconsent banner\b",
)

NAV_PATTERNS = (
    r"^stocks$",
    r"^invest in stocks$",
    r"^f&o$",
    r"^mutual funds$",
    r"^invest in mutual funds$",
    r"^etf screener$",
    r"^ipo$",
    r"^mtfs?$",
    r"^demat account$",
    r"^share market today$",
    r"^option chain\b",
    r"^commodities\b",
)

# trafilatura merges the site-wide product blurbs into a single prose line, so
# these cannot be dropped line-by-line. They are substring matches instead, and
# each entry is a complete phrase: removing only part of one leaves a dangling
# connective behind ("... and ."). The P&L one matters beyond tidiness: it puts
# return language into the corpus.
JUNK_SUBSTRINGS = (
    "Invest in stocks, ETFs, IPOs with fast orders.",
    "Track returns on your stock holdings and view real-time P&L on your positions.",
    "Invest in direct mutual funds at zero charges via lump sump investments or SIPs",
)

TABLE_ROW = re.compile(r"^\s*\|")
TABLE_RULE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")

# trafilatura concatenates a label and its value with no separator, producing
# "Fund benchmarkNIFTY 100 Total Return Index" and "Date of Incorporation10 Dec
# 1999". Left alone, the chunker has no sentence boundary to cut on and slices
# these mid-label, so "benchmark" lands in one chunk and its value in the next.
#
# One rule, anchored on a missing space: lower/digit -> upper or digit, plus a
# lower -> "Upper case which is the shape of an address field.
#
# Deliberately NOT included: a "upper -> upper+lower" rule, to catch "AUMRs".
# It cannot be told apart from the legitimate "ETFs", "IPOs" and "SIPs" that
# appear throughout this corpus, and it shreds them into "ET Fs" and "IP Os".
# AUM is not a graded fact, so the ambiguous case is left alone deliberately.
LABEL_VALUE_GLUE = re.compile(r'(?<=[a-z])(?=[A-Z0-9])|(?<=[a-z])(?="[A-Z])')

LIST_ITEM = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s+")


class LoaderError(Exception):
    """Base class for ingest failures."""


class FetchError(LoaderError):
    """The page could not be retrieved."""


class ExtractionError(LoaderError):
    """The page was retrieved but no text could be extracted from it."""


class IngestError(LoaderError):
    """The extracted text is too small to be a real page. This is the
    JS-rendered-page tripwire: it must fail loudly rather than be ingested."""


class Document:
    """A cleaned source page. Contract per architecture.md section 5."""

    __slots__ = ("source", "text", "fetched_at", "text_hash")

    def __init__(self, source: Source, text: str, fetched_at: str, text_hash: str):
        self.source = source
        self.text = text
        self.fetched_at = fetched_at
        self.text_hash = text_hash

    @property
    def scheme(self) -> str:
        return self.source.scheme

    @property
    def source_url(self) -> str:
        return self.source.url

    @property
    def category(self) -> str:
        return self.source.category

    def __len__(self) -> int:
        return len(self.text)

    def __repr__(self) -> str:
        return (
            f"Document(scheme={self.source.scheme!r}, chars={len(self.text)}, "
            f"fetched_at={self.fetched_at!r})"
        )


def make_session() -> "requests.Session":
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": config.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-IN,en;q=0.9",
        }
    )
    return session


def _download(session: "requests.Session", source: Source) -> bytes:
    last_error: Optional[Exception] = None
    for attempt in range(config.HTTP_RETRIES + 1):
        try:
            response = session.get(
                source.url,
                timeout=config.HTTP_TIMEOUT,
                allow_redirects=True,
                stream=True,
            )
            if response.status_code != 200:
                raise FetchError(
                    f"{source.url} returned HTTP {response.status_code}"
                )
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > MAX_DOWNLOAD_BYTES:
                raise FetchError(
                    f"{source.url} is {declared} bytes, over the "
                    f"{MAX_DOWNLOAD_BYTES} byte cap"
                )
            chunks, total = [], 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise FetchError(
                        f"{source.url} exceeded the {MAX_DOWNLOAD_BYTES} byte cap"
                    )
            return b"".join(chunks)
        except (requests.RequestException, FetchError) as exc:
            last_error = exc
            if attempt < config.HTTP_RETRIES:
                time.sleep(config.RETRY_BACKOFF_SECONDS * (attempt + 1))
    raise FetchError(f"could not fetch {source.url}: {last_error}")


def _extract_html(html: str, url: str) -> str:
    """trafilatura first, BeautifulSoup only as a fallback.

    Both results are computed so the report can show which one was used, but the
    longer result does not win: on Groww pages the longer result is the nav menu.
    """
    trafilatura_text = ""
    if trafilatura is not None:
        try:
            extracted = trafilatura.extract(
                html,
                url=url,
                include_tables=True,
                include_comments=False,
                include_formatting=False,
                include_images=False,
                favor_recall=True,
            )
            trafilatura_text = extracted or ""
        except Exception:
            trafilatura_text = ""

    if len(trafilatura_text.strip()) >= config.MIN_TEXT_CHARS:
        return trafilatura_text

    if BeautifulSoup is None:
        raise ExtractionError("neither trafilatura nor bs4 is available")

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]):
        tag.decompose()
    soup_text = soup.get_text(separator=" ", strip=True)
    if len(trafilatura_text.strip()) >= len(soup_text.strip()):
        return trafilatura_text
    return soup_text


def _extract_pdf(content: bytes, url: str) -> str:
    if PdfReader is None:
        raise ExtractionError("pypdf is required to read PDF sources")
    try:
        reader = PdfReader(io.BytesIO(content))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:
        raise ExtractionError(f"could not parse PDF {url}: {exc}") from exc
    text = "\n".join(pages)
    if not text.strip():
        raise ExtractionError(f"PDF {url} yielded no text")
    return text


def _is_structural(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return bool(TABLE_ROW.match(line) or TABLE_RULE.match(line) or LIST_ITEM.match(line))


def clean_text(raw: str, preserve_structure: bool = True) -> str:
    """Normalise extracted text into a form worth embedding.

    preserve_structure=True keeps table rows and list items on their own lines so
    the chunker can keep a fee row or an exit-load slab intact. PDF text arrives
    hard-wrapped from the page layout, so it is collapsed to prose instead.
    """
    if not raw:
        return ""

    text = unicodedata.normalize("NFKC", raw)
    text = text.translate(ZERO_WIDTH)
    for bad, good in UNICODE_FIXES.items():
        text = text.replace(bad, good)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    kept: List[str] = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", line).strip()
        if not line:
            continue
        if any(re.search(p, line, re.IGNORECASE) for p in CONSENT_PATTERNS):
            continue
        if any(re.match(p, line, re.IGNORECASE) for p in NAV_PATTERNS):
            continue
        if not _is_structural(line):
            line = LABEL_VALUE_GLUE.sub(" ", line)
        kept.append(line)

    if not preserve_structure:
        return _strip_junk(re.sub(r"\s+", " ", " ".join(kept)).strip())

    out: List[str] = []
    prose: List[str] = []
    for line in kept:
        if _is_structural(line):
            if prose:
                out.append(" ".join(prose))
                prose = []
            out.append(line)
        else:
            prose.append(line)
    if prose:
        out.append(" ".join(prose))
    return _strip_junk("\n".join(out).strip())


def _strip_junk(text: str) -> str:
    for junk in JUNK_SUBSTRINGS:
        text = text.replace(junk, " ")
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def fetch_one(source: Source, session: Optional["requests.Session"] = None) -> Document:
    """Retrieve, extract, clean and validate one source.

    Raises FetchError, ExtractionError or IngestError. Never returns a Document
    whose text is too small to be real content.
    """
    session = session or make_session()
    content = _download(session, source)

    if source.content_type == "pdf":
        raw_text = _extract_pdf(content, source.url)
        cleaned = clean_text(raw_text, preserve_structure=False)
    else:
        html = content.decode("utf-8", errors="replace")
        raw_text = _extract_html(html, source.url)
        cleaned = clean_text(raw_text, preserve_structure=True)

    if len(cleaned) < config.MIN_TEXT_CHARS:
        raise IngestError(
            f"{source.url} ({source.scheme}) produced only {len(cleaned)} chars, "
            f"below MIN_TEXT_CHARS={config.MIN_TEXT_CHARS}. The page is probably "
            f"JS-rendered or blocked. Do not ingest it; use a first-party "
            f"factsheet or a regulator page instead."
        )

    return Document(
        source=source,
        text=cleaned,
        fetched_at=FETCHED_AT,
        text_hash=text_hash(cleaned),
    )


def fetch_all(
    source_list: Optional[List[Source]] = None,
) -> Tuple[List[Document], List[Dict]]:
    """Fetch every source. Returns the successes and a per-URL status report.

    A failure is recorded, never swallowed, so a partial corpus is visible in
    the report instead of silently shrinking the index.
    """
    from sources import load_sources

    source_list = source_list if source_list is not None else load_sources()
    session = make_session()
    documents: List[Document] = []
    report: List[Dict] = []

    for source in source_list:
        entry = {
            "scheme": source.scheme,
            "category": source.category,
            "url": source.url,
            "scope": source.scope,
            "content_type": source.content_type,
            "fetched_at": FETCHED_AT,
            "status": "ok",
            "chars": 0,
            "text_hash": None,
            "error": None,
        }
        try:
            document = fetch_one(source, session=session)
            documents.append(document)
            entry["chars"] = len(document.text)
            entry["text_hash"] = document.text_hash
        except LoaderError as exc:
            entry["status"] = "failed"
            entry["error"] = str(exc)
        except Exception as exc:  # unexpected, still recorded rather than lost
            entry["status"] = "failed"
            entry["error"] = f"unexpected {type(exc).__name__}: {exc}"
        report.append(entry)
        time.sleep(config.FETCH_DELAY_SECONDS)

    return documents, report


def save_raw(
    documents: List[Document], report: List[Dict], raw_dir=None
) -> List[str]:
    """Write cleaned text to data/raw/ plus a machine-readable ingest report."""
    raw_dir = raw_dir or config.RAW_DIR
    raw_dir.mkdir(parents=True, exist_ok=True)
    written: List[str] = []

    for document in documents:
        name = f"{slugify(document.source.scheme)}.txt"
        path = raw_dir / name
        header = (
            f"# scheme: {document.source.scheme}\n"
            f"# category: {document.source.category}\n"
            f"# url: {document.source.url}\n"
            f"# scope: {document.source.scope}\n"
            f"# content_type: {document.source.content_type}\n"
            f"# fetched_at: {document.fetched_at}\n"
            f"# text_hash: {document.text_hash}\n"
            f"# chars: {len(document.text)}\n"
            f"{'-' * 72}\n"
        )
        path.write_text(header + document.text, encoding="utf-8")
        written.append(str(path))

    (raw_dir / "_ingest_report.json").write_text(
        json.dumps(
            {
                "fetched_at": FETCHED_AT,
                "min_text_chars": config.MIN_TEXT_CHARS,
                "documents": len(documents),
                "failed": sum(1 for r in report if r["status"] != "ok"),
                "total_chars": sum(r["chars"] for r in report),
                "sources": report,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return written


def format_report(report: List[Dict]) -> str:
    lines = [
        f"{'scheme':34} {'scope':8} {'type':5} {'status':8} {'chars':>8}",
        "-" * 78,
    ]
    for entry in report:
        lines.append(
            f"{entry['scheme'][:34]:34} {entry['scope']:8} {entry['content_type']:5} "
            f"{entry['status']:8} {entry['chars']:>8}"
        )
        if entry["error"]:
            lines.append(f"    error: {entry['error']}")
    ok = sum(1 for e in report if e["status"] == "ok")
    lines.append("-" * 78)
    lines.append(
        f"ingested {ok}/{len(report)} sources, "
        f"{sum(e['chars'] for e in report)} chars total"
    )
    return "\n".join(lines)
