"""Dump the chunks and their stored vectors to a readable text file.

The index is the one artefact in this project you cannot read by opening it: a
Chroma collection is binary, and a chunk is only visible as a JSON array of 407
objects. This writes both in a form a human can actually check, which is the
point — P2 and P3 both made claims about chunk quality and vector dimensions
that were only ever verified by a script printing PASS.

    python scripts/inspect_index.py

Writes reports/index_inspection.txt. Useful flags:

    --all-embeddings   print all 384 dimensions for every chunk, not a sample.
                       The file grows from ~120 KB to ~1.1 MB.
    --limit N          only the first N chunks (for a quick look)
    --out PATH         write somewhere else
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import hashlib
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from rag import vectorstore

RULE = "=" * 78
THIN = "-" * 78
PREVIEW_DIMS = 8


def wrap(text: str, width: int = 76, indent: str = "    ") -> str:
    """Hard-wrap chunk text so column alignment survives a monospace viewer."""
    import textwrap

    return "\n".join(textwrap.wrap(text, width=width, initial_indent=indent, subsequent_indent=indent))


def fingerprint(vector: list) -> str:
    """A short stable id for a vector, so two runs can be compared by eye."""
    payload = ",".join(f"{value:.6f}" for value in vector).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:12]


def norm(vector: list) -> float:
    return math.sqrt(sum(value * value for value in vector))


def section_summary(out, collection, embedder) -> None:
    out.write(f"{RULE}\nCOLLECTION\n{RULE}\n")
    out.write(f"  name            {config.COLLECTION_NAME}\n")
    out.write(f"  path            {config.CHROMA_DIR}\n")
    out.write(f"  rows            {collection.count()}\n")
    out.write(f"  hnsw:space      {collection.metadata.get('hnsw:space')}\n")
    ef = getattr(collection, "_embedding_function", None)
    out.write(f"  embedding_fn    {ef}  (None = Chroma embedded nothing for us)\n")
    out.write(f"  strategy        {config.CHUNK_STRATEGY}\n")
    out.write(f"  chunk_size      {config.CHUNK_SIZE}  overlap {config.CHUNK_OVERLAP}\n")


def section_embedder(out, embedder) -> None:
    out.write(f"\n{RULE}\nEMBEDDER\n{RULE}\n")
    out.write(f"  model           {embedder.model_id()}\n")
    out.write(f"  dimensions      {embedder.EXPECTED_DIM}\n")
    out.write(f"  cache dir       {config.MODEL_CACHE_DIR}\n")
    out.write(f"  load time       {embedder.load_seconds():.2f}s\n")
    out.write("  normalised      yes (L2 norm 1.0, so cosine == dot product)\n")


def section_chunks(out, records, limit) -> int:
    out.write(f"\n{RULE}\nCHUNKS  (full text, all {len(records)} unless --limit)\n{RULE}\n")
    for record in records[:limit]:
        meta = record["metadata"]
        out.write(f"\n{THIN}\n")
        out.write(f"#{meta['ordinal']}  {meta['chunk_id']}\n")
        out.write(f"{THIN}\n")
        out.write(
            f"  scheme    {meta['scheme']}\n"
            f"  category  {meta['category']}\n"
            f"  scope     {meta['scope']}\n"
            f"  section   {meta['section']}\n"
            f"  kind      {meta['kind']}\n"
            f"  chars     {len(record['document'])}\n"
            f"  tokens    {meta['token_estimate']} (approx)\n"
            f"  url       {meta['source_url']}\n"
            f"  fetched   {meta['ingested_at']}\n\n"
        )
        out.write(wrap(record["document"]) + "\n")
    return len(records[:limit])


def section_embeddings(out, records, limit, full: bool) -> None:
    out.write(f"\n{RULE}\nEMBEDDINGS\n{RULE}\n")
    if full:
        out.write(
            "All 384 dimensions for every chunk. Each block is 8 per line, which is\n"
            "48 lines per vector.\n"
        )
    else:
        out.write(
            f"First {PREVIEW_DIMS} dimensions for every chunk, plus the L2 norm and a\n"
            "fingerprint. Run with --all-embeddings for all 384 dimensions of each.\n"
        )
    out.write(
        "\nA fingerprint is the first 12 hex chars of the sha1 of the vector at 6dp.\n"
        "Identical fingerprints across runs mean the model is deterministic.\n"
    )

    for index, record in enumerate(records[:limit]):
        vector = record["embedding"]
        out.write(f"\n#{index}  {record['metadata']['chunk_id']}\n")
        if full:
            for start in range(0, len(vector), 8):
                row = vector[start : start + 8]
                out.write("    " + " ".join(f"{v:+.4f}" for v in row) + "\n")
        else:
            out.write("    " + " ".join(f"{v:+.4f}" for v in vector[:PREVIEW_DIMS]) + "  ...\n")
        out.write(
            f"    dim={len(vector)}  L2={norm(vector):.6f}  sha1={fingerprint(vector)}\n"
        )


def section_neighbours(out, records, limit) -> None:
    """Each chunk's nearest other chunk, so a badly-split chunk is visible."""
    out.write(f"\n{RULE}\nNEAREST NEIGHBOUR PER CHUNK  (cosine, 1.0 = identical)\n{RULE}\n")
    out.write(
        "A high score here means two near-duplicate chunks exist, which wastes\n"
        "retrieval slots. P2 left 7 orphan row fragments; this is how to spot them.\n"
    )
    vectors = [record["embedding"] for record in records]
    for index, record in enumerate(records[:limit]):
        vector = vectors[index]
        best_score, best_index = -2.0, -1
        for other_index, other in enumerate(vectors):
            if other_index == index:
                continue
            score = sum(a * b for a, b in zip(vector, other))
            if score > best_score:
                best_score, best_index = score, other_index
        out.write(
            f"  {best_score:+.4f}  {record['metadata']['chunk_id']}\n"
            f"          -> {records[best_index]['metadata']['chunk_id']}"
            f"  [{records[best_index]['metadata']['section']}]\n"
        )


