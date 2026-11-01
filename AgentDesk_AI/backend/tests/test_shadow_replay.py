"""Step 1g: scripts/shadow_replay.py (old local path vs new strict planner on the gold NLU set) with a fake planner."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from app.ai.base import Plan, PlanAction

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "shadow_replay.py"
spec = importlib.util.spec_from_file_location("shadow_replay", SCRIPT)
shadow_replay = importlib.util.module_from_spec(spec)
sys.modules["shadow_replay"] = shadow_replay
spec.loader.exec_module(shadow_replay)


def _gold_plan_fn(items, error=""):
    by_command = {i["command"]: i for i in items}
    calls = []

    def plan_fn(command):
        calls.append(command)
        item = by_command[command]
        steps = item["expected"]["steps"]
        plan = Plan(actions=[PlanAction(tool=s["tool"], args=dict(s["args"])) for s in steps], intent=item["expected"]["intent"])
        return (None, {"source": "groq", "model": "fake", "latency_ms": 5.0, "error": error}) if error else (plan, {"source": "groq", "model": "fake", "latency_ms": 5.0, "error": ""})

    plan_fn.calls = calls
    return plan_fn


def test_the_replay_set_is_single_turn_and_covers_all_four_languages_with_over_200_items():
    items = shadow_replay.load_items()
    assert len(items) >= 200 and all(i["context"] is None for i in items)
    assert {i["lang"] for i in items} == {"en", "roman_ur", "ur", "hi"}
    assert {i["split"] for i in shadow_replay.load_items("holdout")} == {"holdout"}


def test_replay_scores_old_and_new_against_gold_and_never_writes_the_command_text(tmp_path):
    items = shadow_replay.load_items()[:12]
    plan_fn = _gold_plan_fn(items)
    rows = shadow_replay.replay(items, tmp_path, plan_fn, min_interval=0, sleep=lambda s: None)
    assert [r["id"] for r in rows] == [i["id"] for i in items]
    assert all(r["error"] == "" for r in rows)
    # a planner that returns the gold plan is right whenever the gold has no unresolved references
    assert all(r["new_gold_tools"] for r in rows)
    assert all(r["new_gold_args"] for r in rows if not r["gold_has_refs"] and r["gold_tools"])
    text = (tmp_path / "shadow.jsonl").read_text(encoding="utf-8")
    assert not any(i["command"] in text for i in items) and "command" not in rows[0]


def test_replay_is_resumable_and_does_not_ask_the_planner_twice(tmp_path):
    items = shadow_replay.load_items()[:6]
    first = _gold_plan_fn(items)
    shadow_replay.replay(items[:3], tmp_path, first, min_interval=0, sleep=lambda s: None)
    second = _gold_plan_fn(items)
    rows = shadow_replay.replay(items, tmp_path, second, min_interval=0, sleep=lambda s: None)
    assert len(first.calls) == 3 and len(second.calls) == 3 and len(rows) == 6
    assert len((tmp_path / "shadow.jsonl").read_text(encoding="utf-8").splitlines()) == 6


def test_a_rate_limit_waits_and_retries_then_gives_up_honestly(tmp_path):
    items = shadow_replay.load_items()[:1]
    sleeps = []
    attempts = []

    def limited(command):
        attempts.append(1)
        if len(attempts) < 3:
            return None, {"source": "none", "model": "", "latency_ms": 1.0, "error": "AIRateLimitError"}
        return _gold_plan_fn(items)(command)

    rows = shadow_replay.replay(items, tmp_path / "a", limited, min_interval=0, sleep=sleeps.append)
    assert rows[0]["retries"] == 2 and rows[0]["error"] == "" and sleeps == [70.0, 70.0]

    always = lambda command: (None, {"source": "none", "model": "", "latency_ms": 1.0, "error": "AIRateLimitError"})  # noqa: E731
    rows = shadow_replay.replay(items, tmp_path / "b", always, min_interval=0, sleep=lambda s: None)
    assert rows[0]["retries"] == shadow_replay.MAX_RETRIES and rows[0]["error"] == "AIRateLimitError" and rows[0]["new_tools"] is None


def test_calls_are_paced_to_the_token_limit(tmp_path):
    items = shadow_replay.load_items()[:3]
    sleeps = []
    shadow_replay.replay(items, tmp_path, _gold_plan_fn(items), min_interval=65.0, sleep=sleeps.append)
    assert len(sleeps) == 2 and all(0 < s <= 65.0 for s in sleeps)


def test_summary_counts_planner_failures_as_misses_and_reports_per_language(tmp_path):
    items = shadow_replay.load_items()[:20]
    rows = shadow_replay.replay(items, tmp_path, _gold_plan_fn(items, error="AIRateLimitError"), min_interval=0, sleep=lambda s: None)
    summary = shadow_replay.summarise(rows)
    assert summary["overall"]["commands"] == 20 and summary["overall"]["new_planner_failed"] == 20
    assert summary["overall"]["gold_tools_new"]["ok"] == 0  # no plan from the planner is never a hit
    assert summary["new_errors"] == {"AIRateLimitError": 20}
    assert set(summary["by_language"]) <= {"en", "roman_ur", "ur", "hi"}
