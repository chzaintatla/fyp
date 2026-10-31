"""Per-stage latency log for the voice loop. One JSON line per utterance; no transcript text is stored."""
from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path

STAGES = ("speech_end", "transcribed", "understood", "first_audio_ready", "first_word_played", "done")
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
