"""Chat messages by voice (WhatsApp Desktop, Telegram Desktop, Microsoft Teams): the same read-back and "yes, send it" rule as e-mail.

    "In WhatsApp, message Ahmed 'I'm on my way'"  ->  "To Ahmed on WhatsApp: I'm on my way. Shall I send it?"  ->  "yes, send it"
    ->  the chat opens with the text typed, Send is pressed through UI Automation, the chat is read again  ->  "Message sent to Ahmed."

A WhatsApp or Telegram number comes from the Memory page ("Ahmed: +923001234567"); there is no contact-list access, so an unknown name is
asked for. A Teams recipient is found in the connected e-mail account's contacts. Only a sentence that contains "send" (or "bhej") after the
read-back sends; the model cannot plan `send_message` (hidden tool) and the confirmation is tied to the exact app, recipient and text.
Called from `executor._start_task` right after the e-mail flow.
"""
from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from ..ai.base import Plan, PlanAction
from ..config import Settings
from ..mail import contacts as mail_contacts
from ..mail import service as mail_service
from ..mail.models import MailError, NotConnected
from ..models import Task, TaskStatus, utcnow
from ..nlu.normalizer import detect_language
from ..tools import messenger
from ..tools.messaging import contact_number, normalize_phone
from ..tools.registry import ToolRegistry
from .mail_flow import _NO, _YES_SEND, norm

_APP = r"(?P<app>whatsapp|telegram|teams)"
_START = [
    re.compile(rf"^(?:please )?(?:in|on|using|via) {_APP}[, ]+(?:please )?(?:message|text|msg|send(?: a)?(?: message)? to|tell)\s+(?P<rest>.+)$", re.I),
    re.compile(rf"^(?:please )?send (?:a |an )?{_APP}(?: message)? to\s+(?P<rest>.+)$", re.I),
    re.compile(rf"^(?:please )?(?:message|text|msg)\s+(?P<n>\S+)\s+(?:on|in|via|using)\s+{_APP}\s*[:,]?\s+(?P<t2>.+)$", re.I),
    re.compile(rf"^(?:please )?{_APP}\s+(?:message |msg |text |to )?(?P<rest>[A-Za-z؀-ۿ][^,]+)$", re.I),
    re.compile(rf"^(?:please )?(?:message|text|msg)\s+(?P<rest>.+?)\s+(?:on|in|via|using)\s+{_APP}$", re.I),
    re.compile(rf"^(?:please )?tell\s+(?P<rest>.+?)\s+(?:on|in|via|using)\s+{_APP}\s+(?:that\s+)?(?P<t2>.+)$", re.I),
    re.compile(rf"^(?:please )?(?P<n>[\w]+) ko {_APP} (?:karo|kar do|bhejo|likho)(?: ke| ki| ka| ke ki)? (?P<t2>.+)$", re.I),
]
_DEFAULT_START = re.compile(r"^(?:please )?(?:message|text|msg)\s+(?P<rest>.+)$", re.I)


def _split(rest: str, known_names: list[str]) -> tuple[str, str]:
    """"Ahmed 'I am on my way'" / "Ahmed saying I am late" / "Ahmed I am late" -> (name, text)."""
    rest = rest.strip()
    m = re.match(r"""^(?P<n>.+?)\s*[:,]?\s*["“'‘](?P<t>.+?)["”'’]\s*$""", rest)
    if m:
        return m.group("n").strip(), m.group("t").strip()
    m = re.match(r"^(?P<n>.+?)\s+(?:saying|says|that|:|-)\s+(?P<t>.+)$", rest, re.I)
    if m:
        return m.group("n").strip(), m.group("t").strip()
    for name in sorted(known_names, key=len, reverse=True):
        if rest.casefold().startswith(name.casefold() + " "):
            return name, rest[len(name):].strip(" :,-")
    first, _, text = rest.partition(" ")
    return first, text.strip()


