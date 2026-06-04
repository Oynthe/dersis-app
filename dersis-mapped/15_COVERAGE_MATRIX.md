# 15 — Coverage Matrix

For every included file in the repository (excluding items recorded in `00_EXCLUSIONS.md`), this table shows whether the file was mapped, the corresponding file-map path, whether the file was actually inspected during this pass, and the confidence level.

## Top-level files

| File | mapped | dedicated map | inspected | confidence | notes |
|------|--------|---------------|-----------|------------|-------|
| `README.md` | yes | file-maps/_doc_files.md | yes | high | Group map. |
| `BUILD.md` | yes | file-maps/_doc_files.md | yes | high | Group map. |
| `VERSION` | yes | file-maps/_config_files.md | yes | high | Group map. |
| `.gitignore` | yes | file-maps/_config_files.md | yes | high | |
| `requirements.txt` | yes | file-maps/_config_files.md | yes | high | |
| `requirements-build.txt` | yes | file-maps/_config_files.md | yes | high | |
| `requirements-dev.txt` | yes | file-maps/_config_files.md | yes | high | |
| `requirements-lock.txt` | yes | file-maps/_config_files.md | yes | high | |
| `scheduler_gui.py` | yes | file-maps/scheduler_gui.py.md | yes | high | Offline entry point; detailed map. |
| `verify_deps.py` | yes | file-maps/verify_deps.py.md | yes | high | |
| `build_embed.bat` | yes | file-maps/_config_files.md | partial | medium | Mapped at description level, not script-by-script. |
| `build_nuitka.bat` | yes | file-maps/_config_files.md | partial | medium | Same. |
| `installer.iss` | yes | file-maps/_config_files.md | partial | medium | Same. |

(No test files remain — `test_release_audit.py`, `test_workflow.py`, the `tests/` package, and their file-maps were removed in the offline conversion. See `11_TESTING_AND_QA_MAP.md`.)

## `.github/workflows/`

| File | mapped | dedicated map | inspected | confidence | notes |
|------|--------|---------------|-----------|------------|-------|
| `.github/workflows/ci.yml` | yes | file-maps/_config_files.md | partial | medium | Group map. |
| `.github/workflows/build-installer.yml` | yes | file-maps/_config_files.md | partial | medium | |
| `.github/workflows/release.yml` | yes | file-maps/_config_files.md | partial | medium | |
| `.github/workflows/claude.yml` | yes | file-maps/_config_files.md | partial | medium | |
| `.github/workflows/claude-code-review.yml` | yes | file-maps/_config_files.md | partial | medium | |

## `installer/`

| File | mapped | dedicated map | inspected | confidence | notes |
|------|--------|---------------|-----------|------------|-------|
| `installer/LICENSE.txt` | yes | file-maps/_config_files.md | yes | high | |
| `installer/create_wizard_images.py` | yes | file-maps/installer__create_wizard_images.py.md | yes | high | |
| `installer/wizard_image.bmp` | no (binary) | — | n/a | n/a | Listed in 00_EXCLUSIONS. |
| `installer/wizard_small_image.bmp` | no (binary) | — | n/a | n/a | Listed in 00_EXCLUSIONS. |

## `docs/`

| File | mapped | dedicated map | inspected | confidence |
|------|--------|---------------|-----------|------------|
| `docs/APP_OVERVIEW.md` | yes | file-maps/_doc_files.md | partial | medium |
| `docs/CLEANUP_AUDIT.md` | yes | file-maps/_doc_files.md | partial | medium |
| `docs/CLEANUP_RESULTS.md` | yes | file-maps/_doc_files.md | partial | medium |
| `docs/CONTEXT.md` | yes | file-maps/_doc_files.md | partial | medium |
| `docs/FEATURES.md` | yes | file-maps/_doc_files.md | partial | medium |
| `docs/RELEASE_CHECKLIST.md` | yes | file-maps/_doc_files.md | partial | medium |
| `docs/RUN_STATUS.md` | yes | file-maps/_doc_files.md | partial | medium |
| `docs/STRUCTURE.md` | yes | file-maps/_doc_files.md | yes | high |
| `docs/release-workflow-plan.md` | yes | file-maps/_doc_files.md | partial | medium |
| `docs/runtime_issues.md` | yes | file-maps/_doc_files.md | partial | medium |
| `docs/versioning-strategy.md` | yes | file-maps/_doc_files.md | partial | medium |
| `docs/dersis.png` | no (binary) | — | n/a | n/a |

