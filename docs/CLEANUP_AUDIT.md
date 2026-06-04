# DERSIS — Cleanup Audit

Classification of every file in the repository with evidence-based cleanup decisions.

---

## Classification Legend

| Category | Description |
|---|---|
| 1. Critical runtime | Required for the app to run |
| 2. Build/installer critical | Required for packaging/distribution |
| 3. Important documentation | Valuable technical/product docs |
| 4. Keep test | High-value test with strong regression protection |
| 5. Delete test | Low-value, narrow, debug, or superseded test |
| 6. Optional but valid | Not strictly needed but has a purpose |
| 7. Legacy/duplicate/obsolete | Superseded or no longer relevant |
| 8. Suspicious / needs confirmation | Uncertain, archive rather than delete |

---

## Critical Runtime Files (KEEP — Do Not Touch)

| Path | Category | Notes |
|---|---|---|
| `scheduler_gui.py` | 1 | Primary entry point |
| `requirements.txt` | 1 | Dependency manifest |
| `scheduler_app/__init__.py` | 1 | Package init + backward-compat import shims |
| `scheduler_app/plans.py` | 1 | Tier/plan configuration |
| `scheduler_app/core/*.py` (all 21 files) | 1 | Scheduling engine — the heart of the app |
| `scheduler_app/ui/*.py` (all 17 files) | 1 | PyQt6 GUI layer |
| `scheduler_app/data_io/*.py` (all 5 files) | 1 | Import/export pipeline |
| `scheduler_app/auth/*.py` (all 6 files) | 1 | Authentication & licensing |
| `scheduler_app/learning/*.py` (all 3 files) | 1 | Preference learning |
| `scheduler_app/storage/*.py` (all 2 files) | 1 | Encrypted persistence |
| `scheduler_app/assets/*` | 1 | App icons (used by UI, build, installer) |
| `flags/*.png` (22 files) | 1 | Language selection flag icons |

---

## Build / Installer Files

| Path | Category | Action | Evidence |
|---|---|---|---|
| `build_embed.bat` | 2 | **KEEP** | Recommended build method per BUILD.md |
| `build_nuitka.bat` | 2 | **KEEP** | Alternative build method, documented in BUILD.md |
| `installer.iss` | 2 | **KEEP** | Inno Setup installer script |
| `installer/LICENSE.txt` | 2 | **KEEP** | Referenced by installer.iss |
| `installer/wizard_image.bmp` | 2 | **KEEP** | Referenced by installer.iss |
| `installer/wizard_small_image.bmp` | 2 | **KEEP** | Referenced by installer.iss |
| `installer/create_wizard_images.py` | 6 | **KEEP** | Generates wizard BMPs from logo; useful for rebuild |
| `verify_deps.py` | 2 | **KEEP** | Called by build_embed.bat and build_nuitka.bat |
| `build.bat` | 7 | **ARCHIVE** | Legacy PyInstaller method; superseded by build_embed.bat. Not referenced by other build scripts or installer.iss. BUILD.md recommends build_embed.bat. |

---

## Documentation

| Path | Category | Action | Evidence |
|---|---|---|---|
| `README.md` | 3 | **KEEP** | User-facing project overview |
| `BUILD.md` | 3 | **KEEP** | Build & packaging guide |
| `docs/dersis.png` | 2 | **KEEP** | App logo; used by build scripts and installer |
| `docs/STRUCTURE.md` | 3 | **KEEP** | Created in analysis phase |
| `docs/APP_OVERVIEW.md` | 3 | **KEEP** | Created in analysis phase |
| `docs/FEATURES.md` | 3 | **KEEP** | Created in analysis phase |
| `docs/CONTEXT.md` | 3 | **KEEP** | Created in analysis phase |
| `docs/RUN_STATUS.md` | 3 | **KEEP** | Created in analysis phase |
| `docs/runtime_issues.md` | 3 | **KEEP** | Created in analysis phase |
| `docs/md` | 7 | **DELETE** | Contains only "sadf" — empty/garbage placeholder |

---

## CI/CD

| Path | Category | Action |
|---|---|---|
| `.github/workflows/claude.yml` | 6 | **KEEP** |
| `.github/workflows/claude-code-review.yml` | 6 | **KEEP** |
| `.gitignore` | 1 | **KEEP** |

---

## Test Files — Detailed Analysis

### KEEP (High Value)

| Path | Lines | Category | Reason |
|---|---|---|---|
| `test_release_audit.py` | 788 | 4 | Comprehensive release audit covering all 10 critical paths: startup, scheduler, placement, add/edit/bulk, setup, persistence, import/export, search/filter, translation, deep regression. Self-contained fixtures. Highest-value test in the repo. |
| `test_workflow.py` | 251 | 4 | Tests the core SchedulingWorkflow orchestration layer directly: snapshot/restore, auto-place, batch scheduling, drop validation, class editing. Self-contained fixtures. Tests the critical UI-free business logic boundary. |

### DELETE (Narrow Phase Tests — Superseded by test_release_audit.py)

