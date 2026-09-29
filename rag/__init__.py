"""RAG pipeline modules, one per stage.

loader -> chunker -> embedder -> vectorstore -> retriever -> generator -> verifier
"""

__all__ = ["loader"]
