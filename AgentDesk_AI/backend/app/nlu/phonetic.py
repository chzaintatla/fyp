"""Sound-alike keys for names that Whisper hears wrongly ("fire fox", "vs cold", "note pad").

`phonetic_key` is a compact Metaphone-style key written for this project (no new dependency, works offline, ASCII only).
It is NOT the full Double Metaphone algorithm: it has one key per word, not a primary and an alternate. Before the
English rules run, a Roman-Urdu/Hindi spelling folder maps the usual variants onto one spelling
(kh/k, gh/g, aa/a, ee/i, oo/u, w/v, z/j), so "khalid" and "kalid", or "wideo" and "video", get the same key.
Keys are only used to *rank* candidates; the final decision uses thresholds in `entity_match`.
"""
from __future__ import annotations

import re
from functools import lru_cache

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Spoken forms of "open" in Roman Urdu / Hindi: "kholo", "kholen", "khol do", ... They are commands, not names.
OPEN_WORDS = ("kholo", "kholen", "kholein", "kholna", "kholiye", "kholiyay", "khol do", "khol dein", "khol de", "khol", "khulwao")


def fold_roman(text: str) -> str:
    """Roman-Urdu/Hindi spelling variants -> one spelling (only for building keys, never shown to the user)."""
    t = text.casefold()
    for pattern, repl in (("aa", "a"), ("ee", "i"), ("oo", "u"), ("kh", "k"), ("gh", "g"), ("ph", "f"), ("sh", "x"), ("ch", "x")):
        t = t.replace(pattern, repl)
    return t.replace("w", "v").replace("z", "j")


# Applied in order to a single lowercase word.
_RULES: tuple[tuple[str, str], ...] = (
    (r"^(?:kn|gn|pn|wr)", "n"), (r"^x", "s"), (r"^wh", "w"), (r"^ae", "e"),
    (r"mb$", "m"), (r"tch", "x"), (r"dg", "j"), (r"sch", "sk"), (r"ck", "k"), (r"q", "k"),
    (r"c(?=[eiy])", "s"), (r"c", "k"), (r"gh(?=t|$)", ""), (r"th", "t"),
    (r"x", "ks"),
)


@lru_cache(maxsize=8192)
def _word_key(word: str) -> str:
    w = word
    for pattern, repl in _RULES:
        w = re.sub(pattern, repl, w)
    # w, y and h only count before a vowel; vowels only count at the start of the word
    out: list[str] = []
    for i, ch in enumerate(w):
        nxt = w[i + 1] if i + 1 < len(w) else ""
        if ch in "aeiou":
            if i == 0:
                out.append("a")
            continue
        if ch in "wyh" and nxt not in tuple("aeiou"):
            continue
        if ch.isdigit():
            out.append(ch)
            continue
        if ch == "v":
            ch = "f"
        elif ch == "z":
            ch = "s"
        elif ch == "d":
            ch = "t"  # "code" / "cold" / "coat" stay close: voicing differences are the commonest mishearing
        elif ch == "b":
            ch = "p"
        elif ch == "g":
            ch = "k"
        if out and out[-1] == ch:
            continue
        out.append(ch)
    return "".join(out)


@lru_cache(maxsize=8192)
def phonetic_key(text: str) -> str:
    """Key of a whole phrase with the word breaks removed, so "fire fox" and "firefox" give the same key."""
    words = [w for w in _NON_ALNUM.split(fold_roman(text)) if w]
    return "".join(_word_key(w) for w in words)
