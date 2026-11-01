"""Words about the conversation itself: "stop", "wait", "repeat that", "read it to me", "undo that", and "no, that's all".

They are matched as WHOLE short utterances (a longer sentence is a normal command), in English, Roman Urdu, Urdu and Hindi, before any
command parser. The answer is a finished task with no actions, so the spoken line is exactly the text below, never "Done".
 - stop      halts the task that is running or waiting (the kill switch) and closes the session
 - wait      says it will wait; nothing is cancelled
 - repeat    says the previous reply again; read  = reads the previous result
 - undo      rolls back the last change that has a verified checkpoint (the same mechanism as the Undo button)
 - close     "no / that's all / thanks" after "Anything else?" ends the session; more = "yes" keeps it open
"close" and "more" only count while the assistant has just asked "Anything else?", so a plain "no" elsewhere is never swallowed.
"""
from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Task, TaskStatus, utcnow
from ..nlu.normalizer import detect_language
from ..tools.registry import ToolRegistry
from . import session as sessions

_PUNCT = re.compile(r"[\s.,;:!?،۔।؟…\"'`]+")
_FILLER = re.compile(r"^(?:please|pls|ok|okay|hey|zara|jee|ji)\s+|\s+(?:please|pls|zara|na|yaar)$")

PHRASES: dict[str, set[str]] = {
    "stop": {"stop", "stop it", "stop everything", "stop that", "halt", "cancel", "cancel that", "ruk jao", "ruko", "rukiye", "band ho jao",
             "रुको", "रुक जाओ", "रुक जाइए", "رکو", "رک جاؤ", "رکیے"},
    "wait": {"wait", "hold on", "one second", "just a second", "wait a second", "ek minute", "ek second", "ruko zara", "thehro", "ٹھہرو",
             "رکو ذرا", "रुको ज़रा", "ठहरो", "एक मिनट", "ایک منٹ"},
    "repeat": {"repeat", "repeat that", "say that again", "say it again", "what did you say", "come again", "dobara bolo", "phir se bolo",
               "dohrao", "dobara", "phir bolo", "दोबारा बोलो", "फिर से बोलो", "दोहराओ", "دوبارہ بولو", "پھر سے بولو", "دہراؤ"},
    "read": {"read it to me", "read it", "read that", "read it out", "read it aloud", "read it out loud", "parh ke sunao", "padh ke sunao",
             "sunao", "parho", "पढ़ कर सुनाओ", "पढ़ के सुनाओ", "सुनाओ", "پڑھ کر سناؤ", "سناؤ"},
    "undo": {"undo", "undo that", "undo it", "revert", "revert that", "take that back", "wapas karo", "wapas kar do", "undo karo",
             "isay wapas karo", "वापस करो", "अनडू", "واپس کرو", "اسے واپس کرو"},
    "close": {"no", "nope", "no thanks", "no thank you", "that's all", "thats all", "that's it", "thats it", "that is all", "nothing else",
              "nothing", "i'm good", "im good", "all good", "thanks", "thank you", "ok thanks", "okay thanks", "bas", "bas itna hi", "bas shukriya",
              "bas itna", "nahi", "nahin", "nahi shukriya", "shukriya", "bas ho gaya", "theek hai shukriya", "koi baat nahi",
              "नहीं", "बस", "बस इतना ही", "धन्यवाद", "शुक्रिया", "नहीं धन्यवाद", "نہیں", "بس", "بس اتنا ہی", "شکریہ", "نہیں شکریہ"},
    "more": {"yes", "yeah", "yep", "yes please", "sure", "haan", "han", "ji haan", "haan ji", "ji", "हाँ", "हां", "जी हाँ", "ہاں", "جی ہاں", "جی"},
}
_INDEX = {_PUNCT.sub(" ", p.casefold()).strip(): kind for kind, phrases in PHRASES.items() for p in phrases}

