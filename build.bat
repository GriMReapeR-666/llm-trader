@echo off
title Autonomous Trader - Unified Build Pipeline (RTK + Graphify + Ponytail)
echo ====================================================================
echo  Running Unified Build: RTK + GRAPHIFY + PONYTAIL
echo ====================================================================
git config core.hooksPath .githooks
python "%~dp0scripts\build.py"
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Build failed with code %ERRORLEVEL%
    pause
    exit /b %ERRORLEVEL%
)
echo [SUCCESS] Build, token verification, and knowledge graph updated!
pause
