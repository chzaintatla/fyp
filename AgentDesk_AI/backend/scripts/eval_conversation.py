#!/usr/bin/env python3
"""Live evaluation of M5: 20 chat + 20 web questions (+ 3 memory checks) through the real task pipeline.

    python scripts/eval_conversation.py [--pace 8] [--csv OUT.csv] [--json OUT.json]

Starts a throwaway server (temp database; your backend/.env keys, WIKIPEDIA_CONTACT_EMAIL and the Tavily key from Windows
Credential Manager are used), POSTs each question to /tasks like the UI does, and grades every answer TWO ways:
  * keyword  - a regular expression for the expected fact (strict, cheap, can reject a right answer worded differently);
  * model    - gpt-oss-120b with a rubric: correct / partly / wrong (reads the question, the answer and the expected fact).
Both scores are printed and written to the CSV so you can spot-check any row. "Correct" for the target means model grade
"correct" (and, for web questions, at least one source was read). The grader can be wrong too: the CSV is the evidence.
Exit 0 only if chat >= 18/20 and web >= 18/20 (model-graded correct) and memory 3/3.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from datetime import date
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
    ("hello", r"hello|hi |hey|salam|assalam|assist|help|good (morning|afternoon|evening)"),
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
    ("remember my name is Nabiha", r"nabiha"),
    ("what do you remember about me?", r"nabiha"),
    ("what is my name?", r"nabiha"),
]

RUBRIC = (
    "You grade answers from a voice assistant. Today is {today}. Grade ONE answer:\n"
    "correct = it answers the question and is factually right (any wording, any language; short is fine);\n"
    "partly  = right in part, hedged, incomplete, or right but with a wrong extra detail;\n"
    "wrong   = incorrect, off-topic, a refusal, an error message, or no answer.\n"
    "For questions about the present (current office holders, prices, weather, news) judge plausibility against the reference hint and "
    "the sources' titles; if the answer says it could not find the information, that is wrong.\n"
    'Reply with JSON only: {{"grade": "correct|partly|wrong", "reason": "<one short sentence>"}}')


def grader():
    """gpt-oss-120b through the same service the app uses (cloud brain if configured, else Groq)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.config import get_settings
    settings = get_settings()
    if settings.cloud_configured:
        from app.cloud_client import CloudBrain
        brain = CloudBrain(settings.cloud_brain_url, settings.cloud_brain_token)
        return lambda messages: brain.chat(messages, fast=False)
    from app.ai.groq_client import get_groq_client
    client = get_groq_client()
    return lambda messages: (client.chat(messages, temperature=0, max_tokens=200).get("content") or "")


def model_grade(call, question: str, answer: str, hint: str, sources: list[str]) -> tuple[str, str]:
    prompt = (f"Question: {question}\nAnswer given: {answer}\nReference hint (a pattern the right answer should match): {hint}\n"
              f"Source titles: {'; '.join(sources) or 'none'}")
    messages = [{"role": "system", "content": RUBRIC.format(today=date.today().isoformat())}, {"role": "user", "content": prompt}]
    for attempt in (1, 2):
        try:
            text = call(messages)
            data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
            grade = str(data.get("grade", "")).strip().lower()
            if grade in {"correct", "partly", "wrong"}:
                return grade, str(data.get("reason", ""))[:160]
        except Exception as exc:  # noqa: BLE001 - retried once, then reported as ungraded
            if attempt == 2:
                return "ungraded", f"{type(exc).__name__}: {str(exc)[:100]}"
            time.sleep(20)
    return "ungraded", "unparseable grader reply"


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
    from app.voice import responses  # the text the user would hear
    return task, responses.line_for_task(task), time.perf_counter() - started


def run_set(http, call, items, pace: float, label: str) -> list[dict]:
    rows = []
    for i, (question, pattern) in enumerate(items, 1):
        for attempt in (1, 2):
            task, said, seconds = ask(http, question)
            retryable = task["status"] in {"failed", "waiting_retry"} and re.search(r"busy|rate|limit|429|tokens", json.dumps(task), re.I)
            if not retryable or attempt == 2:
                break
            time.sleep(60)  # "Search is busy": wait a minute, as the assistant tells the user to
        output = (((task.get("actions") or [{}])[0].get("result") or {}).get("output") or {})
        sources = output.get("sources", [])
        keyword_ok = task["status"] == "completed" and bool(re.search(pattern, said, re.I))
        grade, reason = model_grade(call, question, said, pattern, [s.get("title", "") for s in sources])
        ok = grade == "correct" and task["status"] == "completed" and (label != "web" or len(sources) >= 1)
        rows.append({"set": label, "n": i, "question": question, "answer": said, "status": task["status"], "seconds": round(seconds, 1),
                     "sources": len(sources), "providers": ",".join(sorted({s.get("provider", "") for s in sources})),
                     "prompt_tokens": output.get("prompt_tokens", ""), "keyword_ok": keyword_ok, "model_grade": grade, "model_reason": reason, "ok": ok})
        print(f"{'PASS' if ok else 'FAIL'}  {label} {i:2d} {seconds:5.1f}s kw={'Y' if keyword_ok else 'n'} model={grade:7s} {question[:48]:48s} -> {said[:90]}", flush=True)
        time.sleep(pace)
    return rows


def score(rows: list[dict], name: str) -> dict:
    mine = [r for r in rows if r["set"] == name]
    return {"total": len(mine), "model_correct": sum(r["model_grade"] == "correct" for r in mine),
            "model_partly": sum(r["model_grade"] == "partly" for r in mine), "model_wrong": sum(r["model_grade"] == "wrong" for r in mine),
            "ungraded": sum(r["model_grade"] == "ungraded" for r in mine), "keyword_correct": sum(r["keyword_ok"] for r in mine),
            "target_met_correct": sum(r["ok"] for r in mine)}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--pace", type=float, default=8.0, help="seconds between questions (Groq free-tier token limit)")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--csv", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    call = grader()
    root = Path(tempfile.mkdtemp(prefix="agentdesk_eval_"))
    proc, url, headers = start_server(root)
    try:
        http = httpx.Client(base_url=url, headers=headers, timeout=180)
        http.put("/preferences", json={"onboarded": True, "cloud_answers": True})  # the user allowed cloud answers
        rows = run_set(http, call, CHAT, args.pace, "chat") + run_set(http, call, WEB, args.pace, "web") + run_set(http, call, MEMORY, args.pace, "memory")
        http.delete("/memory")
        summary = {name: score(rows, name) for name in ("chat", "web", "memory")}
        print(json.dumps(summary))
        if args.json:
            args.json.write_text(json.dumps({"date": date.today().isoformat(), "summary": summary, "rows": rows}, indent=1, ensure_ascii=False), encoding="utf-8")
        if args.csv:
            with args.csv.open("w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        met = summary["chat"]["target_met_correct"] >= 18 and summary["web"]["target_met_correct"] >= 18 and summary["memory"]["target_met_correct"] == 3
        return 0 if met else 1
    finally:
        proc.terminate()
        proc.wait(10)


if __name__ == "__main__":
    sys.exit(main())
