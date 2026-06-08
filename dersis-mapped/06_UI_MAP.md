# 06 — UI Map

> **Captured 2026-06-04.** _Updated 2026-06-08: reflects the dark-mode-theme fix — documents `apply_light_palette()` (§1.0) and the `QMenu`/`QComboBox`/`QListWidget` stylesheet text-colour additions._

The PyQt6 UI is concentrated under `scheduler_app/ui/`. It is built on top of a UI-free workflow layer (`core/workflow.py`); the UI files mostly assemble widgets, wire signals to workflow methods, and translate result objects into Qt updates.

## 1. Main window — `ui/app.py`

Class: `SchedulerApp(QMainWindow)`. ~4,960 lines. The single source of truth for everything that lives on screen at runtime. The module also defines the light-only stylesheet template and the module-level `apply_light_palette()` helper (see §1.0).

### 1.0 Theming — light-only, made dark-mode-safe

DERSİS renders **light-only**, by two complementary mechanisms:

1. **The application stylesheet** (`_APP_STYLESHEET_TEMPLATE` / `_build_stylesheet()` at the top of `ui/app.py`), applied in the constructor. It styles every widget for the light theme. As of **2026-06-08** three rules that set a light background without a text colour now also set an explicit `color: #1E293B` (slate-800):
   - `QMenu` — `color: #1E293B` (menus and their items).
   - `QComboBox QAbstractItemView` — `color: #1E293B` (the drop-down popup list).
   - `QListWidget` — `background: white; color: #1E293B` (e.g. the unplaced-classes list).
2. **A pinned light palette** — `apply_light_palette(app)`, a module-level function in `ui/app.py` called by `scheduler_gui.main()` immediately after `app.setStyle("Fusion")`. It builds a complete light `QPalette` (Window, WindowText, Base, AlternateBase, Text, Button, ButtonText, BrightText, PlaceholderText, ToolTipBase, ToolTipText, Link, LinkVisited, Highlight, HighlightedText, plus the Disabled colour group) and calls `app.setPalette(pal)`.

**Why the palette is needed (dark-mode readability fix):** the app sets a light-only stylesheet but previously set **no palette**. Qt 6.5+ (the build ships Qt 6.11) adopts the OS colour scheme by default, so on Windows running in *dark mode* the default palette supplies light text. Every widget whose stylesheet rule set a light background without an explicit text colour (drop-down menus, list/table rows, line edits, …) then rendered light-on-light — unreadable, legible only once a row was selected and the highlight colour kicked in. Forcing a deterministic light palette makes the UI render correctly regardless of the OS light/dark setting; the three stylesheet `color` additions above belt-and-brace the most-affected widgets.

### 1.1 Constructor responsibilities (high-level order)
1. Apply style sheet from `_DIALOG_STYLESHEET_TEMPLATE` etc.
2. Build the **menu bar** (`_build_menus`):
   - **File** — New, Open `.egu`, Save, Save As, Import Excel, Export (Excel/CSV/PDF), Recent files, Quit.
   - **Edit** — Undo, Redo, Setup (active days/slots/rooms/lecturers/years), Preferences.
   - **Schedule** — Auto place, Bulk add, Batch place, Reschedule all, Optimization goals.
   - **View** — switch views (per classroom, per lecturer, per branch, show-everything matrix), zoom in/out.
   - **Help** — Tutorial, Features, About.
3. Build the **toolbar** with the most-used actions.
4. Build the **status bar**:
   - `BugReportButton` (icon, in `bug_report.py`)
   - Optional toast area (auto-faded notifications via `Toast` widget).
5. Build the **central widget**:
   - `QTabWidget` containing one tab per view mode.
   - Inside each tab, a `TimetableView` (`renderer.py`) hosting a `TimetableScene` of `LessonItem` + `EmptySlotItem` instances.
   - A separate `DashboardWidget` tab (`dashboard.py`).
   - A `WarningLogPanel` (`widgets.py`) docked at the bottom.
6. Construct a `SchedulingWorkflow(state)` instance — held as `self.workflow`.
7. Construct a `FeedbackLogger` and `PreferenceLearner`. Pass the learner's `get_weights()` into the workflow for AI calls.
8. Restore autosave if present (`saves/autosave.egu`).
9. Apply translations via `retranslate_ui()` — called whenever language changes.
10. Wire keyboard shortcuts (`QShortcut`): Ctrl+Z/Y, Ctrl+S, Ctrl+N, Ctrl+O, Ctrl+= / Ctrl+- for zoom.

### 1.2 Key signal/slot wiring

