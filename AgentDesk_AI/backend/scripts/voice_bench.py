#!/usr/bin/env python3
"""Voice pipeline benchmark: command -> task -> spoken closing line -> first audio ready, 30 runs.

    python scripts/voice_bench.py [--runs 30] [--stt]

Measured per run (ms, from "end of speech" = the moment the command text is known):
  understood         POST /tasks returns (understand + plan + validate + execute + verify)
  line_ready         the spoken closing line is produced (/voice/line)
  first_audio_ready  the first sentence is synthesized (/voice/tts)  <- lower bound of "first word played"
With --stt each command is first spoken to a WAV with Windows SAPI and sent to /voice/stt (Groq Whisper), so the
transcription time and word accuracy are included. A real microphone, speakers and browser playback are NOT measured
here: the UI reports the true first-word time to /voice/latency (see `GET /voice/latency`).
Exit code 0 when at least 90% of runs have first_audio_ready <= 2500 ms, else 1.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

COMMANDS = ["create a folder called Bench{i} in Documents", "show trash", "find files named bench in Documents",
            "list the files in Documents"]
TARGET_MS = 2500


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _sapi_wav(text: str, out: Path) -> None:
    script = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "$s.SetOutputToWaveFile($env:BENCH_OUT); $s.Speak($env:BENCH_TEXT); $s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, capture_output=True, timeout=30,
                   env=dict(os.environ, BENCH_OUT=str(out), BENCH_TEXT=text))


def pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(round(p / 100 * len(ordered))) - 1))]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--stt", action="store_true", help="include speech-to-text (needs Groq / cloud brain)")
    args = parser.parse_args(argv)
    root = Path(tempfile.mkdtemp(prefix="agentdesk_voicebench_"))
    home = root / "home"
    for sub in ("Documents", "Desktop", "Downloads"):
        (home / sub).mkdir(parents=True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), DATABASE_URL=f"sqlite:///{root / 'v.db'}",
               AGENTDESK_ALLOWED_DIRECTORIES=str(home), AGENTDESK_TOKEN_FILE=str(root / "tok"),
               AGENTDESK_LOG_DIR=str(root / "logs"), AGENTDESK_TTS_CACHE="0",  # cold synthesis: no cached clips
               AGENTDESK_LATENCY_LOG=str(root / "voice_latency.jsonl"))
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"],
                              cwd=Path(__file__).resolve().parents[1], env=env)
    url = f"http://127.0.0.1:{port}"
    rows, engines, correct, total_words = [], {}, 0, 0
    try:
        for _ in range(150):
            try:
                if httpx.get(url + "/health").status_code == 200 and (root / "tok").exists():
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        headers = {"Authorization": "Bearer " + (root / "tok").read_text().strip()}
        http = httpx.Client(base_url=url, headers=headers, timeout=60)
        for i in range(args.runs):
            command = COMMANDS[i % len(COMMANDS)].format(i=i)
            marks = {}
            t0 = time.perf_counter()
            if args.stt:
                wav = root / f"c{i}.wav"
                _sapi_wav(command, wav)
                t0 = time.perf_counter()  # speech ends here
                r = http.post("/voice/stt", files={"audio": ("c.wav", wav.read_bytes(), "audio/wav")}, data={"language": "en"})
                r.raise_for_status()
                heard = r.json()["text"]
                marks["transcribed"] = (time.perf_counter() - t0) * 1000
                want = set(_words(command))
                correct += len(want & set(_words(heard)))
                total_words += len(want)
                command = heard or command
            task = http.post("/tasks", json={"command": command}).json()
            marks["understood"] = (time.perf_counter() - t0) * 1000
            line = http.post("/voice/line", json={"task": task}).json()["text"]
            marks["line_ready"] = (time.perf_counter() - t0) * 1000
            first = re.split(r"(?<=[.!?])\s+", line)[0]
            tts = http.post("/voice/tts", json={"text": first[:200]})
            marks["first_audio_ready"] = (time.perf_counter() - t0) * 1000
            engine = tts.headers.get("x-tts-engine", f"error {tts.status_code}")
            engines[engine] = engines.get(engine, 0) + 1
            rows.append({"command": command, "status": task["status"], "line": line, **{k: round(v) for k, v in marks.items()}})
        ready = [r["first_audio_ready"] for r in rows]
        under = sum(v <= TARGET_MS for v in ready)
        result = {"runs": len(rows), "p50_ms": round(statistics.median(ready)), "p90_ms": round(pct(ready, 90)),
                  "max_ms": max(ready), "under_2500ms": under, "share_under_2500ms": round(under / len(rows), 3),
                  "tts_engines": engines, "statuses": sorted({r["status"] for r in rows})}
        if args.stt:
            result["stt_word_accuracy"] = round(correct / max(1, total_words), 3)
        print(json.dumps(result))
        (root / "voice_bench_rows.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
        print("rows:", root / "voice_bench_rows.json")
        return 0 if under / len(rows) >= 0.9 else 1
    finally:
        server.terminate()
        server.wait(10)


if __name__ == "__main__":
    sys.exit(main())
