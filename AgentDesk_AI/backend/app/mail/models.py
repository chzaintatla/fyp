"""Plain data shared by the e-mail pieces, and the small text helpers around e-mail addresses."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

EMAIL = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024     # under Gmail's 25 MB limit once encoded
MAX_RECIPIENTS = 10


class MailError(RuntimeError):
    """A mail step failed; the message is short enough to show or speak."""


class NotConnected(MailError):
    """No usable sign-in for the chosen provider (never connected, revoked, or the refresh failed)."""


@dataclass
class Contact:
    name: str
    email: str
    source: str = ""

    def label(self) -> str:
        return self.name or self.email

    def as_dict(self) -> dict:
        return {"name": self.name, "email": self.email}


@dataclass
class Attachment:
    name: str
    path: str
    size: int

    def as_dict(self) -> dict:
        return {"name": self.name, "path": self.path, "size": self.size}


@dataclass
class SendResult:
    provider: str
    message_id: str | None
    sent_at: float


@dataclass
class SentEvidence:
    """What the provider's Sent folder shows for the message just sent."""
    found: bool
    folder: str = "Sent"
    to: list[str] = field(default_factory=list)
    subject: str = ""
    message_id: str | None = None


def valid_email(value: str) -> bool:
    return bool(EMAIL.match(value or "")) and len(value) <= 254


def spoken_to_email(text: str) -> str | None:
    """"ali dot khan at example dot com" -> "ali.khan@example.com". None when it does not become a valid address."""
    t = " " + re.sub(r"\s+", " ", text.casefold().strip().strip(".,")) + " "
    for words, sym in ((" at the rate of ", "@"), (" at the rate ", "@"), (" at ", "@"), (" dot ", "."), (" underscore ", "_"),
                       (" dash ", "-"), (" hyphen ", "-"), (" plus ", "+")):
        t = t.replace(words, sym)
    t = t.replace(" ", "")
    return t if valid_email(t) else None


def mask_email(email: str) -> str:
    """ali.khan@example.com -> a***n@example.com (for logs and screens that need no full address)."""
    local, _, domain = email.partition("@")
    return (local[:1] + "***" + local[-1:] if len(local) > 2 else local[:1] + "*") + "@" + domain
