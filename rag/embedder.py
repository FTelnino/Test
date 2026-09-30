"""Stage 3: EMBEDDING. One model, loaded once, used for both sides of retrieval.

Every vector in `data/chroma/` and every query vector must come from the same
encoder, or cosine similarity is comparing two different coordinate systems and
retrieval degrades silently — no error, just worse answers. So this module keeps
the model in a private singleton and exposes no way to pass a different one per
call; the only knob is batch size.

Vectors are L2-normalised (`normalize_embeddings=True`), which means cosine
similarity reduces to a dot product and scores stay comparable across runs.
"""

from __future__ import annotations

import os
import time
from typing import List, Optional, Sequence

import config

# Redirect every Hugging Face cache into the project so the demo runs offline
# after the first successful build (NFR-3). This has to happen before
# transformers or huggingface_hub are imported, which is why the import below is
# deferred into get_model() rather than sitting at module top.
os.environ.setdefault("HF_HOME", str(config.MODEL_CACHE_DIR))
os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(config.MODEL_CACHE_DIR))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
# The Rust tokenizer in `transformers` forks a thread pool sized to the core count,
# and every thread carries its own buffers. On a 512 MB deploy box that is memory
# spent on nothing, since EMBED_NUM_THREADS already caps the real work at one
# thread. Must be set before transformers is imported, hence here rather than in
# _configure_threads().
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

# all-MiniLM-L6-v2 is fixed at 384 dimensions. Checked rather than assumed: a
# model swap that changed the width would leave the existing index unreadable,
# and that should fail here, loudly, instead of at query time.
EXPECTED_DIM = 384

_model = None
_load_seconds = 0.0


def _configure_threads() -> None:
    """Keep torch single-threaded on small deploy targets.

    torch defaults to one intra-op thread per core. A Render free web service has
    0.5 CPU, so those threads do not add throughput -- they oversubscribe a core
    that is already time-sliced, and each one reserves its own arena, which is
    memory this project does not have. `EMBED_NUM_THREADS` overrides the guess for
    a box that really does have cores to spare.
    """
    raw = os.environ.get("EMBED_NUM_THREADS")
    try:
        threads = int(raw) if raw and raw.strip() else 1
    except ValueError:
        threads = 1
    threads = max(1, threads)
    try:
        import torch
    except ImportError:
        return
    torch.set_num_threads(threads)
    try:
        torch.set_num_interop_threads(threads)
    except RuntimeError:
        # Interop can only be set before the first parallel region, so this is
        # a no-op once anything has run. Not worth failing a deploy over.
        pass


def get_model():
    """The process-wide SentenceTransformer, loading it on first use."""
    global _model, _load_seconds
    if _model is None:
        _configure_threads()
        from sentence_transformers import SentenceTransformer

        started = time.time()
        _model = SentenceTransformer(config.EMBED_MODEL)
        _load_seconds = time.time() - started
        _verify_model(_model)
        print(
            f"[embedder] loaded {config.EMBED_MODEL} in {_load_seconds:.1f}s "
            f"({EXPECTED_DIM}d) from cache {config.MODEL_CACHE_DIR}"
        )
    return _model


def _verify_model(model) -> None:
    """Refuse to run if the encoder is not the one the index was built with."""
    width = model.get_sentence_embedding_dimension()
    if width != EXPECTED_DIM:
        raise RuntimeError(
            f"{config.EMBED_MODEL} produced {width}-dim vectors, expected "
            f"{EXPECTED_DIM}. The existing index in {config.CHROMA_DIR} was built "
            "with a different encoder; rebuild it or pin the old model."
        )


def model_id() -> str:
    return config.EMBED_MODEL


def load_seconds() -> float:
    """How long the singleton took to load, for the ingest report."""
    return _load_seconds


def embed_documents(
    texts: Sequence[str], batch_size: Optional[int] = None
) -> List[List[float]]:
    """Embed chunk texts. Returns one list[float] per input, in input order."""
    items = list(texts)
    if not items:
        return []
    vectors = get_model().encode(
        items,
        batch_size=batch_size or config.BATCH_SIZE,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return [vector.tolist() for vector in vectors]


def embed_query(text: str) -> List[float]:
    """Embed one query string into a single 384-dim vector.

    The same model and the same normalisation as embed_documents, deliberately:
    a query embedded any other way would be silently unsearchable.
    """
    if not text or not text.strip():
        raise ValueError("embed_query() needs a non-empty query")
    return embed_documents([text])[0]


def warm_cache() -> str:
    """Force the model download now so the demo machine works offline later."""
    get_model()
    probe = embed_query("warm up the embedding cache")
    if len(probe) != EXPECTED_DIM:
        raise RuntimeError(f"expected {EXPECTED_DIM}-dim probe, got {len(probe)}")
    return f"{config.EMBED_MODEL} cached at {config.MODEL_CACHE_DIR}"
