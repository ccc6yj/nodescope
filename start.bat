@echo off
rem One-click launcher for Windows: runs start.ps1 (prepares .venv, deps and mihomo, then starts the web UI).
rem Usage: double-click, or run "start.bat --port 9000" in a terminal.
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
rem Keep the window open on failure so double-click users can read the error
if errorlevel 1 pause
endlocal
