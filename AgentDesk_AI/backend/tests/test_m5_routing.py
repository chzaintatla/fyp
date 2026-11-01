"""Step 1e: question / memory / summary routing is decided by the planner's `intent`; the regex rules are a fast path.

Covers 60 phrasings per routed intent (tests/routing_phrasings.py). The planner here is a fake that returns the intent
the phrasing is labelled with, so these tests prove the dispatch, the safety checks and the fast path's precision.
They do NOT measure how well the real model labels intents (see docs/STATUS.md).
"""
from __future__ import annotations

import pytest

from app import conversation
from app.ai.base import Plan, PlanAction
from app.config import Settings, get_settings
from app.models import Task, TaskStatus
from app.nlu.planner import PlanResult
from app.nlu.schema import INTENTS, NLUPlan, PlanStep, validate_plan
from app.orchestration import conversation_flow, executor
from app.tools.base import ToolContext
from app.tools.registry import ToolRegistry
from tests.routing_phrasings import EXPECTED_COUNTS, PHRASINGS
from tests.test_m5_conversation import FakeChat, _set_cloud_answers  # noqa: F401  (fixtures below reuse them)

ALL = [(intent, lang, text) for intent, langs in PHRASINGS.items() for lang, texts in langs.items() for text in texts]
MEMORY_TOOL = {"memory_remember": "memory_remember", "memory_recall": "memory_recall", "memory_forget": "memory_forget"}


def fast_route(text: str) -> str | None:
    """What the regex fast path decides, in the order the task flow asks (memory, summary, then question / web)."""
    plan = conversation.memory_plan(text)
    if plan is not None:
        return plan.actions[0].tool
    if conversation.summary_plan(text) is not None:
        return "summarise"
    return {"web": "web_answer", "chat": "chat"}.get(conversation.classify(text) or "")


def test_the_phrasing_set_is_60_per_intent_in_the_required_language_mix():
    assert set(PHRASINGS) == {"memory_remember", "memory_recall", "memory_forget", "summarise", "web_answer", "chat"}
    for intent, langs in PHRASINGS.items():
        assert {lang: len(texts) for lang, texts in langs.items()} == EXPECTED_COUNTS, intent
        assert sum(len(t) for t in langs.values()) == 60
        flat = [t for texts in langs.values() for t in texts]
        assert len(set(flat)) == 60, f"{intent}: duplicate phrasing"
    assert len(ALL) == 360


def test_the_new_intents_exist_in_the_planner_schema():
    assert {"memory", "summarise", "web_answer", "chat"} <= set(INTENTS)


@pytest.mark.parametrize("intent,lang,text", ALL)
def test_the_fast_path_never_routes_a_phrasing_to_the_wrong_place(intent, lang, text):
    """A regex hit must be right. A miss is fine: the planner's intent decides those."""
    got = fast_route(text)
    expected = intent if intent in ("summarise", "web_answer", "chat") else MEMORY_TOOL[intent]
    assert got in (None, expected), f"{text!r}: fast path said {got}, expected {expected}"


# ------------------------------------------------------------------ dispatch by the planner's intent
class Recorder:
    """Stands in for `_plan_and_run`, so dispatch is tested without running tools or touching the network."""

    def __init__(self):
        self.calls = []

    def __call__(self, db, task, plan, matcher, registry, settings, auto_approve):
        self.calls.append((matcher, [a.tool for a in plan.actions]))
        return task


@pytest.fixture()
def reg(tmp_path):
    return ToolRegistry(ToolContext(allowed_directories=[tmp_path], approved_applications=["chrome"], test_dir=tmp_path))


@pytest.fixture()
def cloud_chat(client):
    fake = FakeChat("Short answer. Second sentence.")
    conversation.use_chat_client(fake)
    _set_cloud_answers(True)
    yield fake
    conversation.use_chat_client(None)


