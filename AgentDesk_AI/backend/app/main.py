"""AgentDesk AI backend: FastAPI application and HTTP API."""
from __future__ import annotations

import os
import platform
import json
import secrets
import threading
import time
import uuid
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session, selectinload

from .config import get_settings
from .runtime import resource_root
from . import usage
from .logging_setup import log_event, setup_logging
from .security.token import get_api_token
from .database import get_db, init_db, SessionLocal
from .models import MemoryItem, Task, TaskStatus, ActionStatus, utcnow
from . import preferences as prefs
from . import metrics  # registers the automatic pilot export
from .orchestration import executor
from .schemas import (
    ActionOut,
    ApprovalRequest,
    ConfigStatusOut,
    HealthOut,
    TaskCreate,
    TaskReply,
    TaskOut,
)
from .tools.base import ToolContext
from .tools.registry import ToolRegistry
from .browser_bridge import router as browser_router, bridge
from .voice import api as voice_api
from .voice.api import router as voice_router
from . import state  # noqa: F401  (registers state-machine guards)
from .nlu.entity_index import get_index  # imported here, not in lifespan: a first import from the start-up threads can deadlock

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    usage.get_plan(settings)  # an unknown plan name stops startup instead of silently meaning "unlimited"
    init_db()
    log_event("server_start")
    threading.Thread(target=voice_api.get_chain, daemon=True).start()  # first spoken line should not pay setup
    from .voice.tts import warm_sapi
    warm_sapi()  # keep the Windows voice loaded: each sentence then takes ~50 ms instead of ~900 ms
    from .search import budget as _budget
    threading.Thread(target=_budget.warm, daemon=True).start()  # tokenizer download (first run only)
    if os.environ.get("AGENTDESK_ENTITY_INDEX", "1") != "0":  # (the same switch keeps tests off the network)
        from .ai import warm
        warm.start()
        get_index().start()  # apps / recent files / folders, rebuilt every 10 minutes in the background
    # The launcher runs one server. Never leave a crashed task showing as active
    # or replay its possibly completed device/file operation after restart.
    with SessionLocal() as db:
        for task in db.query(Task).filter(Task.status == TaskStatus.EXECUTING).all():
            task.status = TaskStatus.FAILED
            task.error = "Server restart se task interrupt hua. Result verify karke nayi command dein."
            task.updated_at = utcnow()
            for action in task.actions:
                if action.status == ActionStatus.RUNNING:
                    action.status = ActionStatus.FAILED
                    action.error = task.error
                    # The change may or may not have happened; no checkpoint was finalized.
                    action.rollback_status = "unsupported"
                    action.rollback_detail = "Interrupted by a restart: the outcome is unknown, so no verified rollback exists."
                elif action.status == ActionStatus.APPROVED:
                    action.status = ActionStatus.SKIPPED
            log_event("task_recovered_after_restart", level=30, task_id=task.id)
        db.commit()
        from .retention import prune

        prune(db, settings)
    if settings.tool_isolation == "process":
        from .orchestration import worker
        # Start one tool worker in the background so the first command does not pay its start-up cost.
        threading.Thread(target=worker.pool.warm, name="tool-worker-warmup", daemon=True).start()
    yield
    if settings.tool_isolation == "process":
        from .orchestration import worker

        worker.pool.shutdown()
    log_event("server_stop")


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
app.include_router(browser_router)
app.include_router(voice_router)
from .mail.api import router as mail_router  # noqa: E402
app.include_router(mail_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list({settings.frontend_origin, "http://localhost:5173", "http://127.0.0.1:5173"}),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


_LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "testserver"}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_PROTECTED_PREFIXES = ("/tasks", "/config", "/tools", "/usage", "/browser/connect-token", "/preferences", "/memory", "/metrics", "/voice", "/mail")


@app.middleware("http")
async def local_request_guard(request: Request, call_next):
    """Block DNS rebinding (unexpected Host) and cross-site state changes (foreign Origin)."""
    started = time.monotonic()
    request_id = uuid.uuid4().hex[:12]
    response = await _guarded(request, call_next)
    response.headers["X-Request-Id"] = request_id
    if request.url.path != "/health" and not request.url.path.startswith(("/assets", "/browser-extension")):
        log_event("http_request", request_id=request_id, method=request.method, path=request.url.path,
                  http_status=response.status_code, duration_ms=int((time.monotonic() - started) * 1000))
    return response


