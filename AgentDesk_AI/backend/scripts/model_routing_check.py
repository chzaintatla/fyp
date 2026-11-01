#!/usr/bin/env python3
"""Live check of the planner's model routing: gpt-oss-20b vs gpt-oss-120b on the same commands (speed and right tools).

    python scripts/model_routing_check.py [--n 6] [--pause 30] [--out ../docs/eval/u2_models.json]

Uses your Groq key (one call per command per model, about 3k tokens each). The free tier allows about 8k tokens per minute, so calls
are paced (--pause seconds). Commands are drawn from the gold set, skipping follow-ups; the local fast path is bypassed on purpose.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.nlu.planner import BIG_MODEL, SMALL_MODEL, GroqPlanner, PlannerContext  # noqa: E402
from app.tools.base import ToolContext  # noqa: E402
from app.tools.registry import ToolRegistry  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=6)
    ap.add_argument("--pause", type=float, default=30)
    ap.add_argument("--out")
    args = ap.parse_args()
    rows = [json.loads(x) for x in (ROOT / "tests" / "nlu_eval" / "commands.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = [r for r in rows if not r.get("context") and r["expected"].get("steps") and r["lang"] in ("en", "roman_ur")]
    random.Random(7).shuffle(rows)
    registry = ToolRegistry(ToolContext.from_settings())
    result = {SMALL_MODEL: [], BIG_MODEL: []}
    for r in rows[: args.n]:
        want = [s["tool"] for s in r["expected"]["steps"]]
        for model in (SMALL_MODEL, BIG_MODEL):
            planner = GroqPlanner(models=(model,))
            started = time.perf_counter()
            try:
                res = planner.plan(PlannerContext(command=r["command"], registry=registry, check_paths=False))
                got = [s.tool for s in res.plan.steps]
                row = {"id": r["id"], "ms": round((time.perf_counter() - started) * 1000), "right_tools": got == want, "error": None}
            except Exception as exc:  # noqa: BLE001
                row = {"id": r["id"], "ms": round((time.perf_counter() - started) * 1000), "right_tools": False, "error": type(exc).__name__}
            result[model].append(row)
            print(model, row, flush=True)
            time.sleep(args.pause)
    summary = {m: {"n": len(v), "ms_p50": statistics.median(x["ms"] for x in v) if v else None,
                   "right_tools": sum(x["right_tools"] for x in v), "errors": sum(bool(x["error"]) for x in v)} for m, v in result.items()}
    print(json.dumps(summary, indent=1))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({"summary": summary, "rows": result}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
