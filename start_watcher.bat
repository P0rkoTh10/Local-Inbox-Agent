@echo off
title Local Inbox Agent - START
echo Starting Local Inbox Agent (poll every 60s + Gemma)...
start /MIN "" "%~dp0run_poll.bat"
timeout /t 3 >nul
echo Watcher started. Log: logs\poll.log
echo To stop: double-click "stop_watcher.bat"
pause
