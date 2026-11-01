"""U3: the task session, the words about the conversation, bounded re-checks, and ten scripted multi-turn conversations.

Each conversation is a list of what the user says and what must be true afterwards. They run the real executor with local tools
(no cloud, no PC side effects: apps are replaced by fakes where a launch would happen).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.models import ActionStatus, Preference, TaskStatus
from app.orchestration import executor, session, session_commands
from app.tools.base import ToolContext, ToolResult, Verification
from app.tools.registry import ToolRegistry

SETTINGS = Settings(ai_provider="groq", groq_api_key=None)


@pytest.fixture()
def registry(tmp_path, monkeypatch):
    monkeypatch.setattr(executor, "get_provider", lambda *_: pytest.fail("cloud used in a local conversation"))
    monkeypatch.setattr(executor, "RETRY_WAIT_SECONDS", 0.0)
    return ToolRegistry(ToolContext(allowed_directories=[tmp_path]))


def say(db, registry, text):
    task = executor.start_task(db, text, None, registry, SETTINGS)
    db.refresh(task)
    return task


def approve_and_run(db, registry, task):
    executor.approve_task(db, task.id, [a.id for a in task.actions])
    return executor.run_task(db, task.id, registry, SETTINGS)


def is_session_reply(task) -> bool:
    return '"matcher": "session"' in (task.agent_state_json or "")


# --- the phrases ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,kind", [
    ("stop", "stop"), ("Stop!", "stop"), ("ruko", "stop"), ("रुको", "stop"), ("رکو", "stop"),
    ("wait", "wait"), ("hold on", "wait"), ("ek minute", "wait"),
    ("repeat that", "repeat"), ("Say that again.", "repeat"), ("dobara bolo", "repeat"), ("दोबारा बोलो", "repeat"),
    ("read it to me", "read"), ("read it out loud", "read"), ("parh ke sunao", "read"),
    ("undo that", "undo"), ("undo", "undo"), ("wapas karo", "undo"), ("वापस करो", "undo"),
    ("please undo that", "undo"),
])
def test_conversation_phrases(text, kind):
    assert session_commands.parse(text) == kind


@pytest.mark.parametrize("text", ["no", "that's all", "thanks", "bas shukriya", "yes", "haan", "शुक्रिया", "نہیں"])
def test_closing_words_only_count_after_anything_else(text):
    assert session_commands.parse(text) is None
    assert session_commands.parse(text, awaiting_more=True) in ("close", "more")


@pytest.mark.parametrize("text", ["stop the music and close notepad", "undo the changes in report.docx", "open chrome", "repeat the last line of the file",
                                  "read the file notes.txt", "no folder named lab"])
def test_longer_commands_are_not_conversation_phrases(text):
    assert session_commands.parse(text, awaiting_more=True) is None


# --- ten scripted conversations ------------------------------------------------------------------------------------

def test_c01_result_then_anything_else_then_no_closes(db, registry):
    first = say(db, registry, "show trash")
    assert first.status == TaskStatus.COMPLETED
    state = session.current(db)
    assert state and state.awaiting == "anything_else" and state.goal == "show trash" and state.steps_done[-1]["tool"] == "list_trash"
    closing = say(db, registry, "no thanks")
    assert is_session_reply(closing) and closing.plan_note == "Okay, I'm here if you need me." and closing.status == TaskStatus.COMPLETED
    assert session.current(db) is None


def test_c02_repeat_says_the_same_thing_every_time(db, registry):
    first = say(db, registry, "show trash")
    from app.voice import responses
    expected = responses.line_for_task(executor.task_out(first).model_dump(mode="json"), "en")
    assert say(db, registry, "repeat that").plan_note == expected
    assert say(db, registry, "say that again").plan_note == expected     # a repeat is never "repeated" into the word "Okay"
    assert session.current(db) is not None


def test_c03_create_then_undo(db, registry, tmp_path):
    ask = say(db, registry, "create folder Study")
    assert not ask.actions
    plan = say(db, registry, str(tmp_path))
    assert plan.status == TaskStatus.AWAITING_APPROVAL
    assert session.current(db).open_items["waiting_for"] == "approval"
    done = approve_and_run(db, registry, plan)
    assert done.status == TaskStatus.COMPLETED and (tmp_path / "Study").is_dir()
    undone = say(db, registry, "undo that")
    assert undone.plan_note == "Undone." and not (tmp_path / "Study").exists()
    assert say(db, registry, "undo that").plan_note == "There is nothing I can undo."


def test_c04_stop_cancels_the_waiting_task_and_closes_the_session(db, registry, tmp_path):
    say(db, registry, "create folder Study")
    plan = say(db, registry, str(tmp_path))
    assert plan.status == TaskStatus.AWAITING_APPROVAL
    stopped = say(db, registry, "stop")
    db.refresh(plan)
    assert plan.status == TaskStatus.CANCELLED and stopped.plan_note == "Stopped." and session.current(db) is None
    assert not (tmp_path / "Study").exists()


def test_c05_wait_pauses_the_conversation_but_cancels_nothing(db, registry, tmp_path):
    say(db, registry, "create folder Study")
    plan = say(db, registry, str(tmp_path))
    waiting = say(db, registry, "wait")
    db.refresh(plan)
    assert waiting.plan_note == "Okay, I'll wait." and plan.status == TaskStatus.AWAITING_APPROVAL
    assert session.current(db) is not None


def test_c06_roman_urdu_closing(db, registry):
    say(db, registry, "show trash")
    closing = say(db, registry, "bas shukriya")
    assert closing.plan_note == "Theek hai, zaroorat ho to bata dena." and session.current(db) is None


def test_c07_a_plain_no_is_not_swallowed_when_nothing_was_asked(db, registry):
    reply = say(db, registry, "no")
    assert not is_session_reply(reply)          # no session, no "Anything else?": "no" goes to the normal parsers


def test_c08_failed_check_is_repeated_for_tools_that_are_safe_to_repeat(db, registry, monkeypatch):
    from app.tools.apps import OpenApplicationTool
    calls = {"execute": 0, "verify": 0}

    def execute(self, args):
        calls["execute"] += 1
        return ToolResult(success=True, output={"app": "notepad", "pid": 1, "launched": True})

    def verify(self, args, result):
        calls["verify"] += 1
        if calls["verify"] == 1:
            return Verification(passed=False, checks=["app window is open"], details="no window yet")
        return Verification(passed=True, checks=["app window is open"], evidence={"windows": ["Untitled - Notepad"]})

    monkeypatch.setattr(OpenApplicationTool, "execute", execute)
    monkeypatch.setattr(OpenApplicationTool, "verify", verify)
    monkeypatch.setattr(OpenApplicationTool, "risk_for_args", lambda self, args: __import__("app.tools.base", fromlist=["RiskLevel"]).RiskLevel.LOW)
    task = say(db, registry, "open app notepad")
    assert task.status == TaskStatus.COMPLETED and calls == {"execute": 2, "verify": 2}
    action = task.actions[0]
    assert action.status == ActionStatus.SUCCEEDED and json.loads(action.result_json)["output"]["attempts"] == 2


def test_c09_a_failed_check_is_never_repeated_for_a_tool_that_changes_things(db, registry, tmp_path, monkeypatch):
    from app.tools.filesystem import CreateFolderTool
    calls = {"execute": 0}
    real = CreateFolderTool.execute

    def execute(self, args):
        calls["execute"] += 1
        return real(self, args)

    monkeypatch.setattr(CreateFolderTool, "execute", execute)
    monkeypatch.setattr(CreateFolderTool, "verify", lambda self, args, result: Verification(passed=False, checks=["folder exists"], details="not seen"))
    say(db, registry, "create folder Study")
    plan = say(db, registry, str(tmp_path))
    done = approve_and_run(db, registry, plan)
    assert calls["execute"] == 1 and done.status == TaskStatus.FAILED and "tried" not in (done.actions[0].error or "")
    assert session.current(db).awaiting is None            # a failure is explained, not followed by "Anything else?"


def test_c10_read_it_then_yes_keeps_the_conversation_and_idle_closes_it(db, registry):
    first = say(db, registry, "show trash")
    read = say(db, registry, "read it to me")
    assert read.plan_note and is_session_reply(read)
    more = say(db, registry, "yes")
    assert more.plan_note == "Sure, what next?" and session.current(db).awaiting is None
    row = db.get(Preference, session.KEY)
    data = json.loads(row.value_json)
    data["last_active"] = (datetime.now(timezone.utc) - timedelta(seconds=session.IDLE_SECONDS + 5)).isoformat()
    row.value_json = json.dumps(data)
    db.commit()
    assert session.current(db) is None                    # idle too long: back to idle
    assert first.status == TaskStatus.COMPLETED


# --- the spoken line and the API -----------------------------------------------------------------------------------

def test_line_asks_anything_else_after_a_verified_result_only(db, registry, client):
    task = say(db, registry, "show trash")
    body = executor.task_out(task).model_dump(mode="json")
    text = client.post("/voice/line", json={"task": body}).json()["text"]
    assert text.endswith("Anything else?")
    assert client.post("/voice/line", json={"task": body, "language": "ur"}).json()["text"].endswith("اور کچھ؟")
    say(db, registry, "no thanks")
    assert not client.post("/voice/line", json={"task": body}).json()["text"].endswith("Anything else?")


def test_anything_else_can_be_switched_off(db, registry):
    from app.preferences import PreferencesUpdate, update_preferences
    update_preferences(db, PreferencesUpdate(anything_else=False))
    try:
        say(db, registry, "show trash")
        assert session.current(db).awaiting is None
    finally:
        update_preferences(db, PreferencesUpdate(anything_else=True))


def test_session_endpoints(client, db, registry):
    assert client.get("/voice/session").json() == {"open": False}
    say(db, registry, "show trash")
    state = client.get("/voice/session").json()
    assert state["open"] and state["goal"] == "show trash" and state["awaiting"] == "anything_else"
    assert client.post("/voice/session/close", json={}).json() == {"closed": True}
    assert client.get("/voice/session").json() == {"open": False}


def test_follow_up_window_defaults_to_ten_seconds():
    from app.preferences import Preferences
    assert Preferences().follow_up_seconds == 10 and Preferences().anything_else is True
