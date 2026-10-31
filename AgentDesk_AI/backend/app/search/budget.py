"""Token budget for answer prompts, counted with gpt-oss's own tokenizer (tiktoken `o200k_harmony`).

The vocabulary file is downloaded by tiktoken the first time (a public file; no user data is sent). If it cannot be loaded the
count falls back to a conservative estimate (3 characters per token) and `tokenizer_name()` says so, so evidence never claims
more than was measured.
"""
from __future__ import annotations

import threading

PROMPT_TOKEN_BUDGET = 1500
_lock = threading.Lock()
_encoding = None
_failed = False


def _load():
    global _encoding, _failed
    with _lock:
        if _encoding is None and not _failed:
            try:
                import tiktoken
                _encoding = tiktoken.get_encoding("o200k_harmony")
            except Exception:  # noqa: BLE001 - offline first run, or the library is missing
                _failed = True
        return _encoding


def warm() -> None:
    """Load the tokenizer in the background at startup so the first question does not wait for the download."""
    _load()


def tokenizer_name() -> str:
    return "o200k_harmony" if _load() is not None else "estimate (3 chars/token)"


def count(text: str) -> int:
    enc = _load()
    if enc is None:
        return -(-len(text) // 3)
    return len(enc.encode(text, disallowed_special=()))


def truncate(text: str, max_tokens: int) -> str:
    """The first `max_tokens` tokens of text (cut on a token boundary, then trimmed back to a whole word)."""
    if max_tokens <= 0:
        return ""
    enc = _load()
    if enc is None:
        return text[: max_tokens * 3]
    ids = enc.encode(text, disallowed_special=())
    if len(ids) <= max_tokens:
        return text
    cut = enc.decode(ids[:max_tokens])
    return cut.rsplit(" ", 1)[0] if " " in cut else cut


def fit_documents(fixed_tokens: int, texts: list[str], budget: int = PROMPT_TOKEN_BUDGET, floor: int = 60) -> list[str]:
    """Shorten the documents so that `fixed_tokens` (instructions + question) plus all documents fit in `budget`.
    Each document gets an equal share, and short documents give their unused share to the longer ones."""
    room = max(0, budget - fixed_tokens)
    sizes = [count(t) for t in texts]
    shares = [room // max(1, len(texts))] * len(texts)
    for _ in range(3):  # hand unused room from short documents to long ones
        spare = sum(max(0, s - n) for s, n in zip(shares, sizes))
        hungry = [i for i, (s, n) in enumerate(zip(shares, sizes)) if n > s]
        if not spare or not hungry:
            break
        shares = [min(n, s) if n <= s else s + spare // len(hungry) for s, n in zip(shares, sizes)]
    return [truncate(t, max(floor, s)) if room else "" for t, s in zip(texts, shares)]
