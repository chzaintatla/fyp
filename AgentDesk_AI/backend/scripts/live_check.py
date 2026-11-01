#!/usr/bin/env python3
"""AgentDesk live check: run every registered tool once and report PASS/FAIL with evidence.

Each tool is called directly (validate_args -> execute -> verify) against a
throwaway sandbox folder that is the ONLY allowed and discovery directory.
After the tool runs, an independent check reads the real system state (file
exists, content matches, zip members, ...) and that is the evidence printed.

This is a developer diagnostic. It bypasses the approval UI on purpose, so
every probe is chosen to be harmless:

  sandbox  file tools; all paths are inside the sandbox (default: on)
  read     read-only system/desktop/browser/package queries (default: on)
  gui      visible launches: Notepad, Explorer on the sandbox, example.com,
           a web search, a YouTube search, a mailto draft, Settings home,
           and a screenshot saved in the sandbox (opt in: --gui)
  network  download https://example.com, web answer + summaries (opt in: --network; needs an AI service)
  memory   remember / recall / forget against the real database, self-cleaning (opt in: --memory)

Never run: setters for volume/brightness/Bluetooth/Wi-Fi/theme, desktop
clicks/typing/closing, browser tab closing, WinGet downloads.

Statuses: PASS, FAIL, BLOCKED (ran, but an external prerequisite is missing:
not Windows, no browser extension, no winget), SKIP (category not enabled).
Exit code 1 when any tool FAILs or a registered tool has no probe.

Usage (from backend/):
    python scripts/live_check.py                    # sandbox + read-only
    python scripts/live_check.py --gui --network    # everything
    python scripts/live_check.py --only write_text_file --json
    python scripts/live_check.py --api --only control_browser   # browser probe through the RUNNING app (extension paired there)
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.tools.base import ToolContext, ToolResult, Verification  # noqa: E402
from app.tools.registry import ToolRegistry  # noqa: E402

PASS, FAIL, BLOCKED, SKIP = "PASS", "FAIL", "BLOCKED", "SKIP"
IS_WINDOWS = platform.system() == "Windows"
PROBE_TIMEOUT_SECONDS = 45
CATEGORIES = ("sandbox", "read", "gui", "network", "media", "messaging", "install", "memory")


@dataclass
class Outcome:
    status: str
    evidence: str


@dataclass
class Env:
    sandbox: Path
    registry: ToolRegistry
    state: dict[str, Any] = field(default_factory=dict)

    def run(self, name: str, args: dict[str, Any]) -> tuple[ToolResult, Verification]:
        tool = self.registry.get(name)
        parsed = tool.validate_args(args)
        result = tool.execute(parsed)
        return result, tool.verify(parsed, result)

    @property
    def work(self) -> Path:
        return self.sandbox / "work"


@dataclass
class Probe:
    tool: str
    category: str
    run: Callable[[Env], Outcome]
    windows_only: bool = False


def _short(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "..."


def judge(result: ToolResult, ver: Verification, ok: bool, evidence: str) -> Outcome:
    """PASS only when the tool, its verifier AND the independent check agree."""
    if not result.success:
        return Outcome(FAIL, f"tool error: {_short(result.error)}")
    if not ver.passed:
        return Outcome(FAIL, f"verify() failed: {_short(ver.details or ver.checks)}")
    if not ver.evidence:
        return Outcome(FAIL, "verify() passed without evidence")
    return Outcome(PASS if ok else FAIL, evidence + ("" if ok else " (independent check FAILED)"))


def external(result: ToolResult, markers: tuple[str, ...]) -> Outcome | None:
    """Map a failure caused by a missing external prerequisite to BLOCKED."""
    if result.success:
        return None
    error = str(result.error or "")
    if any(m.casefold() in error.casefold() for m in markers):
        return Outcome(BLOCKED, _short(error))
    return None


# ---------------------------------------------------------------- sandbox probes
NOTE_TEXT = "AgentDesk live check: hello"


def p_create_folder(env: Env) -> Outcome:
    r, v = env.run("create_folder", {"path": str(env.work)})
    return judge(r, v, env.work.is_dir(), f"is_dir({env.work.name})=True")


def p_write_text_file(env: Env) -> Outcome:
    note = env.work / "note.txt"
    r, v = env.run("write_text_file", {"path": str(note), "content": NOTE_TEXT})
    actual = note.read_text(encoding="utf-8") if note.is_file() else None
    return judge(r, v, actual == NOTE_TEXT, f"disk content == written ({len(NOTE_TEXT)} chars)")


def p_read_text_file(env: Env) -> Outcome:
    note = env.work / "note.txt"
    r, v = env.run("read_text_file", {"path": str(note)})
    got = (r.output or {}).get("content")
    return judge(r, v, got == note.read_text(encoding="utf-8"), "returned content == disk content")


def p_edit_text_file(env: Env) -> Outcome:
    note = env.work / "note.txt"
    r, v = env.run("edit_text_file", {"path": str(note), "old_text": "hello", "new_text": "edited"})
    actual = note.read_text(encoding="utf-8")
    return judge(r, v, actual == NOTE_TEXT.replace("hello", "edited"), f"disk content now {actual!r}")


def p_list_folder(env: Env) -> Outcome:
    r, v = env.run("list_folder", {"path": str(env.work)})
    names = json.dumps((r.output or {}).get("entries", []), default=str)
    return judge(r, v, "note.txt" in names, "note.txt listed")


def p_find_files(env: Env) -> Outcome:
    r, v = env.run("find_files", {"name": "note", "folder": str(env.work)})
    return judge(r, v, "note.txt" in json.dumps(r.output, default=str), "note.txt found")


def p_find_paths(env: Env) -> Outcome:
    r, v = env.run("find_paths", {"name": "work", "kind": "folder"})
    found = [str(c) for c in (r.output or {}).get("candidates", [])]
    return judge(r, v, any("work" in c for c in found), f"candidates={_short(found, 100)}")


def p_copy_file(env: Env) -> Outcome:
    src, dst = env.work / "note.txt", env.work / "copy.txt"
    r, v = env.run("copy_file", {"source": str(src), "destination": str(dst)})
    ok = src.is_file() and dst.is_file() and src.read_bytes() == dst.read_bytes()
    return judge(r, v, ok, "source kept, copy has identical bytes")


def p_rename_file(env: Env) -> Outcome:
    src, dst = env.work / "copy.txt", env.work / "renamed.txt"
    r, v = env.run("rename_file", {"path": str(src), "new_name": dst.name})
    return judge(r, v, dst.is_file() and not src.exists(), "copy.txt -> renamed.txt on disk")


def p_move_file(env: Env) -> Outcome:
    src = env.work / "renamed.txt"
    (env.sandbox / "moved").mkdir(exist_ok=True)
    dst = env.sandbox / "moved" / "renamed.txt"
    r, v = env.run("move_file", {"source": str(src), "destination": str(dst)})
    return judge(r, v, dst.is_file() and not src.exists(), "file now only in moved/")


def p_compress_folder(env: Env) -> Outcome:
    zip_path = env.sandbox / "work.zip"
    r, v = env.run("compress_folder", {"folder_path": str(env.work), "zip_path": str(zip_path)})
    members = zipfile.ZipFile(zip_path).namelist() if zip_path.is_file() else []
    return judge(r, v, any(m.endswith("note.txt") for m in members), f"zip members={members}")


def p_extract_file(env: Env) -> Outcome:
    dest = env.sandbox / "extracted"
    r, v = env.run("extract_file", {"zip_path": str(env.sandbox / "work.zip"), "destination": str(dest)})
    hits = list(dest.rglob("note.txt")) if dest.exists() else []
    ok = bool(hits) and hits[0].read_bytes() == (env.work / "note.txt").read_bytes()
    return judge(r, v, ok, "extracted note.txt bytes == original")


def p_delete_file(env: Env) -> Outcome:
    target = env.sandbox / "moved" / "renamed.txt"
    r, v = env.run("delete_file", {"path": str(target)})
    in_trash = list((env.sandbox / ".agentdesk_trash").glob("*.json"))
    if in_trash:
        env.state["trash_id"] = in_trash[0].stem
    return judge(r, v, not target.exists() and bool(in_trash), "gone from disk, trash record present")


def p_list_trash(env: Env) -> Outcome:
    r, v = env.run("list_trash", {})
    ids = [i.get("trash_id") for i in (r.output or {}).get("items", [])]
    return judge(r, v, env.state.get("trash_id") in ids, f"trash ids={ids}")


def p_restore_file(env: Env) -> Outcome:
    trash_id = env.state.get("trash_id")
    if not trash_id:
        return Outcome(FAIL, "no trash_id (delete_file probe failed)")
    target = env.sandbox / "moved" / "renamed.txt"
    r, v = env.run("restore_file", {"trash_id": trash_id})
    ok = target.is_file() and target.read_text(encoding="utf-8") == (env.work / "note.txt").read_text(encoding="utf-8")
    return judge(r, v, ok, "restored to original path with original content")


# ---------------------------------------------------------------- read-only probes
def p_system_get(env: Env) -> Outcome:
    r, v = env.run("manage_system_setting", {"setting": "volume", "action": "get"})
    if not IS_WINDOWS and not r.success:
        return Outcome(BLOCKED, f"not Windows: {_short(r.error, 100)}")
    value = (r.output or {}).get("value")
    return judge(r, v, isinstance(value, int) and 0 <= value <= 100, f"volume read={value}")


def p_desktop_inspect(env: Env) -> Outcome:
    r, v = env.run("control_desktop", {"steps": [{"action": "inspect"}]})
    if not IS_WINDOWS and not r.success:
        return Outcome(BLOCKED, f"not Windows: {_short(r.error, 100)}")
    return judge(r, v, bool(r.output), f"inspect output={_short(r.output, 100)}")


def p_ui_inspect(env: Env) -> Outcome:
    r, v = env.run("ui_inspect", {})
    if not IS_WINDOWS and not r.success:
        return Outcome(BLOCKED, f"not Windows: {_short(r.error, 100)}")
    return judge(r, v, bool((r.output or {}).get("window")), f"window={_short((r.output or {}).get('window'), 60)} controls={(r.output or {}).get('count')}")


def p_ui_act_read(env: Env) -> Outcome:
    """ui_act in its read-only form: reads the first named control of the front window (never clicks, types or scrolls)."""
    listed, _ = env.run("ui_inspect", {})
    if not IS_WINDOWS and not listed.success:
        return Outcome(BLOCKED, f"not Windows: {_short(listed.error, 100)}")
    named = [c for c in (listed.output or {}).get("controls", []) if c.get("name") and not c.get("password")]
    if not named:
        return Outcome(BLOCKED, "the front window lists no named control")
    r, v = env.run("ui_act", {"action": "read", "control": named[0]["name"], "id": named[0]["id"]})
    return judge(r, v, bool((r.output or {}).get("text")), f"read {_short(named[0]['name'], 40)}")


def p_terminal_read(env: Env) -> Outcome:
    """run_terminal_command in its read-only form (`echo`): nothing is executed outside this program, nothing is changed."""
    r, v = env.run("run_terminal_command", {"command": "echo live check"})
    return judge(r, v, (r.output or {}).get("lines") == ["live check"], f"output={_short((r.output or {}).get('lines'), 60)}")


def p_open_terminal(env: Env) -> Outcome:
    r, v = env.run("open_terminal", {"shell": "cmd"})
    if not IS_WINDOWS and not r.success:
        return Outcome(BLOCKED, f"not Windows: {_short(r.error, 100)}")
    return judge(r, v, bool((r.output or {}).get("pid")), f"folder={_short((r.output or {}).get('folder'), 60)}")


def _refusal_probe(env: Env, tool: str, args: dict, needle: str) -> Outcome:
    """A hidden send tool must REFUSE a call that has no server-issued confirmation: the probe passes only if it refused and nothing was sent."""
    r, _ = env.run(tool, args)
    refused = (not r.success) and needle in (r.error or "")
    return Outcome(PASS if refused else FAIL, f"refused without a confirmation: {refused} ({_short(r.error, 80)})")


def p_send_email_refuses(env: Env) -> Outcome:
    return _refusal_probe(env, "send_email", {"draft_id": "livecheck0000", "confirmation": "no-such-confirmation"}, "Nothing was sent")


def p_send_message_refuses(env: Env) -> Outcome:
    return _refusal_probe(env, "send_message", {"app": "whatsapp", "confirmation": "no-such-confirmation"}, "Nothing was sent")


def _api_browser_inspect(base_url: str, client: Any = None) -> Outcome:
    """control_browser through the running server: the extension's socket lives in that process, not in this script.

    Asks `/browser/status` first (is exactly one browser profile paired?), then runs the read-only command "list tabs"
    as a normal task (it appears in the task history) and judges the verified result. Never sends input to a page.
    """
    import httpx

    http = client or httpx.Client(base_url=base_url, timeout=90)
    try:
        status = http.get("/browser/status").json()
    except (httpx.HTTPError, ValueError) as exc:
        return Outcome(BLOCKED, f"the app is not reachable at {base_url} ({type(exc).__name__}): start it with START_AGENT_X.cmd")
    if not status.get("connected"):
        profiles = status.get("profiles", 0)
        why = ("no browser extension is paired: open Chrome/Edge setup from the app" if not profiles
               else f"{profiles} browser profiles are connected: close the extra ones, exactly one is supported")
        return Outcome(BLOCKED, why)
    from app.security.token import load_or_create_token

    try:
        body = http.post("/tasks", json={"command": "list tabs"}, headers={"Authorization": "Bearer " + load_or_create_token()}).json()
    except (httpx.HTTPError, ValueError) as exc:
        return Outcome(FAIL, f"the app accepted the connection but the task failed to start ({type(exc).__name__})")
    actions = body.get("actions") or []
    action = actions[0] if actions else {}
    if body.get("status") != "completed" or action.get("tool_name") != "control_browser":
        return Outcome(FAIL, f"task ended {body.get('status')}: {_short(body.get('error') or body.get('plan_note') or action.get('error'))}")
    verification = action.get("verification") or {}
    tabs = (verification.get("evidence") or {}).get("tabs")
    ok = action.get("status") == "succeeded" and verification.get("passed") is True and isinstance(tabs, int)
    return Outcome(PASS if ok else FAIL, f"{tabs} tabs, verified by the extension reply" if ok else f"verification did not pass: {_short(verification)}")


def p_browser_inspect(env: Env) -> Outcome:
    if env.state.get("api"):
        return _api_browser_inspect(env.state["api"], env.state.get("api_client"))
    r, v = env.run("control_browser", {"action": "inspect"})
    blocked = external(r, ("extension", "browser"))
    if blocked:
        return Outcome(BLOCKED, f"no extension connected to this process: {blocked.evidence}")
    tabs = (r.output or {}).get("tabs")
    return judge(r, v, isinstance(tabs, list), f"{len(tabs or [])} tabs")


def p_package_search(env: Env) -> Outcome:
    r, v = env.run("application_package", {"action": "search", "query": "Notepad++"})
    blocked = external(r, ("Windows Package Manager", "WinGet was not found"))
    if blocked:
        return blocked
    return judge(r, v, "notepad" in json.dumps(r.output).casefold(), f"output={_short(r.output, 100)}")


# ---------------------------------------------------------------- gui probes
def _launched(result: ToolResult) -> bool:
    out = result.output or {}
    return bool(out.get("launched") or out.get("launch_requested") or out.get("draft_opened") or out.get("pid"))


def _gui(env: Env, name: str, args: dict, what: str) -> Outcome:
    r, v = env.run(name, args)
    return judge(r, v, _launched(r), f"{what}; launch reported (window not inspected)")


def p_open_application(env: Env) -> Outcome:
    r, v = env.run("open_application", {"app_name": "notepad"})
    pid = (r.output or {}).get("pid")
    alive = False
    try:
        import psutil

        # Windows 11 Notepad is a Store app: the launcher pid exits and a new notepad.exe takes over.
        started = [p for p in psutil.process_iter(["name", "create_time"])
                   if (p.info["name"] or "").lower() == "notepad.exe" and p.info["create_time"] >= time.time() - 60]
        alive = bool(started) or bool(pid and psutil.pid_exists(pid))
        for p in started:
            p.terminate()  # close only notepad processes started during this check
    except Exception:  # noqa: BLE001 - evidence only
        pass
    return judge(r, v, alive, f"notepad launcher pid={pid}; notepad.exe running={alive}, then terminated")


def p_open_folder(env: Env) -> Outcome:
    return _gui(env, "open_folder", {"path": str(env.work)}, "Explorer on sandbox/work")


def p_open_file(env: Env) -> Outcome:
    return _gui(env, "open_file", {"path": str(env.work / "note.txt")}, "default editor on note.txt")


def p_open_url(env: Env) -> Outcome:
    return _gui(env, "open_url", {"url": "https://example.com"}, "browser on example.com")


def p_web_search(env: Env) -> Outcome:
    return _gui(env, "web_search", {"query": "AgentDesk live check"}, "browser search page")


def p_play_youtube(env: Env) -> Outcome:
    r, v = env.run("play_youtube_video", {"query": "lofi hip hop radio"})
    return judge(r, v, bool(r.success and r.output), f"output={_short(r.output, 100)}")


def p_compose_email(env: Env) -> Outcome:
    return _gui(env, "compose_email", {"to": "test@example.com", "subject": "AgentDesk live check",
                                       "body": "Draft only. Do not send."}, "mailto draft (not sent)")


def p_open_settings(env: Env) -> Outcome:
    return _gui(env, "open_windows_settings", {"page": "root"}, "Settings home")


def p_take_screenshot(env: Env) -> Outcome:
    out = env.sandbox / "shot.png"
    r, v = env.run("take_screenshot", {"output_path": str(out)})
    ok = out.is_file() and out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    return judge(r, v, ok, f"PNG in sandbox, {out.stat().st_size if out.exists() else 0} bytes")


# ---------------------------------------------------------------- network probes
def p_download(env: Env) -> Outcome:
    out = env.sandbox / "example.html"
    r, v = env.run("download_web_content", {"url": "https://example.com/", "output_path": str(out),
                                            "save_as_webpage": True})
    blocked = external(r, ("Could not resolve website host",))
    if blocked:
        return Outcome(BLOCKED, f"no direct DNS/network here: {blocked.evidence}")
    saved = Path((r.output or {}).get("path") or out)
    text = saved.read_text(encoding="utf-8", errors="replace") if saved.is_file() else ""
    return judge(r, v, "Example Domain" in text, f"saved {len(text)} chars containing 'Example Domain'")


# ---------------------------------------------------------------- opt-in probes (Phase 18)
def p_media(env: Env) -> Outcome:
    """Toggle play/pause twice, so playback ends as it started. Needs a player that reports to Windows."""
    first, v1 = env.run("media_control", {"action": "play_pause"})
    if not first.success:
        return Outcome(BLOCKED if not IS_WINDOWS else FAIL, _short(first.error))
    second, v2 = env.run("media_control", {"action": "play_pause"})
    ok = v1.passed and v2.passed
    return judge(first, v1, ok, f"toggled and restored: {_short(v1.evidence.get('after'), 90)} -> "
                                f"{_short(v2.evidence.get('after'), 90)}")


def p_whatsapp(env: Env) -> Outcome:
    phone = env.state.get("whatsapp_phone")
    if not phone:
        return Outcome(SKIP, "pass --whatsapp-phone +<your own number> to pre-fill a test message (never sent)")
    r, v = env.run("whatsapp_open_chat", {"phone": phone, "text": "AgentDesk live check - not sent"})
    if not r.success and not IS_WINDOWS:
        return Outcome(BLOCKED, f"not Windows: {_short(r.error, 90)}")
    return judge(r, v, bool(r.output.get("sent") is False), f"chat opened for {r.output.get('to')}, sent=False")


def p_install(env: Env) -> Outcome:
    package = env.state.get("install_id")
    if not package:
        return Outcome(SKIP, "pass --install-id <exact WinGet ID> to install one package (high risk, your choice)")
    r, v = env.run("winget_install", {"package_id": package})
    if not r.success and not IS_WINDOWS:
        return Outcome(BLOCKED, f"not Windows: {_short(r.error, 90)}")
    return judge(r, v, bool(v.evidence.get("winget_list_tail")), f"winget list shows {package}")


# ---------------------------------------------------------------- memory + knowledge probes
def _db():
    from app.database import SessionLocal, init_db
    init_db()
    return SessionLocal()


def p_memory_remember(env: Env) -> Outcome:
    from app.models import MemoryItem
    text = "AgentDesk live check note (safe to delete)"
    r, v = env.run("memory_remember", {"text": text})
    with _db() as db:
        row = db.query(MemoryItem).filter(MemoryItem.text == text).first()
        stored = row is not None
        if row is not None:
            db.delete(row)  # leave the user's real memory exactly as it was
            db.commit()
    return judge(r, v, stored, "note found in the database, then removed")


def p_memory_recall(env: Env) -> Outcome:
    from app.models import MemoryItem
    with _db() as db:
        before = db.query(MemoryItem).count()
    r, v = env.run("memory_recall", {})
    return judge(r, v, (r.output or {}).get("count") == before, f"reported {(r.output or {}).get('count')} of {before} stored notes")


def p_memory_forget(env: Env) -> Outcome:
    from app.models import MemoryItem
    text = "AgentDesk live check forget probe zebra"
    with _db() as db:
        db.add(MemoryItem(kind="fact", text=text))
        db.commit()
    r, v = env.run("memory_forget", {"query": "forget probe zebra"})
    with _db() as db:
        gone = db.query(MemoryItem).filter(MemoryItem.text == text).first() is None
        db.query(MemoryItem).filter(MemoryItem.text == text).delete()
        db.commit()
    return judge(r, v, gone, "note deleted from the database")


def p_summarise_file(env: Env) -> Outcome:
    note = env.work / "note.txt"
    if not note.is_file():
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(NOTE_TEXT * 3, encoding="utf-8")
    r, v = env.run("summarise_file", {"path": str(note)})
    blocked = external(r, ("No AI service", "could not summarise", "busy"))
    if blocked:
        return Outcome(BLOCKED, f"needs an AI service: {blocked.evidence}")
    return judge(r, v, bool((r.output or {}).get("answer")), f"summary: {_short((r.output or {}).get('answer'), 80)}")


def p_web_answer(env: Env) -> Outcome:
    r, v = env.run("web_answer", {"query": "What is the capital of France?"})
    blocked = external(r, ("Search is busy", "SEARCH_SETUP", "couldn't find any web results", "No AI service", "could not summarise", "Settings", "busy"))
    if blocked:
        return Outcome(BLOCKED, f"needs internet and an AI service: {blocked.evidence}")
    answer = str((r.output or {}).get("answer", ""))
    return judge(r, v, "paris" in answer.casefold(), f"answer: {_short(answer, 90)}")


def p_summarise_page(env: Env) -> Outcome:
    r, v = env.run("summarise_page", {"url": "https://example.com/"})
    blocked = external(r, ("couldn't read that page", "No AI service", "could not summarise", "busy"))
    if blocked:
        return Outcome(BLOCKED, f"needs internet and an AI service: {blocked.evidence}")
    return judge(r, v, bool((r.output or {}).get("answer")), f"summary: {_short((r.output or {}).get('answer'), 80)}")


PROBES: list[Probe] = [
    Probe("create_folder", "sandbox", p_create_folder),
    Probe("write_text_file", "sandbox", p_write_text_file),
    Probe("read_text_file", "sandbox", p_read_text_file),
    Probe("edit_text_file", "sandbox", p_edit_text_file),
    Probe("list_folder", "sandbox", p_list_folder),
    Probe("find_files", "sandbox", p_find_files),
    Probe("find_paths", "sandbox", p_find_paths),
    Probe("copy_file", "sandbox", p_copy_file),
    Probe("rename_file", "sandbox", p_rename_file),
    Probe("move_file", "sandbox", p_move_file),
    Probe("compress_folder", "sandbox", p_compress_folder),
    Probe("extract_file", "sandbox", p_extract_file),
    Probe("delete_file", "sandbox", p_delete_file),
    Probe("list_trash", "sandbox", p_list_trash),
    Probe("restore_file", "sandbox", p_restore_file),
    Probe("manage_system_setting", "read", p_system_get),
    Probe("control_desktop", "read", p_desktop_inspect),
    Probe("control_browser", "read", p_browser_inspect),
    Probe("ui_inspect", "read", p_ui_inspect),
    Probe("ui_act", "gui", p_ui_act_read),
    Probe("run_terminal_command", "read", p_terminal_read),
    Probe("open_terminal", "gui", p_open_terminal),
    Probe("send_email", "read", p_send_email_refuses),
    Probe("send_message", "read", p_send_message_refuses),
    Probe("application_package", "read", p_package_search),
    Probe("open_application", "gui", p_open_application),
    Probe("open_folder", "gui", p_open_folder),
    Probe("open_file", "gui", p_open_file),
    Probe("open_url", "gui", p_open_url),
    Probe("web_search", "gui", p_web_search),
    Probe("play_youtube_video", "gui", p_play_youtube),
    Probe("compose_email", "gui", p_compose_email),
    Probe("open_windows_settings", "gui", p_open_settings, windows_only=True),
    Probe("take_screenshot", "gui", p_take_screenshot),
    Probe("download_web_content", "network", p_download),
    Probe("memory_remember", "memory", p_memory_remember),
    Probe("memory_recall", "memory", p_memory_recall),
    Probe("memory_forget", "memory", p_memory_forget),
    Probe("summarise_file", "network", p_summarise_file),
    Probe("web_answer", "network", p_web_answer),
    Probe("summarise_page", "network", p_summarise_page),
    Probe("media_control", "media", p_media),
    Probe("whatsapp_open_chat", "messaging", p_whatsapp),
    Probe("winget_install", "install", p_install),
]


def _with_timeout(fn: Callable[[], Outcome], seconds: float) -> Outcome:
    box: list[Outcome] = []

    def target():
        try:
            box.append(fn())
        except Exception as exc:  # noqa: BLE001 - reported as FAIL
            box.append(Outcome(FAIL, f"exception {type(exc).__name__}: {_short(str(exc))}"))

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(seconds)
    return box[0] if box else Outcome(FAIL, f"timed out after {seconds:g} s (thread left running)")


def run_checks(enabled: set[str], only: set[str] | None = None, sandbox: Path | None = None,
               timeout: float = PROBE_TIMEOUT_SECONDS, options: dict | None = None) -> list[dict[str, str]]:
    own_sandbox = sandbox is None
    sandbox = (sandbox or Path(tempfile.mkdtemp(prefix="agentdesk_live_"))).resolve()
    ctx = ToolContext(allowed_directories=[sandbox], approved_applications=["notepad"],
                      test_dir=sandbox / "testdir", discovery_directories=[sandbox],
                      isolate_system_controls=True)
    registry = ToolRegistry(ctx)
    env = Env(sandbox=sandbox, registry=registry, state=dict(options or {}))
    by_tool = {p.tool: p for p in PROBES}
    rows: list[dict[str, str]] = []
    try:
        for name in registry.names():
            if name not in by_tool:
                rows.append({"tool": name, "category": "-", "status": FAIL, "evidence": "no live probe defined"})
        for probe in PROBES:
            if probe.tool not in registry.names():
                continue
            if only and probe.tool not in only:
                continue
            if probe.category not in enabled:
                outcome = Outcome(SKIP, f"category '{probe.category}' not enabled (use --{probe.category})")
            elif probe.windows_only and not IS_WINDOWS:
                outcome = Outcome(BLOCKED, "Windows-only tool; not run on " + platform.system())
            else:
                outcome = _with_timeout(lambda p=probe: p.run(env), timeout)
                if outcome.status == FAIL and probe.category == "gui" and not IS_WINDOWS:
                    # The product targets the Windows desktop; a headless/other OS cannot prove a GUI tool.
                    outcome = Outcome(BLOCKED, f"no Windows desktop ({platform.system()}): {outcome.evidence}")
            rows.append({"tool": probe.tool, "category": probe.category,
                         "status": outcome.status, "evidence": outcome.evidence})
    finally:
        if own_sandbox:
            shutil.rmtree(sandbox, ignore_errors=True)
    order = {name: i for i, name in enumerate([p.tool for p in PROBES])}
    rows.sort(key=lambda row: order.get(row["tool"], -1))
    return rows


def record(rows: list[dict[str, str]], enabled: set[str]) -> Path:
    """Save this run (no phone numbers or file contents; evidence strings only) to docs/live_runs/."""
    from datetime import datetime, timezone

    folder = BACKEND_DIR.parent / "docs" / "live_runs"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    target = folder / f"{stamp}-live_check-{platform.system().lower()}.json"
    target.write_text(json.dumps({"recorded_at": stamp, "platform": platform.platform(), "python": platform.python_version(),
                                  "categories": sorted(enabled), "rows": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"recorded: {target}")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--gui", action="store_true", help="also run visible launches")
    parser.add_argument("--network", action="store_true", help="also download example.com, answer a web question and summarise a page (needs an AI service)")
    parser.add_argument("--memory", action="store_true", help="also run the memory tools against the real database (each probe cleans up after itself)")
    parser.add_argument("--no-read", action="store_true", help="skip read-only system/desktop/browser/package probes")
    parser.add_argument("--only", nargs="*", help="run only these tool names")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    parser.add_argument("--keep", action="store_true", help="keep the sandbox folder for inspection")
    parser.add_argument("--media", action="store_true", help="toggle play/pause twice on the current player")
    parser.add_argument("--whatsapp-phone", help="your own number (+country code): pre-fills a test message, never sent")
    parser.add_argument("--install-id", help="exact WinGet ID to really install (high risk; e.g. a small tool you want)")
    parser.add_argument("--record", action="store_true", help="save the run with evidence to docs/live_runs/")
    parser.add_argument("--api", nargs="?", const="http://127.0.0.1:8000", metavar="URL",
                        help="probe control_browser through the running app (default http://127.0.0.1:8000)")
    args = parser.parse_args(argv)

    enabled = {"sandbox"} | ({"read"} if not args.no_read else set())
    enabled |= {"gui"} if args.gui else set()
    enabled |= {"network"} if args.network else set()
    enabled |= {"memory"} if args.memory else set()
    enabled |= {"media"} if args.media else set()
    enabled |= {"messaging"} if args.whatsapp_phone else set()
    enabled |= {"install"} if args.install_id else set()
    sandbox = Path(tempfile.mkdtemp(prefix="agentdesk_live_")) if args.keep else None
    options = {"whatsapp_phone": args.whatsapp_phone, "install_id": args.install_id, "api": args.api}
    rows = run_checks(enabled, set(args.only) if args.only else None, sandbox, options=options)
    if args.record:
        record(rows, enabled)

    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        width = max(len(r["tool"]) for r in rows)
        for r in rows:
            print(f"[{r['status']:<7}] {r['tool']:<{width}}  {r['category']:<7}  {r['evidence']}")
        counts = {s: sum(r["status"] == s for r in rows) for s in (PASS, FAIL, BLOCKED, SKIP)}
        print(f"\n{len(rows)} tools: " + ", ".join(f"{n} {s}" for s, n in counts.items())
              + f"  (platform: {platform.system()})")
        if sandbox:
            print(f"sandbox kept at {sandbox}")
    return 1 if any(r["status"] == FAIL for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
