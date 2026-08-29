@echo off
setlocal enabledelayedexpansion
:: ===========================================================================
::  DERSIS - one-time development setup
::
::  Creates .venv next to this file and installs the runtime dependencies into
::  it. Run this once; afterwards use run.bat.
::
::  This does NOT touch the system Python and does NOT need administrator
::  rights. It also does not build an installer - build_embed.bat does that.
:: ===========================================================================
cd /d "%~dp0"

echo ============================================
echo   DERSIS - development setup
echo ============================================
echo.

:: ── 1. Find a host Python to build the environment with ──────────────────
:: `py` (the Windows launcher) is preferred over `python`, because the
:: WindowsApps `python.exe` stub is on PATH by default on Windows 11 and opens
:: the Microsoft Store instead of running anything.
set "HOSTPY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 set "HOSTPY=py -3"

if not defined HOSTPY (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 set "HOSTPY=python"
)

if not defined HOSTPY (
    echo   ERROR: no Python 3.10 or newer was found.
    echo.
    echo   Install it from https://www.python.org/downloads/ and tick
    echo   "Add python.exe to PATH" in the installer, then run this again.
    echo.
    pause
    exit /b 1
)

for /f "delims=" %%v in ('%HOSTPY% -c "import sys;print(sys.version.split()[0])"') do set "HOSTVER=%%v"
echo   [1/4] Host Python: %HOSTVER%  ^(via "%HOSTPY%"^)
echo.

:: ── 2. Create the virtual environment ────────────────────────────────────
if exist ".venv\Scripts\python.exe" (
    echo   [2/4] .venv already exists - reusing it.
) else (
    echo   [2/4] Creating .venv ...
    %HOSTPY% -m venv .venv
    if errorlevel 1 (
        echo   ERROR: could not create .venv.
        pause
        exit /b 1
    )
)
echo.

:: ── 3. Install the dependencies ──────────────────────────────────────────
echo   [3/4] Installing dependencies from requirements.txt ...
echo         ^(this downloads ~200 MB the first time and takes a few minutes^)
echo.
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo   ERROR: dependency installation failed. The output above says why.
    pause
    exit /b 1
)
echo.

:: ── 4. Verify ────────────────────────────────────────────────────────────
:: verify_deps.py checks the transitive set too, not just what requirements.txt
:: names, because a partial install starts and then fails at the first export.
echo   [4/4] Verifying ...
".venv\Scripts\python.exe" verify_deps.py
if errorlevel 1 (
    echo.
    echo   ERROR: some packages are still missing. See the list above.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Setup complete. Start DERSIS with run.bat
echo ============================================
echo.
pause
exit /b 0
