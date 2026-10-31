#!/usr/bin/env python3
"""M1 live Groq smoke test. Needs GROQ_API_KEY (env or backend/.env). Costs a few cents of free-tier tokens.

Checks, each PASS/FAIL/SKIP, then exits non-zero on any FAIL:
  1 chat           gpt-oss-120b answers a tiny prompt
  2 schema         Groq ACCEPTS the strict registry-generated plan schema (the main M1 risk)
  3 plans          3 commands -> validated plans with the expected tool
  4 stt            Whisper transcribes a short WAV (only with --wav FILE)
  5 tts            Orpheus speaks a sentence (only with --tts; preview model, may be withdrawn)

Usage (from backend/):  python scripts/groq_smoke.py [--wav sample.wav] [--tts] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.config import Settings  # noqa: E402
from app.nlu.planner import GroqPlanner, PlannerContext  # noqa: E402
from app.nlu.schema import plan_json_schema  # noqa: E402
from app.tools.base import ToolContext  # noqa: E402
from app.tools.registry import ToolRegistry  # noqa: E402

PLAN_CASES = [("open notepad", "open_application"), ("chrome kholo", "open_application"),
              ("create a folder called Smoke in documents", "create_folder")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav")
    ap.add_argument("--tts", action="store_true")
    ap.add_argument("--json")
    args = ap.parse_args()
    key = os.environ.get("GROQ_API_KEY") or (Settings().resolved_groq_keys() or [None])[0]
    if not key:
        print("SKIP: no GROQ_API_KEY (set it in the environment or backend/.env)")
        return 2
    os.environ["GROQ_API_KEY"] = key
    from app.ai.groq_client import GroqClient

    client = GroqClient.from_settings()
    registry = ToolRegistry(ToolContext(allowed_directories=[Path.home()], approved_applications=["chrome", "notepad"],
                                        test_dir=Path.home()))
    results: list[dict] = []

    def run(name, fn):
        t = time.perf_counter()
        try:
            detail = fn()
            status = "PASS"
        except Exception as exc:  # report, never hide
            status, detail = "FAIL", f"{type(exc).__name__}: {str(exc)[:300]}"
        results.append({"check": name, "status": status, "detail": detail, "seconds": round(time.perf_counter() - t, 2)})
        print(f"{status:5} {name:8} {detail}")

    def chat():
        r = client.chat([{"role": "user", "content": "Reply with exactly: ok"}], model="openai/gpt-oss-120b")
        text = r["content"] or ""
        assert "ok" in text.lower(), text[:100]
        return "gpt-oss-120b replied"

    def schema():
        s = plan_json_schema(registry)
        size = len(json.dumps(s))
        res = GroqPlanner(client=client).plan(PlannerContext(command="open notepad", registry=registry))
        note = f" (120b failed, used {res.model}: {res.errors[0][:120]})" if res.errors else ""
        return f"strict schema accepted by {res.model} ({len(registry.names())} tools, {size} bytes){note}"

    def plans():
        planner, got = GroqPlanner(client=client), []
        for cmd, tool in PLAN_CASES:
            res = planner.plan(PlannerContext(command=cmd, registry=registry))
            assert res.validation.ok, (cmd, res.validation.errors)
            assert res.plan.steps and res.plan.steps[0].tool == tool, (cmd, [s.tool for s in res.plan.steps])
            got.append(f"{res.model} {res.latency_ms:.0f}ms")
        return "; ".join(got)

    run("chat", chat)
    run("schema", schema)
    run("plans", plans)
    if args.wav:
        run("stt", lambda: "transcript: " + str(client.transcribe(Path(args.wav).read_bytes(), filename=Path(args.wav).name).get("text"))[:120])
    if args.tts:
        run("tts", lambda: f"{len(client.speak('Done. Chrome is open.'))} bytes of audio")
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 1 if any(r["status"] == "FAIL" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
