# 14 — Symbol Index

Alphabetical index of the **important** symbols. The list is curated — every public class, every important free-standing function, important constants, and config keys. For an exhaustive lookup, use ripgrep on the source.

Format: `symbol` — file:line-range — short description — related symbols.

## A

- `analyze_schedule(state, placements=None)` — `core/logic.py` — Returns full structured analytics. Wraps `ScheduleAnalytics.analyze`.
- `analyze_conflict_graph(state)` — `core/logic.py` — Returns conflict-graph metrics. Wraps `ConflictGraphBuilder` + `ConflictAnalyzer`.
- `analyze_constraint_propagation(state)` — `core/logic.py` — Per-class valid-placement counts.
- `apply_lecturer_availability_filters(state, lec, days, times)` — `core/models.py` — Restricts days/times based on availability.
- `apply_light_palette(app)` — `ui/app.py` (module-level) — Pins a complete light `QPalette` on the `QApplication` so the light-only stylesheet stays readable under OS dark mode (Qt 6.5+ otherwise supplies light text). Called by `scheduler_gui.main()` right after `app.setStyle("Fusion")`.
- `apply_negotiation_suggestion(cls, suggestion)` — `core/logic.py` — Applies a single relaxation suggestion to a class. Wraps `ConstraintNegotiator.apply_suggestion`.
- `APP_VERSION` — `scheduler_app/_version.py::__version__` — Authoritative app version, read from the `VERSION` file. Imported by `ui/bug_report.py` and shown in the About dialog.
- `AutoPlaceResult` — `core/workflow.py` — Dataclass: `success`, `relocated`, `placed_info`, `explanation`, `score`.
- `auto_place_class(state, new_cls)` — `core/logic.py` — Legacy auto-placement entry point (uses `_solve_backtrack`).

## B

- `BarChartWidget` — `ui/dashboard.py` — QPainter-based horizontal bar chart.
- `batch_schedule(state, new_classes)` — `core/logic.py` — Two-phase batch placement (preserve existing, then full reschedule on fallback).
- `BugReportButton` / `BugReportDialog` / `CrashReportDialog` — `ui/bug_report.py` — Bug + crash report flows. Compose a `mailto:dersis.app@gmail.com` message via `QDesktopServices` (clipboard fallback); no network submission.
- `BulkAddDialog` — `ui/dialogs.py` — Grid editor for many classes at once.
- `build_occupancy(state, exclude_ids=None)` — `core/logic.py` — Builds `(day,slot)→set` maps for rooms, lecturers, groups.
- `build_virtual_classroom_day_layout(state, filter_fn)` — `core/logic.py` — Layout for virtual classroom views (online + lecturer office).

## C

- `cascade_relocate(state, new_cls)` — `core/logic.py` — Place pinned class and cascade-move conflicting classes; rollback on failure.
- `CandidateGenerator` — `core/candidate_generator.py` — `.generate(cls)` → list of valid (day, slot, room).
- `check_entity_limit(tier_slug, entity_type, current_count)` — `plans.py` — Returns `(allowed, limit)`.
- `class_uses_physical_room(cls)` — `core/models.py` — True if class is face-to-face.
- `classroom_of(cls)` — `core/logic.py` — Display room (physical or virtual).
- `cls_key(cls)` — `core/models.py` — Returns class UUID (assigns if missing).
- `compute_all_metrics(state)` — `core/analytics.py` — Top-level metrics aggregator used by the dashboard.
- `ConflictAnalyzer`, `ConflictGraph`, `ConflictGraphBuilder` — `core/conflict_graph.py` — Conflict graph utilities.
- `ConstraintNegotiator`, `InfeasibilityAnalyzer`, `RelaxationSuggester`, `NegotiationReportBuilder` — `core/constraint_negotiator.py`.
- `ConstraintPropagator`, `ConstraintState` — `core/constraint_propagator.py`.
- `ConstraintValidator` — `core/constraint_validator.py` — Authoritative hard-constraint engine. Methods: `respects_constraints`, `check_placement`, `check_placement_explained`, `find_conflicts`, `find_conflicting_classes`, `add_placement`, `remove_placement`, `sort_by_difficulty`.
- `CPSATScheduler` — `core/cpsat_scheduler.py` — Google OR-Tools wrapper.
- `create_occupancy_snapshot(validator)`, `create_state_snapshot(state)` — `core/parallel_scorer.py` — Picklable snapshots for workers.

## D

