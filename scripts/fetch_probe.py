"""Q1 probe: can a static HTTP GET extract usable text from the 5 scheme pages?

Run once during P1. Prints {url: char_count} for both extractors so the corpus
decision in implementation.md P1 is made from evidence, not assumption.

    python scripts/fetch_probe.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import sources


def main() -> int:
    try:
        import trafilatura
    except ImportError:
        trafilatura = None
    from bs4 import BeautifulSoup
    import requests

    session = requests.Session()
    session.headers.update({"User-Agent": config.USER_AGENT, "Accept-Language": "en-IN,en;q=0.9"})

    print(f"{'scheme':32} {'status':>6} {'html':>8} {'trafi':>8} {'best':>8}  verdict")
    print("-" * 88)

    usable = 0
    for source in sources.load_sources():
        try:
            response = session.get(
                source.url, timeout=config.HTTP_TIMEOUT, allow_redirects=True
            )
            status = response.status_code
            html = response.text if status == 200 else ""
        except Exception as exc:
            print(f"{source.scheme:32} {'ERR':>6} {'':>8} {'':>8} {'':>8}  {type(exc).__name__}")
            continue

        trafi_len = 0
        if trafilatura is not None and html:
            try:
                trafi = trafilatura.extract(
                    html,
                    url=source.url,
                    include_tables=True,
                    include_comments=False,
                    favor_recall=True,
                )
                trafi_len = len(trafi.strip()) if trafi else 0
            except Exception:
                trafi_len = 0

        soup_len = 0
        if html:
            soup = BeautifulSoup(html, "lxml")
            for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
                tag.decompose()
            soup_len = len(soup.get_text(separator=" ", strip=True))

        best = max(trafi_len, soup_len)
        verdict = "USABLE" if best >= config.MIN_TEXT_CHARS else "EMPTY (JS-rendered?)"
        if verdict == "USABLE":
            usable += 1

        print(
            f"{source.scheme:32} {status:>6} {len(html):>8} {trafi_len:>8} "
            f"{soup_len:>8}  {verdict}"
        )
        time.sleep(config.FETCH_DELAY_SECONDS)

    print("-" * 88)
    print(f"usable pages: {usable}/{len(sources.load_sources())} (threshold {config.MIN_TEXT_CHARS} chars)")
    if usable == len(sources.load_sources()):
        print("Q1 answer: YES - static fetch works, proceed with Groww as the corpus.")
    elif usable > 0:
        print("Q1 answer: PARTIAL - mixed corpus, see implementation.md P1 fallback table.")
    else:
        print("Q1 answer: NO - follow the architecture.md 6.1 fallback order (HDFC factsheet PDFs).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
