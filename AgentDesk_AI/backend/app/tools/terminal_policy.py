"""Which terminal commands may run without asking, which need a click, and which never run.

A model may PROPOSE a command; this module decides what happens to it, and the model has no say:

  allow    read-only and local: cd, dir / ls, pwd, type / cat (small, non-secret files), echo, where / which, git status / log / diff / show /
           branch, python / pip / npm / node versions and lists. Run at once (low risk). Most of them are done by this program itself and
           never reach a shell at all; the rest run as a program with a fixed argument list (no shell, so nothing can be chained).
  approve  everything else, once the user says "yes, run it" or clicks Approve (medium risk). The whole command is shown and read out.
  high     destructive or powerful (del, rm, rd /s, format, reg delete, Remove-Item, shutdown, curl | iex, Set-ExecutionPolicy, git push
           --force, git reset --hard ...): a click is required, a spoken yes is not enough (high risk).
  blocked  an interactive shell or editor (it would wait for input forever), several commands on separate lines, control characters.

Allow-listing is done on the exact text: a command containing any of ; & | < > ` ^ $ % ( ) { } ! * ? ~ is never allow-listed, so a
redirect, a pipe, an environment variable ("echo %API_KEY%") or a second command cannot ride along with a harmless-looking one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..security.paths import PathNotAllowedError, resolve_within_allowed
from .base import RiskLevel

MAX_COMMAND_CHARS = 500
MAX_READ_BYTES = 64 * 1024
_META = re.compile(r"[;&|<>`^$%(){}!*?~\r\n]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# Destructive or powerful: a click, never just a spoken yes. Matched anywhere in the command, chained or not.
HIGH_PATTERNS: list[tuple[str, re.Pattern]] = [(name, re.compile(rx, re.I)) for name, rx in [
    ("deletes files", r"(?<![\w-])(?:del|erase|rm|unlink|remove-item|ri)(?![\w-])"),
    ("removes folders", r"(?<![\w-])(?:rd|rmdir)(?![\w-])"),
    ("formats a disk", r"(?<![\w-])(?:format|format-volume|clear-disk|diskpart|cipher\s+/w|mkfs)(?![\w-])"),
    ("changes the registry", r"(?<![\w-])reg(?:\.exe)?\s+(?:delete|add|import|load|unload|restore)"),
    ("shuts down or restarts", r"(?<![\w-])(?:shutdown|restart-computer|stop-computer|logoff)(?![\w-])"),
    ("kills processes", r"(?<![\w-])(?:taskkill|stop-process|kill|pkill)(?![\w-])"),
    ("downloads and runs code", r"(?:curl|wget|iwr|invoke-webrequest|irm|invoke-restmethod|downloadstring|downloadfile)[^\n]*(?:\||;)\s*(?:iex|invoke-expression|sh|bash|cmd|powershell|pwsh|python)"),
    ("runs a string as code", r"(?<![\w-])(?:iex|invoke-expression)(?![\w-])|-e(?:nc|ncodedcommand)\b"),
    ("changes the execution policy", r"set-executionpolicy"),
    ("changes accounts or permissions", r"(?<![\w-])(?:net\s+user|net\s+localgroup|takeown|icacls|cacls|runas|bcdedit|schtasks|sc(?:\.exe)?\s+(?:delete|config|stop|create)|new-localuser|add-localgroupmember)(?![\w-])"),
    ("links or hides files", r"(?<![\w-])(?:mklink|attrib)(?![\w-])"),
    ("force-pushes or discards git work", r"git\s+(?:push\s+.*(?:--force|-f\b|--delete)|reset\s+--hard|clean\s+-\w*f|checkout\s+--\s+\.|branch\s+-[dD]\b|stash\s+(?:drop|clear))"),
    ("uninstalls or publishes packages", r"(?<![\w-])(?:pip3?\s+uninstall|npm\s+(?:uninstall|unpublish|publish)|winget\s+uninstall|choco\s+uninstall|twine\s+upload)"),
    ("overwrites with a redirect", r">>?\s*(?:nul|con|[a-z]:\\windows|/dev)"),
]]

# Interactive programs and nested shells wait for input that never comes: refused outright.
ALWAYS_BLOCKED = {"cmd", "powershell", "pwsh", "wsl", "bash", "sh", "zsh", "ssh", "telnet", "ftp", "sftp", "vim", "vi", "nano", "emacs", "less", "more",
                  "mintty", "psexec", "sudo", "su", "regedit", "mmc"}
BARE_BLOCKED = {"python", "py", "python3", "node", "irb", "ipython", "mysql", "sqlite3", "deno", "bun", "ruby", "php"}   # a REPL when run with no arguments
VERSION_TOOLS = {"python", "python3", "py", "pip", "pip3", "node", "npm", "npx", "git", "dotnet", "java", "go", "cargo", "rustc", "pytest", "docker",
                 "flutter", "winget", "code", "adb", "pnpm", "yarn", "tsc", "gcc", "make", "cmake", "ruby", "php", "deno", "bun", "kubectl", "terraform"}
_VERSION_FLAGS = {"--version", "-v", "-V", "version", "-version", "--v"}
_SECRET_FILE = re.compile(r"(?:^|[\\/])(?:\.env(?:\..*)?|\.npmrc|\.pypirc|\.netrc|\.git-credentials|api_token|id_(?:rsa|dsa|ecdsa|ed25519)(?:\.pub)?|"
                          r".*\.(?:pem|key|pfx|p12|ppk|kdbx|keystore)|.*(?:secret|credential|password|passwd|token|apikey|api_key).*)$", re.I)
_GIT_READ = {"status", "log", "diff", "show", "branch", "remote", "rev-parse", "describe", "tag", "ls-files", "blame", "shortlog", "reflog", "stash"}
_GIT_BAD_FLAGS = {"--output", "-o", "--ext-diff", "--textconv", "--exec-path", "--upload-pack", "-c", "-C", "--git-dir", "--work-tree", "--paginate",
                  "--open-files-in-pager", "--no-index"}
_SAFE_FLAG = re.compile(r"^-{1,2}[A-Za-z0-9][A-Za-z0-9-]*(?:=[\w.,:/\\-]+)?$|^-\d+$")
_SAFE_WORD = re.compile(r"^[\w.,:@/\\+=-]+$")
_LIST_FLAGS = {"/a", "/b", "/w", "/q", "-a", "-l", "-la", "-al", "-h", "-lh", "-lah", "-force", "-name", "/ad", "/a:d", "/o:n", "/o:-d"}


@dataclass
class Decision:
    kind: str                       # allow | approve | high | blocked
    risk: RiskLevel
    reason: str                     # one short sentence for the screen
    builtin: str | None = None      # handled by this program: cd, pwd, list, read, echo, where
    argv: list[str] | None = None   # an allow-listed program run without a shell
    target: Path | None = None      # cd / list / read target
    echo: str = ""
    shown: str = ""                 # a short form for speech
    labels: list[str] = field(default_factory=list)


def tokenize(command: str) -> list[str]:
    """Split on spaces, keeping "quoted parts" together (no escapes: Windows paths are full of backslashes)."""
    out: list[str] = []
    current, quote = "", ""
    for ch in command.strip():
        if quote:
            if ch == quote:
                quote = ""
            else:
                current += ch
        elif ch in "\"'":
            quote = ch
        elif ch.isspace():
            if current:
                out.append(current)
                current = ""
        else:
            current += ch
    if quote:
        raise ValueError("unbalanced quote")
    if current:
        out.append(current)
    return out


def short_form(command: str, limit: int = 70) -> str:
    one = " ".join(command.split())
    return one if len(one) <= limit else one[: limit - 1].rstrip() + "…"


def _within(raw: str, cwd: Path, allowed: list[Path]) -> Path | None:
    target = Path(raw).expanduser()
    if not target.is_absolute():
        target = cwd / target
    try:
        return resolve_within_allowed(str(target), allowed)
    except (PathNotAllowedError, ValueError, OSError):
        return None


def high_reasons(command: str) -> list[str]:
    return [name for name, rx in HIGH_PATTERNS if rx.search(command)]


def classify(command: str, cwd: Path, allowed: list[Path]) -> Decision:
    cmd = (command or "").strip()
    if not cmd:
        return Decision("blocked", RiskLevel.HIGH, "There is no command to run.")
    if len(cmd) > MAX_COMMAND_CHARS:
        return Decision("blocked", RiskLevel.HIGH, f"The command is longer than {MAX_COMMAND_CHARS} characters.")
    if _CONTROL.search(cmd) or "\n" in cmd or "\r" in cmd:
        return Decision("blocked", RiskLevel.HIGH, "One command at a time, on one line.")
    shown = short_form(cmd)
    reasons = high_reasons(cmd)
    if reasons:
        return Decision("high", RiskLevel.HIGH, "This command " + " and ".join(reasons[:2]) + ".", shown=shown, labels=reasons)
    try:
        tokens = tokenize(cmd)
    except ValueError:
        return Decision("blocked", RiskLevel.HIGH, "The quotes in the command do not match.", shown=shown)
    first = tokens[0].casefold().removesuffix(".exe")
    if first in ALWAYS_BLOCKED or (first in BARE_BLOCKED and len(tokens) == 1):
        return Decision("blocked", RiskLevel.HIGH, f"'{first}' is interactive: it would wait for input that never comes. Say what to run with it.", shown=shown)
    plain = not _META.search(cmd)
    if plain:
        decision = _allow(first, tokens, cwd, allowed, shown)
        if decision is not None:
            return decision
    why = "It is not on the list of read-only commands." if plain else "It chains, redirects or expands something, so it is not on the read-only list."
    return Decision("approve", RiskLevel.MEDIUM, why + " It runs only after you approve it.", shown=shown)


def _allow(first: str, tokens: list[str], cwd: Path, allowed: list[Path], shown: str) -> Decision | None:
    args = tokens[1:]
    low = [a.casefold() for a in args]
    ok = lambda **kw: Decision("allow", RiskLevel.LOW, "Read-only: runs at once.", shown=shown, **kw)   # noqa: E731

    if first in ("cd", "chdir", "set-location", "sl"):
        args = [a for a in args if a.casefold() != "/d"]
        if not args:
            return ok(builtin="pwd")
        if len(args) != 1:
            return None
        target = _within(args[0], cwd, allowed)
        if target is None or not target.is_dir():
            return None
        return ok(builtin="cd", target=target)
    if first in ("pwd", "get-location") and not args:
        return ok(builtin="pwd")
    if first in ("dir", "ls", "gci", "get-childitem"):
        flags = [a for a in low if a.startswith(("/", "-"))]
        paths = [a for a in args if not a.startswith(("/", "-"))]
        if any(f not in _LIST_FLAGS for f in flags) or len(paths) > 1:
            return None
        target = _within(paths[0], cwd, allowed) if paths else cwd
        if target is None or not target.is_dir():
            return None
        return ok(builtin="list", target=target)
    if first in ("type", "cat", "get-content", "gc"):
        if len(args) != 1 or args[0].startswith(("/", "-")):
            return None
        target = _within(args[0], cwd, allowed)
        if target is None or not target.is_file() or _SECRET_FILE.search(str(target)) or target.stat().st_size > MAX_READ_BYTES:
            return None
        return ok(builtin="read", target=target)
    if first in ("echo", "write-output", "write-host"):
        text = " ".join(args)
        return ok(builtin="echo", echo=text) if len(text) <= 200 else None
    if first in ("where", "which", "get-command"):
        if len(args) == 1 and re.fullmatch(r"[A-Za-z0-9_.+-]+", args[0]):
            return ok(builtin="where", echo=args[0])
        return None
    if first in VERSION_TOOLS and len(args) == 1 and args[0] in _VERSION_FLAGS:
        return ok(argv=[tokens[0], args[0]])
    if first == "git":
        return _git(tokens, ok)
    if first in ("pip", "pip3") and args and low[0] in ("list", "freeze", "check") and all(_SAFE_FLAG.match(a) for a in args[1:]):
        return ok(argv=[tokens[0], *args])
    if first in ("pip", "pip3") and len(args) == 2 and low[0] == "show" and _SAFE_WORD.match(args[1]):
        return ok(argv=[tokens[0], *args])
    if first in ("npm", "pnpm", "yarn") and args and low[0] in ("list", "ls", "outdated") and all(_SAFE_FLAG.match(a) for a in args[1:]):
        return ok(argv=[tokens[0], *args])
    return None


def _git(tokens: list[str], ok) -> Decision | None:
    args = tokens[1:]
    if not args or args[0].casefold() not in _GIT_READ:
        return None
    sub, rest = args[0].casefold(), args[1:]
    for token in rest:
        if token.startswith("-"):
            if not _SAFE_FLAG.match(token) or token.split("=")[0] in _GIT_BAD_FLAGS:
                return None
        elif not _SAFE_WORD.match(token):
            return None
    if sub == "branch" and any(t.startswith("-") and t not in ("-a", "-r", "-v", "-vv", "--list", "-l", "--show-current", "--all", "--remotes") for t in rest):
        return None
    if sub == "branch" and any(not t.startswith("-") for t in rest):
        return None   # `git branch name` creates a branch
    if sub == "remote" and rest not in ([], ["-v"], ["--verbose"]):
        return None
    if sub == "tag" and rest and rest[0] not in ("-l", "--list"):
        return None   # `git tag name` creates a tag
    if sub == "stash" and rest != ["list"]:
        return None
    if sub == "reflog" and rest and rest[0] in ("expire", "delete"):
        return None
    return ok(argv=["git", "--no-pager", sub, *rest])
