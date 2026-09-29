"""Pre-download the models so the demo runs offline (NFR-3).

Run this now, not on demo day. It is the difference between a 3-second model load
and a failed demo on a machine with no network.

    python scripts/warm_cache.py

Warms two things:
  1. the sentence-transformer embedder, into data/models/
  2. the Ollama LLM named in config, via `ollama pull`

Exits 0 only if both are ready. `--require-llm` is not needed: the embedder is
what P3's gate needs, so an unavailable LLM is reported as a failure of this
script but never blocks indexing.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config


def warm_embedder() -> bool:
    try:
        from rag import embedder

        message = embedder.warm_cache()
    except Exception as error:  # noqa: BLE001 - report whatever went wrong
        print(f"[FAIL] embedder: {type(error).__name__}: {error}")
        return False
    print(f"[ OK ] embedder: {message}")
    return True


def warm_llm() -> bool:
    if config.LLM_BACKEND != "ollama":
        print(f"[SKIP] llm: backend {config.LLM_BACKEND!r} needs no pull step")
        return True

    if shutil.which("ollama") is None:
        print(
            f"[FAIL] llm: the `ollama` binary is not on PATH, so "
            f"{config.LLM_MODEL!r} cannot be pulled. Install Ollama "
            "(https://ollama.com) and re-run. P3 indexing does not need the LLM; "
            "P5 generation does."
        )
        return False

    try:
        result = subprocess.run(
            ["ollama", "pull", config.LLM_MODEL],
            capture_output=True,
            text=True,
            timeout=1800,
        )
    except (subprocess.TimeoutExpired, OSError) as error:
        print(f"[FAIL] llm: could not run `ollama pull`: {error}")
        return False

    if result.returncode != 0:
        print(f"[FAIL] llm: `ollama pull {config.LLM_MODEL}` exited {result.returncode}")
        print((result.stderr or result.stdout).strip()[:500])
        return False

    print(f"[ OK ] llm: {config.LLM_MODEL} available via {config.LLM_BASE_URL}")
    return True


def main() -> int:
    print(f"cache dir: {config.MODEL_CACHE_DIR}")
    print(f"model:     {config.EMBED_MODEL}\n")

    embedder_ok = warm_embedder()
    print()
    llm_ok = warm_llm()

    print()
    if embedder_ok and llm_ok:
        print("cache warm; this machine can now run offline.")
        return 0
    print("cache incomplete:")
    if not embedder_ok:
        print("  - the embedder did not load, so P3 indexing cannot run")
    if not llm_ok:
        print("  - the LLM is missing, so P5 generation cannot run")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
