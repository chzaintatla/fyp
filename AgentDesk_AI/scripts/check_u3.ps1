# U3 (agent loop + conversation) checks. Run from AgentDesk_AI\ on your Windows PC:
#   powershell -ExecutionPolicy Bypass -File scripts\check_u3.ps1 [-Full]
# 1. The 10 scripted multi-turn conversations and the phrase tests (tests\test_u3_conversation.py).
# 2. Frontend type check and tests.
# -Full also runs the whole backend suite (about 2 minutes).
# Then do the SPOKEN conversation printed at the end with the app running (needs your voice; the scripted tests are text only).
param([switch]$Full)
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$py = @('.venv\Scripts\python.exe', '.venv-p3\Scripts\python.exe') | ForEach-Object { Join-Path $backend $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $py) { Write-Error 'No backend venv found (backend\.venv)'; exit 1 }
$out = Join-Path $root ("docs\eval\u3_" + (Get-Date -Format 'yyyy-MM-dd_HHmm'))
New-Item -ItemType Directory -Force $out | Out-Null
$rows = @()
function Step($name, [scriptblock]$run, $log) {
  & $run *> (Join-Path $out $log)
  $code = $LASTEXITCODE
  $script:rows += [pscustomobject]@{ Check = $name; Result = $(if ($code -eq 0) { 'PASS' } else { 'FAIL' }); Log = $log }
}
Push-Location $backend
Step '10 scripted conversations + phrases' { & $py -m pytest tests\test_u3_conversation.py -v }                       'pytest_u3.txt'
if ($Full) { Step 'Whole backend test suite' { & $py -m pytest tests -q } 'pytest_all.txt' }
Pop-Location
Push-Location (Join-Path $root 'frontend')
Step 'Frontend type check'                 { npx tsc --noEmit }                                                      'tsc.txt'
Step 'Frontend tests'                      { npm test }                                                              'npm_test.txt'
Pop-Location
$rows | Format-Table -AutoSize | Tee-Object -FilePath (Join-Path $out 'SUMMARY.txt')
Write-Host @"

SPOKEN CONVERSATION (open http://localhost:5173 in Chrome or Edge, press Start AI; write PASS/FAIL next to each):
  1. 'Show trash'                    -> result, then 'Anything else?', microphone opens again for about 10 s without a key press.
  2. 'Repeat that'                   -> the same sentence again.   'Read it to me' -> reads the result.
  3. 'No, that's all'  (or 'bas shukriya') -> 'Okay, I'm here if you need me.' and the microphone closes (idle).
  4. 'Create a folder called Lab in Documents' -> asks approval -> say 'yes' -> verified -> 'Anything else?' -> 'Undo that' -> 'Undone.'
  5. While the assistant is speaking a long answer, start talking: it must stop within about 0.3 s. Then say 'Stop' -> 'Stopped.'
  6. Say 'Open Notepad' and stay silent after the reply: after the follow-up window (Settings, default 10 s) the microphone closes.
  7. Settings > 'Ask Anything else?' off -> step 1 no longer asks it.
Evidence: $out
"@
if ($rows | Where-Object Result -eq 'FAIL') { exit 1 }
