"""Gmail and Outlook (Microsoft Graph) over their REST APIs. One small interface: contacts, send, and "is it in Sent?".

The HTTP client is injected (tests give it a mock transport); every request carries a short-lived bearer token from `oauth.access_token`.
Nothing here decides WHETHER to send: that is `service.MailService`, which requires the read-back confirmation.
"""
from __future__ import annotations

import base64
import mimetypes
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Callable, Protocol

import httpx

from . import oauth
from .models import Attachment, Contact, MailError, SendResult, SentEvidence

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
PEOPLE = "https://people.googleapis.com/v1"
GRAPH = "https://graph.microsoft.com/v1.0"


class MailProvider(Protocol):
    name: str

    def account(self) -> str: ...
    def search_contacts(self, query: str, limit: int = 5) -> list[Contact]: ...
    def send(self, to: list[Contact], subject: str, body: str, attachments: list[Attachment]) -> SendResult: ...
    def find_sent(self, result: SendResult, to: list[Contact], subject: str) -> SentEvidence: ...


class _Base:
    name = ""

    def __init__(self, settings, http: httpx.Client | None = None, token: Callable[[], str] | None = None):
        self.settings = settings
        self.http = http or httpx.Client(timeout=30)
        self._token = token or (lambda: oauth.access_token(self.name, settings, self.http))

    def _call(self, method: str, url: str, **kw) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._token()}", **kw.pop("headers", {})}
        try:
            r = self.http.request(method, url, headers=headers, **kw)
        except httpx.HTTPError as exc:
            raise MailError(f"{self.name.capitalize()} could not be reached: {type(exc).__name__}") from exc
        if r.status_code == 401:
            raise oauth.NotConnected(f"{self.name.capitalize()} refused the sign-in. Connect it again in Settings > Email.")
        if r.status_code == 403:
            raise MailError(f"{self.name.capitalize()} did not allow that (permission missing). Connect it again in Settings > Email.")
        if r.status_code == 429:
            raise MailError(f"{self.name.capitalize()} is busy; try again in a minute.")
        if r.status_code >= 400:
            raise MailError(f"{self.name.capitalize()} answered {r.status_code}.")
        return r


def _read_attachment(a: Attachment) -> tuple[bytes, str]:
    path = Path(a.path)
    if not path.is_file():
        raise MailError(f"The file {a.name} is not there any more.")
    data = path.read_bytes()
    if len(data) > 20 * 1024 * 1024:
        raise MailError(f"{a.name} is bigger than 20 MB.")
    return data, mimetypes.guess_type(a.name)[0] or "application/octet-stream"


def _mime(sender: str, to: list[Contact], subject: str, body: str, attachments: list[Attachment]) -> bytes:
    msg = EmailMessage()
    msg["To"] = ", ".join(f'"{c.name}" <{c.email}>' if c.name else c.email for c in to)
    msg["Subject"] = subject
    if sender:
        msg["From"] = sender
    msg.set_content(body)
    for a in attachments:
        data, ctype = _read_attachment(a)
        main, _, sub = ctype.partition("/")
        msg.add_attachment(data, maintype=main, subtype=sub or "octet-stream", filename=a.name)
    return msg.as_bytes()