def _plan_for(intent: str, text: str) -> Plan:
    if intent in MEMORY_TOOL:
        args = {"memory_remember": {"text": text}, "memory_recall": {}, "memory_forget": {"query": text}}[intent]
        return Plan(actions=[PlanAction(tool=MEMORY_TOOL[intent], args=args)], intent="memory")
    if intent == "summarise":
        return Plan(actions=[PlanAction(tool="summarise_page", args={"url": "https://example.com/report"})], intent="summarise")
    if intent == "web_answer":
        return Plan(actions=[PlanAction(tool="web_answer", args={"query": text})], intent="web_answer")
    return Plan(intent="chat", note="x")


def _new_task(db, text):
    task = Task(command=text, status=TaskStatus.CREATED)
    db.add(task)
    db.commit()
    return task


@pytest.mark.parametrize("intent,lang,text", ALL)
def test_every_phrasing_is_routed_by_the_planner_intent(intent, lang, text, db, reg, cloud_chat, monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(conversation_flow, "_plan_and_run", rec)
    out = conversation_flow.route_by_intent(db, _new_task(db, text), _plan_for(intent, text), text, reg, get_settings(), auto_approve=False)
    assert out is not None
    if intent == "chat":
        assert out.status == TaskStatus.COMPLETED and out.plan_note == "Short answer. Second sentence."
        assert cloud_chat.calls[-1][-1] == {"role": "user", "content": text} and rec.calls == []
    else:
        label = "memory" if intent in MEMORY_TOOL else intent
        tool = MEMORY_TOOL.get(intent) or {"summarise": "summarise_page", "web_answer": "web_answer"}[intent]
        assert rec.calls == [(f"planner:{label}", [tool])]


@pytest.mark.parametrize("intent,tool", [
    ("memory", "delete_file"), ("memory", "web_answer"), ("summarise", "memory_forget"), ("summarise", "open_application"),
    ("web_answer", "memory_remember"), ("web_answer", "delete_file"),
])
def test_a_plan_whose_tools_do_not_match_its_label_is_not_routed(intent, tool, db, reg, cloud_chat, monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(conversation_flow, "_plan_and_run", rec)
    plan = Plan(actions=[PlanAction(tool=tool, args={})], intent=intent)
    assert conversation_flow.route_by_intent(db, _new_task(db, "x"), plan, "x", reg, get_settings(), auto_approve=False) is None
    assert rec.calls == []


def test_a_chat_label_with_actions_and_an_empty_memory_plan_are_not_routed(db, reg, cloud_chat, monkeypatch):
    monkeypatch.setattr(conversation_flow, "_plan_and_run", Recorder())
    task = _new_task(db, "x")
    with_actions = Plan(actions=[PlanAction(tool="delete_file", args={"path": "a"})], intent="chat")
    assert conversation_flow.route_by_intent(db, task, with_actions, "x", reg, get_settings(), auto_approve=False) is None
    assert conversation_flow.route_by_intent(db, task, Plan(intent="memory"), "x", reg, get_settings(), auto_approve=False) is None
    assert cloud_chat.calls == []


@pytest.mark.parametrize("intent", ["open_app", "clarify", "unsupported", None])
def test_other_intents_are_left_to_the_normal_flow(intent, db, reg, cloud_chat):
    plan = Plan(actions=[PlanAction(tool="open_application", args={"app_name": "chrome"})], intent=intent)
    assert conversation_flow.route_by_intent(db, _new_task(db, "open chrome"), plan, "open chrome", reg, get_settings(), auto_approve=False) is None


@pytest.mark.parametrize("setting", [None, False])
@pytest.mark.parametrize("intent", ["chat", "web_answer"])
def test_cloud_questions_stay_blocked_until_the_user_said_yes(intent, setting, client, db, reg, monkeypatch):
    fake = FakeChat()
    conversation.use_chat_client(fake)
    _set_cloud_answers(setting)
    rec = Recorder()
    monkeypatch.setattr(conversation_flow, "_plan_and_run", rec)
    try:
        out = conversation_flow.route_by_intent(db, _new_task(db, "q"), _plan_for(intent, "q"), "q", reg, get_settings(), auto_approve=False)
    finally:
        conversation.use_chat_client(None)
    assert out.status == TaskStatus.FAILED and out.error == conversation.CLOUD_ANSWERS_OFF
    assert fake.calls == [] and rec.calls == []


def test_a_cloud_failure_is_reported_not_invented(db, reg, client):
    from app.ai.base import AIError
    conversation.use_chat_client(FakeChat(AIError("service down")))
    _set_cloud_answers(True)
    try:
        out = conversation_flow.route_by_intent(db, _new_task(db, "why?"), Plan(intent="chat"), "why?", reg, get_settings(), auto_approve=False)
    finally:
        conversation.use_chat_client(None)
    assert out.status == TaskStatus.FAILED and "service down" in out.error and not out.plan_note


# ------------------------------------------------------------------ through start_task (structured mode)
class FakePlanner:
    def __init__(self, registry, plan: NLUPlan):
        self.registry, self.answer, self.llm, self.calls = registry, plan, self, 0

    def plan(self, context):
        self.calls += 1
        return PlanResult(self.answer, validate_plan(self.answer, self.registry), "groq", "fake", latency_ms=5.0)


@pytest.fixture()
def structured(client, reg, monkeypatch):
    holder = {}

    def use(plan: NLUPlan) -> FakePlanner:
        holder["p"] = FakePlanner(reg, plan)
        monkeypatch.setattr("app.nlu.planner.planner_from_settings", lambda settings=None: holder["p"])
        return holder["p"]

    return use


def _settings():
    return Settings(groq_api_key="k", planner_mode="structured", ai_provider="groq", ai_cloud_mode="automatic")


def test_a_question_the_regex_misses_is_answered_through_the_planner_intent(client, reg, structured, cloud_chat):
    planner = structured(NLUPlan(intent="chat", confidence=0.9, reply="x"))
    from app.database import SessionLocal
    with SessionLocal() as db:
        task = executor.start_task(db, "zindagi ka matlab samjhao dost", None, reg, _settings())
    assert task.status == TaskStatus.COMPLETED and task.plan_note == "Short answer. Second sentence."
    assert planner.calls == 1 and '"matcher": "planner:chat"' in task.agent_state_json


def test_the_planner_is_not_asked_when_the_fast_path_already_knows(client, reg, structured, cloud_chat):
    planner = structured(NLUPlan(intent="chat", confidence=0.9))
    from app.database import SessionLocal
    with SessionLocal() as db:
        task = executor.start_task(db, "what do you remember about me", None, reg, _settings())
        tools = [a.tool_name for a in task.actions]
    assert planner.calls == 0 and tools == ["memory_recall"]


def test_a_planner_memory_plan_goes_through_normal_validation_and_risk(client, reg, structured, cloud_chat):
    structured(NLUPlan(intent="memory", confidence=0.9, steps=[PlanStep(tool="memory_forget", args={"query": "exam"})]))
    from app.database import SessionLocal
    with SessionLocal() as db:
        task = executor.start_task(db, "meri purani baat dimaagh se nikaal do", None, reg, _settings())
        tools, status = [a.tool_name for a in task.actions], task.status
    assert tools == ["memory_forget"]
    assert status == TaskStatus.AWAITING_APPROVAL  # forget is medium risk: never silent


def _legacy():
    return Settings(groq_api_key="k", ai_provider="groq", ai_cloud_mode="automatic")


@pytest.mark.parametrize("mode", ["legacy", "shadow"])
def test_every_mode_asks_the_planner_once_for_a_command_nothing_local_understood(mode, client, reg, structured, cloud_chat):
    planner = structured(NLUPlan(intent="chat", confidence=0.9, reply="x"))
    from app.database import SessionLocal
    with SessionLocal() as db:
        task = executor.start_task(db, "zindagi ka matlab samjhao dost", None, reg, Settings(**{**_legacy().model_dump(), "planner_mode": mode}))
    assert planner.calls == 1 and task.status == TaskStatus.COMPLETED and task.plan_note == "Short answer. Second sentence."


def test_legacy_mode_routes_an_urdu_memory_command_the_regex_cannot_read(client, reg, structured, cloud_chat):
    planner = structured(NLUPlan(intent="memory", confidence=0.9, steps=[PlanStep(tool="memory_remember", args={"text": "امتحان بارہ دسمبر"})]))
    from app.database import SessionLocal
    with SessionLocal() as db:
        task = executor.start_task(db, "میرا امتحان بارہ دسمبر کو ہے ذہن میں بٹھا لو", None, reg, _legacy())
        tools, matcher = [a.tool_name for a in task.actions], task.agent_state_json
    assert planner.calls == 1 and tools == ["memory_remember"] and "planner:memory" in matcher


def test_a_non_conversation_intent_is_discarded_and_the_old_path_runs_as_before(client, reg, structured, cloud_chat, monkeypatch):
    planner = structured(NLUPlan(intent="open_app", confidence=0.9, steps=[PlanStep(tool="open_application", args={"app_name": "chrome"})]))
    seen = []
    monkeypatch.setattr("app.orchestration.executor.get_provider", lambda settings: seen.append("agent") or (_ for _ in ()).throw(__import__("app.ai.base", fromlist=["AIError"]).AIError("stop")))
    from app.database import SessionLocal
    with SessionLocal() as db:
        task = executor.start_task(db, "zindagi ka matlab samjhao dost", None, reg, _legacy())
        status, tools = task.status, [a.tool_name for a in task.actions]
    assert planner.calls == 1 and seen == ["agent"] and status == TaskStatus.FAILED and tools == []


def test_local_only_and_explicit_cloud_mode_never_ask_the_planner(client, reg, structured, cloud_chat):
    planner = structured(NLUPlan(intent="chat", confidence=0.9))
    from app.database import SessionLocal
    client.put("/preferences", json={"provider_mode": "local_only"})
    try:
        with SessionLocal() as db:
            executor.start_task(db, "zindagi ka matlab samjhao dost", None, reg, _legacy())
    finally:
        client.put("/preferences", json={"provider_mode": "auto"})
    with SessionLocal() as db:
        executor.start_task(db, "zindagi ka matlab samjhao dost 2", None, reg,
                            Settings(groq_api_key="k", ai_provider="groq", ai_cloud_mode="explicit"))
    assert planner.calls == 0


def test_without_a_cloud_key_the_planner_is_not_asked(client, reg, structured, cloud_chat):
    planner = structured(NLUPlan(intent="chat", confidence=0.9))
    from app.database import SessionLocal
    with SessionLocal() as db:
        executor.start_task(db, "zindagi ka matlab samjhao dost", None, reg, Settings(ai_provider="groq", ai_cloud_mode="automatic"))
    assert planner.calls == 0


# ------------------------------------------------------------------ a failing planner must not slow every command
def test_a_failing_planner_is_not_asked_again_for_five_minutes_and_recovers(reg, monkeypatch):
    from app.orchestration import planner_mode
    calls = []
    clock = [1000.0]
    monkeypatch.setattr(planner_mode.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(planner_mode, "_routing_blocked_until", 0.0)
    state = {"fail": True}

    def fake(command, registry, settings, llm_only=False):
        calls.append(command)
        if state["fail"]:
            return None, {"error": "AIError"}
        return Plan(intent="chat", note="hi"), {"error": ""}

    monkeypatch.setattr(planner_mode, "_planner_plan", fake)
    settings = Settings(groq_api_key="k")
    assert planner_mode.routing_plan("a", reg, settings) is None and planner_mode.routing_plan("b", reg, settings) is None
    assert calls == ["a"]  # the second command did not even try
    clock[0] += planner_mode.ROUTING_BACKOFF_SECONDS + 1
    state["fail"] = False
    assert planner_mode.routing_plan("c", reg, settings).intent == "chat" and calls == ["a", "c"]
    assert planner_mode.routing_plan("d", reg, settings).intent == "chat" and calls == ["a", "c", "d"]  # success clears the back-off
