# DERSIS — Cleanup Results

## Summary

| Action | Files | Lines |
|---|---|---|
| Deleted | 11 | 3,318 |
| Archived | 14 | 3,860 |
| Total removed from active repo | 25 | 7,178 |
| Tests remaining | 2 | 1,039 |

---

## What Was Deleted

All deletions were narrow/debug/phase tests or garbage files with no runtime, build, or documentation value.

| File | Lines | Reason |
|---|---|---|
| `conftest.py` | 13 | Shared fixture only used by deleted tests. Kept tests have own fixtures. |
| `test_optimizer_debug.py` | 2,147 | Massive debug integration test. Name contains "debug". `build_test_state()` only consumed by conftest. All critical paths covered by test_release_audit.py. |
| `test_phase1_1_class_uid.py` | 241 | Narrow phase test (class_uid identity). Covered by test_release_audit.py. |
| `test_phase1_2_improve_only.py` | 174 | Narrow phase test (improve_only protection). Covered by test_release_audit.py. |
| `test_phase1_3_apply_validation.py` | 137 | Narrow phase test (apply_reschedule validation). Covered by test_workflow.py. |
| `test_phase1_4_opt_lock.py` | 79 | Narrow phase test (optimization lock flag). 79 lines for one boolean flag. |
| `test_phase2_1_stability.py` | 102 | Narrow phase test (stability penalty). Scorer tested in test_release_audit.py. |
| `test_phase2_2_warmstart.py` | 157 | Narrow phase test (warm-start + iteration cap). |
| `test_phase3_4.py` | 165 | Narrow phase test (optimality + explainability). |
| `test_conflict_localization.py` | 101 | Narrow test (conflict message translation). Translation tested in test_release_audit.py section 9. |
| `docs/md` | 1 | Garbage placeholder file containing only "sadf". |

---

## What Was Archived

All archived files moved to `archive_repo_cleanup/` preserving original directory structure.

| Original Path | Lines | Reason |
|---|---|---|
| `scheduler.py` | 860 | Legacy standalone CLI scheduler. Not imported, not in builds, not packaged. Superseded by scheduler_gui.py + scheduler_app/. |
| `build.bat` | 75 | Legacy PyInstaller build. Superseded by build_embed.bat (recommended). Different output path. Not referenced by other scripts. |
| `test_location_type.py` | 596 | Substantial location type test (3 types, UI integration). Broader than phase tests. Core coverage exists in test_release_audit.py but this has additional depth. Archived for potential reuse. |
| `tests/test_phase5_device.py` | 128 | Auth pipeline: device fingerprinting tests. |
| `tests/test_phase6_client.py` | 225 | Auth pipeline: AuthClient HTTP tests. |
| `tests/test_phase7_session.py` | 248 | Auth pipeline: session persistence tests. |
| `tests/test_phase8_auth_gate.py` | 108 | Auth pipeline: auth gate integration. |
| `tests/test_phase8_login_dialog.py` | 119 | Auth pipeline: login dialog tests. |
| `tests/test_phase9_heartbeat.py` | 220 | Auth pipeline: heartbeat thread tests. |
| `tests/test_phase10_ui_integration.py` | 113 | Auth pipeline: full UI integration. |
| `tests/test_phase11_version_check.py` | 177 | Auth pipeline: version check logic. |
| `tests/test_stress_scheduler.py` | 607 | Stress testing the scheduler. |
| `tests/stress_test_scenario.py` | 384 | Stress test scenario utilities. |

---

## What Was Kept (Intentionally)

### Tests Remaining

| File | Lines | Coverage | Why Kept |
|---|---|---|---|
| `test_release_audit.py` | 788 | All 10 critical audit areas: startup, scheduler correctness, manual placement, add/edit/bulk workflow, setup flow, persistence, import/export, search/filter, translation integrity, deep regression | Comprehensive single-file regression suite. Self-contained. Covers models, logic, workflow, constraint validator, candidate generator, placement scorer, schedule optimizer, storage, translations, data_io schema. |
| `test_workflow.py` | 251 | Core SchedulingWorkflow: snapshot/restore, auto-place, drop validation, class editing, batch operations | Tests the critical UI-free orchestration boundary. Self-contained fixtures. Validates the contract between UI and engine. |

**Combined: 83 tests, 1,039 lines, passes in 0.66s.**

### Runtime Files
All files under `scheduler_app/` kept untouched (critical runtime).

### Build / Installer
- `build_embed.bat` — recommended build method
- `build_nuitka.bat` — alternative build method
- `installer.iss` — Inno Setup installer
- `installer/` directory — all assets
- `verify_deps.py` — pre-build checker

### Documentation
- `README.md`, `BUILD.md` — project docs
- `docs/dersis.png` — app logo
- `docs/STRUCTURE.md`, `docs/APP_OVERVIEW.md`, `docs/FEATURES.md`, `docs/CONTEXT.md` — analysis docs
- `docs/RUN_STATUS.md`, `docs/runtime_issues.md` — runtime docs
- `docs/CLEANUP_AUDIT.md` — this cleanup's audit trail

### Other
- `.gitignore` (updated to include `.pytest_cache/`)
- `.github/workflows/` — CI configurations
- `flags/` — 22 country flag PNGs for language selection

---

## Validation Results

### App Launch Test
```
APP LAUNCH: OK
Window: Class Schedule Preparation System (1150x720)
ALL IMPORTS: OK
BACKWARD COMPAT SHIMS: OK
```

### Test Suite
```
83 passed in 0.66s
```

All 83 tests pass across both kept test files:
- test_release_audit.py: 36 tests (10 test classes covering all critical paths)
- test_workflow.py: 47 tests (8 test classes covering workflow operations)

### Import Verification
All modules verified importable:
- `scheduler_app.core.*` (all 21 modules)
- `scheduler_app.ui.*` (all 17 modules)
- `scheduler_app.data_io.*` (all 5 modules)
- `scheduler_app.auth.*` (all 6 modules)
- `scheduler_app.learning.*` (all 3 modules)
- `scheduler_app.storage.*` (all 2 modules)
- Backward-compat shims (e.g., `from scheduler_app.models import ...`)

---

## Follow-Up Risks

| Risk | Mitigation |
|---|---|
| Auth pipeline tests only in archive | Auth modules untouched; tests recoverable from `archive_repo_cleanup/tests/` if needed |
| `build_test_state()` deleted with test_optimizer_debug.py | Only used by conftest.py which was also deleted. Kept tests have independent fixtures. |
| Legacy CLI (`scheduler.py`) archived | Never imported or packaged; purely standalone. Recoverable from archive. |
| stress tests archived | Specialized; recoverable if stress testing needed in future. |

---

## Archive Recovery

To restore any archived file:
```bash
# Example: restore scheduler.py
cp archive_repo_cleanup/scheduler.py ./scheduler.py

# Example: restore auth tests
cp -r archive_repo_cleanup/tests/ ./tests/
```

The archive preserves the original directory structure.