| Path | Lines | Category | Reason |
|---|---|---|---|
| `conftest.py` | 13 | 5 | Shared fixture importing `build_test_state()` from test_optimizer_debug.py. Only used by test_optimizer_debug.py. The two kept tests (test_release_audit.py, test_workflow.py) have their own fixtures and do not use this. |
| `test_optimizer_debug.py` | 2147 | 5 | Massive debug integration test. Contains `build_test_state()` which is only consumed by conftest.py. All 34 test functions use the conftest `state` fixture. Exercises every optimization pipeline function but is a debug/development artifact — all critical paths are covered more cleanly by test_release_audit.py. Name contains "debug". |
| `test_phase1_1_class_uid.py` | 241 | 5 | Narrow phase test: validates class_uid (UUID) identity. This specific behavior is covered by test_release_audit.py which uses cls_key() and snapshot/restore extensively. |
| `test_phase1_2_improve_only.py` | 174 | 5 | Narrow phase test: improve_only protection level enforcement. Protection levels are tested in test_release_audit.py regression checks. |
| `test_phase1_3_apply_validation.py` | 137 | 5 | Narrow phase test: apply_reschedule hard constraint validation. Workflow validation is covered by test_workflow.py. |
| `test_phase1_4_opt_lock.py` | 79 | 5 | Narrow phase test: optimization lock flag (_optimizing). 79 lines testing a single boolean flag. Minimal protection value. |
| `test_phase2_1_stability.py` | 102 | 5 | Narrow phase test: stability penalty in PlacementScorer. Scorer behavior is exercised by test_release_audit.py's auto-placement tests. |
| `test_phase2_2_warmstart.py` | 157 | 5 | Narrow phase test: warm-start and iteration cap. Optimizer behavior covered by test_release_audit.py. |
| `test_phase3_4.py` | 165 | 5 | Narrow phase test: optimality and explainability. Both features are tested in test_release_audit.py. |
| `test_conflict_localization.py` | 101 | 5 | Narrow test: conflict message translation. Translation integrity is covered by test_release_audit.py section 9. |

### ARCHIVE (Moderate Value — Uncertain or Possibly Useful Later)

| Path | Lines | Category | Reason |
|---|---|---|---|
| `test_location_type.py` | 596 | 8 | Substantial test covering all 3 location types (face-to-face, online, lecturer_office) with UI integration via SchedulerApp. Broader than a typical phase test. Location types are a complex feature. However, test_release_audit.py covers location type models and logic. Archiving for potential reuse. |
| `tests/test_phase5_device.py` | 128 | 8 | Tests device fingerprinting module. Auth tests are phased development tests but cover the auth pipeline which is hard to test otherwise. Archiving the entire tests/ directory. |
| `tests/test_phase6_client.py` | 225 | 8 | Tests AuthClient HTTP client with mocked requests. |
| `tests/test_phase7_session.py` | 248 | 8 | Tests session persistence (save/load/clear/grace). |
| `tests/test_phase8_auth_gate.py` | 108 | 8 | Tests auth gate integration flow. |
| `tests/test_phase8_login_dialog.py` | 119 | 8 | Tests login dialog UI. |
| `tests/test_phase9_heartbeat.py` | 220 | 8 | Tests heartbeat thread behavior. |
| `tests/test_phase10_ui_integration.py` | 113 | 8 | Tests full UI integration. |
| `tests/test_phase11_version_check.py` | 177 | 8 | Tests version check logic. |
| `tests/test_stress_scheduler.py` | 607 | 8 | Stress testing the scheduler. |
| `tests/stress_test_scenario.py` | 384 | 8 | Stress test scenario utilities. |

---

## Legacy / Obsolete Files

| Path | Category | Action | Evidence |
|---|---|---|---|
| `scheduler.py` | 7 | **ARCHIVE** | Legacy standalone CLI scheduler (860 lines). Completely independent — not imported by any module, not referenced in any build script, not packaged by installer. Superseded by `scheduler_gui.py` + `scheduler_app/`. Only mentioned in docs/STRUCTURE.md and docs/CONTEXT.md as historical context. |
| `build.bat` | 7 | **ARCHIVE** | Legacy PyInstaller build script (75 lines). Uses different output path (`dist/Dersis/`) than current builds (`build/Dersis.dist/`). Not referenced by build_embed.bat, build_nuitka.bat, or installer.iss. BUILD.md recommends build_embed.bat. |
| `docs/md` | 7 | **DELETE** | Contains only "sadf" — accidental/placeholder file. Not documentation. |

---

## Cleanup Summary

| Action | Count | Details |
|---|---|---|
| **DELETE** | 12 files | 10 narrow/debug test files + conftest.py + docs/md |
| **ARCHIVE** | 14 files | scheduler.py, build.bat, test_location_type.py, entire tests/ directory (11 files) |
| **KEEP** | All remaining | Runtime, build, docs, 2 high-value tests |

### Lines Removed by Deletion
| File | Lines |
|---|---|
| test_optimizer_debug.py | 2,147 |
| test_phase1_1_class_uid.py | 241 |
| test_phase1_2_improve_only.py | 174 |
| test_phase1_3_apply_validation.py | 137 |
| test_phase1_4_opt_lock.py | 79 |
| test_phase2_1_stability.py | 102 |
| test_phase2_2_warmstart.py | 157 |
| test_phase3_4.py | 165 |
| test_conflict_localization.py | 101 |
| conftest.py | 13 |
| docs/md | 1 |
| **Total deleted** | **3,318 lines** |

### Lines Archived
| File(s) | Lines |
|---|---|
| scheduler.py | 860 |
| build.bat | 75 |
| test_location_type.py | 596 |
| tests/ directory (11 files) | 2,329 |
| **Total archived** | **3,860 lines** |

### Tests Remaining After Cleanup
| File | Lines | Coverage |
|---|---|---|
| test_release_audit.py | 788 | All 10 critical paths |
| test_workflow.py | 251 | Core workflow orchestration |
| **Total** | **1,039 lines** | Comprehensive regression + workflow |
