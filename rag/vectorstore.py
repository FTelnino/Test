"""Stage 4: VECTOR STORE. Persistent Chroma index built from P2 chunks and P3 vectors.

Two rules make this stage trustworthy:

* **Chroma's own embedding function is never used.** Vectors are computed in
  `rag/embedder.py` and passed explicitly via `embeddings=`, and the collection is
  created with `embedding_function=None`. If Chroma silently embedded the text
  with its own model, the stored vectors and the query vectors would come from
  two different encoders and every score would be meaningless.
* **Writes are keyed by `chunk_id`.** Re-running the ingest converges on the same
  199 rows instead of appending duplicates (NFR-2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import config
from rag.chunker import Chunk

# Chroma accepts only scalar metadata values: no nested dicts, no lists. Keeping
# the field list explicit makes that a checked property instead of a convention,
# because a list slipping in raises deep inside Chroma with an unhelpful message.
METADATA_FIELDS = (
    "scheme",
    "category",
    "scope",
    "section",
    "source_url",
    "chunk_id",
    "ordinal",
    "ingested_at",
    # Not required by architecture.md §6.4, but scalar and needed downstream:
    # `kind` lets P4 drop portfolio-holdings table chunks if calibration shows they
    # crowd out prose answers, and `token_estimate` sizes the context budget.
    "kind",
    "token_estimate",
)

_client = None
_collection = None


@dataclass
class RetrievedChunk:
    """One search hit: its rank, its score, and the Chunk it reconstructs."""

    rank: int
    score: float
    chunk: Chunk


def client_settings():
    """The exact Settings every client on CHROMA_DIR must use.

    Chroma caches one system per path and refuses a second client whose settings
    differ ("An instance of Chroma already exists for ... with different
    settings"). Anything that needs its own client — a test, a maintenance
    script — must take the settings from here rather than constructing its own,
    or it will fail on the second client in the process.
    """
    from chromadb.config import Settings

    return Settings(anonymized_telemetry=False, allow_reset=True)


def get_client():
    """The process-wide PersistentClient. Survives across calls, not just runs."""
    global _client
    if _client is None:
        import chromadb

        _client = chromadb.PersistentClient(
            path=str(config.CHROMA_DIR),
            settings=client_settings(),
        )
    return _client


def get_collection():
    """The collection, created on first use with cosine distance.

    `embedding_function=None` is load-bearing: it is what guarantees this project
    never falls back to Chroma's default embedder.
    """
    global _collection
    if _collection is None:
        _collection = get_client().get_or_create_collection(
            name=config.COLLECTION_NAME,
            metadata={"hnsw:space": config.HNSW_SPACE},
            embedding_function=None,
        )
    return _collection


def chunk_metadata(chunk: Chunk, ingested_at: str) -> Dict:
    """Scalar-only metadata dict for one chunk."""
    values = {
        "scheme": chunk.scheme,
        "category": chunk.category,
        "scope": chunk.scope,
        "section": chunk.section,
        "source_url": chunk.source_url,
        "chunk_id": chunk.chunk_id,
        "ordinal": int(chunk.ordinal),
        "ingested_at": str(ingested_at),
        "kind": chunk.kind,
        "token_estimate": int(chunk.token_estimate),
    }
    extra = set(values) - set(METADATA_FIELDS)
    if extra:
        raise ValueError(f"metadata fields not allowed: {sorted(extra)}")
    for key, value in values.items():
        if not isinstance(value, (str, int, float, bool)):
            raise TypeError(
                f"metadata {key!r} is {type(value).__name__}; Chroma accepts only "
                "str/int/float/bool"
            )
    return values


def upsert(
    chunks: Sequence[Chunk],
    vectors: Sequence[Sequence[float]],
    ingested_at: str,
) -> int:
    """Write chunks and their vectors, keyed by chunk_id. Returns rows written.

    `embeddings=` is always explicit. Batches at CHROMA_BATCH_SIZE to stay well
    inside Chroma's limits.
    """
    chunks = list(chunks)
    vectors = list(vectors)
    if len(chunks) != len(vectors):
        raise ValueError(f"{len(chunks)} chunks but {len(vectors)} vectors")
    if not chunks:
        return 0

    written = 0
    for start in range(0, len(chunks), config.CHROMA_BATCH_SIZE):
        batch = chunks[start : start + config.CHROMA_BATCH_SIZE]
        batch_vectors = vectors[start : start + config.CHROMA_BATCH_SIZE]
        get_collection().upsert(
            ids=[chunk.chunk_id for chunk in batch],
            embeddings=[list(vector) for vector in batch_vectors],
            documents=[chunk.text for chunk in batch],
            metadatas=[chunk_metadata(chunk, ingested_at) for chunk in batch],
        )
        written += len(batch)
    return written


def rebuild(
    chunks: Optional[Sequence[Chunk]] = None,
    vectors: Optional[Sequence[Sequence[float]]] = None,
    ingested_at: str = "",
) -> int:
    """Delete the collection, recreate it, then upsert. Returns rows written.

    Used by the full ingest path so a changed or removed source cannot leave
    orphaned vectors behind. Called with no arguments it only wipes, which is
    what the ingest path does before writing documents one at a time so each
    chunk keeps its own source's `ingested_at`.
    """
    client = get_client()
    try:
        client.delete_collection(config.COLLECTION_NAME)
    except ValueError:
        # Chroma raises when the collection is absent, which is the normal case
        # on a first run.
        pass
    global _collection
    _collection = None
    get_collection()
    if not chunks:
        return 0
    return upsert(chunks, vectors or [], ingested_at)


def count() -> int:
    """Rows in the collection. The gate, and the UI's 'index missing' check."""
    return int(get_collection().count())


def search(
    embedding: Sequence[float],
    top_k: Optional[int] = None,
    where: Optional[Dict] = None,
) -> List[RetrievedChunk]:
    """Nearest neighbours, as RetrievedChunk, best first.

    `where` is a Chroma filter, e.g. `{"$or": [{"scheme": X}, {"scope": "general"}]}`
    — the shape P4 needs so a scheme question can still reach general regulator
    material (architecture.md §6.5).

    Scores are cosine similarities in [0, 1] for L2-normalised vectors, computed
    as `1 - distance` because the collection is configured for cosine distance.
    """
    total = count()
    if total == 0:
        return []
    limit = min(top_k or config.TOP_K, total)

    result = get_collection().query(
        query_embeddings=[list(embedding)],
        n_results=limit,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]

    hits: List[RetrievedChunk] = []
    for position, document in enumerate(documents):
        metadata = dict(metadatas[position] or {})
        distance = float(distances[position]) if position < len(distances) else 1.0
        hits.append(
            RetrievedChunk(
                rank=position + 1,
                score=round(1.0 - distance, 6),
                chunk=Chunk(
                    chunk_id=metadata.get("chunk_id", f"unknown-{position}"),
                    text=document or "",
                    scheme=metadata.get("scheme", ""),
                    category=metadata.get("category", ""),
                    scope=metadata.get("scope", "scheme"),
                    section=metadata.get("section", ""),
                    source_url=metadata.get("source_url", ""),
                    ordinal=int(metadata.get("ordinal", position)),
                    token_estimate=int(metadata.get("token_estimate", 0)),
                    kind=metadata.get("kind", "prose"),
                    strategy="",
                ),
            )
        )
    return hits
