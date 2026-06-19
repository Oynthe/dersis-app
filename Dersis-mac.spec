# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the macOS build of DERSİS.

Produces a self-contained ``Dersis.app`` bundle. The ``build_mac.sh`` script
wraps this spec to also generate the .icns icon, optionally code-sign, and
package the bundle into a .dmg (and .zip).

Why PyInstaller? DERSİS is a PyQt6 desktop app. On Windows it is packaged with
embeddable-Python + Inno Setup; PyInstaller is the equivalent free, open-source,
no-Apple-Developer-membership path for producing a native ``.app`` on macOS.

Architecture:
    The target CPU architecture is taken from the ``DERSIS_TARGET_ARCH`` env var
    ("arm64", "x86_64"/"x64", or unset = host arch). We build natively per-arch
    rather than a universal2 binary because not all runtime wheels (notably
    ortools) reliably ship universal2 builds. Build arm64 on an Apple Silicon
    Mac and x86_64 on an Intel Mac (or via the matching GitHub Actions runner).
"""

import os

from PyInstaller.utils.hooks import collect_all

# ── Identity ────────────────────────────────────────────────────────────────
APP_DISPLAY_NAME = "DERSİS"      # shown to users (Turkish dotted capital İ)
APP_BUNDLE_NAME = "Dersis"       # filesystem-safe base name for .app/.dmg/.zip
BUNDLE_ID = "com.emreuygun.dersis"

# ── Target architecture ───────────────────────────────────────────────────────
_arch_env = os.environ.get("DERSIS_TARGET_ARCH", "").strip().lower()
if _arch_env in ("x64", "x86_64", "intel", "amd64"):
    target_arch = "x86_64"
elif _arch_env in ("arm64", "aarch64", "apple", "silicon"):
    target_arch = "arm64"
else:
    target_arch = None  # build for the host architecture


# ── Version (single source of truth: the VERSION file) ───────────────────────
def _read_version():
    try:
        with open(os.path.join(os.getcwd(), "VERSION"), encoding="utf-8") as fh:
            return fh.read().strip() or "0.0.0"
    except OSError:
        return "0.0.0"


APP_VERSION = _read_version()

# ortools ships native .so libraries and data files that PyInstaller's static
# analysis misses; collect everything explicitly so the CP-SAT solver works.
ortools_datas, ortools_binaries, ortools_hidden = collect_all("ortools")

# Runtime resources, mirroring the relative layout the app expects:
#   <root>/flags/*.png            -> flag icons (scheduler_app/ui/icons.py)
#   <root>/docs/dersis.png        -> window/branding icon (scheduler_app/ui/app.py)
#   <root>/VERSION                -> read by scheduler_app/_version.py
#   <root>/scheduler_app/assets/  -> static icons
datas = [
    ("flags", "flags"),
    ("docs/dersis.png", "docs"),
    ("VERSION", "."),
    ("scheduler_app/assets", "scheduler_app/assets"),
] + ortools_datas

hiddenimports = [
    "PyQt6",
    "cryptography",
    "openpyxl",
    "pandas",
    "reportlab",
    "packaging",
    "deepdiff",
] + ortools_hidden

block_cipher = None

a = Analysis(
    ["scheduler_gui.py"],
    pathex=[],
    binaries=ortools_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PySide2", "PySide6"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_BUNDLE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=target_arch,
    codesign_identity=os.environ.get("DERSIS_CODESIGN_IDENTITY") or None,
    entitlements_file=os.environ.get("DERSIS_ENTITLEMENTS") or None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_BUNDLE_NAME,
)

app = BUNDLE(
    coll,
    name=f"{APP_BUNDLE_NAME}.app",
    icon=os.environ.get("DERSIS_ICNS") or None,
    bundle_identifier=BUNDLE_ID,
    version=APP_VERSION,
    info_plist={
        "CFBundleDisplayName": APP_DISPLAY_NAME,
        "CFBundleName": APP_DISPLAY_NAME,
        "CFBundleShortVersionString": APP_VERSION,
        "CFBundleVersion": APP_VERSION,
        # Education category (App Store / Launchpad / Finder grouping).
        "LSApplicationCategoryType": "public.app-category.education",
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        # The UI ships a light-only stylesheet; pin Aqua so it stays readable
        # when the OS is in dark mode (mirrors apply_light_palette()).
        "NSRequiresAquaSystemAppearance": True,
        "NSHumanReadableCopyright": "Copyright (c) Uygun",
    },
)
