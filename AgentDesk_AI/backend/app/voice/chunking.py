"""Split a reply into sentences of at most `limit` characters (Orpheus accepts 200 per request)."""
from __future__ import annotations

import re

ORPHEUS_LIMIT = 200
_SENTENCE_END = re.compile(r"(?<=[.!?؟۔।])\s+")
_SOFT_BREAK = re.compile(r"(?<=[,;:،])\s+")


def split_for_speech(text: str, limit: int = ORPHEUS_LIMIT) -> list[str]:
    """Sentences in order, each <= limit chars. Long sentences break at commas, then at spaces."""
    chunks: list[str] = []
    for sentence in _SENTENCE_END.split((text or "").strip()):
        sentence = sentence.strip()
        if not sentence:
            continue
        chunks.extend(_fit(sentence, limit))
    return chunks


def _fit(sentence: str, limit: int) -> list[str]:
    if len(sentence) <= limit:
        return [sentence]
    out: list[str] = []
    current = ""
    for part in _SOFT_BREAK.split(sentence):
        for piece in _hard_wrap(part, limit):
            if current and len(current) + 1 + len(piece) > limit:
                out.append(current)
                current = piece
            else:
                current = f"{current} {piece}".strip()
    if current:
        out.append(current)
    return out


def _hard_wrap(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    out, current = [], ""
    for word in text.split():
        while len(word) > limit:  # a single "word" longer than the limit (URL, path)
            if current:
                out.append(current)
                current = ""
            out.append(word[:limit])
            word = word[limit:]
        if current and len(current) + 1 + len(word) > limit:
            out.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        out.append(current)
    return out