def parse_message(command: str) -> tuple[str, str, str] | None:
    """(app, name, text) or None. "message Ahmed ..." without an app means WhatsApp."""
    from ..database import SessionLocal
    from ..models import MemoryItem
    text = " ".join((command or "").split()).strip()
    if not re.search(r"(?i)whatsapp|telegram|teams|message|\btext\b|\bmsg\b", text):
        return None   # nearly every command: no database look-up needed
    with SessionLocal() as db:
        known = sorted({m.group(1) for item in db.query(MemoryItem).all() for m in [re.match(r"^\s*([A-Za-z][\w .'-]{1,30}?)\s*:\s*[+@\d]", item.text)] if m})
    for rx in _START + [_DEFAULT_START]:
        m = rx.match(text.rstrip("."))
        if not m:
            continue
        groups = m.groupdict()
        app = (groups.get("app") or "whatsapp").casefold()
        if groups.get("n") and groups.get("t2"):
            name, body = groups["n"], groups["t2"]
        elif groups.get("rest") and groups.get("t2"):
            name, body = groups["rest"].strip(), groups["t2"]
        else:
            name, body = _split(groups.get("rest", ""), known)
        name, body = name.strip(" ,"), body.strip()
        if not name or not body or len(body) > messenger.MAX_TEXT or len(name) > 40 or name.casefold() in ("me", "myself", "chrome", "app"):
            continue
        if rx is _DEFAULT_START and not (name in known or (name[:1].isupper() and len(body.split()) >= 2) or re.search(r"[\"“'‘]", m.group("rest"))):
            continue   # "text size bigger" is not a message to a person called "size"
        return app, name, body
    return None


SAYS = {
    "confirm_q": {"en": "Shall I send it?", "roman_ur": "Bhej doon?", "ur": "بھیج دوں؟", "hi": "भेज दूँ?"},
    "need_number": {"en": "What is {name}'s number, with the country code?", "roman_ur": "{name} ka number kya hai, country code ke saath?",
                    "ur": "{name} کا نمبر کیا ہے، کنٹری کوڈ کے ساتھ؟", "hi": "{name} का नंबर क्या है, कंट्री कोड के साथ?"},
    "bad_number": {"en": "That is not a valid number. Say it with the country code, like plus 92 300 1234567.",
                   "roman_ur": "Yeh sahi number nahi. Country code ke saath boliye, jaise plus 92 300 1234567.", "ur": "یہ درست نمبر نہیں۔ کنٹری کوڈ کے ساتھ بولیں۔", "hi": "यह सही नंबर नहीं। कंट्री कोड के साथ बोलें।"},
    "say_yes_send": {"en": "Say yes, send it, to send, or no to cancel.", "roman_ur": "Bhejne ke liye haan, bhej do, ya cancel ke liye nahi kahein.",
                     "ur": "بھیجنے کے لیے ہاں، بھیج دو، یا منسوخ کرنے کو نہیں کہیں۔", "hi": "भेजने के लिए हाँ, भेज दो, या रद्द करने के लिए नहीं कहें।"},
    "cancelled": {"en": "Okay, I won't send it.", "roman_ur": "Theek hai, nahi bhejta.", "ur": "ٹھیک ہے، نہیں بھیجتا۔", "hi": "ठीक है, नहीं भेजता।"},
    "no_email": {"en": "I need Teams' e-mail address for {name}: connect e-mail in Settings, or say the address.", "roman_ur": "{name} ka email address chahiye.",
                 "ur": "{name} کا ای میل ایڈریس چاہیے۔", "hi": "{name} का ईमेल पता चाहिए।"},
    "problem": {"en": "{why}", "roman_ur": "{why}", "ur": "{why}", "hi": "{why}"},
}


def say(key: str, lang: str, **slots) -> str:
    return (SAYS[key].get(lang) or SAYS[key]["en"]).format(**slots)


def _finish(db: Session, task: Task, text: str) -> Task:
    task.agent_state_json = json.dumps({"provider": "local", "matcher": "mail", "speak_full": len(text) > 200})
    task.plan_note, task.status, task.error = text, TaskStatus.COMPLETED, None
    task.updated_at = utcnow()
    db.commit()
    db.refresh(task)
    return task


def try_handle(db: Session, task: Task, command: str, registry: ToolRegistry, settings: Settings) -> Task | None:
    draft = messenger.load(db)
    text = norm(command)
    if draft is not None:
        if text in ("stop", "wait", "hold on"):
            return None
        return _continue(db, task, draft, command, text, registry, settings)
    parsed = parse_message(command)
    if parsed is None:
        return None
    app, name, body = parsed
    lang = detect_language(command)
    draft = messenger.MessageDraft(app=app, name=name, target="", text=body, language=lang, state="need_target")
    return _resolve_target(db, task, draft, name, settings)


