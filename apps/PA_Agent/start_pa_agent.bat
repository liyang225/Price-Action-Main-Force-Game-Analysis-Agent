@echo off
setlocal
cd /d "%~dp0"
start "" /b pythonw "%~dp0run.py"

endlocal
exit /b
