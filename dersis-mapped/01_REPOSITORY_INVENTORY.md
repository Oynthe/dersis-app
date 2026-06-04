# 01 — Repository Inventory

This is the complete, classified list of files in the repository (excluding the items recorded in `00_EXCLUSIONS.md`). Every file marked **mapped: yes** has a corresponding file under `file-maps/`; every other file has its role explained either here or in a higher-level map.

The "Map file" column shows the safe-encoded filename used inside `file-maps/`. Directory separators are encoded as `__`.

Repository root: `/home/user/dersis/`

## Top-level files

| Path | Type | Role | Mapped? | Criticality |
|------|------|------|---------|-------------|
| `README.md` | docs | User-facing project overview, install + usage | yes | supporting |
| `BUILD.md` | docs | Build & packaging guide | yes | supporting |
| `VERSION` | data | Single-source-of-truth app version (e.g. `1.0.0`) | yes | critical |
| `.gitignore` | config | Git ignore rules | yes | supporting |
| `requirements.txt` | config | Runtime Python deps (direct only) | yes | critical |
| `requirements-build.txt` | config | Build deps (nuitka, Pillow, ordered-set) | yes | critical |
| `requirements-dev.txt` | config | Dev deps (pytest) | yes | supporting |
| `requirements-lock.txt` | config | Pinned-version lock file | yes | supporting |
| `scheduler_gui.py` | source | Application entry point (offline GUI launcher; language gate → main window) | yes | **critical** |
| `verify_deps.py` | source | Pre-build dependency import-check script | yes | supporting |
| `build_embed.bat` | script | Recommended Windows installer build (embeddable Python) | yes | critical |
| `build_nuitka.bat` | script | Alternative Nuitka build | yes | supporting |
| `installer.iss` | config | Inno Setup installer script | yes | critical |

> The repository is a **fully offline desktop app**. There are no automated test files (`test_release_audit.py`, `test_workflow.py`, `tests/`, and `archive_repo_cleanup/` were removed); verification is manual + CI structural checks (see `11_TESTING_AND_QA_MAP.md`).

## `.github/workflows/`

| Path | Type | Role | Mapped? | Criticality |
|------|------|------|---------|-------------|
| `.github/workflows/ci.yml` | CI | Continuous-integration validation | yes | supporting |
| `.github/workflows/build-installer.yml` | CI | Windows installer build job | yes | critical |
| `.github/workflows/release.yml` | CI | GitHub Release publisher | yes | critical |
| `.github/workflows/claude.yml` | CI | Claude Code CI hook | yes | optional |
| `.github/workflows/claude-code-review.yml` | CI | Claude Code review hook | yes | optional |

## `installer/`

| Path | Type | Role | Mapped? | Criticality |
|------|------|------|---------|-------------|
| `installer/LICENSE.txt` | docs | DERSIS license and usage terms shown by installer | yes | critical |
| `installer/create_wizard_images.py` | source | Generates the Inno Setup wizard BMPs from logo | yes | supporting |
| `installer/wizard_image.bmp` | asset | 164×314 left panel image (binary) | no — binary asset | supporting |
| `installer/wizard_small_image.bmp` | asset | 55×55 top-right icon (binary) | no — binary asset | supporting |

## `docs/`

| Path | Type | Role | Mapped? | Criticality |
|------|------|------|---------|-------------|
| `docs/APP_OVERVIEW.md` | docs | User-facing app overview | yes | supporting |
| `docs/CLEANUP_AUDIT.md` | docs | Cleanup audit trail | yes | optional |
| `docs/CLEANUP_RESULTS.md` | docs | Cleanup result summary | yes | optional |
| `docs/CONTEXT.md` | docs | Architecture context | yes | supporting |
| `docs/FEATURES.md` | docs | Feature inventory | yes | supporting |
| `docs/RELEASE_CHECKLIST.md` | docs | Release checklist | yes | supporting |
| `docs/RUN_STATUS.md` | docs | Run status notes | yes | optional |
| `docs/STRUCTURE.md` | docs | Authoritative repo structure | yes | critical |
| `docs/release-workflow-plan.md` | docs | Release-workflow planning | yes | optional |
| `docs/runtime_issues.md` | docs | Runtime issues log | yes | optional |
| `docs/versioning-strategy.md` | docs | Versioning strategy | yes | supporting |
| `docs/dersis.png` | asset | App logo / installer image source | no — binary asset | supporting |

