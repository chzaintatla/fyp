# API reference

Base URL: `http://127.0.0.1:8000`. Interactive docs: `/docs`.

## Health & config

### `GET /health`
```json
{"status": "ok", "version": "0.1.0"}
```

### `GET /config/status`
Never exposes the API key — only whether one is configured.
```json
{
  "ai_provider": "mock",
  "ai_model": "openai/gpt-oss-120b",
  "ai_configured": true,
  "mock_mode": true,

  "allowed_directories": ["C:\\Users\\you\\Documents\\AgentDeskTest"],
  "approved_applications": ["chrome", "notepad", "calc", "explorer"],
  "platform": "Windows"
}
```

### `GET /usage`
Local plan, limits and counters (requires the API token). Contains no command text.
```json
{"plan": "free",
 "limits": {"tasks_day": 20, "ai_calls_day": 20, "ai_calls_month": 200},
 "used": {"tasks_day": 3, "actions_day": 5, "ai_calls_day": 1, "ai_calls_month": 12},
 "remaining": {"tasks_day": 17, "ai_calls_day": 19, "ai_calls_month": 188},
 "resets_at_utc": "2026-10-28T19:00:00Z"}
```
Plans come from `AGENTDESK_PLAN` (`unlimited` default, `standard`, `free`; an unknown name stops startup).
Limits cover only new tasks and cloud-AI calls. When the daily task limit is reached `POST /tasks` returns
`429` with the message and the usage snapshot; when an AI limit is reached the running task becomes
`waiting_retry` until the period resets. Local PC commands, approvals, rollback and logging are never limited.
Counters reset at **local** midnight (daily) and the first of the local month. This is a local budget guard,
not tamper-proof licensing.

### `GET /tools`
Lists the registered tools with descriptions, risk levels, and JSON schemas.
`control_desktop` can inspect the foreground window or execute up to eight
bounded mouse/keyboard steps, including closing a uniquely matched window.
Inspection is low risk. Input actions are auto-approved only when the user's
command names the target app and describes the action; unmatched or unnamed
targets still require approval. Named close requests are auto-approved the same
way; unnamed closes still need approval. A close is verified by checking that
the target window is gone.

The registry also includes `web_search` (opens Google results in the default
browser), `read_text_file`, `write_text_file`, `open_file`, `list_trash`,
`restore_file`, `download_web_content`, `copy_file`, and `compose_email`.
Most file operations are restricted to
configured allowed directories. `create_folder` and `delete_file` are
exceptions: after user
confirmation, it may create a folder at any absolute path the operating system
permits; `delete_file` needs explicit approval and moves only files or empty
folders to AgentDesk Trash so they can be restored. Replacing a document, restoring from the
AgentDesk Trash, and downloading content require approval. Downloads are
limited to 20 MB; local/private network hosts, overwrites, and video extraction
are blocked. Set `save_as_webpage: true` to save a public HTML page; otherwise
HTML responses are not treated as file downloads.

Deleting a file or empty folder moves it to AgentDesk's internal Trash rather
than permanently removing it. `list_trash` returns `trash_id` values, which
`restore_file` accepts. This Trash is not the Windows Recycle Bin.

### Text documents

`write_text_file` creates a UTF-8 text document inside an allowed directory.
It will not overwrite an existing file unless `overwrite: true` is explicitly
set; replacements require high-risk approval. `read_text_file` returns at most
50,000 characters. `open_file` opens an existing file using its default desktop
application.

### Copy and email drafts

`copy_file` copies a file or directory inside allowed directories; it will not
overwrite existing destinations and refuses symbolic links.

`compose_email` prepares a draft in the system's configur ed email application
using a `mailto:` link. It never sends email; the user reviews the recipient,
subject, and body, then manually sends or rejects the draft in that application.
The email application must be configured in the operating system.

```json
{"to": "user@example.com", "subject": "Project update", "body": "Hello..."}
```

### Web search and downloads

`web_search` opens Google results, or YouTube's own search results when
`site` is `youtube.com`. On Windows, YouTube search/navigation reuses an
already-open YouTube window when possible; otherwise it opens through the
default browser. It navigates the current YouTube tab and does not select or
start a video automatically.

