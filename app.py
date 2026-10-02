#!/usr/bin/env python3
"""Single-screen Streamlit UI (PRD §7, architecture.md FR-11 + FR-14).

The screen is deliberately thin. Every decision — guarding, retrieval, generation,
verification — already happened in `rag.pipeline.answer()`, and this file only
renders the `Answer` it returns. That is what keeps the CLI, the eval harness, and
the UI telling the same story: they call the same one entry point and differ only
in how they show the result.

The dark theme is applied as one stylesheet rather than through per-widget options.
Streamlit's own theming API does not cover most of what a distinct look needs, and
scattering `st.markdown` calls with inline styles throughout the render functions
would put presentation decisions next to behaviour ones. Injecting once in
`main()` keeps the render functions readable and means the palette lives in a
single block that can be reworked without hunting through the file.

Two constraints the stylesheet is written around, both because the tests in
`tests/test_app.py` drive this file through `AppTest` and assert on the widget
tree, not on pixels: the title must stay an `st.title` (not a styled `markdown`
heading), and the submit button must remain the last `st.button` on the page.

Run:
    streamlit run app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from rag import embedder, pipeline, vectorstore  # noqa: E402
from rag.verifier import ANSWERED  # noqa: E402


# One palette, referenced by the stylesheet and by the status pill colours below.
# Keeping the hexes as named constants means the pill and the theme cannot drift
# apart, which is the usual way a themed UI ends up with two different greens.
INK = "#E8ECF4"
MUTED = "#8D97A8"
ACCENT = "#7DD3FC"
VIOLET = "#A78BFA"
AMBER = "#FBBF24"
ROSE = "#FB7185"
MINT = "#6EE7B7"

THEME = f"""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

  :root {{
    --ink: {INK};
    --muted: {MUTED};
    --accent: {ACCENT};
    --violet: {VIOLET};
    --line: rgba(255, 255, 255, 0.07);
    --line-strong: rgba(255, 255, 255, 0.13);
    --surface: #12161D;
    --surface-2: #171C25;
  }}

  /* App frame. Two fixed radial washes over a near-black base read as depth
     without an image request, so first paint stays fast on a free tier. */
  .stApp {{
    background:
      radial-gradient(1100px 620px at 12% -8%, rgba(125, 211, 252, 0.10), transparent 62%),
      radial-gradient(900px 560px at 92% 4%, rgba(167, 139, 250, 0.09), transparent 58%),
      radial-gradient(700px 700px at 50% 108%, rgba(110, 231, 183, 0.05), transparent 60%),
      #0A0C10;
    color: var(--ink);
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  }}

  .stApp, .stApp p, .stApp label, .stApp h1, .stApp h2, .stApp h3 {{
    color: var(--ink);
  }}

  /* A hairline grid over the whole page. At 3% opacity it is invisible as a
     pattern and only shows up as a texture in the dark areas. */
  [data-testid="stAppViewContainer"]::before {{
    content: '';
    position: fixed;
    inset: 0;
    pointer-events: none;
    background-image:
      linear-gradient(var(--line) 1px, transparent 1px),
      linear-gradient(90deg, var(--line) 1px, transparent 1px);
    background-size: 58px 58px;
    opacity: 0.5;
    z-index: 0;
  }}
  [data-testid="stAppViewContainer"] > * {{ position: relative; z-index: 1; }}

  [data-testid="stMain"] {{ max-width: 780px; }}

  /* Title as a gradient wordmark. Kept as st.title in the tree; only the
     rendering changes. */
  h1 {{
    font-weight: 600 !important;
    font-size: 2.35rem !important;
    letter-spacing: -0.035em !important;
    line-height: 1.12 !important;
    background: linear-gradient(96deg, #FFFFFF 4%, {ACCENT} 46%, {VIOLET} 92%);
    -webkit-background-clip: text;
    background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 0.15rem !important;
  }}

  /* A short accent rule under the wordmark, drawn rather than typed so it
     cannot end up in the copy a test reads. */
  [data-testid="stHeadingContainer"] h1 {{ position: relative; }}
  [data-testid="stHeadingContainer"] h1::after {{
    content: '';
    display: block;
    width: 68px;
    height: 3px;
    margin-top: 14px;
    border-radius: 3px;
    background: linear-gradient(90deg, {ACCENT}, {VIOLET});
  }}

  .stCaption, [data-testid="stCaptionContainer"] {{
    color: var(--muted) !important;
    font-size: 0.79rem !important;
    letter-spacing: 0.005em;
  }}

  hr {{ border-color: var(--line) !important; margin: 2.1rem 0 !important; }}

  /* Input: dark field, accent focus ring. */
  [data-testid="stTextInput"] input {{
    background: rgba(255, 255, 255, 0.035) !important;
    border: 1px solid var(--line-strong) !important;
    border-radius: 12px !important;
    color: var(--ink) !important;
    padding: 0.72rem 0.9rem !important;
    font-size: 0.97rem !important;
    transition: border-color 0.15s ease, box-shadow 0.15s ease, background 0.15s ease;
  }}
  [data-testid="stTextInput"] input::placeholder {{ color: #5D6675 !important; }}
  [data-testid="stTextInput"] input:focus {{
    outline: none !important;
    border-color: {ACCENT} !important;
    background: rgba(125, 211, 252, 0.05) !important;
    box-shadow: 0 0 0 3px rgba(125, 211, 252, 0.14) !important;
  }}

  /* Buttons. The gradient is on the primary action only; example chips stay flat
     so the hierarchy is unambiguous. */
  .stButton > button, .stFormSubmitButton > button {{
    border-radius: 11px !important;
    border: 1px solid var(--line-strong) !important;
    background: var(--surface-2) !important;
    color: var(--ink) !important;
    font-weight: 500 !important;
    font-size: 0.87rem !important;
    padding: 0.58rem 0.9rem !important;
    transition: transform 0.12s ease, border-color 0.15s ease, background 0.15s ease;
  }}
  .stButton > button:hover {{
    border-color: rgba(125, 211, 252, 0.42) !important;
    background: #1C222D !important;
    transform: translateY(-1px);
  }}
  .stFormSubmitButton > button {{
    background: linear-gradient(96deg, {ACCENT}, {VIOLET}) !important;
    border: none !important;
    color: #08121A !important;
    font-weight: 650 !important;
    letter-spacing: 0.01em;
    padding: 0.68rem 1rem !important;
  }}
  .stFormSubmitButton > button:hover {{
    transform: translateY(-1px);
    box-shadow: 0 8px 24px rgba(125, 211, 252, 0.24);
  }}

  /* The ask box gets a card of its own so the primary control reads as one
     object instead of a loose field floating between chips and results. */
  [data-testid="stForm"] {{
    background: linear-gradient(180deg, rgba(255,255,255,0.045), rgba(255,255,255,0.012));
    border: 1px solid var(--line-strong);
    border-radius: 16px;
    padding: 1.15rem 1.2rem 1.2rem;
  }}

  /* The answer panel. Border is tinted by status via a wrapper class so an
     answered response and a refusal are distinguishable at a glance. */
  .answer-card {{
    background: linear-gradient(180deg, rgba(255,255,255,0.05), rgba(255,255,255,0.015));
    border: 1px solid var(--line-strong);
    border-left: 3px solid var(--edge, {ACCENT});
    border-radius: 15px;
    padding: 1.1rem 1.25rem 0.9rem;
    margin: 0.15rem 0 0.6rem;
  }}
  .answer-card.is-answered {{ --edge: {MINT}; }}
  .answer-card.is-missing  {{ --edge: {AMBER}; }}
  .answer-card.is-refused  {{ --edge: {ROSE}; }}

  .status-pill {{
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.26rem 0.72rem 0.3rem;
    border-radius: 999px;
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 0.71rem;
    font-weight: 500;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--edge, {ACCENT});
    background: color-mix(in srgb, var(--edge, {ACCENT}) 13%, transparent);
    border: 1px solid color-mix(in srgb, var(--edge, {ACCENT}) 32%, transparent);
  }}
  .status-pill .dot {{
    width: 6px; height: 6px; border-radius: 50%;
    background: currentColor;
    box-shadow: 0 0 8px currentColor;
  }}

  .meta-row {{ margin: 0.5rem 0 0.1rem; }}
  .meta-row a {{ color: {ACCENT} !important; text-decoration: none; border-bottom: 1px solid rgba(125,211,252,0.32); }}
  .meta-row a:hover {{ border-bottom-color: {ACCENT} !important; }}

  /* Evidence cards. Monospace score chip, indented source quote. */
  [data-testid="stExpander"] {{
    background: rgba(255, 255, 255, 0.022);
    border: 1px solid var(--line) !important;
    border-radius: 13px !important;
  }}
  [data-testid="stExpander"] summary {{ font-weight: 500 !important; color: var(--muted) !important; font-size: 0.86rem !important; }}
  [data-testid="stExpander"] summary:hover {{ color: var(--ink) !important; }}
  [data-testid="stExpander"] [data-testid="stExpanderDetails"] {{ border-top: 1px solid var(--line); padding-top: 0.7rem; }}

  .hit {{
    border: 1px solid var(--line);
    border-left: 2px solid rgba(125, 211, 252, 0.42);
    border-radius: 11px;
    background: rgba(255, 255, 255, 0.022);
    padding: 0.7rem 0.85rem;
    margin-bottom: 0.55rem;
  }}
  .hit-head {{ display: flex; justify-content: space-between; align-items: baseline; gap: 0.7rem; flex-wrap: wrap; }}
  .hit-scheme {{ font-weight: 600; font-size: 0.9rem; color: {INK}; }}
  .hit-section {{ color: {MUTED}; font-size: 0.79rem; }}
  .hit-score {{
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 0.71rem;
    color: {ACCENT};
    background: rgba(125, 211, 252, 0.10);
    border: 1px solid rgba(125, 211, 252, 0.24);
    border-radius: 6px;
    padding: 0.1rem 0.4rem;
    white-space: nowrap;
  }}
  .hit-quote {{
    margin: 0.5rem 0 0;
    padding-left: 0.7rem;
    border-left: 2px solid var(--line-strong);
    color: #C3CBD9;
    font-size: 0.855rem;
    line-height: 1.55;
  }}
  .hit-url {{ margin-top: 0.42rem; font-size: 0.73rem; color: #8D97A8; word-break: break-all; }}

  /* System messages keep Streamlit's semantics but pick up the palette. */
  .stAlert {{ border-radius: 13px !important; border: 1px solid var(--line-strong) !important; background: var(--surface) !important; }}
  .stAlert p {{ color: var(--ink) !important; }}
  code {{
    font-family: 'JetBrains Mono', ui-monospace, monospace !important;
    background: #0E1219 !important;
    border: 1px solid var(--line) !important;
    border-radius: 8px !important;
    color: {MINT} !important;
  }}
  .stAlert code {{ color: inherit !important; }}

  /* Keep the scrollbar from reading as a bright band on the dark frame. */
  ::-webkit-scrollbar {{ width: 9px; height: 9px; }}
  ::-webkit-scrollbar-track {{ background: transparent; }}
  ::-webkit-scrollbar-thumb {{ background: rgba(255,255,255,0.11); border-radius: 6px; }}
  ::-webkit-scrollbar-thumb:hover {{ background: rgba(255,255,255,0.19); }}

  @media (prefers-reduced-motion: reduce) {{
    .stButton > button, .stFormSubmitButton > button {{ transition: none !important; }}
    .stButton > button:hover, .stFormSubmitButton > button:hover {{ transform: none !important; }}
  }}
</style>
"""


# Status -> (pill word, one-line explanation, card class). A refusal must read as
# a refusal, so the wording is deliberate rather than a reformat of the enum.
STATUS_PRESENTATION = {
    "ANSWERED": (
        "Answered",
        "Answered from the sources cited below.",
        "is-answered",
    ),
    "NOT_FOUND": (
        "Not found",
        "Not found in the sources, so nothing was invented to fill the gap.",
        "is-missing",
    ),
    "REFUSED_ADVICE": (
        "Refused",
        "Refused — the assistant does not give investment advice.",
        "is-refused",
    ),
    "REFUSED_RETURNS": (
        "Refused",
        "Refused — returns are not something this assistant will predict.",
        "is-refused",
    ),
    "REFUSED_PII": (
        "Refused",
        "Refused — please do not share personal or account details.",
        "is-refused",
    ),
    "OUT_OF_SCOPE": (
        "Out of scope",
        "Out of scope — this assistant covers HDFC mutual fund schemes only.",
        "is-missing",
    ),
}


@st.cache_resource(show_spinner=False)
def warm_runtime() -> int:
    """Load the embedder and open the Chroma collection exactly once per process.

    `@st.cache_resource` is the difference between a usable demo and an unusable
    one: without it, Streamlit re-runs this whole file on every keystroke, and the
    ~2.5 s model load would happen on each one, so NFR-1 could never be met. The
    result is the collection size, which also tells the UI whether the index exists.

    Only two things need caching. The embedder is a heavyweight model. The Chroma
    client is a handle to a persistent store. The generator is not cached because
    there is nothing to cache: `rag.generator` is stateless HTTP to Groq, built per
    call, so a cached handle would be a handle to a module.
    """
    embedder.warm_cache()
    return vectorstore.count()


def set_question(text: str) -> None:
    """Widget callback for an example chip.

    Set in a callback, not inline, because Streamlit forbids assigning to a widget's
    session key after the widget for that key has been created in the same run. The
    callback fires before the next run, so the text box picks up the value cleanly.
    """
    st.session_state.question = text


def status_presentation(status: str):
    """Pill word, explanation and card class for a status, with a safe fallback."""
    return STATUS_PRESENTATION.get(
        status, (status.replace("_", " ").title(), "", "is-missing")
    )


def render_status(result) -> None:
    """Open the answer panel and show the status pill. Refusals read as refusals.

    The panel is opened here and closed by `render_answer`, because Streamlit emits
    each block as its own element: a div closed within one `st.markdown` cannot
    contain the answer text emitted by the next. The wrapper carries the
    status-specific border colour, so it has to open before the pill that shows
    which status this is.
    """
    _, _, card_class = status_presentation(result.status)
    st.markdown(
        f'<div class="answer-card {card_class}">', unsafe_allow_html=True
    )
    pill, explanation, _ = status_presentation(result.status)
    st.markdown(
        f'<span class="status-pill"><span class="dot"></span>{pill}</span>',
        unsafe_allow_html=True,
    )
    if explanation:
        st.caption(explanation)


def render_answer(result) -> None:
    """Render one `Answer`: text, one citation, the date line, and the sources."""
    render_status(result)

    if result.status == ANSWERED:
        st.markdown(result.answer)
    else:
        st.info(result.answer)

    meta = []
    if result.citation_url:
        meta.append(f"**Source:** [{result.citation_url}]({result.citation_url})")
    # PRD criterion 6 fixes this wording. A refusal has no evidence and so no date;
    # saying "not applicable" is more honest than printing the label with a blank.
    if result.last_updated:
        meta.append(f"**Last updated from sources:** {result.last_updated}")
    else:
        # Also emitted as a caption, not only inside the meta row: the date line
        # for a refusal is a statement about the absence of evidence, and the
        # golden contract has it as a caption. Duplicating one short line is
        # cheaper than making the reader decode an empty label.
        st.caption("Last updated from sources: not applicable (no source retrieved)")
    st.markdown(f'<div class="meta-row">{" · ".join(meta)}</div>', unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    # FR-14. This is what makes retrieval visible instead of magical: the user can
    # see the exact passages the answer was drawn from, with their scores.
    if result.evidence:
        with st.expander(f"Show sources ({len(result.evidence)})"):
            for hit in result.evidence:
                st.markdown(
                    f'<div class="hit">'
                    f'<div class="hit-head">'
                    f'<span><span class="hit-scheme">{hit.chunk.scheme}</span>'
                    f'<span class="hit-section"> · {hit.chunk.section}</span></span>'
                    f'<span class="hit-score">{hit.score:.3f}</span>'
                    f"</div>"
                    f'<div class="hit-quote">{hit.chunk.text}</div>'
                    f'<div class="hit-url">{hit.chunk.source_url}</div>'
                    f"</div>",
                    unsafe_allow_html=True,
                )


def render_empty_index() -> None:
    """No index yet. Say how to build one instead of showing a question box that
    can only ever return NOT_FOUND."""
    st.warning("The index is empty, so there are no sources to answer from yet.")
    st.markdown("Build it with:")
    st.code("python run_ingest.py", language="bash")


def main() -> None:
    st.set_page_config(
        page_title="HDFC Mutual Funds FAQ Assistant",
        page_icon="◆",
        layout="centered",
    )
    st.markdown(THEME, unsafe_allow_html=True)

    st.title("HDFC Mutual Funds FAQ Assistant")
    st.markdown(f"*{config.disclaimer_text()}*")

    # Task 7: fail with instructions, not a traceback. Checked before anything that
    # would touch the index, so a missing index is a clear message on first paint.
    try:
        count = warm_runtime()
    except Exception as exc:  # noqa: BLE001 - any startup failure must not 500 the page
        st.error(
            "The assistant could not start. No index was found, or the embedding "
            "model is unavailable."
        )
        st.code("python run_ingest.py", language="bash")
        st.caption(f"Details: {type(exc).__name__}")
        return

    if count == 0:
        render_empty_index()
        return

    st.markdown("Ask e.g.")
    for example in config.EXAMPLE_QUESTIONS:
        st.button(example, on_click=set_question, args=(example,), use_container_width=True)

    if "question" not in st.session_state:
        st.session_state.question = ""

    with st.form("ask", clear_on_submit=False):
        question = st.text_input(
            "Ask a question about HDFC mutual fund schemes",
            key="question",
            placeholder="What is the exit load on HDFC Large Cap Fund?",
        )
        submitted = st.form_submit_button("Ask", use_container_width=True)

    if submitted and question.strip():
        with st.spinner("Searching the official sources…"):
            result = pipeline.answer(question.strip())
        st.divider()
        render_answer(result)

    st.divider()
    st.caption(config.disclaimer_text())


main()
