"""Stage 2: CHUNKING. Split cleaned documents into retrievable chunks.

The strategy in config.CHUNK_STRATEGY was chosen by inspecting this corpus, not
by preference. Evidence is in reports/chunking_decision.md. The short version:
the corpus has no headings at all, so heading-based splitting is inert here, and
it is tabular, so a pure character ladder slices fee rows and label/value pairs
in half. Sentence-aware segmentation with table-block protection wins.

Segment model
-------------
A document is first reduced to atomic segments, which are the smallest pieces
this corpus can be cut at without destroying meaning:

  * prose sentences, split on sentence punctuation, after label/value de-gluing
    has put a real boundary at "Fund benchmark | NIFTY 500"
  * table blocks, which are runs of consecutive markdown rows and are never split
    mid-row

Strategies then differ only in how they treat those segments.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

import config
from rag.loader import Document
from sources import Source

SEPARATOR_LADDER = ["\n####", "\n###", "\n##", "\n#", "\n\n", ". ", " "]

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")
TABLE_LINE = re.compile(r"^\s*\|")
# Either a markdown heading, or a short capitalised line such as "Exit load:".
# The two are alternates within one pattern, so each has to be able to match a
# whole line on its own; splitting them into separate anchored patterns is what
# previously left "## Fees" undetectable.
HEADING = re.compile(r"^(?:#{1,6}\s+\S.*|[A-Z][A-Za-z0-9 &/()-]{2,58}:?)$")
# The AMFI pages carry a few tables that are whitespace-aligned rather than
# pipe-delimited. Per the spec, a run of 3+ consecutive numeric tokens marks
# such a line as table-like so it is never cut mid-row.
NUMERIC_TOKEN = re.compile(r"^[-(]?[$]?[+-]?[\d,]+(?:\.\d+)?%?\)?$")
# Longest line still treated as a single table row. A fee or holding row is far
# below this; anything longer is prose that merely contains numbers.
MAX_TABLE_ROW_CHARS = 240


def _is_numeric_run(line: str) -> bool:
    """True for a line of 3+ consecutive numeric tokens, e.g. a returns row.

    The AMFI pages carry a few tables that are whitespace-aligned rather than
    pipe-delimited, so pipes alone would not protect their rows. The run must be
    consecutive: prose such as "NAV: 25 Sep '26 Rs.1,189.08" has numbers in it
    but is not a row, and must stay splittable prose. The length cap matters more
    still: the AMFI PDF body is a single 27k-character run-on line that happens
    to contain three consecutive numbers, and without the cap the whole document
    is misread as one indivisible table row.
    """
    if len(line) > MAX_TABLE_ROW_CHARS:
        return False
    run = 0
    for token in line.split():
        if NUMERIC_TOKEN.match(token):
            run += 1
            if run >= 3:
                return True
        else:
            run = 0
    return False


# Fact labels used to give every chunk a meaningful `section`, since this corpus
# has no headings to inherit one from. Order matters: the first match wins, so
# more specific labels are listed before broader ones.
SECTION_LABELS: Tuple[Tuple[str, str], ...] = (
    (r"exit load", "Exit load"),
    (r"expense ratio|total expense|expenses? of the scheme", "Expense ratio"),
    (r"lock-?in|section 80c|80c|tax saver", "ELSS lock-in and tax"),
    (r"riskometer|risk level|very high risk|moderately high|risk\b", "Riskometer"),
    (r"benchmark", "Benchmark"),
    (r"minimum (sip|lumpsum|investment)|min\.? for (sip|1st|2nd)", "Minimum investment"),
    (r"consolidated account statement|account statement|capital gains|\bcas\b", "Account statements"),
    (r"nav\b|expense ratio", "NAV and pricing"),
    (r"holdings|portfolio|equity\s*\|", "Portfolio holdings"),
    (r"returns?\b|annualised|1 year|3 year|5 year|10 year", "Returns table"),
    (r"fund objective|seeks to|investment objective", "Objective"),
    (r"fund manager|current fund manager", "Fund manager"),
    (r"aum|fund size|net assets", "AUM"),
    (r"tax|harvest|redemption", "Tax and redemption"),
    (r"investor|folio|kYC|kyc", "Investor and folio"),
)


@dataclass
class Chunk:
    """A retrievable unit. Contract per architecture.md section 5."""

    chunk_id: str
    text: str
    scheme: str
    category: str
    scope: str
    section: str
    source_url: str
    ordinal: int
    token_estimate: int
    kind: str = "prose"  # prose | table
    strategy: str = ""

    def as_dict(self) -> Dict:
        return asdict(self)


@dataclass
class Segment:
    """An atomic piece of a document, before it is merged into chunks."""

    text: str
    kind: str  # prose | table

    def __len__(self) -> int:
        return len(self.text)


# --- text utilities ------------------------------------------------------


def token_estimate(text: str) -> int:
    """Cheap character heuristic, so no tokenizer has to be downloaded."""
    return max(1, len(text) // 4)


def _is_table_line(line: str) -> bool:
    return bool(TABLE_LINE.match(line)) or _is_numeric_run(line)


def infer_section(text: str, fallback: str) -> str:
    """Name a chunk after the fact it carries.

    The corpus has zero headings, so `section` would otherwise be the scheme
    name on every chunk and carry no retrieval or citation signal. Matching a
    known fact label gives the chunk usable metadata instead.
    """
    lowered = text.lower()
    for pattern, label in SECTION_LABELS:
        if re.search(pattern, lowered, re.IGNORECASE):
            return label
    return fallback


def _overlap_tail(window: Sequence[Segment], overlap: int) -> List[Segment]:
    """The trailing segments of a window to repeat at the head of the next one.

    Segments are repeated whole, not sliced mid-sentence, so a label never gets
    separated from its value across the seam. An empty result means overlap is 0
    or the window holds a single segment too large to share.
    """
    if overlap <= 0 or len(window) < 2:
        return []
    tail: List[Segment] = []
    for segment in reversed(window):
        # +1 per segment for the "\n" the join will insert.
        if sum(len(s) for s in tail) + len(segment) + len(tail) + 1 > overlap:
            break
        tail.insert(0, segment)
    return tail


def _joined_len(segments: Sequence[Segment]) -> int:
    """Exact length of "\\n".join(segments)."""
    if not segments:
        return 0
    return sum(len(s) for s in segments) + len(segments) - 1


def _make_chunks(
    doc: Document, segments: Sequence[Segment], strategy: str, overlap: Optional[int] = None
) -> List[Chunk]:
    """Merge atomic segments up to the size budget, never splitting a segment."""
    overlap = config.CHUNK_OVERLAP if overlap is None else overlap
    size = config.CHUNK_SIZE
    source = doc.source

    chunks: List[Chunk] = []
    window: List[Segment] = []
    pending_carry: List[Segment] = []
    ordinal = 0

    def flush() -> None:
        nonlocal window, ordinal
        if not window:
            return
        text = "\n".join(segment.text for segment in window).strip()
        if len(text) >= config.MIN_CHUNK_CHARS:
            kind = "table" if all(s.kind == "table" for s in window) else "prose"
            chunks.append(
                Chunk(
                    chunk_id=chunk_id(source, doc.text_hash, ordinal),
                    text=text,
                    scheme=source.scheme,
                    category=source.category,
                    scope=source.scope,
                    section=infer_section(text, source.scheme),
                    source_url=source.url,
                    ordinal=ordinal,
                    token_estimate=token_estimate(text),
                    kind=kind,
                    strategy=strategy,
                )
            )
            ordinal += 1
        window = []

    for segment in _normalize_segments(segments, size):
        # Repeat the previous window's tail at the head of this one. The test is
        # against the whole resulting window, not just the carry, because the
        # window may already hold a segment: after a flush the current segment is
        # appended before the next iteration runs. Seeding blind there produced
        # 824-character chunks, well past the budget.
        if pending_carry:
            carried = pending_carry
            pending_carry = []
            candidate = window + carried + [segment]
            if _joined_len(candidate) <= size:
                window = candidate
                continue

        # window_len is the exact length of "\n".join(window), so the separator
        # cost is charged when a segment is added. Ignoring it let every chunk
        # overshoot by up to one character per segment it contained.
        joined = _joined_len(window) + (1 if window else 0) + len(segment)
        # A window below MIN_CHUNK_CHARS keeps absorbing instead of flushing.
        # This is a data-loss guard, not a size preference: on the Groww pages
        # the fact sentences sit in short prose islands between two tables, so
        # flushing one on its own discarded "Minimum SIP Investment is set to
        # Rs.100." entirely, and the graded answer vanished from the corpus.
        # The overshoot is bounded by MIN_CHUNK_CHARS because that is how much
        # more the window is allowed to take.
        if joined > size and window and _joined_len(window) >= config.MIN_CHUNK_CHARS:
            pending_carry = _overlap_tail(window, overlap)
            flush()
        window.append(segment)

    flush()
    return chunks


def _normalize_segments(segments: Sequence[Segment], size: int) -> List[Segment]:
    """Break any segment larger than the budget into budget-sized pieces.

    Two different sources of oversized segments, and they need different cuts:

    * a table block, cut on row boundaries so a fee row is never halved
    * a prose "sentence", which happens on the AMFI PDF: it is slide text with
      no sentence punctuation, so the sentence splitter finds nothing and a
      single segment can run to 3,292 characters. Cut on the same ladder the
      recursive strategy uses.

    A single table row longer than the budget is left intact deliberately; it is
    the smallest meaningful unit in that table.
    """
    out: List[Segment] = []
    for segment in segments:
        if len(segment) <= size:
            out.append(segment)
            continue
        if segment.kind == "table":
            out.extend(Segment(piece, "table") for piece in _split_table_block(segment, size))
        else:
            out.extend(
                Segment(piece, "prose")
                for piece in _recursive_split(segment.text, size, [". ", " "])
                if piece.strip()
            )
    return out


def _split_table_block(segment: Segment, size: int) -> List[str]:
    """Cut a table block on row boundaries, never mid-row.

    A block that is a single line has no row boundary to cut on, so it falls back
    to word splitting. Without that fallback a misread run-on line would be
    emitted whole, since the only other option is to keep an oversized row
    intact.
    """
    rows = [row for row in segment.text.split("\n") if row.strip()]
    if len(rows) == 1 and len(rows[0]) > size:
        return _recursive_split(rows[0], size, [" "])
    pieces: List[str] = []
    current: List[str] = []
    current_len = 0
    for row in rows:
        if current and current_len + len(row) + 1 > size:
            pieces.append("\n".join(current))
            current, current_len = [], 0
        current.append(row)
        current_len += len(row) + 1
    if current:
        pieces.append("\n".join(current))
    return pieces


def chunk_id(source: Source, text_hash: str, ordinal: int) -> str:
    """Content-addressed, so a re-ingest cannot duplicate a chunk."""
    from sources import slugify

    return f"{slugify(source.scheme)}:{text_hash[:8]}:{ordinal}"


# --- segmentation --------------------------------------------------------


def segment_document(doc: Document) -> List[Segment]:
    """Reduce a document to atomic prose sentences and table blocks."""
    lines = doc.text.split("\n")
    segments: List[Segment] = []
    buffer: List[str] = []
    table: List[str] = []

    def flush_prose() -> None:
        if not buffer:
            return
        blob = " ".join(buffer).strip()
        buffer.clear()
        if not blob:
            return
        for sentence in SENTENCE_SPLIT.split(blob):
            sentence = sentence.strip()
            if sentence:
                segments.append(Segment(sentence, "prose"))

    def flush_table() -> None:
        if not table:
            return
        block = "\n".join(table).strip()
        table.clear()
        if block:
            segments.append(Segment(block, "table"))

    for line in lines:
        if _is_table_line(line):
            flush_prose()
            table.append(line)
        else:
            flush_table()
            buffer.append(line)
    flush_prose()
    flush_table()
    return segments


# --- strategies ----------------------------------------------------------


class ChunkStrategy(Protocol):
    name: str

    def split(self, doc: Document) -> List[Chunk]: ...


class RecursiveSplitter:
    """Character/sentence ladder over the raw text. The naive baseline.

    It has no notion of a table row or a label/value pair, so on this corpus it
    cuts both. Kept because the comparison in the decision report is the point.
    """

    name = "recursive"

    def split(self, doc: Document) -> List[Chunk]:
        pieces = _recursive_split(doc.text, config.CHUNK_SIZE, SEPARATOR_LADDER)
        segments = [Segment(piece, "prose") for piece in pieces if piece.strip()]
        return _make_chunks(doc, segments, self.name, overlap=config.CHUNK_OVERLAP)


class SectionSplitter:
    """Heading-based, with recursive fallback inside oversized sections.

    The fallback is what makes this safe on a corpus with no headings: it
    degrades to the recursive behaviour rather than emitting one document-sized
    chunk. On this corpus that is exactly what happens, and the resulting chunk
    sizes are the evidence against this strategy.
    """

    name = "section"

    def split(self, doc: Document) -> List[Chunk]:
        lines = doc.text.split("\n")
        sections: List[Tuple[str, List[str]]] = [("", [])]
        for line in lines:
            if HEADING.match(line.strip()) and not _is_table_line(line):
                sections.append((line.strip(), []))
            else:
                sections[-1][1].append(line)

        segments: List[Segment] = []
        for heading, body in sections:
            body_text = "\n".join(body).strip()
            if not body_text:
                continue
            for piece in _recursive_split(body_text, config.CHUNK_SIZE, SEPARATOR_LADDER):
                segment = piece
                if heading:
                    segment = f"{heading}\n{segment}"
                segments.append(Segment(segment, "prose"))
        return _make_chunks(doc, segments, self.name, overlap=config.CHUNK_OVERLAP)


class TableAwareSplitter:
    """Sentence-aware prose plus table blocks as atomic units. The winner.

    Two properties the others lack on this corpus: a fee row or exit-load slab
    stays whole, and a label stays attached to its value, because both are
    boundaries the segmenter recognises before any size cut happens.
    """

    name = "table_aware"

    def split(self, doc: Document) -> List[Chunk]:
        return _make_chunks(doc, segment_document(doc), self.name)


def _recursive_split(text: str, size: int, separators: Sequence[str]) -> List[str]:
    """Standard recursive character splitting down the separator ladder."""
    if len(text) <= size:
        return [text]
    for index, separator in enumerate(separators):
        if separator not in text:
            continue
        parts = text.split(separator)
        rest = separators[index + 1 :]
        out: List[str] = []
        buffer = ""
        for part in parts:
            candidate = (buffer + separator + part) if buffer else part
            if len(candidate) <= size:
                buffer = candidate
                continue
            if buffer:
                out.append(buffer)
            if len(part) > size:
                out.extend(_recursive_split(part, size, rest or [" "]))
                buffer = ""
            else:
                buffer = part
        if buffer:
            out.append(buffer)
        return out
    return [text]


STRATEGIES: Dict[str, ChunkStrategy] = {
    RecursiveSplitter.name: RecursiveSplitter(),
    SectionSplitter.name: SectionSplitter(),
    TableAwareSplitter.name: TableAwareSplitter(),
}


def get_strategy(name: Optional[str] = None) -> ChunkStrategy:
    name = name or config.CHUNK_STRATEGY
    if name not in STRATEGIES:
        raise KeyError(
            f"unknown chunking strategy {name!r}; choose from {sorted(STRATEGIES)}"
        )
    return STRATEGIES[name]


# --- batch API -----------------------------------------------------------


def split_all(documents: Iterable[Document], strategy: Optional[str] = None) -> List[Chunk]:
    """Chunk every document with one strategy, ordinals continuing per document."""
    splitter = get_strategy(strategy)
    chunks: List[Chunk] = []
    for document in documents:
        chunks.extend(splitter.split(document))
    return chunks


def validate_chunks(chunks: Sequence[Chunk]) -> List[str]:
    """Return human-readable invariant violations. Empty list means healthy."""
    problems: List[str] = []
    seen: Dict[str, int] = {}
    for chunk in chunks:
        if not chunk.text.strip():
            problems.append(f"{chunk.chunk_id}: empty text")
        if not chunk.section.strip():
            problems.append(f"{chunk.chunk_id}: empty section")
        if not chunk.source_url.strip():
            problems.append(f"{chunk.chunk_id}: empty source_url")
        if len(chunk.text) > config.CHUNK_SIZE + _size_tolerance(chunk):
            problems.append(
                f"{chunk.chunk_id}: oversize {len(chunk.text)} chars "
                f"(limit {config.CHUNK_SIZE})"
            )
        if len(chunk.text) < config.MIN_CHUNK_CHARS:
            problems.append(
                f"{chunk.chunk_id}: undersize {len(chunk.text)} chars "
                f"(min {config.MIN_CHUNK_CHARS})"
            )
        if chunk.kind == "table" and re.search(r"^\s*\|?[^|\n]*$", chunk.text):
            problems.append(f"{chunk.chunk_id}: table chunk lost its row structure")
        seen[chunk.chunk_id] = seen.get(chunk.chunk_id, 0) + 1
    for chunk_id_value, count in seen.items():
        if count > 1:
            problems.append(f"{chunk_id_value}: duplicate chunk_id x{count}")
    return problems


def _size_tolerance(chunk: Chunk) -> int:
    """How far a chunk may exceed the budget and still be valid.

    A table block may overshoot by up to one indivisible row. Any chunk may
    overshoot by MIN_CHUNK_CHARS, because a window under the minimum is allowed
    to keep absorbing segments rather than be discarded (see _make_chunks).
    """
    row_tolerance = 0 if chunk.kind == "prose" else 120
    return max(row_tolerance, config.MIN_CHUNK_CHARS)



def persist_chunks(chunks: Sequence[Chunk], path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([chunk.as_dict() for chunk in chunks], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return str(path)


def strategy_stats(chunks: Sequence[Chunk]) -> Dict:
    sizes = [len(chunk.text) for chunk in chunks]
    if not sizes:
        return {"chunks": 0}
    return {
        "chunks": len(chunks),
        "table_chunks": sum(1 for c in chunks if c.kind == "table"),
        "mean_chars": round(statistics.mean(sizes), 1),
        "median_chars": round(statistics.median(sizes), 1),
        "min_chars": min(sizes),
        "max_chars": max(sizes),
        "distinct_sections": len({chunk.section for chunk in chunks}),
        "total_tokens_est": sum(chunk.token_estimate for chunk in chunks),
    }


def load_documents() -> List[Document]:
    """Re-read the P1 artefacts, so chunking is reproducible without refetching."""
    report_path = config.RAW_DIR / "_ingest_report.json"
    if not report_path.exists():
        raise FileNotFoundError(
            "no ingest report; run: python run_ingest.py --fetch-only"
        )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    from sources import load_sources

    by_url = {source.url: source for source in load_sources()}
    documents: List[Document] = []
    for entry in report["sources"]:
        if entry["status"] != "ok":
            continue
        source = by_url[entry["url"]]
        path = config.RAW_DIR / f"{_slug(source.scheme)}.txt"
        body = path.read_text(encoding="utf-8").split("-" * 72, 1)[1].strip()
        documents.append(
            Document(
                source=source,
                text=body,
                fetched_at=entry["fetched_at"],
                text_hash=entry["text_hash"],
            )
        )
    return documents


def _slug(value: str) -> str:
    from sources import slugify

    return slugify(value)
