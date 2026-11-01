# Terminal control

Two tools, both for a real Windows PC:

- `open_terminal` (low risk): opens a real command prompt / PowerShell / Windows Terminal in a folder, for you to type in. Says "open command prompt in my FYP folder".
- `run_terminal_command` (risk set by the command): runs ONE command and shows it live in a window titled "AgentDesk terminal"; the last 50 lines come back and a short
  sentence is spoken ("3 tests failed, 12 passed.", "On branch main: 2 changed, 1 untracked.").

## What needs approval

| Kind | Examples | What happens |
|---|---|---|
| Read-only (allow-list) | cd, dir / ls, pwd, type / cat of a small non-secret file, echo, where / which, git status / log / diff / show / branch / remote -v / rev-parse, python / pip / npm / node / git versions, pip list / show, npm list | runs at once (low risk) |
| Everything else | pytest, npm test, git pull / commit, python script.py, pip install, copy, move, start ... | shown in full and read out ("I'd run this command: ... Say yes, run it, or click Approve."), medium risk |
| Destructive or powerful | del, rm, rd /s, format, reg delete, Remove-Item, shutdown, taskkill, curl or iwr piped to iex, Set-ExecutionPolicy, git push --force, git reset --hard, git clean -f, pip uninstall, npm publish | high risk: the Approve CLICK is required, a spoken yes is refused |
| Never | an interactive shell or editor (cmd, powershell, bash, vim, python or node with no arguments, ssh), several lines, control characters | refused, even if approved |

A command with any of `; & | < > ` ^ $ % ( ) { } ! * ? ~` is never on the read-only list: no redirect, pipe, variable (`echo %API_KEY%`) or second command can ride along.
The model proposes a command; it cannot lower the risk, approve itself, or add anything the user did not ask for.

## How it runs (and a deliberate difference from "typing into a terminal")

The command runs as a child process with no input, a time limit (default 120 s, at most 600; the whole process tree is killed at the limit) and the
program's own secrets (API keys, tokens) removed from its environment. Its output goes to a transcript file; the visible window follows that file live. The window
is a live view, not a shell the assistant types into: that is what makes the exit code, the output capture and the time-out reliable. The current folder is kept
between commands (`cd backend` then `git status`). Output is masked for keys, tokens and `password=...` values before it is shown or spoken.
A non-zero exit code is reported as it is ("It failed with exit code 3: ..."), never as success.

Files: `backend/app/tools/terminal_policy.py` (what is allowed), `terminal.py` (running, transcript, window, summaries). Check: `scripts\check_u5.ps1`.
