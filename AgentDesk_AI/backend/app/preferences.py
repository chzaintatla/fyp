"""User preferences and the editable memory store.

Preferences hold no secrets: API keys stay in the environment / Credential
Manager and are never readable or writable through the API (rule 6).

The risk policy can only make approvals STRICTER than the server default
(rule 3): "standard" = server policy, "strict" = every action, including low
risk, waits for approval. There is no looser option.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from .models import MemoryItem, Preference, Task, utcnow
from .voice import personas


class Persona(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="Agent X", min_length=1, max_length=40)
    language: Literal["ur-PK", "en-US", "en-GB", "hi-IN"] = "ur-PK"
    style: Literal["friendly", "brief", "formal"] = "friendly"
    speak_replies: bool = True
    orb_color: Literal["indigo", "emerald", "rose", "amber", "slate"] = "indigo"
    # M3: character. Changes how it looks, sounds and words things; never what it does (rule 3.9).
    user_name: str = Field(default="", max_length=40)
    avatar_id: Literal["nova", "aria", "zara", "orion", "atlas", "kai", "custom"] = "aria"   # catalog index 1: the default character
    show_avatar: bool = True                      # False = orb only
    gender: Literal["female", "male", "neutral"] = "female"
    voice_en: str = "hannah"      # Aria's voice (personas.AVATARS)
    voice_ur: str = "ur-PK-UzmaNeural"
    voice_hi: str = "hi-IN-SwaraNeural"
    personality: Literal["assistant", "mentor", "friend", "coach", "custom"] = "assistant"
    custom_description: str = Field(default="", max_length=500)
    formality: int = Field(default=50, ge=0, le=100)
    humour: int = Field(default=50, ge=0, le=100)
    verbosity: int = Field(default=50, ge=0, le=100)
    wake_word: str = Field(default="", max_length=30)

    @field_validator("voice_en")
    @classmethod
    def _english_voice(cls, value: str) -> str:
        if value not in personas.ORPHEUS_VOICES:
            raise ValueError(f"voice_en must be one of {', '.join(personas.ORPHEUS_VOICES)}")
        return value

    @field_validator("voice_ur", "voice_hi")
    @classmethod
    def _edge_voice(cls, value: str) -> str:
        if not personas.EDGE_VOICE.match(value):
            raise ValueError("expected a neural voice name such as ur-PK-UzmaNeural")
        return value

    @field_validator("user_name", "wake_word", "custom_description")
    @classmethod
    def _tidy(cls, value: str) -> str:
        return " ".join(value.split()) if value else ""


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persona: Persona = Field(default_factory=Persona)
    listen_mode: Literal["push_to_talk", "hands_free", "off"] = "push_to_talk"
    provider_mode: Literal["auto", "local_only"] = "auto"
    risk_policy: Literal["standard", "strict"] = "standard"
    memory_enabled: bool = True
    onboarded: bool = False
    follow_up_seconds: int = Field(default=10, ge=0, le=15)  # keep listening this long after a spoken reply
    anything_else: bool = True                    # after a verified result ask "Anything else?" and keep the conversation open
    speech_volume: int = Field(default=100, ge=0, le=100)
    cloud_answers: bool | None = None             # None = not asked yet; True = questions/web answers may use the cloud AI
    daily_greeting: bool = False                  # speak a greeting the first time the app opens each day
    conversation_cutoff: str | None = None   # ISO time; older turns are not used as memory


class PreferencesUpdate(BaseModel):
    """Partial update; unknown keys (e.g. an API key) are rejected with 422."""

    model_config = ConfigDict(extra="forbid")

    persona: Persona | None = None
    listen_mode: Literal["push_to_talk", "hands_free", "off"] | None = None
    provider_mode: Literal["auto", "local_only"] | None = None
    risk_policy: Literal["standard", "strict"] | None = None
    memory_enabled: bool | None = None
    onboarded: bool | None = None
    follow_up_seconds: int | None = Field(default=None, ge=0, le=15)
    anything_else: bool | None = None
    speech_volume: int | None = Field(default=None, ge=0, le=100)
    daily_greeting: bool | None = None
    cloud_answers: bool | None = None


def get_preferences(db: Session) -> Preferences:
    rows = {row.key: json.loads(row.value_json) for row in db.query(Preference).all()}
    data = {k: v for k, v in rows.items() if k in Preferences.model_fields}
    try:
        return Preferences.model_validate(data)
    except ValueError:
        return Preferences()  # a corrupted row never blocks the app; defaults are the safe policy


def update_preferences(db: Session, patch: PreferencesUpdate) -> Preferences:
    for key, value in patch.model_dump(exclude_none=True).items():
        row = db.get(Preference, key) or Preference(key=key)
        row.value_json = json.dumps(value)
        row.updated_at = utcnow()
        db.add(row)
    db.commit()
    return get_preferences(db)


def _set(db: Session, key: str, value: Any) -> None:
    row = db.get(Preference, key) or Preference(key=key)
    row.value_json = json.dumps(value)
    db.add(row)
    db.commit()


def forget_conversation(db: Session) -> Preferences:
    _set(db, "conversation_cutoff", utcnow().isoformat())
    return get_preferences(db)


# ------------------------------------------------------------------ memory items
_SECRET = re.compile(r"(gsk_[A-Za-z0-9]{8,}|sk-[A-Za-z0-9_-]{12,}|AIza[0-9A-Za-z_-]{16,}|"
                     r"(?i:password|passcode|api[ _-]?key|token|pin)\s*[:=]?\s*\S{4,})|"
                     r"(?i:\b(?:passwords?|passcodes?|passwd|api[ _-]?keys?|secret keys?|cvv|otp|pin(?: code| number)?|card number)\b)")


class MemoryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["fact", "preference", "alias"] = "fact"
    text: str = Field(min_length=1, max_length=500)

    @field_validator("text")
    @classmethod
    def no_secrets(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("text must not be blank")
        if _SECRET.search(value):
            raise ValueError("Memory must not hold passwords, PINs or API keys.")
        return value


def memory_out(item: MemoryItem) -> dict[str, Any]:
    return {"id": item.id, "kind": item.kind, "text": item.text,
            "created_at": item.created_at.isoformat(), "updated_at": item.updated_at.isoformat()}


def recent_turns(db: Session, prefs: Preferences, limit: int = 10) -> list[dict[str, Any]]:
    """The last turns the planner may use as conversation memory (after any 'forget')."""
    query = db.query(Task).order_by(Task.created_at.desc())
    if prefs.conversation_cutoff:
        query = query.filter(Task.created_at > datetime.fromisoformat(prefs.conversation_cutoff))
    return [{"task_id": t.id, "command": t.command, "status": t.status, "created_at": t.created_at.isoformat()}
            for t in query.limit(limit).all()]
