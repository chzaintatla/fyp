# U1 (understanding) checks. Run from AgentDesk_AI\ on your Windows PC:
#   powershell -ExecutionPolicy Bypass -File scripts\check_u1.ps1 [-Full]
# 1. U1 unit tests (index, sound-alike matching, thresholds, corrections, Whisper hint, planner hint).
# 2. The 218-command text evaluation (dev + holdout) -> docs\eval\u1_<time>\u1_eval.json.
# 3. The REAL index of this PC: counts, build time per source, sample misheard apps, timing. Prints counts and app names only.
# -Full also runs the whole backend test suite (about 2.5 minutes).
# Then do the SPOKEN checks printed at the end with the app running: that part needs your voice and cannot be scripted.
param([switch]$Full)
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$py = @('.venv\Scripts\python.exe', '.venv-p3\Scripts\python.exe') | ForEach-Object { Join-Path $backend $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $py) { Write-Error 'No backend venv found (backend\.venv)'; exit 1 }
$out = Join-Path $root ("docs\eval\u1_" + (Get-Date -Format 'yyyy-MM-dd_HHmm'))
New-Item -ItemType Directory -Force $out | Out-Null
Push-Location $backend
$rows = @()
function Step($name, [scriptblock]$run, $log) {
  & $run *> (Join-Path $out $log)
  $code = $LASTEXITCODE
  $script:rows += [pscustomobject]@{ Check = $name; Result = $(if ($code -eq 0) { 'PASS' } else { 'FAIL' }); Log = $log }
}
Step 'U1 unit tests'                       { & $py -m pytest tests\test_u1_entity.py -q }                                    'pytest_u1.txt'
Step 'Text evaluation (dev + holdout)'     { & $py scripts\eval_u1.py --holdout --out (Join-Path $out 'u1_eval.json') }      'eval.txt'
Step 'Real index on this PC'               { & $py scripts\u1_live.py }                                                      'live_index.txt'
if ($Full) { Step 'Whole backend test suite' { & $py -m pytest tests -q } 'pytest_all.txt' }
Pop-Location
$rows | Format-Table -AutoSize | Tee-Object -FilePath (Join-Path $out 'SUMMARY.txt')
Get-Content (Join-Path $out 'eval.txt') | Select-Object -First 12
Get-Content (Join-Path $out 'live_index.txt')
Write-Host @"

SPOKEN CHECKS (app running; say each with your normal accent, then write PASS/FAIL next to it):
  1. 'Open fire fox'                -> Firefox opens (or asks, if two names are close).
  2. 'Open note pad'                -> Notepad opens.
  3. 'Open vs cold'                 -> Visual Studio Code, or asks 'VS Code or Visual Studio?'.
  4. 'Open the fee wipe report'     -> asks which FYP report, if you have several; opens it if one.
  5. 'No, I said Firefox' right after a wrong guess -> opens Firefox; Memory page shows 'heard => Firefox' (kind alias).
  6. Say 'Open <one of your own app names>' 10 times with an accent you really use; count how many open the right app.
  7. 'Delete <a test file>' mis-said on purpose -> it must NOT act; it asks or shows the real name and needs your click.
Evidence: $out
"@
if ($rows | Where-Object Result -eq 'FAIL') { exit 1 }
