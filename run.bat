@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
set "PA_AGENT_ROOT=%PROJECT_ROOT%apps\PA_Agent"
set "ENTRY_POINT=%PA_AGENT_ROOT%\run.py"

if not exist "%ENTRY_POINT%" (
    echo [ERROR] PA Agent entry point not found:
    echo         %ENTRY_POINT%
    pause
    exit /b 1
)

set "PYTHON_EXE="
if exist "%PROJECT_ROOT%.venv\Scripts\pythonw.exe" (
    set "PYTHON_EXE=%PROJECT_ROOT%.venv\Scripts\pythonw.exe"
) else if exist "%PROJECT_ROOT%.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%PROJECT_ROOT%.venv\Scripts\python.exe"
) else (
    where pythonw.exe >nul 2>nul
    if not errorlevel 1 set "PYTHON_EXE=pythonw.exe"
)

if not defined PYTHON_EXE (
    where python.exe >nul 2>nul
    if not errorlevel 1 set "PYTHON_EXE=python.exe"
)

if not defined PYTHON_EXE (
    echo [ERROR] Python 3.11 or newer was not found.
    echo         Install Python or create .venv in the repository root.
    pause
    exit /b 1
)

pushd "%PA_AGENT_ROOT%"
start "PA Agent" /b "%PYTHON_EXE%" "%ENTRY_POINT%"
set "RUN_EXIT_CODE=%ERRORLEVEL%"
popd

if not "%RUN_EXIT_CODE%"=="0" (
    echo [ERROR] PA Agent failed to start. Exit code: %RUN_EXIT_CODE%
    pause
    exit /b %RUN_EXIT_CODE%
)

endlocal
exit /b 0
