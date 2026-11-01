"""Terminal tools: open a terminal in a folder (`open_terminal`) and run a command with its output on screen (`run_terminal_command`).

How a command runs (see terminal_policy.py for which ones may): the program itself starts the command as a child process with no
input, a time limit and a fixed argument list or one shell string, writes everything to a transcript file and keeps the last 50 lines.
A visible PowerShell window follows that transcript live, so the user watches "PS C:\\folder> git status" and its output appear.
The window is a live view, not a shell the assistant types into: that is what makes the exit code, the output and a timeout kill
reliable, and nothing a page or a file says can type into it.

State that survives between commands (the current folder) is kept in `state.json` next to the transcript, so "go to the backend folder"
followed by "run git status" works even though each tool call may run in a different worker process.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ..logging_setup import redact
from ..security.paths import PathNotAllowedError, resolve_within_allowed
from ..verification import failed_result, verdict, window_check
from .base import BaseTool, RiskLevel, ToolContext, ToolResult, Verification
from .terminal_policy import MAX_READ_BYTES, Decision, classify, short_form

TAIL_LINES = 50
MAX_LINE_CHARS = 240
MAX_TRANSCRIPT_BYTES = 1_000_000
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600
TERMINAL_PROCESSES = {"cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "conhost.exe", "openconsole.exe", "wt.exe"}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
_lock = threading.Lock()

_SECRETS = [
    (re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd|pwd|authorization)(\s*[=:]\s*)(?:bearer\s+)?\S+"), r"\1\2[REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"), "Bearer [REDACTED]"),
    (re.compile(r"\bgsk_[A-Za-z0-9]{20,}|\bghp_[A-Za-z0-9]{20,}|\bAKIA[0-9A-Z]{16}\b"), "[REDACTED]"),
]


def terminal_dir() -> Path:
    base = os.environ.get("AGENTDESK_TERMINAL_DIR")
    if base:
        return Path(base)
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "AgentDesk" / "terminal"


def scrub(text: str) -> str:
    """No secret leaves the program through terminal output: keys, tokens and password=... values are masked, then long tokens."""
    for rx, repl in _SECRETS:
        text = rx.sub(repl, text)
    return redact(text, MAX_LINE_CHARS)


# ----------------------------------------------------------------------------------------------------------------- state
def _state_path() -> Path:
    return terminal_dir() / "state.json"


def load_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    terminal_dir().mkdir(parents=True, exist_ok=True)
    tmp = _state_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(_state_path())


def session_cwd(ctx: ToolContext) -> Path:
    """The folder commands run in: the one set by `cd` / `open_terminal`, else the user's home (or the first allowed folder)."""
    saved = load_state().get("cwd")
    if saved and Path(saved).is_dir():
        return Path(saved)
    home = Path.home()
    for base in ctx.allowed_directories:
        try:
            home.resolve().relative_to(base.expanduser().resolve())
            return home
        except ValueError:
            continue
    return ctx.allowed_directories[0].expanduser().resolve() if ctx.allowed_directories else home


# -------------------------------------------------------------------------------------------------------------- transcript
class Transcript:
    def __init__(self) -> None:
        self.path = terminal_dir() / "session.log"
        terminal_dir().mkdir(parents=True, exist_ok=True)
        if not self.path.exists() or self.path.stat().st_size > MAX_TRANSCRIPT_BYTES:
            self.path.write_text("AgentDesk terminal: every command the assistant runs appears here.\n", encoding="utf-8-sig")

    def write(self, line: str) -> None:
        with _lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(line.rstrip("\r\n") + "\n")


def ensure_view(transcript: Transcript) -> int | None:
    """Open (once) the visible window that follows the transcript. Returns its PID, None when it cannot or must not be shown."""
    if os.environ.get("AGENTDESK_TERMINAL_VIEW", "1") == "0" or os.name != "nt":
        return None
    state = load_state()
    pid = state.get("view_pid")
    if pid:
        try:
            import psutil
            if psutil.pid_exists(int(pid)) and "powershell" in psutil.Process(int(pid)).name().casefold():
                return int(pid)
        except Exception:  # noqa: BLE001
            pass
    path = str(transcript.path).replace("'", "''")
    script = f"$Host.UI.RawUI.WindowTitle = 'AgentDesk terminal'; Get-Content -Wait -Tail 200 -LiteralPath '{path}'"
    try:
        proc = subprocess.Popen(["powershell.exe", "-NoProfile", "-NoExit", "-Command", script], creationflags=_NEW_CONSOLE)
    except OSError:
        return None
    state["view_pid"] = proc.pid
    save_state(state)
    return proc.pid