- `DAY_KEYS` — `ui/day_keys.py` — `["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]`.
- `DashboardWidget` — `ui/dashboard.py` — Analytics tab.
- `DataValidationReport` — `data_io/importer.py` — Errors + warnings container.
- `DEFAULT_GOALS` — `core/optimization_goals.py` — 6-slider default values.
- `DEFAULT_WEIGHTS` — `core/placement_scorer.py` — 14-component scoring weights.
- `display_day(value)`, `day_label(key)`, `format_day_time(day, slot=None)` — `ui/day_keys.py`.
- `DropValidation` — `core/workflow.py` — Drop-validation result dataclass.

## E

- `effective_day(cls)`, `effective_time(cls)`, `effective_room(cls)` — `core/models.py` — Pinned-first then placed.
- `EditClassResult` — `core/workflow.py`.
- `EguFileError` (alias `UvaFileError`) — `storage/storage.py` — Raised on container failures.
- `ensure_class_uid(cls)`, `ensure_dirs()` — `core/models.py` / `storage/storage.py`.
- `EmptySlotItem`, `LessonItem`, `MatrixLessonItem`, `HeaderItem` — `ui/renderer.py` — QGraphicsItem subclasses.
- `ENTITY_*` constants — `plans.py` — `ENTITY_SCHEDULES`, `ENTITY_CLASSES`, `ENTITY_CLASSROOMS`, `ENTITY_LECTURERS`, `ENTITY_YEARS`, `ENTITY_DEVICES`.
- `export_schedule(state, path, format=…)` — `data_io/exporter.py` — Excel/CSV/PDF export.
- `ExplanationEngine` — `core/explanation_engine.py` — `.explain_placement(cls, day, slot, room, breakdown)`.

## F

- `FeatureGateWidget` — `ui/tier_enforcement.py` — Wrapper that disables a widget and shows upgrade tooltip.
- `FeatureState` — `plans.py` — Result of `get_feature_state` (enabled, required_plan, reason, tooltip_message).
- `FEATURE_*` constants — `plans.py` — Feature flag keys.
- `FeedbackLogger` — `learning/feedback_logger.py` — Persistent encrypted log of user interactions.
- `filter_class_days(cls, all_days)`, `filter_class_times(cls, all_times)` — `core/models.py`.
- `FinalSchedule` — `data_io/exporter.py` — State wrapper for export.
- `find_conflicts(state, cls, day, slot, room)` — `core/logic.py` — Returns translated conflict strings.
- `find_conflicting_classes(...)` — `core/logic.py` — Returns conflicting class dicts.
- `find_valid_options(state, cls)` — `core/logic.py` — All valid (day, slot, room) for one class.
- `FirstRunController`, `LanguageSelectorDialog`, `run_language_gate()` — `ui/first_run.py`.

## G

- `gate_menu_action(action, feature)` — `ui/tier_enforcement.py` — Disables a QAction and adds upgrade tooltip.
- `generate_excel_template(filepath)` — `data_io/template.py` — Builds localised Excel template.
- `get_active_physical_classroom(cls)`, `get_effective_room_resource_for_class(cls)`, `get_display_location_label(cls)` — `core/models.py`.
- `get_classroom_export_labels(classrooms, classes)` — `core/models.py`.
- `get_consecutive_slots(state, start_slot, duration)` — `core/logic.py`.
- `get_feature_state(tier_slug, feature_name)`, `get_limit_state(...)` — `plans.py`.
- `get_lecturer_availability(state, lec_name)` — `core/models.py`.
- `get_limit(tier_slug, limit_name)` — `plans.py`.
- `get_location_label(lt)`, `get_location_labels()`, `get_virtual_location_labels()` — `core/models.py`.
- `get_physical_room_candidates(state, cls, apply_capacity=True)` — `core/models.py`.
- `get_placed_classes(state)` — `core/logic.py` — Classes that are placed OR pinned.
- `get_plan(tier_slug)`, `get_required_tier_for_limit(...)`, `get_upgrade_tier(tier_slug, feature_name)` — `plans.py`.
- `get_protection_label(level)`, `get_room_candidates(state, cls)`, `get_room_capacity(state, room)` — `core/models.py`.
- `get_year_color(state, year_name)` — `core/logic.py`.
- `_global_exception_handler(exc_type, exc_value, exc_tb)` — `scheduler_gui.py` — Top-level `sys.excepthook` (installed in the `__main__` guard before `main()`). Two paths: **no `QApplication`** → `_report_startup_failure` (writes `startup_error.log` + native `MessageBox`); **`QApplication` running** → writes `crash_log.txt` and shows `CrashReportDialog` (`QMessageBox.critical` fallback). Skips `KeyboardInterrupt`; ends at `sys.__excepthook__`. Heavy imports are lazy.

## H

- `has_feature(tier_slug, feature_name)` — `plans.py`.

## I

