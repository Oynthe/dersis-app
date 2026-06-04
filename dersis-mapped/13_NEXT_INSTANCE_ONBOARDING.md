# 13 — Next Instance Onboarding

A focused guide for the next Claude (or human) who picks this up cold.

## Mental model in two paragraphs

DERSİS is a desktop PyQt6 application whose central data structure is a plain Python dict called `state`. Everything you do — adding classes, dragging lessons around, importing Excel, running the optimiser, exporting PDF — ultimately mutates that dict. Pinned classes are immovable; protected classes constrain how the optimiser may move them; flexible classes can be re-placed freely.

The codebase has a hard separation between **UI** (`scheduler_app/ui/`) and **scheduling logic** (`scheduler_app/core/`), bridged by a UI-free orchestration layer (`core/workflow.py`). Persistence, learning, and import/export are independent cross-cutting concerns. The app is **fully offline** — there is no login, license check, account page, or update mechanism, and it makes no network calls; it launches straight into the main window with every feature unlocked locally. The most important file you'll touch for **logic** is `core/workflow.py`; for **UI** it's `ui/app.py`; for **state shape** it's `core/models.py`; for **storage** it's `storage/storage.py`. All persistent data lives in `~/Documents/Dersis/`.

## Read these first

1. **`02_PROJECT_OVERVIEW.md`** — what the app does.
2. **`03_ARCHITECTURE_MAP.md`** — how the layers fit.
3. **`05_DOMAIN_MODEL_AND_DATA_FLOW.md`** — the shape of `state` and `cls`.
4. **`07_SCHEDULING_AND_OPTIMIZATION_MAP.md`** — the brain of the product.

Then dip into:
- **`06_UI_MAP.md`** if you're doing UI work.
- **`09_SETTINGS_LOCALIZATION_AND_PERSISTENCE_MAP.md`** for save format and i18n.
- **`12_RISKS_TECH_DEBT_AND_UNKNOWN.md`** before any refactor.

## Where to look for common tasks

### "I need to add a new field to a class."
1. Add it to `new_class()` in `scheduler_app/core/models.py`.
2. Add it to `_EDITABLE_CLASS_FIELDS` and `copy_editable_class_fields` if it should be editable.
3. Extend `normalize_class_data` so old saves backfill the field.
4. Update `AddClassDialog` (and `EditClassDialog`) in `scheduler_app/ui/dialogs.py` to render and capture it.
5. If it affects scheduling, update `ConstraintValidator.check_placement` and `CandidateGenerator.get_search_space`.
6. If it affects scoring, add a weight key to `DEFAULT_WEIGHTS` and the relevant `_score_*` block in `PlacementScorer`.
7. If it's importable from Excel, extend `WORKBOOK_SHEETS["classes"]` in `data_io/schema.py` and `_process_classes` in `data_io/importer.py`.

### "I need to add a new translation."
1. Add the key + value to **every** language block in `scheduler_app/ui/translations.py`. Use the same format placeholders across all 22 languages.
2. Reference it with `tr("your.key.path")`.
3. If you forget a language, `tr()` falls back to English silently — there's no parity test, so be deliberate.

### "I need to add a new hard constraint."
1. Add the field on `cls` (see above).
2. Implement the check in `ConstraintValidator.respects_constraints` and `check_placement` (look at how `excluded_classrooms` is enforced).
3. Update `unplaced_reason` in `CandidateGenerator` so the user gets a reason when the new constraint blocks placement.
4. Add the failure message to `validation.*` translation keys.
5. Update `InfeasibilityAnalyzer` in `core/constraint_negotiator.py` to categorise the failure.
6. Mirror the constraint in `core/cpsat_scheduler.py` if you want CP-SAT to respect it.
7. Verify manually (see "How to verify changes manually" below) — there is no automated test suite.

### "I need to add a new scoring component."
1. Add a default weight to `DEFAULT_WEIGHTS` in `core/placement_scorer.py`.
2. Implement the component in `PlacementScorer._score_*` and `TimetableScorer.score`.
3. Add an entry in `_COMPONENT_INFO` of `explanation_engine.py` (label, positive/negative translation keys).
4. Add translation keys for the explanation strings.
5. Add the weight to `optimization_goals.py` mapping if the user-facing sliders should affect it.
6. Allow `PreferenceLearner._update_delta` to touch it.

### "I need to add a new menu action."
1. Add the action to `ui/app.py::_build_menus` (and toolbar if appropriate).
2. Define the slot method on `SchedulerApp`.
3. Add translation key under `menus.*` and a tooltip key.
4. If the action is tier-gated, wrap it with `gate_menu_action(action, feature_flag)` from `ui/tier_enforcement.py` and add the flag to `plans.py`.
5. Trigger the underlying logic through `self.workflow.…` to keep the UI thin.