# ---------------------------------------------------------------------------------------------------------------- running
class RunResult(BaseModel):
    lines: list[str] = []
    line_count: int = 0
    exit_code: int | None = None
    timed_out: bool = False
    seconds: float = 0.0
    error: str | None = None


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=10, creationflags=_NO_WINDOW)
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.kill()
    except OSError:
        pass


def _stream(argv: list[str] | str, cwd: Path, timeout: int, transcript: Transcript, env: dict[str, str] | None = None) -> RunResult:
    started = time.monotonic()
    tail: deque[str] = deque(maxlen=TAIL_LINES)
    count = 0
    try:
        proc = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                creationflags=_NO_WINDOW, env=env, shell=False)
    except (OSError, ValueError) as exc:
        return RunResult(error=f"Could not start it: {type(exc).__name__}: {str(exc)[:120]}", exit_code=None)
    timed_out = threading.Event()

    def watchdog() -> None:
        if proc.poll() is None and not done.wait(timeout):
            timed_out.set()
            _kill_tree(proc)

    done = threading.Event()
    threading.Thread(target=watchdog, daemon=True).start()
    try:
        assert proc.stdout is not None
        for raw in iter(proc.stdout.readline, b""):
            line = scrub(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
            count += 1
            tail.append(line)
            if count <= 2000:
                transcript.write(line)
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        _kill_tree(proc)
    finally:
        done.set()
    return RunResult(lines=list(tail), line_count=count, exit_code=None if timed_out.is_set() else proc.returncode,
                     timed_out=timed_out.is_set(), seconds=round(time.monotonic() - started, 2))


def _builtin(decision: Decision, cwd: Path, transcript: Transcript) -> RunResult:
    lines: list[str] = []
    started = time.monotonic()
    if decision.builtin == "pwd":
        lines = [str(cwd)]
    elif decision.builtin == "cd":
        lines = []
    elif decision.builtin == "list":
        assert decision.target is not None
        try:
            entries = sorted(decision.target.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold()))
        except OSError as exc:
            return RunResult(error=f"Could not list it: {type(exc).__name__}", exit_code=1)
        for p in entries[:200]:
            try:
                st = p.stat()
                when = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
                lines.append(f"{when}  {'<DIR>' if p.is_dir() else format(st.st_size, ',').rjust(12)}  {p.name}")
            except OSError:
                lines.append(f"{'':16}  {p.name}")
        if len(entries) > 200:
            lines.append(f"... and {len(entries) - 200} more")
        lines.append(f"{sum(p.is_dir() for p in entries)} folders, {sum(not p.is_dir() for p in entries)} files")
    elif decision.builtin == "read":
        assert decision.target is not None
        data = decision.target.read_bytes()[:MAX_READ_BYTES]
        lines = data.decode("utf-8", errors="replace").splitlines()
    elif decision.builtin == "echo":
        lines = [decision.echo]
    elif decision.builtin == "where":
        found = shutil.which(decision.echo)
        lines = [found] if found else []
        if not found:
            lines = [f"'{decision.echo}' was not found on the PATH"]
            return RunResult(lines=lines, line_count=1, exit_code=1, seconds=0.0)
    out = [scrub(x) for x in lines]
    for x in out[:2000]:
        transcript.write(x)
    return RunResult(lines=out[-TAIL_LINES:], line_count=len(out), exit_code=0, seconds=round(time.monotonic() - started, 3))


