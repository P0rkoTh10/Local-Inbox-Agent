@echo off
title Local Inbox Agent - STOP
echo Stopping Local Inbox Agent...
python "%~dp0scripts\kill_poll.py"
timeout /t 2 >nul
echo Stopped. Check poll.log to verify.
pause
