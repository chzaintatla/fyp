"""A fake mailbox for the e-mail tests: contacts, a Sent folder, and switches to make it misbehave."""
from __future__ import annotations

import time

from app.mail.models import Attachment, Contact, SendResult, SentEvidence


class FakeMail:
    name = "gmail"

    def __init__(self, contacts: list[Contact] | None = None):
        self.contacts = contacts if contacts is not None else [Contact("Ali Khan", "ali.khan@example.com")]
        self.sent: list[dict] = []
        self.sent_folder_shows_it = True
        self.fail_send: str | None = None
        self.searches: list[str] = []

    def account(self) -> str:
        return "me@example.com"

    def search_contacts(self, query: str, limit: int = 5) -> list[Contact]:
        self.searches.append(query)
        q = query.casefold()
        return [c for c in self.contacts if q in c.name.casefold() or q in c.email.casefold()][:limit]

    def send(self, to: list[Contact], subject: str, body: str, attachments: list[Attachment]) -> SendResult:
        if self.fail_send:
            from app.mail.models import MailError
            raise MailError(self.fail_send)
        self.sent.append({"to": [c.email for c in to], "subject": subject, "body": body, "attachments": [a.name for a in attachments]})
        return SendResult("gmail", f"msg{len(self.sent)}", time.time())

    def find_sent(self, result: SendResult, to: list[Contact], subject: str) -> SentEvidence:
        if not self.sent_folder_shows_it:
            return SentEvidence(False)
        last = self.sent[-1]
        ok = last["subject"] == subject and last["to"] == [c.email for c in to]
        return SentEvidence(ok, "Sent", last["to"], last["subject"], result.message_id)
