#!/usr/bin/env python3
"""U5 live check of the terminal tool on THIS PC: opens the visible AgentDesk terminal window and runs real commands in a folder of yours.

    python scripts/terminal_live.py [--folder F:\\path\\to\\a\\git\\project] [--keep] [--out ../docs/eval/u5_terminal_live.json]

Read-only commands run as they would in the app (no approval). Two "approved" commands are run directly through the tool, standing in for your click:
a harmless python one-liner, and a one-liner that runs too long (it must be stopped). Nothing is deleted or changed. A window titled
"AgentDesk terminal" appears and follows everything live; it is closed at the end unless --keep is given.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("AGENTDESK_TERMINAL_DIR", str(Path(tempfile.gettempdir()) / "agentdesk_u5_live"))

from app.tools.base import ToolContext  # noqa: E402
from app.tools.terminal import RunTerminalTool, load_state  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", default=str(ROOT.parent))
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args()
    folder = Path(args.folder).resolve()
    tool = RunTerminalTool(ToolContext(allowed_directories=[folder]))
    rows = []

    def go(command: str, expect_risk: str, expect_exit: int | None = 0, expect_text: str = "", **kw) -> None:
        a = tool.validate_args({"command": command, **kw})
        started = time.time()
        risk = tool.risk_for_args(a).value
        result = tool.execute(a)
        ver = tool.verify(a, result) if result.success else None
        out = result.output or {}
        row = {"command": command, "risk": risk, "expected_risk": expect_risk, "ran": result.success, "verified": bool(ver and ver.passed),
               "exit_code": out.get("exit_code"), "seconds": round(time.time() - started, 2), "spoken": out.get("spoken") or result.error}
        rows.append(row)
        good = risk == expect_risk and result.success and ver is not None and (out.get("timed_out") if expect_exit is None else out.get("exit_code") == expect_exit)
        good = good and expect_text in " ".join(out.get("lines") or [])
        row["good"] = bool(good)
        flag = "PASS" if good else "FAIL"
        print(f"[{flag}] {command!r:46} risk={risk:6} verified={row['verified']!s:5} {row['seconds']:5.2f}s | {row['spoken']}", flush=True)

    go("pwd", "low", cwd=str(folder))
    go("dir", "low")
    go("git status", "low")
    go("git log --oneline -3", "low")
    go("python --version", "low")
    go("echo hello from the assistant", "low")
    go("where git", "low")
    go('python -c "print(40 + 2)"', "medium", expect_text="42", shell="cmd")           # approved by you in the app; here run directly
    go('python -c "import time; time.sleep(60)"', "medium", expect_exit=None, shell="cmd", timeout_seconds=5)   # must be stopped
    time.sleep(1.5)
    ok = [r for r in rows if r["good"]]
    print(f"\n{len(ok)}/{len(rows)} behaved as expected; the window followed the transcript at {load_state().get('view_pid')}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({"folder": str(folder), "rows": rows}, indent=1), encoding="utf-8")
    pid = load_state().get("view_pid")
    if pid and not args.keep:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    return 0 if len(ok) == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
