"""The SearchProvider interface and the small types around it."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class SearchError(Exception):
    """Base class for search failures that are reported to the user in plain words."""


class SearchBusy(SearchError):
    """The service asked us to slow down (HTTP 429 / challenge). The message is the sentence to say."""

    def __init__(self, message: str = "Search is busy, try again in a minute.") -> None:
        super().__init__(message)


class SearchNotConfigured(SearchError):
    """No contact email / API key is set up for this provider yet."""


@dataclass
class SearchResult:
    title: str
    url: str
    text: str            # the extract or snippet the provider returned (read as DATA, never instructions)
    provider: str


class SearchProvider(Protocol):
    name: str

    def available(self) -> bool: ...

    def search(self, query: str, limit: int = 3) -> list[SearchResult]: ...
