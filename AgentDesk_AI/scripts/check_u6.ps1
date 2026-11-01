# U6 (e-mail + chat messages) checks. Run from AgentDesk_AI\ on your Windows PC:
#   powershell -ExecutionPolicy Bypass -File scripts\check_u6.ps1 [-Provider gmail -To test.mailbox@gmail.com] [-Full]
# 1. Tests: the dictation flow, voice edits, contacts, attachments, the send rule, the Gmail and Outlook REST calls and OAuth on mock transports, chat
#    messages on a fake desktop (tests\test_u6_mail.py, test_u6_providers.py, test_u6_messages.py). No real account is touched.
# 2. LIVE send to a TEST mailbox (only with -Provider and -To): backend\scripts\mail_live.py sends one marked message and looks in Sent.
#    Needs the OAuth client id in backend\.env and Settings > Email > Connect done once: see docs\EMAIL_SETUP.md. Use a TEST mailbox only.
# -Full also runs the whole backend test suite.
param([ValidateSet('gmail', 'outlook')][string]$Provider, [string]$To, [switch]$Full)
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$py = @('.venv\Scripts\python.exe', '.venv-p3\Scripts\python.exe') | ForEach-Object { Join-Path $backend $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $py) { Write-Error 'No backend venv found (backend\.venv)'; exit 1 }
$out = Join-Path $root ("docs\eval\u6_" + (Get-Date -Format 'yyyy-MM-dd_HHmm'))
New-Item -ItemType Directory -Force $out | Out-Null
$env:PYTHONIOENCODING = 'utf-8'
$rows = @()
function Step($name, [scriptblock]$run, $log) {
  & $run *> (Join-Path $out $log)
  $code = $LASTEXITCODE
  $script:rows += [pscustomobject]@{ Check = $name; Result = $(if ($code -eq 0) { 'PASS' } else { 'FAIL' }); Log = $log }
}
Push-Location $backend
Step 'E-mail, providers, OAuth, chat messages' { & $py -m pytest tests\test_u6_mail.py tests\test_u6_providers.py tests\test_u6_messages.py -q } 'pytest_u6.txt'
if ($Provider -and $To) { Step "LIVE send to $To ($Provider)" { & $py scripts\mail_live.py --provider $Provider --to $To --i-am-using-a-test-mailbox } 'live_mail.txt' }
if ($Full) { Step 'Whole backend test suite' { & $py -m pytest tests -q } 'pytest_all.txt' }
Pop-Location
Push-Location (Join-Path $root 'frontend')
Step 'Frontend type check' { npx tsc --noEmit } 'tsc.txt'
Pop-Location
$rows | Format-Table -AutoSize | Tee-Object -FilePath (Join-Path $out 'SUMMARY.txt')
if ($Provider -and $To) { Get-Content (Join-Path $out 'live_mail.txt') }
Write-Host @"

BY HAND (app running, http://localhost:5173 in Chrome or Edge; Settings > Email connected to a TEST account):
  1. 'Write an email to <a contact of the test account>' -> 'What is the subject?' -> speak a subject -> 'Go ahead with the message.'
  2. Say two sentences; watch the 'Email draft' card fill in. Say 'read it back', 'delete the last sentence', 'change tomorrow to Monday'.
  3. Say 'make it more formal' -> it reads the new version -> say 'no, keep my original' (needs cloud answers on).
  4. 'attach <a small file name>' -> it says the file name and size -> 'yes'.
  5. 'stop dictation' -> 'send it' -> it must read recipient, subject and first line -> say just 'yes' (it must NOT send) -> say 'yes, send it'.
  6. Expect 'Email sent to <name>.' and check the Sent folder of the test mailbox yourself.
  7. With two contacts called Ali: 'Write an email to Ali' -> it must ask which one.
  8. WhatsApp Desktop (if installed): save 'Test: +<your number>' on the Memory page, then 'In WhatsApp, message Test hello' -> read-back -> 'yes, send it'.
  Silence for 15 seconds while dictating closes the conversation; 'continue my email' resumes the draft.
Evidence: $out
"@
if ($rows | Where-Object Result -eq 'FAIL') { exit 1 }
