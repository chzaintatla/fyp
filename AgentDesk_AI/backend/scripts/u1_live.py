"""U1 live check on THIS PC: builds the real entity index, times each source, and tries sample misheard commands.

Prints counts and timings only (and app names for the samples); file names stay on the machine.
Usage: python scripts/u1_live.py [--say "fire fox" --say "note pad" ...]
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.nlu.entity_index import DEFAULT_SOURCES, EntityIndex  # noqa: E402
from app.nlu.entity_match import candidates_for_command, resolve  # noqa: E402
from app.voice.stt_vocab import build_prompt  # noqa: E402

SAMPLES = ["fire fox", "note pad", "calculater", "vs cold", "power point", "whats app", "task manger", "file explorar", "setings", "sniping tool"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--say", action="append", default=[])
    args = ap.parse_args()
    print("== index sources ==")
    for source in DEFAULT_SOURCES:
        t = time.perf_counter()
        try:
            n = len(source())
            note = ""
        except Exception as exc:  # noqa: BLE001
            n, note = 0, f"  FAILED: {exc}"
        print(f"  {source.__name__:22s} {n:6d} names  {time.perf_counter() - t:5.2f} s{note}")
    index = EntityIndex()
    t = time.perf_counter()
    total = index.refresh()
    print(f"  total {total} unique names in {time.perf_counter() - t:.2f} s (background rebuild, never on the voice path)")
    apps = index.entries(["app"])
    apps_p = index.prepared(["app"])
    print(f"  apps {len(apps)}, files {len(index.entries(['file']))}, folders {len(index.entries(['folder']))}")
    prompt = build_prompt(index, {}, None)
    print(f"== Whisper hint: {len(prompt)} characters (limit 800) ==")
    print("== sample misheard app names ==")
    times = []
    for phrase in SAMPLES + args.say:
        t = time.perf_counter()
        d = resolve(phrase, apps_p)
        ms = (time.perf_counter() - t) * 1000
        times.append(ms)
        top = ", ".join(f"{c.name} {c.score:.2f}" for c in d.candidates[:3])
        print(f"  {phrase!r:18} -> {d.action:8} {d.understood or '-':28} [{top}]  {ms:.1f} ms")
    allp = index.prepared()
    t = time.perf_counter()
    candidates_for_command("open fire fox and note pad", allp, 3)
    print(f"== planner hint over {len(allp.entries)} names: {(time.perf_counter() - t) * 1000:.0f} ms ==")
    print(f"== resolve median {statistics.median(times):.1f} ms, max {max(times):.1f} ms ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
