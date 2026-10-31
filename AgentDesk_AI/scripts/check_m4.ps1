# M4 live check. Run from AgentDesk_AI\ on your Windows PC:  powershell -ExecutionPolicy Bypass -File scripts\check_m4.ps1
# Needs Microsoft Edge (the browser tests use it) and backend\.venv or .venv-p3.
# Evidence goes to docs\eval\m4_<date>\ ; screenshots of every page at 100% and 150% scaling go to docs\ui\.
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
$py = @('.venv\Scripts\python.exe', '.venv-p3\Scripts\python.exe') | ForEach-Object { Join-Path $backend $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $py) { Write-Error 'No backend venv found (backend\.venv)'; exit 1 }
$env:AGENTDESK_PYTHON = $py
$out = Join-Path $root ("docs\eval\m4_" + (Get-Date -Format 'yyyy-MM-dd_HHmm'))
New-Item -ItemType Directory -Force $out | Out-Null
$rows = @()
function Step($name, [scriptblock]$run, $log, $dir) {
  Push-Location $dir
  & $run *> (Join-Path $out $log)
  $code = $LASTEXITCODE
  Pop-Location
  $status = switch ($code) { 0 { 'PASS' } 2 { 'SKIP' } default { 'FAIL' } }
  $script:rows += [pscustomobject]@{ Check = $name; Result = $status; Log = $log }
}
Step 'Backend tests (pytest)'                      { & $py -m pytest tests -q }                                  'pytest.txt' $backend
Step 'Frontend unit tests (incl. 4.5:1 contrast)'  { npm test }                                                  'unit.txt' $frontend
Step 'Frontend build'                              { npm run build }                                             'build.txt' $frontend
Step 'Browser tests (20: flows, UI, visual)'       { npm run test:e2e }                                          'e2e.txt' $frontend
Step 'Screenshots 100% + 150%, no sideways scroll' { node e2e/ui_screens.mjs }                                   'screens.txt' $frontend
$rows | Format-Table -AutoSize | Tee-Object -FilePath (Join-Path $out 'SUMMARY.txt')
Write-Host "Evidence: $out   Screenshots: $(Join-Path $root 'docs\ui')"
Write-Host "Now do the human checks in docs\TEST_SCRIPT.md (section M4), including Windows display scaling 100/125/150%."
if ($rows | Where-Object Result -eq 'FAIL') { exit 1 }
