# DERSİS on macOS — Build & Install Guide

DERSİS runs on macOS as a native `Dersis.app` bundle, packaged into a
`.dmg` (and an optional `.zip`). It is the macOS counterpart to the Windows
installer (`Dersis_Setup.exe`) — but you build it yourself: the Windows
installer is the only file any release has ever carried.

> **Naming note:** the app is **displayed** everywhere as **DERSİS** (Turkish
> dotted capital İ). Distributable **filenames** use the ASCII-safe `Dersis`
> spelling so they work reliably across operating systems and tools.

---

## For users — what to download

**Nothing: there is no ready-made Mac download.** Every DERSİS release so far
carries a single asset, the Windows installer `Dersis_Setup_v<version>.exe`, and
no macOS artifact has ever been attached to one. An earlier revision of this page
sent Mac users to the releases page for `.dmg` files that were not there — the
READMEs that link here have already been corrected, and so has this page.

On a Mac you produce the bundle yourself from this repository. It takes a few
minutes and needs no Apple Developer account:

```bash
./build_mac.sh
```

The script writes into `dist/`:

| File | What it is |
|------|------------|
| `dist/Dersis-<version>-mac-<arch>.dmg` | Drag-to-Applications disk image |
| `dist/Dersis-<version>-mac-<arch>.zip` | The same bundle zipped, for people who prefer not to use a disk image (skip it with `DERSIS_SKIP_ZIP=1`) |

`<arch>` is whichever architecture you built on: `arm64` on a 2020-or-newer
Apple Silicon Mac (M1/M2/M3/M4), `x64` on an older Intel Mac. **Not sure which
Mac you have?** Click the  Apple menu → **About This Mac**. If it says *Apple
M1/M2/M3/…* (or "Apple silicon") you are on Apple Silicon; if it says *Intel*,
you are not. You can only build each architecture on a machine of that
architecture — see [For developers — building
locally](#for-developers--building-locally) for the prerequisites and the full
build.

### Installing what you built

1. Open the `.dmg` in `dist/`.
2. Drag **DERSİS** onto the **Applications** folder.
3. Launch it from Applications or Launchpad.

---

## ⚠️ First launch — Gatekeeper / "unidentified developer"

> Because DERSİS is currently distributed outside the Mac App Store and may not
> yet be notarized by Apple, macOS may show a security warning on first launch.
> Users can open it via **System Settings → Privacy & Security**, or by
> **right-clicking the app and selecting Open**.

In practice, the first time you open the app:

- **Right-click (or Control-click) `DERSİS` → Open**, then confirm **Open** in
  the dialog. You only need to do this once; afterwards it opens normally.
- **Or:** open **System Settings → Privacy & Security**, scroll to the message
  that says *“DERSİS was blocked…”* and click **Open Anyway**.

If macOS reports the app is **“damaged and can’t be opened”** (this can happen
to unsigned apps downloaded via a browser, which adds a quarantine flag), remove
the quarantine attribute in Terminal:

```bash
xattr -dr com.apple.quarantine /Applications/Dersis.app
```

This is expected for free, unsigned/un-notarized software and does not mean the
app is unsafe — DERSİS is fully offline and requires no account or network.

---

## For developers — building locally

### Requirements

- A **Mac** (you must build macOS apps on macOS).
- **Python 3.10+** (3.11 recommended).
- Xcode Command Line Tools (`xcode-select --install`) — provides `codesign`.
  `sips`, `iconutil`, `hdiutil`, and `ditto` are built into macOS.

No Apple Developer Program membership is required for a local build.

### Build

```bash
# Build for your Mac's own architecture
./build_mac.sh

# Or target a specific architecture explicitly
./build_mac.sh arm64    # Apple Silicon
./build_mac.sh x64      # Intel
```

> You can only build **x64 on an Intel Mac** and **arm64 on an Apple Silicon
> Mac** — each architecture builds natively against its matching Python and
> wheels. CI does both by using one runner per architecture.

The script will:

1. Install build dependencies (`requirements-build-mac.txt`, which adds
   PyInstaller to the runtime requirements).
2. Generate `Dersis.icns` from `scheduler_app/assets/app_icon.png`.
3. Build `dist/Dersis.app` via PyInstaller (`Dersis-mac.spec`).
4. Apply an **ad-hoc** code signature (so Apple Silicon will run it).
5. Produce:
   - `dist/Dersis-<version>-mac-<arch>.dmg` (drag-to-Applications)
   - `dist/Dersis-<version>-mac-<arch>.zip` (set `DERSIS_SKIP_ZIP=1` to skip)

### Run it

```bash
open dist/Dersis.app
```

---

## Code signing & notarization (optional)

The default build is **unsigned** (ad-hoc only). It runs locally and can be
shared, but users will see the Gatekeeper prompt described above.

If you have an Apple Developer ID, you can sign with it by exporting an
identity before building:

```bash
DERSIS_CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
    ./build_mac.sh arm64
```

Optional related environment variables:

| Variable | Purpose |
|----------|---------|
| `DERSIS_CODESIGN_IDENTITY` | Real signing identity (omit for ad-hoc). |
| `DERSIS_ENTITLEMENTS` | Path to an entitlements plist (advanced). |

**Notarization** (uploading to Apple so Gatekeeper trusts the app silently) is
**not automated** here, because it requires a paid Apple Developer account and
secrets. Once you have signed with a Developer ID, you can notarize manually:

```bash
xcrun notarytool submit dist/Dersis-<version>-mac-<arch>.dmg \
    --apple-id you@example.com --team-id TEAMID --password APP_SPECIFIC_PW \
    --wait
xcrun stapler staple dist/Dersis-<version>-mac-<arch>.dmg
```

---

## Why two files instead of one "universal" app?

A single universal2 binary that runs on both architectures is technically
possible, but it requires every dependency to ship universal2 wheels. Some of
DERSİS's dependencies (notably **Google OR-Tools**) do not reliably provide
universal2 wheels, so a clean universal build is not guaranteed. Building one
`.dmg` per architecture is simpler, smaller per download, and more reliable.

If all dependencies gain universal2 wheels in the future, switching is a small
change: set `DERSIS_TARGET_ARCH=universal2` plumbing in `Dersis-mac.spec`.