class GmailProvider(_Base):
    name = "gmail"

    def account(self) -> str:
        return (oauth.get_store().load("gmail") or {}).get("account", "")

    def search_contacts(self, query: str, limit: int = 5) -> list[Contact]:
        found: list[Contact] = []
        for url, key in ((f"{PEOPLE}/people:searchContacts", "results"), (f"{PEOPLE}/otherContacts:search", "results")):
            try:
                r = self._call("GET", url, params={"query": query, "readMask": "names,emailAddresses", "pageSize": limit})
            except oauth.NotConnected:
                raise
            except MailError:
                continue   # contacts are a convenience: the address can still be spoken
            for item in r.json().get(key, []):
                person = item.get("person", {})
                name = (person.get("names") or [{}])[0].get("displayName", "")
                for mail in person.get("emailAddresses") or []:
                    if mail.get("value"):
                        found.append(Contact(name, mail["value"], "google"))
        return _unique(found)[:limit]

    def send(self, to, subject, body, attachments) -> SendResult:
        raw = base64.urlsafe_b64encode(_mime(self.account(), to, subject, body, attachments)).decode()
        r = self._call("POST", f"{GMAIL}/messages/send", json={"raw": raw})
        data = r.json()
        return SendResult("gmail", data.get("id"), time.time())

    def find_sent(self, result, to, subject) -> SentEvidence:
        if not result.message_id:
            return SentEvidence(False)
        r = self._call("GET", f"{GMAIL}/messages/{result.message_id}", params=[("format", "metadata"), ("metadataHeaders", "To"), ("metadataHeaders", "Subject")])
        data = r.json()
        headers = {h["name"].casefold(): h["value"] for h in data.get("payload", {}).get("headers", []) if "name" in h}
        wanted = {c.email.casefold() for c in to}
        shown_to = headers.get("to", "")
        recipients = [x for x in wanted if x in shown_to.casefold()]
        found = "SENT" in data.get("labelIds", []) and headers.get("subject", "") == subject and len(recipients) == len(wanted)
        return SentEvidence(found, "Sent", recipients, headers.get("subject", ""), data.get("id"))


class OutlookProvider(_Base):
    name = "outlook"

    def account(self) -> str:
        return (oauth.get_store().load("outlook") or {}).get("account", "")

    def search_contacts(self, query: str, limit: int = 5) -> list[Contact]:
        found: list[Contact] = []
        try:
            r = self._call("GET", f"{GRAPH}/me/people", params={"$search": f'"{query}"', "$top": limit})
        except oauth.NotConnected:
            raise
        except MailError:
            return []
        for person in r.json().get("value", []):
            for mail in person.get("scoredEmailAddresses") or []:
                if mail.get("address"):
                    found.append(Contact(person.get("displayName", ""), mail["address"], "outlook"))
        return _unique(found)[:limit]

    def send(self, to, subject, body, attachments) -> SendResult:
        message: dict = {"subject": subject, "body": {"contentType": "Text", "content": body},
                         "toRecipients": [{"emailAddress": {"address": c.email, **({"name": c.name} if c.name else {})}} for c in to]}
        if attachments:
            message["attachments"] = []
            for a in attachments:
                data, ctype = _read_attachment(a)
                message["attachments"].append({"@odata.type": "#microsoft.graph.fileAttachment", "name": a.name, "contentType": ctype,
                                               "contentBytes": base64.b64encode(data).decode()})
        started = time.time()
        self._call("POST", f"{GRAPH}/me/sendMail", json={"message": message, "saveToSentItems": True})   # 202 with no body: no id to remember
        return SendResult("outlook", None, started)

    def find_sent(self, result, to, subject) -> SentEvidence:
        r = self._call("GET", f"{GRAPH}/me/mailFolders/sentitems/messages",
                       params={"$top": 10, "$orderby": "sentDateTime desc", "$select": "id,subject,toRecipients,sentDateTime"})
        wanted = {c.email.casefold() for c in to}
        for item in r.json().get("value", []):
            sent = _parse_time(item.get("sentDateTime", ""))
            recipients = {x.get("emailAddress", {}).get("address", "").casefold() for x in item.get("toRecipients", [])}
            if item.get("subject") == subject and wanted <= recipients and sent >= result.sent_at - 120:
                return SentEvidence(True, "Sent Items", sorted(wanted), item.get("subject", ""), item.get("id"))
        return SentEvidence(False, "Sent Items")


def _parse_time(value: str) -> float:
    from datetime import datetime, timezone
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return 0.0
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()


def _unique(contacts: list[Contact]) -> list[Contact]:
    seen: dict[str, Contact] = {}
    for c in contacts:
        seen.setdefault(c.email.casefold(), c)
    return list(seen.values())


def make_provider(name: str, settings, http: httpx.Client | None = None) -> MailProvider:
    if name == "gmail":
        return GmailProvider(settings, http)
    if name == "outlook":
        return OutlookProvider(settings, http)
    raise MailError("No e-mail account is chosen. Pick Gmail or Outlook in Settings > Email.")
