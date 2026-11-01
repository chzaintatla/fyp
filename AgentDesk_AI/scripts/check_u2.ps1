# U2 (speed) checks. Run from AgentDesk_AI\ on your Windows PC:
#   powershell -ExecutionPolicy Bypass -File scripts\check_u2.ps1 [-Full] [-NoStt]
# 1. U2 unit tests (latency log + budgets, model routing, early acknowledgement, warm connection).
# 2. Fast-path coverage: how many gold commands the local (no network) path answers, and in how many ms.
# 3. 30-run pipeline measurement against a sandboxed copy of the app: Whisper (needs your Groq key), acknowledgement, text-to-speech,
#    plan, execute + verify, closing line. Apps are NOT launched. Takes about 3 minutes (paced for the free tier).
# 4. Your REAL use: per-stage numbers from the app's own log (empty until you have used the app with the new build).
# -Full also runs the whole backend test suite (about 2.5 minutes). -NoStt skips the Whisper part (no Groq key / no network).
param([switch]$Full, [switch]$NoStt)
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$py = @('.venv\Scripts\python.exe', '.venv-p3\Scripts\python.exe') | ForEach-Object { Join-Path $backend $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $py) { Write-Error 'No backend venv found (backend\.venv)'; exit 1 }
$out = Join-Path $root ("docs\eval\u2_" + (Get-Date -Format 'yyyy-MM-dd_HHmm'))
New-Item -ItemType Directory -Force $out | Out-Null
Push-Location $backend
$rows = @()
function Step($name, [scriptblock]$run, $log) {
  & $run *> (Join-Path $out $log)
  $code = $LASTEXITCODE
  $script:rows += [pscustomobject]@{ Check = $name; Result = $(if ($code -eq 0) { 'PASS' } else { 'FAIL' }); Log = $log }
}
$stt = if ($NoStt) { '--no-stt' } else { '' }
Step 'U2 unit tests'                    { & $py -m pytest tests\test_u2_speed.py -q }                                                              'pytest_u2.txt'
Step 'Fast-path coverage'               { & $py scripts\fastpath_coverage.py --out (Join-Path $out 'fastpath.json') }                              'fastpath.txt'
Step '30-run pipeline measurement'      { & $py scripts\latency_u2.py --runs 30 $stt --out (Join-Path $out 'latency_u2.json') }                    'latency_u2.txt'
Step 'Your real use (app log)'          { & $py scripts\latency_report.py }                                                                        'real_use.txt'
if ($Full) { Step 'Whole backend test suite' { & $py -m pytest tests -q } 'pytest_all.txt' }
Pop-Location
$rows | Format-Table -AutoSize | Tee-Object -FilePath (Join-Path $out 'SUMMARY.txt')
Get-Content (Join-Path $out 'latency_u2.txt') | Select-Object -First 14
Get-Content (Join-Path $out 'real_use.txt')
Write-Host @"

NOTE: 'Your real use' exits FAIL when a stage misses its budget: that is information, not a broken install. Read the MISS rows.
LIVE CHECKS (open http://localhost:5173 in Chrome or Edge, not the built-in preview: it blocks the microphone):
  1. Say 'Open Notepad' / 'Show trash' / 'Downloads folder kholo': you should hear 'Opening Notepad.' BEFORE the result line.
  2. Say 'Delete <a test file>': NO early 'Okay'; the approval question comes after the plan.
  3. Speak a sentence with a short pause in the middle ('Open ... Chrome'): if it is cut in two, tell me (the silence that ends an
     utterance is now 450 ms, was 650 ms; the hold before sending is now 120 ms, was 500 ms).
  4. After 10 commands run:  python backend\scripts\latency_report.py   and send me the table.
Evidence: $out
"@
if ($rows | Where-Object Result -eq 'FAIL') { exit 1 }
