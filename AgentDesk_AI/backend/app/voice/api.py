"""HTTP endpoints for the voice loop: /voice/stt, /voice/tts, /voice/chunks, /voice/line, /voice/latency."""
from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..ai.base import AIConfigError, AIError, AIRateLimitError
from ..config import get_settings
from ..database import get_db
from . import latency, personas, responses
from .chunking import ORPHEUS_LIMIT
from .tts import TTSChain, VoiceChoice, default_chain

router = APIRouter(prefix="/voice")
MAX_AUDIO_BYTES = 10 * 1024 * 1024

_lock = threading.Lock()
_chain: TTSChain | None = None


def _speech_client():
    """The cloud brain when configured, else the local Groq client; None when neither is available."""
    settings = get_settings()
    if settings.cloud_configured:
        from ..cloud_client import CloudBrain
        return CloudBrain(settings.cloud_brain_url, settings.cloud_brain_token)
    if settings.groq_configured:
        from ..ai.groq_client import get_groq_client
        return get_groq_client()
    return None


def get_chain() -> TTSChain:
    global _chain
    with _lock:
        if _chain is None:
            _chain = default_chain(_speech_client())
        return _chain


def set_chain(chain: TTSChain | None) -> None:
    """Tests inject a fake chain; None resets to the real one."""
    global _chain
    with _lock:
        _chain = chain


class ChunksIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    direction: str | None = Field(default=None, max_length=24)


class TtsIn(BaseModel):
    text: str = Field(min_length=1, max_length=ORPHEUS_LIMIT)
    language: str = "en"
    gender: str = Field(default="female", pattern="^(female|male)$")
    voice_en: str = "diana"
    voice_ur: str | None = None
    voice_hi: str | None = None
    direction: str | None = Field(default=None, max_length=24)


class LineIn(BaseModel):
    event: str | None = None
    task: dict[str, Any] | None = None
    tool: str | None = None
    args: dict[str, Any] = {}
    language: str = "en"
    personality: str = Field(default="assistant", pattern="^(assistant|mentor|friend|coach|custom)$")
    user_name: str = Field(default="", max_length=40)
    formality: int = Field(default=50, ge=0, le=100)
    humour: int = Field(default=50, ge=0, le=100)
    slots: dict[str, Any] = {}


class LatencyIn(BaseModel):
    kind: str = Field(default="local", pattern="^(local|planned|chat)$")
    ms: dict[str, float]


@router.get("/greeting")
def greeting_line(db: Session = Depends(get_db)) -> dict:
    """The daily greeting (time of day, the user's name, reminders due today). `enabled` follows the Settings switch."""
    from datetime import datetime
    from ..preferences import get_preferences
    prefs = get_preferences(db)
    lang = {"ur-PK": "roman", "hi-IN": "hi"}.get(prefs.persona.language, "en")
    text = responses.greeting(datetime.now().hour, lang, prefs.persona.user_name, reminders=0)  # reminders arrive with M6
    return {"enabled": prefs.daily_greeting, "text": text}


@router.get("/catalog")
def catalog() -> dict:
    """Built-in avatars, personalities and the voices that exist for them."""
    return personas.catalog()


@router.post("/chunks")
def chunks(body: ChunksIn) -> dict:
    choice = VoiceChoice(direction=body.direction)
    return {"chunks": get_chain().chunks(body.text, choice), "limit": ORPHEUS_LIMIT}


@router.post("/tts")
def tts(body: TtsIn) -> Response:
    choice = VoiceChoice(language=body.language, gender=body.gender, voice_en=body.voice_en,
                         voice_ur=body.voice_ur, voice_hi=body.voice_hi, direction=body.direction)
    try:
        clip = get_chain().synthesize(body.text, choice)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return Response(clip.audio, media_type=clip.mime, headers={"X-TTS-Engine": clip.engine, "X-TTS-Voice": clip.voice})


@router.post("/line")
def spoken_line(body: LineIn) -> dict:
    """The phrase to speak. A task is turned into its closing line; "Done" appears only for verified tasks."""
    who = responses.Voiceprint(body.personality, body.user_name, body.formality, body.humour)
    if body.task is not None:
        text = responses.line_for_task(body.task, body.language, who)
    elif body.tool:
        text = responses.acknowledge_line(body.tool, body.args, body.language, who)
    elif body.event in responses._T:
        text = responses.line(body.event, body.language, who, **body.slots)
    else:
        raise HTTPException(422, "Give a task, a tool, or a known event.")
    return {"text": text}


def _vocabulary_prompt(prompt: str | None) -> str | None:
    """The browser's hint plus this PC's own app / file / contact names. A broken index only means no extra hint."""
    try:
        from ..nlu.correction_memory import current
        from ..nlu.entity_index import get_index
        from .stt_vocab import build_prompt
        return build_prompt(get_index(), current(), prompt) or prompt
    except Exception:  # noqa: BLE001
        return prompt


@router.post("/stt")
async def stt(audio: UploadFile = File(...), language: str | None = Form(default=None),
              prompt: str | None = Form(default=None)) -> dict:
    data = await audio.read(MAX_AUDIO_BYTES + 1)
    if not data or len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413 if data else 422, "Audio is empty or larger than 10 MB.")
    client = _speech_client()
    if client is None:
        raise HTTPException(503, "No speech service is configured (set GROQ_API_KEYS or CLOUD_BRAIN_URL).")
    name = audio.filename or "speech.wav"
    prompt = _vocabulary_prompt(prompt)
    try:
        if hasattr(client, "transcribe") and client.__class__.__name__ == "CloudBrain":
            try:
                return {"text": client.transcribe(data, name, language, prompt)}
            except (AIError, AIConfigError):  # cloud asleep / unreachable: use this machine's Groq key if it has one
                if not get_settings().groq_configured:
                    raise
                from ..ai.groq_client import get_groq_client
                client = get_groq_client()
        result = client.transcribe(data, filename=name, language=language, prompt=prompt)
        return {"text": result["text"], "language": result.get("language"), "model": result.get("model")}
    except AIRateLimitError as exc:
        raise HTTPException(429, str(exc)) from exc
    except AIConfigError as exc:
        raise HTTPException(503, str(exc)) from exc
    except AIError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/latency", status_code=204)
def report_latency(body: LatencyIn) -> None:
    """The UI reports durations (ms) per stage; no transcript text is accepted or stored."""
    utt = latency.Utterance(body.kind)
    utt.marks = {"speech_end": 0.0, **{k: v / 1000 for k, v in body.ms.items() if k in latency.STAGES}}
    utt.finish()


@router.get("/latency")
def latency_summary(kind: str | None = None) -> dict:
    return {"first_word": latency.summarize(stage="first_word_played", kind=kind),
            "acknowledgement": latency.summarize(stage="first_audio_ready", kind=kind)}