## `flags/`

| Path | mapped | dedicated map | inspected | confidence |
|------|--------|---------------|-----------|------------|
| `flags/*.png` (22 binary files) | yes (group) | file-maps/flags__group.md | yes (filenames) | high |

## `scheduler_app/`

### Package root

| File | mapped | dedicated map | inspected | confidence |
|------|--------|---------------|-----------|------------|
| `scheduler_app/__init__.py` | yes | file-maps/scheduler_app____init__.py.md | yes | high |
| `scheduler_app/_version.py` | yes | file-maps/scheduler_app___version.py.md | yes | high |
| `scheduler_app/plans.py` | yes | file-maps/scheduler_app__plans.py.md | yes | high |

### `scheduler_app/core/`

| File | mapped | dedicated map | inspected | confidence |
|------|--------|---------------|-----------|------------|
| `core/__init__.py` | yes | file-maps/scheduler_app__core____init__.py.md | yes | high |
| `core/constants.py` | yes | file-maps/scheduler_app__core__constants.py.md | yes | high |
| `core/models.py` | yes | file-maps/scheduler_app__core__models.py.md | yes | high |
| `core/logic.py` | yes | file-maps/scheduler_app__core__logic.py.md | yes | high (large file, mapped at function-level granularity) |
| `core/workflow.py` | yes | file-maps/scheduler_app__core__workflow.py.md | partial | medium |
| `core/constraint_validator.py` | yes | file-maps/scheduler_app__core__constraint_validator.py.md | partial | medium |
| `core/constraint_propagator.py` | yes | file-maps/scheduler_app__core__constraint_propagator.py.md | partial | medium |
| `core/constraint_negotiator.py` | yes | file-maps/scheduler_app__core__constraint_negotiator.py.md | partial | medium |
| `core/candidate_generator.py` | yes | file-maps/scheduler_app__core__candidate_generator.py.md | partial | medium |
| `core/conflict_graph.py` | yes | file-maps/scheduler_app__core__conflict_graph.py.md | partial | medium |
| `core/placement_scorer.py` | yes | file-maps/scheduler_app__core__placement_scorer.py.md | partial | medium |
| `core/timetable_scorer.py` | yes | file-maps/scheduler_app__core__timetable_scorer.py.md | partial | medium |
| `core/parallel_scorer.py` | yes | file-maps/scheduler_app__core__parallel_scorer.py.md | partial | medium |
| `core/schedule_optimizer.py` | yes | file-maps/scheduler_app__core__schedule_optimizer.py.md | partial | medium |
| `core/schedule_analytics.py` | yes | file-maps/scheduler_app__core__schedule_analytics.py.md | partial | medium |
| `core/schedule_impact_analyzer.py` | yes | file-maps/scheduler_app__core__schedule_impact_analyzer.py.md | partial | medium |
| `core/analytics.py` | yes | file-maps/scheduler_app__core__analytics.py.md | partial | medium |
| `core/explanation_engine.py` | yes | file-maps/scheduler_app__core__explanation_engine.py.md | partial | medium |
| `core/lns_strategies.py` | yes | file-maps/scheduler_app__core__lns_strategies.py.md | partial | medium |
| `core/cpsat_scheduler.py` | yes | file-maps/scheduler_app__core__cpsat_scheduler.py.md | partial | medium |
| `core/optimization_goals.py` | yes | file-maps/scheduler_app__core__optimization_goals.py.md | partial | medium |

### `scheduler_app/ui/`

