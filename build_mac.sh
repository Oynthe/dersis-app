#!/usr/bin/env bash
# =============================================================================
#  DERSİS — macOS Build & Packaging
#
#  Builds a native `Dersis.app` with PyInstaller and packages it into a
#  user-friendly `.dmg` (drag-to-Applications) plus an optional `.zip`.
#
#  This is the macOS counterpart to the Windows build (build_embed.bat +
#  installer.iss). It uses only free, open-source tooling and macOS built-ins
#  (sips/iconutil/hdiutil/ditto) — no Apple Developer Program membership is
#  required for a local build.
#
#  Usage:
#      ./build_mac.sh                 # build for the host architecture
#      ./build_mac.sh arm64           # build for Apple Silicon
#      ./build_mac.sh x64             # build for Intel
#      DERSIS_SKIP_ZIP=1 ./build_mac.sh   # skip the secondary .zip output
#
#  Optional code signing / notarization (see docs/MACOS.md):
#      DERSIS_CODESIGN_IDENTITY="Developer ID Application: NAME (TEAMID)" \
#          ./build_mac.sh arm64
#
#  Output (in dist/):
#      Dersis.app
#      Dersis-<version>-mac-<arch>.dmg
#      Dersis-<version>-mac-<arch>.zip   (unless DERSIS_SKIP_ZIP=1)
# =============================================================================
set -euo pipefail

# ── Must run on macOS ────────────────────────────────────────────────────────
if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "ERROR: build_mac.sh must be run on macOS (uname=$(uname -s))." >&2
    echo "       The Windows build uses build_embed.bat + installer.iss instead." >&2
    exit 1
fi

cd "$(dirname "$0")"

APP_BUNDLE_NAME="Dersis"          # filesystem-safe name (.app / .dmg / .zip)
APP_DISPLAY_NAME="DERSİS"         # shown to users
SPEC="Dersis-mac.spec"

# ── Resolve target architecture ──────────────────────────────────────────────
ARG_ARCH="${1:-}"
case "$(printf '%s' "$ARG_ARCH" | tr '[:upper:]' '[:lower:]')" in
    arm64|aarch64|apple|silicon) export DERSIS_TARGET_ARCH="arm64"; ARCH_LABEL="arm64" ;;
    x64|x86_64|intel|amd64)      export DERSIS_TARGET_ARCH="x86_64"; ARCH_LABEL="x64" ;;
    "")
        # Default to the host architecture.
        HOST="$(uname -m)"
        if [[ "$HOST" == "arm64" ]]; then
            export DERSIS_TARGET_ARCH="arm64"; ARCH_LABEL="arm64"
        else
            export DERSIS_TARGET_ARCH="x86_64"; ARCH_LABEL="x64"
        fi
        ;;
    *)
        echo "ERROR: unknown architecture '$ARG_ARCH' (use arm64 or x64)." >&2
        exit 1
        ;;
esac

