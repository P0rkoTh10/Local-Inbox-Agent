@echo off
REM Uses poll_gmail.py from this repo (log inside the repo)
python -u "%~dp0poll_gmail.py" > "%~dp0logs\poll.log" 2>&1
