@echo off
REM ============================================================================
REM LLM Trader engine watchdog (master-plan P6.1)
REM Installs an NSSM Windows service that keeps the trading loop alive and
REM restarts it on crash. Run from an elevated prompt:
REM     ops\watchdog\install_nssm.bat install
REM     ops\watchdog\install_nssm.bat remove
REM Prereq: nssm.exe on PATH (https://nssm.cc) — or winget install nssm
REM ============================================================================
setlocal

set SERVICE=LLMTraderEngine
set ROOT=%~dp0..\..
for %%I in ("%ROOT%") do set ROOT=%%~fI

set BUN_PYTHON=python
set ENTRY=%ROOT%\mt5_trader.py
set ARGS=llm-trade --paper --continuous --tui --api-sync http://127.0.0.1:8000/api/internal/update
set LOGDIR=%ROOT%\artifacts\logs

if "%1"=="install" goto :install
if "%1"=="remove"  goto :remove
echo Usage: %~nx0 install^|remove
exit /b 1

:install
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
nssm install %SERVICE% "%BUN_PYTHON%" "%ENTRY%" %ARGS%
nssm set %SERVICE% AppDirectory "%ROOT%"
nssm set %SERVICE% AppStdout "%LOGDIR%\engine-out.log"
nssm set %SERVICE% AppStderr "%LOGDIR%\engine-err.log"
nssm set %SERVICE% AppRotateFiles 1
nssm set %SERVICE% AppRotateBytes 10485760
nssm set %SERVICE% AppExit Default Restart
nssm set %SERVICE% AppRestartDelay 15000
nssm set %SERVICE% Start SERVICE_DELAYED_AUTO_START
nssm start %SERVICE%
echo [%SERVICE%] installed. Supervisor heartbeat staleness ^>90s = crashed run.
exit /b 0

:remove
nssm stop %SERVICE% 2>nul
nssm remove %SERVICE% confirm
echo [%SERVICE%] removed.
exit /b 0
