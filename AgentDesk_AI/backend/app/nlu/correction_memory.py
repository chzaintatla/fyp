"""Learn from spoken corrections: "no, I said Firefox" stores heard -> meant, and the next "fire fox" opens Firefox.

Stored as ordinary memory rows of kind "alias" with the text "heard => meant", so they show on the Memory page where the
user can edit or delete them. Nothing is stored when the user switched memory off.
"""
from __future__ import annotations

import re
import time

from ..models import MemoryItem
from .entity_match import _norm

SEP = " => "
_EN = re.compile(r"^(?:no|nope|not that)[,.!\s]+(?:i\s+(?:said|meant|mean|want(?:ed)?)|it'?s|it is)\s+(.+)$", re.I)
_UR1 = re.compile(r"^(?:nahi|nahin|nai|nhi)[,.!\s]+maine\s+(.+?)\s+kaha(?:\s+tha)?$", re.I)
_UR2 = re.compile(r"^(?:nahi|nahin|nai|nhi)[,.!\s]+(?:mera matlab|meri murad)\s+(.+?)(?:\s+tha)?$", re.I)


def parse_correction(text: str) -> str | None:
    """"no, I said Firefox" -> "Firefox". None when the sentence is not a correction."""
    t = (text or "").strip().rstrip(".!?")
    for pattern in (_EN, _UR1, _UR2):
        m = pattern.match(t)
        if m:
            meant = m.group(1).strip().strip("\"'")
            return meant or None
    return None


def load(db) -> dict[str, str]:
    """normalised heard text -> meant name."""
    out: dict[str, str] = {}
    for item in db.query(MemoryItem).filter(MemoryItem.kind == "alias").order_by(MemoryItem.created_at).all():
        if SEP in item.text:
            heard, meant = item.text.split(SEP, 1)
            if _norm(heard) and meant.strip():
                out[_norm(heard)] = meant.strip()
    return out


def learn(db, heard: str, meant: str) -> bool:
    """Store (or update) a correction. False when memory is off or the pair is meaningless."""
    from ..preferences import get_preferences
    if not get_preferences(db).memory_enabled:
        return False
    if not _norm(heard) or not _norm(meant) or _norm(heard) == _norm(meant):
        return False
    text = f"{heard.strip()}{SEP}{meant.strip()}"
    for item in db.query(MemoryItem).filter(MemoryItem.kind == "alias").all():
        if SEP in item.text and _norm(item.text.split(SEP, 1)[0]) == _norm(heard):
            item.text = text
            db.commit()
            _drop_cache()
            return True
    db.add(MemoryItem(kind="alias", text=text))
    db.commit()
    _drop_cache()
    return True


_CACHE: tuple[float, dict[str, str]] = (0.0, {})
_TTL = 30.0


def _drop_cache() -> None:
    global _CACHE
    _CACHE = (0.0, {})


def current() -> dict[str, str]:
    """Corrections for code that has no database session (tools). Cached for 30 s; empty if the database is unavailable."""
    global _CACHE
    stamp, data = _CACHE
    if time.monotonic() - stamp < _TTL and stamp:
        return data
    try:
        from ..database import SessionLocal
        with SessionLocal() as db:
            data = load(db)
    except Exception:  # noqa: BLE001 - a broken memory table must never stop an app from opening
        data = {}
    _CACHE = (time.monotonic(), data)
    return data