async def _guarded(request: Request, call_next):
    raw_host = request.headers.get("host", "")
    host = "[::1]" if raw_host.startswith("[") else raw_host.rsplit(":", 1)[0]
    if host.lower() not in _LOCAL_HOSTS:
        return JSONResponse({"detail": "Unexpected Host header"}, status_code=403)
    origin = request.headers.get("origin")
    if origin and request.method not in _SAFE_METHODS:
        # Same-origin (the UI served by this very server, whatever its port) plus the dev server.
        allowed = {settings.frontend_origin, "http://localhost:5173", "http://127.0.0.1:5173", "http://127.0.0.1:8000", "http://localhost:8000", "http://" + raw_host}
        if origin not in allowed:
            return JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)
    token = get_api_token()
    if token and request.method != "OPTIONS" and request.url.path.startswith(_PROTECTED_PREFIXES) and request.url.path != "/mail/oauth/callback":
        supplied = request.headers.get("authorization", "")
        if not (supplied.startswith("Bearer ") and secrets.compare_digest(supplied[7:], token)):
            return JSONResponse({"detail": "Missing or invalid API token"}, status_code=401)
    return await call_next(request)


def _registry() -> ToolRegistry:
    return ToolRegistry(ToolContext.from_settings())


# ------------------------------------------------------------------ health
@app.exception_handler(usage.QuotaExceeded)
async def quota_exceeded_handler(request: Request, exc: usage.QuotaExceeded):
    return JSONResponse({"detail": exc.message, "usage": exc.snapshot}, status_code=429)


@app.get("/usage")
def get_usage(db: Session = Depends(get_db)) -> dict:
    """Plan, limits and local usage counters. Contains no command text."""
    return usage.snapshot(db, get_settings())


