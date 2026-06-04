# DERSİS Release Checklist

Pre-release validation checklist for every DERSİS desktop build.

## Before Building

- [ ] `VERSION` file contains a valid `X.Y.Z` semver string
- [ ] Version in `VERSION` matches the intended release tag (e.g., `v1.2.0`)
- [ ] No uncommitted changes in the working tree
- [ ] CI is green on the commit being released (version, build-file, and import-smoke checks — there are no test files)

## Build

- [ ] Run `build_embed.bat` — verify it exits with no errors
- [ ] Verify `build\version.iss` contains the correct `#define AppVersion`
- [ ] Verify `build\Dersis.dist\VERSION` matches the root `VERSION`
- [ ] Run `iscc installer.iss` — verify it produces `Output\Dersis_Setup_vX.Y.Z.exe`
- [ ] Verify installer filename matches the VERSION (`Dersis_Setup_v1.2.0.exe`)

## Checksum

- [ ] Generate SHA-256: `certutil -hashfile Output\Dersis_Setup_vX.Y.Z.exe SHA256`
- [ ] Save to `Output\Dersis_Setup_vX.Y.Z.exe.sha256` (format: `hexhash  filename`)
- [ ] Double-check the checksum file content matches the generated hash

## GitHub Release (Automated)

Pushing a version tag triggers the Release workflow automatically:

- [ ] Create tag: `git tag -a vX.Y.Z -m "Release vX.Y.Z"`
- [ ] Push tag: `git push origin vX.Y.Z`
- [ ] Verify the Release workflow completes in Actions tab
- [ ] Verify GitHub Release was created with `.exe` and `.sha256` assets
- [ ] Edit release notes if needed (auto-generated from commits)
- [ ] Mark as latest release (not pre-release, not draft)

**Manual alternative:** Use Actions → Release → Run workflow with the tag name.

## Smoke Test

The app is fully offline — there is no login, update check, or network activity to verify.

- [ ] Install from the new installer on a clean machine (or VM)
- [ ] Verify the app opens directly into the main window (no login or account step)
- [ ] Verify the About dialog shows the correct version
- [ ] Create, save (encrypted `.egu`), and reload a schedule
- [ ] Run an auto-schedule / optimization and an export (Excel/CSV/PDF)
- [ ] Verify the in-app **Report Bug** button opens the email client (mailto)

## Rollback Plan

The app does not auto-update, so a bad release only reaches users who download it manually:

1. Investigate and fix the issue.
2. Release a patched version (bump `VERSION`, tag, and publish a new GitHub Release).
3. Remove the bad GitHub Release / installer asset so it can no longer be downloaded.
