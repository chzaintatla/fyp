"""Rollout of the strict-JSON planner (Phase 15) next to the older parsers.

PLANNER_MODE (backend/.env):
  legacy      (default) older parsers and the Groq agent loop only.
  shadow      the live path is unchanged; for each command the older local parsers understood, the LLM planner is
              also asked in a background thread and the two plans are compared in `shadow_plans.jsonl`.
              One cloud call per such command: opt-in only, and it needs a Groq key.
  structured  commands the local parsers did not understand are planned by the strict-JSON planner; if it fails or
              its plan cannot be used, the older Groq agent path runs exactly as before.
In EVERY mode, a command nothing local understood is first labelled by the planner (`routing_plan`) so questions, memory
commands and summaries are routed by intent; this is one extra cloud call and never happens in Local-only mode.

Privacy: a shadow row holds a short hash of the command, never its text, paths or arguments. Risk and approvals
are untouched: `plan_task` applies the server policy to every action whichever planner produced it.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from ..ai.base import AIError, Plan
from ..nlu.schema import STEP_REF

MODES = ("legacy", "shadow", "structured")


def mode(settings) -> str:
    value = (getattr(settings, "planner_mode", "legacy") or "legacy").strip().lower()
    return value if value in MODES else "legacy"


def _enabled(settings) -> bool:
    return bool(getattr(settings, "groq_configured", False) or getattr(settings, "cloud_configured", False) or getattr(settings, "gemini_api_key", None)
                or getattr(settings, "openai_api_key", None))


def shadow_path() -> Path:
    override = os.environ.get("AGENTDESK_PILOT_DIR")
    if override:
        return Path(override) / "shadow_plans.jsonl"
    from ..logging_setup import log_dir

    return log_dir().parent / "pilot" / "shadow_plans.jsonl"


def _signature(plan: Plan) -> list[tuple[str, dict[str, Any]]]:
    return [(a.tool, {k: v for k, v in a.args.items() if v is not None}) for a in plan.actions]


def compare(legacy: Plan, candidate: Plan | None) -> dict[str, Any]:
    old = _signature(legacy)
    new = _signature(candidate) if candidate is not None else None
    return {
        "legacy_tools": [t for t, _ in old],
        "planner_tools": [t for t, _ in new] if new is not None else None,
        "tools_match": new is not None and [t for t, _ in old] == [t for t, _ in new],
        "args_match": new is not None and old == new,
    }


def _planner_plan(command: str, registry, settings, llm_only: bool = False):
    """Run the strict planner. Returns (legacy Plan | None, info dict). Never raises."""
    from ..nlu.planner import PlannerContext, planner_from_settings

    info: dict[str, Any] = {"source": "none", "model": "", "latency_ms": 0.0, "error": ""}
    started = time.perf_counter()
    try:
        planner = planner_from_settings(settings)
        if llm_only and hasattr(planner, "llm") and planner.llm is not None:
            planner = planner.llm
        result = planner.plan(PlannerContext(command=command, registry=registry))
    except AIError as exc:
        info["error"] = type(exc).__name__
        info["latency_ms"] = (time.perf_counter() - started) * 1000
        return None, info
    info.update(source=result.source, model=result.model, latency_ms=result.latency_ms or (time.perf_counter() - started) * 1000)
    plan = result.plan
    if not result.validation.ok:
        info["error"] = "validation"
        return None, info
    if any(isinstance(v, str) and STEP_REF.match(v) for s in plan.steps for v in s.args.values()):
        info["error"] = "unresolved_reference"
        return None, info
    if plan.needs_clarification and not plan.steps:
        return Plan(note=plan.clarifying_question), info
    if not plan.steps and plan.intent not in {"chat", "unsupported", "clarify"}:
        info["error"] = "empty"
        return None, info
    return plan.to_legacy(), info


def structured_plan(command: str, registry, settings) -> Plan | None:
    """The plan for `structured` mode, or None to fall back to the older path."""
    if mode(settings) != "structured" or not _enabled(settings):
        return None
    plan, _info = _planner_plan(command, registry, settings)
    if plan is None or (not plan.actions and not plan.note and plan.intent != "chat"):
        return None
    if plan.actions:
        plan.note = plan.note or "Planner (strict JSON) ne plan tayyar kiya."
    return plan


ROUTED_INTENTS = {"chat", "memory", "summarise", "web_answer"}
ROUTING_BACKOFF_SECONDS = 300.0
_routing_blocked_until = 0.0  # monotonic time; set after a failed routing call so a broken planner is not retried per command


def routing_plan(command: str, registry, settings) -> Plan | None:
    """The planner's plan when its intent is a conversation intent (chat / memory / summarise / web_answer), in ANY mode.

    Asked only for commands the regex fast path and the local parsers left undecided, and only on the path where a cloud
    call was already allowed (not in Local-only or explicit-cloud mode). Any other intent returns None and the caller
    continues exactly as before; the plan is then discarded.
    """
    global _routing_blocked_until
    if not _enabled(settings) or time.monotonic() < _routing_blocked_until:
        return None
    plan, info = _planner_plan(command, registry, settings)
    if info.get("error") and plan is None:
        # Rate limit, oversized prompt (HTTP 413 on the free tier), cloud brain down...: stop asking for a while.
        _routing_blocked_until = time.monotonic() + ROUTING_BACKOFF_SECONDS
        return None
    _routing_blocked_until = 0.0
    return plan if plan is not None and plan.intent in ROUTED_INTENTS else None


def _record(row: dict[str, Any]) -> None:
    path = shadow_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _shadow_worker(command: str, legacy: Plan, registry, settings) -> None:
    try:
        candidate, info = _planner_plan(command, registry, settings, llm_only=True)
        _record({"ts": time.time(), "cmd": hashlib.sha256(command.encode("utf-8")).hexdigest()[:12],
                 **compare(legacy, candidate), "source": info["source"], "model": info["model"],
                 "latency_ms": round(info["latency_ms"], 1), "error": info["error"]})
    except Exception:  # shadow mode must never affect a task
        pass


def shadow(command: str, legacy: Plan, registry, settings, wait: bool = False) -> threading.Thread | None:
    if mode(settings) != "shadow" or not _enabled(settings) or not legacy.actions:
        return None
    thread = threading.Thread(target=_shadow_worker, args=(command, legacy, registry, settings), daemon=True)
    thread.start()
    if wait:
        thread.join()
    return thread
