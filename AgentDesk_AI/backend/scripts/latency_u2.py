#!/usr/bin/env python3
"""U2 latency measurement: 30 single-step commands through the REAL voice pipeline pieces, stage by stage, against a sandboxed app.

    python scripts/latency_u2.py [--runs 30] [--no-stt] [--out ../docs/eval/u2_latency.json]

Per run it times: Whisper (Groq, on audio the app's own TTS made from the command), /voice/ack, the first TTS sentence, POST /tasks
(plan + execute + verify) with the server's own plan / execute_verify split, /voice/line and its TTS. Commands are read-only or touch only
the sandbox folders (apps are NOT launched: opening an app is measured by the app's own log in real use, see scripts/latency_report.py).
NOT measured here: the microphone, the browser, audio playback. Those are added as the configured constants below and labelled "est.".
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from voice_live import start_server  # noqa: E402

VAD_END_SILENCE_MS = 450     # frontend/src/speech/vad.ts DEFAULT_VAD.endSilenceMs
WHISPER_DEBOUNCE_MS = 120    # frontend/src/hooks/useVoiceInput.ts
ACK_BUDGET_MS, DONE_BUDGET_MS = 1500, 5000
COMMANDS = ["show trash", "list the files in Documents", "list the files in Desktop", "list the files in Downloads",
            "what do you remember about me", "remember my exam is on twelve December", "create a folder called Lat{i} in Documents"]


def ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(p / 100 * len(ordered)) - 1))] if ordered else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=30)
    ap.add_argument("--no-stt", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args()
    root = Path(tempfile.mkdtemp(prefix="agentdesk_u2_"))
    server, url, headers = start_server(root)
    rows: list[dict] = []
    try:
        http = httpx.Client(base_url=url, headers=headers, timeout=60)
        who = {"language": "en", "personality": "assistant"}
        http.post("/voice/tts", json={"text": "warm up", "language": "en"})            # the SAPI worker is warm in real use
        stt_ok = not args.no_stt
        for i in range(args.runs):
            command = COMMANDS[i % len(COMMANDS)].format(i=i)
            row: dict = {"run": i}
            if stt_ok:
                audio = http.post("/voice/tts", json={"text": command, "language": "en"})
                if audio.status_code == 200 and len(audio.content) > 2000:
                    started = time.perf_counter()
                    r = http.post("/voice/stt", files={"audio": ("s.wav", audio.content, "audio/wav")}, data={"language": "en"})
                    if r.status_code == 200:
                        row["stt_ms"] = ms(started)
                    else:
                        stt_ok = False
                        print(f"  (speech-to-text unavailable: HTTP {r.status_code}; continuing without it)")
                    time.sleep(3.2)           # stay under the free tier's 20 requests per minute
                else:
                    stt_ok = False
            started = time.perf_counter()
            ack = http.post("/voice/ack", json={"command": command, **who}).json().get("text")
            row["ack_ms"] = ms(started)
            row["ack_text"] = bool(ack)
            started = time.perf_counter()
            http.post("/voice/tts", json={"text": ack or "On it.", "language": "en"})
            row["ack_tts_ms"] = ms(started)
            started = time.perf_counter()
            task = http.post("/tasks", json={"command": command}).json()
            row["task_ms"] = ms(started)
            row["status"] = task.get("status")
            started = time.perf_counter()
            line = http.post("/voice/line", json={"task": task, **who}).json().get("text", "")
            row["line_ms"] = ms(started)
            started = time.perf_counter()
            http.post("/voice/tts", json={"text": line or "Done.", "language": "en"})
            row["line_tts_ms"] = ms(started)
            rows.append(row)
        log = root / "lat.jsonl"
        server_rows = [json.loads(x) for x in log.read_text().splitlines() if '"task"' in x] if log.exists() else []
        for r, s in zip(rows, server_rows):
            r["plan_ms"], r["execute_verify_ms"] = s["ms"]["plan"], s["ms"]["execute_verify"]
        for r in rows:
            front = VAD_END_SILENCE_MS + WHISPER_DEBOUNCE_MS
            stt = r.get("stt_ms", 0.0)
            r["ack_est_ms"] = front + stt + r["ack_ms"] + r["ack_tts_ms"] if r["ack_text"] else None
            r["done_est_ms"] = front + stt + r["task_ms"] + r["line_ms"] + r["line_tts_ms"]
    finally:
        server.terminate()
        server.wait(10)
    have_stt = any("stt_ms" in r for r in rows)

    def stat(key: str) -> dict:
        v = [r[key] for r in rows if r.get(key) is not None]
        return {"n": len(v), "p50": round(statistics.median(v), 1) if v else None, "p90": round(pct(v, 90), 1) if v else None,
                "max": round(max(v), 1) if v else None}

    stages = {k: stat(k) for k in ("stt_ms", "ack_ms", "ack_tts_ms", "plan_ms", "execute_verify_ms", "task_ms", "line_ms",
                                   "line_tts_ms", "ack_est_ms", "done_est_ms")}
    ack_rows = [r for r in rows if r["ack_est_ms"] is not None]
    summary = {
        "runs": len(rows), "with_whisper": have_stt, "statuses": {s: sum(r["status"] == s for r in rows) for s in {r["status"] for r in rows}},
        "ack_share_with_early_ack": round(len(ack_rows) / len(rows), 2) if rows else 0,
        "ack_within_1500ms": f"{sum(r['ack_est_ms'] <= ACK_BUDGET_MS for r in ack_rows)}/{len(ack_rows)}",
        "done_within_5000ms": f"{sum(r['done_est_ms'] <= DONE_BUDGET_MS for r in rows)}/{len(rows)}",
        "estimated_constants_ms": {"vad_end_silence": VAD_END_SILENCE_MS, "whisper_debounce": WHISPER_DEBOUNCE_MS},
    }
    print(json.dumps({"summary": summary, "stages": stages}, indent=1))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({"summary": summary, "stages": stages, "rows": rows}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
