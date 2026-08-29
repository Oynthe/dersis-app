# DERSİS Release Workflow

## Status: Implemented

---

## Step-by-Step Release Process

### 1. Prepare the release

```bash
# Edit the VERSION file with the new version
echo "1.2.0" > VERSION

# Commit the version bump
git add VERSION
git commit -m "Bump version to 1.2.0"

# Create an annotated tag
git tag -a v1.2.0 -m "Release 1.2.0"

# Push
git push origin main --tags
```

### 2. Build (on Windows)

```bat
:: Build embeddable Python distribution
build_embed.bat

:: Create installer
iscc installer.iss
```

Output: `Output\Dersis_Setup_v1.2.0.exe`

### 3. Generate checksum

```bat
certutil -hashfile "Output\Dersis_Setup_v1.2.0.exe" SHA256 > "Output\Dersis_Setup_v1.2.0.exe.sha256"
```

### 4. Create GitHub Release

- Tag: `v1.2.0`
- Title: `DERSİS v1.2.0`
- Body: release notes in markdown
- Assets: upload both `Dersis_Setup_v1.2.0.exe` and `Dersis_Setup_v1.2.0.exe.sha256`

The pushed version tag (`v1.2.0`) triggers the Release workflow, which builds the
installer on a Windows runner and publishes the GitHub Release automatically. The
steps above describe the equivalent manual process.

---

## Version Bump Rules (SemVer)

| Increment | When | Example |
|-----------|------|---------|
| MAJOR | Breaking changes, file format incompatibility | `1.x → 2.0.0` |
| MINOR | New features, new constraint types, new exports | `1.0.x → 1.1.0` |
| PATCH | Bug fixes, performance, translation corrections | `1.0.0 → 1.0.1` |

## Asset Naming Convention

| Artifact | Pattern | Example |
|----------|---------|---------|
| Git tag | `vX.Y.Z` | `v1.2.0` |
| Installer | `Dersis_Setup_vX.Y.Z.exe` | `Dersis_Setup_v1.2.0.exe` |
| Checksum | `Dersis_Setup_vX.Y.Z.exe.sha256` | `Dersis_Setup_v1.2.0.exe.sha256` |
| GitHub Release title | `DERSİS vX.Y.Z` | `DERSİS v1.2.0` |

## Rollback Procedure

The app does not auto-update, so a bad release only affects users who download it
manually. To roll back:

1. **Quick fix**: Release a patch version (`1.2.1`), push as a new GitHub Release.
2. **Delete bad release**: Remove the GitHub Release / installer asset so it can no
   longer be downloaded.

---

## GitHub Actions CI/CD

Four workflows automate validation, building, and release publishing.
`tests/test_release_pipeline.py` checks the trigger sentences below against the
parsed workflows, so a lane that changes shape turns this document red.

### CI (`.github/workflows/ci.yml`)

Runs on every push to `main`, every version tag (`v*`), every pull request, and
manual dispatch.

**Checks performed:**
- VERSION file exists and is valid semver
- `_version.py` reads the correct version at runtime
- Tag matches VERSION (on tag pushes)
- Required build files exist (`build_embed.bat`, `installer.iss`, etc.)
- Core Python module imports succeed (import-smoke check)
- `mypy` over the Qt-free engine packages, gated at zero errors
- The regression suite: `pytest -m "not slow"` over `tests/`
- Installer script references are valid

A second job, **Scheduling invariants**, runs the `slow`-marked engine modules
(the hard-constraint oracle, the placement floors, the determinism pins and the
solver-work ratchet) that `-m "not slow"` skips.

The `v*` trigger is load-bearing: without it a tag push ran zero tests while the
release lane built and published from that same tag.

### Build Installer (`.github/workflows/build-installer.yml`)

Runs on `workflow_dispatch` (manual) only.

The `v*` trigger was removed by ST-SEC-001: one human tag used to start two
Windows builds and four macOS builds across three workflows, of which only
`release.yml`'s ever reached a release.

**How to trigger manually:**
1. Go to Actions → "Build Installer"
2. Click "Run workflow"
3. Select the branch and build method
4. Artifacts appear under the workflow run when complete

**What it does:**
1. Runs `build_embed.bat` on a Windows runner
2. Installs Inno Setup and runs `iscc installer.iss`
3. Verifies installer output exists and is >1 MB
4. Generates `Dersis_Setup_vX.Y.Z.exe.sha256` checksum
5. Uploads installer + checksum as workflow artifacts (90-day retention)

### Build macOS (`.github/workflows/build-macos.yml`)

Runs on `workflow_dispatch` (manual) only.

The on-demand macOS lane: `./build_mac.sh` on `macos-15` (arm64) and
`macos-15-intel` (x64), producing a `.dmg`, a `.zip` and a `.sha256` for each
architecture as workflow artifacts. It publishes nothing — `release.yml` builds
its own macOS artifacts for a release.

Each architecture is built on its matching runner because not all runtime wheels
(notably `ortools`) ship `universal2` binaries.

### Release (`.github/workflows/release.yml`)

Runs on version tags (`v*`) or manual dispatch.

**How to use with tags:**
```bash
git tag -a v1.2.0 -m "Release 1.2.0"
git push origin v1.2.0
```
This automatically builds the installer and creates a GitHub Release with both
the `.exe` and `.sha256` attached.

**Manual dispatch:**
1. Go to Actions → "Release"
2. Enter the tag name (e.g. `v1.2.0`) — the tag must already exist
3. Optionally mark as pre-release

**Jobs, and what gates what:**
1. **Test the tag** — `pytest -m "not slow"` on the exact ref being released.
2. **Build DERSİS Installer** — the Windows installer and its checksum.
3. **Build DERSİS macOS** — `.dmg` + `.zip` for arm64 and x64.
4. **Publish GitHub Release** — waits for (1) and (2); (3) is
   optional-but-expected. If a macOS runner is unavailable the release still
   ships Windows-only, with a `::warning::` in the log. If the suite or the
   Windows build fails, nothing is published.

**Artifacts produced:**
- `Dersis_Setup_vX.Y.Z.exe` — the installer
- `Dersis_Setup_vX.Y.Z.exe.sha256` — SHA-256 checksum (format: `hash  filename`)
- `Dersis-X.Y.Z-mac-arm64.dmg` / `-mac-x64.dmg` and their `.zip` and `.sha256`
  counterparts, when the macOS legs succeed

### Secrets & Configuration

| Secret | Required | Purpose |
|--------|----------|---------|
| `GITHUB_TOKEN` | Auto-provided | Used by release workflow to create releases |
| `CLAUDE_CODE_OAUTH_TOKEN` | For Claude workflows | Used by Claude Code Action workflows |

No additional secrets or environment variables are needed for CI or build workflows.

### Limitations

- Builds use the **embeddable Python** method only (not Nuitka)
- `vc_redist.x64.exe` is not bundled in CI builds (optional; most machines have it)
- Inno Setup is downloaded fresh each build run (~30s overhead)
- The `build_embed.bat` `pause` commands are bypassed via `echo. |` pipe
