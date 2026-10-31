"""A small in-memory answer cache (10 minutes) so repeating a question does not search or call the model again."""
from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from typing import Any, Callable

TTL_SECONDS = 600
MAX_ENTRIES = 128


def normalise(query: str) -> str:
    return re.sub(r"[^\w\s]", "", query.casefold(), flags=re.U).strip()


class TTLCache:
    def __init__(self, ttl: float = TTL_SECONDS, size: int = MAX_ENTRIES, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl, self.size, self.clock = ttl, size, clock
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            hit = self._data.get(key)
            if hit is None:
                return None
            stamp, value = hit
            if self.clock() - stamp > self.ttl:
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return value

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = (self.clock(), value)
            self._data.move_to_end(key)
            while len(self._data) > self.size:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
