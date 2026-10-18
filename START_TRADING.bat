@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1
title LLM Trader - Autonomous Headless Trading

set "PROJECT_ROOT=%~dp0"
cd /d "%PROJECT_ROOT%"
set "DELIBERATIVE_GRAPH_SHADOW=1"
rem Dynamic trade ladder: enforce in the paper canary so the contextual plan
rem (news calendar / session / growth curve) actually gates sizing; the
rem TRADE_PLANNER_MODE env var set by the operator still wins.
if not defined TRADE_PLANNER_MODE set "TRADE_PLANNER_MODE=enforce"

echo.
echo ====================================================
echo   LLM TRADER - AUTONOMOUS HEADLESS TRADING ENGINE
echo ====================================================
echo.

set "FIRST_ARG=%~1"
set "PREFIX=!FIRST_ARG:~0,2!"

if "%~1"=="" (
    echo [HEADLESS AUTO-MODE] Launching EURUSD AUTO ^(Paper Mode, Continuous^)
    if /i "%SMOKE_PREFLIGHT%"=="1" (
        echo [PREFLIGHT] QKF smoke probes running ^(set SMOKE_PREFLIGHT=0 to skip^)...
        pushd quant_knowledge_framework
        python -X utf8 smoke_test.py --fast
        if errorlevel 1 (
            popd
            echo [PREFLIGHT] QKF smoke RED - aborting launch. Report: artifacts\qkf_smoke_report.txt
            goto :done
        )
        popd
        echo [PREFLIGHT] QKF smoke GREEN.
    )
    if /i "%LLM_TRADER_COMPACT_CONTEXT%"=="1" (
        echo [PREFLIGHT] Compact-context probe running ^(LLM_TRADER_COMPACT_CONTEXT=1^)...
        call python -X utf8 artifacts\ctx_probe.py
        if errorlevel 1 (
            echo [PREFLIGHT] Compact-context probe RED - aborting launch. See artifacts\ctx_probe.txt
            goto :done
        )
        echo [PREFLIGHT] Compact-context probe GREEN.
    )
    python -u mt5_trader.py llm-trade --paper --symbol EURUSD --timeframe AUTO --model meta/llama-3.2-11b-vision-instruct --risk-per-trade 1.0 --continuous
    goto :done
)

if "!PREFIX!"=="--" (
    echo [HEADLESS AUTO-MODE] Launching EURUSD AUTO with options: %*
    python -u mt5_trader.py llm-trade --paper --symbol EURUSD --timeframe AUTO --model meta/llama-3.2-11b-vision-instruct --risk-per-trade 1.0 %*
    goto :done
)

echo [HEADLESS CUSTOM-COMMAND] Launching: %*
python -u mt5_trader.py %*

:done
if "%~1"=="" if not "%CI%"=="1" if not "%HEADLESS%"=="1" pause
endlocal
