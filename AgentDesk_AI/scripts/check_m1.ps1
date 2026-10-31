# M1 live check. Run from AgentDesk_AI\ on your Windows PC:  powershell -ExecutionPolicy Bypass -File scripts\check_m1.ps1
# Needs backend\.venv (or .venv-p3) and, for the live Groq rows, GROQ_API_KEY in the environment or backend\.env.
# Evidence is saved to docs\eval\m1_<date>\ ; the PASS/FAIL table is printed at the end.
param([switch]$Gui)
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$py = @('.venv\Scripts\python.exe', '.venv-p3\Scripts\python.exe') | ForEach-Object { Join-Path $backend $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $py) { Write-Error 'No backend venv found (backend\.venv). See docs\SETUP_WINDOWS.md'; exit 1 }
$out = Join-Path $root ("docs\eval\m1_" + (Get-Date -Format 'yyyy-MM-dd_HHmm'))
New-Item -ItemType Directory -Force $out | Out-Null
$rows = @()
function Step($name, [scriptblock]$run, $log) {
  Push-Location $backend
  & $run *> (Join-Path $out $log)
  $code = $LASTEXITCODE
  Pop-Location
  $status = switch ($code) { 0 { 'PASS' } 2 { 'SKIP' } default { 'FAIL' } }
  $script:rows += [pscustomobject]@{ Check = $name; Result = $status; Log = $log }
}
Step 'Automated tests (pytest)'        { & $py -m pytest tests -q }                                  'pytest.txt'
Step 'Live Groq: chat+schema+plans'    { & $py scripts\groq_smoke.py --json (Join-Path $out 'groq_smoke.json') } 'groq_smoke.txt'
Step 'NLU gate, held-out, local'       { & $py scripts\eval_nlu.py --split holdout --no-report }      'eval_local.txt'
Step 'NLU held-out, local+Groq'        { & $py scripts\eval_nlu.py --split holdout --no-report --mode pipeline } 'eval_pipeline.txt'
$tools = @('--record'); if ($Gui) { $tools += '--gui' }
Step 'All tools on this Windows PC'    { & $py scripts\live_check.py @tools }                         'live_check.txt'
$shadow = Join-Path $env:LOCALAPPDATA 'AgentDesk\pilot\shadow_plans.jsonl'
if (Test-Path $shadow) { Copy-Item $shadow $out; $rows += [pscustomobject]@{ Check = 'Shadow log copied'; Result = 'PASS'; Log = 'shadow_plans.jsonl' } }
else { $rows += [pscustomobject]@{ Check = 'Shadow log (set PLANNER_MODE=shadow, use the app, re-run)'; Result = 'SKIP'; Log = '-' } }
$rows | Format-Table -AutoSize | Tee-Object -FilePath (Join-Path $out 'SUMMARY.txt')
Write-Host "Evidence: $out"
if ($rows | Where-Object Result -eq 'FAIL') { exit 1 }
