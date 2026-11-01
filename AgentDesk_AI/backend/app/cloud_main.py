"""AgentDesk "cloud brain": the part of the backend that can run on Render (Linux).

It only thinks and talks: plan a command, answer a chat question, transcribe speech, synthesise speech. It never
runs a tool: every PC action stays on the user's Windows machine, which re-validates each plan against its own
registry, allowed folders and approval rules before anything runs. The Groq keys live only here.

    uvicorn app.cloud_main:app --host 0.0.0.0 --port $PORT

Environment (Render dashboard, never committed):
    CLOUD_API_TOKEN         long random secret every request must send as `Authorization: Bearer ...` (required)
    GROQ_API_KEYS           comma separated Groq keys (used one after another when a limit is hit)
    CLOUD_RATE_PER_MINUTE   requests per minute allowed per token (default 30)
"""
from __future__ import annotations

import logging
import os
import secrets
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

# The cloud brain stores nothing. Some code it shares with the PC app builds a database engine when imported, and a
# DATABASE_URL meant for another service (e.g. postgresql:// without its driver) made every /cloud/plan answer 500.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .ai.base import AIConfigError, AIError, AIRateLimitError
from .config import get_settings
from .nlu.memory import ConversationMemory, Turn
from .nlu.planner import PlannerContext, planner_from_settings

MAX_AUDIO_BYTES = 10 * 1024 * 1024
TTS_MAX_CHARS = 200          # Orpheus accepts at most 200 characters per request
CHAT_MODEL = "openai/gpt-oss-120b"

app = FastAPI(title="AgentDesk Cloud Brain", version="0.2.0", docs_url=None, redoc_url=None)

_hits: dict[str, deque[float]] = {}
_hits_lock = threading.Lock()


def _token() -> str:
    return os.environ.get("CLOUD_API_TOKEN", "").strip()


def _rate_limit() -> int:
    try:
        return max(1, int(os.environ.get("CLOUD_RATE_PER_MINUTE", "30")))
    except ValueError:
        return 30


@app.middleware("http")
async def guard(request: Request, call_next):
    if request.url.path == "/health":
        return await call_next(request)
    token = _token()
    if len(token) < 24:  # fail closed: never serve without a real secret
        return JSONResponse({"detail": "Server has no CLOUD_API_TOKEN (min 24 chars)"}, status_code=503)
    supplied = request.headers.get("authorization", "")
    if not (supplied.startswith("Bearer ") and secrets.compare_digest(supplied[7:], token)):
        return JSONResponse({"detail": "Missing or invalid API token"}, status_code=401)
    now = time.monotonic()
    with _hits_lock:
        q = _hits.setdefault(token, deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= _rate_limit():
            return JSONResponse({"detail": "Too many requests"}, status_code=429, headers={"Retry-After": "30"})
        q.append(now)
    return await call_next(request)


@app.exception_handler(AIRateLimitError)
async def _rate_limited(_request: Request, exc: AIRateLimitError):
    retry = int(getattr(exc, "retry_after", None) or 30)
    return JSONResponse({"detail": str(exc)}, status_code=429, headers={"Retry-After": str(retry)})


@app.exception_handler(AIConfigError)
async def _config_error(_request: Request, exc: AIConfigError):
    return JSONResponse({"detail": str(exc)}, status_code=503)


@app.exception_handler(AIError)
async def _ai_error(_request: Request, exc: AIError):
    return JSONResponse({"detail": str(exc)}, status_code=502)


@app.exception_handler(Exception)
async def _unexpected(request: Request, exc: Exception):
    """Any other failure: log the traceback on the server and tell the (token-holding) caller which error it was, not just "500"."""
    logging.getLogger("agentdesk.cloud").exception("unhandled error on %s", request.url.path)
    return JSONResponse({"detail": f"Cloud brain internal error: {type(exc).__name__}: {str(exc)[:200]}"}, status_code=500)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "keys": len(get_settings().resolved_groq_keys()), "token_set": len(_token()) >= 24}


# ------------------------------------------------------------------ plan
class PlanRequest(BaseModel):
    command: str = Field(min_length=1, max_length=2000)
    language_hint: str | None = Field(default=None, max_length=12)
    memory: list[dict[str, Any]] = Field(default_factory=list, max_length=10)
    desktop: dict[str, Any] = Field(default_factory=dict)


_registry = None


def _planner_registry():
    """Tool schemas only; nothing is ever executed here, and path rules are applied by the client."""
    global _registry
    if _registry is None:
        from .tools.base import ToolContext
        from .tools.registry import ToolRegistry

        base = Path(tempfile.gettempdir())
        _registry = ToolRegistry(ToolContext(allowed_directories=[base], approved_applications=[], test_dir=base))
    return _registry


@app.post("/cloud/plan")
def plan(req: PlanRequest) -> dict[str, Any]:
    registry = _planner_registry()
    memory = ConversationMemory([Turn(**{k: v for k, v in t.items() if k in Turn.__dataclass_fields__})
                                 for t in req.memory])
    planner = planner_from_settings(get_settings())
    planner = getattr(planner, "llm", None) or planner
    if planner is None:
        raise AIConfigError("No planner provider is configured on the server.")
    result = planner.plan(PlannerContext(command=req.command, registry=registry, memory=memory,
                                         desktop=req.desktop, language_hint=req.language_hint, check_paths=False))
    return {"plan": result.plan.model_dump(), "source": result.source, "model": result.model,
            "usage": result.usage, "latency_ms": round(result.latency_ms, 1),
            "valid": result.validation.ok, "errors": result.validation.errors}


# ------------------------------------------------------------------ chat
class ChatRequest(BaseModel):
    messages: list[dict[str, str]] = Field(min_length=1, max_length=24)
    fast: bool = False


@app.post("/cloud/chat")
def chat(req: ChatRequest) -> dict[str, Any]:
    from .ai.groq_client import get_groq_client

    for m in req.messages:
        if m.get("role") not in {"system", "user", "assistant"} or len(m.get("content", "")) > 8000:
            return JSONResponse({"detail": "Invalid message"}, status_code=422)  # type: ignore[return-value]
    model = "openai/gpt-oss-20b" if req.fast else CHAT_MODEL
    out = get_groq_client().chat(req.messages, model=model, temperature=0.4, max_tokens=400)
    return {"content": out["content"], "model": out["model"]}


# ------------------------------------------------------------------ speech
@app.post("/cloud/stt")
async def stt(audio: UploadFile = File(...), language: str | None = Form(default=None),
              prompt: str | None = Form(default=None)) -> dict[str, Any]:
    from .ai.groq_client import get_groq_client

    data = await audio.read(MAX_AUDIO_BYTES + 1)
    if len(data) > MAX_AUDIO_BYTES:
        return JSONResponse({"detail": "Audio too large (10 MB max)"}, status_code=413)  # type: ignore[return-value]
    out = get_groq_client().transcribe(data, filename=audio.filename or "speech.wav",
                                       language=language or None, prompt=(prompt or None) and prompt[:500])
    return {"text": out.get("text", ""), "model": out.get("model", "")}


class TtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=TTS_MAX_CHARS)
    voice: str | None = Field(default=None, max_length=32)


@app.post("/cloud/tts")
def tts(req: TtsRequest) -> Response:
    from .ai.groq_client import get_groq_client

    audio = get_groq_client().speak(req.text, voice=req.voice)
    return Response(content=audio, media_type="audio/wav")
