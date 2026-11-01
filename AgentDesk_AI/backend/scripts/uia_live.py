#!/usr/bin/env python3
"""U4 live test: drives REAL apps through the UI Automation tools and counts verified actions.

    python scripts/uia_live.py [--only calculator notepad explorer settings chrome word excel] [--out ../docs/eval/u4_live.json]

It opens its OWN windows (a new Calculator / Notepad tab / Explorer window on a temporary folder / Chrome window on a temporary page),
operates them through `ui_inspect` and `ui_act` exactly as the assistant would, checks the result by looking again, and closes what it
opened. It never touches your documents; text typed goes only into the temporary files it creates.
While it runs, do not use the mouse or keyboard (about one minute). Word and Excel are skipped when they are not installed.

Outcomes per action: PASS (ran and verified), FAIL (error or not verified), SKIP (app not installed / not started).
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.tools.base import ToolContext  # noqa: E402
from app.tools.uia import UiActTool, UiInspectTool, get_service  # noqa: E402

WM_CLOSE = 0x0010
PAGE = """<!doctype html><html><head><title>U4 test page</title></head><body>
<h1>U4 test page</h1>
<label for="q">Search box</label><input id="q" type="text" aria-label="Search box">
<button id="add" onclick="document.getElementById('out').textContent = 'Added: ' + document.getElementById('q').value">Add item</button>
<p id="out">Nothing added</p>
<a href="#bottom" onclick="document.title='Signed in'">Sign in</a>
""" + "".join(f"<p>Line {i} of a long page.</p>" for i in range(120)) + "<p id='bottom'>The end</p></body></html>"

tool = UiActTool(ToolContext.from_settings())
inspect = UiInspectTool(ToolContext.from_settings())
rows: list[dict] = []


def record(app: str, label: str, outcome: str, detail: str = "") -> None:
    rows.append({"app": app, "action": label, "outcome": outcome, "detail": detail})
    print(f"  [{outcome}] {app}: {label}" + (f"  ({detail})" if detail and outcome != "PASS" else ""), flush=True)


def act(app: str, label: str, **raw) -> dict | None:
    try:
        args = tool.validate_args(raw)
        result = tool.execute(args)
        if not result.success:
            record(app, label, "FAIL", (result.error or "")[:140])
            return None
        verification = tool.verify(args, result)
        record(app, label, "PASS" if verification.passed else "FAIL", (verification.details or "")[:140])
        return result.output if verification.passed else None
    except Exception as exc:  # noqa: BLE001
        record(app, label, "FAIL", f"{type(exc).__name__}: {str(exc)[:120]}")
        return None


def wait_for(query: str, seconds: float = 15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            return get_service().window(query)
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return None


def close_window(handle: int) -> None:
    ctypes.windll.user32.PostMessageW(handle, WM_CLOSE, 0, 0)


def skip(app: str, why: str, count: int) -> None:
    for i in range(count):
        record(app, f"(not run {i + 1}/{count})", "SKIP", why)


# ------------------------------------------------------------------------------------------------------------ apps
def calculator() -> None:
    before = {w.handle for w in get_service().backend.windows()}
    subprocess.Popen(["calc.exe"])
    win = wait_for("Calculator")
    if win is None:
        return skip("calculator", "Calculator did not open", 9)
    w = "Calculator"
    time.sleep(1.0)
    act("calculator", "click Seven", action="click", control="Seven", window=w)
    act("calculator", "click Plus", action="click", control="Plus", window=w)
    act("calculator", "click Eight", action="click", control="Eight", window=w)
    act("calculator", "click Equals", action="click", control="Equals", window=w)
    out = act("calculator", "read the display", action="read", control="Display", window=w)
    if out is not None:
        ok = "15" in (out.get("text") or "")
        record("calculator", "display shows 15", "PASS" if ok else "FAIL", f"read: {(out.get('text') or '')[:40]}")
    act("calculator", "click Clear (misheard: 'clea')", action="click", control="clea", window=w)
    act("calculator", "press_keys 5", action="press_keys", keys="5", window=w)
    act("calculator", "read the display again", action="read", control="Display", window=w)
    if win.handle not in before:
        close_window(win.handle)


def notepad() -> None:
    folder = Path(tempfile.mkdtemp(prefix="agentdesk_u4_"))
    path = folder / "u4_test.txt"
    path.write_text("", encoding="utf-8")
    subprocess.Popen(["notepad.exe", str(path)])
    win = wait_for("u4_test")
    if win is None:
        shutil.rmtree(folder, ignore_errors=True)
        return skip("notepad", "Notepad did not open the test file", 7)
    w = "u4_test"
    time.sleep(1.0)
    snap = inspect.execute(inspect.validate_args({"window": w})).output
    editor = next((c["name"] for c in snap["controls"] if c["type"] in ("Document", "Edit")), "Text editor")
    act("notepad", "type text", action="type", control=editor, text="hello from the assistant", window=w)
    out = act("notepad", "read it back", action="read", control=editor, window=w)
    if out is not None:
        record("notepad", "text matches", "PASS" if "hello from the assistant" in (out.get("text") or "") else "FAIL")
    act("notepad", "press ctrl+a", action="press_keys", keys="ctrl+a", window=w)
    act("notepad", "type empty (clear the text)", action="type", control=editor, text="", window=w)
    act("notepad", "inspect (read-only) a menu", action="read", control=editor, window=w)
    act("notepad", "press ctrl+w (close our tab)", action="press_keys", keys="ctrl+w", window=w)
    time.sleep(0.8)
    shutil.rmtree(folder, ignore_errors=True)


def explorer() -> None:
    folder = Path(tempfile.mkdtemp(prefix="agentdesk_u4_explorer_"))
    (folder / "alpha.txt").write_text("a", encoding="utf-8")
    (folder / "beta.txt").write_text("b", encoding="utf-8")
    (folder / "sub").mkdir()
    subprocess.Popen(["explorer.exe", str(folder)])
    win = wait_for(folder.name)
    if win is None:
        shutil.rmtree(folder, ignore_errors=True)
        return skip("explorer", "the Explorer window did not open", 6)
    w = folder.name
    time.sleep(1.5)
    act("explorer", "click alpha.txt", action="click", control="alpha.txt", window=w)
    act("explorer", "click beta.txt (misheard: 'beta text')", action="click", control="beta.txt", window=w)
    snap = inspect.execute(inspect.validate_args({"window": w})).output
    box = next((c["name"] for c in snap["controls"] if c["type"] == "Edit" and "search" in c["name"].lower()), None)
    act("explorer", "read alpha.txt", action="read", control="alpha.txt", window=w)
    act("explorer", "press alt+up (one folder up)", action="press_keys", keys="alt+up", window=w)  # the list has the focus after the clicks above
    if box:
        act("explorer", "type in the search box", action="type", control=box, text="alpha", window=w)
    else:
        record("explorer", "type in the search box", "SKIP", "no search box in the list")
    close_window(win.handle)
    time.sleep(0.5)
    shutil.rmtree(folder, ignore_errors=True)


def settings() -> None:
    already = any("settings" == x.title.casefold() for x in get_service().backend.windows())
    os.startfile("ms-settings:")           # opens or focuses the Settings app
    win = wait_for("Settings")
    if win is None:
        return skip("settings", "Settings did not open", 5)
    w = "Settings"
    time.sleep(2.0)
    snap = inspect.execute(inspect.validate_args({"window": w})).output
    names = [c["name"] for c in snap["controls"]]
    target = next((n for n in names if n.lower().startswith("bluetooth")), None)
    box = next((c["name"] for c in snap["controls"] if c["type"] == "Edit"), None)
    if target:
        act("settings", "click Bluetooth & devices", action="click", control=target, window=w)
    else:
        record("settings", "click Bluetooth & devices", "FAIL", f"not in the list: {names[:8]}")
    act("settings", "press alt+left (back to Home)", action="press_keys", keys="alt+left", window=w)
    act("settings", "scroll the page", action="scroll", amount=3, window=w)
    act("settings", "read the first control", action="read", control=names[0] if names else "Settings", window=w)
    if box:
        act("settings", "type in the search box", action="type", control=box, text="display", window=w)
    else:
        record("settings", "type in the search box", "FAIL", "no search box")
    if not already:
        close_window(win.handle)


def chrome() -> None:
    exe = next((p for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")) if os.path.exists(p)), None)
    if exe is None:
        return skip("chrome", "Chrome is not installed", 7)
    folder = Path(tempfile.mkdtemp(prefix="agentdesk_u4_chrome_"))
    page = folder / "u4.html"
    page.write_text(PAGE, encoding="utf-8")
    subprocess.Popen([exe, "--new-window", "--force-renderer-accessibility", page.as_uri()])
    win = wait_for("U4 test page", 20)
    if win is None:
        shutil.rmtree(folder, ignore_errors=True)
        return skip("chrome", "the Chrome window did not open", 7)
    w = "U4 test page"
    time.sleep(2.5)                          # the page's accessibility tree is built after Chrome sees a UI Automation client
    act("chrome", "type in the search box (with text)", action="type", control="Search box", text="milk", window=w)
    act("chrome", "click Add item", action="click", control="Add item", window=w, expect="Added: milk")
    act("chrome", "read the result", action="read", control="Added: milk", window=w)
    act("chrome", "scroll down", action="scroll", amount=5, window=w)
    act("chrome", "scroll up", action="scroll", amount=-5, window=w)
    act("chrome", "click Sign in (jumps to the end of the page)", action="click", control="Sign in", window=w)
    act("chrome", "press ctrl+l (address bar)", action="press_keys", keys="ctrl+l", window="Signed in")
    close_window(win.handle)
    time.sleep(0.5)
    shutil.rmtree(folder, ignore_errors=True)


def office(app: str, exe_name: str) -> None:
    found = shutil.which(exe_name) or any(Path(p).exists() for p in (
        rf"C:\Program Files\Microsoft Office\root\Office16\{exe_name}", rf"C:\Program Files (x86)\Microsoft Office\root\Office16\{exe_name}"))
    skip(app, "not installed on this PC" if not found else "installed but the live script does not drive it: run the actions by hand", 7)


SCENARIOS = {"calculator": calculator, "notepad": notepad, "explorer": explorer, "settings": settings, "chrome": chrome,
             "word": lambda: office("word", "WINWORD.EXE"), "excel": lambda: office("excel", "EXCEL.EXE")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=list(SCENARIOS))
    ap.add_argument("--out")
    args = ap.parse_args()
    for name in args.only:
        print(f"== {name}", flush=True)
        try:
            SCENARIOS[name]()
        except Exception as exc:  # noqa: BLE001
            record(name, "scenario", "FAIL", f"{type(exc).__name__}: {str(exc)[:120]}")
    ran = [r for r in rows if r["outcome"] != "SKIP"]
    passed = [r for r in ran if r["outcome"] == "PASS"]
    by_app = {a: f"{sum(r['outcome'] == 'PASS' for r in ran if r['app'] == a)}/{sum(r['app'] == a for r in ran)}" for a in sorted({r['app'] for r in ran})}
    summary = {"run": len(ran), "passed": len(passed), "skipped": len(rows) - len(ran),
               "pass_pct": round(100 * len(passed) / len(ran), 1) if ran else 0.0, "by_app": by_app}
    print(json.dumps(summary, indent=1))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({"summary": summary, "rows": rows}, indent=1), encoding="utf-8")
    return 0 if ran and summary["pass_pct"] >= 90 else 1


if __name__ == "__main__":
    raise SystemExit(main())
