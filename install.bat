@echo off
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3 is required - install from https://python.org and tick "Add to PATH" & pause & exit /b 1)
python setup_wizard.py %*
pause
