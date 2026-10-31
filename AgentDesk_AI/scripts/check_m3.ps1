# M3 live check. Run from AgentDesk_AI\ on your Windows PC:  powershell -ExecutionPolicy Bypass -File scripts\check_m3.ps1
# Evidence goes to docs\eval\m3_<date>\ . Listen to <evidence>\audio\ : male and female voices must sound male and female.
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$py = @('.venv\Scripts\python.exe', '.venv-p3\Scripts\python.exe') | ForEach-Object { Join-Path $backend $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $py) { Write-Error 'No backend venv found (backend\.venv)'; exit 1 }
$out = Join-Path $root ("docs\eval\m3_" + (Get-Date -Format 'yyyy-MM-dd_HHmm'))
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
Step 'Backend tests (pytest, all)'                  { & $py -m pytest tests -q }                                   'pytest.txt' $backend
Step 'Same plan under every personality (rule 3.9)' { & $py -m pytest tests/test_m3_personas.py -q }               'rule_3_9.txt' $backend
Step 'Frontend unit tests + build'                  { npm test; if ($LASTEXITCODE -eq 0) { npm run build } }      'frontend.txt' (Join-Path $root 'frontend')
Step 'Voices: male + female, EN/UR/HI (listen)'     { & $py scripts\tts_probe.py --out (Join-Path $out 'audio') }  'tts_probe.txt' $backend
$rows | Format-Table -AutoSize | Tee-Object -FilePath (Join-Path $out 'SUMMARY.txt')
Write-Host "Evidence: $out"
Write-Host "Now do the human checks in docs\TEST_SCRIPT.md (section M3)."
if ($rows | Where-Object Result -eq 'FAIL') { exit 1 }
