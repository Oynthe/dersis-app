@echo off
setlocal enabledelayedexpansion
:: ===========================================================================
::  DERSIS - run the app from source
::
::  Double-click this file, or run it from a terminal. It works without
::  activating anything.
::
::  Why this file exists: `python scheduler_gui.py` runs whatever `python`
::  happens to be first on PATH, and on a machine where the dependencies were
::  installed into a virtual environment that is NOT the active one, that is
::  the system interpreter with no PyQt6. The failure looks like a crash
::  ("ModuleNotFoundError: No module named 'PyQt6'") rather than what it is -
::  the wrong interpreter. This script picks the interpreter by looking for
::  one, instead of trusting PATH.
::
::  Pass any argument (e.g. `run.bat -v`) to keep the console window open and
::  show the app's output, which is what you want when diagnosing a problem.
:: ===========================================================================
cd /d "%~dp0"

:: ── 1. Find an interpreter that belongs to this project ──────────────────
:: .venv first (what setup.bat creates), then .venv-audit (the test
:: environment, which also carries the full runtime set).
set "PYDIR="
if exist ".venv\Scripts\python.exe"        set "PYDIR=.venv\Scripts"
if not defined PYDIR if exist ".venv-audit\Scripts\python.exe" set "PYDIR=.venv-audit\Scripts"

if not defined PYDIR (
    echo.
    echo   DERSIS cannot start: no project environment was found.
    echo.
    echo   Expected one of:
    echo       .venv\Scripts\python.exe
    echo       .venv-audit\Scripts\python.exe
    echo.
    echo   Run setup.bat once to create it, then try again.
    echo.
    pause
    exit /b 1
)

:: ── 2. Check the dependencies before launching ───────────────────────────
:: Without this the app starts, fails to import, and reports through its own
:: startup-error dialog - correct, but it names the missing module rather than
:: the reason, and the reason is almost always an incomplete install.
"%PYDIR%\python.exe" -c "import PyQt6, cryptography" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   DERSIS cannot start: %PYDIR%\python.exe is missing dependencies.
    echo.
    echo   Install them with:
    echo       %PYDIR%\python.exe -m pip install -r requirements.txt
    echo.
    echo   or run setup.bat, which does that for you.
    echo.
    pause
    exit /b 1
)

:: ── 3. Launch ────────────────────────────────────────────────────────────
if "%~1"=="" goto :windowed

echo Running with %PYDIR%\python.exe  (console mode)
echo.
"%PYDIR%\python.exe" scheduler_gui.py %*
echo.
echo   DERSIS exited with code %errorlevel%.
pause
exit /b %errorlevel%

:windowed
:: pythonw.exe so no console window is left behind. A startup failure is still
:: visible: scheduler_gui.py catches it, writes Documents\Dersis\logs\
:: startup_error.log and shows a message box, which is what that handler is for.
start "" "%PYDIR%\pythonw.exe" "scheduler_gui.py"
exit /b 0
