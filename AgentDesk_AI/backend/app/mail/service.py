"""Choose the provider, send a confirmed draft exactly once, and look in the Sent folder for it.

`send` is the only place an e-mail leaves the PC, and it refuses unless the draft carries a confirmation that was issued for its EXACT
current content (see draft.py). The tool wrapper (tools/mail.py) and the dictation flow both go through here.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from . import draft as drafts
from .models import MAX_ATTACHMENT_BYTES, MAX_RECIPIENTS, Attachment, Contact, MailError, SendResult, SentEvidence, valid_email
from .providers import MailProvider, make_provider

_override: MailProvider | None = None


def set_provider_override(provider: MailProvider | None) -> None:
    """Tests (and the live script) give a ready provider; normal runs build one from the Settings choice."""
    global _override
    _override = provider


def provider_for(name: str, settings) -> MailProvider:
    return _override if _override is not None else make_provider(name, settings)


def chosen_provider(db) -> str:
    from ..preferences import get_preferences
    return get_preferences(db).mail_provider


def check_ready(draft: drafts.Draft) -> None:
    """Everything that must be true before the read-back is even offered."""
    if not draft.to:
        raise MailError("There is nobody to send it to yet.")
    if len(draft.to) > MAX_RECIPIENTS:
        raise MailError(f"At most {MAX_RECIPIENTS} recipients.")
    bad = [c.get("email", "") for c in draft.to if not valid_email(c.get("email", ""))]
    if bad:
        raise MailError(f"{bad[0]} is not a valid address.")
    if not draft.body.strip():
        raise MailError("The email has no text yet.")
    total = 0
    for a in draft.attachments:
        path = Path(a["path"])
        if not path.is_file():
            raise MailError(f"The file {a['name']} is not there any more.")
        total += path.stat().st_size
    if total > MAX_ATTACHMENT_BYTES:
        raise MailError("The attachments are bigger than 20 MB together.")


def send(draft: drafts.Draft, token: str | None, settings) -> SendResult:
    """Send `draft`. Raises MailError without sending when the confirmation is missing, stale, or for different content."""
    if draft.sent:
        raise MailError("This email was already sent.")
    if not draft.confirmation_valid(token):
        raise MailError("I have not read this email back and heard your yes for its current text. Nothing was sent.")
    check_ready(draft)
    provider = provider_for(draft.provider, settings)
    to = [Contact(c.get("name", ""), c["email"]) for c in draft.to]
    attachments = [Attachment(a["name"], a["path"], a["size"]) for a in draft.attachments]
    return provider.send(to, draft.subject or "(no subject)", draft.body, attachments)


def find_in_sent(draft: drafts.Draft, result: SendResult, settings, attempts: int = 4, wait: float = 1.5,
                 sleep: Callable[[float], None] = time.sleep) -> SentEvidence:
    """Look for the message in the provider's Sent folder; the folder can lag a moment behind, so a few tries."""
    provider = provider_for(draft.provider, settings)
    to = [Contact(c.get("name", ""), c["email"]) for c in draft.to]
    evidence = SentEvidence(False)
    for i in range(attempts):
        evidence = provider.find_sent(result, to, draft.subject or "(no subject)")
        if evidence.found:
            return evidence
        if i + 1 < attempts:
            sleep(wait)
    return evidence
