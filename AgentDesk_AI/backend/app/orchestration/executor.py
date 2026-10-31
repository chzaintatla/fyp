"""Execution orchestration: plan -> approve -> execute -> verify -> report.

Two flows share this module:

1. Static planning (mock provider): plan_task() plans everything up front,
   then run_task() executes the approved actions. Used by tests.
2. Agentic loop (Groq provider): start_agent_task() / continue_agent_task().
   The model proposes one tool call at a time; the backend validates it, the
   permission engine checks risk, low-risk tools execute immediately while
   medium/high-risk ones pause for user approval, and each result is fed
   back to the model for further reasoning. Loop state persists in
   Task.agent_state_json so it can pause and resume across HTTP requests.

State machine (tasks):
  created -> planned/awaiting_approval/approved -> executing -> completed/failed
  awaiting_approval -> approved (all pending approved) | rejected | cancelled
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import time

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..ai.base import AIConfigError, AIError, AIRateLimitError, Plan
from ..ai.planner import get_provider, plan_command
from ..ai.tool_selection import tool_metadata
from ..config import Settings
from ..models import Action, ActionStatus, Task, TaskStatus, utcnow
from ..schemas import ActionOut, TaskOut
from ..security.permissions import needs_approval
from ..tools.base import RiskLevel, ToolResult, Verification
from ..tools.desktop import DesktopControlTool
from ..tools.registry import ToolRegistry, UnknownToolError
from . import rollback
from ..logging_setup import log_event
from .. import usage


# ---------------------------------------------------------------- serialization
def _action_out(a: Action) -> ActionOut:
    return ActionOut(
        id=a.id,
        seq=a.seq,
        tool_name=a.tool_name,
        args=json.loads(a.args_json or "{}"),
        risk=a.risk,
        status=a.status,
        result=json.loads(a.result_json) if a.result_json else None,
        verification=json.loads(a.verification_json) if a.verification_json else None,
        error=a.error,
        rollback_status=a.rollback_status,
        rollback_detail=a.rollback_detail,
    )


def task_out(task: Task) -> TaskOut:
    state = _agent_state(task)  # parsed once; it can be large
    return TaskOut(
        id=task.id,
        command=task.command,
        status=task.status,
        plan_note=task.plan_note,
        error=task.error,
        created_at=task.created_at,
        updated_at=task.updated_at,
        retry_at=state.get("retry_at"),
        provider_note=state.get("provider_note"),
        partial=_partial_summary(task),
        actions=[_action_out(a) for a in sorted(task.actions, key=lambda x: x.seq)],
    )


def _partial_summary(task: Task) -> dict | None:
    """Honest record of what already happened when a task did not fully complete."""
    if task.status not in (TaskStatus.FAILED, TaskStatus.PARTIALLY_COMPLETED, TaskStatus.CANCELLED, TaskStatus.REJECTED, TaskStatus.PAUSED):
        return None
    done = [a for a in task.actions if a.status == ActionStatus.SUCCEEDED]
    if not done:
        return None
    return {
        "succeeded": len(done),
        "not_run": len([a for a in task.actions if a.status in (ActionStatus.SKIPPED, ActionStatus.CANCELLED, ActionStatus.PENDING, ActionStatus.APPROVED, ActionStatus.PAUSED)]),
        "failed": len([a for a in task.actions if a.status == ActionStatus.FAILED]),
        "changes_remain": [a.tool_name for a in done if a.tool_name in rollback.SUPPORTED_TOOLS or a.risk != "low"],
        "rollback_available": [a.id for a in done if a.rollback_status == "available"],
    }


def _get_task(db: Session, task_id: str) -> Task:
    task = db.query(Task).filter(Task.id == task_id).first()
    if task is None:
        raise KeyError(f"Task '{task_id}' not found")
    return task


def _strict_policy(db: Session) -> bool:
    from ..preferences import get_preferences

    return get_preferences(db).risk_policy == "strict"


def _risk_for_task_action(tool, parsed_args, command: str) -> RiskLevel:
    if isinstance(tool, DesktopControlTool):
        return tool.risk_for_user_command(parsed_args, command)
    return tool.risk_for_args(parsed_args)


# ------------------------------------------------------------------ lifecycle
def create_or_get_task(db: Session, command: str, idempotency_key: str | None = None) -> tuple[Task, bool]:
    """Create a task, or return the existing one for this idempotency key.

    Returns (task, created). `created` is True only for the one request that actually inserted
    the row: when two requests race with the same key, the database's unique constraint lets
    exactly one insert win and the other receives the winner's task with created=False, so only
    the winner plans or runs it.
    """
    if idempotency_key:
        existing = db.query(Task).filter(Task.idempotency_key == idempotency_key).first()
        if existing:
            return existing, False
    task = Task(command=command, idempotency_key=idempotency_key, status=TaskStatus.CREATED)
    db.add(task)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        winner = db.query(Task).filter(Task.idempotency_key == idempotency_key).first() if idempotency_key else None
        if winner is None:
            raise
        return winner, False
    db.refresh(task)
    return task, True


def create_task(db: Session, command: str, idempotency_key: str | None = None) -> Task:
    """Create a task; an existing idempotency key returns the original task."""
    return create_or_get_task(db, command, idempotency_key)[0]


def plan_task(db: Session, task: Task, registry: ToolRegistry, settings: Settings, auto_approve: bool = False, preplanned: Plan | None = None) -> Task:
    """Generate + validate a plan and persist the planned actions."""
    try:
        if preplanned is None:
            provider = get_provider(settings)
            plan: Plan = plan_command(task.command, registry, provider)
        else:
            plan = preplanned
            for action in plan.actions:
                registry.get(action.tool).validate_args(action.args)
    except (AIError, ValidationError, UnknownToolError) as exc:
        task.status = TaskStatus.FAILED
        task.error = str(exc)
        task.updated_at = utcnow()
        db.commit()
        db.refresh(task)
        return task

    # Meta-command: report the previous task's result.
    if plan.note == "__PREVIOUS_TASK__":
        prev = (
            db.query(Task)
            .filter(Task.id != task.id, Task.status.in_([TaskStatus.COMPLETED, TaskStatus.FAILED]))
            .order_by(Task.created_at.desc())
            .first()
        )
        if prev:
            done = sum(1 for a in prev.actions if a.status == ActionStatus.SUCCEEDED)
            task.plan_note = (
                f"Previous task '{prev.command}' finished with status '{prev.status}': "
                f"{done}/{len(prev.actions)} actions succeeded."
            )
        else:
            task.plan_note = "There is no previous finished task yet."
        task.status = TaskStatus.COMPLETED
        db.commit()
        db.refresh(task)
        return task

    # Clarification / informational plans carry no actions.
    if not plan.actions:
        task.plan_note = plan.note or "No actions were planned for this command."
        task.status = TaskStatus.AWAITING_INPUT if preplanned is not None else TaskStatus.COMPLETED
        db.commit()
        db.refresh(task)
        return task

    task.plan_note = plan.note
    if getattr(plan, "mock", False):
        prefix = "[mock AI] "
        task.plan_note = (prefix + (task.plan_note or "")).strip()
    any_pending = False
    for seq, action in enumerate(plan.actions):
        tool = registry.get(action.tool)
        parsed = tool.validate_args(action.args)
        risk = _risk_for_task_action(tool, parsed, task.command)
        strict = _strict_policy(db)  # user setting: approve every action (never looser than the server)
        status = ActionStatus.PENDING if needs_approval(risk) or strict else ActionStatus.APPROVED
        if auto_approve and not strict:
            status = ActionStatus.APPROVED
        any_pending = any_pending or status == ActionStatus.PENDING
        db.add(
            Action(
                task_id=task.id,
                seq=seq,
                tool_name=action.tool,
                args_json=json.dumps(action.args),
                risk=risk.value,
                status=status,
            )
        )
    task.status = TaskStatus.AWAITING_APPROVAL if any_pending else TaskStatus.APPROVED
    db.commit()
    db.refresh(task)
    if auto_approve and task.status == TaskStatus.APPROVED and task.actions:
        return run_task(db, task.id, registry, settings)
    return task


def approve_task(db: Session, task_id: str, action_ids: list[str] | None = None) -> Task:
    """Approve pending actions.

    Approving "all" (action_ids is None) never covers HIGH-risk actions: those
    must be named explicitly so the user confirms each one individually.
    Approval is bound to the action's stored hash and expires for medium/high risk.
    """
    from ..config import get_settings
    from ..security.approvals import binding_for, expiry_for

    task = _get_task(db, task_id)
    if task.status not in (TaskStatus.AWAITING_APPROVAL, TaskStatus.APPROVED, TaskStatus.PLANNED):
        raise ValueError(f"Task '{task_id}' is '{task.status}' and cannot be approved now")
    targets = [a for a in task.actions if a.status == ActionStatus.PENDING]
    if action_ids is not None:
        wanted = set(action_ids)
        targets = [a for a in targets if a.id in wanted]
    else:
        targets = [a for a in targets if a.risk != RiskLevel.HIGH.value]
    now = utcnow()
    ttl = get_settings().approval_ttl_seconds
    for a in targets:
        if binding_for(a) != a.binding_hash:
            a.status = ActionStatus.REJECTED
            a.error = "Action was altered after it was planned; approval refused."
            continue
        a.status = ActionStatus.APPROVED
        a.approved_at = now
        a.approval_expires_at = expiry_for(a.risk, ttl, now)
    if any(a.status == ActionStatus.REJECTED and a.error and "altered" in a.error for a in targets):
        task.status = TaskStatus.REJECTED
        task.error = "An action changed after planning; nothing was run."
    else:
        remaining = [a for a in task.actions if a.status == ActionStatus.PENDING]
        task.status = TaskStatus.APPROVED if not remaining else TaskStatus.AWAITING_APPROVAL
    task.updated_at = utcnow()
    db.commit()
    db.refresh(task)
    log_event("task_approved", task_id=task.id, status=task.status, count=len(targets))
    return task


def reject_task(db: Session, task_id: str, action_ids: list[str] | None = None) -> Task:
    task = _get_task(db, task_id)
    if task.status not in (TaskStatus.AWAITING_APPROVAL, TaskStatus.APPROVED, TaskStatus.PLANNED):
        raise ValueError(f"Task '{task_id}' is '{task.status}' and cannot be rejected now")
    targets = [a for a in task.actions if a.status in (ActionStatus.PENDING, ActionStatus.APPROVED)]
    if action_ids is not None:
        wanted = set(action_ids)
        targets = [a for a in targets if a.id in wanted]
    for a in targets:
        a.status = ActionStatus.REJECTED
    task.status = TaskStatus.REJECTED
    task.updated_at = utcnow()
    db.commit()
    db.refresh(task)
    return task


def cancel_task(db: Session, task_id: str, registry: ToolRegistry | None = None) -> Task:
    task = _get_task(db, task_id)
    if task.status in (TaskStatus.COMPLETED, TaskStatus.PARTIALLY_COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
        return task  # already terminal; nothing to cancel
    for a in task.actions:
        if a.status in (ActionStatus.PENDING, ActionStatus.APPROVED, ActionStatus.PAUSED):
            if a.status == ActionStatus.PAUSED and registry is not None:
                tool = registry.get(a.tool_name)
                tool.on_cancel(tool.validate_args(json.loads(a.args_json or "{}")))  # e.g. delete .part
            a.status = ActionStatus.CANCELLED
    _stop_running(task, registry, "cancel")
    task.status = TaskStatus.CANCELLED
    task.updated_at = utcnow()
    db.commit()
    db.refresh(task)
    return task


# ------------------------------------------------------------------ execution
STOP_MESSAGES = {
    "timeout": "Stopped after {limit}s: the tool process was killed ({killed} process(es)). Check the real state before retrying.",
    "pause": "Paused: the running step was stopped ({killed} process(es) killed). Resume continues from the last verified step.",
    "cancel": "Cancelled: the running step was stopped ({killed} process(es) killed).",
    "crashed": "The tool process exited unexpectedly; nothing was confirmed.",
}


def _run_and_verify(tool, parsed_args, settings: Settings, action_id: str | None = None) -> tuple[ToolResult, Verification, str | None]:
    """Execute and verify one action. Returns (result, verification, stop_reason).

    Process isolation (default): both run in a worker process that is killed on
    timeout, pause or cancel, so a stopped step really stops.
    """
    limit = tool.timeout_seconds or settings.tool_timeout_seconds
    if settings.tool_isolation == "process" and tool.isolation == "process":
        from . import worker

        control = worker.register(action_id) if action_id else None
        try:
            outcome = worker.run_tool(tool, parsed_args, limit, control)
        finally:
            if action_id:
                worker.unregister(action_id)
        if outcome.stopped:
            message = STOP_MESSAGES[outcome.stopped].format(limit=limit, killed=len(outcome.killed_pids))
            result = ToolResult(success=False, error=message,
                                output={"stopped": outcome.stopped, "timed_out": outcome.stopped == "timeout",
                                        "killed_pids": outcome.killed_pids})
            return result, Verification(passed=False, checks=[f"stopped: {outcome.stopped}"], details=message), outcome.stopped
        result = ToolResult.model_validate(outcome.result)
        verification = (Verification.model_validate(outcome.verification) if outcome.verification
                        else Verification(passed=False, checks=[], details=result.error))
        return result, verification, None
    result = _run_with_timeout(tool, parsed_args, limit)
    if result.success:
        verification = tool.verify(parsed_args, result)
    else:
        verification = Verification(passed=False, checks=[], details=result.error)
    return result, verification, ("timeout" if (result.output or {}).get("timed_out") else None)


def _run_with_timeout(tool, parsed_args, timeout_seconds: int) -> ToolResult:
    limit = timeout_seconds
    # No `with` block: its exit would wait for the worker and defeat the timeout.
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(tool.execute, parsed_args)
    try:
        return future.result(timeout=limit)
    except concurrent.futures.TimeoutError:
        # A Python thread cannot be killed, so the tool may still finish its side
        # effect. Say so instead of claiming it was stopped.
        return ToolResult(
            success=False,
            output={"timed_out": True},
            error=f"Tool timed out after {limit}s and may still be running; verify the actual state before retrying.",
        )
    finally:
        pool.shutdown(wait=False)


def run_task(db: Session, task_id: str, registry: ToolRegistry, settings: Settings) -> Task:
    """Execute approved actions in order, verifying each one.

    Duplicate-execution prevention: terminal tasks are returned unchanged,
    and a task is only runnable from an approved state with no pending actions.
    """
    task = _get_task(db, task_id)
    if task.status in (TaskStatus.COMPLETED, TaskStatus.PARTIALLY_COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.REJECTED):
        return task  # already finished; never re-execute
    if task.status == TaskStatus.PAUSED:
        refused = _resume_from_checkpoint(db, task, registry)
        if refused is not None:
            return refused
    pending = [a for a in task.actions if a.status == ActionStatus.PENDING]
    if pending:
        raise ValueError(
            f"Task '{task_id}' still has {len(pending)} action(s) awaiting approval"
        )
    runnable = [a for a in sorted(task.actions, key=lambda x: x.seq) if a.status == ActionStatus.APPROVED]
    if not runnable and task.status not in (TaskStatus.APPROVED,):
        raise ValueError(f"Task '{task_id}' has no approved actions to run")

    task.status = TaskStatus.EXECUTING
    if task.plan_note and task.plan_note.startswith("Task pause hai"):
        task.plan_note = None
    task.updated_at = utcnow()
    db.commit()

    failed = False
    for action in runnable:
        # Refresh: a cancel may have landed between actions.
        db.refresh(task)
        if task.status == TaskStatus.CANCELLED:
            action.status = ActionStatus.CANCELLED
            db.commit()
            break
        if _agent_state(task).get("pause_requested"):
            state = _agent_state(task)
            state.pop("pause_requested", None)
            task.agent_state_json = json.dumps(state)
            task.status = TaskStatus.PAUSED
            task.plan_note = "Task pause hai. Baqi approved actions nahi chali; Resume dabayein."
            task.updated_at = utcnow()
            db.commit()
            db.refresh(task)
            return task
        tool = registry.get(action.tool_name)
        parsed = tool.validate_args(json.loads(action.args_json or "{}"))
        _execute_single_action(db, action, tool, parsed, settings)
        if action.status == ActionStatus.PAUSED:
            task.status = TaskStatus.PAUSED
            task.plan_note = "Task pause hai. Chalta hua step roka gaya; Resume us step ko wahin se aage chalayega."
            task.updated_at = utcnow()
            db.commit()
            db.refresh(task)
            return task
        if action.status == ActionStatus.CANCELLED:
            break
        if action.status == ActionStatus.SUCCEEDED:
            _save_checkpoint(db, task, action)
        if action.status == ActionStatus.FAILED:
            failed = True
            break  # never continue blindly after a failure

    if any(a.status == ActionStatus.RUNNING for a in runnable):
        # Another request owns a still-running action; do not report a result yet.
        db.refresh(task)
        return task

    if failed:
        for a in runnable:
            if a.status == ActionStatus.APPROVED:
                a.status = ActionStatus.SKIPPED
        verified_before = any(a.status == ActionStatus.SUCCEEDED for a in task.actions)
        task.status = TaskStatus.PARTIALLY_COMPLETED if verified_before else TaskStatus.FAILED
        failed_action = next((a for a in runnable if a.status == ActionStatus.FAILED), None)
        task.error = failed_action.error if failed_action else "One or more actions failed; remaining actions were skipped."
        if failed_action and json.loads(failed_action.result_json or "{}").get("output", {}).get("needs_confirmation"):
            task.status = TaskStatus.AWAITING_INPUT
            task.plan_note = task.error
            task.error = None
    elif task.status == TaskStatus.CANCELLED:
        pass  # keep cancellation
    else:
        task.status = TaskStatus.COMPLETED
        task.error = None
        if _agent_state(task).get("provider") == "local" and not _agent_state(task).get("workflow"):
            last = next((a for a in reversed(runnable) if a.status == ActionStatus.SUCCEEDED), None)
            if last:
                output = json.loads(last.result_json or "{}").get("output", {})
                if last.tool_name == "manage_system_setting":
                    setting = output.get("setting", "Setting")
                    if "value" in output:
                        task.plan_note = f"{setting.capitalize()} ab {output['value']}% hai."
                    elif "muted" in output:
                        task.plan_note = "Volume mute kar diya." if output["muted"] else "Volume unmute kar diya."
                    elif "enabled" in output:
                        task.plan_note = "Bluetooth on hai." if output["enabled"] else "Bluetooth off hai."
                    else:
                        task.plan_note = f"Windows/app theme: {output.get('system', '')}/{output.get('apps', '')}."
                elif last.tool_name == "application_package":
                    task.plan_note = output.get("details", "Package operation complete.")[:6000]
                elif last.tool_name == "control_browser":
                    if output.get("tabs") is not None:
                        task.plan_note = "\n".join(f"tab {t['id']}: {t.get('title', '')}" for t in output["tabs"]) or "Koi tab nahi mila."
                    else:
                        task.plan_note = {"pause": "YouTube video pause kar di.", "resume": "YouTube video resume kar di.", "close": "Requested tab band kar diya."}.get(output.get("action"), "Browser command complete.")
                elif last.tool_name == "play_youtube_video":
                    task.plan_note = output.get("message", "YouTube page khol diya; playback verify karein.")
                else:
                    task.plan_note = f"Kaam complete: {last.tool_name.replace('_', ' ')}."
    task.updated_at = utcnow()
    db.commit()
    db.refresh(task)
    return task


# ------------------------------------------------------------------ shared
def _execute_single_action(db: Session, action: Action, tool, parsed, settings: Settings) -> None:
    """Run one approved action with timeout, then verify it. Commits.

    The action is claimed with an atomic approved->running update, so two
    concurrent requests can never both execute it.
    """
    from ..security.approvals import ApprovalError, check_before_execution

    try:
        check_before_execution(action)
    except ApprovalError as exc:
        action.status = ActionStatus.FAILED
        action.error = str(exc)
        db.commit()
        log_event("action_refused", level=30, task_id=action.task_id, action_id=action.id, tool=action.tool_name, detail=str(exc))
        return
    claimed = (
        db.query(Action)
        .filter(Action.id == action.id, Action.status == ActionStatus.APPROVED)
        .update({Action.status: ActionStatus.RUNNING}, synchronize_session=False)
    )
    db.commit()
    db.refresh(action)
    if claimed != 1:
        return  # another request already owns (or finished) this action
    log_event("action_start", task_id=action.task_id, action_id=action.id, tool=action.tool_name, risk=action.risk)
    usage.record(db, "action", action.task_id)
    try:
        pre = rollback.capture(tool.name, parsed, tool.ctx) if tool.name in rollback.SUPPORTED_TOOLS else {}
    except rollback.CheckpointError as exc:
        action.status = ActionStatus.FAILED
        action.error = f"Not run: {exc}"
        db.commit()
        return
    result, verification, stop_reason = _run_and_verify(tool, parsed, settings, action.id)
    action.result_json = result.model_dump_json()
    if stop_reason in ("pause", "cancel"):
        action.verification_json = verification.model_dump_json()
        action.status = ActionStatus.PAUSED if stop_reason == "pause" else ActionStatus.CANCELLED
        action.error = result.error
        if stop_reason == "cancel":
            tool.on_cancel(parsed)
        db.commit()
        log_event("action_end", level=30, task_id=action.task_id, action_id=action.id, tool=action.tool_name,
                  status=action.status, detail=result.error)
        return
    if result.success and verification.passed and not verification.evidence:
        # A pass with nothing observed is not a verification.
        verification = Verification(passed=False, checks=verification.checks + ["no evidence recorded"],
                                    details="Verifier returned no evidence of the real outcome.")
    action.verification_json = verification.model_dump_json()
    if result.success and verification.passed:
        action.status = ActionStatus.SUCCEEDED
        if tool.name in rollback.SUPPORTED_TOOLS:
            plan = rollback.finalize(tool.name, parsed, result.output, pre, tool.ctx)
            action.rollback_json = json.dumps(plan) if plan else None
            action.rollback_status = "available" if plan else "unsupported"
        elif tool.risk_level != RiskLevel.LOW:
            action.rollback_status = "unsupported"  # e.g. system settings, desktop input, launches
    else:
        action.status = ActionStatus.FAILED
        action.error = result.error or f"Verification failed: {'; '.join(verification.checks)}"
    db.commit()
    log_event("action_end", level=20 if action.status == ActionStatus.SUCCEEDED else 30, task_id=action.task_id,
              action_id=action.id, tool=action.tool_name, status=action.status,
              detail=action.error if action.status != ActionStatus.SUCCEEDED else None)


# ------------------------------------------------------------------ agentic loop (Groq)
def _agent_state(task: Task) -> dict:
    if task.agent_state_json:
        try:
            return json.loads(task.agent_state_json)
        except json.JSONDecodeError:
            pass
    return {}


def _save_agent_state(db: Session, task: Task, state: dict) -> None:
    task.agent_state_json = json.dumps(state)
    task.updated_at = utcnow()
    db.commit()


def _fingerprint(tool_name: str, args: dict) -> str:
    return hashlib.sha256(
        f"{tool_name}:{json.dumps(args, sort_keys=True)}".encode()
    ).hexdigest()


def _tool_result_message(call_id: str, ok: bool, detail: str) -> dict:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps({"success": ok, "detail": detail[:16000]}),
    }


def _action_summary(action: Action) -> str:
    parts = []
    if action.result_json:
        try:
            output = json.loads(action.result_json).get("output", {})
            parts.append("output=" + json.dumps(output)[:14000])
        except (json.JSONDecodeError, AttributeError):
            pass
    if action.verification_json:
        try:
            v = json.loads(action.verification_json)
            parts.append(
                "verification_passed=" + str(v.get("passed"))
                + "; checks=" + "; ".join(v.get("checks", []))
            )
        except (json.JSONDecodeError, AttributeError):
            pass
    if action.error:
        parts.append("error=" + action.error[:500])
    return "; ".join(parts) or "done"


_NLU_FALLBACK_INTENTS_SKIP = {"clarify", "chat", "unsupported"}


def _nlu_fallback(command: str, registry: ToolRegistry) -> Plan | None:
    """Ask the Phase 15 local matcher only when the older parser had no answer.

    Used only for a confident, fully valid plan without unresolved step references (find-then-act plans
    stay with the older parser, which resolves paths with the user). Risk and approval are unchanged:
    plan_task applies the server policy to every action.
    """
    from ..nlu import THRESHOLD, match, validate_plan
    from ..nlu.schema import STEP_REF

    result = match(command)
    plan = result.plan
    if plan is None or result.confidence < THRESHOLD or plan.intent in _NLU_FALLBACK_INTENTS_SKIP or not plan.steps:
        return None
    if any(isinstance(v, str) and STEP_REF.match(v) for s in plan.steps for v in s.args.values()):
        return None
    if not validate_plan(plan, registry).ok:
        return None
    legacy = plan.to_legacy()
    legacy.note = "Local agent ne plan tayyar kiya; cloud API use nahi hui."
    return legacy


def start_task(
    db: Session, command: str, idempotency_key: str | None,
    registry: ToolRegistry, settings: Settings,
    auto_approve: bool = False,
) -> Task:
    """Create a task and start the right flow for the configured provider."""
    from . import planner_mode

    is_duplicate = bool(idempotency_key) and db.query(Task).filter(Task.idempotency_key == idempotency_key).first() is not None
    if not is_duplicate:
        usage.check_task(db, settings)  # raises QuotaExceeded before anything is created
    task, created = create_or_get_task(db, command, idempotency_key)
    if not created:
        return task  # idempotency key matched a task made by an earlier or concurrent request
    usage.record(db, "task", task.id)
    if settings.ai_provider.strip().lower() != "mock":
        from .local_agent import prepare_local_request
        local = prepare_local_request(db, task, registry)
        if local is not None:
            planner_mode.shadow(command, local, registry, settings)
            if not task.agent_state_json:
                task.agent_state_json = json.dumps({"provider": "local"})
            task = plan_task(db, task, registry, settings, auto_approve=auto_approve, preplanned=local)
            if task.status == TaskStatus.APPROVED:
                return run_task(db, task.id, registry, settings)
            return task
        local = _nlu_fallback(command, registry)
        if local is not None:
            planner_mode.shadow(command, local, registry, settings)
            # The Phase 15 matcher understood what the older local parser did not (e.g. write/overwrite a file).
            task.agent_state_json = json.dumps({"provider": "local", "matcher": "nlu"})
            task = plan_task(db, task, registry, settings, auto_approve=auto_approve, preplanned=local)
            if task.status == TaskStatus.APPROVED:
                return run_task(db, task.id, registry, settings)
            return task
        from ..preferences import get_preferences

        if get_preferences(db).provider_mode == "local_only":
            return plan_task(db, task, registry, settings, preplanned=Plan(note="Local-only mode is on (Settings): cloud AI is not used. Say the app/file name and the action, or switch Provider to Auto."))
        if settings.ai_cloud_mode != "automatic" and not command.strip().casefold().startswith(("ai:", "research ", "explain ")):
            return plan_task(db, task, registry, settings, preplanned=Plan(note="Local agent ko yeh command clear nahi hui. App/file ka naam aur operation batayein. Detailed AI reasoning ke liye 'AI:' ya 'research' se request shuru karein."))
    structured = planner_mode.structured_plan(command, registry, settings)
    if structured is not None:
        task.agent_state_json = json.dumps({"provider": "local", "matcher": "structured"})
        task = plan_task(db, task, registry, settings, auto_approve=auto_approve, preplanned=structured)
        if task.status == TaskStatus.APPROVED:
            return run_task(db, task.id, registry, settings)
        return task
    try:
        provider = get_provider(settings)
    except AIError as exc:
        # No silent fallback: a misconfigured provider fails the task loudly.
        task.status = TaskStatus.FAILED
        task.error = str(exc)
        task.updated_at = utcnow()
        db.commit()
        db.refresh(task)
        return task
    from ..ai.groq_provider import GroqProvider

    if isinstance(provider, GroqProvider):
        return start_agent_task(db, task, registry, provider, settings, auto_approve=auto_approve)
    return plan_task(db, task, registry, settings, auto_approve=auto_approve)


_CONTENT_KEYS = {"content", "text", "body", "stdout", "stderr", "details", "html", "data", "message"}


def _compact_observation(value, key: str = ""):
    """Minimise what earlier tool output is re-sent to a cloud model.

    Keeps paths, ids and short facts needed to resolve "yes, that one", but
    never re-sends file contents or long text, and bounds list and string size.
    """
    if isinstance(value, dict):
        return {k: _compact_observation(v, k) for k, v in list(value.items())[:20]}
    if isinstance(value, list):
        return [_compact_observation(v, key) for v in value[:10]]
    if isinstance(value, str):
        if key in _CONTENT_KEYS and len(value) > 80:
            return f"[omitted {len(value)} chars]"
        return value if len(value) <= 300 else value[:300] + "..."
    return value


def _session_user_message(db: Session, task: Task) -> str:
    """Keep a short conversation memory so confirmations continue the work."""
    previous = (
        db.query(Task)
        .filter(Task.id != task.id)
        .order_by(Task.created_at.desc())
        .limit(4)
        .all()
    )
    if not previous:
        return task.command
    blocks = []
    for prior in reversed(previous):
        reply = (prior.plan_note or prior.error or prior.status or "").strip()
        if not prior.command:
            continue
        observations = []
        for action in sorted(prior.actions, key=lambda a: a.seq)[-6:]:
            observations.append({"tool": action.tool_name, "args": json.loads(action.args_json or "{}"),
                                 "status": action.status, "result": _compact_observation(json.loads(action.result_json or "{}"))})
        blocks.append(f"User: {prior.command}\nAssistant: {reply[:2000] or prior.status}\nObserved tools: "
                      + json.dumps(observations, ensure_ascii=False)[:2000])
    return (
        "Recent conversation (if the user is confirming a suggested app, file, "
        "folder, window, or next step, perform that original work now):\n"
        + "\n\n".join(blocks)
        + f"\n\nLatest user message: {task.command}"
    )


def start_agent_task(
    db: Session, task: Task, registry: ToolRegistry, provider, settings: Settings,
    auto_approve: bool = False,
) -> Task:
    """Run the agentic loop until the model finishes or approval is needed."""
    from ..ai.groq_provider import GroqProvider, SYSTEM_PROMPT

    assert isinstance(provider, GroqProvider)
    specs = GroqProvider.build_tool_specs(tool_metadata(registry, task.command))
    state = {
        "provider": "groq",
        "auto_approve": auto_approve,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _session_user_message(db, task)},
        ],
        "fingerprints": [],
        "rejections_reported": [],
        "steps": 0,
    }
    task.status = TaskStatus.EXECUTING
    db.commit()
    try:
        return _agent_loop(db, task, registry, provider, settings, specs, state)
    except AIError as exc:
        # Groq failure: fail loudly, never fall back to mock.
        if isinstance(exc, AIRateLimitError):
            task.status = TaskStatus.WAITING_RETRY
            state["retry_at"] = time.time() + exc.retry_after
            task.plan_note = getattr(exc, "user_note", None) or "AI quota ki wajah se task pause hai. Reset time ke baad Resume dabayein; simple volume/brightness commands abhi bhi chalengi."
            task.error = None
        else:
            task.status = TaskStatus.FAILED
            task.error = str(exc)
        _save_agent_state(db, task, state)
        db.refresh(task)
        return task


def _agent_loop(db, task, registry, provider, settings, specs, state) -> Task:
    max_steps = settings.agent_max_steps
    while state["steps"] < max_steps:
        db.refresh(task)
        if task.status == TaskStatus.CANCELLED:
            _save_agent_state(db, task, state)
            return task
        # May raise AIError -> caught by the caller, task fails loudly.
        usage.check_ai(db, settings)  # AIQuotaExceeded -> task waits (waiting_retry) until the period resets
        try:
            resp = provider.complete(state["messages"], tools=specs)
        except AIConfigError:
            raise  # nothing was sent to a provider
        except Exception:
            usage.record(db, "ai_call", task.id)  # a failed request still consumed provider quota
            raise
        usage.record(db, "ai_call", task.id)
        state.pop("retry_at", None)
        if getattr(provider, "notice", None):
            state["provider_note"] = provider.notice
        state["steps"] += 1
        tool_calls = resp.get("tool_calls") or []
        if not tool_calls:
            # Model finished (or is asking the user a question).
            task.plan_note = resp.get("content") or "Done."
            state["messages"].append({"role": "assistant", "content": task.plan_note})
            task.status = TaskStatus.COMPLETED
            task.error = None
            _save_agent_state(db, task, state)
            db.refresh(task)
            return task
        state["messages"].append(
            {
                "role": "assistant",
                "content": resp.get("content"),
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["raw_arguments"]},
                        **({"extra_content": tc["extra_content"]} if tc.get("extra_content") else {}),
                    }
                    for tc in tool_calls
                ],
            }
        )
        paused = False
        for tc in tool_calls:
            # Validate independently of the model - never trust it blindly.
            if tc.get("invalid_arguments"):
                # Unparseable arguments must not silently become {} (defaults could run).
                state["messages"].append(
                    _tool_result_message(tc["id"], False, "Tool arguments were not valid JSON; call rejected. Retry with valid arguments or ask the user.")
                )
                continue
            try:
                tool = registry.get(tc["name"])
            except UnknownToolError as exc:
                state["messages"].append(
                    _tool_result_message(tc["id"], False, f"Unknown tool rejected: {exc}")
                )
                continue
            try:
                parsed = tool.validate_args(tc["arguments"])
            except ValidationError as exc:
                state["messages"].append(
                    _tool_result_message(tc["id"], False, f"Invalid arguments rejected: {exc}")
                )
                continue
            fp = _fingerprint(tc["name"], tc["arguments"])
            if fp in state["fingerprints"]:
                state["messages"].append(
                    _tool_result_message(tc["id"], True, "Already executed - not repeated.")
                )
                continue
            risk = _risk_for_task_action(tool, parsed, task.command)
            seq = len(task.actions)
            strict = _strict_policy(db)
            auto_approved = state.get("auto_approve", False) and not strict
            if (needs_approval(risk) or strict) and not auto_approved:
                db.add(
                    Action(
                        task_id=task.id,
                        seq=seq,
                        tool_name=tc["name"],
                        args_json=json.dumps(tc["arguments"]),
                        risk=risk.value,
                        status=ActionStatus.PENDING,
                        call_id=tc["id"],
                    )
                )
                task.status = TaskStatus.AWAITING_APPROVAL
                paused = True
                continue  # still answer every other tool_call id this turn
            action = Action(
                task_id=task.id,
                seq=seq,
                tool_name=tc["name"],
                args_json=json.dumps(tc["arguments"]),
                risk=risk.value,
                status=ActionStatus.APPROVED,
                call_id=tc["id"],
            )
            db.add(action)
            db.commit()
            _execute_single_action(db, action, tool, parsed, settings)
            state["fingerprints"].append(fp)
            state["messages"].append(
                _tool_result_message(
                    tc["id"], action.status == ActionStatus.SUCCEEDED, _action_summary(action)
                )
            )
        _save_agent_state(db, task, state)
        db.refresh(task)
        if paused:
            return task
    task.status = TaskStatus.FAILED
    task.error = (
        f"Agent exceeded the maximum of {max_steps} tool steps without finishing. "
        "Try a smaller request."
    )
    _save_agent_state(db, task, state)
    db.refresh(task)
    return task


def continue_agent_task(db: Session, task_id: str, registry: ToolRegistry, provider, settings: Settings) -> Task:
    """Resume a paused agentic loop after approval/rejection, then keep going."""
    task = _get_task(db, task_id)
    if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.REJECTED):
        return task  # terminal: never re-execute
    state = _agent_state(task)
    if not state or state.get("provider") != "groq":
        return run_task(db, task_id, registry, settings)  # not an agent task
    if task.status == TaskStatus.WAITING_RETRY and time.time() < state.get("retry_at", 0):
        return task  # Respect the provider reset even if Resume is pressed repeatedly.
    pending = [a for a in task.actions if a.status == ActionStatus.PENDING]
    if pending:
        raise ValueError(
            f"Task '{task_id}' still has {len(pending)} action(s) awaiting approval"
        )
    from ..ai.groq_provider import GroqProvider

    specs = GroqProvider.build_tool_specs(tool_metadata(registry, task.command))
    # Execute newly-approved actions and report rejections back to the model.
    for action in sorted(task.actions, key=lambda x: x.seq):
        if action.status == ActionStatus.APPROVED and action.call_id:
            fp = _fingerprint(action.tool_name, json.loads(action.args_json or "{}"))
            if fp in state.get("fingerprints", []):
                action.status = ActionStatus.SKIPPED
                db.commit()
                continue
            tool = registry.get(action.tool_name)
            parsed = tool.validate_args(json.loads(action.args_json or "{}"))
            _execute_single_action(db, action, tool, parsed, settings)
            state["fingerprints"].append(fp)
            state["messages"].append(
                _tool_result_message(
                    action.call_id, action.status == ActionStatus.SUCCEEDED, _action_summary(action)
                )
            )
        elif action.status == ActionStatus.REJECTED and action.call_id:
            if action.call_id not in state.get("rejections_reported", []):
                state["messages"].append(
                    _tool_result_message(
                        action.call_id,
                        False,
                        "The user rejected this action. Do not retry it; adjust the plan or explain.",
                    )
                )
                state["rejections_reported"].append(action.call_id)
    task.status = TaskStatus.EXECUTING
    db.commit()
    try:
        return _agent_loop(db, task, registry, provider, settings, specs, state)
    except AIError as exc:
        if isinstance(exc, AIRateLimitError):
            task.status = TaskStatus.WAITING_RETRY
            state["retry_at"] = time.time() + exc.retry_after
            task.plan_note = getattr(exc, "user_note", None) or "AI quota ki wajah se task pause hai. Reset time ke baad Resume dabayein; simple volume/brightness commands abhi bhi chalengi."
            task.error = None
        else:
            task.status = TaskStatus.FAILED
            task.error = str(exc)
        _save_agent_state(db, task, state)
        db.refresh(task)
        return task


# ------------------------------------------------------------------ pause / rollback
def pause_task(db: Session, task_id: str, registry: ToolRegistry | None = None) -> Task:
    """Request a pause. It takes effect before the next action; an action already
    running cannot be interrupted safely and finishes first."""
    task = _get_task(db, task_id)
    if task.status != TaskStatus.EXECUTING:
        raise ValueError(f"Task '{task_id}' is '{task.status}'; only a running task can be paused")
    state = _agent_state(task)
    state["pause_requested"] = True
    task.agent_state_json = json.dumps(state)
    task.updated_at = utcnow()
    db.commit()
    _stop_running(task, registry, "pause")
    db.refresh(task)
    return task


# ------------------------------------------------------------------ checkpoints / stopping
def _stop_running(task: Task, registry: ToolRegistry | None, reason: str) -> list[str]:
    """Stop the running step now if its tool is interruptible; others finish first."""
    from . import worker

    running = [a for a in task.actions if a.status == ActionStatus.RUNNING]
    if registry is not None:
        running = [a for a in running if registry.get(a.tool_name).interruptible]
    return worker.request_stop([a.id for a in running], reason)


def _save_checkpoint(db: Session, task: Task, action: Action) -> None:
    """Remember the last step whose real outcome was verified."""
    state = _agent_state(task)
    state["checkpoint"] = {"action_id": action.id, "seq": action.seq, "tool": action.tool_name,
                           "verified_at": utcnow().isoformat()}
    task.agent_state_json = json.dumps(state)
    db.commit()


def _resume_from_checkpoint(db: Session, task: Task, registry: ToolRegistry) -> Task | None:
    """Before resuming, re-check that the last verified step still holds on the real system.
    Returns the refused task, or None to continue."""
    for a in task.actions:
        if a.status == ActionStatus.PAUSED:
            a.status = ActionStatus.APPROVED  # the same approved action continues (download resumes from .part)
    state = _agent_state(task)
    if state.pop("pause_requested", None):  # the pause was already honoured
        task.agent_state_json = json.dumps(state)
    db.commit()
    checkpoint = _agent_state(task).get("checkpoint")
    if not checkpoint:
        return None
    action = next((a for a in task.actions if a.id == checkpoint["action_id"]), None)
    if action is None or not action.result_json:
        return None
    tool = registry.get(action.tool_name)
    parsed = tool.validate_args(json.loads(action.args_json or "{}"))
    recheck = tool.verify(parsed, ToolResult.model_validate_json(action.result_json))
    if recheck.passed and recheck.evidence:
        return None
    for a in task.actions:
        if a.status == ActionStatus.APPROVED:
            a.status = ActionStatus.SKIPPED
    task.status = TaskStatus.FAILED
    task.error = (f"Resume refused: step {action.seq + 1} ({action.tool_name}) no longer holds on this PC "
                  f"({'; '.join(recheck.checks) or recheck.details}). Nothing else was run.")
    task.updated_at = utcnow()
    db.commit()
    db.refresh(task)
    return task


def rollback_task(db: Session, task_id: str, registry: ToolRegistry) -> Task:
    """Undo supported, still-untouched changes in reverse order. Each result is
    verified before it is reported; unsupported actions are reported as such."""
    task = _get_task(db, task_id)
    if task.status in (TaskStatus.EXECUTING, TaskStatus.AWAITING_APPROVAL, TaskStatus.APPROVED, TaskStatus.PAUSED):
        raise ValueError(f"Task '{task_id}' is '{task.status}'; finish or cancel it before rolling back")
    candidates = [a for a in sorted(task.actions, key=lambda x: -x.seq) if a.status == ActionStatus.SUCCEEDED and a.rollback_status in ("available", "failed")]
    if not candidates:
        raise ValueError("Nothing to roll back: no completed action has an available checkpoint")
    for action in candidates:
        tool = registry.get(action.tool_name)
        ok, detail = rollback.perform(json.loads(action.rollback_json or "{}"), tool.ctx)
        action.rollback_status = "done" if ok else "failed"
        action.rollback_detail = detail
        db.commit()
        if not ok:
            break  # earlier undos may depend on this one; stop and report
    task.updated_at = utcnow()
    db.commit()
    db.refresh(task)
    return task
