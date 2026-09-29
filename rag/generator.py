"""Stage 6 generator: the only module that talks to an LLM (architecture.md §6.6).

Everything else in this project is deterministic and offline. This is the one place
a network call happens, and it is deliberately thin: it sends a prompt, returns text,
and refuses to paper over a failure.

Three rules, all of them load-bearing:

1. **The API key is never logged.** It is read from the environment at call time,
   so it does not exist as a module attribute for anything to print by accident,
   and no exception message here includes headers.
2. **A backend error raises `LLMUnavailable`.** It does not fall back to a canned
   answer. A fabricated "official-looking" answer is a worse failure than an error
   the user can see, and the spec is explicit about this.
3. **Only `content` is read, never `reasoning`.** gpt-oss models return a separate
   reasoning trace; it is scratchpad, and returning it would put "Need to answer
   using only context" on screen. `temperature=0` and no retries that change the
   answer, so the same question twice gives the same text.
"""

from __future__ import annotations

import os
import sys
import time
import unicodedata
from pathlib import Path
from typing import Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config


class LLMUnavailable(RuntimeError):
    """The backend could not be reached, or refused the request.

    Distinct from a bad prompt. P7 catches this and reports it, rather than
    rendering a plausible answer that no source supports.
    """


#: Unicode spaces models emit that break naive regexes. gpt-oss-120b returned
#: U+202F inside "1 year"; the verifier's patterns are written against ASCII
#: whitespace, so these are folded to a plain space before anything reads the text.
_UNICODE_SPACES = {
    "\u00a0": " ",  # no-break space
    "\u202f": " ",  # narrow no-break space
    "\u2007": " ",  # figure space
    "\u2009": " ",  # thin space
}


def _normalise(text: str) -> str:
    for char, replacement in _UNICODE_SPACES.items():
        text = text.replace(char, replacement)
    return unicodedata.normalize("NFKC", text).strip()


class Completion:
    """A completion plus the numbers the pipeline logs.

    `generate()` returns plain text because that is what most callers want, but the
    observability spec wants `tokens_out` and `ms` on the generate log line. Rather
    than change the simple contract or stash usage in module state, the detailed
    call is a separate function and `generate` delegates to it.
    """

    __slots__ = ("text", "ms", "tokens_out", "model")

    def __init__(self, text: str, ms: float, tokens_out: int, model: str):
        self.text = text
        self.ms = ms
        self.tokens_out = tokens_out
        self.model = model

    def __repr__(self) -> str:
        return (
            f"Completion(tokens_out={self.tokens_out}, ms={self.ms:.0f}, "
            f"model={self.model!r}, text={self.text[:40]!r})"
        )


def _post(payload: dict, base_url: str, headers: dict) -> dict:
    """POST once, retrying only on throttling and 5xx.

    Retrying is safe here precisely because it cannot change the answer: the same
    prompt is resent with the same `temperature=0`, so a retry returns the same
    text a moment later rather than a second guess. The spec forbids retries that
    change the answer, not retries that wait out a rate limit.

    Bounded, because a gate script must not hang: four attempts, honouring
    `Retry-After` when the backend sends it.
    """
    import time

    import requests

    url = f"{base_url.rstrip('/')}/chat/completions"
    last_status = None

    for attempt in range(config.LLM_MAX_ATTEMPTS):
        try:
            response = requests.post(
                url, headers=headers, json=payload, timeout=config.LLM_TIMEOUT
            )
        except requests.RequestException as exc:
            # The URL is safe to show; the headers are not, and they are not in exc.
            raise LLMUnavailable(f"{base_url} unreachable: {type(exc).__name__}") from None

        last_status = response.status_code
        if last_status == 200:
            break

        throttled = last_status == 429
        server_error = 500 <= last_status < 600
        if not (throttled or server_error) or attempt == config.LLM_MAX_ATTEMPTS - 1:
            raise LLMUnavailable(
                f"HTTP {last_status} from {base_url}: {response.text[:200]}"
            )

        # Never logged, never included: only the status and the wait.
        wait = response.headers.get("Retry-After")
        try:
            delay = float(wait) if wait else config.LLM_BACKOFF_SECONDS * (2 ** attempt)
        except ValueError:
            delay = config.LLM_BACKOFF_SECONDS * (2 ** attempt)
        time.sleep(min(delay, 60.0))

    try:
        return response.json()
    except ValueError:
        raise LLMUnavailable("backend returned a non-JSON body") from None


def _generate_groq(prompt: str, system: Optional[str]) -> Tuple[str, int]:
    key = os.environ.get(config.GROQ_KEY_ENV, "").strip()
    if not key:
        raise LLMUnavailable(
            f"{config.GROQ_KEY_ENV} is not set. Put it in .env; never in code."
        )

    payload = {
        "model": config.GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system or ""},
            {"role": "user", "content": prompt},
        ],
        "temperature": config.LLM_TEMPERATURE,
        "max_tokens": config.LLM_MAX_TOKENS,
        # Without this the model spends the whole budget reasoning and the answer
        # arrives truncated. Measured: 68 tokens with it, 160 without.
        "reasoning_effort": config.GROQ_REASONING_EFFORT,
    }
    data = _post(payload, config.GROQ_BASE_URL, {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    })

    choices = data.get("choices") or []
    if not choices:
        raise LLMUnavailable("backend returned no choices")

    # `content` only. `reasoning` is the model's scratchpad and is never returned.
    content = choices[0].get("message", {}).get("content")
    if content is None:
        raise LLMUnavailable("backend returned no message content")
    tokens = (data.get("usage") or {}).get("completion_tokens") or 0
    return _normalise(content), int(tokens)


def _generate_ollama(prompt: str, system: Optional[str]) -> Tuple[str, int]:
    full = f"{system}\n\n{prompt}" if system else prompt
    payload = {
        "model": config.OLLAMA_MODEL,
        "prompt": full,
        "stream": False,
        "options": {
            "temperature": config.LLM_TEMPERATURE,
            "num_predict": config.LLM_MAX_TOKENS,
        },
    }
    data = _post(payload, config.OLLAMA_BASE_URL, {"Content-Type": "application/json"})
    response = data.get("response")
    if response is None:
        raise LLMUnavailable("ollama returned no response field")
    return _normalise(response), int(data.get("eval_count") or 0)


def generate_ex(prompt: str, system: Optional[str] = None) -> Completion:
    """One completion, with timing and token usage. Raises `LLMUnavailable`.

    This is the form the pipeline logs; `generate` is the plain-text shorthand.
    """
    started = time.time()
    if config.LLM_BACKEND == "groq":
        text, tokens = _generate_groq(prompt, system)
        model = config.GROQ_MODEL
    elif config.LLM_BACKEND == "ollama":
        text, tokens = _generate_ollama(prompt, system)
        model = config.OLLAMA_MODEL
    else:
        raise LLMUnavailable(f"unknown LLM_BACKEND {config.LLM_BACKEND!r}")
    return Completion(
        text=text,
        ms=(time.time() - started) * 1000.0,
        tokens_out=tokens,
        model=model,
    )


def generate(prompt: str, system: Optional[str] = None) -> str:
    """One completion. Returns text; raises `LLMUnavailable` on any backend failure."""
    return generate_ex(prompt, system).text


def backend() -> str:
    """A short description of the live backend, for logs and the UI footer."""
    if config.LLM_BACKEND == "groq":
        return f"groq:{config.GROQ_MODEL}"
    if config.LLM_BACKEND == "ollama":
        return f"ollama:{config.OLLAMA_MODEL}"
    return config.LLM_BACKEND
