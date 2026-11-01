# U5 (terminal + browser/YouTube) checks. Run from AgentDesk_AI\ on your Windows PC:
#   powershell -ExecutionPolicy Bypass -File scripts\check_u5.ps1 [-NoLive] [-Full]
# 1. Terminal tests (every allow-list entry, every approval path, injection cases, real runs): tests\test_u5_terminal.py
# 2. Browser tests (spoken commands, the tool, DOM-based verification on a simulated extension): tests\test_u5_browser.py
# 3. Extension tests on a fake YouTube page (node): browser-extension\tests\test_youtube.cjs, test_controls.cjs, test_pairing.cjs
# 4. LIVE terminal check: a window titled "AgentDesk terminal" opens and follows nine real commands in this folder (about 10 seconds).
#    -NoLive skips it. -Full also runs the whole backend test suite.
# The YouTube controls on the REAL youtube.com need the extension loaded and paired in your Chrome / Edge: see "BY HAND" at the end.
param([switch]$NoLive, [switch]$Full)
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$py = @('.venv\Scripts\python.exe', '.venv-p3\Scripts\python.exe') | ForEach-Object { Join-Path $backend $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $py) { Write-Error 'No backend venv found (backend\.venv)'; exit 1 }
$out = Join-Path $root ("docs\eval\u5_" + (Get-Date -Format 'yyyy-MM-dd_HHmm'))
New-Item -ItemType Directory -Force $out | Out-Null
$env:PYTHONIOENCODING = 'utf-8'
$rows = @()
function Step($name, [scriptblock]$run, $log) {
  & $run *> (Join-Path $out $log)
  $code = $LASTEXITCODE
  $script:rows += [pscustomobject]@{ Check = $name; Result = $(if ($code -eq 0) { 'PASS' } else { 'FAIL' }); Log = $log }
}
Push-Location $backend
Step 'Terminal tests'                         { & $py -m pytest tests\test_u5_terminal.py -q }                                                     'pytest_terminal.txt'
Step 'Browser tests'                          { & $py -m pytest tests\test_u5_browser.py tests\test_phase18_browser.py tests\test_browser_controls.py -q } 'pytest_browser.txt'
if (-not $NoLive) { Step 'Live terminal check (9 commands)' { & $py scripts\terminal_live.py --folder $root --out (Join-Path $out 'terminal_live.json') } 'terminal_live.txt' }
if ($Full) { Step 'Whole backend test suite' { & $py -m pytest tests -q } 'pytest_all.txt' }
Pop-Location
Push-Location (Join-Path $root 'browser-extension')
Step 'Extension: YouTube page controls'       { node tests\test_youtube.cjs }                                                                      'node_youtube.txt'
Step 'Extension: tabs, pairing'               { node tests\test_controls.cjs; node tests\test_pairing.cjs }                                        'node_controls.txt'
Pop-Location
$rows | Format-Table -AutoSize | Tee-Object -FilePath (Join-Path $out 'SUMMARY.txt')
if (-not $NoLive) { Get-Content (Join-Path $out 'terminal_live.txt') | Select-Object -Last 14 }
Write-Host @"

BY HAND (app running; browser extension loaded in Chrome/Edge and paired; open http://localhost:5173 in that browser):
  1. chrome://extensions -> reload 'Agent X Browser Bridge' (or wait: it updates itself to 1.2.0 when connected). Settings should show 'Browser connected'.
  2. 'Search YouTube for Python tutorials' -> results page.  'Read the results' -> three titles.  'Play the second one' -> that video opens.
  3. 'Pause' / 'Play' / 'Skip 10 seconds' / 'Rewind 10 seconds' / 'Speed 1.5' / 'Youtube volume 30' / 'Mute the video' / 'Next video'.
  4. 'Full screen': Chrome may refuse (only a real key press may enter it); the assistant must SAY it was blocked, not claim it worked.
  5. 'Switch to Gmail tab' -> Gmail comes to the front.  'What page is this?' -> reads the title.
  6. Terminal: 'Open command prompt in my FYP folder' -> a real command prompt opens there.  'Run git status' -> the AgentDesk terminal
     window shows it, runs without asking, and the reply says e.g. 'On branch main: 3 changed.'
  7. 'Run the tests' -> asks approval (read out: "I'd run this command: pytest -q. Say yes, run it, or click Approve."); say 'yes, run it'.
  8. 'Delete the file x.txt in the terminal' / 'run rm -rf x' -> HIGH risk: a spoken yes must NOT approve it; only the Approve click.
Evidence: $out
"@
if ($rows | Where-Object Result -eq 'FAIL') { exit 1 }