def scheme_filter(question: str, schemes: list) -> dict:
    """The §6.5 filter, applied whenever the question names a scheme.

    Without it, "exit load on HDFC Large Cap" ranks Small Cap and Equity Fund
    chunks above Large Cap's own, because every scheme has an exit load.
    """
    named = [scheme for scheme in schemes if scheme.lower() in question.lower()]
    if not named:
        return {}
    return {"$or": [{"scheme": scheme} for scheme in named] + [{"scope": "general"}]}


def sample_queries(schemes) -> list:
    """A spread of retrieval probes, built from the corpus.

    This used to be `config.EXAMPLE_QUESTIONS`, the three chips the UI offered.
    Those are gone, and hard-coding probes here would drift the same way they did:
    naming 2 of 15 schemes while the collection held 407 chunks. Deriving one
    question per scheme means the printed sample always exercises what is actually
    indexed, and a scheme that cannot be found shows up here rather than nowhere.

    Callers pass scheme-scope chunks only; the AMFI regulator pages are not funds,
    and "What is the exit load on AMFI Investor Awareness Programme?" is not a
    question worth printing.
    """
    return [f"What is the exit load on {scheme}?" for scheme in schemes]


def section_search(out, embedder, records, queries) -> None:
    out.write(f"\n{RULE}\nSAMPLE RETRIEVAL  (cosine score, higher is closer)\n{RULE}\n")
    schemes = sorted({record["metadata"]["scheme"] for record in records})
    for question in queries:
        vector = embedder.embed_query(question)
        out.write(f"\nQ: {question}\n")
        out.write(f"   query vector: dim={len(vector)} L2={norm(vector):.6f}\n")
        where = scheme_filter(question, schemes)
        if where:
            out.write(f"   filter: {json.dumps(where)}\n")
        else:
            out.write("   filter: none (no scheme named in the question)\n")
        for hit in vectorstore.search(vector, top_k=3, where=where or None):
            out.write(
                f"   #{hit.rank} {hit.score:.4f}  [{hit.chunk.kind:5}] "
                f"{hit.chunk.section:26} {hit.chunk.scheme[:30]}\n"
            )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=config.REPORTS_DIR / "index_inspection.txt")
    parser.add_argument("--limit", type=int, default=0, help="0 = every chunk")
    parser.add_argument("--all-embeddings", action="store_true", help="print all 384 dims of each")
    parser.add_argument("--no-search", action="store_true", help="skip the retrieval demo")
    args = parser.parse_args(argv)

    from rag import embedder

    collection = vectorstore.get_collection()
    if collection.count() == 0:
        print("collection is empty. Run: python run_ingest.py --index")
        return 1

    stored = collection.get(include=["documents", "metadatas", "embeddings"])
    records = [
        {"metadata": meta, "document": doc, "embedding": emb}
        for doc, meta, emb in zip(
            stored["documents"], stored["metadatas"], stored["embeddings"]
        )
    ]
    # Chroma returns insertion order, which is not ordinal order across documents.
    records.sort(key=lambda record: (record["metadata"]["scheme"], record["metadata"]["ordinal"]))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    limit = args.limit or len(records)
    embedder.get_model()
    with args.out.open("w", encoding="utf-8") as out:
        out.write(f"{RULE}\nHDFC MUTUAL FUND RAG — INDEX INSPECTION\n{RULE}\n")
        out.write(
            f"generated  {_datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n"
            f"chunks     {len(records)}\n"
            f"showing    {limit}\n"
        )
        section_summary(out, collection, embedder)
        section_embedder(out, embedder)
        section_chunks(out, records, limit)
        section_embeddings(out, records, limit, args.all_embeddings)
        section_neighbours(out, records, limit)
        if not args.no_search:
            scheme_names = sorted(
                {
                    record["metadata"]["scheme"]
                    for record in records
                    if record["metadata"].get("scope") != "general"
                }
            )
            section_search(out, embedder, records, sample_queries(scheme_names))

    size_kb = args.out.stat().st_size / 1024
    print(f"wrote {args.out}  ({size_kb:,.0f} KB, {limit} chunks)")
    if limit < len(records):
        print(f"  note: --limit {limit} of {len(records)}; the collection is unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
