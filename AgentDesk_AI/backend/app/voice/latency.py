"""Per-stage latency log for the voice loop. One JSON line per utterance; no transcript text is stored."""
from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path

STAGES = ("speech_end", "transcribed", "understood", "first_audio_ready", "ack_first_word", "first_word_played", "done")
# Section 7 of the upgrade spec. Milliseconds from the end of the user's speech, except the server rows (plan / execute_verify / total),
# which are measured inside the app from the moment the command arrives.
BUDGET_MS = {"transcribed": 1100, "ack_first_word": 1500, "first_word_played": 2500, "done": 5000,
             "plan_local": 100, "plan_cloud": 1200, "execute_verify": 2000, "total_local": 2500}
_lock = threading.Lock()


def log_path() -> Path:
    base = os.environ.get("AGENTDESK_LATENCY_LOG")
    if base:
        return Path(base)
    root = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "AgentDesk" / "pilot"
    return root / "voice_latency.jsonl"


class Utterance:
    """Mark stages as they happen; `finish()` appends durations (ms since speech_end) to the log."""

    def __init__(self, kind: str = "local", path: Path | None = None):
        self.kind, self.path, self.marks = kind, path or log_path(), {}

    def mark(self, stage: str, at: float | None = None) -> None:
        self.marks[stage] = at if at is not None else time.perf_counter()

    def durations_ms(self) -> dict[str, float]:
        start = self.marks.get("speech_end")
        if start is None:
            return {}
        return {s: round((t - start) * 1000, 1) for s, t in self.marks.items() if s != "speech_end"}

    def finish(self) -> dict:
        row = {"ts": round(time.time(), 3), "kind": self.kind, "ms": self.durations_ms()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        return row


def record_task(route: str, plan_ms: float, run_ms: float, path: Path | None = None) -> None:
    """Server-side row for one command: how long planning took and how long running + verifying took (no command text)."""
    p = path or log_path()
    row = {"ts": round(time.time(), 3), "kind": "task", "route": route,
           "ms": {"plan": round(plan_ms, 1), "execute_verify": round(run_ms, 1), "total": round(plan_ms + run_ms, 1)}}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with _lock, p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        pass  # a full disk must never fail a command


def stage_report(path: Path | None = None) -> list[dict]:
    """Per stage: runs, p50, p90 and the budget, with a MISS flag when the median or p90 is over it (never hidden)."""
    p = path or log_path()
    rows: list[dict] = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    out: list[dict] = []

    def add(name: str, values: list[float], budget: int | None) -> None:
        if not values:
            return
        p50, p90 = percentile(values, 50), percentile(values, 90)
        out.append({"stage": name, "runs": len(values), "p50_ms": round(p50, 1), "p90_ms": round(p90, 1), "budget_ms": budget,
                    "within_budget": None if budget is None else sum(v <= budget for v in values) / len(values),
                    "status": "n/a" if budget is None else ("ok" if p90 <= budget else "MISS")})

    for stage in ("transcribed", "ack_first_word", "first_word_played", "done"):
        add(stage, [r["ms"][stage] for r in rows if r.get("kind") != "task" and stage in r.get("ms", {})], BUDGET_MS[stage])
    for route in ("local", "cloud"):
        sel = [r["ms"] for r in rows if r.get("kind") == "task" and r.get("route") == route]
        add(f"plan ({route})", [m["plan"] for m in sel], BUDGET_MS["plan_local" if route == "local" else "plan_cloud"])
        add(f"execute_verify ({route})", [m["execute_verify"] for m in sel], BUDGET_MS["execute_verify"])
        add(f"total in app ({route})", [m["total"] for m in sel], BUDGET_MS["total_local"] if route == "local" else None)
    return out


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(pct / 100 * len(ordered)) - 1))]


def summarize(path: Path | None = None, stage: str = "first_word_played", kind: str | None = None) -> dict:
    """p50/p90 of `stage`, and the share under the 2.5 s local target."""
    values: list[float] = []
    p = path or log_path()
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if kind and row.get("kind") != kind:
                continue
            if stage in row.get("ms", {}):
                values.append(row["ms"][stage])
    under = sum(v <= 2500 for v in values)
    return {"stage": stage, "runs": len(values), "p50_ms": percentile(values, 50), "p90_ms": percentile(values, 90),
            "under_2500ms": under, "share_under_2500ms": (under / len(values)) if values else 0.0}