## `flags/`

22 PNG country flags used in the language selection dialog. Listed as a single group (one map file).

| Path pattern | Type | Role | Mapped? | Criticality |
|---|---|---|---|---|
| `flags/*.png` (22 files) | asset | UI flag icons for the language picker | yes (group map) | supporting |

## `scheduler_app/` — the application package

### Package roots

| Path | Type | Role | Mapped? | Criticality |
|------|------|------|---------|-------------|
| `scheduler_app/__init__.py` | source | Package init + back-compat import shim (meta-path finder) | yes | **critical** |
| `scheduler_app/_version.py` | source | Reads `VERSION` file (with fallbacks) | yes | critical |
| `scheduler_app/plans.py` | source | Tier/plan limits + feature flags + tier helpers | yes | critical |

### `scheduler_app/core/` — scheduling engine (~16k LOC)

| Path | Type | Role | Mapped? | Criticality |
|------|------|------|---------|-------------|
| `core/__init__.py` | source | Package marker | yes | optional |
| `core/constants.py` | source | Visual colors and cell dimensions | yes | supporting |
| `core/models.py` | source | Class/state dicts, protection levels, location types, validation | yes | **critical** |
| `core/logic.py` | source | Core scheduling logic + AI bridge | yes | **critical** |
| `core/workflow.py` | source | UI-free orchestration of scheduling operations | yes | **critical** |
| `core/constraint_validator.py` | source | Hard-constraint checks with O(1) occupancy lookups | yes | **critical** |
| `core/constraint_propagator.py` | source | Incremental constraint propagation cache | yes | supporting |
| `core/constraint_negotiator.py` | source | Infeasibility analysis + relaxation suggestions | yes | critical |
| `core/candidate_generator.py` | source | Generates valid placement candidates | yes | critical |
| `core/conflict_graph.py` | source | Class-class conflict graph for analysis | yes | supporting |
| `core/placement_scorer.py` | source | 14-weight soft scoring + look-ahead | yes | **critical** |
| `core/timetable_scorer.py` | source | Full-timetable quality scoring | yes | critical |
| `core/parallel_scorer.py` | source | Multi-process scoring pool | yes | supporting |
| `core/schedule_optimizer.py` | source | Greedy + LNS + CP-SAT pipeline | yes | **critical** |
| `core/schedule_analytics.py` | source | Quality grading (A–F) + insights | yes | critical |
| `core/schedule_impact_analyzer.py` | source | Non-invasive impact assessment | yes | supporting |
| `core/analytics.py` | source | Per-entity metrics (gaps, utilization) | yes | supporting |
| `core/explanation_engine.py` | source | Human-readable AI explanations | yes | critical |
| `core/lns_strategies.py` | source | LNS destroy/repair strategies | yes | critical |
| `core/cpsat_scheduler.py` | source | Google OR-Tools CP-SAT wrapper | yes | critical |
| `core/optimization_goals.py` | source | UI slider goals → internal weights | yes | supporting |

### `scheduler_app/ui/` — PyQt6 GUI (~36k LOC, ~22k of which is translations)