# ---------------------------------------------------------------------------------------------------------------- summary
def summarise(command: str, lines: list[str], exit_code: int | None, timed_out: bool, builtin: str | None = None, line_count: int | None = None) -> str:
    """One short sentence to say aloud: "3 tests failed, 12 passed.", "On branch main: 2 modified, 1 untracked.", never the whole output."""
    text = "\n".join(lines)
    low = command.casefold()
    last = next((x.strip() for x in reversed(lines) if x.strip()), "")
    if timed_out:
        return "It took too long, so I stopped it."
    jest = re.search(r"Tests:\s+(?:(\d+) failed, )?(?:(\d+) passed)?", text)
    if jest and ("jest" in low or "npm" in low or "yarn" in low):
        return (f"{jest.group(1)} tests failed, " if jest.group(1) else "") + (f"{jest.group(2)} passed." if jest.group(2) else "Tests finished.")
    if re.search(r"\bpytest\b|py\.test|-m pytest", low) or re.search(r"\d+ (?:passed|failed)|no tests ran", text):
        failed = re.search(r"(\d+) failed", text)
        passed = re.search(r"(\d+) passed", text)
        errors = re.search(r"(\d+) errors?", text)
        parts = []
        if failed:
            parts.append(f"{failed.group(1)} test{'s' if failed.group(1) != '1' else ''} failed")
        if errors:
            parts.append(f"{errors.group(1)} error{'s' if errors.group(1) != '1' else ''}")
        if passed:
            parts.append(f"{passed.group(1)} passed")
        if parts:
            return ", ".join(parts).capitalize() + "."
        if "no tests ran" in text:
            return "No tests ran."
    if exit_code not in (0, None):   # a failed command is said to have failed, whatever kind of command it was
        return f"It failed with exit code {exit_code}" + (f": {short_form(last, 80)}" if last else ".")
    if low.startswith("git status"):
        branch = re.search(r"On branch (\S+)", text)
        if "nothing to commit" in text:
            return f"On branch {branch.group(1)}: working tree clean." if branch else "Working tree clean."
        mod = len(re.findall(r"^\s*(?:modified|new file|deleted|renamed):", text, re.M))
        unt = 0
        if "Untracked files:" in text:
            block = text.split("Untracked files:")[1]
            unt = len([x for x in block.splitlines()[2:] if x.startswith(("\t", "    ")) and x.strip()])
        if branch:
            return f"On branch {branch.group(1)}: {mod} changed, {unt} untracked." if (mod or unt) else f"On branch {branch.group(1)}."
    if low.startswith("git log"):
        subjects = [x for x in lines if x.strip()]
        return f"{len(subjects)} commits; the latest: {short_form(subjects[0], 60)}" if "--oneline" in low and subjects else f"Showed {line_count or len(lines)} lines of history."
    if low.startswith("git diff"):
        files = len(re.findall(r"^diff --git", text, re.M))
        stat = re.search(r"(\d+) files? changed", text)
        n = int(stat.group(1)) if stat else files
        return f"{n} file{'s' if n != 1 else ''} changed." if n else "No changes."
    if builtin == "list":
        return last + "."
    if builtin == "cd":
        return "Moved there."
    if builtin == "pwd":
        return f"You are in {short_form(lines[0], 60)}." if lines else "Done."
    if builtin == "echo":
        return short_form(lines[0], 80) if lines else "Done."
    if builtin == "where":
        return f"It is at {short_form(lines[0], 70)}." if lines else "Not found."
    if builtin == "read":
        return f"Read {line_count or len(lines)} lines."
    if low.startswith(("pip list", "pip3 list")):
        return f"{max(0, (line_count or len(lines)) - 2)} packages installed."
    if re.search(r"--version|-v$|-V$", low) or low.endswith(" version"):
        return short_form(lines[0], 60) if lines else "Done."
    if exit_code not in (0, None):
        return f"It failed with exit code {exit_code}" + (f": {short_form(last, 80)}" if last else ".")
    return "Finished without errors." + (f" Last line: {short_form(last, 60)}" if last and len(last) <= 60 else "")


# ---------------------------------------------------------------------------------------------------------------- tools
class RunTerminalArgs(BaseModel):
    command: str = Field(min_length=1, max_length=500, description="The one command to run, exactly as the user asked (never invented)")
    cwd: str | None = Field(default=None, max_length=300, description="Absolute folder to run it in; empty = the current terminal folder")
    shell: Literal["powershell", "cmd"] = Field(default="powershell", description="Which command interpreter runs a command that is not read-only")
    timeout_seconds: int = Field(default=DEFAULT_TIMEOUT, ge=5, le=MAX_TIMEOUT)


