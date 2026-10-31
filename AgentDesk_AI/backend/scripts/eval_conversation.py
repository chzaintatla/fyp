#!/usr/bin/env python3
"""Live evaluation of M5: 20 chat + 20 web questions (+ 3 memory checks) through the real task pipeline.

    python scripts/eval_conversation.py [--pace 14] [--json OUT.json]

Starts a throwaway server (temp database; your .env keys are used for Groq / the cloud brain), POSTs each question to /tasks
like the UI does, and grades the answer with expected keywords. The grading is AUTOMATIC keyword matching, so it can mark a
correct answer worded differently as wrong (and the web set depends on today's pages): read the printed answers too.
Exit 0 only if at least 90% of each set is correct.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

CHAT = [
    ("What is the capital of Japan?", r"tokyo"),
    ("What is 12 times 12?", r"\b144\b"),
    ("At what temperature does water boil at sea level in Celsius?", r"\b100\b"),
    ("Who wrote the play Hamlet?", r"shakespeare"),
    ("Which is the largest planet in our solar system?", r"jupiter"),
    ("What is the chemical symbol for gold?", r"\bau\b"),
    ("How many days are there in a leap year?", r"\b366\b"),
    ("What does CPU stand for?", r"central processing unit"),
    ("What is the speed of light in kilometres per second, roughly?", r"299|300"),
    ("What is the square root of 81?", r"\b9\b|nine"),
    ("Explain photosynthesis in one sentence.", r"light|sun"),
    ("What is the difference between RAM and ROM?", r"volatile|temporar|permanent|read.only"),
    ("Explain what an API is in one sentence.", r"interface|application"),
    ("Who is the founder of Pakistan?", r"jinnah"),
    ("Pakistan ki rajdhani kya hai?", r"islamabad|اسلام آباد"),
    ("Pani ka chemical formula kya hai?", r"h2o|h₂o|ایچ"),
    ("Translate 'thank you' into Urdu.", r"shukri|شکریہ"),
    ("Tell me a joke.", r"\w+ \w+ \w+ \w+ \w+"),
    ("hello", r"hello|hi |hey|salam|assalam|help"),
    ("How are you?", r"\w+ \w+ \w+"),
]
WEB = [
    ("Who is the current CEO of Microsoft?", r"nadella"),
    ("Who is the current CEO of Google?", r"pichai"),
    ("Who is the current CEO of Nvidia?", r"huang"),
    ("Who is the current CEO of Amazon?", r"jassy"),
    ("Who is the current CEO of Tesla?", r"musk"),
    ("Who is the current president of France?", r"macron"),
    ("Who won the 2022 FIFA World Cup?", r"argentin|messi"),
    ("Who won the 2023 Cricket World Cup?", r"austral"),
    ("Who won the men's singles at Wimbledon in 2023?", r"alcaraz"),
    ("Who won the Nobel Peace Prize in 2023?", r"mohammadi"),
    ("What is the latest stable version of Python?", r"3\.1[3-9]|3\.[2-9]\d"),
    ("What is the weather in Karachi today?", r"°|degree|celsius|temperature|\d\s?c\b|sunny|cloud|clear|haz|humid|rain"),
    ("How much is one US dollar in Pakistani rupees right now?", r"\b2\d\d\b|\b3\d\d\b|rupee|pkr"),
    ("What is the price of one bitcoin in US dollars right now?", r"\$|dollar|usd|\d{2,3},\d{3}|\d{5,}"),
    ("What is the latest news about NASA?", r"nasa"),
    ("Who is the current Secretary-General of the United Nations?", r"guterres|secretary"),
    ("Who is the current head coach of the Pakistan men's cricket team?", r"coach|cricket|pakistan"),
    ("What is today's top headline in technology?", r"\w+ \w+ \w+ \w+ \w+"),
    ("What is the latest iPhone model?", r"iphone"),
    ("Who is the current Prime Minister of the United Kingdom?", r"starmer|prime minister"),
]
MEMORY = [
    ("remember my name is Nabiha", None, r"nabiha"),
    ("what do you remember about me?", None, r"nabiha"),
    ("what is my name?", None, r"nabiha"),
]


def start_server(root: Path) -> tuple[subprocess.Popen, str, dict]:
    home = root / "home"
    home.mkdir()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), DATABASE_URL=f"sqlite:///{root / 'e.db'}",
               AGENTDESK_ALLOWED_DIRECTORIES=str(home), AGENTDESK_TOKEN_FILE=str(root / "tok"), AGENTDESK_LOG_DIR=str(root / "logs"))
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"],
                            cwd=Path(__file__).resolve().parents[1], env=env)
    url = f"http://127.0.0.1:{port}"
    for _ in range(150):
        try:
            if httpx.get(url + "/health").status_code == 200 and (root / "tok").exists():
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    return proc, url, {"Authorization": "Bearer " + (root / "tok").read_text().strip()}


def ask(http: httpx.Client, question: str) -> tuple[dict, str, float]:
    started = time.perf_counter()
    task = http.post("/tasks", json={"command": question}).json()
    if task.get("status") == "approved":
        task = http.post(f"/tasks/{task['id']}/run").json()
    from app.voice import responses  # same text the user would hear
    return task, responses.line_for_task(task), time.perf_counter() - started


def run_set(http, items, pace: float, label: str) -> list[dict]:
    rows = []
    for i, (question, pattern) in enumerate(items, 1):
        for attempt in (1, 2):
            task, said, seconds = ask(http, question)
            retryable = task["status"] in {"failed", "waiting_retry"} and re.search(r"busy|rate|limit|429|tokens", json.dumps(task), re.I)
            if not retryable or attempt == 2:
                break
            time.sleep(45)  # the free tier allows ~8k tokens a minute for the whole organisation
        sources = (((task.get("actions") or [{}])[0].get("result") or {}).get("output") or {}).get("sources", [])
        ok = task["status"] == "completed" and bool(re.search(pattern, said, re.I))
        if label == "web":
            ok = ok and len(sources) >= 1  # a web answer must come from at least one page that was read
        rows.append({"set": label, "n": i, "question": question, "ok": ok, "status": task["status"], "seconds": round(seconds, 1),
                     "sources": len(sources), "answer": said})
        print(f"{'PASS' if ok else 'FAIL'}  {label} {i:2d}  {seconds:5.1f}s  {question[:55]:55s} -> {said[:110]}", flush=True)
        time.sleep(pace)
    return rows


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--pace", type=float, default=14.0, help="seconds between questions (Groq free-tier token limit)")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    root = Path(tempfile.mkdtemp(prefix="agentdesk_eval_"))
    proc, url, headers = start_server(root)
    try:
        http = httpx.Client(base_url=url, headers=headers, timeout=120)
        http.put("/preferences", json={"onboarded": True, "cloud_answers": True})  # the user allowed cloud answers
        rows = run_set(http, CHAT, args.pace, "chat") + run_set(http, WEB, args.pace, "web")
        mem_rows = []
        for question, _, pattern in MEMORY:
            task, said, seconds = ask(http, question)
            ok = task["status"] == "completed" and bool(re.search(pattern, said, re.I))
            mem_rows.append({"set": "memory", "question": question, "ok": ok, "answer": said})
            print(f"{'PASS' if ok else 'FAIL'}  memory     {seconds:5.1f}s  {question[:55]:55s} -> {said[:110]}", flush=True)
            time.sleep(args.pace)
        http.delete("/memory")
        summary = {name: {"correct": sum(r["ok"] for r in rows if r["set"] == name), "total": sum(r["set"] == name for r in rows)} for name in ("chat", "web")}
        summary["memory"] = {"correct": sum(r["ok"] for r in mem_rows), "total": len(mem_rows)}
        print(json.dumps(summary))
        if args.json:
            args.json.write_text(json.dumps({"summary": summary, "rows": rows + mem_rows}, indent=1, ensure_ascii=False), encoding="utf-8")
        return 0 if all(v["correct"] / v["total"] >= 0.9 for k, v in summary.items() if k != "memory") and summary["memory"]["correct"] == 3 else 1
    finally:
        proc.terminate()
        proc.wait(10)


if __name__ == "__main__":
    sys.exit(main())
