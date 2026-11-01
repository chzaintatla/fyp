"""Match a spoken name against the entity index: text similarity + sound-alike key, then the act / say / ask decision.

Section 4.4 of the upgrade spec, as code:
  score >= 0.85 and clearly ahead of the runner-up -> "act"
  0.60-0.85 and clearly ahead                       -> "act_say" (low risk only: act, and say what was understood)
  below 0.60, or two close candidates               -> "ask"
High-risk callers get "ask" unless the match is exact: a wrong guess must never reach a delete or a send.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from .phonetic import OPEN_WORDS, phonetic_key

ACT = 0.85
SAY = 0.60
GAP = 0.08            # the best candidate must lead the second by this much, else the user is asked
EXACT = 0.99

_NOISE = re.compile(r"\b(?:the|app|application|program|desktop|please|my|file|folder|document)\b", re.I)
_SPLIT = re.compile(r"[^a-z0-9+#]+")


def _norm(text: str, strip_noise: bool = True) -> str:
    t = re.sub(r"\.(?:lnk|url|exe)$", "", text.strip(), flags=re.I)
    t = re.sub(r"plus\s+plus", "++", t, flags=re.I)
    words = " ".join(w for w in _SPLIT.split((_NOISE.sub(" ", t) if strip_noise else t).casefold()) if w)
    return words or " ".join(w for w in _SPLIT.split(t.casefold()) if w)


def _abbreviated(n: str) -> str | None:
    """"visual studio code" -> "vs code": people say the initials of the leading words."""
    words = n.split()
    return "".join(w[0] for w in words[:-1]) + " " + words[-1] if len(words) >= 3 else None


def strip_open_words(text: str) -> str:
    """"vs code kholo" -> "vs code": the Roman-Urdu verb is a command, not part of the name."""
    t = text.strip()
    for w in sorted(OPEN_WORDS, key=len, reverse=True):
        t = re.sub(rf"(?:^|\s){re.escape(w)}(?=\s|$)", " ", t, flags=re.I)
    t = re.sub(r"\b(?:wala|wali|ko|ka|ki|ke)\b", " ", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()


def score(query: str, name: str) -> float:
    """0..1 similarity of a spoken name to a real name. 1.0 only when they are the same after normalising.

    "app"/"file" style filler words are ignored, but "whats app" must still match "WhatsApp", so the unstripped spelling is tried too.
    """
    best = _score(_norm(query), _norm(name))
    raw_q, raw_n = _norm(query, False), _norm(name, False)
    if best < 1.0 and (raw_q != _norm(query) or raw_n != _norm(name)):
        best = max(best, _score(raw_q, raw_n))
    short = _abbreviated(_norm(name))
    if best < 1.0 and short:
        best = max(best, _score(_norm(query), short) * 0.99)
    return best


def _score(q: str, n: str) -> float:
    if not q or not n:
        return 0.0
    qj, nj = q.replace(" ", ""), n.replace(" ", "")
    if qj == nj:
        return 1.0
    lexical = max(
        fuzz.ratio(qj, nj) / 100,
        fuzz.token_sort_ratio(q, n) / 100,
        fuzz.token_set_ratio(q, n) / 100 * 0.9,
    )
    if min(len(qj), len(nj)) >= 3 and (qj in nj or nj in qj):
        lexical = max(lexical, 0.9 if min(len(qj), len(nj)) * 2 >= max(len(qj), len(nj)) else 0.7)
    if len(qj) >= 3 and qj in n.split():
        lexical = max(lexical, 0.88)   # "v l c" -> "VLC media player", "chrome" -> "Google Chrome"
    kq, kn = phonetic_key(q), phonetic_key(n)
    sound = 0.0
    if len(kq) >= 3 and len(kn) >= 3:
        sound = fuzz.ratio(kq, kn) / 100 * 0.97
        if kq == kn:
            sound = 0.97
        if abs(len(kq) - len(kn)) > max(2, len(kn) // 2):
            sound *= 0.8   # "pad" must not match "notepad" just because the sounds overlap
    final = min(max(lexical, sound), 0.98)
    if SAY <= final < ACT and len(q.split()) >= 2 and _coverage(q, n) < 0.67:
        final = min(final * 0.9, SAY - 0.005)   # "taxi receipt" is not "Tax Return": half the words match nothing, so ask instead of guessing
    return round(final, 4)


def _coverage(q: str, n: str) -> float:
    """Share of the spoken words that have a similar word (by spelling or sound) in the name."""
    name_words = n.split()
    hit = 0
    for w in q.split():
        if any(fuzz.ratio(w, x) >= 70 or (len(w) > 2 and phonetic_key(w) == phonetic_key(x)) for x in name_words):
            hit += 1
    return hit / max(1, len(q.split()))


@dataclass
class Candidate:
    name: str
    score: float
    kind: str = ""
    path: str = ""
    via: str = "similarity"      # similarity | correction | exact

    def as_dict(self) -> dict:
        return {"name": self.name, "score": self.score, "kind": self.kind, "via": self.via}


@dataclass
class Decision:
    action: str                  # act | act_say | ask | none
    candidates: list[Candidate] = field(default_factory=list)
    chosen: Candidate | None = None

    @property
    def understood(self) -> str:
        return self.chosen.name if self.chosen else ""


class Prepared:
    """Entries with their normalised spelling and sound key computed once, so a lookup over thousands of names stays fast."""

    def __init__(self, entries):
        self.entries = list(entries)
        self.joined = [_norm(e.name).replace(" ", "") for e in self.entries]
        self.keys = [phonetic_key(_norm(e.name)) for e in self.entries]


SHORTLIST_OVER = 150   # below this many names every name is scored; above it a C-speed pre-filter picks ~40 to score
_SOURCE_WEIGHT = {"app_paths": 0.9}   # executable names (powerpnt, IEXPLORE) are noisy next to the Start Menu's own names


def _shortlist(q: str, prep: Prepared) -> list[int]:
    from rapidfuzz import process
    qj = _norm(q).replace(" ", "")
    kq = phonetic_key(q)
    picked: set[int] = set()
    for choices, query, scorer in ((prep.joined, qj, fuzz.WRatio), (prep.keys, kq, fuzz.ratio), (prep.joined, qj, fuzz.partial_ratio)):
        if query:
            picked.update(i for _, _, i in process.extract(query, choices, scorer=scorer, limit=14))
    return sorted(picked)


def rank(query: str, entries, limit: int = 3, corrections: dict[str, str] | None = None) -> list[Candidate]:
    """Best `limit` distinct names. `entries` are objects with .name .kind .path .source (entity_index.Entity), or a Prepared."""
    prep = entries if isinstance(entries, Prepared) else Prepared(entries)
    rows = prep.entries
    q = strip_open_words(query)
    out: dict[str, Candidate] = {}
    if corrections:
        meant = corrections.get(_norm(q))
        if meant:
            for e in rows:
                if _norm(e.name) == _norm(meant):
                    out[prep.joined[rows.index(e)]] = Candidate(e.name, 1.0, e.kind, e.path, "correction")
    indexes = _shortlist(q, prep) if len(rows) > SHORTLIST_OVER else range(len(rows))
    for i in indexes:
        e = rows[i]
        key = prep.joined[i]          # "SnippingTool" and "Snipping Tool" are one name
        if key in out and out[key].via == "correction":
            continue
        s = score(q, e.name)
        weight = _SOURCE_WEIGHT.get(getattr(e, "source", ""), 1.0)
        if weight < 1.0:
            s = round(s * weight, 4)
        if s >= 0.4:
            old = out.get(key)
            if old is None or s > old.score or (s == old.score and " " in e.name and " " not in old.name):
                out[key] = Candidate(e.name, s, e.kind, e.path, "exact" if s >= 1.0 else "similarity")
    return sorted(out.values(), key=lambda c: (-c.score, c.name.casefold()))[:limit]


def decide(cands: list[Candidate], risk: str = "low") -> Decision:
    if not cands or cands[0].score < 0.4:
        return Decision("none", cands)
    best = cands[0]
    runner = cands[1].score if len(cands) > 1 else 0.0
    clear = best.score - runner >= GAP or best.via == "correction" or (best.score >= 1.0 and runner < 1.0)   # the exact name beats a near one (Notepad vs Notepad++)
    if risk != "low":
        return Decision("act" if best.score >= EXACT and clear else "ask", cands, best if best.score >= EXACT and clear else None)
    if best.score >= ACT and clear:
        return Decision("act", cands, best)
    if best.score >= SAY and clear:
        return Decision("act_say", cands, best)
    return Decision("ask", cands)


def resolve(query: str, entries, risk: str = "low", corrections: dict[str, str] | None = None) -> Decision:
    return decide(rank(query, entries, 3, corrections), risk)


_STOP = set("open launch start run close show play find the a an to in on of and or my please me it kholo karo kar do dein band chalao "
            "go switch search for create make new folder file called named from with this that is are can you could".split())


def candidates_for_command(command: str, entries, limit: int = 3, corrections: dict[str, str] | None = None) -> list[Candidate]:
    """Names in the index that sound like any 1-4 word piece of the command, for the planner to choose from (score >= 0.6).

    A piece that matches a name exactly is skipped: the planner needs no help with a name that was heard correctly.
    """
    words = [w for w in _SPLIT.split(command.casefold()) if w]
    best: dict[str, Candidate] = {}
    entries = entries if isinstance(entries, Prepared) else Prepared(entries)
    for size in (1, 2, 3, 4):
        for i in range(len(words) - size + 1):
            piece = words[i:i + size]
            if piece[0] in _STOP or piece[-1] in _STOP or len("".join(piece)) < 3:
                continue
            cands = rank(" ".join(piece), entries, 3, corrections)
            if cands and cands[0].via == "exact":
                continue   # this piece is a real name already
            for c in cands:
                if c.score >= SAY and c.via != "exact":
                    old = best.get(c.name.casefold())
                    if old is None or old.score < c.score:
                        best[c.name.casefold()] = c
    return sorted(best.values(), key=lambda c: (-c.score, c.name.casefold()))[:limit]
