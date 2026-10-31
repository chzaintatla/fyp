"""Routes memory commands, questions and small talk. Called from `executor.start_task` at two points:

1. before the command parsers: memory commands ("remember...", "forget...", "what do you remember about me") are matched by
   strict rules, so they are never mistaken for file commands;
2. after the parsers gave up: a question becomes a `web_answer` plan (current facts) or a short spoken chat answer.

Plans go through `plan_task` like every other plan: tool validation, server-decided risk, approvals, verification.
A chat answer has no tool, so it is stored as the task's reply and the task completes.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from .. import conversation
from ..ai.base import AIError, AIRateLimitError
from ..config import Settings
from ..models import Task, TaskStatus, utcnow
from ..tools.registry import ToolRegistry


def _plan_and_run(db: Session, task: Task, plan, matcher: str, registry: ToolRegistry, settings: Settings, auto_approve: bool) -> Task:
    from . import executor

    task.agent_state_json = json.dumps({"provider": "local", "matcher": matcher})
    task = executor.plan_task(db, task, registry, settings, auto_approve=auto_approve, preplanned=plan)
    if task.status == TaskStatus.APPROVED:
        return executor.run_task(db, task.id, registry, settings)
    return task


def _history(db: Session, task: Task) -> list[tuple[str, str]]:
    rows = (db.query(Task).filter(Task.id != task.id, Task.status == TaskStatus.COMPLETED, Task.plan_note.isnot(None))
            .order_by(Task.created_at.desc()).limit(4).all())
    return [(t.command, t.plan_note) for t in reversed(rows) if not t.actions]


def try_conversation(db: Session, task: Task, command: str, registry: ToolRegistry, settings: Settings,
                     *, auto_approve: bool, before_local: bool) -> Task | None:
    if before_local:
        plan = conversation.memory_plan(command)
        if plan is not None:
            return _plan_and_run(db, task, plan, "memory", registry, settings, auto_approve)
        plan = conversation.summary_plan(command)
        return _plan_and_run(db, task, plan, "summary", registry, settings, auto_approve) if plan is not None else None

    kind = conversation.classify(command)
    if kind is None:
        return None
    from ..preferences import get_preferences
    if get_preferences(db).provider_mode == "local_only":
        return None  # the existing local-only message explains it
    if get_preferences(db).cloud_answers is not True:
        task.status, task.error = TaskStatus.FAILED, conversation.CLOUD_ANSWERS_OFF
        task.updated_at = utcnow()
        db.commit()
        db.refresh(task)
        return task
    if kind == "web":
        return _plan_and_run(db, task, conversation.web_plan(command), "web", registry, settings, auto_approve)
    task.agent_state_json = json.dumps({"provider": "cloud", "matcher": "chat"})
    try:
        answer = conversation.answer_chat(db, command, settings, _history(db, task))
    except AIRateLimitError:
        task.status, task.error = TaskStatus.FAILED, "The AI service is busy right now. Please ask again in a moment."
    except AIError as exc:
        task.status, task.error = TaskStatus.FAILED, f"I couldn't answer: {exc}"
    else:
        task.plan_note, task.status = answer, TaskStatus.COMPLETED
    task.updated_at = utcnow()
    db.commit()
    db.refresh(task)
    return task
