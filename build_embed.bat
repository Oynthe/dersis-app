@echo off
setlocal enabledelayedexpansion
echo ============================================
echo  DERSIS - Embeddable Python Build
echo  No Nuitka required. Fast and reliable.
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
:: Validate version format: must be X.Y.Z where X, Y, Z are numbers.
:: NOTE: there must be NO space before the pipe. "echo %VAR% | findstr" would
:: emit a trailing space into the piped text and break the trailing "$" anchor.
echo %APP_VERSION%|findstr /r "^[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*$" >nul
if errorlevel 1 (
    echo ERROR: VERSION "%APP_VERSION%" is not valid semver ^(expected X.Y.Z^).
    pause
    exit /b 1
)
echo   Version: %APP_VERSION%
set PY_VERSION=3.11.9
set PY_ZIP=python-%PY_VERSION%-embed-amd64.zip
set PY_URL=https://www.python.org/ftp/python/%PY_VERSION%/%PY_ZIP%
set DIST_DIR=build\Dersis.dist
set PY_DIR=%DIST_DIR%\python

:: ── Step 1: Install build-time dependencies in current environment ──────
echo [1/8] Installing build-time dependencies...
echo.

python --version 2>nul
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.10+ and add to PATH.
    pause
    exit /b 1
)

:: Install Inno Setup helper and Pillow (for wizard images if needed)
pip install --quiet Pillow 2>nul
echo   [OK] Build-time deps ready
echo.

:: ── Step 2: Download Python embeddable ──────────────────────────────────
echo [2/8] Downloading Python %PY_VERSION% embeddable...

if not exist "build" mkdir build
if exist "%DIST_DIR%" (
    echo   Cleaning previous build...
    rmdir /s /q "%DIST_DIR%"
)
mkdir "%DIST_DIR%"
mkdir "%PY_DIR%"

if not exist "build\%PY_ZIP%" (
    echo   Downloading %PY_URL%...
    curl -L -o "build\%PY_ZIP%" "%PY_URL%"
    if errorlevel 1 (
        echo   Trying with PowerShell...
        powershell -Command "Invoke-WebRequest -Uri '%PY_URL%' -OutFile 'build\%PY_ZIP%'"
    )
    if not exist "build\%PY_ZIP%" (
        echo ERROR: Failed to download Python embeddable.
        pause
        exit /b 1
    )
) else (
    echo   Using cached %PY_ZIP%
)
echo   Extracting to %PY_DIR%...
powershell -Command "Expand-Archive -Force 'build\%PY_ZIP%' '%PY_DIR%'"
echo   [OK] Python embeddable ready
echo.

:: ── Step 3: Enable pip in embeddable Python ─────────────────────────────
echo [3/8] Setting up pip in embeddable Python...

:: Configure the embeddable Python search path (python*._pth):
::   - Uncomment "import site" so pip-installed site-packages are importable.
::   - Add ".." so the app package (one level up, in the dist root) is found.
:: When a ._pth file is present the interpreter runs in "safe path" mode and
:: does NOT add the launched script's own directory to sys.path. Without "..",
:: "import scheduler_app" fails and the app closes silently under pythonw.exe.
for %%f in ("%PY_DIR%\python*._pth") do (
    powershell -NoProfile -Command "$f='%%f'; $c = Get-Content $f; $c = $c -replace '^#\s*import site','import site'; if ($c -notcontains '..') { $c += '..' }; Set-Content -Path $f -Value $c"
    echo   Updated %%~nxf - added app dir to sys.path, enabled site
)

:: Download and run get-pip.py
if not exist "build\get-pip.py" (
    echo   Downloading get-pip.py...
    curl -L -o "build\get-pip.py" "https://bootstrap.pypa.io/get-pip.py"
    if errorlevel 1 (
        powershell -Command "Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile 'build\get-pip.py'"
    )
)
"%PY_DIR%\python.exe" "build\get-pip.py" --no-warn-script-location
echo   [OK] pip installed
echo.

:: ── Step 4: Install ALL runtime dependencies ────────────────────────────
echo [4/8] Installing all runtime dependencies...
echo.

