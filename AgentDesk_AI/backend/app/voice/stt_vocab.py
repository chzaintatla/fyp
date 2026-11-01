"""The vocabulary hint sent to Whisper: the user's own app, file and contact names, so "VS Code" is not heard as "vs cold".

Whisper's prompt is short (the client caps it at 800 characters, roughly 200 tokens). Order matters: the assistant's
name and the user's corrections come first, then apps, files, folders and contacts. A hint the browser already sent
is kept and our words are appended after it.
"""
from __future__ import annotations

MAX_CHARS = 700
ASSISTANT_NAME = "AgentDesk"


def build_prompt(index, corrections: dict[str, str] | None = None, extra: str | None = None,
                 limits: dict[str, int] | None = None) -> str:
    lim = {"app": 25, "file": 20, "folder": 10, "contact": 10, "page": 5, **(limits or {})}
    words: list[str] = [ASSISTANT_NAME]
    words += list(dict.fromkeys((corrections or {}).values()))[:15]
    for kind in ("app", "file", "folder", "contact", "page"):
        words += [n for n in index.names(kind, 400)[: lim[kind]]]
    seen: set[str] = set()
    parts: list[str] = []
    budget = MAX_CHARS - len(extra or "") - 2
    used = 0
    for w in words:
        w = w.strip()
        key = w.casefold()
        if not w or key in seen or len(w) > 40:
            continue
        if used + len(w) + 2 > budget:
            break
        seen.add(key)
        parts.append(w)
        used += len(w) + 2
    hint = ", ".join(parts)
    return f"{extra.strip()} {hint}".strip() if extra else hint
