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
:: .venv first, then the audit environment, then setup.bat's portable fallback.
set "PYEXE="
set "PYWEXE="
set "EMBEDDED_RUNTIME="

:: Probe the executable, not just its path. A venv launcher remains on disk
:: after its base Python has been uninstalled, but it cannot start.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import PyQt6, cryptography" >nul 2>&1
    if not errorlevel 1 (
        set "PYEXE=.venv\Scripts\python.exe"
        set "PYWEXE=.venv\Scripts\pythonw.exe"
    )
)

if not defined PYEXE if exist ".venv-audit\Scripts\python.exe" (
    ".venv-audit\Scripts\python.exe" -c "import PyQt6, cryptography" >nul 2>&1
    if not errorlevel 1 (
        set "PYEXE=.venv-audit\Scripts\python.exe"
        set "PYWEXE=.venv-audit\Scripts\pythonw.exe"
    )
)

if not defined PYEXE if exist ".tools\source-runtime\python.exe" (
    ".tools\source-runtime\python.exe" -c "import PyQt6, cryptography" >nul 2>&1
    if not errorlevel 1 (
        set "PYEXE=.tools\source-runtime\python.exe"
        set "PYWEXE=.tools\source-runtime\pythonw.exe"
        set "EMBEDDED_RUNTIME=1"
    )
)

:: A generated source distribution carries the same complete runtime as the
:: installer. It is a useful local fallback when a host Python disappears.
if not defined PYEXE if exist "build\Dersis.dist\python\python.exe" (
    "build\Dersis.dist\python\python.exe" -c "import PyQt6, cryptography" >nul 2>&1
    if not errorlevel 1 (
        set "PYEXE=build\Dersis.dist\python\python.exe"
        set "PYWEXE=build\Dersis.dist\python\pythonw.exe"
        set "EMBEDDED_RUNTIME=1"
    )
)

if not defined PYEXE (
    echo.
    echo   DERSIS cannot start: no project environment was found.
    echo.
    echo   No healthy project runtime was found. Checked:
    echo       .venv\Scripts\python.exe
    echo       .venv-audit\Scripts\python.exe
    echo       .tools\source-runtime\python.exe
    echo       build\Dersis.dist\python\python.exe
    echo.
    echo   Run setup.bat once to create it, then try again.
    echo.
    pause
    exit /b 1
)

:: ── 2. Check the dependencies before launching ───────────────────────────
:: Candidate probing above already checked the startup dependencies.

:: ── 3. Launch ────────────────────────────────────────────────────────────
:: The embeddable distribution intentionally ignores PYTHONPATH. Insert this
:: source tree explicitly so it runs today's code, not its packaged snapshot.
set "DERSIS_SOURCE_ROOT=%CD%"
if "%~1"=="" goto :windowed

echo Running with %PYEXE%  (console mode)
echo.
if defined EMBEDDED_RUNTIME (
    "%PYEXE%" -c "import os,runpy,sys;p=os.environ['DERSIS_SOURCE_ROOT'];sys.path.insert(0,p);sys.argv=[os.path.join(p,'scheduler_gui.py')]+sys.argv[1:];runpy.run_path(sys.argv[0],run_name='__main__')" %*
) else (
    "%PYEXE%" scheduler_gui.py %*
)
echo.
echo   DERSIS exited with code %errorlevel%.
pause
exit /b %errorlevel%

:windowed
:: pythonw.exe so no console window is left behind. A startup failure is still
:: visible: scheduler_gui.py catches it, writes Documents\Dersis\logs\
:: startup_error.log and shows a message box, which is what that handler is for.
if defined EMBEDDED_RUNTIME (
    start "" "%PYWEXE%" -c "import os,runpy,sys;p=os.environ['DERSIS_SOURCE_ROOT'];sys.path.insert(0,p);sys.argv=[os.path.join(p,'scheduler_gui.py')];runpy.run_path(sys.argv[0],run_name='__main__')"
) else (
    start "" "%PYWEXE%" "scheduler_gui.py"
)
exit /b 0
