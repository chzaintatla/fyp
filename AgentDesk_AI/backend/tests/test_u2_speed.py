"""U2 (speed): latency log and budgets, model routing, early acknowledgement, warm connection."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from app.nlu.planner import BIG_MODEL, SMALL_MODEL, GroqPlanner, is_complex
from app.voice import api as voice_api
from app.voice import latency, responses


# --- latency log ---------------------------------------------------------------------------------------------------

def test_stage_report_flags_a_miss_with_numbers(tmp_path):
    log = tmp_path / "l.jsonl"
    for v in (800, 900, 1000, 3000, 3200):        # transcribed budget is 1100 ms: p90 is far over it
        log.open("a").write(json.dumps({"kind": "local", "ms": {"transcribed": v}}) + "\n")
    rows = {r["stage"]: r for r in latency.stage_report(log)}
    t = rows["transcribed"]
    assert t["runs"] == 5 and t["budget_ms"] == 1100 and t["status"] == "MISS" and t["within_budget"] == 0.6 and t["p90_ms"] == 3200


def test_stage_report_ok_and_task_rows(tmp_path):
    log = tmp_path / "l.jsonl"
    for plan, run in ((5, 40), (8, 60), (6, 30)):
        latency.record_task("local", plan, run, log)
    latency.record_task("cloud", 900, 100, log)
    rows = {r["stage"]: r for r in latency.stage_report(log)}
    assert rows["plan (local)"]["status"] == "ok" and rows["execute_verify (local)"]["status"] == "ok"
    assert rows["total in app (local)"]["p50_ms"] == 45.0 and rows["plan (cloud)"]["budget_ms"] == 1200
    assert latency.stage_report(tmp_path / "missing.jsonl") == []


def test_record_task_never_stores_the_command_and_never_raises(tmp_path):
    log = tmp_path / "l.jsonl"
    latency.record_task("local", 1, 2, log)
    row = json.loads(log.read_text())
    assert set(row) == {"ts", "kind", "route", "ms"} and row["ms"] == {"plan": 1.0, "execute_verify": 2.0, "total": 3.0}
    latency.record_task("local", 1, 2, tmp_path)          # a directory cannot be opened as a file: swallowed


def test_running_a_command_writes_a_split_row(client):
    log = Path(os.environ["AGENTDESK_LATENCY_LOG"])
    before = log.read_text().count('"task"') if log.exists() else 0
    task = client.post("/tasks", json={"command": "show trash"}).json()
    assert task["status"] in ("completed", "awaiting_input", "failed")
    rows = [json.loads(x) for x in log.read_text().splitlines() if '"task"' in x]
    assert len(rows) == before + 1 and rows[-1]["route"] in ("local", "cloud")
    assert rows[-1]["ms"]["total"] >= rows[-1]["ms"]["execute_verify"] >= 0 and "show trash" not in log.read_text()


def test_stages_endpoint(client):
    client.post("/tasks", json={"command": "show trash"})
    data = client.get("/voice/latency/stages").json()
    assert any(r["stage"].startswith("plan") for r in data)


# --- model routing -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("command,complex_", [
    ("open chrome", False), ("turn the volume up", False), ("create a folder called Notes in Documents", False),
    ("open chrome and then search for python tutorials", True), ("pehle notepad kholo phir usme kuch likho", True),
    ("find my old cover letter in documents then zip it and move the zip to the desktop folder please", True),
    ("open word and also open excel for me please right now", True),
])
def test_complexity(command, complex_):
    assert is_complex(command) is complex_


class _Client:
    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def chat(self, messages, model=None, **kw):
        self.calls.append(model)
        if model in self.fail:
            from app.ai.base import AIError
            raise AIError("down")
        plan = {"intent": "chat", "language": "en", "confidence": 0.9, "needs_clarification": False, "clarifying_question": None,
                "steps": [], "reply": "hello"}
        return {"content": json.dumps(plan), "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


def _plan(command, client):
    from app.nlu.planner import PlannerContext
    from app.tools.registry import ToolRegistry
    from tests.conftest import _make_ctx
    return GroqPlanner(client=client).plan(PlannerContext(command=command, registry=ToolRegistry(_make_ctx(Path(os.environ["AGENTDESK_TEST_DIR"]))),
                                                          check_paths=False))


def test_simple_plan_uses_the_small_model_first(tmp_path):
    c = _Client()
    result = _plan("tell me something nice", c)
    assert c.calls == [SMALL_MODEL] and result.model == SMALL_MODEL


def test_multi_step_plan_uses_the_big_model_first(tmp_path):
    c = _Client()
    _plan("open chrome and then search for python tutorials", c)
    assert c.calls == [BIG_MODEL]


def test_other_model_is_the_fallback(tmp_path):
    c = _Client(fail={SMALL_MODEL})
    result = _plan("tell me something nice", c)
    assert c.calls == [SMALL_MODEL, BIG_MODEL] and result.model == BIG_MODEL


def test_custom_model_lists_keep_their_order():
    p = GroqPlanner(client=_Client(), models=("a", "b"))
    assert p.models_for("open chrome and then x") == ("a", "b")


# --- early acknowledgement -----------------------------------------------------------------------------------------

def _ack(command, language="en"):
    return voice_api.quick_ack_text(command, language, responses.Voiceprint("assistant", "", 50, 50))


@pytest.mark.parametrize("command", ["open chrome", "notepad kholo", "Open the YouTube.", "downloads folder kholo", "show trash"])
def test_safe_one_step_commands_are_acknowledged_early(command):
    assert _ack(command)


@pytest.mark.parametrize("command", ["delete the folder Lab", "volume 40 kar do", "what is the capital of France",
                                     "open vs code and play music", "remember my exam is on twelve December", "xqzv blorp"])
def test_risky_multi_step_or_unknown_commands_get_no_early_ack(command):
    assert _ack(command) is None


def test_early_ack_is_localised_and_fast():
    started = time.perf_counter()
    text = _ack("notepad kholo", "roman")
    assert (time.perf_counter() - started) < 0.05 and "Notepad" in text and "khol" in text


def test_ack_endpoint_never_fails_the_command(client):
    assert client.post("/voice/ack", json={"command": "open chrome"}).json()["text"]
    assert client.post("/voice/ack", json={"command": "delete the folder Lab"}).json() == {"text": None}
    assert client.post("/voice/ack", json={"command": ""}).status_code == 422


# --- warm connection -------------------------------------------------------------------------------------------------

def test_warm_returns_milliseconds_or_none():
    from app.ai.groq_client import GroqClient

    class Models:
        def list(self):
            return []

    class Sdk:
        models = Models()

    ok = GroqClient("k", client=Sdk())
    assert ok.warm() is not None and ok.warm() >= 0

    class Boom:
        class models:
            @staticmethod
            def list():
                raise RuntimeError("offline")

    assert GroqClient("k", client=Boom()).warm() is None


def test_keepalive_is_longer_than_the_servers_idle_close(monkeypatch):
    from app.ai import groq_client, warm
    assert groq_client.KEEPALIVE_SECONDS >= 60 and warm.PING_SECONDS < 60     # measured: Groq closes an idle connection after about a minute


def test_warm_loop_pings_and_stops_when_idle(monkeypatch):
    from app.ai import warm
    pings = []

    class C:
        last_used = 0.0

        def warm(self):
            pings.append(1)

    monkeypatch.setattr(warm, "_started", False)
    assert warm.start(C(), interval=0.02)
    time.sleep(0.2)
    assert len(pings) >= 3
    monkeypatch.setattr(warm, "_started", False)
    monkeypatch.setattr(warm, "IDLE_STOP_SECONDS", -1)    # idle for "too long": the loop must stop pinging
    idle = C()
    count = len(pings)
    warm.start(idle, interval=0.02)
    time.sleep(0.15)
    assert len(pings) - count <= 3   # only the earlier loop's pings; the idle one made none
