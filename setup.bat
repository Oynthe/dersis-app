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

:: A Python uninstall leaves existing venv launchers unusable. If this tree has
:: already produced an embeddable build, it is a complete verified runtime and
:: can seed a portable source environment without touching system Python.
set "PORTABLE_SOURCE="
if not defined HOSTPY if exist "build\Dersis.dist\python\python.exe" (
    "build\Dersis.dist\python\python.exe" -c "import sys, PyQt6, cryptography; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PORTABLE_SOURCE=build\Dersis.dist\python"
)

if defined PORTABLE_SOURCE goto :portable_setup

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

:portable_setup
echo   [1/4] Host Python is unavailable; using the verified embedded runtime.
echo.

if not exist ".tools" mkdir ".tools"
echo   [2/4] Creating .tools\source-runtime ...
robocopy "%PORTABLE_SOURCE%" ".tools\source-runtime" /E /NFL /NDL /NJH /NJS /NP /R:2 /W:2
if errorlevel 8 (
    echo   ERROR: could not copy the embedded runtime.
    pause
    exit /b 1
)
ver >nul
echo.

:: Embeddable Python ignores PYTHONPATH while a python*._pth file exists. Add
:: the repository root explicitly: source-runtime is two levels below it.
for %%f in (".tools\source-runtime\python*._pth") do (
    powershell -NoProfile -Command "$f='%%f'; $c=Get-Content -LiteralPath $f; $c=$c -replace '^#\s*import site','import site'; if ($c -notcontains '..\..') { $c += '..\..' }; Set-Content -Encoding ASCII -LiteralPath $f -Value $c"
    if errorlevel 1 (
        echo   ERROR: could not configure %%~nxf.
        pause
        exit /b 1
    )
)

echo   [3/4] Runtime dependencies came from the verified packaged build.
echo.
echo   [4/4] Verifying ...
".tools\source-runtime\python.exe" verify_deps.py
if errorlevel 1 (
    echo.
    echo   ERROR: some packages are missing. See the list above.
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
