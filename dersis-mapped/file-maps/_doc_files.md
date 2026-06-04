# Group map: Documentation files under `docs/`

The in-repo `docs/` folder contains pre-existing markdown that was authoritative input for this mapping. Each entry below summarises the file's role and how to use it.

| File | Role | Use when |
|------|------|----------|
| `docs/APP_OVERVIEW.md` | User-facing overview (similar to `README.md` but more product-focused). | Orienting non-engineers. |
| `docs/STRUCTURE.md` | Authoritative repository structure with per-file responsibilities. | Quickly locating a file by purpose. |
| `docs/FEATURES.md` | Feature inventory. | Verifying whether a behaviour is intended or accidental. |
| `docs/CONTEXT.md` | Architecture context / design narrative. | Reading the "why" behind major decisions. |
| `docs/CLEANUP_AUDIT.md` | Audit trail of the cleanup pass. | Tracing where archived files came from. |
| `docs/CLEANUP_RESULTS.md` | Summary of cleanup outcomes. | Companion to CLEANUP_AUDIT. |
| `docs/RELEASE_CHECKLIST.md` | Pre-release manual QA checklist. | Before every release. |
| `docs/RUN_STATUS.md` | Run status notes / dev journal. | Optional. |
| `docs/release-workflow-plan.md` | Release pipeline design notes. | Reference when changing CI. |
| `docs/runtime_issues.md` | Known runtime issues log. | Triage when reproducing a bug. |
| `docs/versioning-strategy.md` | Versioning + tag conventions. | Before bumping VERSION. |
| `docs/dersis.png` | Brand logo (binary). | Used by the About dialog + installer image generator. |
| `BUILD.md` (repo root) | Build & packaging guide. | Building the installer. |
| `README.md` (repo root) | User-facing project overview. | First read. |

## Why grouped

These files are markdown documentation; mapping each individually would be redundant when the content is already self-describing. The integration points relevant to engineering tasks are surfaced in the higher-level maps (`02_PROJECT_OVERVIEW.md`, `03_ARCHITECTURE_MAP.md`, `10_BUILD_PACKAGING_RELEASE_MAP.md`, `13_NEXT_INSTANCE_ONBOARDING.md`).

## Conflicts with this map

If any in-repo doc disagrees with the code, the code wins. The pre-existing docs are useful but not all guaranteed to be in sync after every refactor. This mapping was built by reading the actual source, with the docs as a cross-check.
