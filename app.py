#!/usr/bin/env python3
"""Single-screen Streamlit UI (PRD §7, architecture.md FR-11 + FR-14).

The screen is deliberately thin. Every decision — guarding, retrieval, generation,
verification — already happened in `rag.pipeline.answer()`, and this file only
renders the `Answer` it returns. That is what keeps the CLI, the eval harness, and
the UI telling the same story: they call the same one entry point and differ only
in how they show the result.

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


def render_status(result) -> None:
    """A small, honest status line. Refusals should read as refusals."""
    labels = {
        "ANSWERED": "Answered",
        "NOT_FOUND": "Not found in sources",
        "REFUSED_ADVICE": "Refused — advice",
        "REFUSED_RETURNS": "Refused — returns",
        "REFUSED_PII": "Refused — personal data",
        "OUT_OF_SCOPE": "Out of scope",
    }
    st.caption(labels.get(result.status, result.status))


def render_answer(result) -> None:
    """Render one `Answer`: text, one citation, the date line, and the sources."""
    render_status(result)

    if result.status == ANSWERED:
        st.markdown(result.answer)
    else:
        st.info(result.answer)

    if result.citation_url:
        st.markdown(f"**Source:** [{result.citation_url}]({result.citation_url})")

    # PRD criterion 6 fixes this wording. A refusal has no evidence and so no date;
    # saying "not applicable" is more honest than printing the label with a blank.
    if result.last_updated:
        st.markdown(f"**Last updated from sources:** {result.last_updated}")
    else:
        st.caption("Last updated from sources: not applicable (no source retrieved)")

    # FR-14. This is what makes retrieval visible instead of magical: the user can
    # see the exact passages the answer was drawn from, with their scores.
    if result.evidence:
        with st.expander(f"Show sources ({len(result.evidence)})"):
            for hit in result.evidence:
                st.markdown(
                    f"**{hit.chunk.scheme}** · {hit.chunk.section} · "
                    f"`score {hit.score:.3f}`"
                )
                st.caption(hit.chunk.source_url)
                st.markdown(f"> {hit.chunk.text}")


def main() -> None:
    st.set_page_config(
        page_title="HDFC Mutual Funds FAQ Assistant",
        page_icon="📄",
        layout="centered",
    )

    st.title("HDFC Mutual Funds FAQ Assistant")
    st.markdown(f"**{config.disclaimer_text()}**")

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
        st.warning("The index is empty, so there are no sources to answer from yet.")
        st.markdown("Build it with:")
        st.code("python run_ingest.py", language="bash")
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
