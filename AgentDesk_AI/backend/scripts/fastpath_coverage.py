#!/usr/bin/env python3
"""How many commands the local fast path (no network) understands, and how fast. Uses the gold commands in tests/nlu_eval.

    python scripts/fastpath_coverage.py [--out ../docs/eval/u2_fastpath.json]

"Understands" = the local matcher returns a plan at or above the planner's confidence threshold AND its tool sequence equals the gold one.
Commands that need earlier turns (a `context`) are skipped: they are follow-ups, not first commands.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.nlu.local_matcher import THRESHOLD, match  # noqa: E402
from app.nlu.memory import ConversationMemory  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    args = ap.parse_args()
    rows = [json.loads(x) for x in (ROOT / "tests" / "nlu_eval" / "commands.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = [r for r in rows if not r.get("context") and r["expected"].get("steps")]
    by_lang: dict[str, list[int]] = {}
    by_split: dict[str, list[int]] = {}
    times: list[float] = []
    for r in rows:
        started = time.perf_counter()
        m = match(r["command"], ConversationMemory())
        times.append((time.perf_counter() - started) * 1000)
        got = [s.tool for s in m.plan.steps] if m.plan and m.confidence >= THRESHOLD else None
        want = [s["tool"] for s in r["expected"]["steps"]]
        slot = by_lang.setdefault(r["lang"], [0, 0, 0])
        slot[0] += 1
        slot[1] += got is not None
        slot[2] += got == want
        sp = by_split.setdefault(r.get("split", "?"), [0, 0, 0])
        sp[0] += 1
        sp[1] += got is not None
        sp[2] += got == want
    total = [sum(v[i] for v in by_lang.values()) for i in range(3)]
    result = {"commands": total[0], "answered_locally": total[1], "answered_locally_and_right_tools": total[2],
              "share_answered_pct": round(100 * total[1] / total[0], 1), "share_right_pct": round(100 * total[2] / total[0], 1),
              "by_language": {k: {"n": v[0], "answered": v[1], "right": v[2]} for k, v in by_lang.items()},
              "by_split": {k: {"n": v[0], "answered": v[1], "right": v[2]} for k, v in by_split.items()},
              "ms_p50": round(statistics.median(times), 2), "ms_max": round(max(times), 2)}
    print(json.dumps(result, indent=1))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
