"""The e-mail being written, and the confirmation that must exist before it can be sent.

One draft at a time, stored as a preference row (so it survives a restart and the screen can show it live). Every change to the
recipients, the subject, the text or the attachments erases any earlier confirmation: the user must hear the NEW read-back and say
"yes, send it" again. A confirmation is tied to the exact content (a hash) and expires after ten minutes.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from dataclasses import asdict, dataclass, field

from sqlalchemy.orm import Session

from ..models import Preference

KEY = "mail_draft"
IDLE_SECONDS = 1800
CONFIRM_SECONDS = 600
MAX_HISTORY = 10
MAX_BODY_CHARS = 20000
STATES = ("to", "to_choose", "subject", "body", "review", "confirm", "sent", "cancelled")
DICTATING = {"to", "to_choose", "subject", "body", "review", "confirm"}
_END = re.compile(r"[.!?؟۔।…]$")


@dataclass
class Draft:
    id: str
    provider: str
    state: str = "to"
    language: str = "en"
    to: list[dict] = field(default_factory=list)            # [{"name", "email"}]
    subject: str = ""
    sentences: list[str] = field(default_factory=list)
    attachments: list[dict] = field(default_factory=list)   # [{"name", "path", "size"}]
    wanted_name: str = ""                                    # the name the user said, while contacts are being chosen
    candidates: list[dict] = field(default_factory=list)
    pending: dict | None = None                              # {"kind": "rewrite" | "attach", ...} waiting for "yes"
    history: list[list[str]] = field(default_factory=list)   # earlier versions of the text, for undo
    confirm: dict | None = None                              # {"token", "hash", "at"}
    sent: dict | None = None
    updated_at: float = field(default_factory=time.time)

    # -- reading
    @property
    def body(self) -> str:
        return " ".join(self.sentences)

    def first_line(self, limit: int = 110) -> str:
        first = self.sentences[0] if self.sentences else ""
        return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"

    def recipients_label(self) -> str:
        return " and ".join(c.get("name") or c.get("email", "") for c in self.to)

    def content_hash(self) -> str:
        blob = json.dumps([sorted(c["email"].casefold() for c in self.to), self.subject, self.body,
                           sorted((a["name"], a["size"]) for a in self.attachments)], ensure_ascii=False)
        return hashlib.sha256(blob.encode()).hexdigest()

    def readback(self) -> str:
        """What is read aloud before sending: who, what subject, and the first line. Nothing else decides the confirmation."""
        files = f" With {len(self.attachments)} attachment{'s' if len(self.attachments) != 1 else ''}: " + ", ".join(a["name"] for a in self.attachments) + "." if self.attachments else ""
        return (f"To {self.recipients_label()}. Subject: {self.subject or 'no subject'}. It starts: {(self.first_line() or 'it is empty').rstrip('.!?')}.{files}")

    # -- every edit goes through here
    def _changed(self) -> None:
        self.confirm = None
        self.pending = None
        self.updated_at = time.time()
        if self.state == "confirm":
            self.state = "review"

    def _snapshot(self) -> None:
        self.history = (self.history + [list(self.sentences)])[-MAX_HISTORY:]

    def add_text(self, text: str) -> None:
        text = " ".join(text.split())
        if not text:
            return
        if not _END.search(text):
            text += "."
        if len(self.body) + len(text) > MAX_BODY_CHARS:
            raise ValueError("The email is getting too long.")
        self._snapshot()
        self.sentences.append(text)
        self._changed()

    def delete_last(self) -> str | None:
        if not self.sentences:
            return None
        self._snapshot()
        removed = self.sentences.pop()
        self._changed()
        return removed

    def replace(self, old: str, new: str) -> int:
        """Replace `old` by `new` everywhere in the text (ignoring case). Returns how many places changed."""
        pattern = re.compile(re.escape(old.strip()), re.I)
        count = sum(len(pattern.findall(s)) for s in self.sentences)
        if count:
            self._snapshot()
            self.sentences = [pattern.sub(new.strip(), s) for s in self.sentences]
            self._changed()
        return count

    def set_body(self, text: str) -> None:
        self._snapshot()
        parts = [p for p in re.split(r"(?<=[.!?؟۔।])\s+", " ".join(text.split())) if p]
        self.sentences = parts
        self._changed()

    def undo(self) -> bool:
        if not self.history:
            return False
        self.sentences = self.history.pop()
        self._changed()
        return True

    def set_subject(self, subject: str) -> None:
        self.subject = " ".join(subject.split()).strip(" .")[:200]
        self._changed()

    def set_recipients(self, contacts: list[dict]) -> None:
        self.to = contacts
        self._changed()

    def add_attachment(self, attachment: dict) -> None:
        if not any(a["path"] == attachment["path"] for a in self.attachments):
            self.attachments.append(attachment)
        self._changed()

    # -- the confirmation
    def issue_confirmation(self) -> str:
        token = secrets.token_urlsafe(20)
        self.confirm = {"token": token, "hash": self.content_hash(), "at": time.time()}
        self.state = "confirm"
        self.updated_at = time.time()
        return token

    def confirmation_valid(self, token: str | None) -> bool:
        c = self.confirm
        return bool(c and token and hmac.compare_digest(str(c["token"]), str(token)) and c["hash"] == self.content_hash()
                    and time.time() - c["at"] <= CONFIRM_SECONDS)

    def public(self) -> dict:
        """What the screen shows (no tokens)."""
        return {"id": self.id, "provider": self.provider, "state": self.state, "to": self.to, "subject": self.subject, "body": self.body,
                "attachments": [{"name": a["name"], "size": a["size"]} for a in self.attachments], "candidates": self.candidates,
                "pending": (self.pending or {}).get("kind"), "dictating": self.state in DICTATING, "sent": self.sent}


_SENT = {"en": "Email sent to {who}.", "roman_ur": "{who} ko email bhej di.", "ur": "{who} کو ای میل بھیج دی۔", "hi": "{who} को ईमेल भेज दी।"}


def sent_text(to: list[dict], language: str) -> str:
    who = " and ".join(c.get("name") or c.get("email", "") for c in to)
    return _SENT.get(language, _SENT["en"]).format(who=who)


def new_draft(provider: str, language: str = "en") -> Draft:
    return Draft(id=uuid.uuid4().hex[:12], provider=provider, language=language)


def load(db: Session) -> Draft | None:
    row = db.get(Preference, KEY)
    if row is None:
        return None
    try:
        data = json.loads(row.value_json)
        draft = Draft(**data) if data else None
    except (ValueError, TypeError):
        return None
    if draft is None or draft.state in ("sent", "cancelled") or time.time() - draft.updated_at > IDLE_SECONDS:
        return None
    return draft


def load_any(db: Session) -> Draft | None:
    """The stored draft in any state (the verifier of a send reads it after the state became "sent")."""
    row = db.get(Preference, KEY)
    try:
        data = json.loads(row.value_json) if row else None
        return Draft(**data) if data else None
    except (ValueError, TypeError):
        return None


def save(db: Session, draft: Draft | None) -> None:
    row = db.get(Preference, KEY) or Preference(key=KEY)
    row.value_json = json.dumps(asdict(draft) if draft else {})
    db.add(row)
    db.commit()


def clear(db: Session) -> bool:
    had = load(db) is not None
    save(db, None)
    return had
