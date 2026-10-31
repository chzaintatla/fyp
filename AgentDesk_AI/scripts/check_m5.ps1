# M5 live check. Run from AgentDesk_AI\ on your Windows PC:  powershell -ExecutionPolicy Bypass -File scripts\check_m5.ps1
# Needs backend\.env with GROQ_API_KEYS (or CLOUD_BRAIN_URL + CLOUD_BRAIN_TOKEN) and internet for the live rows (about 12 minutes).
# Evidence goes to docs\eval\m5_<date>\ . The 20 chat + 20 web questions are graded by keywords: read the answers in conversation_eval.txt too.
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
$py = @('.venv\Scripts\python.exe', '.venv-p3\Scripts\python.exe') | ForEach-Object { Join-Path $backend $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $py) { Write-Error 'No backend venv found (backend\.venv)'; exit 1 }
$env:AGENTDESK_PYTHON = $py
$out = Join-Path $root ("docs\eval\m5_" + (Get-Date -Format 'yyyy-MM-dd_HHmm'))
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
Step 'Backend tests (pytest)'                         { & $py -m pytest tests -q }                                                          'pytest.txt' $backend
Step 'Frontend unit tests + build'                    { npm test; if ($LASTEXITCODE -eq 0) { npm run build } }                                'frontend.txt' $frontend
Step 'Browser tests (Edge)'                           { npm run test:e2e }                                                                    'e2e.txt' $frontend
Step 'Memory tools on the real database (self-clean)' { & $py scripts\live_check.py --memory --only memory_remember memory_recall memory_forget } 'live_memory.txt' $backend
Step 'Web answer + summaries on this PC'              { & $py scripts\live_check.py --network --only web_answer summarise_page summarise_file } 'live_knowledge.txt' $backend
Step 'Live: 20 chat + 20 web questions + 3 memory'    { & $py scripts\eval_conversation.py --json (Join-Path $out 'conversation_eval.json') } 'conversation_eval.txt' $backend
$rows | Format-Table -AutoSize | Tee-Object -FilePath (Join-Path $out 'SUMMARY.txt')
Write-Host "Evidence: $out"
Write-Host "Now do the human checks in docs\TEST_SCRIPT.md (section M5)."
if ($rows | Where-Object Result -eq 'FAIL') { exit 1 }
