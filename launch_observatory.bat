@echo off
title Strategy Observatory - Autonomous Quantitative Cockpit
mode con: cols=175 lines=50
cd /d "%~dp0tui_opentui"
echo ======================================================================
echo  Launching Strategy Observatory Cockpit (Live Transport Bridge)...
echo ======================================================================
"C:\Users\User\.bun\bin\bun.exe" run demo:observatory
pause
