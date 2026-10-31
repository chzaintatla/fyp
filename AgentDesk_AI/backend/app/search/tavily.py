"""Tavily search API. The key is read from Windows Credential Manager (service 'AgentDesk-search', user 'tavily') or the
TAVILY_API_KEY environment variable; it is never logged, returned by the API, or committed. Tavily returns text snippets, so no
page is scraped."""
from __future__ import annotations

import os

import httpx

from .provider import SearchBusy, SearchError, SearchNotConfigured, SearchResult

URL = "https://api.tavily.com/search"
KEY_SERVICE, KEY_USER = "AgentDesk-search", "tavily"


def load_key() -> str | None:
    env = os.environ.get("TAVILY_API_KEY", "").strip()
    if env:
        return env
    try:
        import keyring
        return (keyring.get_password(KEY_SERVICE, KEY_USER) or "").strip() or None
    except Exception:  # noqa: BLE001 - no credential store available: treated as "not configured"
        return None


class TavilyProvider:
    name = "tavily"

    def __init__(self, key: str | None = None, client: httpx.Client | None = None) -> None:
        self._key = key
        self._client = client

    def _api_key(self) -> str | None:
        return self._key or load_key()

    def available(self) -> bool:
        return bool(self._api_key())

    def search(self, query: str, limit: int = 3) -> list[SearchResult]:
        key = self._api_key()
        if not key:
            raise SearchNotConfigured("No Tavily key is stored. Run scripts/set_search_key.py once.")
        client = self._client or httpx.Client(timeout=15)
        try:
            r = client.post(URL, json={"query": query, "max_results": limit, "search_depth": "basic", "include_answer": False,
                                       "include_raw_content": False}, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as exc:
            raise SearchError(f"I couldn't reach the search service ({type(exc).__name__}).") from exc
        finally:
            if self._client is None:
                client.close()
        if r.status_code in (429, 432, 433):
            raise SearchBusy()
        if r.status_code in (401, 403):
            raise SearchNotConfigured("The stored Tavily key was refused. Run scripts/set_search_key.py with a valid key.")
        if r.status_code != 200:
            raise SearchError(f"The search service answered HTTP {r.status_code}.")
        return [SearchResult(title=(x.get("title") or x.get("url") or "")[:120], url=x.get("url") or "", text=(x.get("content") or "").strip(),
                             provider=self.name)
                for x in r.json().get("results", [])[:limit] if x.get("url") and len((x.get("content") or "").strip()) > 40]