:: Install all runtime dependencies from the lock file for reproducibility.
:: The lock file pins exact versions so builds are deterministic.
"%PY_DIR%\python.exe" -m pip install --no-warn-script-location -r requirements-lock.txt

if errorlevel 1 (
    echo.
    echo ERROR: Failed to install some packages. Check output above.
    pause
    exit /b 1
)
echo.

:: ── Step 5: Verify all packages are importable ──────────────────────────
echo [5/8] Verifying all packages are importable...
"%PY_DIR%\python.exe" verify_deps.py
if errorlevel 1 (
    echo.
    echo ERROR: Some packages are missing. Check output above.
    pause
    exit /b 1
)
echo.

:: ── Step 6: Copy application files ──────────────────────────────────────
echo [6/8] Copying application files...

:: Main entry point
copy /y scheduler_gui.py "%DIST_DIR%\" >nul

:: Application package (exclude __pycache__ and .pyc files).
:: Use robocopy instead of "xcopy /exclude" — xcopy's /exclude with a quoted
:: list file fails with "Can't read file" on some hosts (e.g. CI runners),
:: silently copying nothing. Robocopy returns exit codes 0-7 on success and
:: >=8 only on real errors.
robocopy scheduler_app "%DIST_DIR%\scheduler_app" /E /XD __pycache__ /XF *.pyc /NFL /NDL /NJH /NJS /NP /R:2 /W:2
if errorlevel 8 (
    echo ERROR: Failed to copy scheduler_app.
    pause
    exit /b 1
)
:: Normalize errorlevel — robocopy uses 1-7 for non-error states.
ver >nul

:: Flag icons
xcopy /s /e /i /q /y flags "%DIST_DIR%\flags" >nul

:: Logo
if not exist "%DIST_DIR%\docs" mkdir "%DIST_DIR%\docs"
copy /y docs\dersis.png "%DIST_DIR%\docs\" >nul

:: VERSION file (single source of truth, read at runtime by _version.py)
copy /y VERSION "%DIST_DIR%\" >nul

:: License
if exist "installer\LICENSE.txt" (
    copy /y installer\LICENSE.txt "%DIST_DIR%\" >nul
)

:: Generate installer version include file for Inno Setup
if not exist "build" mkdir build
echo #define AppVersion "%APP_VERSION%"> "build\version.iss"
echo   [OK] Generated build\version.iss with version %APP_VERSION%

echo   [OK] Application files copied
echo.

:: ── Step 7: Compile .py to .pyc (bytecode) ──────────────────────────────
echo [7/8] Compiling Python files to bytecode...

:: Compile all .py files to .pyc for faster startup and basic obfuscation
"%PY_DIR%\python.exe" -m compileall -q -b "%DIST_DIR%\scheduler_app"
"%PY_DIR%\python.exe" -m compileall -q -b "%DIST_DIR%\scheduler_gui.py"

:: Remove .py source files (keep only .pyc) for obfuscation
:: The .pyc files are placed alongside .py with -b flag
del /q "%DIST_DIR%\scheduler_gui.py" 2>nul
for /r "%DIST_DIR%\scheduler_app" %%f in (*.py) do del "%%f" 2>nul

:: Rename scheduler_gui.pyc to scheduler_gui.py so the launcher finds it
:: (Python can run .pyc files directly when named .pyc)
:: Actually, pythonw.exe can run .pyc files, but we need to adjust the launcher
:: Let's keep the .py entry point and only compile the package
:: Restore scheduler_gui.py
copy /y scheduler_gui.py "%DIST_DIR%\" >nul

echo   [OK] Bytecode compiled, source removed from scheduler_app
echo.

:: ── Step 8: Create launcher and verify ──────────────────────────────────
echo [8/8] Creating launcher and verifying build...

:: .vbs launcher (silent, no console window)
(
echo Set WshShell = CreateObject^("WScript.Shell"^)
echo appDir = CreateObject^("Scripting.FileSystemObject"^).GetParentFolderName^(WScript.ScriptFullName^)
echo WshShell.CurrentDirectory = appDir
echo WshShell.Run Chr^(34^) ^& appDir ^& "\python\pythonw.exe" ^& Chr^(34^) ^& " " ^& Chr^(34^) ^& appDir ^& "\scheduler_gui.py" ^& Chr^(34^), 0, False
) > "%DIST_DIR%\Dersis.vbs"

