# Group map: Configuration / Build files

This single map covers the small configuration and build files listed below. Each entry includes role, why-it-matters, contents summary, integration points, and risks. These files don't have separate per-file maps because they are short and structurally simple.

---

## `VERSION`
- **Role**: Single source of truth for `__version__`. One-line semver string (currently `1.0.0`).
- **Critical**.
- **Read by**: `scheduler_app/_version.py`, both `.bat` build scripts, `.github/workflows/release.yml` (matches against tag).
- **Risks**: Mismatch with Git tag breaks the release workflow.

## `.gitignore`
- **Role**: Standard ignore list.
- **Supporting**.
- **Contents**: `__pycache__/`, `*.pyc/pyo`, `build/`, `dist/`, `*.dist/`, `*.egg-info/`, `.eggs/`, `*.spec`, `*.tmp`, `.tools/`, `.venv/`, `.pytest_cache/`, `Output/`, `*.exe`.
- **Notes**: `*.exe` ignores would also catch the installer output, which is intentional.

## `requirements.txt`
- **Role**: Direct runtime dependencies, minimum versions only.
- **Critical**.
- **Contents**: PyQt6 ≥ 6.5; cryptography ≥ 41; openpyxl ≥ 3.1; pandas ≥ 2.0; reportlab ≥ 4.0; ortools ≥ 9.7; packaging ≥ 21.0; deepdiff ≥ 6.0. (`requests` was removed in the offline conversion — the app makes no network calls.)

## `requirements-build.txt`
- **Role**: Build deps: includes `requirements.txt` + nuitka ≥ 2.0; ordered-set ≥ 4.1; Pillow ≥ 10.0.
- **Critical** for Nuitka builds.

## `requirements-dev.txt`
- **Role**: Dev deps: includes `requirements.txt` + pytest ≥ 7.0. (The repo currently ships no test files; pytest is retained only for future local tests.)
- **Supporting**.

## `requirements-lock.txt`
- **Role**: Pinned versions for reproducible installs.
- **Supporting**.
- **Notes**: Header includes regeneration command. Lock generated on 2026-04-03 from a clean venv. Includes both direct and transitive deps so Nuitka can statically discover everything.

## `build_embed.bat`
- **Role**: Recommended Windows build via embeddable Python.
- **Critical**.
- **Outputs**: `build\Dersis.dist\` with full Python runtime + app source + `version.iss`.
- **Process**: validate VERSION → download embeddable Python → enable site → install lock requirements → copy app source → emit launcher + `version.iss`.

## `build_nuitka.bat`
- **Role**: Alternative Nuitka build.
- **Supporting**.
- **Outputs**: `build\Dersis.dist\Dersis.exe` (compiled).
- **Process**: validate VERSION → `pip install -r requirements-build.txt` → `verify_deps.py` → Nuitka `--standalone --enable-plugin=pyqt6 --include-data-dir flags + docs --include-data-file VERSION` → write `version.iss`.

## `installer.iss`
- **Role**: Inno Setup installer script.
- **Critical**.
- **Defines**: AppName "Dersis", AppFullName, AppVersion (from `build\version.iss`), AppPublisher "Uygun". (`AppURL` is now blank and the `AppPublisherURL`/`AppSupportURL` lines were removed — the former remote URL is gone.)
- **Sources**: `build\Dersis.dist\*` → `{app}`.
- **Shortcuts**: Start Menu (+ optional Desktop).
- **License**: `installer\LICENSE.txt`.
- **Wizard images**: `installer\wizard_image.bmp`, `installer\wizard_small_image.bmp`.
- **Output**: `Output\Dersis_Setup.exe`.

## `installer/LICENSE.txt`
- **Role**: DERSIS license and usage terms shown by the installer.
- **Critical** (legal).
- **Scope**: DERSIS is free for individual users; institutional use (embedding, integration, deployment, customization, or official adoption) requires a paid license.

## `.github/workflows/ci.yml`
- **Role**: Continuous integration on push / PR to `master`.
- **Critical**.
- **Steps**: setup Python → install runtime deps → VERSION/semver checks → build-file existence checks → core-import smoke test. No automated test files are run (they were removed in the offline conversion).

## `.github/workflows/build-installer.yml`
- **Role**: Windows installer build (callable + dispatchable).
- **Critical**.
- **Process**: Windows runner → run `build_embed.bat` (default) or `build_nuitka.bat` → run `iscc installer.iss` → upload installer + SHA-256 as artefacts.

## `.github/workflows/release.yml`
- **Role**: GitHub Release publisher; triggered on `v*` tags or workflow_dispatch.
- **Critical**.
- **Process**: verify VERSION matches tag → reuse `build-installer.yml` → upload installer + checksum to a new GitHub Release.

## `.github/workflows/claude.yml`
- **Role**: Claude Code GitHub app hook (issue_comment, pull_request_review_comment, issues opened/assigned, pull_request_review submitted).
- **Optional**. Not part of the user-facing release pipeline.

## `.github/workflows/claude-code-review.yml`
- **Role**: Claude Code Review hook on PRs (opened, synchronize, ready_for_review, reopened).
- **Optional**.

---

## Why these are mapped as a group

Each is short (typically 10–300 lines), structurally simple, and consists of declarative content (key=value, dependency lines, workflow YAML). A dedicated per-file map for each would mostly repeat the file's content. The integration points and risks are documented in `10_BUILD_PACKAGING_RELEASE_MAP.md`.