For an explicit request to play a named video/song, `play_youtube_video`
searches YouTube for a close title match and opens its watch page with autoplay
requested, reusing an existing YouTube window when available. If the title
match is unclear or YouTube search cannot be parsed, it opens the search results
instead. YouTube and browser autoplay settings can prevent playback; the tool
reports a selected video as opened, not playback-confirmed.

```json
{"query": "Hindi cartoon song title"}
```
```json
{"query": "relaxing piano music", "site": "youtube.com"}
```

`download_web_content` downloads a direct public file URL or saves an HTML
page, only inside an allowed directory:
```json
{"url": "https://example.com/report.pdf", "output_path": "C:\\Users\\you\\Documents\\report.pdf"}
```
For HTML pages set `save_as_webpage` to `true`. The response is capped at 20 MB
and existing destination files are never overwritten.

## Authentication and request guard

`/tasks*`, `/config*` and `/tools` require `Authorization: Bearer <token>` (`401` otherwise).
`/health`, CORS preflight, the static UI and `/browser/*` do not. The token is a random
per-user secret stored in `%LOCALAPPDATA%\AgentDesk\api_token` (override: `AGENTDESK_TOKEN_FILE`).
The launcher opens the UI as `http://127.0.0.1:8000/#token=...`; the fragment is never sent to the
server, the UI keeps the token in `sessionStorage` for that tab and clears it from the address bar.
Set `AGENTDESK_REQUIRE_API_TOKEN=0` to disable (tests only).

All routes also reject a `Host` header other than `127.0.0.1`, `localhost` or `[::1]` (DNS rebinding)
and reject state-changing requests carrying a foreign `Origin` (`403`). The API must stay bound to loopback.

## Tasks

### `POST /tasks`
Plan a command.
```json
// request. Unknown fields are rejected with 422 (approval cannot be requested by the client)
{"command": "Create a folder called FYP in Documents", "idempotency_key": "optional-unique-key"}
// response: TaskOut (status: awaiting_approval | approved | completed | failed | awaiting_input | waiting_retry)
```
`command` is 1-2000 characters. If `idempotency_key` matches an existing task,
that task is returned unchanged: no duplicate planning or execution.

### `GET /tasks`
History, newest first. Query: `limit` (default 20, max 100).

### `GET /tasks/{task_id}`
One task with its actions. `404` if unknown.

### `POST /tasks/{task_id}/approve`
Approve pending actions.
```json
{"action_ids": ["<id>", "..."]}   // named actions, any risk
{}                                // all pending MEDIUM/LOW actions; HIGH-risk actions stay pending
```
**High-risk actions are approved only when named in `action_ids`.** Each approval is bound to a
SHA-256 over (task, action, tool, canonical args, risk) fixed when the action was planned. If the stored
action changed since planning, approval is refused and the task is `rejected`. MEDIUM/HIGH approvals
expire after `APPROVAL_TTL_SECONDS` (default 600); an altered or expired approval makes the action
`failed` at `/run` and nothing executes. Ids belonging to another task are ignored.
`404` unknown task; `409` if the task state does not allow approval.

### `POST /tasks/{task_id}/reject`
Reject (nothing runs). `404` / `409` as above.

### `POST /tasks/{task_id}/run`
Execute approved actions in order with per-tool timeouts and verification.
Each action is claimed atomically, so concurrent calls execute it once. Finished
tasks are never re-executed. `409` if actions are still awaiting approval.
A timed-out tool is reported as failed with `output.timed_out = true`; it may
still be running, so verify the real state before retrying.

### `POST /tasks/{task_id}/cancel`
Cancel a task that is not yet finished. An interruptible running step is stopped at once (a partial download is
deleted); other steps finish first. Finished tasks are returned unchanged.

### `POST /tasks/{task_id}/pause`

Stops an interruptible running step (download, WinGet, read-only tools) at once by killing its worker; other steps finish first. Resume with `POST /tasks/{task_id}/run`, which re-verifies the last verified step before continuing.

Ask a running task to pause (status `paused`). Non-interruptible steps (file changes, settings) finish first and the
task stops before the next action. `409` unless the task is `executing`.

### `POST /tasks/{task_id}/rollback`
Undo completed actions that have a checkpoint (`actions[].rollback_status = "available"`), newest first.
Supported: create_folder, move_file, rename_file, copy_file, compress_folder, extract_file,
write_text_file, edit_text_file (restores the previous content), delete_file (restores from AgentDesk
Trash) and download_web_content. Everything else reports `unsupported`. Each undo is refused if the
result was changed after the action (content hash differs) and is only reported `done` after the
filesystem is re-checked; the first failure stops the run and is reported in `rollback_detail`.
`409` if the task is still running/awaiting approval or nothing has a checkpoint.

