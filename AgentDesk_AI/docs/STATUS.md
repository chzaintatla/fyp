# AgentDesk AI — STATUS

Single source of truth for "what is done". Updated at the end of every session.
Older evidence: `FEATURE_AUDIT.md`, `audit/FEATURE_TRACEABILITY_MATRIX.md`,
`audit/FINAL_ACCEPTANCE_AND_SECURITY_REPORT.md`, `audit/phases/PHASE_*_COMPLETION.md`.

## Session log

### 2026-11-01 — Session 15: Step 1f, App.tsx split (no behaviour change)

**Done:** `frontend/src/App.tsx` 1,417 -> 212 lines. Moved to `taskFormat.ts`, `components/{Spinner,ActionCard,AppHeader,CommandPanel,TaskPanel,TaskHistory}.tsx`,
`speech/recognitionTypes.ts`, and hooks `useTasks`, `useAppData`, `useVoiceCore` (shared voice state/refs), `useVoiceInput` (microphone side), `useSpokenReplies`.
The largest new file is 252 lines. The code was moved, not rewritten; one-off `stopSpeaking` handler extracted from the button.

**Tested how:** `tsc --noEmit` clean; `npm test` 21/21; `npm run test:e2e` (Edge, real backend, includes pixel snapshots) 23/23; `npm run build` ok.

**Not verified:** real microphone / real voice (the e2e microphone is faked). `src/api.ts` is still 347 lines (over the 300 limit; not part of App.tsx).

### 2026-10-31 — Session 14: M5 web search rebuild (branch `m5-search`, NOT merged, NOT yet measured live)

**Decision (user, 2026-10-31):** option B. Wikipedia official REST API + Tavily, behind a `SearchProvider` interface; no scraping, no
user-agent spoofing. Contact e-mail for Wikipedia's User-Agent and the Tavily key are still to be provided by the user.

