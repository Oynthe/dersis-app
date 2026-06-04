# DERSİS Versioning Strategy

## Status: Implemented

---

## Single Source of Truth

The application version is defined in one place: the `VERSION` file at the repository root.

```
VERSION
```

Contains a bare semver string, e.g. `1.0.0`.

---

## How It Flows

```
VERSION (repo root, plain text)
    ↓ build_embed.bat reads it
    ↓ Copies VERSION to build/Dersis.dist/
    ↓ Generates build/version.iss (#define AppVersion "X.Y.Z")
    ↓
scheduler_app/_version.py reads VERSION at runtime → __version__
    ↓
scheduler_app/ui/bug_report.py: APP_VERSION = __version__ (shown in reports)
ui/app.py: About dialog shows __version__
installer.iss: includes build/version.iss → Windows metadata
```

## Where Version Was Previously Hardcoded (Now Unified)

| Old Location | Old Value | New Behavior |
|-------------|-----------|-------------|
| `scheduler_gui.py` | `APP_VERSION = '1.0.0'` | Consumers now import `__version__` from `_version.py` |
| `build_embed.bat:11` | `set APP_VERSION=1.0.0` | Now reads from `VERSION` file |
| `installer.iss:20` | `#define AppVersion "1.0.0"` | Now includes `build/version.iss` |

## Runtime Version Reading

`scheduler_app/_version.py` reads the `VERSION` file with this fallback chain:

1. `../VERSION` relative to `_version.py` (repo root or dist root)
2. `./VERSION` in the package directory
3. `VERSION` in the current working directory
4. Falls back to `"0.0.0"` (indicates broken build)

No server dependency. The version is embedded in the build artifact.

## Version Comparison

The `packaging` library (PEP 440 / semver compliant) is available for any version
comparison the app needs — for example, ordering tags or sorting release strings:

- `1.2.0 > 1.1.0` (standard comparison)
- `1.2.0-beta.1 < 1.2.0` (pre-release is lower)
- `1.2.0-rc.1 > 1.2.0-beta.2` (RC > beta)
- Falls back to simple tuple comparison if `packaging` is unavailable.

The app is fully offline and never checks any server for a newer version.

## Git Tag Convention

- Tags: `vX.Y.Z` (e.g., `v1.2.0`)
- Tag version must match `VERSION` file content (without `v` prefix).
- Pre-release tags: `v1.2.0-beta.1`, `v1.2.0-rc.1`.

## Release Workflow

1. Edit `VERSION` → `1.2.0`
2. Commit: `git commit -m "Bump version to 1.2.0"`
3. Tag: `git tag -a v1.2.0 -m "Release 1.2.0"`
4. Build: `build_embed.bat` → reads VERSION, generates build artifacts
5. Install: `iscc installer.iss` → produces `Dersis_Setup_v1.2.0.exe`
6. Create GitHub Release with tag `v1.2.0`, upload installer

## Installer Output Naming

```
Output/Dersis_Setup_v{VERSION}.exe
```

The version is embedded in the installer filename via `installer.iss`:
```iss
OutputBaseFilename=Dersis_Setup_v{#AppVersion}
```
