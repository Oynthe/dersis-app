# File: `scheduler_app/ui/app.py`

## 1. File Role
The main window class `SchedulerApp(QMainWindow)`. ~4,900 lines. Assembles every visible widget, wires every signal to the workflow, manages drag-drop, undo/redo, file I/O, exports, and (always-unlocked) tier gating. Fully offline: no auth status, account, avatar, heartbeat, or update integration.

## 2. Why this file matters
**Critical and central.** Almost every user action originates here.

## 3. Imports and Dependencies
- Third-party: a wide swath of `PyQt6.QtWidgets`, `PyQt6.QtCore`, `PyQt6.QtGui`. Optional `openpyxl` for ad-hoc Excel needs.
- Internal: virtually every other module — renderer, dashboard, constants, translations, day_keys, models, logic, workflow, dialogs, widgets, icons, tutorial, first_run, data_io, feedback_logger, preference_learner, storage, tier_enforcement.

## 4. Main Symbols (functional groups)

| Group | What it contains |
|-------|------------------|
| Constructor (`__init__`) | Stylesheet, menus, toolbar, status bar, tabs, renderer instances, dashboard, warning log, workflow init, autosave restore, translations applied. |
| Menu builders (`_build_menu`, `_build_toolbar`, `_build_status`) | All `QAction`s, shortcuts, tier-gated actions. The **Help** menu is Tutorial / Features / About. The status bar holds only `status_label` + a `BugReportButton`. |
| Translation handlers (`retranslate_ui`, `change_language`) | Re-applies every translated label. |
| File I/O (`open_file`, `save_file`, `save_as`, `_import_from_excel`, `_export_to_excel`, `export_csv`, `_export_to_pdf`, autosave) | Drives `storage.save_encrypted` / `load_encrypted` and `data_io.export_schedule`. |
| Edit / Setup (`edit_setup`, days/slots/rooms/lecturers/years editors) | Mutates state via dialog results; triggers impact analyser. |
| Scheduling actions (`place_class`, `add_class`, `place_single_class`, `bulk_add_classes`, `reschedule`, optimization goals) | Each delegates to `SchedulingWorkflow`. |
| Drop validation handler | Connected to `TimetableScene.lessonDropped`. |
| Drag/edit/delete handlers | Class-level mutations + undo entry. |
| Undo/redo | Snapshot-based via `workflow.snapshot_placements` / `restore_placements`. |
| View management (`_switch_tab`, filter combos, zoom) | Switches tabs + renderer adapter mode. |
| Tier handlers (`_update_upgrade_btn_visibility`) | Listens to `TierEnforcement.on_tier_changed`. Offline: tier is `institutional`, so the upgrade button/banner stay hidden and nothing is gated. |
| Bug reporting (`_open_bug_report`) | Opens `BugReportDialog`, which composes a `mailto:` message (no network). |
| About / Features (`_show_about`, `_show_features`) | `_show_about` shows the local embedded version from `scheduler_app._version.__version__` (no server fetch). |
| Tutorial trigger (`_show_tutorial`) | Constructs `TutorialOverlay`. |
| First-run controller integration | Receives signals from `FirstRunController`. |

## 5. Block-by-block code map (logical sections — literal line-by-line is impractical)

| Section | Approximate purpose |
|---------|---------------------|
| Lines 1–60 | Imports. |
| ~60–300 | Constructor — sets up the window, menus, toolbars, status bar, central widget, and all the deferred-build hooks. |
| ~300–700 | Menu/toolbar/shortcut wiring. |
| ~700–1200 | File operations (Open/Save/SaveAs/Import/Export). |
| ~1200–1700 | Setup dialog + state-mutation handlers. |
| ~1700–2300 | Add/edit/delete/place/auto-place/bulk-add/batch handlers. |
| ~2300–2900 | Reschedule_all flow with `OptimizationProgressDialog` + `ScheduleAnalyticsDialog`. |
| ~2900–3300 | Drag-drop handlers, undo/redo. |
| ~3300–3700 | View switching, zoom, filters. |
| ~3700–4100 | Translation, language change, retranslate_ui. |
| ~4100–4500 | Tier refresh callback (upgrade button stays hidden offline), view/zoom helpers. |
| ~4500–4800 | Bug report (`_open_bug_report`), tutorial trigger, About/Features dialogs (About shows local version). |
| ~4800–4900 | Closing, autosave on close, cleanup. |

## 6. Runtime Behavior
- Constructed once after the language gate (no auth gate exists).
- `show()` triggers initial paint.
- The only background worker is the reschedule/optimization QThread (via `OptimizationProgressDialog`). No avatar/update/heartbeat workers.
- Every interaction is event-driven via Qt signals.

## 7. Data Flow
- Holds the live `state` dict in memory.
- Mutates it via workflow methods; persists via `storage` (`.egu` files under `~/Documents/Dersis/`).
- No session/account/auth data is read or written.

## 8. UI Flow
The single window the user sees. Manages every visible action.

## 9. Error Handling and Edge Cases
- Failed open/load → translated error dialog + warning log.
- Save errors → translated error + warning log; autosave retried.
- Drop rejection → toast + warning entry.
- Reschedule cancellation → preserves the previous state.
- The reschedule/optimization worker reports failures via signals.

## 10. Integration Points
Essentially everything. The hub of the application.

## 11. Risks and Maintenance Notes
- Large file; refactor candidate but high regression risk.
- Many handlers are long methods — extract helpers carefully without changing behaviour.
- Keep tier gating in sync: any new menu/action that depends on a feature flag must call `gate_menu_action` or `FeatureGateWidget.wrap(...)`.
- The undo/redo system relies on `snapshot_placements` being symmetric with `restore_placements`; do not store extra state without mirroring on the other side.

## 12. Mini Summary
The main window. Massive file but cleanly delegating to the workflow layer and reusable widgets. Read this alongside `core/workflow.py` to understand any user action end-to-end.
