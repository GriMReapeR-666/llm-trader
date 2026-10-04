"""Rebuild the PU-0129/0133 bat harness.

v2 fixes vs v1:
- stub: no %ERRORLEVEL% expansion inside parenthesized blocks (parse-time
  expansion discarded the probe's rc); all checks are dynamic
  `if errorlevel N` lines.
- run.bat: DECK scenarios redirect `<nul` instead of `echo.|` pipes (pipe +
  call propagates rc unreliably); progress markers around the TRADING call.
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
H = ROOT / "artifacts" / "bat_harness"
(H / "bin").mkdir(parents=True, exist_ok=True)

real_py = subprocess.run(
    ["python", "-c", "import sys; print(sys.executable)"],
    capture_output=True, text=True, check=True,
).stdout.strip()
proj = str(ROOT) + "\\"

stub = f"""@echo off
set "ARGS=%*"
echo %ARGS% | findstr /C:"ctx_probe.py" >nul
if errorlevel 1 (
    echo [STUB-PYTHON] %ARGS%
    if defined CTX_PROBE_PY_FAIL exit /b 3
    exit /b 0
)
echo [STUB-PROBE] real probe run
if defined CTX_PROBE_FORCE_FAIL set "LLM_TRADER_COMPACT_CONTEXT=0"
"{real_py}" -X utf8 "{proj}artifacts\\ctx_probe.py"
if errorlevel 1 exit /b 1
exit /b 0
"""
(H / "bin" / "python.bat").write_text(stub, encoding="utf-8", newline="\r\n")

deck = (ROOT / "START_DECK.bat").read_text(encoding="utf-8", errors="replace")
anchor = "echo  [OK] PREFLIGHT: compact-context probe GREEN."
idx = deck.find(anchor)
assert idx != -1, "compact-context anchor not found in START_DECK.bat"
close_idx = deck.find(")", idx)
seg = deck[: close_idx + 1] + "\r\ngoto :eof\r\n"
seg = seg.replace('set "PROJECT_ROOT=%~dp0"', f'set "PROJECT_ROOT={proj}"')
assert 'set "PROJECT_ROOT=' + proj in seg
(H / "deck_seg.bat").write_text(seg, encoding="utf-8", newline="\r\n")

run = f"""@echo off
setlocal EnableDelayedExpansion
set "PROJ={proj}"
set "PATH={H}\\bin;%PATH%"
cd /d "%PROJ%"
set "CI=1"
set "HEADLESS=1"

echo === S1: DECK compact on, probe RED ===
set "LLM_TRADER_COMPACT_CONTEXT=1"
set "CTX_PROBE_FORCE_FAIL=1"
call "%PROJ%artifacts\\bat_harness\\deck_seg.bat" > "%PROJ%artifacts\\bat_harness\\s1.log" 2>&1 <nul
echo [S1] rc=!ERRORLEVEL!
set "CTX_PROBE_FORCE_FAIL="

echo === S2: DECK compact on, probe GREEN ===
set "LLM_TRADER_COMPACT_CONTEXT=1"
call "%PROJ%artifacts\\bat_harness\\deck_seg.bat" > "%PROJ%artifacts\\bat_harness\\s2.log" 2>&1 <nul
echo [S2] rc=!ERRORLEVEL!

echo === S3: DECK compact unset ===
set "LLM_TRADER_COMPACT_CONTEXT="
call "%PROJ%artifacts\\bat_harness\\deck_seg.bat" > "%PROJ%artifacts\\bat_harness\\s3.log" 2>&1 <nul
echo [S3] rc=!ERRORLEVEL!

echo === S4: TRADING compact on, probe RED ===
set "LLM_TRADER_COMPACT_CONTEXT=1"
set "CTX_PROBE_FORCE_FAIL=1"
echo [trace] before TRADING call
call "%PROJ%START_TRADING.bat" > "%PROJ%artifacts\\bat_harness\\s4.log" 2>&1 <nul
echo [trace] after TRADING call, rc=!ERRORLEVEL!
echo [S4] rc=!ERRORLEVEL!
set "CTX_PROBE_FORCE_FAIL="

echo === S5: TRADING compact on, probe GREEN ===
set "LLM_TRADER_COMPACT_CONTEXT=1"
call "%PROJ%START_TRADING.bat" > "%PROJ%artifacts\\bat_harness\\s5.log" 2>&1 <nul
echo [S5] rc=!ERRORLEVEL!

echo === S6: TRADING explicit command, compact unset ===
set "LLM_TRADER_COMPACT_CONTEXT="
call "%PROJ%START_TRADING.bat" --loops 3 > "%PROJ%artifacts\\bat_harness\\s6.log" 2>&1 <nul
echo [S6] rc=!ERRORLEVEL!
"""
(H / "run.bat").write_text(run, encoding="utf-8", newline="\r\n")
print("harness v2 written")
print("PU-0133-BUILD-OK")
