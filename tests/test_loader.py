"""Stage 1 tests: extraction, cleaning, and the failure modes loader exists to catch."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from rag import loader
from sources import Source, load_sources, slugify


def make_source(**overrides) -> Source:
    base = {
        "scheme": "HDFC Large Cap Fund",
        "category": "Large Cap",
        "url": "https://example.test/scheme",
        "slug": "hdfc-large-cap-fund-direct-growth",
    }
    base.update(overrides)
    return Source(**base)


# --- clean_text ----------------------------------------------------------

def test_clean_text_collapses_whitespace_and_strips_zero_width():
    raw = "Expense  ratio  is \r\n 0.50%\t today"
    assert loader.clean_text(raw, preserve_structure=False) == "Expense ratio is 0.50% today"


def test_clean_text_normalises_unicode_quotes_and_dashes():
    raw = "The “risk” level – and the ‘very high’ label"
    cleaned = loader.clean_text(raw, preserve_structure=False)
    assert cleaned == 'The "risk" level - and the \'very high\' label'


def test_clean_text_splits_glued_label_from_value():
    """trafilatura emits "Fund benchmarkNIFTY 100" with no separator, which
    leaves the chunker no boundary and slices a label away from its value."""
    assert loader.clean_text(
        "Fund benchmarkNIFTY 100 Total Return Index", preserve_structure=False
    ) == "Fund benchmark NIFTY 100 Total Return Index"
    assert loader.clean_text(
        "Date of Incorporation10 Dec 1999", preserve_structure=False
    ) == "Date of Incorporation 10 Dec 1999"


def test_clean_text_does_not_shatter_acronyms_or_quotes():
    """The acronym rule was tried and rejected: ETFs/IPOs/SIPs are shaped
    exactly like the glued "AUMRs", and it broke them into "ET Fs"."""
    raw = "The fund holds ETFs and IPOs; HDFC, ICICI, SIP and NIFTY 500 are cited."
    assert loader.clean_text(raw, preserve_structure=False) == raw
    quoted = 'The "risk" level and the label'
    assert loader.clean_text(quoted, preserve_structure=False) == quoted


def test_clean_text_drops_consent_and_nav_lines():
    raw = "\n".join(
        [
            "We use cookies to improve your experience.",
            "Stocks",
            "Invest in stocks",
            "Expense ratio 0.50%",
        ]
    )
    cleaned = loader.clean_text(raw, preserve_structure=False)
    assert cleaned == "Expense ratio 0.50%"
    assert "cookie" not in cleaned.lower()
    assert "stocks" not in cleaned.lower()


def test_clean_text_strips_nav_blurbs_merged_into_prose():
    raw = (
        "Invest in stocks, ETFs, IPOs with fast orders. Track returns on your stock "
        "holdings and view real-time P&L on your positions. Expense ratio 0.50%"
    )
    cleaned = loader.clean_text(raw, preserve_structure=False)
    assert cleaned == "Expense ratio 0.50%"
    assert "P&L" not in cleaned


def test_clean_text_keeps_table_rows_and_list_items_on_their_own_lines():
    raw = "\n".join(
        [
            "Exit load details follow.",
            "| Period | Load |",
            "|---|---|",
            "| 1 year | 1% |",
            "- Minimum SIP is Rs.100",
            "Benchmark is NIFTY 100 TRI.",
        ]
    )
    lines = loader.clean_text(raw, preserve_structure=True).split("\n")
    assert lines[0] == "Exit load details follow."
    assert lines[1] == "| Period | Load |"
    assert lines[2] == "|---|---|"
    assert lines[3] == "| 1 year | 1% |"
    assert lines[4] == "- Minimum SIP is Rs.100"
    assert lines[5] == "Benchmark is NIFTY 100 TRI."


def test_clean_text_collapses_pdf_prose_by_default():
    raw = "Shortest lock-in\nperiod of 3 years\n\nas compared to other options"
    assert loader.clean_text(raw, preserve_structure=False) == (
        "Shortest lock-in period of 3 years as compared to other options"
    )


def test_clean_text_on_empty_input():
    assert loader.clean_text("") == ""


# --- extraction ----------------------------------------------------------

def test_extract_html_prefers_trafilatura_over_longer_soup_text():
    """Length is not a quality signal: the longer BeautifulSoup result on these
    pages is the site navigation, so trafilatura must win."""
    html = (
        "<html><body><main><p>" + ("Exit load of 1% if redeemed within 1 year. " * 40)
        + "</p></main><nav>" + ("Stocks ETF Screener IPO MTFs Demat Account " * 200)
        + "</nav></body></html>"
    )
    extracted = loader._extract_html(html, "https://example.test/x")
    assert "Exit load" in extracted
    assert extracted.count("Stocks ETF Screener") < 5


def test_extract_html_falls_back_to_soup_when_trafilatura_finds_little(monkeypatch):
    monkeypatch.setattr(loader, "trafilatura", None)
    html = "<html><body><script>var x=1;</script><p>Minimum SIP is Rs.100</p></body></html>"
    assert "Minimum SIP" in loader._extract_html(html, "https://example.test/x")


def test_extract_html_raises_extraction_error_without_any_extractor(monkeypatch):
    monkeypatch.setattr(loader, "trafilatura", None)
    monkeypatch.setattr(loader, "BeautifulSoup", None)
    with pytest.raises(loader.ExtractionError):
        loader._extract_html("<html></html>", "https://example.test/x")


# --- the JS-rendered-page tripwire ---------------------------------------

def test_fetch_one_raises_ingest_error_when_text_is_too_small(monkeypatch):
    monkeypatch.setattr(loader, "_download", lambda session, source: b"<html></html>")
    monkeypatch.setattr(loader, "_extract_html", lambda html, url: "tiny")
    with pytest.raises(loader.IngestError) as excinfo:
        loader.fetch_one(make_source())
    message = str(excinfo.value)
    assert "MIN_TEXT_CHARS" in message
    assert "example.test" in message


def test_ingest_error_names_the_offending_url_not_just_the_scheme():
    error = loader.IngestError(
        "https://groww.in/x (HDFC Large Cap Fund) produced only 12 chars"
    )
    assert "https://groww.in/x" in str(error)


# --- fetch_all reporting -------------------------------------------------

def test_fetch_all_records_failures_instead_of_shrinking_the_corpus(monkeypatch):
    good = make_source(scheme="HDFC Large Cap Fund", url="https://example.test/good")
    bad = make_source(scheme="HDFC Small Cap Fund", url="https://example.test/bad")

    def fake_fetch_one(source, session=None):
        if source.url.endswith("bad"):
            raise loader.FetchError(f"could not fetch {source.url}: timeout")
        return loader.Document(source, "x" * 900, "2026-01-01", "abc123")

    monkeypatch.setattr(loader, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(config, "FETCH_DELAY_SECONDS", 0)

    documents, report = loader.fetch_all([good, bad])

    assert len(documents) == 1
    assert [entry["status"] for entry in report] == ["ok", "failed"]
    assert "timeout" in report[1]["error"]


def test_fetch_all_records_unexpected_exceptions_rather_than_losing_them(monkeypatch):
    def boom(source, session=None):
        raise RuntimeError("something unforeseen")

    monkeypatch.setattr(loader, "fetch_one", boom)
    monkeypatch.setattr(config, "FETCH_DELAY_SECONDS", 0)

    documents, report = loader.fetch_all([make_source()])
    assert documents == []
    assert "RuntimeError" in report[0]["error"]


# --- save_raw ------------------------------------------------------------

def test_save_raw_writes_text_and_report(tmp_path):
    document = loader.Document(
        make_source(), "Exit load of 1%.", "2026-01-01", "deadbeef"
    )
    written = loader.save_raw([document], [], raw_dir=tmp_path)

    body = Path(written[0]).read_text(encoding="utf-8")
    assert "# scheme: HDFC Large Cap Fund" in body
    assert "# text_hash: deadbeef" in body
    assert body.rstrip().endswith("Exit load of 1%.")

    report = json.loads((tmp_path / "_ingest_report.json").read_text(encoding="utf-8"))
    assert report["documents"] == 1
    assert report["failed"] == 0


# --- the corpus itself ---------------------------------------------------

def test_corpus_has_the_five_schemes_plus_the_regulator_pages():
    sources = load_sources()
    assert len(sources) == 7
    scheme_scoped = [s for s in sources if s.scope == "scheme"]
    general = [s for s in sources if s.scope == "general"]
    assert len(scheme_scoped) == 5
    assert len(general) == 2
    assert {s.category for s in general} == {"Regulator (AMFI)"}


def test_every_source_url_is_https():
    for source in load_sources():
        assert source.url.startswith("https://")


def test_general_sources_exist_so_scheme_filtering_cannot_hide_them():
    """An ELSS lock-in question filters on scheme; the lock-in text only exists
    on a general source, so at least one general source must be present."""
    assert any(s.scope == "general" for s in load_sources())


def test_scheme_aliases_resolve_to_real_schemes():
    scheme_names = {s.scheme for s in load_sources()}
    for alias, target in __import__("sources").SCHEME_ALIASES.items():
        assert target in scheme_names, alias


def test_slugify():
    assert slugify("HDFC ELSS Tax Saver Fund") == "hdfc-elss-tax-saver-fund"
    assert slugify("AMFI Investor Awareness Programme") == (
        "amfi-investor-awareness-programme"
    )
