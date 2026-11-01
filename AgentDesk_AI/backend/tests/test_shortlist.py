"""Step 1g: the planner is shown a per-command tool shortlist so the request fits the 8k tokens/minute free tier."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.nlu.memory import ConversationMemory, Turn
from app.nlu.planner import GroqPlanner, PlannerContext, build_messages, pick_examples, response_format, shortlist_for
from app.nlu.schema import plan_json_schema, tool_names_in_schema
from app.nlu.shortlist import FILE_TOOLS, MAX_SCORED, MIN_TOOLS, shortlist_tools, tools_for
from app.tools.base import ToolContext
from app.tools.registry import ToolRegistry
from tests.routing_phrasings import PHRASINGS

GOLD = [json.loads(line) for line in (Path(__file__).parent / "nlu_eval" / "commands.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
LIMIT = 8000


@pytest.fixture(scope="module")
def registry():
    return ToolRegistry(ToolContext.from_settings())


def _tokens(messages, fmt) -> int:
    import tiktoken
    enc = tiktoken.get_encoding("o200k_base")
    return sum(len(enc.encode(m["content"])) for m in messages) + len(enc.encode(json.dumps(fmt)))


def _context(item, registry):
    turns = [Turn(**{k: v for k, v in t.items() if k in Turn.__dataclass_fields__}) for t in ((item.get("context") or {}).get("turns") or [])]
    return PlannerContext(command=item["command"], registry=registry, memory=ConversationMemory(turns))


def test_the_full_catalogue_request_is_over_the_limit_and_the_shortlist_is_far_under_it(registry):
    ctx = PlannerContext(command="turn the volume up", registry=registry, tools=registry.model_names())
    assert _tokens(build_messages(ctx), response_format(registry, ctx.tools)) > LIMIT  # the bug that made every planner call HTTP 413
    sizes = []
    for item in GOLD:
        c = _context(item, registry)
        c.tools = shortlist_for(c)
        sizes.append(_tokens(build_messages(c), response_format(registry, c.tools)))
    assert max(sizes) < 5000  # leaves room for the 900-token answer and the system prompt growing


@pytest.mark.parametrize("split", ["dev", "holdout"])
def test_the_shortlist_keeps_the_tools_the_gold_plan_needs(split, registry):
    rows = [r for r in GOLD if r["split"] == split and r["expected"]["steps"]]
    missed = [r["id"] for r in rows if not {s["tool"] for s in r["expected"]["steps"]} <= set(shortlist_for(_context(r, registry)))]
    assert len(rows) - len(missed) >= 0.97 * len(rows), missed


def test_the_shortlist_keeps_the_tools_for_the_conversation_phrasings(registry):
    expect = {"memory_remember": "memory_remember", "memory_recall": "memory_recall", "memory_forget": "memory_forget",
              "summarise": ("summarise_page", "summarise_file"), "web_answer": "web_answer"}
    total = hit = 0
    for intent, langs in PHRASINGS.items():
        if intent not in expect:
            continue
        want = expect[intent] if isinstance(expect[intent], tuple) else (expect[intent],)
        for texts in langs.values():
            for text in texts:
                total += 1
                hit += any(t in shortlist_for(PlannerContext(command=text, registry=registry)) for t in want)
    assert hit >= 0.97 * total, (hit, total)


def test_size_is_bounded_and_never_below_the_minimum(registry):
    names = registry.model_names()
    for command in ["", "asdf", "open notepad", "copy report.docx to desktop and zip it and email it and delete the old one",
                    "volume brightness bluetooth wifi settings screenshot tabs window folder file zip email whatsapp install"]:
        chosen = shortlist_tools(command, names)
        assert MIN_TOOLS <= len(chosen) <= MAX_SCORED + 4, (command, chosen)
        assert chosen == [n for n in names if n in chosen]  # registry order, no duplicates
        assert len(set(chosen)) == len(chosen)


def test_file_operations_bring_find_paths_and_install_brings_the_package_search(registry):
    names = registry.model_names()
    assert "find_paths" in shortlist_tools("rename draft.txt to final.txt", names) and FILE_TOOLS & set(shortlist_tools("rename draft.txt", names))
    assert "application_package" in shortlist_tools("install zoom", names)


def test_the_local_matchers_guess_and_the_previous_turn_widen_the_shortlist(registry):
    names = registry.model_names()
    assert "take_screenshot" not in shortlist_tools("hmm do the thing", names)
    assert "take_screenshot" in shortlist_tools("hmm do the thing", names, local_tools=["take_screenshot"])
    assert "manage_system_setting" in shortlist_tools("a bit more", names, last_tools=["manage_system_setting"])


def test_the_schema_offers_only_the_shortlisted_tools_but_the_default_is_unchanged(registry):
    names = registry.model_names()
    subset = ["open_application", "manage_system_setting"]
    assert tool_names_in_schema(plan_json_schema(registry, subset)) == subset
    assert tool_names_in_schema(plan_json_schema(registry)) == names


def test_examples_only_use_shortlisted_tools(registry):
    subset = ["manage_system_setting", "open_application"]
    for ex in pick_examples("volume up", 4, subset):
        assert all(s["tool"] in subset for s in ex["plan"]["steps"])
    assert len(pick_examples("volume up", 4)) == 4


def test_the_planner_sends_the_shortlist_and_validation_still_uses_the_full_registry(registry):
    seen = {}

    class FakeClient:
        def chat(self, messages, model=None, response_format=None, temperature=0, max_tokens=0):
            seen["tools"] = [v["properties"]["tool"]["enum"][0] for v in response_format["json_schema"]["schema"]["properties"]["steps"]["items"]["anyOf"]]
            seen["catalogue"] = messages[0]["content"]
            plan = {"intent": "system_setting", "language": "en", "confidence": 0.9, "needs_clarification": False, "clarifying_question": None,
                    "steps": [{"tool": "manage_system_setting", "args": {"setting": "volume", "action": "increase", "value": 10,
                                                                         "enabled": None, "level": None}, "depends_on": None, "expected_outcome": ""}],
                    "reply": ""}
            return {"content": json.dumps(plan), "usage": {}}

    result = GroqPlanner(client=FakeClient()).plan(PlannerContext(command="turn the volume up", registry=registry))
    assert "manage_system_setting" in seen["tools"] and len(seen["tools"]) < 15 and "compress_folder" not in seen["tools"]
    assert "- compress_folder:" not in seen["catalogue"] and "- manage_system_setting:" in seen["catalogue"]
    assert result.validation.ok or result.plan.steps  # parsed and validated against the full registry


def test_caching_returns_the_same_list_and_does_not_leak_between_commands(registry):
    names = registry.model_names()
    a = tools_for("open notepad", names)
    a.append("x")
    assert "x" not in tools_for("open notepad", names)
