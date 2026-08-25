@echo off
setlocal
cd /d "%~dp0"
set "PA_AGENT_PYTHONW=C:\Users\bai\AppData\Local\Programs\Python\Python314\pythonw.exe"

if exist "%PA_AGENT_PYTHONW%" (
    start "" /b "%PA_AGENT_PYTHONW%" "%~dp0run.py"
) else (
    start "" /b pythonw "%~dp0run.py"
)

endlocal
exit /b