**Done**
- Removed the DuckDuckGo HTML scraping and its browser-style user agent. `app/search/`: `provider.py` (interface, `SearchBusy`,
  `SearchNotConfigured`), `wikipedia.py` (REST search + page summaries, User-Agent `AgentDesk-AI/0.3 (Riphah FYP; <email>)`, at most 1 request/second,
  off until `WIKIPEDIA_CONTACT_EMAIL` is set), `tavily.py` (key from Windows Credential Manager or `TAVILY_API_KEY`; sent only in the
  Authorization header), `router.py` (Wikipedia first for "what/who is ..." definitions, Tavily for everything else, each the other's fallback),
  `cache.py` (10-minute TTL), `budget.py` (token budget with gpt-oss's `o200k_harmony` tokenizer; estimate fallback is labelled).
- `web_answer` now: checks the privacy switch BEFORE any search request (the question itself goes to Wikipedia/Tavily), reads provider snippets
  (no page scraping), keeps the whole prompt <= 1,500 tokens, summarises with gpt-oss-20b, caches answers 10 minutes, and says
  "Search is busy, try again in a minute." on 429 / Tavily 432-433. Verifier also checks the token budget.
  `summarise_page` / `summarise_file` use the same budget and the small model; the page reader identifies itself honestly as `AgentDesk-AI/0.3`.
- `scripts/set_search_key.py` (hidden prompt -> Credential Manager), `docs/SEARCH_SETUP.md`, Settings shows which providers are set up.
- `scripts/eval_conversation.py`: model grading (gpt-oss-120b, correct / partly / wrong) next to keyword grading, CSV for spot-checks, target
  web >= 18/20 model-graded correct.

**Tested how:** `pytest tests -q` -> 947 passed, 4 skipped (new `tests/test_m5_search.py`, 41 tests: Wikipedia headers + 1/s spacing, Tavily key only in the
header, 429/432/433, routing table, cache TTL, token budget, injection text fenced, no request before permission). All with fakes.

**NOT measured:** nothing live. Wikipedia and Tavily were not called (no e-mail, no key), so the web target (>= 18/20, twice, an hour apart) is
unproven. M5's last live result stays: web 12/20 (old DuckDuckGo path, keyword graded). The first run of the tokenizer downloads its vocabulary
file from a public host (a new network destination, no user data).

### 2026-10-31 — Session 13: Step 0 merge to main (v0.3.0)

**Done:** merged `m1-cloud-brain`, `m2-voice`, `m3-avatars`, `m4-ui`, `m5-memory-chat` into `main` in that order (merge commits, no squash).
Full backend + frontend unit suites after each merge: 736, 774, 831, 837, 918 backend tests passed (4 skipped) and 4, 11, 18, 21, 21 frontend tests.
Browser tests (Edge) 14/14 on the M5 branch before the merge. Tagged `v0.3.0`.

**Known issue (not fixed):** `tests/test_phase16_worker.py::test_apps_started_by_earlier_jobs_survive_a_later_kill` is flaky:
about 3-4 failures in 20 runs, and the same rate on the original v0.2.0 commit `7a5f967`, so it is not new. In the failing runs the helper app
started by the earlier job is already gone when the assertion runs although it is not in `killed_pids`. Root cause not found. It could be
only a test race (the helper is a venv launcher process), or a real defect where an app the assistant opened closes when a later tool times out.
Needs investigation before v1.0. One full-suite run during the M5 merge failed on it; the rerun passed (918).

**Web answers target NOT met:** M5 live eval web 12/20 (target 18/20). Web search rebuild in progress on `m5-search` (user decision: Wikipedia
REST API + Tavily; waiting for the contact email and the Tavily key).

### 2026-10-31 — Session 12: M5 Memory and conversation (branch `m5-memory-chat`)

**Done**
- Memory tools `memory_remember` (low), `memory_recall` (low), `memory_forget` (medium, preview + approval, refuses when several notes match);
  voice/typed routing by strict rules (`app/conversation.py`), `DELETE /memory` (delete all) + button, secret guard now also refuses
  "my password is ..." (it let that through before), daily greeting (`GET /voice/greeting`, Settings switch, once per day).
- Chat answers (`conversation_flow.py`): questions and small talk get a spoken answer of at most 3 sentences with persona wording and the
  saved notes as fenced data. `web_answer` (search, read up to 3 pages as data, answer + sources), `summarise_page`, `summarise_file`
  (txt/md/csv/pdf/docx, allowed folders only), "summarise <url or full path>" routing. A spoken line for a verified single action is its own
  `spoken` text, not "Done."
- **Privacy gate (decided by the user, 2026-10-31):** questions and web text go to Groq only after the user says yes. Onboarding step
  "Questions and privacy" asks once; Settings has the switch; unanswered or "no" means questions are refused locally with a spoken
  explanation and nothing is sent. One switch covers chat and web answers; "Local only" mode overrides it. Commands for this PC are unaffected.

**Tested how**
- `pytest tests -q` -> 900 passed before the gate; `tests/test_m5_conversation.py` 81 passed after (adds the gate, search limits, routing).
  Browser tests (Edge): 14/14 for ui + new_user after the gate.
- Live evaluation (`scripts/eval_conversation.py`, real Groq via the cloud brain, real internet, keyword-graded, run on the code BEFORE the
  fixes below): **chat 19/20, web 12/20, memory 3/3**. Chat miss: "hello" -> "Good evening. How may I assist you?" is a fine answer my
  keyword grader rejected (counted as a miss). Web misses: 6x "couldn't read any web page" (DuckDuckGo's HTML endpoint answered HTTP 202,
  a bot challenge, after a few quick queries; Wikipedia answers 403 to any generic user agent), 2x cloud brain 422 (my prompt was ~10,500
  characters; the cloud brain refuses over 8,000). **The 90% target for web answers is NOT met.**
- Fixed after the eval: prompt documents capped at 6,000 characters; a throttled search now says so instead of "couldn't read any web page".
  Not re-run live yet.

**Not done / needs the user**
- A reliable web search source. DuckDuckGo's HTML endpoint is rate-limited for automated use and Wikipedia requires contact details in the
  user agent. Options need a user decision (an API key, or what contact to send). No workaround was added.
- Human steps in `docs/TEST_SCRIPT.md` (M5). Web answers and summaries not verified on a second internet connection.

### 2026-10-31 — Session 11: M4 UI refinement (branch `m4-ui`)

**Done**
- Design tokens (`--bg/--surface/--raised/--line/--ink/--ink2/--accent/--ok/--warn/--bad`) in Tailwind + `index.css`; dark HUD by default,
  light theme toggle (remembered), accent follows the chosen character, Inter / Space Grotesk (web fonts; system fonts offline),
  14 px radius, glass cards. The old components keep their classes; legacy slate/white/blue/red/amber/emerald classes are mapped to tokens.
- Left rail (Home, Characters, Memory, Settings; bottom bar under ~770 px), Start AI / Stop button + `Ctrl + Space` hint, chat-bubble
  transcript, avatar-first Home. Mini mode = 72 px character bubble with state glow and an "Approve?" tag. Tray: Start/stop listening,
  mini mode, Settings (`window.__agentx` hooks, fixed calls only). Settings: follow-up window 0-15 s and speech volume (new preferences).
- Playwright now installed (`playwright`, `pixelmatch`, `pngjs`); the harness runs on Edge and ignores local API keys.

**Tested how (all on this PC)**
- `pytest tests -q` -> 837 passed, 4 skipped. `npm test` 21 passed (incl. contrast >= 4.5:1 for every text/background token pair in both
  themes and for all six avatar accents, read from the real CSS). `npm run build` ok.
- `npm run test:e2e` -> 20/20 in Edge: 8 earlier flows (onboarding now 5 steps), 10 new UI tests (avatar select + reload, filter, personality,
  avatar mood idle/listening/concerned, approval, theme, settings persistence, layout at 1280 and 360 px, focus ring, mini mode), 2 pixel
  snapshots of Home (baselines in `frontend/e2e/snapshots/`).
- `docs/ui/`: 27 screenshots (onboarding, home, characters, memory, settings, approval card in dark and light, at 100% = 1920x1080 and
  150% = 1280x720 CSS px at 1.5x; mini mode); none scroll sideways (`docs/ui/report.json`).

**Not done / not measured**
- Not tested at real Windows display scaling 125%, on a 4K monitor, or with Narrator; "150%" is emulated in the browser.
- Tray and mini window were not run (pywebview shell); only the tray menu logic is unit-tested.
- Spec items not built: Tasks / Routines / World Monitor rail entries (M6, M7), mic device picker, speech speed, noise-level test,
  a dedicated "current task" side panel (the existing Current task card is kept below the command box), mic level meter in the bottom bar,
  Framer Motion (CSS transitions are used), the light theme keeps a fixed darker accent.
- Visual snapshots cover Home only. The fonts load from Google Fonts when online.

### 2026-10-31 — Session 10: M3 Avatars and personalities (branch `m3-avatars`, partly done)

**Done:** 6 avatars (3 female, 3 male) + custom picture, 5 personalities (Assistant, Mentor, Best Friend, Coach, Custom with sliders),
persona fields, `/voice/catalog`, personality wording in `responses.py`, SVG avatar with 6 moods, Characters page, 5-step onboarding
with mic test, Home shows the avatar (or the orb). Rule 3.9 test: same commands give identical plans under every personality.
**Tested:** `pytest tests -q` 831 passed, 4 skipped; `npm test` 18 passed; `npm run build` ok; onboarding and Characters page clicked
through in a browser (Orion + Best Friend saved with male voices). **Not done:** `scripts/check_m3.ps1` and the M3 section of
`docs/TEST_SCRIPT.md`; real voice playback of male vs female voices; e2e (Playwright not installed, flow updated but not run);
wake-word detection (phrase is only saved); Urdu/Hindi personality wording (uses Assistant wording).

### 2026-10-31 — Session 9: M2 Two-way voice (branch `m2-voice`)

**Done**
- `backend/app/voice/`: `responses.py` (spoken lines per event x language [en, Roman Urdu, Urdu, Hindi]; "Done" only when every
  action is `succeeded` AND its verification passed), `chunking.py` (sentences <= 200 chars, direction tag budgeted), `tts.py`
  (chain Orpheus -> edge-tts -> Windows SAPI; a failing engine is skipped for 5 min; 64-clip cache; SAPI picks an installed
  voice of the requested gender), `latency.py` (per-stage JSONL, no transcript text), `api.py` (`/voice/stt|tts|chunks|line|latency`,
  token-protected; `/config/status` has `speech_ready`).
- Frontend: `speech/player.ts` (sentence-by-sentence playback, next sentence generated while one plays, immediate `stop()`, browser
  voice as last resort), `speech/whisperRecognition.ts` (mic + energy VAD + pre-roll, WAV to Groq Whisper via `/voice/stt`, drop-in for
  the browser SpeechRecognition, `onspeechstart` = barge-in), `speech/vad.ts`. App.tsx: server voice when `speech_ready`, spoken
  acknowledgement after 0.9 s and "Still working" after 4.5 s of a running task, 8 s follow-up window in push-to-talk, silence
  debounce 2.2 s -> 0.5 s for Whisper input. Without a speech service the old browser voice is unchanged.
- `scripts/check_m2.ps1`, `scripts/voice_bench.py`, `scripts/tts_probe.py`, `docs/TEST_SCRIPT.md` (M2 section).
- Cloud brain URL set in `backend/.env` (git-ignored): chat through Render returned HTTP 200.

**Tested how**
- `pytest tests -q` -> 774 passed, 4 skipped, 0 failed (`tests/test_m2_voice.py`: 38). `npm test` 11 passed (incl. 7 new VAD/WAV), `npm run build` ok.
- Real engines on this PC (`tts_probe.py`): edge-tts ur-PK-UzmaNeural / AsadNeural, hi-IN-SwaraNeural / MadhurNeural PASS (1.8-3.5 s per
  sentence, new connection each time); SAPI male (David) and female (Zira) PASS (~0.5 s); Orpheus FAIL: Groq org must accept the terms.
- `voice_bench.py` (server side, cold synthesis, 30 runs, command text -> first sentence synthesized): p50 0.9 s, p90 1.56 s,
  29/30 <= 2.5 s (96.7%). With Whisper in front (`--stt`, SAPI-spoken commands): p50 2.24 s, only 22/30 (73%) <= 2.5 s; Whisper
  word accuracy 100% (30 synthetic commands). Direct Groq (no Render hop) was no faster (STT ~1.0 s). **The 90% target is NOT met
  with speech-to-text included**; Orpheus (faster than spawning SAPI) cannot be measured until the terms are accepted.
- Bugs found by measuring and fixed: action status is `succeeded` (not `completed`), so real tasks would never have said "Done";
  long `plan_note` text was read aloud; SAPI `SelectVoiceByHints` ignored gender.

**Not done / not measured (needs you)**
- Real microphone, real speaker playback, barge-in time (<= 300 ms), follow-up window and the true first-word latency in the browser:
  run `docs/TEST_SCRIPT.md` section M2 (the UI reports to `/voice/latency`).
- Orpheus: accept the terms at console.groq.com/playground?model=canopylabs/orpheus-v1-english.
- VAD is energy-based, not Silero (Silero needs onnxruntime-web); Roman Urdu is spoken by the English voice chain.
- Personalities: only the Assistant phrase set exists (M3). `PLANNER_MODE` still `legacy`; M1 items still open.

### 2026-10-31 — Session 8: M1 Live foundations (in progress)

**Done**
- `PLANNER_MODE` (`legacy` default | `shadow` | `structured`), `app/orchestration/planner_mode.py`. `shadow`: for commands the
  local parsers understood, the strict-JSON planner is also asked in a background thread and compared in
  `%LOCALAPPDATA%\AgentDesk\pilot\shadow_plans.jsonl` (command hash only). `structured`: commands the local parsers did not
  understand go to the strict planner first; any failure falls back to the old Groq agent path. Risk/approval unchanged.
- `backend/scripts/groq_smoke.py` (chat, strict-schema acceptance, 3 plans, optional `--wav` STT, `--tts`).
- `scripts/check_m1.ps1` (tests, live Groq, NLU held-out local + Groq, all tools on Windows, shadow log; evidence in `docs/eval/m1_*`).

**Tested how**
- `pytest tests -q` -> 731 passed, 3 skipped, 1 failed. The failure (`test_phase18_tools.py::test_media_key_off_windows_fails_honestly`)
  also fails without these changes: it assumes a non-Windows host. New: `tests/test_m1_planner_mode.py` (7).

- **Multiple Groq keys** (`GROQ_API_KEYS=k1,k2,...` in `backend/.env`, git-ignored; `app/ai/key_pool.py`). On HTTP 429 the key cools down
  (Groq's retry-after, else 60 s) and the next key is used at once, without waiting; a key Groq rejects (401/403) is dropped. Shared by
  the agent loop, planner, STT and TTS. Tests: `tests/test_key_pool.py` (7). Tests ignore `backend/.env`.
- **Live run found and fixed two real problems**: (1) Groq rejected the strict schema (`"required": []` on no-argument tools);
  (2) the planner prompt was ~10k tokens, over the free tier's 8k tokens/min (HTTP 413) — catalogue now names/descriptions only,
  4 few-shot examples chosen by similarity, max_tokens 900 -> about 7.1k prompt tokens per plan.

- **Cloud brain for Render** (`app/cloud_main.py`, `render.yaml`, `requirements-cloud.txt`, `docs/RENDER_DEPLOY.md`): token-guarded
  `/cloud/plan|chat|stt|tts`, fails closed without `CLOUD_API_TOKEN`, rate limited, no tools. The PC calls it through
  `app/cloud_client.py` (`CLOUD_BRAIN_URL` + `CLOUD_BRAIN_TOKEN`) and re-validates every plan locally. Tests: `test_cloud_brain.py` (6).
  Run locally with real keys: token check 401 without / plan "chrome kholo" -> `open_application` valid / chat OK. NOT yet deployed
  to Render, so cold-start time and Linux install of `requirements-cloud.txt` are unmeasured.

**Measured live (2026-10-31, free tier)**
- `groq_smoke.py`: chat PASS; strict schema accepted by gpt-oss-120b (2 plans: "open notepad", "chrome kholo" -> `open_application`, valid).
  Full smoke run: plans row FAIL with a rate limit (see below). TTS FAIL: Orpheus needs the org admin to accept its terms at
  console.groq.com/playground?model=canopylabs/orpheus-v1-english.
- All 11 keys appear to belong to one Groq organization (the 413 error names the same org): the 8k tokens/min limit is per
  organization, so switching keys does NOT add capacity. Separate accounts/organizations are needed for that.

**Not done (needs you)**
- Accept the Orpheus terms (above). Planner-vs-old comparison and the Windows tool run are NOT measured: run `scripts\check_m1.ps1`.
  Default stays `legacy` until shadow data exists.

### 2026-10-31 — Session 7: Phases 19-20 PILOT, RELEASE, REPORT

**Done**
- **Pilot task metrics** (`app/metrics.py`): with `PILOT_METRICS=1` every task that finishes (or stops as "not understood")
  appends one row to `%LOCALAPPDATA%\AgentDesk\pilot\task_metrics.csv`; `GET /metrics/tasks.csv?days=N` returns the same
  rows from the database. Rows: timings, status, language, command length, source, tools, max risk, approvals and wait,
  verified/failed actions, stop reason, error category. No command text, paths, arguments, numbers or addresses (tested).
- **Automated pilot** (`scripts/pilot_run.py`): extracts the release zip, starts that copy with everything redirected to a
  scratch folder, and runs checklist A (checksum, manifest, forbidden files), B (create/approve/verify/undo,
  write/overwrite/undo, high-risk delete refused by voice then approved and undone, cancel), D (no network → clear error;
  planted key in no log or response) and F (history after restart) by plain-language commands. Report + metrics CSV in
  `docs/pilot/`.
- **Top issues fixed** (`docs/pilot/FINDINGS.md`): (1) write/overwrite/undo was impossible by command without cloud AI —
  the Phase 15 local matcher is now a fallback after the older parser (confident, fully valid plans only; no cloud change),
  plus "overwrite X with Y" (high risk) and "first version" no longer read as an ordinal; (2) not-understood commands were
  missing from metrics; (3) Windows-only errors now categorised `unsupported_platform`.
- **Release 0.2.0**: `packaging/build_release.py` → `dist-release/AgentDesk-AI-0.2.0-win.zip` (124 files, git-ignored;
  SHA-256 `bb82612561c0077593bbac154445a055254984e33b9ec416c5629ba73af5bb9e`). Includes desktop shell and docs.
- **Traceability matrix**: new "Release 0.2.0 — measured results" table (R-01..R-43), measured-only, each row with the
  command that produced it and what is not measured.
- `scripts/latency_check.py` (server part of the latency target). `docs/USER_GUIDE.md`, `docs/DEMO_SCRIPT.md`.

**Tested how**
- `pytest tests -q` → **717 passed, 3 skipped, 0 failed**. Gate G3 PASS (99.3% / 99.3%, 0, 0). `npm run build` pass.
- Automated pilot on 0.1.0: 12 PASS / 0 FAIL / 8 NOT RUN → after fixes on 0.2.0: **13 PASS / 0 FAIL / 7 NOT RUN**.
- Latency (server only, 30 local commands): p50 0.036 s, p90 0.054 s, 30/30 under 2.5 s.

**Not done (needs you)**
- **The pilot on a second Windows PC by a person** (`docs/audit/PILOT_CHECKLIST.md`, sections A install, C devices,
  E browser profile, F voice) and the 5-person usability test. Turn on `PILOT_METRICS=1` in `backend\.env` for it.
- **Windows installer/exe**: the release zip + INSTALL.cmd is built; the PyInstaller exe must be built on Windows
  (`backend\build_windows.ps1`) and it does not include the desktop shell yet.
- Static/dependency scans not re-run for 0.2.0; new pins (rapidfuzz, pywinauto, pywebview, pystray) unverified on Windows.
- Open decision 1 (full switch of live commands to the Phase 15 planner + Groq structured output) is still yours; only the
  local fallback was added.

### 2026-10-31 — Session 6: Phase 18 INTEGRATIONS

**Done**
- **Browser extension 1.1** (`browser-extension/`). Root cause of "tab opening needs a Reload": the extension compared
  `https://site` with Chrome's normalised `https://site/` (open always "not confirmed"), and a still-loading tab only has
  `pendingUrl`, so closing it failed "Tab changed since inspection". Both now compare normalised URLs. The extension sends
  `{version, capabilities}` on connect; the backend reads the shipped `manifest.json` and, for an outdated 1.1+, sends
  `reload` (`chrome.runtime.reload()` loads the updated unpacked files — no manual click). A 1.0 extension (no `open`)
  gets a verified fallback: launch Chrome, then confirm the tab through the extension's `inspect`. `/browser/status`
  reports `version`, `expected_version`, `outdated`. Real-browser test notes in `docs/BROWSER_CONTROLS.md`.
- **`media_control`** (low): media keys via `keybd_event` (0xB3/0xB0/0xB1/0xB2); verified through the Windows media
  session (SMTC via winsdk): status for play/pause/stop, track title for next/previous; play/pause are idempotent;
  no session = honest failure. NLU: "next song", "agla gaana", "gaana rok do", Urdu/Hindi (YouTube/video stays with the browser).
- **YouTube play**: `play_youtube_video` now counts a chosen video only when Windows reports it **playing** (else
  "autoplay may be blocked — press Play"); before that it asks the extension to press play on the new tab.
- **`whatsapp_open_chat`** (medium): `whatsapp://send?phone&text` in WhatsApp Desktop, contact only from a number saved in
  Memory, international format required, never presses Send; verified by the WhatsApp window and, when UI Automation can
  read it, the draft text. NLU routes WhatsApp messages to it (was "unsupported"); 4 eval items relabelled and disclosed in
  `tests/nlu_eval/README.md`; Gate G3 still PASS (99.3% / 99.3%, 0 invented, 0 bypasses).
- **`winget_install`** (high): exact WinGet ID only (schema pattern), approval card says it accepts the license, never
  interrupted (a killed installer breaks apps), 900 s budget, already-installed is a no-op with evidence, verified by a
  fresh `winget list --id <id> --exact`. "install zoom" still searches first; "install package VideoLAN.VLC" installs.
- **Email stays a draft**: test enforces `mailto:` only, `sent: false`, no SMTP code, no tool named send*.
- Approval card shows package/source/contact/number/message plus a plain note per tool ("not sent — you press Send").
- `live_check.py`: probes for the three new tools (opt-in `--media`, `--whatsapp-phone`, `--install-id`), `--record`
  writes `docs/live_runs/*.json`. `docs/INTEGRATIONS.md` lists the four Windows commands to record one live run each.

**Tested how**
- Backend `pytest tests -q` -> **710 passed, 3 skipped, 0 failed** (new: `test_phase18_tools.py` 35, `test_phase18_browser.py` 10,
  NLU routes 9). Extension mocks `test_controls.cjs` (now incl. URL normalisation + self-reload) and `test_pairing.cjs` pass.
  Frontend build pass; `npm run test:e2e` 8/8 (unchanged).
- **Recorded live run — browser extension** (`docs/live_runs/2026-10-31-browser-extension.json`): real Chromium 153 (headless),
  real unpacked extension, paired via the real setup page, real backend on :8000: handshake 1.1.0 -> list tabs (verified) ->
  open Gmail via the extension (verified by re-inspect) -> close that tab (verified gone). This run found the loading-tab close bug.
- **Recorded live run — container** (`docs/live_runs/2026-10-31T082309Z-live_check-linux.json`): 32 tools, 15 PASS, 0 FAIL,
  17 BLOCKED. `media_control`, `whatsapp_open_chat`, `winget_install` are BLOCKED here (not Windows).

**Done-when status: NOT fully met.** Each new tool has tests and a verifier. The live run is recorded for the extension only;
media keys, WhatsApp Desktop and winget need the Windows PC — run the four commands in `docs/INTEGRATIONS.md`.

**Known issues**
- WhatsApp Desktop's text box may not be readable through UI Automation; then the open window is the only evidence.
- SMTC only sees players that report to Windows (most do; some old players do not) — those runs fail honestly.
- winget may trigger a UAC prompt; if declined, the install fails (reported with WinGet's output).

### 2026-10-31 — Session 5: Phase 17 DESKTOP EXPERIENCE

**Done**
- **Backend (preferences + memory).** Tables `preferences` and `memory_items` (schema v6). `GET/PUT /preferences`: persona (name, language, style, speak replies, orb colour), listen mode (`push_to_talk` / `hands_free` / `off`), provider mode (`auto` / `local_only` — local-only never builds a cloud provider), approval policy (`standard` / `strict` — strict makes every action, even low risk, wait for approval; there is no looser option), memory on/off, onboarded flag. `GET/POST/PUT/DELETE /memory` + `POST /memory/forget-conversation`; text that looks like a password/PIN/API key is refused (422). All new routes require the token (the route-matrix security test caught them missing at first) and are documented in `docs/API.md`.
- **Rule 3 enforced.** A spoken/typed "yes" now approves only low/medium risk; with a high-risk action pending it is refused with "High risk needs a click on Approve" (was: a bare yes could approve a single delete). This was open decision 2; the brief makes it non-negotiable. One older test rewritten accordingly.
- **Desktop UI (React).** Orb with one animation per voice state (idle, listening, transcribing, thinking, awaiting approval, awaiting answer, executing, verifying, speaking, error; respects reduced-motion) and the state as text; live transcript (interim words, command, assistant reply); action-preview dialog (risk colour + plain-language risk text, exact targets, approve all / approve one / reject, typed reply, voice answer; focus trap, Esc rejects, Approve focused for low/medium but **Reject focused for high risk** so Enter cannot approve a delete; page behind it `inert`); Settings page (persona, listen mode, provider, approval policy, usage, autostart toggle when running in the desktop shell, allowed folders); Memory page (view/add/edit/delete, recent conversation, forget conversation, memory on/off); 3-step first-run onboarding that ends by starting the microphone on a guided command; push-to-talk (microphone off after one command) and listen-mode Off (mic hidden); speak-replies toggle; mini-orb route `?view=orb`. Keyboard: Ctrl+Space mic, Alt+1/2/3 pages, `/` command box; visible focus everywhere; every control has an accessible name.
- **Bugs found by driving the UI and fixed:** a `speechSynthesis` error blanked the whole app (now caught); Tailwind `grid` overrode `hidden` so the assistant panel showed under Settings; the orb said "approval" while waiting for an answer; background controls were reachable behind the modal; Settings radios lagged the server round-trip (now optimistic, reverted on failure).
- **Desktop shell** `desktop/shell.py` (pywebview): panel window (closing hides to tray) + 132 px frameless transparent always-on-top orb window bottom-right that expands the panel; pystray tray menu (Open, Show/hide orb, Start with Windows, Quit); `desktop/autostart.py` writes/deletes only `HKCU\...\Run\AgentDesk` (no admin). The JS bridge exposes only expand/collapse/get_autostart/set_autostart — no commands, files or approvals. `launch.ensure_backend()` shared by the browser launcher and the shell. `START_AGENT_X_DESKTOP.cmd`, `requirements-desktop.txt` (pywebview, pystray; not yet in the verified lock). Release zip includes them.

**Tested how (Linux container)**
- Backend: `python -m pytest tests -q` -> **657 passed, 3 skipped, 0 failed**. New: `test_phase17_preferences.py` (8: defaults/partial update, looser/unknown values and API keys rejected, no secrets returned, memory CRUD + secret guard, forget conversation, strict policy incl. internal auto-approve, local-only never calls cloud), `test_phase17_desktop.py` (8: two windows and orb geometry, bridge surface, expand/collapse/close-to-tray, minimized start, autostart writes one value and removes it, refused off Windows, tray menu actions, run starts webview + tray).
- Frontend: `npm run build` (tsc + vite) pass; `node --test voice.test.mjs` 4/4.
- **E2E (Playwright, real backend + Chromium, fake microphone): `npm run test:e2e` -> 8/8 pass.** `new_user.test.mjs`: fresh install -> onboarding -> spoken "create a folder called Demo in Documents" -> live transcript -> preview card (medium, exact path, Approve focused) -> Approve -> task `completed` with verified folder on disk, **3.0 s automated** (gate: < 3 min).
- `scripts/eval_nlu.py` still Gate G3 PASS (unchanged).

**What is left**
- **The human gate is not measured yet**: run `docs/USABILITY_TEST.md` with 5 new users on the Windows PC (real microphone, real speech recognition, real WebView2). The automated 3 s proves the path, not human discoverability.
- Desktop shell is unit-tested with fakes only: run `START_AGENT_X_DESKTOP.cmd` on Windows, check the orb stays on top, tray menu, autostart after sign-out/in; then pin pywebview/pystray in the lock.
- The packaged exe does not include the desktop shell yet (autostart refuses in the exe rather than registering a broken command).
- Memory items are stored and editable, but the planner does not read them yet (wiring the Phase 15 NLU planner into `start_task` is still pending — open decision 1).
- Global hotkey / wake word for the orb (Phase 19 voice loop); the mini orb shows the latest task state by polling, not live listening state.

**Known issues**
- Speech recognition is still the browser's Web Speech API (needs internet); Whisper is Phase 19.
- E2E tests need Playwright installed locally (`npm i --no-save playwright`); not added to package.json to keep `npm ci` unchanged.

### 2026-10-31 — Session 4: Phase 16 REAL VERIFICATION AND CONTROL

**Done**
- **Verification contract.** `Verification.evidence` added. `BaseTool.verify()` now FAILS by default; the executor also rejects any pass with empty evidence. All 29 tools have their own `verify()` that re-reads the real system (test enforces it):
  - files: exists + size + SHA-256 (copy compares source/destination hashes; read compares returned text with disk; list re-lists the folder; find/trash re-check every path);
  - launches (apps, folders, files, URLs, search, YouTube, e-mail draft, Settings): a matching window must appear (title and/or process, Win32 enumeration, polled up to 4 s); PID liveness recorded;
  - `manage_system_setting`: independent re-read in a separate worker process — volume value **and mute state** (pycaw; `get` now reports `muted`), brightness via WMI first (`screen_brightness_control` method `wmi`, then default for external monitors), Bluetooth/Wi-Fi state, theme from the HKCU registry; compared with what was requested;
  - `control_browser`: extension confirmation + re-inspect (closed tab is gone; paused/resumed tab still open); `application_package`: downloaded files exist with hashes;
  - screenshot: PNG header + hash; download: size + hash.
  - `app/verification.py` holds the probes; tests use `ScriptedProbe` (strict) or `AgreeableProbe` (session default for unrelated tests).
- **Worker process with kill-on-timeout** (`app/orchestration/worker.py`). Each action runs execute + verify in a pooled `spawn` child process. On timeout / pause / cancel the parent kills the worker and every process that job started (e.g. a hung winget); apps started by earlier jobs survive. A worker that died while idle is replaced and the job is sent once more only if it was never delivered; a started job is never repeated. Per-tool `timeout_seconds` (WinGet 250 s, download 180 s, default `TOOL_TIMEOUT_SECONDS`=30). `TOOL_ISOLATION=process` (default) / `thread` (tests). `control_browser` and `open_url` stay in-process (`isolation="thread"`) because the extension socket lives in the server process. One worker is warmed at startup; `multiprocessing.freeze_support()` added for the exe.
- **Window control finished.** New `focus_window` (bring to front, low risk). Every window step records `hwnd`/`pid`; verify re-reads the window: close = gone, minimize = iconic, maximize = zoomed, restore = neither, focus = foreground, close-tab = title changed or window gone, input = target window existed. If a close leaves the window open, pywinauto UIA reads the app's dialog; an unsaved-changes prompt is reported (`needs_confirmation`) and never answered for the user. NLU routes "minimize chrome", "notepad chota karo", "switch to excel", "کروم چھوٹا کرو" (intent `window_control`).
- **Pause/resume from the last verified checkpoint.** After each verified step the task stores a checkpoint. Resume first re-runs that step's verifier on the real PC; if it no longer holds (e.g. the created folder was deleted), resume is refused with the reason and nothing else runs. Pause stops an *interruptible* running step immediately (download, WinGet, read-only tools); other steps finish first. New statuses: action `paused`; task `partially_completed` (some steps verified, a later one failed — rule 4). Frontend and voice replies updated.
- **Download pause/resume/cancel.** `download_web_content` writes to a fixed `.agentdesk.part` file plus a small JSON sidecar (URL, ETag/Last-Modified). Pause kills the worker and keeps the part; Resume sends `Range` + `If-Range` and appends on `206`, restarts cleanly on `200` (server ignored Range or file changed). Cancel kills the worker and deletes the part and sidecar. Network interruptions keep the part for Resume; size/type errors delete it.
- Dependencies: `pywinauto==0.6.9` (+ `six`) for Windows only, in `requirements.txt` and the lock (not yet installed on the Windows PC).

**Tested how (Linux container, Python 3.13)**
- `python -m pytest tests -q` -> **638 passed, 3 skipped, 0 failed** (skips: Groq live tests).
- New `tests/test_phase16_worker.py` (7, real processes, no mocks): runs in another process; **timeout kills the worker and the grandchild it started, and the tool's "finished" marker never appears**; cancel via stop request; earlier apps survive a later kill; crash reported; dead idle worker replaced; killed worker never reused.
- New `tests/test_phase16_download_control.py` (5, real local HTTP server with Range, real worker processes, real executor): pause keeps the part and resume continues with `Range` to an identical SHA-256; server without Range restarts cleanly; executor pause -> resume -> completed; executor cancel deletes the part; **a timed-out task is FAILED with "Stopped after 2s" and its child process is dead**.
- New `tests/test_phase16_verification.py` (26, strict probe): every tool has a verifier, default verifier never passes, executor rejects a pass without evidence, launch needs the real window, settings read-back mismatches fail (volume, mute, Bluetooth, theme), window states re-read, closed window must be gone, save prompt detected, changed disk detected, resume refused / continued correctly.
- 6 older tests changed because they asserted success without real evidence (now they supply observed state) or expected `failed` where rule 4 now gives `partially_completed`.
- `npm run build` pass; `node --test voice.test.mjs` 4/4.
- `scripts/live_check.py --gui --network` -> 29 tools reported, 15 PASS (all with evidence), 0 FAIL, 14 BLOCKED (Linux: no Windows desktop/extension/DNS). `scripts/eval_nlu.py` -> Gate G3 still PASS (99.3% / 99.3%, 0 invented, 0 bypasses).

**What is left (must be done on the Windows PC)**
- Run `python scripts/live_check.py --gui --network` there: the Win32 window enumeration, pywinauto dialog reading, WMI brightness and the worker process under Windows `spawn` are unit-tested only.
- Measure the worker start-up cost and first-action latency on Windows (target local < 2.5 s); the pool warms one worker at startup.
- PyInstaller exe: confirm spawn workers work in the frozen build (`freeze_support` added; `agentdesk.spec` may need hidden imports for `app.orchestration.worker` and `pywinauto`).
- Download progress (bytes so far) is not shown in the UI yet.

**Known issues / limits**
- Launch verification proves a matching window appeared, not that the page/app finished loading; the Settings check confirms the window, not the sub-page.
- Free input (click/type/keys) cannot be read back in general; evidence is that the target window existed and had focus.
- A window title that never contains the app/site name (some apps) will fail verification -> task fails honestly instead of claiming success.
- Non-interruptible steps (file moves, settings) are not killed on pause/cancel, to avoid half-applied changes; they finish first.

### 2026-10-31 — Session 3: Phase 15 NATURAL-LANGUAGE COMMAND MODEL

Phase 14 was skipped as a separate step; its planner pieces (PlannerProvider, Groq structured outputs, fallback chain) are delivered here.

**Done** (package lives in `backend/app/nlu/`, next to `app/ai/`, so it imports like the rest of the app)
- `schema.py`: plan schema v2 (`NLUPlan`: intent enum, language, confidence, needs_clarification, clarifying_question, steps[tool,args,depends_on,expected_outcome], reply). `plan_json_schema(registry)` builds the strict JSON schema: one step variant per registered tool (tool enum generated from the registry), every property required, `additionalProperties:false` everywhere, optional values nullable, unsupported keywords stripped. `validate_plan()` = the single server-side gate for every provider: unknown tool, unknown/invalid argument (incl. `auto_approve`), bad `depends_on`, bad `$N` step reference, path outside allowed roots -> rejected with a reason; risk from `tool.risk_for_args`.
- `normalizer.py`: language detection (script + Roman Urdu markers), number words in en/roman_ur/ur/hi -> digits, shared multilingual lexicon -> canonical tokens, rapidfuzz typo correction (ASCII only, cutoff 86, never on paths/URLs/e-mails), nukta/NFKC handling.
- `local_matcher.py`: ordered rules over canonical tokens (unsupported, chat, e-mail, settings pages, device settings, follow-ups, browser/media/search, websites, screenshot/trash/packages, explicit paths, file ops with find_paths + `$0`, apps, clarification). Confidence 0.95 minus penalties for typos and unexplained words; unknown -> 0. Negated commands are never matched.
- `memory.py`: last 10 turns, last results, `last_target` only from the immediately preceding successful turn, ordinals into the last result list, JSON prompt view.
- `planner.py`: `PlannerProvider.plan(context) -> PlanResult`; `GroqPlanner` (one call, `response_format` json_schema strict, no tools/streaming, gpt-oss-120b -> gpt-oss-20b, via `GroqClient`), `OpenAICompatiblePlanner` (OpenAI; Gemini via its OpenAI endpoint) with the same schema and validator, `FallbackPlanner`, `LocalFirstPlanner` (Groq only below 0.85), usage per plan, `planner_from_settings()`.
- `prompts/system.md` (role, rules 1-8, conventions, tool catalogue) + `prompts/examples.jsonl` (11 few-shot plans: en, roman_ur, ur, hi, multi-step, follow-up, ordinal, ambiguous -> question, unsupported, adversarial "don't ask"). None of them is in the eval set (test enforces it).
- `tests/nlu_eval/commands.jsonl` (300: en 92, roman_ur 88, ur 60, hi 60; dev 201 / holdout 99), committed before the matcher. `tests/nlu_eval/paraphrases.jsonl` (40 independent probes).
- `scripts/eval_nlu.py`: modes local / pipeline / llm; accuracy per language and split, invented tools, approval bypasses (approval tier of the predicted plan lower than expected for the same intent, or approval-like argument), language detection, paraphrase probe; writes `docs/eval/nlu_<mode>_<split>.md/.json`.
- `rapidfuzz` added to `requirements.txt` and `requirements.lock.txt` (3.14.6; not yet installed/verified on the Windows PC).

**Tested how (Linux container, Python 3.13)**
- `python -m pytest tests -q` -> **595 passed, 3 skipped, 0 failed** (skips = Groq live tests, no key). New `tests/test_nlu.py`: 43 tests (strict-schema rules on every object, validator rejections, path check, normaliser, matcher, adversarial delete stays HIGH risk, memory, planners with fake Groq/Gemini clients incl. 120b->20b fallback and backup provider, prompt contents, dataset validity, **Gate G3 as a regression test**).
- `python scripts/eval_nlu.py` -> **Gate G3 PASS**: intent+tool 99.3%, exact args 99.3%, 0 invented tools, 0 approval bypasses (holdout first run 100.0% / 99.0%). Paraphrase probe 20/40 correct, 20 deferred, **0 confidently wrong**. Report: `docs/eval/nlu_local_all.md`, caveats in `docs/eval/README.md`.

**What is left**
- Run `python scripts/eval_nlu.py --mode pipeline` with `GROQ_API_KEY` (verifies Groq accepts the strict schema, measures LLM accuracy and latency).
- **Not wired into the executor yet.** `start_task` still uses the existing local agent + Groq tool-calling loop. Wiring `LocalFirstPlanner` in (resolving `$0` via find_paths, feeding memory from task history) changes the live command path — needs your go-ahead (see open decision 1).
- Real-user transcripts as a third eval split.

**Known issues**
- Same author for data and matcher: in-set numbers are optimistic; the probe shows ~50% local coverage of unseen phrasing.
- Groq strict-schema compatibility (anyOf of 29 variants, `type: [x, "null"]`) is unverified live.

### 2026-10-31 — Session 2: Phase 13 STABILISE

**Done**
- `backend/app/ai/groq_client.py` — `GroqClient`: one client for chat, STT (`whisper-large-v3-turbo` -> `whisper-large-v3`) and TTS (`canopylabs/orpheus-v1-english`, WAV).
  Explicit per-request timeout; 429 / 5xx / connection errors retried with exponential backoff (1 s, 2 s, +10% jitter) honouring `Retry-After`, max 2 retries; a Retry-After above 15 s is not waited out (raises `AIRateLimitError`). Timeouts, auth (401/403) and unknown model (404) are never retried. Thread-safe `UsageCounter` per kind (calls, succeeded, failed, retries, rate_limited, tokens, audio seconds, TTS chars). One `groq_call` log event per attempt with kind/model/status/HTTP status/duration only — no prompt, transcript, spoken text or key; error text goes through `redact`. STT passes `language` and `prompt` (known names). `get_groq_client()` gives one shared instance.
  Note: the brief said `backend/ai/`; the package is `backend/app/ai/`, so the file lives there.
- New settings: `GROQ_STT_MODEL`, `GROQ_STT_FALLBACK_MODEL`, `GROQ_TTS_MODEL`, `GROQ_TTS_VOICE` (`config.py`, `.env.example`).
- `backend/scripts/live_check.py` — runs every registered tool once (29 tools) in a temp sandbox that is the only allowed/discovery directory, calls `validate_args -> execute -> verify`, then an **independent** check of real state (disk content, zip members, trash records, PNG header, PID alive...). PASS only when tool, `verify()` and the independent check all agree. Categories: `sandbox` + `read` (default), `gui` (`--gui`), `network` (`--network`). Never runs setters, clicks/typing, tab closing or WinGet download. Statuses PASS / FAIL / BLOCKED (missing prerequisite) / SKIP. Exit 1 on any FAIL or on a registered tool without a probe.
- Existing `GroqProvider` not yet switched to `GroqClient` (Phase 14 builds the planner on it).

**Tested how (Linux cloud container, Python 3.13, Node 22)**
- Backend: `python -m pytest tests -q` -> **552 passed, 3 skipped, 0 failed**. Skips: 2 new live Groq smoke tests + old opt-in Groq test (no `GROQ_API_KEY` here).
- New: `tests/test_groq_client.py` (16, fake SDK: backoff timings, Retry-After, long Retry-After, 5xx retry, no retry on auth/timeout, real `groq.RateLimitError`, STT language/prompt + fallback, TTS, usage, log redaction), `tests/test_groq_client_live.py` (chat + TTS->STT round trip, skipped without key), `tests/test_live_check.py` (4: probe for every tool, no dangerous probe, sandbox probes really pass, missing probe = FAIL).
- Frontend: `npm ci && npm run build` (tsc + vite) -> pass. `node --test voice.test.mjs` -> 4/4. Extension: `test_controls.cjs`, `test_pairing.cjs` -> pass.
- `python scripts/live_check.py --gui --network` -> **29 tools reported: 15 PASS, 0 FAIL, 14 BLOCKED, 0 SKIP**. All 15 file tools PASS with disk evidence. BLOCKED because this is headless Linux: system setting read, desktop inspect, WinGet, Settings, all GUI launches; browser extension not connected to the script process; no direct DNS for the download.

**What is left**
- Run `python scripts/live_check.py --gui --network` on the Windows PC and paste the table here (the 14 BLOCKED rows need it).
- Run the live smoke test with a real key: `$env:GROQ_API_KEY=...; python -m pytest tests/test_groq_client_live.py -v`.
- `control_browser` probe can only reach the extension through the running server; add an `--api` mode later.

**Known issues**
- GUI probes verify "launch requested / PID alive", not the visible window (Phase 17 verifiers).
- Probe timeout uses a thread that cannot be killed (same limit as R-08; Phase 17 worker process).

### 2026-10-31 — Session 1: baseline and plan (no code changed)

**Done**
- Read README, feature audit, traceability matrix (R-01..R-33), final acceptance report, phase 0-12 reports, `ai/` and `tools/` layout.
- Created this file and the phase plan below.

**Tested how**
- Full backend suite on Linux (Python 3.13, cloud container, no Windows SDKs):
  `python -m pytest tests -q` -> **529 passed, 4 skipped, 0 failed** (skips: live Groq + Windows-SDK tests).
  Windows reference (2026-10-28, Python 3.12, pinned deps): 532 passed, 1 skipped.

**Known issues / decisions needed** — see "Open decisions" below.

## Baseline vs. target architecture

| Area | Today (in repo) | Target (product brief) | Gap |
|---|---|---|---|
| Plan model | `ai/base.py` `Plan{actions[{tool,args,rationale}], note}` | `Plan{intent, language, confidence, needs_clarification, clarifying_question, steps[{tool,args,depends_on,expected_outcome}], reply}` | New schema + adapter so executor keeps working |
| Provider interface | `AIProvider.generate_plan(command, tool_descriptions)` | `PlannerProvider.plan(context) -> Plan` | Context object (desktop, memory, profile) |
| Groq | Multi-turn **tool-calling** agent loop (`groq_provider.py`, `executor.py`) | **Single** call, `response_format=json_schema, strict=true`, `gpt-oss-120b` -> `gpt-oss-20b` -> Gemini/OpenAI | Rewrite of the call path; agent loop retired or kept behind flag |
| Tool enum | Registry validates names after the fact | Enum generated from registry at startup inside the JSON schema | Schema builder |
| Local fast path | Regex/normaliser per domain (`speech_commands`, `command_language`, ...) | Normaliser + rapidfuzz, confidence score, Groq only if < 0.85 | Confidence scoring, rapidfuzz |
| Memory | Recent-task memory, local workflow state | Last 10 turns, resolved references, opt-in profile | New conversation store |
| Verification | Files verified on disk; launches/system/desktop trust own result | `verify() -> bool + evidence` on every tool; completed only if verified | Verifiers for apps, system, desktop, browser |
| Timeouts | Thread pool; cannot kill | Worker process killable on timeout | Process-based runner |
| Approvals | Hash-bound, single use, expiry; bare "yes" approves on-screen action | Medium = preview, voice yes OK; High = click required; 120 s expiry | Channel-aware approval (voice vs click) |
| Voice | Browser Web Speech + browser TTS | sounddevice + Silero VAD + Groq Whisper; Orpheus / edge-tts / SAPI; barge-in; WS state push | New backend voice service |
| Retries | Provider cooldown only | Max 2 automatic retries, never destructive without re-check | Retry policy in executor |
| Metrics | Load check script | Latency targets + 300-command NLU test set | Eval harness + dataset |

## Proposed phases (each on its own branch, small commits, full suite before "done")

| Phase | Goal | Main files | Acceptance |
|---|---|---|---|
| 13 | **Plan schema v2 + strict JSON-schema builder** from registry (tool enum, nullable optionals, `additionalProperties:false`); adapter v2 -> existing executor | `ai/base.py`, new `ai/plan_schema.py`, `tools/registry.py` | Schema validates against Groq strict rules; invented tool rejected; all existing tests green |
| 14 | **PlannerProvider + Groq structured-output planner** with fallback chain, 429 backoff, usage per plan, few-shot system prompt (8-12 examples, 4 languages) | `ai/planner_provider.py`, `ai/groq_provider.py`, `ai/resilient_provider.py`, `usage.py` | Scripted provider tests; same validator for all providers; live test opt-in |
| 15 | **Local fast path with confidence** (normaliser + rapidfuzz), Groq only < 0.85; NLU test set (300 commands) + eval script | `ai/speech_commands.py`, new `ai/local_matcher.py`, `tests/nlu/` | >= 90% intent+tool, >= 85% args, 0 invented tools on local+mock |
| 16 | **Conversation memory + references** ("close it", "the second one"), desktop context collector | new `memory.py`, `orchestration/local_agent.py`, `tools/desktop.py` | Follow-up tests in 4 languages |
| 17 | **Verify-everything + killable worker process + retry policy** | `tools/base.py`, `executor.py`, `tools/system_worker.py`, all tools | Every tool has `verify()`; timeout kills worker; no "completed" without evidence |
| 18 | **Approval channels** (voice may approve medium; high needs click; 120 s) | `security/approvals.py`, `main.py`, frontend | Bypass tests incl. voice-approving-high |
| 19 | **Backend voice loop** (hotkey, sounddevice, Silero VAD, Groq Whisper, TTS chain, barge-in, WebSocket states) | new `voice/`, `main.py`, frontend | Latency measured: local < 2.5 s p90 of 30 runs |
| 20 | **Window control + live pilot** of never-run tools on a real Windows PC | `tools/desktop.py`, `scripts/verify_windows.py` | Pilot checklist executed by a person |

WhatsApp and software install stay out of scope until 13-18 are done (rule 8: pre-fill only).

## Open decisions (need owner answer before the affected phase)

1. **Groq tool-calling agent loop** (`groq_provider.py` + `executor.py` agent path) works today. The brief requires one structured-output call. Proposal: add the new planner as default, keep the agent loop behind `AI_MODE=agent` for one release, then remove. (Phase 14)
2. **Approval semantics change**: today a bare "yes"/"haan" approves the one on-screen action at any risk level. The brief requires a click for high risk. This tightens the security model. (Phase 18)
3. **Windows-only verification**: this cloud container is Linux. Hardware, window, voice-device and latency work can be unit-tested here but must be verified on the Windows PC by a person.
4. **New dependencies**: rapidfuzz, sounddevice, silero-vad (torch or onnxruntime — ~large), edge-tts. Silero via onnxruntime is preferred to avoid torch.