- `ImpactLevel`, `ImpactResult` — `core/schedule_impact_analyzer.py`.
- `is_immovable(cls)`, `is_sequential_class(cls)`, `is_virtual_location_type(lt)` — `core/models.py`.
- `is_rtl(lang=None)`, `RTL_LANGUAGES` — `ui/translations.py`.

## L

- `lecturer_available_at(state, lec, day, slot)` — `core/models.py`.
- `lighten_color(hex_color, factor=0.45)` — `core/logic.py`.
- `load_encrypted(path)`, `save_encrypted(data, path)`, `load_encrypted_lines(path)`, `save_encrypted_lines(entries, path)`, `append_encrypted_entry(entry, path)` — `storage/storage.py`.
- `load_scheduler_data_from_excel(filepath)` — `data_io/importer.py`.
- `location_type_of(cls)` — `core/models.py`.
- `LOCATION_FACE_TO_FACE`, `LOCATION_ONLINE`, `LOCATION_LECTURER_OFFICE`, `LOCATION_TYPES`, `VIRTUAL_LOCATION_TYPES` — `core/models.py`.

## M

- `main()` — `scheduler_gui.py` — Offline application entry point. Lazily imports the heavy deps, then `freeze_support` → `QApplication`(Fusion) → `apply_light_palette` → `run_language_gate()` → `TierEnforcement.set_tier(TIER_INSTITUTIONAL)` → `SchedulerApp()` → `exec()`. The crash excepthook (`_global_exception_handler`) is installed in the `__main__` guard *before* `main()`. No auth/version/heartbeat/updater.
- `mark_placed(cls, day, slot, room)`, `mark_unplaced(cls)` — `core/models.py`.
- `migrate_legacy_files()` — `storage/storage.py` — Idempotent legacy migration.
- `MultiSelectButton` — `ui/widgets.py`.

## N

- `needs_physical_room(cls)` (alias of `class_uses_physical_room`) — `core/models.py`.
- `negotiate_after_optimization(state, placed_list, unplaced_list)` — `core/logic.py`.
- `new_class()`, `new_lecturer_availability()`, `new_state()` — `core/models.py`.
- `new_save_path()`, `autosave_path()`, `feedback_log_path()`, etc. — `storage/storage.py`.
- `normalize_class_data(cls)`, `normalize_class_location_fields(cls)`, `normalize_state_classes(state)` — `core/models.py`.
- `normalize_day_value(value)`, `normalize_day_list(values)`, `normalize_state_day_keys(state)` — `ui/day_keys.py`.

## O

- `occupied_slots_of(state, cls)` — `core/logic.py`.
- `optimized_auto_place(state, new_cls, weights=None)` — `core/logic.py` — Bridge to `ScheduleOptimizer`.
- `optimized_batch_schedule(state, new_classes, weights=None)` — `core/logic.py`.
- `optimized_reschedule_all(state, weights=None, ...)` — `core/logic.py`.
- `OptimizationGoalsDialog`, `OptimizationProgressDialog`, `OpenSlotsDialog` — `ui/dialogs.py`.

## P

- `parse_location_type_label(value)` — `core/models.py` — Translated label → key.
- `ParallelScorerPool` — `core/parallel_scorer.py`.
- `PlacementScorer` — `core/placement_scorer.py` — `.score`, `.score_explained`, `.score_candidates`, `.score_candidates_with_lookahead`.
- `PlaceBatchResult`, `PlaceClassDialog` — `core/workflow.py`, `ui/dialogs.py`.
- `PLANS` — `plans.py` — Dict of tier → {limits, features, prices}.
- `PreferenceLearner` — `learning/preference_learner.py`.
- `PROTECTION_NONE`, `PROTECTION_SOFT`, `PROTECTION_SAME_DAY`, `PROTECTION_IMPROVE_ONLY`, `PROTECTION_LOCKED`, `PROTECTION_LEVELS`, `PROTECTION_LABELS`, `PROTECTION_LABEL_KEYS` — `core/models.py`.

## R

- `RendererAdapter` — `ui/renderer.py`.
- `RepairStrategy`, `DestroyStrategy`, `AdaptiveStrategySelector`, `get_destroy_strategy(name, state, weights=None)` — `core/lns_strategies.py`.
- `_report_startup_failure(tb_text)` — `scheduler_gui.py` — Persists + surfaces a fatal **startup** error before the Qt app exists: appends to `startup_error.log`, shows a native Windows `MessageBox` (via `ctypes`), echoes to stderr. stdlib-only, so it works even when PyQt6/`scheduler_app` fail to import. Called by `_global_exception_handler` on the no-`QApplication` path.
- `reschedule_all(state)` — `core/logic.py` — Legacy global re-optimization.
- `respects_constraints(...)` — `core/logic.py` — **Deprecated** alias.
- `room_fits_class(state, room, cls)` — `core/models.py`.