| File | mapped | dedicated map | inspected | confidence |
|------|--------|---------------|-----------|------------|
| `ui/__init__.py` | yes | file-maps/scheduler_app__ui____init__.py.md | yes | high |
| `ui/app.py` | yes | file-maps/scheduler_app__ui__app.py.md | partial | medium (large; mapped at functional-group granularity) |
| `ui/dialogs.py` | yes | file-maps/scheduler_app__ui__dialogs.py.md | partial | medium (large; mapped per-dialog) |
| `ui/renderer.py` | yes | file-maps/scheduler_app__ui__renderer.py.md | partial | medium |
| `ui/dashboard.py` | yes | file-maps/scheduler_app__ui__dashboard.py.md | partial | medium |
| `ui/widgets.py` | yes | file-maps/scheduler_app__ui__widgets.py.md | partial | medium |
| `ui/cell_formatter.py` | yes | file-maps/scheduler_app__ui__cell_formatter.py.md | yes | high |
| `ui/badge_formatter.py` | yes | file-maps/scheduler_app__ui__badge_formatter.py.md | yes | high |
| `ui/day_keys.py` | yes | file-maps/scheduler_app__ui__day_keys.py.md | yes | high |
| `ui/icons.py` | yes | file-maps/scheduler_app__ui__icons.py.md | partial | medium |
| `ui/translations.py` | yes | file-maps/scheduler_app__ui__translations.py.md | partial | medium (22k lines; mapped at language-block granularity) |
| `ui/tier_translations.py` | yes | file-maps/scheduler_app__ui__tier_translations.py.md | partial | medium |
| `ui/tier_enforcement.py` | yes | file-maps/scheduler_app__ui__tier_enforcement.py.md | partial | medium |
| `ui/first_run.py` | yes | file-maps/scheduler_app__ui__first_run.py.md | partial | medium |
| `ui/tutorial.py` | yes | file-maps/scheduler_app__ui__tutorial.py.md | partial | medium |
| `ui/bug_report.py` | yes | file-maps/scheduler_app__ui__bug_report.py.md | partial | medium |

### `scheduler_app/data_io/`

| File | mapped | dedicated map | inspected | confidence |
|------|--------|---------------|-----------|------------|
| `data_io/__init__.py` | yes | file-maps/scheduler_app__data_io____init__.py.md | yes | high |
| `data_io/importer.py` | yes | file-maps/scheduler_app__data_io__importer.py.md | yes | high |
| `data_io/exporter.py` | yes | file-maps/scheduler_app__data_io__exporter.py.md | partial | medium |
| `data_io/schema.py` | yes | file-maps/scheduler_app__data_io__schema.py.md | yes | high |
| `data_io/template.py` | yes | file-maps/scheduler_app__data_io__template.py.md | yes | high |

(The `scheduler_app/auth/` package and its file-maps were removed in the offline conversion — no auth/licensing/heartbeat/updater/device/version code remains.)

### `scheduler_app/learning/`

| File | mapped | dedicated map | inspected | confidence |
|------|--------|---------------|-----------|------------|
| `learning/__init__.py` | yes | file-maps/scheduler_app__learning____init__.py.md | yes | high |
| `learning/feedback_logger.py` | yes | file-maps/scheduler_app__learning__feedback_logger.py.md | yes | high |
| `learning/preference_learner.py` | yes | file-maps/scheduler_app__learning__preference_learner.py.md | yes | high |

### `scheduler_app/storage/`

| File | mapped | dedicated map | inspected | confidence |
|------|--------|---------------|-----------|------------|
| `storage/__init__.py` | yes | file-maps/scheduler_app__storage____init__.py.md | yes | high |
| `storage/storage.py` | yes | file-maps/scheduler_app__storage__storage.py.md | yes | high |

### `scheduler_app/assets/`

| File | mapped | dedicated map | inspected | confidence |
|------|--------|---------------|-----------|------------|
| `assets/__init__.py` | yes | file-maps/scheduler_app__assets____init__.py.md | yes | high |
| `assets/*.png`, `*.ico` (8 binary files) | yes (group) | file-maps/scheduler_app__assets.md | yes (filenames) | high |

## Summary

| Category | Files mapped | Files inspected fully | Confidence high |
|----------|-------------|----------------------|-----------------|
| Python source | 53 | 28 | 28 |
| Configuration / CI / build | 13 | 7 | 7 |
| Documentation | 12 | 1 | 1 |
| Binary assets (group-mapped) | 30 | n/a | n/a |
| **Total mapped artefacts** | **every non-excluded file in the offline app** | | |

(Counts reflect the offline app: the `auth/` package, the login/account/update dialogs, and all test files — with their file-maps — were removed.)

## Confidence rationale

- **High** files were read in full or near-full and their per-symbol contents verified directly against source code.
- **Medium** files had their top sections + structural skeleton inspected; the file map describes intent + main symbols based on docstrings, class/function signatures, and a representative sample of internal logic. Specifics like "exact line numbers for every method" are derived from what was directly observed.
- All file maps follow the structure described in `file-maps/_TEMPLATE.md`.

## What's not mapped but acknowledged

- The historical `archive_repo_cleanup/` directory was removed in the offline conversion and no longer exists.
- The `mnt/` directory present in the working tree was treated as out-of-scope environment data.
- Compiled / cache / build outputs were never inspected.
