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

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

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
#   reportlab's bundled faces  -> the PDF export resolves a font by name at
#                                 runtime; hiddenimports collects modules, not
#                                 the .ttf/.pfb files sitting beside them.
datas = [
    ("flags", "flags"),
    ("docs/dersis.png", "docs"),
    ("VERSION", "."),
    ("scheduler_app/assets", "scheduler_app/assets"),
] + ortools_datas + collect_data_files("reportlab")

# ST-ARCH-009: scheduler_app must be collected by name, not by import analysis.
#
# scheduler_app/__init__.py installs a sys.meta_path finder that maps 32 flat
# aliases onto real modules, and scheduler_gui.py imports through three of them:
# `scheduler_app.app`, `scheduler_app.first_run` and `scheduler_app.translations`
# are aliases with NO FILE of that name. PyInstaller's modulegraph records
# `from pkg import name` on a non-existent submodule as an attribute rather than
# a missing module, so it warns about nothing and collects nothing.
#
# Measured with PyInstaller's own modulegraph 6.22.2 against this spec before
# this line existed: 13 of 58 scheduler_app modules collected, 45 dropped, 0
# warnings emitted — the 45 including ui/app.py (the main window) and
# i18n/translations.py (the entire translation table). A real launch imports 37
# modules, 24 of them absent from the bundle. The .dmg built green and could not
# open its window.
#
# collect_submodules walks the package on disk, so it sees all 58 regardless of
# how they are imported. tests/test_packaging_manifest.py pins this.
hiddenimports = [
    "PyQt6",
    "cryptography",
    "openpyxl",
    "pandas",
    "reportlab",
    "packaging",
    "deepdiff",
] + collect_submodules("scheduler_app") + ortools_hidden

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
