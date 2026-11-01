#!/usr/bin/env python3
"""Step 1g: replay the NLU evaluation commands through the OLD local path and the NEW strict planner, side by side.

The app's `PLANNER_MODE=shadow` does the same thing one real command at a time. This replays a fixed, gold-labelled set
(`tests/nlu_eval/commands.jsonl`, single-turn items only) so the comparison is repeatable and scored against the expected plan.

  old  = what the legacy path plans without the cloud: `prepare_local_request`, then the Phase 15 matcher fallback.
         None means the legacy path has no local plan and would hand the command to the Groq agent loop.
  new  = the strict-JSON planner asked for the plan with the LLM only (cloud brain, then Groq), validated like in the app.

Needs a cloud brain or a Groq key (backend/.env) and spends API quota: the planner request is ~3k tokens and the free tier
allows 8k tokens/minute per organization, so the default pace is one call every 45 seconds (200 commands ~ 2.5 hours; the request is ~3k tokens with the tool shortlist).
The run is resumable: results are appended per command and finished ids are skipped on the next start.
A temporary database is used; your tasks and memory are not touched. Command text is not written to the output (ids only).

Usage (from backend/):
    python scripts/shadow_replay.py --limit 5                 # a small pilot
    python scripts/shadow_replay.py --limit 200 --out ../docs/eval/1g_shadow_main
    python scripts/shadow_replay.py --summarise-only --out ../docs/eval/1g_shadow_main
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "scripts"))
os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(Path(tempfile.mkdtemp(prefix="agentdesk_shadow_")) / "shadow.db").replace("\\", "/"))

DATASET = BACKEND_DIR / "tests" / "nlu_eval" / "commands.jsonl"
LANGS = ("en", "roman_ur", "ur", "hi")
RATE_LIMIT_WAIT = 70.0
MAX_RETRIES = 3


def load_items(split: str = "all") -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r.get("context") is None and (split == "all" or r["split"] == split)]


def _actions(plan) -> list[tuple[str, dict[str, Any]]] | None:
    return None if plan is None else [(a.tool, a.args) for a in plan.actions]


def old_plan(db, command: str, registry):
    """The legacy local plan for a command, or None."""
    from app.models import Task, TaskStatus
    from app.orchestration.executor import _nlu_fallback
    from app.orchestration.local_agent import prepare_local_request

    task = Task(command=command, status=TaskStatus.CREATED)
    db.add(task)
    db.commit()
    try:
        plan = prepare_local_request(db, task, registry)
        if plan is None:
            plan = _nlu_fallback(command, registry)
    except Exception:  # noqa: BLE001 - one odd command must not stop the run
        plan = None
    db.delete(task)
    db.commit()
    return plan if plan is not None and plan.actions else None


def score_row(registry, item: dict[str, Any], old, new, info: dict[str, Any]) -> dict[str, Any]:
    from app.orchestration import planner_mode
    from eval_nlu import args_match  # same directory; the scoring rules of the NLU gate

    gold = item["expected"]["steps"]
    gold_tools = [s["tool"] for s in gold]
    has_refs = any(s.get("depends_on") is not None for s in gold)
    old_a, new_a = _actions(old), _actions(new)

    def tools_ok(actions) -> bool:
        return [t for t, _ in (actions or [])] == gold_tools

    def args_ok(actions) -> bool:
        if not actions or has_refs or len(actions) != len(gold):
            return False
        return all(args_match(registry, {**g, "depends_on": None}, {"tool": t, "args": a, "depends_on": None}) for g, (t, a) in zip(gold, actions))

    row: dict[str, Any] = {
        "id": item["id"], "lang": item["lang"], "split": item["split"], "gold_intent": item["expected"]["intent"],
        "gold_tools": gold_tools, "gold_has_refs": has_refs,
        "old_tools": [t for t, _ in old_a] if old_a is not None else None,
        "new_tools": [t for t, _ in new_a] if new_a is not None else None,
        "new_intent": getattr(new, "intent", None),
        "old_gold_tools": tools_ok(old_a), "new_gold_tools": tools_ok(new_a),
        "old_gold_args": args_ok(old_a), "new_gold_args": args_ok(new_a),
        "source": info.get("source"), "model": info.get("model"), "latency_ms": round(float(info.get("latency_ms") or 0), 1),
        "error": info.get("error") or "", "retries": info.get("retries", 0),
    }
    if old is not None and new is not None:
        row.update(planner_mode.compare(old, new))
    return row


def replay(items: list[dict[str, Any]], out: Path, plan_fn: Callable[[str], tuple[Any, dict[str, Any]]],
           min_interval: float = 45.0, sleep: Callable[[float], None] = time.sleep) -> list[dict[str, Any]]:
    """Run every item not already in out/shadow.jsonl; append one JSON row per command; return all rows."""
    from app.database import Base, SessionLocal, engine
    from app.tools.base import ToolContext
    from app.tools.registry import ToolRegistry

    Base.metadata.create_all(engine)
    registry = ToolRegistry(ToolContext.from_settings())
    out.mkdir(parents=True, exist_ok=True)
    path = out / "shadow.jsonl"
    done = {json.loads(line)["id"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()} if path.exists() else set()
    last_call = 0.0
    with SessionLocal() as db, path.open("a", encoding="utf-8") as handle:
        for n, item in enumerate(items, 1):
            if item["id"] in done:
                continue
            old = old_plan(db, item["command"], registry)
            wait = min_interval - (time.monotonic() - last_call)
            if wait > 0 and last_call:
                sleep(wait)
            retries = 0
            while True:
                last_call = time.monotonic()
                new, info = plan_fn(item["command"])
                if info.get("error") != "AIRateLimitError" or retries >= MAX_RETRIES:
                    break
                retries += 1
                sleep(RATE_LIMIT_WAIT)
            info["retries"] = retries
            row = score_row(registry, item, old, new, info)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            print(f"[{n}/{len(items)}] {item['id']:<12} old={row['old_tools']} new={row['new_tools']} gold={row['gold_tools']} {row['error']}".rstrip(), flush=True)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _rate(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    return {"n": len(rows), "ok": sum(bool(r.get(key)) for r in rows), "rate": round(sum(bool(r.get(key)) for r in rows) / len(rows), 4) if rows else None}


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Old vs new, overall and per language. `new` counts a planner failure (rate limit, rejected plan) as a miss."""
    by_lang: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_lang[r["lang"]].append(r)
    def block(sub: list[dict[str, Any]]) -> dict[str, Any]:
        sub_args = [r for r in sub if not r["gold_has_refs"]]
        sub_both = [r for r in sub if r["old_tools"] is not None and r["new_tools"] is not None]
        return {
            "commands": len(sub),
            "old_has_local_plan": sum(r["old_tools"] is not None for r in sub),
            "new_planner_failed": sum(bool(r["error"]) for r in sub),
            "gold_tools_old": _rate(sub, "old_gold_tools"), "gold_tools_new": _rate(sub, "new_gold_tools"),
            "gold_args_old": _rate(sub_args, "old_gold_args"), "gold_args_new": _rate(sub_args, "new_gold_args"),
            "old_vs_new_when_both_plan": {"n": len(sub_both), "tools_match": sum(bool(r.get("tools_match")) for r in sub_both),
                                          "args_match": sum(bool(r.get("args_match")) for r in sub_both)},
        }

    latencies = sorted(r["latency_ms"] for r in rows if r["latency_ms"] and not r["error"])
    out = {"overall": block(rows), "by_language": {lang: block(by_lang[lang]) for lang in LANGS if lang in by_lang},
           "new_latency_ms": {"median": latencies[len(latencies) // 2] if latencies else None,
                              "p90": latencies[int(len(latencies) * 0.9) - 1] if latencies else None},
           "new_errors": dict(sorted(Counter(r["error"] for r in rows if r["error"]).items())),
           "note": "gold = tests/nlu_eval/commands.jsonl (single-turn items). 'old' None = the legacy path would call the Groq agent loop."}
    return out


def live_plan_fn(registry_holder: dict[str, Any]) -> Callable[[str], tuple[Any, dict[str, Any]]]:
    from app.config import get_settings
    from app.orchestration import planner_mode
    from app.tools.base import ToolContext
    from app.tools.registry import ToolRegistry

    settings = get_settings()
    if not planner_mode._enabled(settings):
        raise SystemExit("No cloud brain or Groq key is configured (backend/.env): nothing to compare against.")
    registry = ToolRegistry(ToolContext.from_settings())
    registry_holder["registry"] = registry
    return lambda command: planner_mode._planner_plan(command, registry, settings, llm_only=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--limit", type=int, default=200, help="how many commands (default 200)")
    parser.add_argument("--split", choices=("all", "dev", "holdout"), default="all")
    parser.add_argument("--min-interval", type=float, default=45.0, help="seconds between planner calls (token limit: ~3.5k tokens per call, 8k per minute shared with the app)")
    parser.add_argument("--out", default=str(BACKEND_DIR.parent / "docs" / "eval" / "1g_shadow"))
    parser.add_argument("--summarise-only", action="store_true")
    args = parser.parse_args(argv)
    out = Path(args.out)
    if args.summarise_only:
        rows = [json.loads(line) for line in (out / "shadow.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        items = load_items(args.split)
        # Stratified: take the same share of every language so a short run is not all English.
        per_lang = {lang: [i for i in items if i["lang"] == lang] for lang in LANGS}
        ordered = [i for group in zip(*per_lang.values()) for i in group] + [i for lang in LANGS for i in per_lang[lang][min(len(v) for v in per_lang.values()):]]
        rows = replay(ordered[: args.limit], out, live_plan_fn({}), args.min_interval)
    summary = summarise(rows)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary["overall"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