# ------------------------------------------------------------------ pilot metrics
@app.get("/metrics/tasks.csv")
def task_metrics_csv(days: int | None = None, db: Session = Depends(get_db)):
    """One row per finished task: timings, tools, risk, approvals, verification. No command text or paths."""
    from fastapi.responses import PlainTextResponse

    if days is not None and not 1 <= days <= 365:
        raise HTTPException(status_code=422, detail="days must be 1-365")
    return PlainTextResponse(metrics.export_csv(db, days), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=agentdesk_task_metrics.csv"})


# ------------------------------------------------------------------ preferences & memory
@app.get("/preferences")
def get_preferences_route(db: Session = Depends(get_db)) -> dict:
    return prefs.get_preferences(db).model_dump()


@app.put("/preferences")
def put_preferences(payload: prefs.PreferencesUpdate, db: Session = Depends(get_db)) -> dict:
    updated = prefs.update_preferences(db, payload)
    log_event("preferences_updated", detail=",".join(sorted(payload.model_dump(exclude_none=True))))
    return updated.model_dump()


@app.get("/memory")
def list_memory(db: Session = Depends(get_db)) -> dict:
    current = prefs.get_preferences(db)
    items = db.query(MemoryItem).order_by(MemoryItem.updated_at.desc()).all()
    return {"enabled": current.memory_enabled, "items": [prefs.memory_out(i) for i in items],
            "recent_turns": prefs.recent_turns(db, current), "conversation_cutoff": current.conversation_cutoff}


@app.post("/memory", status_code=201)
def add_memory(payload: prefs.MemoryIn, db: Session = Depends(get_db)) -> dict:
    item = MemoryItem(kind=payload.kind, text=payload.text)
    db.add(item)
    db.commit()
    return prefs.memory_out(item)


@app.put("/memory/{item_id}")
def edit_memory(item_id: str, payload: prefs.MemoryIn, db: Session = Depends(get_db)) -> dict:
    item = db.get(MemoryItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Memory item not found")
    item.kind, item.text = payload.kind, payload.text
    db.commit()
    return prefs.memory_out(item)


@app.delete("/memory")
def delete_all_memory(db: Session = Depends(get_db)) -> dict:
    """Delete every remembered note (the Memory page asks first). Task history is separate."""
    count = db.query(MemoryItem).delete()
    db.commit()
    log_event("memory_deleted_all", count=count)
    return {"deleted": count}


@app.delete("/memory/{item_id}")
def delete_memory(item_id: str, db: Session = Depends(get_db)) -> dict:
    item = db.get(MemoryItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Memory item not found")
    db.delete(item)
    db.commit()
    return {"deleted": item_id}


@app.post("/memory/forget-conversation")
def forget_conversation(db: Session = Depends(get_db)) -> dict:
    """Earlier commands stay in task history but are no longer used as conversation memory."""
    return prefs.forget_conversation(db).model_dump()


@app.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut(status="ok", version=settings.app_version)


def _search_status(s) -> dict[str, bool]:
    from .search.tavily import TavilyProvider
    from .search.wikipedia import WikipediaProvider
    return {"wikipedia": WikipediaProvider(s.wikipedia_contact_email).available(), "tavily": TavilyProvider().available()}


@app.get("/config/status", response_model=ConfigStatusOut)
def config_status() -> ConfigStatusOut:
    # Never expose any API key - only whether one is configured.
    s = get_settings()
    provider = s.ai_provider.strip().lower()
    if s.is_mock_mode:
        ai_configured = True  # mock always works; it is explicitly selected
    elif provider == "groq":
        ai_configured = s.groq_configured
    else:
        ai_configured = bool(s.openai_api_key)
    return ConfigStatusOut(
        ai_provider=s.ai_provider,
        ai_model=s.ai_model,
        ai_configured=ai_configured,
        mock_mode=s.is_mock_mode,
        groq_configured=s.groq_configured,
        speech_ready=s.groq_configured or s.cloud_configured,
        search=_search_status(s),
        agent_max_steps=s.agent_max_steps,
        fallback_provider=s.ai_fallback_provider,
        cloud_mode=s.ai_cloud_mode,
        browser_connected=len(bridge.clients) == 1,
        browser_profiles=len(bridge.clients),
        fallback_configured=bool((s.ai_fallback_provider == "gemini" and s.gemini_api_key) or (s.ai_fallback_provider == "openai" and s.openai_api_key)),
        allowed_directories=[str(p) for p in s.resolved_allowed_directories()],
        approved_applications=s.resolved_approved_applications(),
        platform=platform.system(),
        plan=usage.get_plan(s)[0],
    )


@app.get("/tools", response_model=list[dict])
def list_tools() -> list[dict]:
    return _registry().metadata()


# ------------------------------------------------------------------ tasks
@app.post("/tasks", response_model=TaskOut)
def create_task(payload: TaskCreate, db: Session = Depends(get_db)) -> TaskOut:
    # start_task branches on the provider: static planning for mock,
    # the agentic tool-calling loop for Groq.
    task = executor.start_task(
        db, payload.command, payload.idempotency_key, _registry(), get_settings(),
    )
    return executor.task_out(task)


@app.get("/tasks", response_model=list[TaskOut])
def list_tasks(limit: int = Query(default=20, le=100), db: Session = Depends(get_db)) -> list[TaskOut]:
    # selectinload fetches every task's actions in one extra query instead of one per task.
    tasks = db.query(Task).options(selectinload(Task.actions)).order_by(Task.created_at.desc()).limit(limit).all()
    return [executor.task_out(t) for t in tasks]


@app.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(task_id: str, db: Session = Depends(get_db)) -> TaskOut:
    try:
        return executor.task_out(executor._get_task(db, task_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/tasks/{task_id}/approve", response_model=TaskOut)
def approve_task(task_id: str, payload: ApprovalRequest, db: Session = Depends(get_db)) -> TaskOut:
    try:
        return executor.task_out(executor.approve_task(db, task_id, payload.action_ids))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/tasks/{task_id}/reject", response_model=TaskOut)
def reject_task(task_id: str, payload: ApprovalRequest, db: Session = Depends(get_db)) -> TaskOut:
    try:
        return executor.task_out(executor.reject_task(db, task_id, payload.action_ids))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/tasks/{task_id}/run", response_model=TaskOut)
def run_task(task_id: str, db: Session = Depends(get_db)) -> TaskOut:
    try:
        task = executor._get_task(db, task_id)
        if task.agent_state_json and json.loads(task.agent_state_json).get("provider") != "local":
            # Agentic-loop task: resume the loop (runs newly-approved actions,
            # feeds results back to the model, continues reasoning).
            from .ai.planner import get_provider

            provider = get_provider(get_settings())
            return executor.task_out(
                executor.continue_agent_task(db, task_id, _registry(), provider, get_settings())
            )
        return executor.task_out(executor.run_task(db, task_id, _registry(), get_settings()))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/tasks/{task_id}/pause", response_model=TaskOut)
def pause_task(task_id: str, db: Session = Depends(get_db)) -> TaskOut:
    try:
        return executor.task_out(executor.pause_task(db, task_id, _registry()))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/tasks/{task_id}/rollback", response_model=TaskOut)
def rollback_task(task_id: str, db: Session = Depends(get_db)) -> TaskOut:
    try:
        return executor.task_out(executor.rollback_task(db, task_id, _registry()))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/tasks/{task_id}/cancel", response_model=TaskOut)
def cancel_task(task_id: str, db: Session = Depends(get_db)) -> TaskOut:
    try:
        return executor.task_out(executor.cancel_task(db, task_id, _registry()))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# Re-export for tests that import from app.main
__all__ = ["app", "ActionOut"]


@app.post("/tasks/{task_id}/reply", response_model=TaskOut)
def reply_task(task_id: str, payload: TaskReply, db: Session = Depends(get_db)) -> TaskOut:
    """Bind conversational approval to the precise task the user is viewing."""
    try:
        task = executor._get_task(db, task_id)
        if task.status not in {"awaiting_approval", "awaiting_input"}:
            raise ValueError("This task is no longer waiting for a reply. Refresh before responding.")
        message = payload.message.strip().casefold().replace(",", "").rstrip(".!۔।؟?").strip()
        yes = {"yes run it", "run it", "go ahead", "yes go ahead", "yes", "yes do it", "yes please", "confirm", "approve", "do it", "okay", "ok", "haan", "han", "haan kar do", "ji haan", "हाँ", "हां", "हाँ कर दो", "हां कर दो", "जी हाँ कर दो", "ہاں", "جی ہاں", "ہاں کر دو", "جی ہاں کر دو"}
        no = {"no", "cancel", "cancel it", "stop", "don't", "do not", "nahi", "nahin", "nahi karo", "नहीं", "नहीं करो", "रहने दो", "نہیں", "نہیں کرو", "رہنے دو"}
        if message in yes and task.status == "awaiting_approval":
            pending = [a for a in task.actions if a.status == ActionStatus.PENDING]
            # Rule 3: a spoken/typed "yes" may approve low/medium risk only. High risk
            # (delete, overwrite, download, input) needs the on-screen Approve click.
            if any(a.risk == "high" for a in pending):
                task.plan_note = ("High-risk action: on-screen Approve button dabayein. Voice/typed 'haan' sirf "
                                  "medium-risk actions approve karta hai. (High risk needs a click on Approve.)")
                db.commit()
                log_event("voice_approval_refused_high_risk", task_id=task.id, count=len(pending))
                return executor.task_out(task)
            ids = [a.id for a in pending]
            approved = executor.approve_task(db, task_id, ids)
            if approved.status != TaskStatus.APPROVED:
                return executor.task_out(approved)
            return run_task(task_id, db)
        if message in no:
            return executor.task_out(executor.cancel_task(db, task_id))
        # A clear new local command supersedes an unanswered clarification.
        # Do not reinterpret 'mute PC' as a missing folder path.
        from .ai.local_commands import local_plan
        from .ai.browser_commands import browser_plan
        from .ai.window_commands import close_request
        from .ai.command_intents import canonical_command
        routed = canonical_command(payload.message)
        explicit = local_plan(routed) or browser_plan(routed)
        if (explicit is not None and explicit.actions) or close_request(routed) is not None:
            executor.cancel_task(db, task_id)
            return executor.task_out(executor.start_task(db, payload.message, None, _registry(), get_settings()))
        if task.status == "awaiting_input" or (task.agent_state_json and json.loads(task.agent_state_json).get("provider") == "local"):
            from .orchestration.local_agent import correction_plan
            corrected = executor.create_task(db, payload.message)
            plan = correction_plan(corrected, task, payload.message, _registry())
            if plan is not None:
                executor.cancel_task(db, task_id)
                corrected.agent_state_json = corrected.agent_state_json or json.dumps({"provider": "local"})
                corrected = executor.plan_task(db, corrected, _registry(), get_settings(), preplanned=plan)
                if corrected.status == TaskStatus.APPROVED:
                    corrected = executor.run_task(db, corrected.id, _registry(), get_settings())
                return executor.task_out(corrected)
            # Remove the unused placeholder before normal conversational processing.
            db.delete(corrected)
            db.commit()
        if task.status == "awaiting_input":
            return executor.task_out(executor.start_task(db, payload.message, None, _registry(), get_settings()))
        # An unresolved correction must not be wrapped as a new prose command.
        # Keep the proposal waiting so a later exact path can still correct it.
        task.plan_note = 'Correction clear nahi hui. Sahi target ka exact path ya poori nayi command batayein; purana action execute nahi hua.'
        db.commit()
        return executor.task_out(task)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# Register LAST so API routes retain priority over the frontend file server.
frontend_dist = resource_root() / "frontend" / "dist"
browser_extension = resource_root() / "browser-extension"
if browser_extension.is_dir():
    app.mount("/browser-extension", StaticFiles(directory=str(browser_extension), html=True), name="browser-extension")
if (frontend_dist / "index.html").is_file():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
