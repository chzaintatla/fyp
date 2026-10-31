"""Several Groq API keys used one after another.

When a key hits its rate/usage limit (HTTP 429) it "cools down" for the time Groq asks for (or 60 s) and the next
key is used immediately; a key Groq rejects (401/403) is dropped for the rest of the run. When every key is cooling,
the one that frees up first is used. Keys are never logged, returned by the API or shown in `repr`.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

DEFAULT_COOLDOWN_SECONDS = 60.0
MAX_COOLDOWN_SECONDS = 3600.0


class KeyPool:
    def __init__(self, keys: list[str], clock: Callable[[], float] = time.monotonic):
        self._keys = list(dict.fromkeys(k.strip() for k in keys if k and k.strip()))
        self._until: dict[str, float] = {}
        self._dead: set[str] = set()
        self._clock = clock
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._keys)

    def __repr__(self) -> str:
        return f"KeyPool({len(self._keys)} keys, {self.available()} available)"

    def _live(self) -> list[str]:
        return [k for k in self._keys if k not in self._dead]

    def available(self) -> int:
        now = self._clock()
        with self._lock:
            return sum(1 for k in self._live() if self._until.get(k, 0) <= now)

    def current(self) -> str | None:
        """First key that is not cooling; otherwise the one that frees up soonest."""
        now = self._clock()
        with self._lock:
            live = self._live()
            for key in live:
                if self._until.get(key, 0) <= now:
                    return key
            return min(live, key=lambda k: self._until.get(k, 0), default=None)

    def penalize(self, key: str, seconds: float | None = None) -> bool:
        """Cool `key` down. Returns True when another key is usable right now."""
        seconds = DEFAULT_COOLDOWN_SECONDS if seconds is None else max(1.0, min(float(seconds), MAX_COOLDOWN_SECONDS))
        with self._lock:
            self._until[key] = self._clock() + seconds
        return self.available() > 0

    def drop(self, key: str) -> bool:
        """Remove a key Groq rejected. Returns True when another key is usable right now."""
        with self._lock:
            self._dead.add(key)
        return self.available() > 0


def parse_keys(single: str | None, many: str | None) -> list[str]:
    raw = [*(many or "").replace(";", ",").replace("\n", ",").split(","), single or ""]
    return list(dict.fromkeys(k.strip() for k in raw if k.strip()))


_pools: dict[tuple[str, ...], KeyPool] = {}


def pool_for(keys: list[str]) -> KeyPool:
    """One shared pool per key set, so the agent loop, planner and speech share cooldowns."""
    return _pools.setdefault(tuple(keys), KeyPool(keys))
