"""The task session: what the conversation is about right now, so "now send it", "the second one", "undo that" and "that's all" work.

One open session at a time (one user, one PC). It is stored as a preference row, so it survives a restart, and it expires after
`IDLE_SECONDS` without a command. It holds only short facts, never file contents or passwords:
  goal        the first command of the session ("open my FYP report")
  steps_done  the tools that ran (and whether each was verified)
  current_app the app or site the last command acted on
  open_items  things still waiting: a question for the user, a draft field, an approval
  awaiting    "anything_else" after a verified result (the assistant asked, the next "no / thanks" closes the session)
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models import Preference, Task, TaskStatus, utcnow

KEY = "task_session"
IDLE_SECONDS = 90
MAX_STEPS = 20
TERMINAL = {TaskStatus.COMPLETED, TaskStatus.PARTIALLY_COMPLETED, TaskStatus.FAILED, TaskStatus.REJECTED, TaskStatus.CANCELLED}
_APP_TOOLS = {"open_application": "app_name", "open_url": "url", "open_folder": "path", "open_file": "path", "control_browser": "target"}


@dataclass
class TaskSession:
    goal: str
    opened_at: str
    last_active: str
    steps_done: list[dict] = field(default_factory=list)
    current_app: str | None = None
    open_items: dict = field(default_factory=dict)
    awaiting: str | None = None          # "anything_else" | None
    last_task_id: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: str) -> datetime:
    t = datetime.fromisoformat(value)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def current(db: Session) -> TaskSession | None:
    """The open session, or None (never opened, closed, or idle for too long)."""
    row = db.get(Preference, KEY)
    if row is None:
        return None
    try:
        data = json.loads(row.value_json)
        session = TaskSession(**data) if data else None
    except (ValueError, TypeError):
        return None
    if session is None or _now() - _parse(session.last_active) > timedelta(seconds=IDLE_SECONDS):
        return None
    return session


def _save(db: Session, session: TaskSession | None) -> None:
    row = db.get(Preference, KEY) or Preference(key=KEY)
    row.value_json = json.dumps(session.as_dict() if session else {})
    db.add(row)
    db.commit()


def close(db: Session) -> bool:
    """End the session (the user said "that's all", or stayed silent). True if one was open."""
    was_open = current(db) is not None
    _save(db, None)
    return was_open


def touch(db: Session) -> None:
    session = current(db)
    if session is not None:
        session.last_active = _now().isoformat()
        _save(db, session)


def _target_of(task: Task) -> str | None:
    for action in reversed(list(task.actions)):
        key = _APP_TOOLS.get(action.tool_name)
        if key:
            try:
                value = json.loads(action.args_json or "{}").get(key)
            except ValueError:
                continue
            if value:
                return str(value)[:80]
    return None


def observe(db: Session, task: Task, *, ask_more: bool | None = None) -> TaskSession:
    """Update the session after a task changed state. Opens one if none is open."""
    from ..preferences import get_preferences
    session = current(db) or TaskSession(goal=task.command[:200], opened_at=_now().isoformat(), last_active=_now().isoformat())
    seen = {s.get("key") for s in session.steps_done}
    for action in sorted(task.actions, key=lambda a: a.seq):
        if action.status in ("succeeded", "failed") and f"{task.id}:{action.seq}" not in seen:
            verified = False
            if action.verification_json:
                try:
                    verified = bool(json.loads(action.verification_json).get("passed"))
                except ValueError:
                    pass
            session.steps_done.append({"task_id": task.id, "key": f"{task.id}:{action.seq}", "tool": action.tool_name,
                                       "ok": action.status == "succeeded", "verified": verified})
    session.steps_done = session.steps_done[-MAX_STEPS:]
    target = _target_of(task)
    if target:
        session.current_app = target
    session.last_task_id = task.id
    session.last_active = _now().isoformat()
    if task.status in (TaskStatus.AWAITING_INPUT, TaskStatus.AWAITING_APPROVAL):
        session.open_items = {"waiting_for": "approval" if task.status == TaskStatus.AWAITING_APPROVAL else "answer",
                              "task_id": task.id}
        session.awaiting = None
    else:
        session.open_items.pop("waiting_for", None)
        session.open_items.pop("task_id", None)
        wants = get_preferences(db).anything_else if ask_more is None else ask_more
        if _mid_workflow(task):
            wants = False   # "Is this the right folder? yes / no" is still waiting: a "yes" must answer THAT question
        # Ask "Anything else?" after a verified result only: a failure is explained, not followed by a cheerful question.
        verified_done = task.status == TaskStatus.COMPLETED and all(
            _verified(a) for a in task.actions if a.status != "skipped")
        session.awaiting = "anything_else" if (wants and task.status in TERMINAL and verified_done) else None
    _save(db, session)
    return session


def _mid_workflow(task: Task) -> bool:
    try:
        return bool(json.loads(task.agent_state_json or "{}").get("workflow"))
    except ValueError:
        return False


def _verified(action) -> bool:
    if action.status != "succeeded":
        return False
    try:
        return bool(json.loads(action.verification_json or "{}").get("passed"))
    except ValueError:
        return False


def wrap(db: Session, task: Task) -> Task:
    """`observe` that can never break the command that triggered it."""
    try:
        observe(db, task)
    except Exception:  # noqa: BLE001 - the session is a convenience; a task result must reach the user
        db.rollback()
    db.refresh(task)   # saving the session committed: the caller reads the task after its own session is closed
    return task