# ── Version (single source of truth) ─────────────────────────────────────────
APP_VERSION="$(tr -d '[:space:]' < VERSION)"
if [[ ! "$APP_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "ERROR: VERSION '$APP_VERSION' is not valid semver (expected X.Y.Z)." >&2
    exit 1
fi

echo "============================================"
echo " DERSİS — macOS Build"
echo " Version:      $APP_VERSION"
echo " Architecture: $DERSIS_TARGET_ARCH ($ARCH_LABEL)"
echo " Host:         $(uname -m)"
echo "============================================"
echo

PY="${PYTHON:-python3}"

# ── Step 1: Build-time dependencies ──────────────────────────────────────────
echo "[1/5] Installing build dependencies..."
"$PY" -m pip install --upgrade pip >/dev/null
"$PY" -m pip install -r requirements-build-mac.txt
echo "  [OK] dependencies ready"
echo

# ── Step 2: Generate the .icns icon from the PNG app icon ────────────────────
# macOS app bundles want an .icns; we derive one from the existing PNG using the
# built-in sips + iconutil so no icon binary needs to live in the repo.
echo "[2/5] Generating app icon (.icns)..."
ICON_SRC="scheduler_app/assets/app_icon.png"
ICONSET="build/Dersis.iconset"
ICNS_OUT="build/Dersis.icns"
rm -rf "$ICONSET" "$ICNS_OUT"
mkdir -p "$ICONSET"
if [[ -f "$ICON_SRC" ]] && command -v sips >/dev/null && command -v iconutil >/dev/null; then
    for sz in 16 32 64 128 256 512; do
        sips -z "$sz" "$sz"        "$ICON_SRC" --out "$ICONSET/icon_${sz}x${sz}.png"       >/dev/null
        sips -z $((sz*2)) $((sz*2)) "$ICON_SRC" --out "$ICONSET/icon_${sz}x${sz}@2x.png"   >/dev/null
    done
    iconutil -c icns "$ICONSET" -o "$ICNS_OUT"
    export DERSIS_ICNS="$PWD/$ICNS_OUT"
    echo "  [OK] $ICNS_OUT"
else
    echo "  [WARN] sips/iconutil or source icon unavailable — building without a custom icon"
fi
echo

# ── Step 3: Build the .app with PyInstaller ──────────────────────────────────
echo "[3/5] Building Dersis.app with PyInstaller..."
rm -rf "build/$APP_BUNDLE_NAME" "dist/$APP_BUNDLE_NAME.app"
"$PY" -m PyInstaller --noconfirm --clean "$SPEC"
APP_PATH="dist/$APP_BUNDLE_NAME.app"
if [[ ! -d "$APP_PATH" ]]; then
    echo "ERROR: PyInstaller did not produce $APP_PATH" >&2
    exit 1
fi
echo "  [OK] $APP_PATH"
echo

# ── Step 4: Code sign (ad-hoc by default; Developer ID if provided) ──────────
# Apple Silicon refuses to launch unsigned binaries, so we always apply at
# least an ad-hoc signature ("-"). If DERSIS_CODESIGN_IDENTITY is set, we sign
# with that real identity instead (a prerequisite for notarization).
echo "[4/5] Code signing..."
SIGN_ID="${DERSIS_CODESIGN_IDENTITY:--}"
if codesign --force --deep --options runtime --sign "$SIGN_ID" "$APP_PATH" 2>/dev/null; then
    if [[ "$SIGN_ID" == "-" ]]; then
        echo "  [OK] ad-hoc signed (unsigned distribution — see Gatekeeper note in docs/MACOS.md)"
    else
        echo "  [OK] signed with: $SIGN_ID"
    fi
else
    # --options runtime can fail for ad-hoc signing on older toolchains; retry plain.
    codesign --force --deep --sign "$SIGN_ID" "$APP_PATH" || true
    echo "  [OK] signed (basic)"
fi
echo

# ── Step 5: Package into .dmg (+ optional .zip) ──────────────────────────────
echo "[5/5] Packaging distributables..."
DMG_OUT="dist/${APP_BUNDLE_NAME}-${APP_VERSION}-mac-${ARCH_LABEL}.dmg"
ZIP_OUT="dist/${APP_BUNDLE_NAME}-${APP_VERSION}-mac-${ARCH_LABEL}.zip"
rm -f "$DMG_OUT" "$ZIP_OUT"

# Build a staging folder with the app + an /Applications symlink so the DMG
# opens to the familiar "drag DERSİS into Applications" layout.
STAGE="build/dmg-stage-${ARCH_LABEL}"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "$APP_PATH" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

# Volume name kept ASCII-safe for cross-tool reliability; the app inside still
# displays as "DERSİS".
hdiutil create \
    -volname "DERSIS $APP_VERSION" \
    -srcfolder "$STAGE" \
    -ov -format UDZO \
    "$DMG_OUT" >/dev/null
echo "  [OK] $DMG_OUT"

if [[ "${DERSIS_SKIP_ZIP:-0}" != "1" ]]; then
    # ditto preserves the bundle's resource forks / signature inside the zip.
    ditto -c -k --keepParent "$APP_PATH" "$ZIP_OUT"
    echo "  [OK] $ZIP_OUT"
fi
rm -rf "$STAGE"
echo

echo "============================================"
echo " BUILD SUCCESSFUL"
echo
echo " App:  $APP_PATH"
echo " DMG:  $DMG_OUT"
if [[ "${DERSIS_SKIP_ZIP:-0}" != "1" ]]; then
echo " ZIP:  $ZIP_OUT"
fi
echo
echo " Distributed unsigned/ad-hoc? Users may need to right-click → Open,"
echo " or allow it via System Settings → Privacy & Security. See docs/MACOS.md."
echo "============================================"
