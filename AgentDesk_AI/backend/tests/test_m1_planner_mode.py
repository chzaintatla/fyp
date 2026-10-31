"""M1: strict-planner rollout (legacy | shadow | structured) keeps the safety model intact."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ai.base import AIError
from app.config import Settings
from app.nlu.planner import PlanResult
from app.nlu.schema import NLUPlan, PlanStep, validate_plan
from app.orchestration import planner_mode
from app.tools.base import ToolContext
from app.tools.registry import ToolRegistry


@pytest.fixture()
def registry(tmp_path: Path) -> ToolRegistry:
    return ToolRegistry(ToolContext(allowed_directories=[tmp_path], approved_applications=["chrome"], test_dir=tmp_path))


class FakePlanner:
    def __init__(self, registry, plan: NLUPlan | None = None, error: bool = False):
        self.registry, self.answer, self.error, self.llm = registry, plan, error, self
        self.calls = 0

    def plan(self, context):
        self.calls += 1
        if self.error:
            raise AIError("down")
        return PlanResult(self.answer, validate_plan(self.answer, self.registry), "groq", "fake", latency_ms=5.0)


def _open_chrome() -> NLUPlan:
    return NLUPlan(intent="open_app", confidence=0.95, steps=[PlanStep(tool="open_application", args={"app_name": "chrome"})])


def _use(monkeypatch, fake):
    monkeypatch.setattr("app.nlu.planner.planner_from_settings", lambda settings=None: fake)


def test_default_mode_is_legacy_and_never_calls_planner(registry, monkeypatch):
    fake = FakePlanner(registry, _open_chrome())
    _use(monkeypatch, fake)
    settings = Settings(groq_api_key="k")
    assert planner_mode.mode(settings) == "legacy"
    assert planner_mode.structured_plan("open chrome", registry, settings) is None
    assert fake.calls == 0


def test_structured_mode_returns_validated_plan(registry, monkeypatch):
    _use(monkeypatch, FakePlanner(registry, _open_chrome()))
    plan = planner_mode.structured_plan("open chrome", registry, Settings(groq_api_key="k", planner_mode="structured"))
    assert [a.tool for a in plan.actions] == ["open_application"]


def test_structured_mode_falls_back_on_error_or_no_key(registry, monkeypatch):
    _use(monkeypatch, FakePlanner(registry, error=True))
    assert planner_mode.structured_plan("x", registry, Settings(groq_api_key="k", planner_mode="structured")) is None
    _use(monkeypatch, FakePlanner(registry, _open_chrome()))
    assert planner_mode.structured_plan("x", registry, Settings(planner_mode="structured")) is None


def test_structured_mode_rejects_unresolved_references(registry, monkeypatch):
    bad = NLUPlan(intent="delete", steps=[PlanStep(tool="find_paths", args={"query": "a"}),
                                           PlanStep(tool="delete_file", args={"path": "$0"}, depends_on=0)])
    _use(monkeypatch, FakePlanner(registry, bad))
    assert planner_mode.structured_plan("delete a", registry, Settings(groq_api_key="k", planner_mode="structured")) is None


def test_structured_clarification_becomes_a_note(registry, monkeypatch):
    ask = NLUPlan(intent="clarify", needs_clarification=True, clarifying_question="Which report?")
    _use(monkeypatch, FakePlanner(registry, ask))
    plan = planner_mode.structured_plan("report", registry, Settings(groq_api_key="k", planner_mode="structured"))
    assert plan.actions == [] and plan.note == "Which report?"


def test_shadow_logs_comparison_without_command_text(registry, monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTDESK_PILOT_DIR", str(tmp_path / "pilot"))
    _use(monkeypatch, FakePlanner(registry, _open_chrome()))
    legacy = _open_chrome().to_legacy()
    settings = Settings(groq_api_key="k", planner_mode="shadow")
    planner_mode.shadow("open chrome secret-name", legacy, registry, settings, wait=True)
    row = json.loads((tmp_path / "pilot" / "shadow_plans.jsonl").read_text().splitlines()[0])
    assert row["tools_match"] and row["args_match"] and "secret-name" not in json.dumps(row)


def test_shadow_off_in_other_modes_and_never_raises(registry, monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTDESK_PILOT_DIR", str(tmp_path / "pilot"))
    _use(monkeypatch, FakePlanner(registry, error=True))
    legacy = _open_chrome().to_legacy()
    assert planner_mode.shadow("x", legacy, registry, Settings(groq_api_key="k")) is None
    planner_mode.shadow("x", legacy, registry, Settings(groq_api_key="k", planner_mode="shadow"), wait=True)
    row = json.loads((tmp_path / "pilot" / "shadow_plans.jsonl").read_text().splitlines()[0])
    assert row["planner_tools"] is None and not row["tools_match"]
