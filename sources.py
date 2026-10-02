"""The approved public sources. Single source of truth for the corpus.

15 scheme pages on Groww (the assigned source platform) plus 2 regulator pages on
AMFI. The AMFI pages are required because the Groww scheme pages do not carry
ELSS lock-in, riskometer, or statement guidance, which PRD 11 criterion 2 asks
the assistant to answer. PRD 4.1 permits supplementary sources only when they
are public first-party or regulator URLs; both AMFI pages qualify and both are
recorded in the source list.

The 15 Groww pages span the categories the assignment cares about -- equity by
cap (large, flexi, mid, small), a value fund, balanced advantage, ELSS, index
tracking, and debt (liquid, ultra short, banking and PSU, credit risk, medium
term, gilt) -- so "expense ratio", "exit load", and "riskometer" questions have
both an equity and a debt answer in scope, rather than resolving to the only
scheme that happens to mention the term.

Reachability evidence (recorded during P1, see reports/ingest_decisions.md):
  groww.in            200, extractable          -> scheme pages
  hdfcfund.com        403 for every client      -> unusable, bot protection
  sebi.gov.in         connection timeout        -> unusable from this network
  amfiindia.com       200, extractable          -> regulator pages
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

SOURCES: List["Source"] = [
    {
        "scheme": "HDFC Large Cap Fund",
        "category": "Large Cap",
        "slug": "hdfc-large-cap-fund-direct-growth",
        "url": "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
    },
    {
        "scheme": "HDFC Equity Fund",
        "category": "Flexi Cap",
        "slug": "hdfc-equity-fund-direct-growth",
        "url": "https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth",
    },
    {
        "scheme": "HDFC ELSS Tax Saver Fund",
        "category": "ELSS",
        "slug": "hdfc-elss-tax-saver-fund-direct-plan-growth",
        "url": "https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth",
    },
    {
        "scheme": "HDFC Small Cap Fund",
        "category": "Small Cap",
        "slug": "hdfc-small-cap-fund-direct-growth",
        "url": "https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth",
    },
    {
        "scheme": "HDFC Balanced Advantage Fund",
        "category": "Balanced Advantage",
        "slug": "hdfc-balanced-advantage-fund-direct-growth",
        "url": "https://groww.in/mutual-funds/hdfc-balanced-advantage-fund-direct-growth",
    },
    {
        "scheme": "HDFC Mid-Cap Opportunities Fund",
        "category": "Mid Cap",
        "slug": "hdfc-mid-cap-opportunities-fund-direct-growth",
        "url": "https://groww.in/mutual-funds/hdfc-mid-cap-opportunities-fund-direct-growth",
    },
    {
        "scheme": "HDFC Value Fund",
        "category": "Value",
        "slug": "hdfc-value-fund-direct-plan-growth",
        "url": "https://groww.in/mutual-funds/hdfc-value-fund-direct-plan-growth",
    },
    {
        "scheme": "HDFC Liquid Fund",
        "category": "Liquid",
        "slug": "hdfc-liquid-fund-direct-growth",
        "url": "https://groww.in/mutual-funds/hdfc-liquid-fund-direct-growth",
    },
    {
        "scheme": "HDFC Ultra Short Term Fund",
        "category": "Ultra Short Duration",
        "slug": "hdfc-ultra-short-term-fund-direct-growth",
        "url": "https://groww.in/mutual-funds/hdfc-ultra-short-term-fund-direct-growth",
    },
    {
        "scheme": "HDFC Banking and PSU Debt Fund",
        "category": "Banking and PSU Debt",
        "slug": "hdfc-banking-and-psu-debt-fund-direct-growth",
        "url": "https://groww.in/mutual-funds/hdfc-banking-and-psu-debt-fund-direct-growth",
    },
    {
        "scheme": "HDFC Credit Risk Fund",
        "category": "Credit Risk",
        "slug": "hdfc-credit-risk-fund-direct-growth",
        "url": "https://groww.in/mutual-funds/hdfc-credit-risk-fund-direct-growth",
    },
    {
        "scheme": "HDFC Medium Term Fund",
        "category": "Medium Term",
        "slug": "hdfc-medium-term-fund-direct-growth",
        "url": "https://groww.in/mutual-funds/hdfc-medium-term-fund-direct-growth",
    },
    {
        "scheme": "HDFC Gilt Fund",
        "category": "Gilt",
        "slug": "hdfc-gilt-fund-direct-growth",
        "url": "https://groww.in/mutual-funds/hdfc-gilt-fund-direct-growth",
    },
    {
        "scheme": "HDFC Nifty Midcap 150 Index Fund",
        "category": "Index",
        "slug": "hdfc-nifty-midcap-150-index-fund-direct-growth",
        "url": "https://groww.in/mutual-funds/hdfc-nifty-midcap-150-index-fund-direct-growth",
    },
    {
        "scheme": "HDFC Nifty 100 Index Fund",
        "category": "Index",
        "slug": "hdfc-nifty-100-index-fund-direct-growth",
        "url": "https://groww.in/mutual-funds/hdfc-nifty-100-index-fund-direct-growth",
    },
]

GENERAL_SOURCES: List["Source"] = [
    {
        "scheme": "AMFI Investor Awareness Programme",
        "category": "Regulator (AMFI)",
        "slug": "amfi-investor-awareness-presentation",
        "url": (
            "https://www.amfiindia.com/Themes/Theme1/downloads/"
            "InvestorsAwarenessProgrampresentation.pdf"
        ),
        "url_type": "regulator",
        "scope": "general",
        "content_type": "pdf",
    },
    {
        "scheme": "AMFI Account Statements and CAS",
        "category": "Regulator (AMFI)",
        "slug": "amfi-account-statements-cas",
        "url": (
            "https://www.amfiindia.com/investor/"
            "become-mf-distributor?zoneName=InvestorService"
        ),
        "url_type": "regulator",
        "scope": "general",
        "content_type": "html",
    },
]

# Every scheme page in SOURCES needs at least its full name here. `detect_scheme`
# consults only this table, so a scheme that is fetched, chunked and indexed but
# missing from here is invisible to the retriever: the query gets no scheme filter
# and the factual chunk competes against all 407 vectors, which is how 10 of the 15
# pages came back NOT_FOUND on questions their own pages answer. A test asserts the
# two lists agree, so adding a source without an alias fails the suite instead of
# shipping a page that silently cannot be asked about.
SCHEME_ALIASES = {
    "hdfc mid cap opportunities fund": "HDFC Mid-Cap Opportunities Fund",
    "mid cap opportunities": "HDFC Mid-Cap Opportunities Fund",
    "hdfc value fund": "HDFC Value Fund",
    "hdfc liquid fund": "HDFC Liquid Fund",
    "hdfc ultra short term fund": "HDFC Ultra Short Term Fund",
    "ultra short term": "HDFC Ultra Short Term Fund",
    "hdfc banking and psu debt fund": "HDFC Banking and PSU Debt Fund",
    "banking and psu debt": "HDFC Banking and PSU Debt Fund",
    "psu debt": "HDFC Banking and PSU Debt Fund",
    "hdfc credit risk fund": "HDFC Credit Risk Fund",
    "credit risk": "HDFC Credit Risk Fund",
    "hdfc medium term fund": "HDFC Medium Term Fund",
    "medium term": "HDFC Medium Term Fund",
    "hdfc gilt fund": "HDFC Gilt Fund",
    "hdfc nifty midcap 150 index fund": "HDFC Nifty Midcap 150 Index Fund",
    "nifty midcap 150": "HDFC Nifty Midcap 150 Index Fund",
    "hdfc nifty 100 index fund": "HDFC Nifty 100 Index Fund",
    "nifty 100": "HDFC Nifty 100 Index Fund",
    "hdfc balanced advantage fund": "HDFC Balanced Advantage Fund",
    "balanced advantage": "HDFC Balanced Advantage Fund",
    "hdfc elss tax saver fund": "HDFC ELSS Tax Saver Fund",
    "hdfc elss": "HDFC ELSS Tax Saver Fund",
    "elss tax saver": "HDFC ELSS Tax Saver Fund",
    "tax saver fund": "HDFC ELSS Tax Saver Fund",
    "hdfc equity fund": "HDFC Equity Fund",
    "flexi cap": "HDFC Equity Fund",
    "hdfc large cap fund": "HDFC Large Cap Fund",
    "large cap": "HDFC Large Cap Fund",
    "hdfc small cap fund": "HDFC Small Cap Fund",
    "small cap": "HDFC Small Cap Fund",
    "elss": "HDFC ELSS Tax Saver Fund",
}


def slugify(value: str) -> str:
    out = [c.lower() if c.isalnum() else "-" for c in value]
    text = "".join(out)
    while "--" in text:
        text = text.replace("--", "-")
    return text.strip("-")


@dataclass(frozen=True)
class Source:
    """One approved public page. Frozen so it cannot be mutated downstream.

    scope="scheme"  one of the 5 HDFC schemes, and is eligible for the
                    scheme metadata filter used at retrieval time.
    scope="general" regulator or investor-education material that is not tied to
                    one scheme. These must stay retrievable even when the user
                    names a scheme, otherwise "ELSS lock-in" would filter the
                    corpus down to the Groww ELSS page, which never states the
                    lock-in. Retrieval therefore filters with
                    {"$or": [{"scheme": X}, {"scope": "general"}]}.
    """

    scheme: str
    category: str
    url: str
    slug: str
    url_type: str = "scheme_page"
    scope: str = "scheme"
    content_type: str = "html"

    @property
    def scheme_slug(self) -> str:
        return slugify(self.scheme)

    def as_dict(self) -> dict:
        return {
            "scheme": self.scheme,
            "category": self.category,
            "url": self.url,
            "slug": self.slug,
            "url_type": self.url_type,
            "scope": self.scope,
            "content_type": self.content_type,
        }


def load_sources() -> List[Source]:
    """All corpus sources: the 15 scheme pages, then the general regulator pages."""
    return [Source(**entry) for entry in SOURCES + GENERAL_SOURCES]


if __name__ == "__main__":
    for source in load_sources():
        print(
            f"{source.scheme:34} {source.category:22} {source.scope:8} "
            f"{source.content_type:5} {source.url}"
        )