## S

- `save_encrypted`, `save_encrypted_lines` — see above.
- `ScheduleAnalytics` — `core/schedule_analytics.py`.
- `ScheduleImpactAnalyzer` (legacy spelling), `ImpactResult` — `core/schedule_impact_analyzer.py`.
- `ScheduleNewResult` — `core/workflow.py`.
- `SchedulerApp` — `ui/app.py` — QMainWindow.
- `SchedulerDataset` — `data_io/importer.py`.
- `ScheduleOptimizer` — `core/schedule_optimizer.py` — `.optimize`, `.place_with_reschedule`, `._greedy_construct`, `._lns_loop`, `._multi_start`, `._cpsat_refine`.
- `SchedulingWorkflow` — `core/workflow.py` — UI-free orchestrator.
- `score_placement(state, cls, day, slot, room, weights=None)` — `core/logic.py`.
- `score_placement_explained(...)` — `core/logic.py`.
- `set_language(lang)`, `get_language()`, `tr(key, **kwargs)` — `ui/translations.py`.
- `slot_index(state, slot_name)`, `slots_fit(state, start, duration)` — `core/logic.py`.
- `slot_offset_for_target(cls, target_idx)` — `core/models.py`.
- `split_non_joint(cls)` — `core/models.py`.
- `_startup_log_path()` — `scheduler_gui.py` — Returns the first writable of `~/Documents/Dersis/logs`, the app dir, then the temp dir, as `.../startup_error.log` (or `None`). stdlib-only; used by `_report_startup_failure` (and as the crash-log fallback when `storage` can't be imported).

## T

- `targets_overlap(targets_a, targets_b)` — `core/logic.py`.
- `TierEnforcement`, `UpgradeDialog` — `ui/tier_enforcement.py`. Singleton gate + upgrade dialog. Offline: default tier is `institutional`, so gating always allows and `UpgradeDialog` is never shown.
- `PRICING_PAGE_URL` — `ui/tier_enforcement.py` — Now an empty string (no pricing page); the upgrade CTA is a no-op.
- `TIER_FREE`, `TIER_STARTER`, `TIER_PROFESSIONAL`, `TIER_MAX`, `TIER_INSTITUTIONAL`, `TIER_ORDER` — `plans.py`.
- `TimetableScene`, `TimetableView` — `ui/renderer.py`.
- `TimetableScorer` — `core/timetable_scorer.py`.
- `Toast`, `WarningLogPanel` — `ui/widgets.py`.
- `total_duration(cls)` — `core/logic.py`.
- `TRANSLATIONS` — `ui/translations.py` — 22-language dict.
- `tooltip_text(cls, …)`, `plain_cell_text(entry)` — `ui/cell_formatter.py`.
- `TutorialOverlay` — `ui/tutorial.py`.

## U

- `UNLIMITED` — `plans.py` — `-1`.

## V

- `validate_class_fields(cls)` — `core/models.py`.
- `VIRTUAL_LOCATION_DISPLAY`, `VIRTUAL_LOCATION_TYPES` — `core/models.py`.

## W

- `WORKBOOK_SHEETS` — `data_io/schema.py` — Sheet structure for import/export.
- `WarningLogPanel` — `ui/widgets.py`.
- `YEAR_COLORS` — `core/constants.py` — `["#3B82F6","#10B981","#F59E0B","#EF4444","#8B5CF6","#EC4899","#06B6D4","#84CC16"]`.

## File-format constants (`storage/storage.py`)

- `_MAGIC = b"EGU1"`
- `_LEGACY_MAGIC = b"UVA1"`
- `_FORMAT_VERSION = 1`
- `_SALT_LEN = 16`
- `_IV_LEN = 12`
- `_HEADER_FMT = "!4sH"` — `struct` format
- `_PAYLOAD_LEN_FMT = "!I"`
- `_CHECKSUM_LEN = 32` — SHA-256
- Subdir constants: `SETTINGS_DIR`, `SAVES_DIR`, `LEARNING_DIR`, `LOGS_DIR`, `EXPORTS_DIR`, `BACKUPS_DIR`, `KEYS_DIR`.

## Network endpoints

None. The app is fully offline and makes no network calls. (The former remote licensing/heartbeat/version/update/bug-report endpoints were removed with the `auth/` package.)

## Translation key prefixes

See `09_SETTINGS_LOCALIZATION_AND_PERSISTENCE_MAP.md` section 2.2 for the full list.
