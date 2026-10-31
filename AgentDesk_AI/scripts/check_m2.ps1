# M2 live check. Run from AgentDesk_AI\ on your Windows PC:  powershell -ExecutionPolicy Bypass -File scripts\check_m2.ps1
# Evidence goes to docs\eval\m2_<date>\ ; a PASS/FAIL table is printed at the end.
# Listen to the files in <evidence>\audio\ : each voice (male/female, English/Urdu/Hindi) should sound right.
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$py = @('.venv\Scripts\python.exe', '.venv-p3\Scripts\python.exe') | ForEach-Object { Join-Path $backend $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $py) { Write-Error 'No backend venv found (backend\.venv)'; exit 1 }
$out = Join-Path $root ("docs\eval\m2_" + (Get-Date -Format 'yyyy-MM-dd_HHmm'))
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
Step 'Backend tests (pytest)'              { & $py -m pytest tests -q }                                         'pytest.txt' $backend
Step 'Frontend unit tests + build'         { npm test; if ($LASTEXITCODE -eq 0) { npm run build } }            'frontend.txt' (Join-Path $root 'frontend')
Step 'Speech engines (listen to audio\)'   { & $py scripts\tts_probe.py --out (Join-Path $out 'audio') }       'tts_probe.txt' $backend
Step 'Latency: 30 commands, to first audio' { & $py scripts\voice_bench.py --runs 30 }                          'voice_bench.txt' $backend
Step 'Latency + Whisper accuracy (30 runs)' { & $py scripts\voice_bench.py --runs 30 --stt }                    'voice_bench_stt.txt' $backend
$rows | Format-Table -AutoSize | Tee-Object -FilePath (Join-Path $out 'SUMMARY.txt')
Write-Host "Evidence: $out"
Write-Host "Now do the human checks in docs\TEST_SCRIPT.md (section M2): microphone, spoken results, barge-in."
if ($rows | Where-Object Result -eq 'FAIL') { exit 1 }
