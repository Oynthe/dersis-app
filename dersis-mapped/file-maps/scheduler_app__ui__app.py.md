# File: `scheduler_app/ui/app.py`

> **Captured 2026-06-04.** _Updated 2026-06-08: reflects the dark-mode-theme fix — added the module-level `apply_light_palette(app)` helper and the `QMenu`/`QComboBox QAbstractItemView`/`QListWidget` text-colour additions to the stylesheet template._

## 1. File Role
The main window class `SchedulerApp(QMainWindow)`. ~4,960 lines. Assembles every visible widget, wires every signal to the workflow, manages drag-drop, undo/redo, file I/O, exports, and (always-unlocked) tier gating. The module also defines the light-only stylesheet template and `apply_light_palette()` (a module-level function used by the launcher). Fully offline: no auth status, account, avatar, heartbeat, or update integration.

## 2. Why this file matters
**Critical and central.** Almost every user action originates here.

## 3. Imports and Dependencies
- Third-party: a wide swath of `PyQt6.QtWidgets`, `PyQt6.QtCore`, `PyQt6.QtGui`. Optional `openpyxl` for ad-hoc Excel needs.
- Internal: virtually every other module — renderer, dashboard, constants, translations, day_keys, models, logic, workflow, dialogs, widgets, icons, tutorial, first_run, data_io, feedback_logger, preference_learner, storage, tier_enforcement.

## 4. Main Symbols (functional groups)

| Group | What it contains |
|-------|------------------|
| Module-level theming (`_build_stylesheet`, `_APP_STYLESHEET_TEMPLATE`, `apply_light_palette`) | The light-only QSS template + builder, and `apply_light_palette(app)` (line ~533) which pins a complete light `QPalette` on the `QApplication`. Called by `scheduler_gui.main()` right after `app.setStyle("Fusion")`. See Runtime Behavior → Theming. |
| Constructor (`__init__`) | Stylesheet (applies `_build_stylesheet()`), menus, toolbar, status bar, tabs, renderer instances, dashboard, warning log, workflow init, autosave restore, translations applied. |
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
| Lines 1–~90 | Imports. |
| ~90–530 | Module-level helpers and the **light-only stylesheet** — small icon/encoding helpers, `_build_stylesheet()` (~149), and the `_APP_STYLESHEET_TEMPLATE` QSS (~160–528, incl. `QMenu`, `QComboBox QAbstractItemView`, `QListWidget` with explicit `color: #1E293B`). |
| ~531–577 | `apply_light_palette(app)` — pins a deterministic light `QPalette` (Window/WindowText/Base/AlternateBase/Text/Button/ButtonText/BrightText/PlaceholderText/ToolTip*/Link*/Highlight/HighlightedText + the Disabled group). Lazily imports `QPalette`/`QColor`. |
| ~580–797 | `_UnplacedTabButton`, `DraggableUnplacedList` and other module-level widget helpers, up to the `SchedulerApp` class. |

The remaining rows are **approximate logical offsets inside the `SchedulerApp` class** (which spans ~798–4961); they are not exact file lines.

| Section | Approximate purpose |
|---------|---------------------|
| ~798–1050 | Constructor — sets up the window, menus, toolbars, status bar, central widget, and all the deferred-build hooks. |
| ~1050–1500 | Menu/toolbar/shortcut wiring. |
| ~1500–2000 | File operations (Open/Save/SaveAs/Import/Export). |
| ~2000–2400 | Setup dialog + state-mutation handlers. |
| ~2400–2950 | Add/edit/delete/place/auto-place/bulk-add/batch handlers. |
| ~2950–3450 | Reschedule_all flow with `OptimizationProgressDialog` + `ScheduleAnalyticsDialog`. |
| ~3450–3750 | Drag-drop handlers, undo/redo. |
| ~3750–4050 | View switching, zoom, filters. |
| ~4050–4350 | Translation, language change, retranslate_ui. |
| ~4350–4600 | Tier refresh callback (upgrade button stays hidden offline), view/zoom helpers. |
| ~4600–4850 | Bug report (`_open_bug_report`), tutorial trigger, About/Features dialogs (About shows local version). |
| ~4850–4961 | Closing, autosave on close, cleanup. |

## 6. Runtime Behavior
- Constructed once after the language gate (no auth gate exists).
- `show()` triggers initial paint.
- The only background worker is the reschedule/optimization QThread (via `OptimizationProgressDialog`). No avatar/update/heartbeat workers.
- Every interaction is event-driven via Qt signals.

### Theming
The app is **light-only**. Two complementary mechanisms keep it readable:
- The `_APP_STYLESHEET_TEMPLATE` QSS (applied in the constructor) styles every widget. As of 2026-06-08 it sets an explicit `color: #1E293B` (slate-800) on `QMenu`, `QComboBox QAbstractItemView` (the drop-down list), and `QListWidget` (which also gets `background: white`) — rules that previously set a light background without a text colour.
- `apply_light_palette(app)` (module-level, called by `scheduler_gui.main()` after `setStyle("Fusion")`) pins a complete light `QPalette`. **Why it's needed:** the stylesheet sets no palette, and Qt 6.5+ follows the OS colour scheme, so on Windows *dark mode* the default palette supplies light text — making any unstyled light-background widget render white-on-white (legible only when a row is selected and the highlight colour kicks in). Forcing the palette makes the UI render correctly regardless of the OS light/dark setting.

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
