"""Turn a spoken name into a recipient: "Ali" -> the one Ali in the contacts, or a question when there are several.

Names are matched with the same sound-alike matcher as app and file names, so "Allie" or "Aly" still find "Ali". An address that was spoken
("ali dot khan at example dot com") or typed is used as it is, after a check that it is a valid address.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..nlu import entity_match
from .models import Contact, spoken_to_email, valid_email


@dataclass
class Resolution:
    kind: str                       # one | many | none
    contacts: list[Contact] = field(default_factory=list)


class _Named:
    def __init__(self, c: Contact):
        self.name, self.kind, self.path, self.source = c.label(), "contact", c.email, c.source


def resolve(spoken: str, provider) -> Resolution:
    text = " ".join((spoken or "").split()).strip(" .,")
    if not text:
        return Resolution("none")
    direct = text if valid_email(text) else spoken_to_email(text)
    if direct:
        return Resolution("one", [Contact("", direct, "spoken")])
    found = provider.search_contacts(text, 8)
    for size in (3, 2):   # a misheard name ("Zaynab" for "Zainab"): look by its first letters, keep what sounds like it
        if found or len(text) <= size:
            break
        found = [c for c in provider.search_contacts(text[:size], 15) if entity_match.score(text, c.label()) >= 0.6]
    if not found:
        return Resolution("none")
    exact = [c for c in found if c.label().casefold() == text.casefold()]
    if len(exact) == 1:
        return Resolution("one", exact)
    if len(found) == 1:
        return Resolution("one", found)
    decision = entity_match.resolve(text, [_Named(c) for c in found])
    if decision.action in ("act", "act_say") and decision.chosen is not None:
        same = [c for c in found if c.label() == decision.chosen.name]
        if len(same) == 1:
            return Resolution("one", same)
    return Resolution("many", found[:5])


def pick(spoken: str, candidates: list[dict]) -> Contact | None:
    """The user's answer to "Which Ali: Ali Khan or Ali Raza?": a number, "the first / second", or a name."""
    from ..ai.browser_commands import _ORDINALS
    text = " ".join((spoken or "").casefold().split()).strip(" .")
    contacts = [Contact(c.get("name", ""), c["email"]) for c in candidates]
    for word in text.replace("number", "").split():
        if word.isdigit() and 1 <= int(word) <= len(contacts):
            return contacts[int(word) - 1]
        if word in _ORDINALS and _ORDINALS[word] <= len(contacts):
            return contacts[_ORDINALS[word] - 1]
    hits = [c for c in contacts if text and (text in c.label().casefold() or c.label().casefold() in text)]
    if len(hits) == 1:
        return hits[0]
    decision = entity_match.resolve(text, [_Named(c) for c in contacts]) if text else None
    if decision and decision.action in ("act", "act_say") and decision.chosen is not None:
        return next((c for c in contacts if c.label() == decision.chosen.name), None)
    return None
