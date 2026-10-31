"""Wikipedia through its official REST API, with the User-Agent Wikimedia's policy asks for and at most one request per second.

The contact e-mail comes from WIKIPEDIA_CONTACT_EMAIL (backend/.env). Without it this provider reports itself unavailable: the
app never sends a made-up or generic user agent to Wikipedia.
"""
from __future__ import annotations

import threading
import time
from typing import Callable
from urllib.parse import quote

import httpx

from .provider import SearchBusy, SearchResult

API = "https://en.wikipedia.org"
MIN_INTERVAL = 1.0


class RateLimiter:
    def __init__(self, interval: float = MIN_INTERVAL, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.interval, self.clock, self.sleep = interval, clock, sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self.clock()
            delay = self._next - now
            if delay > 0:
                self.sleep(delay)
                now = self.clock()
            self._next = now + self.interval


def user_agent(email: str) -> str:
    return f"AgentDesk-AI/0.3 (Riphah FYP; {email})"


class WikipediaProvider:
    name = "wikipedia"

    def __init__(self, contact_email: str, client: httpx.Client | None = None, limiter: RateLimiter | None = None) -> None:
        self.email = (contact_email or "").strip()
        self.limiter = limiter or RateLimiter()
        self._client = client

    def available(self) -> bool:
        return "@" in self.email

    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        self.limiter.wait()
        client = self._client or httpx.Client(timeout=10)
        try:
            r = client.get(API + path, params=params, headers={"User-Agent": user_agent(self.email), "Accept": "application/json"})
        finally:
            if self._client is None:
                client.close()
        if r.status_code in (429, 503):
            raise SearchBusy()
        return r

    def search(self, query: str, limit: int = 3) -> list[SearchResult]:
        if not self.available():
            return []
        r = self._get("/w/rest.php/v1/search/page", {"q": query, "limit": limit})
        if r.status_code != 200:
            return []
        out: list[SearchResult] = []
        for page in r.json().get("pages", [])[:limit]:
            key = page.get("key") or ""
            summary = self._get(f"/api/rest_v1/page/summary/{quote(key, safe='')}")
            if summary.status_code != 200:
                continue
            data = summary.json()
            text = (data.get("extract") or "").strip()
            if len(text) > 40:
                url = (data.get("content_urls") or {}).get("desktop", {}).get("page") or f"{API}/wiki/{quote(key)}"
                out.append(SearchResult(title=data.get("title") or page.get("title") or key, url=url, text=text, provider=self.name))
        return out
