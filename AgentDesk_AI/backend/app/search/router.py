"""Chooses providers for a question: Wikipedia first for definitional "what/who is X" questions, Tavily for everything else,
each as the fallback of the other. Answers are cached for 10 minutes. A busy provider is reported, never retried in a loop."""
from __future__ import annotations

import re

from .cache import TTLCache, normalise
from .provider import SearchBusy, SearchNotConfigured, SearchProvider, SearchResult

# "what is X", "who is X", "who was X", "what are X", "tell me about X", Roman Urdu "X kya hai / kaun hai"
_DEFINITION = re.compile(r"^(?:what(?:'s| is| are| was| were)|who(?:'s| is| was| were)|define|tell me about|meaning of)\s+(?!the (?:current|latest|new)\b)"
                         r"|(?:\bkya hai|\bkaun hai|\bkaun tha|\bkya hota hai)\??$", re.I)
_CURRENT = re.compile(r"\b(current(?:ly)?|latest|today|tonight|now|right now|this (?:week|month|year)|news|weather|price|score|won|winner|"
                      r"stock|rate|forecast|result|abhi|aaj|taaza)\b|20\d\d", re.I)

cache = TTLCache()


def is_definitional(query: str) -> bool:
    q = " ".join(query.strip().split())
    return bool(_DEFINITION.search(q)) and not _CURRENT.search(q)


def order(query: str, wikipedia: SearchProvider, tavily: SearchProvider) -> list[SearchProvider]:
    return [wikipedia, tavily] if is_definitional(query) else [tavily, wikipedia]


def search(query: str, wikipedia: SearchProvider, tavily: SearchProvider, limit: int = 3, use_cache: bool = True) -> list[SearchResult]:
    """Results from the first provider that has any. Raises SearchBusy when a provider is busy and nothing else answered,
    SearchNotConfigured when no provider is set up."""
    key = normalise(query)
    if use_cache and (hit := cache.get(key)) is not None:
        return hit
    busy: SearchBusy | None = None
    configured = False
    for provider in order(query, wikipedia, tavily):
        if not provider.available():
            continue
        configured = True
        try:
            results = provider.search(query, limit)
        except SearchBusy as exc:
            busy = exc
            continue
        except SearchNotConfigured:
            continue
        if results:
            if use_cache:
                cache.put(key, results)
            return results
    if busy is not None:
        raise busy
    if not configured:
        raise SearchNotConfigured("Web answers need a search provider: set WIKIPEDIA_CONTACT_EMAIL and/or store a Tavily key (see docs/SEARCH_SETUP.md).")
    return []
