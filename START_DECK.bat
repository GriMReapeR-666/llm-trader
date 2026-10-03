@echo off
setlocal EnableDelayedExpansion
title LLM Trader - Command Deck

set "BUN=%USERPROFILE%\.bun\bin\bun.exe"
if not exist "%BUN%" set "BUN=bun"
set "PROJECT_ROOT=%~dp0"
set "DELIBERATIVE_GRAPH_SHADOW=1"
if "%~1"=="" (echo  Args: none) else (echo  Args: %*)

echo.
echo  +====================================================+
echo  ^|   ##      ########  ########  ##   ##  ########   ^|
echo  ^|   ##      ##    ##  ##    ##   ## ##   ##         ^|
echo  ^|   ##      ########  ########    ###    ######     ^|
echo  ^|   ##      ##    ##  ##    ##   ## ##   ##         ^|
echo  ^|   ######  ########  ########  ##   ##  ########   ^|
echo  ^|   autonomous multi-agent trading                  ^|
echo  +====================================================+
echo.

REM -- Pre-flight checks ---------------------------------

where bun >nul 2>&1
if errorlevel 1 (
  if not exist "%BUN%" (
    echo  [X] Bun not found. Install: powershell -c "irm bun.sh/install.ps1 | iex"
    pause
    exit /b 1
  )
)

where python >nul 2>&1
if errorlevel 1 (
  echo  [X] Python not found in PATH.
  pause
    exit /b 1
)

REM -- Optional QKF smoke preflight: set SMOKE_PREFLIGHT=1 to enable --------

if /i "%SMOKE_PREFLIGHT%"=="1" (
  echo  [PREFLIGHT] QKF smoke probes running ^(set SMOKE_PREFLIGHT=0 to skip^)...
  pushd "%PROJECT_ROOT%quant_knowledge_framework"
  python -X utf8 smoke_test.py --fast
  if errorlevel 1 (
    popd
    echo  [X] PREFLIGHT: QKF smoke RED - aborting launch. Report: artifacts\qkf_smoke_report.txt
    pause
    exit /b 1
  )
  popd
  echo  [OK] PREFLIGHT: QKF smoke GREEN.
)

if not exist "%PROJECT_ROOT%tui_opentui\package.json" (
    echo  [X] Project not found. Run from repo root.
    pause
    exit /b 1
)

REM -- Optional compact-context preflight: set LLM_TRADER_COMPACT_CONTEXT=1 --
REM    (R1 payload audit: rolling-window history + summaries; verify env first).

if /i "%LLM_TRADER_COMPACT_CONTEXT%"=="1" (
  call python -X utf8 artifacts\ctx_probe.py
  if errorlevel 1 (
    echo  [X] PREFLIGHT: compact-context probe RED - aborting launch. See artifacts\ctx_probe.txt
    pause
    exit /b 1
  )
  echo  [OK] PREFLIGHT: compact-context probe GREEN.
)

REM -- Boot mode selection: fast-boot skips the ~25-min Laya news triage --
REM    (NEWS_TRIAGE_MODE=off -> deterministic rule-based triage, honest fallback).

set "TRIAGE_MODE=auto"
if /i "%DECK_TRIAGE_MODE%"=="off" set "TRIAGE_MODE=off"
if /i "%DECK_TRIAGE_MODE%"=="auto" set "TRIAGE_MODE=auto_no_prompt"
if /i "%1"=="--fast" set "TRIAGE_MODE=off"
if /i "%1"=="--full" set "TRIAGE_MODE=full"
if "%TRIAGE_MODE%"=="auto" if not "%DECK_TRIAGE_MODE%"=="auto" (
  echo.
  choice /C YN /N /T 5 /D N /M "Fast boot? Skip ~25min news triage this launch [Y/N, N in 5s]: "
  if errorlevel 2 (set "TRIAGE_MODE=full") else set "TRIAGE_MODE=off"
)
if "%TRIAGE_MODE%"=="off" (set "NEWS_TRIAGE_MODE=off") else if "%TRIAGE_MODE%"=="full" (set "NEWS_TRIAGE_MODE=auto") else set "NEWS_TRIAGE_MODE="
if "%TRIAGE_MODE%"=="off" echo  [FAST BOOT] NEWS_TRIAGE_MODE=off -- deterministic news triage, Laya inference skipped
if "%TRIAGE_MODE%"=="full" echo  [FULL BOOT] NEWS_TRIAGE_MODE=auto -- full Laya news triage runs at engine start

REM -- Kill stale processes on port 8000 -----------------

for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr :8000 ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1

REM -- Start API Server in background --------------------

echo  Starting API Server on :8000...
cd /d "%PROJECT_ROOT%"
start "LLM Trader API" /B cmd /c "python -u ui\gui_api.py > api.log 2>&1"

REM -- Wait for API (simple retry loop) ------------------

echo  Waiting for API...
set "RETRIES=0"
:WAITLOOP
ping 127.0.0.1 -n 2 >nul
python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)" >nul 2>&1
if %ERRORLEVEL% EQU 0 goto APIREADY
set /a RETRIES+=1
if %RETRIES% LSS 30 goto WAITLOOP
echo  [X] API server failed to start
if exist "%PROJECT_ROOT%api.log" (
  echo.
  echo  --- Last API log output: ---
  powershell -nop -c "Get-Content '%PROJECT_ROOT%api.log' -Tail 15" 2>nul
  echo  ----------------------------
)
pause
exit /b 1

:APIREADY
echo  [OK] API server ready
echo.

REM -- Auto-start trader in PAPER mode --------------------

echo  Starting trader (paper mode)...
python -c "import urllib.request, json; r=urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000/api/control/start', data=json.dumps({'mode':'paper','symbol':'EURUSD','timeframe':'AUTO','risk':1.0,'continuous':True,'news_triage_mode':__import__('os').environ.get('NEWS_TRIAGE_MODE','') or None}).encode(), headers={'Content-Type':'application/json'}), timeout=10); b=json.load(r); print('[OK] Trader started (paper mode)' if b.get('status')=='success' else '[!] Trader start refused: '+str(b.get('status'))+' -- start manually from Command Deck [5]')" 2>nul
if %ERRORLEVEL% EQU 0 goto TRADER_DONE
echo  [!] Trader auto-start failed (connection error) -- start manually from Command Deck [5]
:TRADER_DONE
echo.

REM -- Install TUI deps if needed -----------------------

if not exist "%PROJECT_ROOT%tui_opentui\node_modules" (
    echo  Installing TUI dependencies...
    cd /d "%PROJECT_ROOT%tui_opentui"
    call "%BUN%" install
    if errorlevel 1 (
        echo  [X] bun install failed
        pause
        exit /b 1
    )
)

REM -- Launch TUI -----------------------------------------

echo  +====================================================+
echo  ^|  Starting Command Deck...                          ^|
echo  +====================================================+
echo.
echo   Keys: 1-6 sections  [ ] screens  / palette  i prompt  x dismiss
echo   Exit: Ctrl+C
echo.

cd /d "%PROJECT_ROOT%tui_opentui"
set "TUI_ARGS=%DECK_TUI_ARGS%"
if defined TUI_ARGS ("%BUN%" src/index.tsx %TUI_ARGS%) else "%BUN%" src/index.tsx

REM -- Cleanup: kill API on exit -------------------------

echo.
echo  Shutting down API server...
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr :8000 ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1
echo  Done.
endlocal
