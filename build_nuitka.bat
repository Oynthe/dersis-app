@echo off
setlocal enabledelayedexpansion
echo ============================================
echo  DERSIS - Nuitka Production Build
echo  Mode: Standalone Directory
echo ============================================
echo.

:: ── Configuration ───────────────────────────────────────────────────────
set APP_NAME=Dersis
:: Read version from the single source of truth: VERSION file.
:: Use for /f (not set /p) so a trailing CR from CRLF line endings is stripped.
set "APP_VERSION="
for /f "usebackq tokens=1 delims= " %%v in ("VERSION") do if not defined APP_VERSION set "APP_VERSION=%%v"
if not defined APP_VERSION (
    echo ERROR: VERSION file is missing or empty.
    pause
    exit /b 1
)
set ENTRY=scheduler_gui.py
set DIST_DIR=build\Dersis.dist
set COMPANY=Uygun

:: ── Step 1: Check Python ────────────────────────────────────────────────
echo [1/6] Checking Python...
python --version 2>nul
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.10+ and add to PATH.
    pause
    exit /b 1
)
echo.

:: ── Step 2: Install ALL dependencies ────────────────────────────────────
echo [2/6] Installing all dependencies...
echo.

:: Install all runtime deps (pinned for reproducibility) + build tools
pip install --upgrade pip
if errorlevel 1 (
    echo ERROR: Failed to upgrade pip.
    pause
    exit /b 1
)

pip install -r requirements-lock.txt
if errorlevel 1 (
    echo ERROR: Failed to install runtime dependencies.
    pause
    exit /b 1
)

pip install nuitka ordered-set
if errorlevel 1 (
    echo ERROR: Failed to install Nuitka.
    pause
    exit /b 1
)

:: Verify no dependency conflicts
pip check
if errorlevel 1 (
    echo WARNING: pip check reported dependency issues.
)
echo.

:: ── Step 3: Verify all packages are importable ──────────────────────────
echo [3/6] Verifying all packages are importable...
python verify_deps.py
if errorlevel 1 (
    echo.
    echo Cannot proceed — fix the missing packages above.
    pause
    exit /b 1
)
echo.

:: Pre-download C compiler so Nuitka doesn't prompt
python -m nuitka --version >nul 2>&1

:: ── Step 4: Build with Nuitka ───────────────────────────────────────────
echo ============================================
echo [4/6] Building with Nuitka (standalone)...
echo       This may take 5-15 minutes.
echo ============================================
echo.

python -m nuitka ^
    --standalone ^
    --enable-plugin=pyqt6 ^
    --disable-console ^
    --output-dir=build ^
    --jobs=%NUMBER_OF_PROCESSORS% ^
    --follow-imports ^
    --nofollow-import-to=test_* ^
    --nofollow-import-to=tests.* ^
    --nofollow-import-to=conftest ^
    --nofollow-import-to=unittest ^
    --nofollow-import-to=doctest ^
    --nofollow-import-to=setuptools ^
    --include-package=scheduler_app ^
    --include-package=scheduler_app.core ^
    --include-package=scheduler_app.ui ^
    --include-package=scheduler_app.i18n ^
    --include-package=scheduler_app.storage ^
    --include-package=scheduler_app.learning ^
    --include-package=scheduler_app.data_io ^
    --include-package=scheduler_app.assets ^
    --include-package=packaging ^
    --include-package=cryptography ^
    --include-package=openpyxl ^
    --include-package=pandas ^
    --include-package=numpy ^
    --include-package=reportlab ^
    --include-package=deepdiff ^
    --include-package=ortools ^
    --include-package-data=certifi ^
    --include-package-data=reportlab ^
    --include-package-data=ortools ^
    --include-data-dir=scheduler_app/assets=scheduler_app/assets ^
    --include-data-dir=flags=flags ^
    --include-data-files=docs/dersis.png=docs/dersis.png ^
    --windows-icon-from-ico=scheduler_app/assets/app_icon.ico ^
    --company-name="%COMPANY%" ^
    --product-name="%APP_NAME%" ^
    --product-version="%APP_VERSION%" ^
    --file-description="Dersis - Class Scheduling System" ^
    --copyright="(c) %COMPANY%" ^
    --assume-yes-for-downloads ^
    %ENTRY%

if errorlevel 1 (
    echo.
    echo ============================================
    echo  ERROR: Nuitka build failed.
    echo ============================================
    pause
    exit /b 1
)

echo.
echo Build complete!
echo.

:: ── Step 5: Rename output ───────────────────────────────────────────────
echo [5/6] Renaming build output...

set SRC_DIST=build\scheduler_gui.dist
if not exist "%SRC_DIST%" (
    echo ERROR: Expected %SRC_DIST% not found.
    dir /b /ad build\*.dist 2>nul
    pause
    exit /b 1
)

if exist "%SRC_DIST%\scheduler_gui.exe" (
    if exist "%SRC_DIST%\Dersis.exe" del "%SRC_DIST%\Dersis.exe"
    rename "%SRC_DIST%\scheduler_gui.exe" "Dersis.exe"
    echo   Renamed scheduler_gui.exe to Dersis.exe
)

if not exist "%DIST_DIR%" (
    rename "%SRC_DIST%" "Dersis.dist"
    echo   Renamed folder to Dersis.dist
)

:: ── Step 6: Verify assets ───────────────────────────────────────────────
echo.
echo [6/6] Verifying packaged assets...

set ERRORS=0

if exist "%DIST_DIR%\Dersis.exe" (
    echo   [OK] Dersis.exe
) else (
    echo   [MISSING] Dersis.exe
    set /a ERRORS+=1
)

if exist "%DIST_DIR%\docs\dersis.png" (
    echo   [OK] docs\dersis.png
) else (
    echo   [MISSING] docs\dersis.png
    set /a ERRORS+=1
)

if exist "%DIST_DIR%\flags" (
    set /a FLAG_COUNT=0
    for %%f in ("%DIST_DIR%\flags\*.png") do set /a FLAG_COUNT+=1
    echo   [OK] flags\ (!FLAG_COUNT! flags)
) else (
    echo   [MISSING] flags\
    set /a ERRORS+=1
)

set PY_LEAKED=0
for /r "%DIST_DIR%" %%f in (*.py) do set /a PY_LEAKED+=1
if %PY_LEAKED% GTR 0 (
    echo   [WARN] %PY_LEAKED% .py files in dist
) else (
    echo   [OK] No .py source files leaked
)

set /a FILE_COUNT=0
for /r "%DIST_DIR%" %%f in (*) do set /a FILE_COUNT+=1
echo   Total files: %FILE_COUNT%

echo.
echo ============================================
echo  BUILD SUCCESSFUL!
echo.
echo  Output:     %DIST_DIR%\
echo  Executable: %DIST_DIR%\Dersis.exe
echo.
echo  Next step:  iscc installer.iss
echo ============================================
echo.
pause