| Path | Type | Role | Mapped? | Criticality |
|------|------|------|---------|-------------|
| `ui/__init__.py` | source | Package marker | yes | optional |
| `ui/app.py` | source | Main window class `SchedulerApp` | yes | **critical** |
| `ui/dialogs.py` | source | All modal dialogs (setup, add class, bulk add, etc.) | yes | **critical** |
| `ui/renderer.py` | source | QGraphicsView timetable grid renderer | yes | **critical** |
| `ui/dashboard.py` | source | Analytics dashboard (charts, gauges, tabs) | yes | critical |
| `ui/widgets.py` | source | Reusable widgets (toast, multi-select, warning log) | yes | critical |
| `ui/cell_formatter.py` | source | Cell content assembly for display/export | yes | supporting |
| `ui/badge_formatter.py` | source | Protection-level badge styling | yes | supporting |
| `ui/day_keys.py` | source | Weekday key helpers + normalization | yes | supporting |
| `ui/icons.py` | source | Programmatic icon generation + flag PNG loader | yes | supporting |
| `ui/translations.py` | source | **22-language string table (~21,790 lines)** + `tr()` / `set_language()` | yes | **critical** |
| `ui/tier_translations.py` | source | Tier/upgrade dialog translations | yes | supporting |
| `ui/tier_enforcement.py` | source | Tier feature gating + upgrade dialog (offline: always unlocked) | yes | supporting |
| `ui/first_run.py` | source | First-run language gate + tutorial trigger | yes | critical |
| `ui/tutorial.py` | source | Interactive spotlight tutorial overlay | yes | supporting |
| `ui/bug_report.py` | source | Bug + crash report dialogs (compose a `mailto:`) | yes | critical |

### `scheduler_app/data_io/` — import/export

| Path | Type | Role | Mapped? | Criticality |
|------|------|------|---------|-------------|
| `data_io/__init__.py` | source | Public API re-exports | yes | supporting |
| `data_io/importer.py` | source | Excel import with schema validation | yes | critical |
| `data_io/exporter.py` | source | Excel/CSV/PDF export | yes | critical |
| `data_io/schema.py` | source | Localized workbook schema definitions | yes | supporting |
| `data_io/template.py` | source | Localized Excel template generator | yes | supporting |

> The former `scheduler_app/auth/` package (HTTP licensing client, encrypted session, heartbeat thread, device fingerprinting, auto-updater, version fetch) was **removed** in the offline conversion. The app makes no network calls.

### `scheduler_app/learning/`

| Path | Type | Role | Mapped? | Criticality |
|------|------|------|---------|-------------|
| `learning/__init__.py` | source | Package marker | yes | optional |
| `learning/feedback_logger.py` | source | Encrypted feedback log of user interactions | yes | supporting |
| `learning/preference_learner.py` | source | Online gradient weight adjustment | yes | supporting |

### `scheduler_app/storage/`

| Path | Type | Role | Mapped? | Criticality |
|------|------|------|---------|-------------|
| `storage/__init__.py` | source | Public re-export shim | yes | critical |
| `storage/storage.py` | source | Path management + AES-256-GCM `.egu` container | yes | **critical** |

### `scheduler_app/assets/`

| Path | Type | Role | Mapped? | Criticality |
|------|------|------|---------|-------------|
| `assets/__init__.py` | source | Defines `ASSETS_DIR` and `asset_path()` | yes | supporting |
| `assets/app_icon.ico` | asset | Windows icon | no — binary | supporting |
| `assets/app_icon.png` | asset | Default raster icon | no — binary | supporting |
| `assets/app_icon_{16,32,48,64,128,256}.png` | asset | Multi-res icons | no — binary | supporting |

## Totals

| Category | Count |
|----------|-------|
| Python source files (mapped) | 54 |
| Config/build/CI files (mapped) | 13 |
| Documentation files (mapped, including this folder's referents) | 12 |
| Asset files (binary; documented as a group) | 30 |

(Counts reflect the offline app after removal of the auth package, the login/account/update dialogs, and all test files.)

See `15_COVERAGE_MATRIX.md` for the per-file mapped/inspected/confidence matrix.
See `00_EXCLUSIONS.md` for what was deliberately *not* mapped.
