"""`send_message`: send a chat message in WhatsApp Desktop, Telegram Desktop or Microsoft Teams after the read-back and the user's yes.

Same rules as e-mail (see mail/draft.py and tools/mail.py): hidden from the model, needs the server-issued confirmation for the exact text
and recipient, checked afterwards by looking at the chat. How it works: the app is opened on the chat with the text already typed (a deep link:
`whatsapp://send`, `tg://msg`, `msteams:/l/chat`), then the Send button is pressed through UI Automation (U4) and the chat is read again: the
message must appear in the conversation and the text box must be empty. If any step cannot be confirmed, the answer says so; "sent" is never assumed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import asdict, dataclass, field
from urllib.parse import quote

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..models import Preference
from ..verification import failed_result, verdict
from . import platform as _platform
from .base import BaseTool, RiskLevel, ToolContext, ToolResult, Verification

KEY = "msg_draft"
CONFIRM_SECONDS = 600
IDLE_SECONDS = 900
MAX_TEXT = 1000
SEND_NAMES = ("send", "send message", "send now")

APPS = {
    "whatsapp": {"label": "WhatsApp", "window": "WhatsApp", "target": "phone"},
    "telegram": {"label": "Telegram", "window": "Telegram", "target": "phone_or_user"},
    "teams": {"label": "Microsoft Teams", "window": "Teams", "target": "email"},
}


def deep_link(app: str, target: str, text: str) -> str:
    if app == "whatsapp":
        return f"whatsapp://send?phone={target}&text={quote(text, safe='')}"
    if app == "telegram":
        to = target if target.startswith("@") else "+" + target.lstrip("+")
        return f"tg://msg?text={quote(text, safe='')}&to={quote(to, safe='@+')}"
    return f"msteams:/l/chat/0/0?users={quote(target, safe='@')}&message={quote(text, safe='')}"


@dataclass
class MessageDraft:
    app: str
    name: str
    target: str
    text: str
    language: str = "en"
    state: str = "confirm"
    confirm: dict | None = None
    sent: dict | None = None
    updated_at: float = field(default_factory=time.time)

    def content_hash(self) -> str:
        return hashlib.sha256(json.dumps([self.app, self.target, self.text], ensure_ascii=False).encode()).hexdigest()

    def issue_confirmation(self) -> str:
        token = secrets.token_urlsafe(20)
        self.confirm = {"token": token, "hash": self.content_hash(), "at": time.time()}
        self.updated_at = time.time()
        return token

    def confirmation_valid(self, token: str | None) -> bool:
        c = self.confirm
        return bool(c and token and hmac.compare_digest(str(c["token"]), str(token)) and c["hash"] == self.content_hash() and time.time() - c["at"] <= CONFIRM_SECONDS)

    def readback(self) -> str:
        text = self.text if self.text[-1:] in ".!?؟۔।" else self.text + "."
        return f"To {self.name} on {APPS[self.app]['label']}: {text}"


def load(db: Session) -> MessageDraft | None:
    row = db.get(Preference, KEY)
    try:
        data = json.loads(row.value_json) if row else None
        draft = MessageDraft(**data) if data else None
    except (ValueError, TypeError):
        return None
    if draft is None or draft.sent or time.time() - draft.updated_at > IDLE_SECONDS:
        return None
    return draft


def load_any(db: Session) -> MessageDraft | None:
    row = db.get(Preference, KEY)
    try:
        data = json.loads(row.value_json) if row else None
        return MessageDraft(**data) if data else None
    except (ValueError, TypeError):
        return None


def save(db: Session, draft: MessageDraft | None) -> None:
    row = db.get(Preference, KEY) or Preference(key=KEY)
    row.value_json = json.dumps(asdict(draft) if draft else {})
    db.add(row)
    db.commit()


class SendMessageArgs(BaseModel):
    app: str = Field(pattern="^(whatsapp|telegram|teams)$")
    confirmation: str = Field(min_length=8, max_length=80)


class SendMessageTool(BaseTool):
    name = "send_message"
    description = "Send the confirmed chat message (internal: only after the read-back and the user's yes)."
    risk_level = RiskLevel.HIGH
    args_schema = SendMessageArgs
    hidden_from_model = True
    isolation = "thread"

    def execute(self, args: BaseModel) -> ToolResult:
        from ..database import SessionLocal
        from .uia import get_service
        from .uia_backend import UiaError
        a = SendMessageArgs.model_validate(args.model_dump())
        with SessionLocal() as db:
            draft = load(db)
            if draft is None or draft.app != a.app:
                return ToolResult(success=False, error="That message is not waiting to be sent any more. Nothing was sent.")
            if not draft.confirmation_valid(a.confirmation):
                return ToolResult(success=False, error="I have not read this message back and heard your yes for its current text. Nothing was sent.")
            spec = APPS[draft.app]
            try:
                _platform.open_path_with_os(deep_link(draft.app, draft.target, draft.text))
            except OSError as exc:
                return ToolResult(success=False, error=f"Could not open {spec['label']}: {exc}")
            service = get_service()
            try:
                window = self._wait_window(service, spec["window"])
                button = self._find_send(service, window)
                draft.sent = {"at": time.time(), "app": draft.app}     # recorded BEFORE the click: a retry can never send twice
                save(db, draft)
                service.backend.click(window, button)
            except UiaError as exc:
                if draft.sent:
                    return ToolResult(success=False, error=f"I pressed Send in {spec['label']} but could not confirm it: {exc} Please check the chat.")
                return ToolResult(success=False, error=f"{spec['label']} is open with the message typed in, but I could not press Send: {exc} Press Send yourself.")
            return ToolResult(success=True, output={"app": draft.app, "to": draft.name, "text_chars": len(draft.text), "window": window.title,
                                                    "spoken": f"Message sent to {draft.name}."})

    @staticmethod
    def _wait_window(service, title: str, seconds: float = 12.0):
        from .uia_backend import UiaError
        deadline = time.monotonic() + seconds
        while True:
            try:
                return service.window(title)
            except UiaError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)

    @staticmethod
    def _find_send(service, window):
        from .uia_backend import UiaError
        deadline = time.monotonic() + 8
        while True:
            snap = service.snapshot(window, fresh=True)
            for c in snap.controls:
                if c.type in ("Button", "SplitButton") and c.name.casefold().strip() in SEND_NAMES and c.enabled:
                    return c
            if time.monotonic() > deadline:
                raise UiaError("There is no Send button yet.")
            time.sleep(0.5)

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        if not result.success:
            return failed_result(result)
        from ..database import SessionLocal
        from .uia import get_service
        out = result.output
        with SessionLocal() as db:
            draft = load_any(db)
        if draft is None:
            return Verification(passed=False, checks=["draft found"], details="The message record is gone, so the chat cannot be checked.")
        service = get_service()
        probe = draft.text[:40].casefold()
        deadline = time.monotonic() + 6
        shown = empty_box = False
        while True:
            try:
                window = service.window(APPS[draft.app]["window"])
                snap = service.snapshot(window, fresh=True)
            except Exception as exc:  # noqa: BLE001
                return Verification(passed=False, checks=["chat read again"], details=f"I could not read the chat: {str(exc)[:80]}")
            shown = any(c.type in ("Text", "ListItem", "DataItem", "Document") and probe in f"{c.name} {c.value or ''}".casefold() and not c.focused for c in snap.controls)
            boxes = [c for c in snap.controls if c.type in ("Edit", "Document") and c.focused]
            empty_box = all(not (c.value or "").strip() for c in boxes)
            if (shown and empty_box) or time.monotonic() > deadline:
                break
            time.sleep(0.5)
        return verdict([("the message appears in the chat", shown), ("the text box is empty again", empty_box)],
                       {"app": draft.app, "to": out.get("to"), "window": out.get("window"), "chat_shows_it": shown},
                       details=None if shown else "I could not see the message in the chat. Please check it.")


def messenger_tools(ctx: ToolContext | None = None) -> list[BaseTool]:
    return [SendMessageTool(ctx)]