| User action | Trigger | Calls |
|-------------|---------|-------|
| Drop a `LessonItem` on an `EmptySlotItem` | `TimetableScene.lessonDropped` | `workflow.validate_drop` → if OK, `mark_placed` + repaint; else toast + warning log. |
| Right-click a lesson → "Auto place" | context menu in `LessonItem` | `workflow.auto_place_class` → AutoPlaceResult → `_apply_auto_place_result`. |
| Menu → Add class | toolbar/menu | Opens `AddClassDialog`; on accept, calls `workflow.schedule_new_classes`. |
| Menu → Reschedule | menu action | Opens `OptimizationProgressDialog`; `workflow.reschedule_all` runs in a `QThread`; result drives a `ScheduleAnalyticsDialog`. |
| Menu → Bulk add | menu | `BulkAddDialog` → grid editor; on accept, `workflow.schedule_new_classes`. |
| Drag a lesson within the grid | `LessonItem.mouseMoveEvent` → `QDrag` | Drop fires `validate_drop`. |
| Setup → Days/Slots/Rooms/Lecturers/Years | menu | `SetupDialog` (tabbed editor); on accept, state lists are mutated and views are re-rendered. |
| Language change | menu | `set_language(code)` then `retranslate_ui()`. |
| Bug report | status bar | `BugReportDialog.exec()` — composes a `mailto:` message (see §12). |
| Help → About | menu | `_show_about` dialog showing the local embedded version (no server fetch). |

### 1.3 View modes (rendered by `ui/renderer.py`)

- **Per-classroom** — rows are time slots, columns are days; multiple lessons stack only if they truly share a room (rare). Used to spot room collisions visually.
- **Per-lecturer** — same axes; lesson cells coloured by year (`YEAR_COLORS` from `constants.py`).
- **Per-branch / student group** — same axes.
- **Show Everything (matrix)** — wide matrix view; days × time slots vs branches; uses `MATRIX_*` colours.

The renderer keeps a "virtual classroom" view (`build_virtual_classroom_day_layout` from `logic.py`) so online/lecturer-office lessons get sub-columns inside their day column.

## 2. Dialogs — `ui/dialogs.py`

~4,451 lines. Each dialog is a `QDialog` subclass. Notable dialogs (alphabetised):

| Class | Purpose |
|-------|---------|
| `AddClassDialog` | Enter / edit a single class. Tabs for basics, targets, constraints, location, pinning. Calls `validate_class_fields()`. |
| `BulkAddDialog` | Multi-row grid editor for entering many classes at once. Validates each row. |
| `EditClassDialog` | In-place edit of an already-existing class. Uses `copy_editable_class_fields`. |
| `PlaceClassDialog` | Manual placement: pick day/slot/room from listboxes restricted by constraints. |
| `SelectClassDialog` | Picks a class to act on (used by "Place class" menu). |
| `SetupDialog` | Tabbed editor for the global lists: active weekdays, time slots, classrooms (with capacity), lecturers (with availability), years/branches. |
| `WarningsDialog` | Shows aggregated warnings/conflicts. Backed by `WarningLogPanel`. |
| `OpenSlotsDialog` | Lists every valid (day, slot, room) for a single class. |
| `PostAddDialog` | Shown after add — choose Auto place / Manual / Skip. |
| `OptimizationGoalsDialog` | 6 sliders mapped to `optimization_goals.py` presets. |
| `OptimizationProgressDialog` | Cancellable progress dialog used during reschedule_all. |
| `ScheduleAnalyticsDialog` | Shows the `ScheduleAnalytics` results — grade, gauge, per-entity tables, insights. |
| `NegotiationDialog` | Shows constraint-negotiation suggestions and lets user apply them. |
| `BatchResolveDialog` | Resolve unplaced classes by relaxing or unpinning. |
| `LanguageDialog` | Language picker (also accessible from menu after first run). |
| `ImportPreviewDialog` | Shows the validation report from an import; user accepts/rejects. |
| `RecentFilesDialog` | Lists recent saves; double-click opens. |
| `AboutDialog` | App version, license, link to website. |

All dialogs use the shared `_DIALOG_STYLESHEET_TEMPLATE` (top of `dialogs.py`) for consistent visuals.

## 3. Top-level dialogs outside `dialogs.py`

| File | Class | Purpose |
|------|-------|---------|
| `ui/bug_report.py` | `BugReportDialog`, `CrashReportDialog`, `BugReportButton` | Manual bug-report and auto-crash-report flows. Compose a `mailto:` message handed to the user's email client (no network POST). |
| `ui/tier_enforcement.py` | `UpgradeDialog`, `FeatureGateWidget`, `TierEnforcement` (singleton) | Tier-based gating, polished upgrade UI. Offline build: tier defaults to `institutional`, so gating always allows and `UpgradeDialog` is never shown. |
| `ui/tutorial.py` | `TutorialOverlay` | Full-window spotlight + step card. Section-based progress bar. |
| `ui/first_run.py` | `LanguageSelectorDialog`, `FirstRunController` | First-time language gate and follow-up tutorial trigger. |

