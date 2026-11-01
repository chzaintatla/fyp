# Step 1g live checks. Run from AgentDesk_AI\ on your Windows PC with the app already running (START_AGENT_X.cmd):
#   powershell -ExecutionPolicy Bypass -File scripts\check_1g.ps1 [-WhatsAppPhone +923001234567] [-InstallId Notepad++.Notepad++] [-Shadow]
# 1. control_browser through the RUNNING app. Needs the Chrome/Edge extension paired (app > "Chrome/Edge setup kholein").
#    Runs the read-only command "list tabs"; it shows up in the task history.
# 2. whatsapp_open_chat: opens WhatsApp Desktop with a test message pre-filled for YOUR OWN number. Nothing is sent: do not press Send.
# 3. winget_install: REALLY installs the package you name (high risk: pick a small tool you want). Skipped unless -InstallId is given.
# -Shadow also replays 200 gold commands old-vs-new (slow, spends API quota; see docs\STATUS.md Session 17 before using it).
param([string]$WhatsAppPhone, [string]$InstallId, [switch]$Shadow)
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$py = @('.venv\Scripts\python.exe', '.venv-p3\Scripts\python.exe') | ForEach-Object { Join-Path $backend $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $py) { Write-Error 'No backend venv found (backend\.venv)'; exit 1 }
$out = Join-Path $root ("docs\eval\1g_" + (Get-Date -Format 'yyyy-MM-dd_HHmm'))
New-Item -ItemType Directory -Force $out | Out-Null
Push-Location $backend
$rows = @()
function Step($name, [scriptblock]$run, $log) {
  & $run *> (Join-Path $out $log)
  $code = $LASTEXITCODE
  $status = if ($code -ne 0) { 'FAIL' } elseif (Select-String -Path (Join-Path $out $log) -Pattern '\[BLOCKED' -Quiet) { 'BLOCKED' } elseif (Select-String -Path (Join-Path $out $log) -Pattern '\[SKIP' -Quiet) { 'SKIP' } else { 'PASS' }
  $script:rows += [pscustomobject]@{ Check = $name; Result = $status; Log = $log }
}
Step 'Backend tests (pytest)'                      { & $py -m pytest tests -q }                                                            'pytest.txt'
Step 'control_browser through the running app'     { & $py scripts\live_check.py --api --only control_browser --record }                  'live_browser.txt'
if ($WhatsAppPhone) { Step 'whatsapp_open_chat (draft only, not sent)' { & $py scripts\live_check.py --whatsapp-phone $WhatsAppPhone --only whatsapp_open_chat --record } 'live_whatsapp.txt' }
if ($InstallId)     { Step "winget_install $InstallId (real install)"  { & $py scripts\live_check.py --install-id $InstallId --only winget_install --record }               'live_winget.txt' }
if ($Shadow)        { Step 'Shadow replay, 200 commands (slow)'         { & $py scripts\shadow_replay.py --limit 200 --out (Join-Path $root 'docs\eval\1g_shadow') }                   'shadow.txt' }
Pop-Location
$rows | Format-Table -AutoSize | Tee-Object -FilePath (Join-Path $out 'SUMMARY.txt')
Write-Host "Evidence: $out  (recorded runs: docs\live_runs)"
Write-Host "BLOCKED in a log is not a pass: it means the prerequisite (paired extension, WhatsApp Desktop, WinGet) was missing."
if ($rows | Where-Object Result -eq 'FAIL') { exit 1 }
