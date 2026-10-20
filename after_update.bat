@echo off
cd /d "%~dp0"
where py >nul 2>nul && (py -3 aow4diff.py after-update) || (python aow4diff.py after-update)
pause