### "I need to change the file format."
You almost certainly don't. If you really must:
1. Increment `_FORMAT_VERSION` in `storage/storage.py`.
2. Update `_HEADER_FMT` if the header shape changes.
3. Keep the old version parseable in `_parse_container` for backward compat.
4. Update `EguFileError` messages.
5. Test with old files from real users.

### "I need to handle a new language."
1. Pick the ISO code (e.g. `fi` for Finnish).
2. Add `'fi': { … }` block to `TRANSLATIONS` containing **every** key (paste the English block and translate). 
3. If RTL, add to `RTL_LANGUAGES`.
4. Add the flag PNG to `flags/` and expose a helper in `ui/icons.py`.
5. Add a flag button in `ui/first_run.py::LanguageSelectorDialog`.
6. Test the language gate flow.

### "I'm chasing a bug in optimization output."
1. Reproduce against a saved `.egu` (use the `File → Open` path on a copy).
2. Drop a breakpoint or print in `ScheduleOptimizer.optimize()`'s `_greedy_construct` and `_lns_loop`.
3. Check `summary` field in the result — `before/after` quality, strategy stats.
4. Inspect `PlacementScorer.score_explained(cls, day, slot, room)` for a specific candidate to see the breakdown.
5. If CP-SAT misbehaves, set `use_cpsat=False` to isolate; reproduce with heuristic only.

### "I'm chasing a bug in the bug/crash report flow."
1. Both dialogs live in `ui/bug_report.py` (`BugReportDialog`, `CrashReportDialog`); the status-bar trigger is `BugReportButton`.
2. The report is composed locally and handed to the user's email client via `_open_mailto()` (`QDesktopServices.openUrl` on a `mailto:emre.uygun.elt@gmail.com` link, subject "DERSİS Bug Report"). Nothing is transmitted by the app.
3. If no mail client is available, `_open_mailto` copies the body to the clipboard and shows an info dialog with the address.
4. The crash hook is `_global_exception_handler` in `scheduler_gui.py`, which also writes `~/Documents/Dersis/logs/crash_log.txt`.

## Likely future development workflows

- **Add a feature behind a tier**: extend `FEATURE_*` in `plans.py`, gate at the UI in `tier_enforcement.py`, add upgrade tooltip and dialog text to `tier_translations.py`. No server-side change.
- **Tune scoring**: change `DEFAULT_WEIGHTS` or add a new component. Ship with telemetry by extending `feedback_logger.py`. The learner will adapt.
- **Add a new view mode** in the renderer: add a new value to the filter mode enum + tab in `ui/app.py` + paint logic in `ui/renderer.py`.
- **Add a new translation language**: see the recipe above.

## What NOT to touch carelessly

- `scheduler_app/__init__.py` — the import shim. Many call sites rely on `from scheduler_app.models import …`.
- `storage/storage.py` — file format. Breaking changes lose user data.
- `models.new_class()` and `models.normalize_class_data()` — the canonical schema.
- `ui/translations.py` — see above. Use a script for systematic changes.
- The whole `EGU1` checksum/auth-tag flow.

## How to verify changes manually (rough)

There is no automated test suite anymore — verification is manual plus the CI import/version/build checks.

1. Run `python verify_deps.py` to confirm the environment is sound.
2. Run `python scheduler_gui.py`. It opens **directly into the main window** — no login, no license, no network. (First launch shows the one-time language picker.)
3. Try: add a class, auto-place, drag, reschedule, export Excel/CSV/PDF, save, re-open.
4. Inspect `~/Documents/Dersis/saves/autosave.egu` size to confirm it changes.
5. To exercise the bug/crash flow, click the status-bar bug icon (or trigger an exception) and confirm your email client opens prefilled to `emre.uygun.elt@gmail.com`.

## Tooling cheat-sheet

| Need | Command |
|------|---------|
| Run the app | `python scheduler_gui.py` |
| Verify deps | `python verify_deps.py` |
| Regenerate installer wizard images | `python installer/create_wizard_images.py` |
| Build Windows distribution (embed) | `build_embed.bat` |
| Build Windows distribution (nuitka) | `build_nuitka.bat` |
| Build installer | `iscc installer.iss` |
| Bump version | edit `VERSION`; the rest auto-derives |
| Inspect a `.egu` file | only via `storage.load_encrypted(path)` from a Python REPL — the file is encrypted with the master key in `~/Documents/Dersis/keys/key.bin` |