### `POST /tasks/{task_id}/reply`
Conversational answer to a task in `awaiting_approval` or `awaiting_input`.
```json
{"message": "yes"}
```
`409` if the task is not waiting for a reply. A plain "yes" approves exactly the one pending action on
screen. With several pending actions it approves them only if none is high-risk; otherwise the task
stays `awaiting_approval` and each high-risk action must be approved individually.

## Pilot metrics

### `GET /metrics/tasks.csv`
Optional `days` (1-365). CSV, one row per finished task: `task_id, created_at, finished_at, duration_s, status, language,
command_chars, source, actions, tools, max_risk, approvals_required, approval_wait_s, verified_actions, failed_actions,
stopped, error_kind`. Never contains command text, paths, arguments, results, phone numbers or e-mail addresses.
With `PILOT_METRICS=1` the same row is also appended automatically to `%LOCALAPPDATA%\AgentDesk\pilot\task_metrics.csv`
when a task finishes.

## Preferences and memory (Phase 17)

All require the bearer token. Preferences never contain API keys; unknown fields are rejected with 422.

### `GET /preferences`
`{persona: {name, language, style, speak_replies, orb_color}, listen_mode: push_to_talk|hands_free|off,
provider_mode: auto|local_only, risk_policy: standard|strict, memory_enabled, onboarded, conversation_cutoff}`.

### `PUT /preferences`
Partial update with the same fields (except `conversation_cutoff`). `risk_policy` can only be `standard`
(server policy) or `strict` (every action, including low risk, waits for approval); nothing looser exists.
`provider_mode: local_only` means the cloud planner is never called.

### `GET /memory`
`{enabled, items: [{id, kind: fact|preference|alias, text, created_at, updated_at}], recent_turns: [...], conversation_cutoff}`.

### `POST /memory`
Body `{kind, text}` (text 1-500 chars). Text that looks like a password, PIN or API key is rejected with 422.

### `PUT /memory/{item_id}`
Replace kind and text. 404 if missing.

### `DELETE /memory/{item_id}`
Delete one item. 404 if missing.

### `POST /memory/forget-conversation`
Earlier commands stay in task history but are no longer used as conversation memory.

## Browser bridge

### `GET /browser/status`
`{"connected": bool, "profiles": int}`.

### `GET /browser/connect-token`
Returns the extension connection token. Requires the API bearer token (`401` otherwise) **and** header
`X-AgentX-Bridge: 1` (`403` otherwise). The extension obtains the bearer token through one-time pairing. The WebSocket `/browser/ws` accepts only `chrome-extension://` origins on a local `Host`, then requires the bridge token.

## TaskOut shape

```json
{
  "id": "...", "command": "...", "status": "completed",
  "plan_note": "[mock AI]",
  "error": null,
  "created_at": "...", "updated_at": "...",
  "retry_at": null, "provider_note": null,
  "actions": [
    {
      "id": "...", "seq": 0, "tool_name": "create_folder",
      "args": {"path": "C:\...\FYP"},
      "risk": "medium", "status": "succeeded",
      "result": {"success": true, "output": {"path": "..."}},
      "verification": {"passed": true, "checks": ["folder exists: True"]},
      "error": null
    }
  ]
}
```

## State machines

Enforced in `backend/app/state.py`; an illegal transition raises `InvalidTransition`.
See [docs/audit/ARCHITECTURE.md](audit/ARCHITECTURE.md) for the diagram.

Task statuses: `created`, `planned`, `awaiting_approval`, `awaiting_input`,
`approved`, `executing`, `waiting_retry`, `completed`, `failed`, `cancelled`, `rejected`
(last four are terminal).
Action statuses: `pending`, `approved`, `rejected`, `running`, `succeeded`,
`failed`, `skipped`, `cancelled`.

### `DELETE /memory`
Delete every remembered note (the Memory page asks first). Returns `{"deleted": <count>}`. Task history is not touched.

### `GET /voice/greeting`
`{"enabled": bool, "text": str}`: the time-of-day greeting with the user's name; `enabled` follows the "daily greeting" setting.
