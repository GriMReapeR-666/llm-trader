"""Write standalone S5 (GREEN) and S6 (explicit-command) harness runners."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
H = ROOT / "artifacts" / "bat_harness"
proj = str(ROOT) + "\\"

s5 = f"""@echo off
setlocal EnableDelayedExpansion
set "PROJ={proj}"
set "PATH={H}\\bin;%PATH%"
cd /d "%PROJ%"
set "CI=1"
set "HEADLESS=1"
set "LLM_TRADER_COMPACT_CONTEXT=1"
call "%PROJ%START_TRADING.bat" > "%PROJ%artifacts\\bat_harness\\s5.log" 2>&1 <nul
echo [S5] rc=!ERRORLEVEL!
"""
s6 = f"""@echo off
setlocal EnableDelayedExpansion
set "PROJ={proj}"
set "PATH={H}\\bin;%PATH%"
cd /d "%PROJ%"
set "CI=1"
set "HEADLESS=1"
set "LLM_TRADER_COMPACT_CONTEXT="
call "%PROJ%START_TRADING.bat" --loops 3 > "%PROJ%artifacts\\bat_harness\\s6.log" 2>&1 <nul
echo [S6] rc=!ERRORLEVEL!
"""
(H / "run_s5.bat").write_text(s5, encoding="utf-8", newline="\r\n")
(H / "run_s6.bat").write_text(s6, encoding="utf-8", newline="\r\n")
print("run_s5.bat + run_s6.bat written")
print("PU-0135-BUILD-OK")
