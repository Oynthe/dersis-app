# Dersis — Build & Packaging Guide

## Overview

Dersis can be packaged two ways. Both produce the same `build\Dersis.dist\` folder, and both use the same Inno Setup installer.

| Method | Script | Speed | Complexity |
|--------|--------|-------|------------|
| **Embeddable Python** (recommended) | `build_embed.bat` | ~2 min | Low |
| **Nuitka compilation** | `build_nuitka.bat` | 5-15 min | High |

The **embeddable Python** method bundles a portable Python runtime + all packages + your source code. No compilation needed. Recommended for most use cases.

The **Nuitka** method compiles Python to native C code. Produces a smaller output and hides source code, but takes longer and can be finicky with dependency discovery.

## Dependencies

### Dependency Files

| File | Purpose | When to use |
|------|---------|-------------|
| `requirements.txt` | Direct runtime dependencies with minimum versions | Local development, `pip install -r requirements.txt` |
| `requirements-lock.txt` | Pinned exact versions for reproducible installs | CI/CD builds, Nuitka builds |
| `requirements-build.txt` | Runtime deps + Nuitka + Pillow | Building installers locally |
| `requirements-dev.txt` | Runtime deps + pytest | Optional dev tooling |

### Local Developer Setup

```bat
:: Create a virtual environment (recommended)
python -m venv .venv
.venv\Scripts\activate

:: Install runtime dependencies
pip install -r requirements.txt
```

### Refreshing the Lock File

When dependencies are added or updated, regenerate the lock file from a clean environment:

```bat
python -m venv .venv-lock
.venv-lock\Scripts\pip install -r requirements.txt
.venv-lock\Scripts\pip freeze > requirements-lock.txt
:: Re-add the header comment
```

The lock file should be regenerated on Windows for full accuracy.

### GitHub Actions

- **CI workflow** (`ci.yml`): Installs from `requirements.txt` (Linux runner). There are no test files; CI runs version, build-file, and import-smoke checks only.
- **Build workflow** (`build-installer.yml`):
  - Embed method: `build_embed.bat` handles its own dependency installation
  - Nuitka method: Installs from `requirements-lock.txt` for pinned reproducibility

## Required Tools

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.10+ | Build environment |
| Inno Setup | 6.x | Windows installer |
| curl | Any | Downloading Python embeddable (embed method) |
| Nuitka | Latest | Only if using Nuitka method |

**Inno Setup**: Download from https://jrsoftware.org/isinfo.php (add `iscc` to PATH)

## Quick Start (Recommended)

```bat
:: 1. Build
build_embed.bat

:: 2. Create installer
iscc installer.iss
```

Output: `Output\Dersis_Setup.exe` — a single installer that works on any Windows 10/11 x64 machine with zero external dependencies.

## Build Method 1: Embeddable Python (Recommended)

```bat
build_embed.bat
```

This will:
1. Download Python 3.11.9 embeddable distribution from python.org (~10MB)
2. Set up pip inside the embeddable Python
3. Install all runtime dependencies into it
4. Verify all packages are importable
5. Copy the app source, assets, flags, and logo
6. Create a `Dersis.exe` launcher wrapper
7. Output to `build\Dersis.dist\`

The result is fully self-contained — it includes its own Python runtime, all packages, and all app files.

## Build Method 2: Nuitka

```bat
build_nuitka.bat
```

This will:
1. Install Nuitka and all dependencies (direct + transitive)
2. Verify all required packages via `verify_deps.py`
3. Compile into a standalone native directory
4. Output to `build\Dersis.dist\`

## Creating the Installer

After either build method:

```bat
iscc installer.iss
```

Output: `Output\Dersis_Setup.exe`

### VC++ Redistributable (for clean machines)

The installer optionally bundles the Visual C++ Redistributable, which Python and Qt need on clean Windows machines. To include it:

1. Download from https://aka.ms/vs/17/release/vc_redist.x64.exe
2. Place it in `installer\vc_redist.x64.exe`
3. The installer will silently install it before launching the app

If the file is not present, the installer skips this step. Most Windows 10/11 machines already have it.

### Regenerating Installer Branding (one-time)

```bat
pip install Pillow
python installer\create_wizard_images.py
```

Only needed if `docs/dersis.png` changes.

## What Gets Packaged

### Runtime Resources

| Resource | Source | Purpose |
|----------|--------|---------|
| App icons | `scheduler_app/assets/` | Window icon, taskbar, shortcuts |
| Flag icons (22) | `flags/` | Language selection UI |
| App logo | `docs/dersis.png` | Branding and installer wizard images |

### Installer Assets

| File | Purpose |
|------|---------|
| `installer/LICENSE.txt` | License agreement shown by the installer |
| `installer/wizard_image.bmp` | Left panel branding image |
| `installer/wizard_small_image.bmp` | Top-right icon |
| `installer/vc_redist.x64.exe` | VC++ runtime (optional) |
| `scheduler_app/assets/app_icon.ico` | Setup icon and shortcuts |

## Installer Languages

13 languages via Inno Setup built-in `.isl` files:

English, Turkish, German, French, Spanish, Italian, Dutch, Polish, Portuguese, Russian, Japanese, Korean, Danish

The app itself supports 20 languages (including Arabic, Persian, Hindi, Chinese, Swedish, etc.) via the language selection dialog on first run.

## Branding Assets Origin

| Asset | Source |
|-------|--------|
| App icon (`app_icon.ico`) | Generated from `docs/dersis.png` |
| Installer wizard images | Generated from `docs/dersis.png` via `installer/create_wizard_images.py` |
| Brand colors (#6e4f9e, #1e2058) | Application brand palette |

## Runtime Data Locations

```
%USERPROFILE%\Documents\Dersis\
    settings/    — App preferences (.egu encrypted)
    saves/       — Schedule files (.egu encrypted)
    learning/    — Preference model data
    logs/        — Crash logs
    exports/     — Exported files
    backups/     — Migration backups
    keys/        — Encryption keys
```

## CI/CD Workflows

GitHub Actions automate validation, building, and releases. See `.github/workflows/`.

| Workflow | File | Trigger | Purpose |
|----------|------|---------|---------|
| **CI** | `ci.yml` | Push / PR to `master` | Version checks, build-file checks, import-smoke checks (no test files) |
| **Build Installer** | `build-installer.yml` | Manual / `v*` tags | Full Windows build + installer + checksum |
| **Release** | `release.yml` | Manual / `v*` tags | Build + publish GitHub Release |

**Quick release:**
```bash
echo "1.2.0" > VERSION
git add VERSION && git commit -m "Bump version to 1.2.0"
git tag -a v1.2.0 -m "Release 1.2.0"
git push origin master --tags
```
The Release workflow builds the installer on Windows and creates a GitHub Release with `Dersis_Setup_v1.2.0.exe` and its `.sha256` checksum.

**Manual build:** Go to Actions → "Build Installer" → "Run workflow".

## Known Limitations

1. **Installer languages**: 13 of 20 app languages have installer UI translations. The 7 missing only affect the installer wizard — the installed app has all 20.

2. **Windows only**: Both build scripts are `.bat` files for Windows x64.

3. **Embed method ships source**: The embeddable Python method includes `.py` source files. Use Nuitka if source code protection is required.

4. **VC++ Redistributable**: Optional but recommended for clean machines. Download and place in `installer\vc_redist.x64.exe`.