def _resolve_target(db, task, draft, spoken: str, settings) -> Task:
    lang, app = draft.language, draft.app
    try:
        if app == "teams":
            name = draft.name
            provider = mail_service.chosen_provider(db)
            try:
                found = mail_contacts.resolve(spoken, mail_service.provider_for(provider, settings))
            except (MailError, NotConnected):
                found = None
            if found is None or found.kind != "one":
                direct = mail_contacts.resolve(spoken, _NoContacts()) if spoken else None
                found = direct if direct is not None and direct.kind == "one" else None
            if found is None:
                draft.state = "need_target"
                messenger.save(db, draft)
                return _finish(db, task, say("no_email", lang, name=name))
            draft.target, draft.name = found.contacts[0].email, found.contacts[0].label()
        else:
            said = _spoken_digits(spoken.strip())
            raw = said if said.startswith(("+", "@")) or re.fullmatch(r"[\d\s()+-]{7,20}", said) else (contact_number(draft.name) or (_memory_handle(draft.name) if app == "telegram" else None))
            if not raw:
                draft.state = "need_target"
                messenger.save(db, draft)
                return _finish(db, task, say("need_number", lang, name=draft.name))
            if app == "telegram" and raw.startswith("@"):
                draft.target = raw
            else:
                draft.target = normalize_phone(_spoken_digits(raw))
    except ValueError:
        draft.state = "need_target"
        messenger.save(db, draft)
        return _finish(db, task, say("bad_number", lang))
    draft.state = "confirm"
    draft.issue_confirmation()
    messenger.save(db, draft)
    return _finish(db, task, f"{draft.readback()} {say('confirm_q', lang)}")


def _memory_handle(name: str) -> str | None:
    """A Telegram @username saved on the Memory page ("Sara: @sara_k")."""
    from ..database import SessionLocal
    from ..models import MemoryItem
    wanted = name.strip().casefold()
    with SessionLocal() as db:
        for item in db.query(MemoryItem).all():
            if re.search(rf"(?<!\w){re.escape(wanted)}(?!\w)", item.text.casefold()):
                m = re.search(r"@[A-Za-z0-9_]{4,32}", item.text)
                if m:
                    return m.group(0)
    return None


class _NoContacts:
    def search_contacts(self, query, limit=5):
        return []


def _spoken_digits(raw: str) -> str:
    return re.sub(r"(?i)\bplus\b", "+", raw).replace(" ", "") if re.search(r"(?i)\bplus\b", raw) else raw


def _continue(db, task, draft, raw, text, registry, settings) -> Task:
    lang = draft.language
    if re.match(r"^(?:cancel|never ?mind|discard|don t send(?: it)?|do not send(?: it)?)$", text):
        messenger.save(db, None)
        return _finish(db, task, say("cancelled", lang))
    if draft.state == "need_target":
        return _resolve_target(db, task, draft, raw.strip().rstrip("."), settings)
    if _YES_SEND.match(text):
        return _send_now(db, task, draft, registry, settings)
    if _NO.match(text):
        messenger.save(db, None)
        return _finish(db, task, say("cancelled", lang))
    m = re.match(r"(?is)^\s*(?:change it to|make it|say instead|change the message to)\s+(.+?)\s*$", raw)
    if m and len(m.group(1)) <= messenger.MAX_TEXT:
        draft.text = m.group(1).strip().strip("\"'“”")
        draft.issue_confirmation()
        messenger.save(db, draft)
        return _finish(db, task, f"{draft.readback()} {say('confirm_q', lang)}")
    return _finish(db, task, say("say_yes_send", lang))


def _send_now(db, task, draft, registry, settings) -> Task:
    from . import executor
    token = (draft.confirm or {}).get("token")
    if not draft.confirmation_valid(token):
        draft.issue_confirmation()
        messenger.save(db, draft)
        return _finish(db, task, f"{draft.readback()} {say('confirm_q', draft.language)}")
    plan = Plan(actions=[PlanAction(tool="send_message", args={"app": draft.app, "confirmation": token})], note="")
    task.agent_state_json = json.dumps({"provider": "local", "matcher": "mail"})
    task = executor.plan_task(db, task, registry, settings, auto_approve=False, preplanned=plan)
    if task.status == TaskStatus.AWAITING_APPROVAL:
        executor.approve_task(db, task.id, [a.id for a in task.actions])   # the spoken "yes, send it" after the read-back is the explicit confirmation
        task = executor.run_task(db, task.id, registry, settings)
    return task