class RunTerminalTool(BaseTool):
    name = "run_terminal_command"
    description = ("Run ONE terminal command in a folder with its output visible in an AgentDesk terminal window; the last 50 lines come back. "
                   "Read-only commands (cd, dir, pwd, type, echo, where, git status/log/diff, python/pip/npm versions) run at once; every other "
                   "command needs approval and is shown in full; destructive ones (del, rm, format, Remove-Item, shutdown, curl | iex) need a click.")
    risk_level = RiskLevel.MEDIUM
    args_schema = RunTerminalArgs
    isolation = "thread"            # starts and supervises its own child process, tails files, and must not be killed half-way by the worker
    interruptible = False

    def _allowed(self) -> list[Path]:
        return [p.expanduser().resolve() for p in self.ctx.allowed_directories]

    def _cwd(self, a: RunTerminalArgs) -> Path:
        if a.cwd:
            return resolve_within_allowed(a.cwd, self._allowed())
        return session_cwd(self.ctx)

    def decide(self, a: RunTerminalArgs) -> Decision:
        try:
            cwd = self._cwd(a)
        except (PathNotAllowedError, ValueError):
            return Decision("high", RiskLevel.HIGH, "That folder is outside the folders the assistant may work in.", shown=short_form(a.command))
        return classify(a.command, cwd, self._allowed())

    def risk_for_args(self, args: BaseModel | None) -> RiskLevel:
        if args is None:
            return RiskLevel.MEDIUM
        return self.decide(RunTerminalArgs.model_validate(args.model_dump())).risk

    def execute(self, args: BaseModel) -> ToolResult:
        a = RunTerminalArgs.model_validate(args.model_dump())
        try:
            cwd = self._cwd(a)
        except (PathNotAllowedError, ValueError) as exc:
            return ToolResult(success=False, error=str(exc))
        if not cwd.is_dir():
            return ToolResult(success=False, error=f"The folder {cwd} does not exist.")
        decision = classify(a.command, cwd, self._allowed())
        if decision.kind == "blocked":
            return ToolResult(success=False, error=decision.reason)
        transcript = Transcript()
        view = ensure_view(transcript)
        seq = int(load_state().get("seq", 0)) + 1
        transcript.write("")
        transcript.write(f"PS {cwd}> {a.command}")
        if decision.builtin:
            result = _builtin(decision, cwd, transcript)
        else:
            if decision.argv:
                exe = resolve_program(decision.argv[0])
                if exe is None:
                    transcript.write(f"'{decision.argv[0]}' was not found on the PATH")
                    result = RunResult(lines=[f"'{decision.argv[0]}' was not found on the PATH"], line_count=1, exit_code=127)
                else:
                    result = _stream([exe, *decision.argv[1:]], cwd, a.timeout_seconds, transcript, env=_clean_env())
            elif a.shell == "cmd":
                result = _stream(f'cmd.exe /d /s /c "{a.command}"', cwd, a.timeout_seconds, transcript, env=_clean_env())
            else:
                result = _stream(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", a.command], cwd, a.timeout_seconds, transcript, env=_clean_env())
        if result.error:
            transcript.write(f"[{result.error}]")
            return ToolResult(success=False, error=result.error)
        cwd_after = decision.target if decision.builtin == "cd" and decision.target else cwd
        state = load_state()
        state.update({"cwd": str(cwd_after), "seq": seq})
        save_state(state)
        status = "timed out" if result.timed_out else f"exit {result.exit_code}"
        transcript.write(f"[#{seq} {status}, {result.seconds}s, {result.line_count} lines]")
        spoken = summarise(a.command, result.lines, result.exit_code, result.timed_out, decision.builtin, result.line_count)
        output = {"command": a.command, "decision": decision.kind, "cwd": str(cwd), "cwd_after": str(cwd_after), "exit_code": result.exit_code,
                  "timed_out": result.timed_out, "seconds": result.seconds, "line_count": result.line_count, "lines": result.lines,
                  "seq": seq, "transcript": str(transcript.path), "view_pid": view, "builtin": decision.builtin, "spoken": spoken}
        return ToolResult(success=True, output=output)

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        """Read the transcript back: the end marker of THIS run must be there with the same exit code and line count."""
        if not result.success:
            return failed_result(result)
        out = result.output
        try:
            tail = Path(out["transcript"]).read_text(encoding="utf-8-sig", errors="replace").splitlines()[-(TAIL_LINES + 4):]
        except OSError as exc:
            return Verification(passed=False, checks=["transcript readable"], details=f"{type(exc).__name__}")
        status = "timed out" if out.get("timed_out") else f"exit {out.get('exit_code')}"
        marker = next((ln for ln in reversed(tail) if ln.startswith(f"[#{out.get('seq')} ")), "")
        state = load_state()
        checks = [("the run's end marker is in the transcript", bool(marker) and status in marker),
                  ("the command finished (no time-out)", not out.get("timed_out")),
                  ("the terminal remembers the folder", Path(state.get("cwd", "")) == Path(out["cwd_after"]))]
        evidence = {"exit_code": out.get("exit_code"), "lines": out.get("line_count"), "cwd": out.get("cwd_after"), "marker": marker,
                    "window_pid": out.get("view_pid")}
        return verdict(checks, evidence, details=None if not out.get("timed_out") else "It was stopped after the time limit.")


def resolve_program(name: str) -> str | None:
    """The program to run for `name`. On Windows `python` on the PATH is often the Microsoft Store stub, which only prints "Python was not
    found": then the py launcher, or the newest real installation, is used instead."""
    found = shutil.which(name)
    if found and not (name.casefold().removesuffix(".exe") in ("python", "python3", "pip", "pip3") and "windowsapps" in found.casefold()):
        return found
    if name.casefold().removesuffix(".exe") in ("python", "python3", "pip", "pip3") and os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python"
        installs = sorted(local.glob("Python3*"), reverse=True) if local.is_dir() else []
        for folder in installs:
            exe = folder / ("python.exe" if "pip" not in name else "Scripts/pip.exe")
            if exe.exists():
                return str(exe)
        launcher = shutil.which("py")
        if launcher:
            return launcher
    return found


def _clean_env() -> dict[str, str]:
    """The environment of the command: this program's own secrets (API keys, tokens) are not handed to it."""
    drop = re.compile(r"(?i)(?:api[_-]?key|secret|token|password|groq|openai|tavily|cloud_brain)")
    env = {k: v for k, v in os.environ.items() if not drop.search(k)}
    real = resolve_program("python")
    if real and Path(real).name.casefold() == "python.exe" and (shutil.which("python") or "") != real:
        # `python` on the PATH is the Microsoft Store stub: put the real interpreter (and its Scripts folder) first so `python` and `pip` work
        folder = Path(real).parent
        env["PATH"] = os.pathsep.join([str(folder), str(folder / "Scripts"), env.get("PATH", "")])
    return env


class OpenTerminalArgs(BaseModel):
    folder: str | None = Field(default=None, max_length=300, description="Absolute folder to open it in; empty = the current terminal folder")
    shell: Literal["terminal", "cmd", "powershell"] = Field(default="cmd", description="'command prompt' = cmd, 'PowerShell' = powershell, otherwise Windows Terminal")


class OpenTerminalTool(BaseTool):
    name = "open_terminal"
    description = "Open a real, interactive terminal (command prompt, PowerShell or Windows Terminal) in a folder for the user to type in. Runs nothing by itself."
    risk_level = RiskLevel.LOW
    args_schema = OpenTerminalArgs
    isolation = "thread"

    def execute(self, args: BaseModel) -> ToolResult:
        a = OpenTerminalArgs.model_validate(args.model_dump())
        allowed = [p.expanduser().resolve() for p in self.ctx.allowed_directories]
        try:
            folder = resolve_within_allowed(a.folder, allowed) if a.folder else session_cwd(self.ctx)
        except (PathNotAllowedError, ValueError) as exc:
            return ToolResult(success=False, error=str(exc))
        if not folder.is_dir():
            return ToolResult(success=False, error=f"The folder {folder} does not exist.")
        if os.name != "nt":
            return ToolResult(success=False, error="Opening a terminal window is supported on Windows only.")
        shell = a.shell
        if shell == "terminal" and not shutil.which("wt.exe"):
            shell = "powershell"
        if shell == "terminal":
            argv, titles = ["wt.exe", "-d", str(folder)], ["terminal", folder.name]
            processes = {"windowsterminal.exe", "wt.exe"}
        elif shell == "powershell":
            argv, titles = ["powershell.exe", "-NoLogo", "-NoExit"], ["powershell", folder.name]
            processes = {"powershell.exe", "windowsterminal.exe", "pwsh.exe"}
        else:
            argv, titles = ["cmd.exe", "/k", "title AgentDesk command prompt"], ["command prompt", "cmd", "agentdesk"]
            processes = {"cmd.exe", "windowsterminal.exe"}
        try:
            proc = subprocess.Popen(argv, cwd=str(folder), creationflags=_NEW_CONSOLE if shell != "terminal" else 0)
        except OSError as exc:
            return ToolResult(success=False, error=f"Could not open it: {type(exc).__name__}")
        state = load_state()
        state["cwd"] = str(folder)
        save_state(state)
        return ToolResult(success=True, output={"folder": str(folder), "shell": shell, "pid": proc.pid, "titles": titles, "processes": sorted(processes)})

    def verify(self, args: BaseModel, result: ToolResult) -> Verification:
        if not result.success:
            return failed_result(result)
        out = result.output
        seen, windows = window_check(title_contains=out["titles"], processes=set(out["processes"]), match="any")
        return verdict([("a terminal window is open", seen)], {"pid": out.get("pid"), "folder": out["folder"], **windows},
                       details=None if seen else "No terminal window appeared in time.")


def terminal_tools(ctx: ToolContext | None = None) -> list[BaseTool]:
    return [OpenTerminalTool(ctx), RunTerminalTool(ctx)]
