"""Ingest entrypoint.

    python run_ingest.py --fetch-only    # stage 1, loading only
    python run_ingest.py --chunk         # stage 2, chunking only
    python run_ingest.py --index         # stages 3/4, embed and store

Exits non-zero when any source fails, so a broken fetch cannot masquerade as a
successful build.
"""

from __future__ import annotations

import argparse
import time

import config
from rag import chunker, loader
from sources import load_sources


def run_chunking() -> int:
    """Split the P1 artefacts and persist every strategy's output.

    All three strategies are written, not just the configured one, because the
    comparison in reports/chunking_decision.md is the evidence for the choice and
    has to be re-runnable to check.
    """
    documents = chunker.load_documents()
    total_chars = sum(len(document.text) for document in documents)
    print(
        f"stage 2: chunking {len(documents)} documents "
        f"({total_chars} chars), size={config.CHUNK_SIZE} "
        f"overlap={config.CHUNK_OVERLAP} min={config.MIN_CHUNK_CHARS}"
    )
    print(f"configured strategy: {config.CHUNK_STRATEGY}\n")

    failures = 0
    for name in chunker.STRATEGIES:
        chunks = chunker.split_all(documents, name)
        stats = chunker.strategy_stats(chunks)
        problems = chunker.validate_chunks(chunks)
        path = chunker.persist_chunks(
            chunks, config.RAW_DIR / f"chunks_{name}.json"
        )
        marker = "OK " if not problems else "FAIL"
        print(
            f"  [{marker}] {name:12} {stats['chunks']:4} chunks  "
            f"mean={stats['mean_chars']:6}  median={stats['median_chars']:6}  "
            f"max={stats['max_chars']:4}  table={stats.get('table_chunks', 0):3}  "
            f"sections={stats['distinct_sections']:3}  violations={len(problems)}"
        )
        print(f"         -> {path}")
        for problem in problems[:5]:
            print(f"         ! {problem}")
        if problems:
            failures += 1

    if failures:
        print(f"\nFAILED: {failures} strategy(ies) produced invalid chunks.")
        return 1

    print("\nchunks written. Evidence: reports/chunking_decision.md")
    return 0


def run_indexing() -> int:
    """Embed the chosen strategy's chunks and store them in Chroma.

    Rebuilds the collection first so a changed or removed source cannot leave
    orphaned vectors behind, then writes one document at a time so every chunk
    keeps its own source's `ingested_at` rather than one blanket timestamp.
    """
    from rag import embedder, vectorstore

    documents = chunker.load_documents()
    strategy = chunker.get_strategy()
    print(
        f"stage 3/4: indexing {len(documents)} documents with strategy "
        f"{strategy.name!r}, model {config.EMBED_MODEL}"
    )

    # Chunk and validate everything up front, so a chunking failure aborts before
    # the index is wiped. Rebuilding first with nothing to write would leave it empty.
    per_document = [(document, strategy.split(document)) for document in documents]
    # An empty document is invisible to validate_chunks, which only ever sees a flat
    # list; check it here or one dead source would silently index as zero rows.
    empty = [
        document.source.scheme for document, chunks in per_document if not chunks
    ]
    if empty:
        print(f"  ! no chunks produced for: {', '.join(empty)}")
    all_chunks = [chunk for _, chunks in per_document for chunk in chunks]
    problems = chunker.validate_chunks(all_chunks)
    for problem in problems[:5]:
        print(f"  ! {problem}")
    if empty or problems:
        print(f"\nFAILED: {len(empty) + len(problems)} problem(s); index left untouched.")
        return 1
    print(f"  {len(all_chunks)} chunks, {len(problems)} invariant violations")
    print(f"  embedder: {embedder.model_id()} ({embedder.EXPECTED_DIM}d, batch={config.BATCH_SIZE})\n")

    started = time.time()
    embedder.get_model()
    load_time = time.time() - started
    print(f"  model ready in {load_time:.1f}s\n")

    vectorstore.rebuild()
    print(f"  collection {config.COLLECTION_NAME!r} reset at {config.CHROMA_DIR}")

    started = time.time()
    total_vectors = 0
    for document, chunks in per_document:
        vectors = embedder.embed_documents([chunk.text for chunk in chunks])
        written = vectorstore.upsert(
            chunks, vectors, ingested_at=document.fetched_at
        )
        total_vectors += written
        print(
            f"  {document.source.scheme[:34]:34} {written:4} chunks  "
            f"scope={document.source.scope:7} fetched={document.fetched_at}"
        )
    elapsed = time.time() - started

    count = vectorstore.count()
    print(
        f"\nindexed {total_vectors} vectors in {elapsed:.1f}s "
        f"(model load {load_time:.1f}s)"
    )
    print(f"collection count: {count}")
    print(f"persisted to: {config.CHROMA_DIR}")

    if count != total_vectors:
        print(
            f"\nFAILED: wrote {total_vectors} vectors but the collection holds "
            f"{count}. The index is not trustworthy; rebuild it."
        )
        return 1
    if count <= 0:
        print("\nFAILED: the collection is empty.")
        return 1
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the RAG corpus.")
    parser.add_argument(
        "--fetch-only",
        action="store_true",
        help="stage 1 only: fetch, extract and clean the source pages",
    )
    parser.add_argument(
        "--chunk",
        action="store_true",
        help="stage 2 only: split the saved documents into chunks",
    )
    parser.add_argument(
        "--index",
        action="store_true",
        help="stage 3/4: embed the chunked corpus and store it in Chroma",
    )
    args = parser.parse_args(argv)

    config.ensure_dirs()

    if args.index:
        return run_indexing()

    if args.chunk:
        return run_chunking()

    if not args.fetch_only:
        parser.print_help()
        return 1

    sources = load_sources()
    print(f"stage 1: loading {len(sources)} sources")
    print(f"extractor: {config.EXTRACTOR} primary, beautifulsoup4 fallback")
    print(f"min text chars: {config.MIN_TEXT_CHARS}\n")

    started = time.time()
    documents, report = loader.fetch_all(sources)
    loader.save_raw(documents, report)
    elapsed = time.time() - started

    print(loader.format_report(report))
    print(f"raw text written to {config.RAW_DIR}")
    print(f"elapsed: {elapsed:.1f}s")

    failed = [entry for entry in report if entry["status"] != "ok"]
    if failed:
        print(f"\nFAILED: {len(failed)} source(s) did not ingest.")
        print("A partial corpus is not a corpus. Fix the source or exclude it")
        print("deliberately, then re-run. See reports/ingest_decisions.md.")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
