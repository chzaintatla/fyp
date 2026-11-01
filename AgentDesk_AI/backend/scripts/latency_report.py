#!/usr/bin/env python3
"""Per-stage latency of your REAL use of the app (the log the app writes), against the section-7 budgets. Misses are printed, never hidden.

    python scripts/latency_report.py [path-to-voice_latency.jsonl]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.voice import latency  # noqa: E402


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    rows = latency.stage_report(path)
    print(f"log: {path or latency.log_path()}")
    if not rows:
        print("no rows yet: use the app (voice or text commands) and run this again")
        return 0
    print(f"{'stage':28} {'runs':>5} {'p50 ms':>8} {'p90 ms':>8} {'budget':>7} {'within':>7}  status")
    for r in rows:
        within = "-" if r["within_budget"] is None else f"{r['within_budget'] * 100:.0f}%"
        print(f"{r['stage']:28} {r['runs']:5d} {r['p50_ms']:8.0f} {r['p90_ms']:8.0f} {r['budget_ms'] or '-':>7} {within:>7}  {r['status']}")
    return 1 if any(r["status"] == "MISS" for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
