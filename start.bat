@echo off
rem Fleet Fuel ^& VAT Refund System -- double-click to start (Windows).
cd /d "%~dp0"
where python >nul 2>nul || (
  echo Fleet Fuel needs Python 3 to run.
  echo Get it free from https://www.python.org/downloads/ -- during install tick "Add Python to PATH".
  echo Then double-click this file again.
  pause & exit /b 1
)
python start.py
pause