SAYS: dict[str, dict[str, str]] = {
    "stopped": {"en": "Stopped.", "roman_ur": "Ruk gaya.", "ur": "رک گیا۔", "hi": "रुक गया।"},
    "nothing_running": {"en": "Okay, nothing is running.", "roman_ur": "Theek hai, kuch chal nahi raha.", "ur": "ٹھیک ہے، کچھ چل نہیں رہا۔",
                        "hi": "ठीक है, कुछ चल नहीं रहा।"},
    "waiting": {"en": "Okay, I'll wait.", "roman_ur": "Theek hai, main intezar karta hoon.", "ur": "ٹھیک ہے، میں انتظار کرتا ہوں۔",
                "hi": "ठीक है, मैं इंतज़ार करता हूँ।"},
    "nothing_to_repeat": {"en": "I haven't said anything yet.", "roman_ur": "Maine abhi tak kuch nahi kaha.", "ur": "میں نے ابھی تک کچھ نہیں کہا۔",
                          "hi": "मैंने अभी तक कुछ नहीं कहा।"},
    "nothing_to_undo": {"en": "There is nothing I can undo.", "roman_ur": "Aisa kuch nahi hai jo main wapas kar sakoon.",
                        "ur": "ایسا کچھ نہیں جو میں واپس کر سکوں۔", "hi": "ऐसा कुछ नहीं जिसे मैं वापस कर सकूँ।"},
    "undone": {"en": "Undone.", "roman_ur": "Wapas kar diya.", "ur": "واپس کر دیا۔", "hi": "वापस कर दिया।"},
    "undo_failed": {"en": "I couldn't undo that: {why}", "roman_ur": "Main isay wapas nahi kar saka: {why}", "ur": "میں اسے واپس نہیں کر سکا: {why}",
                    "hi": "मैं इसे वापस नहीं कर सका: {why}"},
    "closed": {"en": "Okay, I'm here if you need me.", "roman_ur": "Theek hai, zaroorat ho to bata dena.", "ur": "ٹھیک ہے، ضرورت ہو تو بتا دینا۔",
               "hi": "ठीक है, ज़रूरत हो तो बता देना।"},
    "more": {"en": "Sure, what next?", "roman_ur": "Ji, aur kya karna hai?", "ur": "جی، اور کیا کرنا ہے؟", "hi": "जी, और क्या करना है?"},
}


def parse(command: str, awaiting_more: bool = False) -> str | None:
    """"repeat that" -> "repeat". None for anything that is not exactly such a phrase (close / more need `awaiting_more`)."""
    text = _PUNCT.sub(" ", (command or "").casefold()).strip()
    text = _FILLER.sub("", text).strip()
    kind = _INDEX.get(text)
    if kind in ("close", "more") and not awaiting_more:
        return None
    return kind


def _say(key: str, lang: str, **slots: str) -> str:
    table = SAYS[key]
    return table.get(lang, table["en"]).format(**slots)


def _done(db: Session, task: Task, text: str) -> Task:
    task.agent_state_json = json.dumps({"provider": "local", "matcher": "session"})
    task.plan_note, task.status, task.error = text, TaskStatus.COMPLETED, None
    task.updated_at = utcnow()
    db.commit()
    db.refresh(task)
    return task


def _previous(db: Session, task: Task, limit: int = 12) -> list[Task]:
    """Earlier tasks, newest first, without the session replies themselves (so "repeat" twice repeats the same thing)."""
    rows = db.query(Task).filter(Task.id != task.id).order_by(Task.created_at.desc()).limit(limit).all()
    return [t for t in rows if '"matcher": "session"' not in (t.agent_state_json or "")]


def _spoken(t: Task, lang: str) -> str:
    from ..voice import responses
    from . import executor
    return responses.line_for_task(executor.task_out(t).model_dump(mode="json"), "en" if lang == "en" else lang)


def try_handle(db: Session, task: Task, command: str, registry: ToolRegistry, settings: Settings) -> Task | None:
    from ..ai.browser_commands import browser_plan
    from . import executor
    state = sessions.current(db)
    kind = parse(command, awaiting_more=bool(state and state.awaiting == "anything_else"))
    if kind is None:
        return None
    if kind == "undo" and browser_plan(command) is not None:
        return None
    lang = detect_language(command)
    if kind == "close":
        sessions.close(db)
        return _done(db, task, _say("closed", lang))
    if kind == "more":
        sessions.touch(db)
        if state:
            state.awaiting = None
            sessions._save(db, state)
        return _done(db, task, _say("more", lang))
    if kind == "wait":
        sessions.touch(db)
        return _done(db, task, _say("waiting", lang))
    previous = _previous(db, task)
    if kind == "stop":
        live = [t for t in previous if t.status not in sessions.TERMINAL]
        for t in live:
            executor.cancel_task(db, t.id, registry)
        sessions.close(db)
        return _done(db, task, _say("stopped" if live else "nothing_running", lang))
    if kind in ("repeat", "read"):
        last = previous[0] if previous else None
        if last is None:
            return _done(db, task, _say("nothing_to_repeat", lang))
        text = (last.plan_note if kind == "read" and last.status == TaskStatus.COMPLETED and last.plan_note else None) or _spoken(last, lang)
        sessions.touch(db)
        return _done(db, task, text)
    # undo: the newest finished task that still has a verified checkpoint
    for t in previous[:5]:
        if any(a.rollback_status in ("available", "failed") and a.status == "succeeded" for a in t.actions):
            try:
                executor.rollback_task(db, t.id, registry)
            except ValueError as exc:
                return _done(db, task, _say("undo_failed", lang, why=str(exc)[:120]))
            db.refresh(t)
            failed = [a for a in t.actions if a.rollback_status == "failed"]
            if failed:
                return _done(db, task, _say("undo_failed", lang, why=(failed[0].rollback_detail or "it changed since")[:120]))
            sessions.touch(db)
            return _done(db, task, _say("undone", lang))
    return _done(db, task, _say("nothing_to_undo", lang))