## 4. Renderer — `ui/renderer.py`

QGraphicsView-based grid. Classes:

| Class | Role |
|-------|------|
| `RendererAdapter` | Reads `state` and produces a normalised layout (rows = slots, columns = days, lanes for overlapping virtual cells). |
| `HeaderItem` | Day header / time header / corner cell. |
| `EmptySlotItem` | Drop-target for empty cells. Highlights on drag-enter. |
| `LessonItem` | Interactive lesson cell. Supports mouse press / drag / right-click context menu. Paints lecturer text, room, badge, target labels. |
| `MatrixLessonItem` | Read-only variant used in the "Show Everything" matrix. |
| `TimetableScene(QGraphicsScene)` | Assembles items into the grid based on view mode and filter mode. Emits `lessonDropped(cls, day, slot, room)`. |
| `TimetableView(QGraphicsView)` | Adds scroll, drop support, zoom (Ctrl+wheel). |

Layout constants (top of file): `COL_TIME_W=85`, `COL_DAY_W=MIN_CELL_W=150`, `ROW_HEADER_H=38`, `ROW_SLOT_H=MIN_CELL_H=70`. Cell colours come from `core/constants.py`.

Filter modes:
- `FILTER_MODE_DEFAULT` — show everything matching the current view.
- `FILTER_MODE_VIRTUAL_CLASSROOM_OVERLAP` — only show classes that share a virtual location.

## 5. Dashboard — `ui/dashboard.py`

`DashboardWidget` is a tabbed `QWidget` containing:
- Quality gauge (painted arc with grade letter overlay, A–F).
- Per-lecturer compactness `BarChartWidget`.
- Per-group compactness `BarChartWidget`.
- Room utilisation table (`QTableWidget`).
- Day-balance bar chart.
- Insights list (text bullets from `ScheduleAnalytics`).

All charts are painted with `QPainter` — no matplotlib dependency.

## 6. Reusable widgets — `ui/widgets.py`

| Class | Description |
|-------|-------------|
| `Toast` | Self-fading floating notification (bottom-right). |
| `MultiSelectButton` | Button that opens a popup menu of checkable items; used for day/slot multi-select inputs. |
| `WarningLogPanel` | Bottom-docked collapsible panel listing every warning event with icon and level. |

## 7. Translations — `ui/translations.py`

A single module-level dict `TRANSLATIONS` with 22 language blocks (en, tr, de, fr, es, zh, ru, ar, fa, it, pt_BR, pt_PT, nl, sv, da, pl, az, hi, id, af, ja, ko). Key format is **dot-namespaced** (`menus.file`, `dialogs.add_class.duration`, `errors.lecturer_required`, `weekdays.monday`, etc.). The bottom of the file exposes:
- `tr(key, **kwargs)` — translation with optional `str.format(**kwargs)` substitution; falls back to English on missing keys.
- `get_language()` / `set_language(code)` — global current language.
- `is_rtl(lang=None)` — True for Arabic/Hebrew/Persian/Urdu.

Tier-specific keys (`upgrade.*`) are merged in by `ui/tier_translations.py` on import.

## 8. Icons — `ui/icons.py`

- `_png_flag_icon(filename, size=22)` → loads from `flags/` directory.
- Programmatic icons (toolbar/menu) drawn with `QPainter` paths and shared shapes.
- Helpers exposed: `flag_gb`, `flag_tr`, `flag_de`, `flag_fr`, `flag_es`, `flag_cn`, `flag_ru`, `flag_br`, `flag_se`, `flag_dk`, `flag_it`, `flag_nl`, `flag_pl`, `flag_in`, `flag_id`, `flag_az`, `flag_za`, `flag_sa`, `flag_ir`, `flag_jp`, `flag_kr`, `flag_pt` (used by `first_run.py`).
- An `_ensure_arrow_dir()` writes temporary PNG arrows used by Qt stylesheets that need `image: url(...)`.

## 9. Day-key helpers — `ui/day_keys.py`

| Function | Purpose |
|----------|---------|
| `DAY_KEYS` | Stable list of weekday keys (`["monday","tuesday",…]`). |
| `day_label(key)` | Returns translated label via `tr("weekdays.…")`. |
| `display_day(value)` | Tries to coerce an input value (key OR translated label) into a translated display string. |
| `format_day_time(day, slot=None)` | "Monday 09:00". |
| `normalize_day_value(value)` | Translated label → key. |
| `normalize_day_list(values)` | Apply normalization to a whole list. |
| `normalize_state_day_keys(state)` | Walk the entire state dict and normalise day strings (used when restoring older saves). |