:: .bat launcher (fallback)
(
echo @echo off
echo cd /d "%%~dp0"
echo start "" "python\pythonw.exe" "scheduler_gui.py"
) > "%DIST_DIR%\Dersis.bat"

:: .exe launcher — compile a tiny C# wrapper
echo   Creating Dersis.exe...
powershell -NoProfile -Command ^
  "$src = 'using System; using System.Diagnostics; using System.IO; using System.Reflection; class P { static void Main() { string d = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location); Process.Start(new ProcessStartInfo { FileName = Path.Combine(d, \"python\", \"pythonw.exe\"), Arguments = \"\\\"\" + Path.Combine(d, \"scheduler_gui.py\") + \"\\\"\", WorkingDirectory = d, UseShellExecute = false, CreateNoWindow = true }); } }'; Add-Type -TypeDefinition $src -OutputAssembly '%DIST_DIR%\Dersis.exe' -OutputType ConsoleApplication 2>$null; if (Test-Path '%DIST_DIR%\Dersis.exe') { Write-Host '  [OK] Dersis.exe' } else { Write-Host '  [WARN] exe failed, using .vbs' }"

:: Copy icon into dist for shortcuts
copy /y scheduler_app\assets\app_icon.ico "%DIST_DIR%\scheduler_app\assets\" >nul 2>nul

:: ── Verify ──────────────────────────────────────────────────────────────
echo.
echo   Verifying build output...

set ERRORS=0

if exist "%DIST_DIR%\python\pythonw.exe" (
    echo   [OK] python\pythonw.exe
) else (
    echo   [MISSING] python\pythonw.exe
    set /a ERRORS+=1
)

if exist "%DIST_DIR%\scheduler_gui.py" (
    echo   [OK] scheduler_gui.py
) else (
    echo   [MISSING] scheduler_gui.py
    set /a ERRORS+=1
)

if exist "%DIST_DIR%\docs\dersis.png" (
    echo   [OK] docs\dersis.png
) else (
    echo   [MISSING] docs\dersis.png
    set /a ERRORS+=1
)

if exist "%DIST_DIR%\flags" (
    set /a FC=0
    for %%f in ("%DIST_DIR%\flags\*.png") do set /a FC+=1
    echo   [OK] flags\ ^(!FC! flags^)
) else (
    echo   [MISSING] flags\
    set /a ERRORS+=1
)

if exist "%DIST_DIR%\VERSION" (
    echo   [OK] VERSION ^(%APP_VERSION%^)
) else (
    echo   [MISSING] VERSION
    set /a ERRORS+=1
)

if exist "%DIST_DIR%\scheduler_app\ui\app.pyc" (
    echo   [OK] scheduler_app compiled to .pyc
) else if exist "%DIST_DIR%\scheduler_app\ui\app.py" (
    echo   [OK] scheduler_app ^(source^)
) else (
    echo   [MISSING] scheduler_app
    set /a ERRORS+=1
)

:: Import smoke test: run the bundled interpreter exactly as the launcher will
:: and confirm it can import the app package. Catches python*._pth / sys.path
:: regressions that otherwise surface only as a silent crash on the user's PC.
echo   Running import smoke test...
"%PY_DIR%\python.exe" -c "from scheduler_app.app import SchedulerApp"
if errorlevel 1 (
    echo   [FAIL] embedded Python cannot import scheduler_app.app
    echo          ^(check that python*._pth contains the ".." app-dir line^)
    set /a ERRORS+=1
) else (
    echo   [OK] embedded Python imports scheduler_app.app
)

set /a TOTAL=0
for /r "%DIST_DIR%" %%f in (*) do set /a TOTAL+=1
echo   Total files: %TOTAL%

echo.
if %ERRORS% GTR 0 (
    echo WARNING: %ERRORS% critical file^(s^) missing!
    pause
) else (
    echo ============================================
    echo  BUILD SUCCESSFUL!
    echo.
    echo  Output:     %DIST_DIR%\
    echo  Launcher:   %DIST_DIR%\Dersis.exe
    echo.
    echo  Next step:  iscc installer.iss
    echo ============================================
)
echo.
pause