## 10. Cell + badge formatters

| File | Functions | Used by |
|------|-----------|---------|
| `ui/cell_formatter.py` | `tooltip_text(cls, ...)`, `plain_cell_text(entry)` | Renderer (tooltips), exporter (CSV/Excel plain text). |
| `ui/badge_formatter.py` | `get_badge(cls)` → `(emoji, label, color)`, `badge_text(cls)` | Renderer (badge painting), exporter (Excel rich text), tooltips. |

Badges (single source of truth, `_BADGE_MAP` in `badge_formatter.py`):

| `cls["protection"]` | Emoji | Colour | Translation key |
|---|---|---|---|
| `soft` | 🛡️ | `#D97706` | `badges.protected` |
| `same_day` | ↔ | `#2563EB` | `badges.same_day` |
| `improve_only` | ↑ | `#7C3AED` | `badges.improve_only` |
| `locked` | 🔒 | `#DC2626` | `badges.locked` |

If `cls["pinned"] == True`, the pin emoji `📌` in red `#DC2626` overrides (label key `badges.pinned`).

## 11. Tier enforcement — `ui/tier_enforcement.py`

- `TierEnforcement` is a singleton (`.instance()`). Holds the current tier slug and emits `tier_changed(str)` whenever it changes.
- `FeatureGateWidget` wraps any QWidget (typically a QPushButton). When the current tier lacks the required feature, it disables the widget and shows a tooltip. The wrapped widget is replaced (visually) by a click handler that opens `UpgradeDialog` on click.
- `gate_menu_action(action, feature)` — disables a `QAction` similarly.
- `UpgradeDialog` — polished centred dialog showing feature name, current vs required tier, pricing line, and an "Upgrade to {plan}" CTA. Offline build: `PRICING_PAGE_URL` is empty, so the CTA is a no-op and the dialog is never reached (tier defaults to `institutional`, so nothing is locked).
- `_FEATURE_TOOLTIPS` map is in `plans.py`, not here.

## 12. Crash & bug reporting — `ui/bug_report.py`

- `BugReportDialog` — manual report form (title, severity dropdown, type, expected/actual/steps fields, optional traceback).
- `CrashReportDialog` — automatic on crash (called from `_global_exception_handler`). Pre-fills exception type + message + traceback + log path. Has a "Report This Crash" button.
- All reports are composed **locally** and handed to the user's default email client via a `mailto:dersis.app@gmail.com` link (subject "DERSİS Bug Report") through `QDesktopServices.openUrl`. If no mail client is configured, the report text is copied to the clipboard and a dialog shows the address. Nothing is transmitted over the network (no `bug-report.php` POST).
- `BugReportButton` — small bug icon in the status bar; on click opens `BugReportDialog`.

## 13. Tutorial — `ui/tutorial.py`

`TutorialOverlay(QWidget)`. Steps are a list of dicts (`widget`, `title`, `body`, `section`, `action`, `widget_fn`). Paints a four-rectangle dim overlay around a spotlight cutout. Sections (`SECTION_NAME_KEYS`) include welcome, interface, setup, classes, placement, views, panels, optimization, dashboard. Emits `finished` signal on skip/complete.

## 14. UI ↔ workflow result mapping

| Workflow result dataclass | Where built | Where consumed |
|---------------------------|-------------|----------------|
| `AutoPlaceResult` | `core/workflow.py` | `ui/app.py::_apply_auto_place_result` (toast + repaint + warnings + maybe explanation dialog). |
| `ScheduleNewResult` | `core/workflow.py` | Drives `PostAddDialog` or batch summary. |
| `PlaceBatchResult` | `core/workflow.py` | Drives `BatchResolveDialog` / `OptimizationProgressDialog` completion. |
| `DropValidation` | `core/workflow.py` | `TimetableScene` callback — accepts/rejects drop, shows reason. |
| `EditClassResult` | `core/workflow.py` | `EditClassDialog` post-edit re-render. |

## 15. Threading model in the UI

Heavy work is **never** done on the main thread. Because the app is fully offline, the only background threading is the scheduler:

| QThread | File | Purpose |
|---------|------|---------|
| (`OptimizationProgressDialog` runs `optimized_reschedule_all` in a QThread inside `ui/app.py`) | | Cancellable reschedule/optimization off the UI thread. |

The thread communicates with the UI **only via `pyqtSignal`**. (The former auth/heartbeat/version/updater/account-fetch worker threads were removed in the offline conversion.)
