# File: `scheduler_app/ui/dialogs.py`

## 1. File Role
Every modal dialog the app shows except for login, account, bug report, update, tier upgrade, language selector, and tutorial overlay (those live in dedicated files). ~4,451 lines.

## 2. Why this file matters
**Critical.** Almost every user data-entry path passes through one of these dialogs.

## 3. Imports and Dependencies
- Third-party: `PyQt6.QtWidgets.*`, `PyQt6.QtCore.{Qt, QTimer}`, `PyQt6.QtGui.{QColor, QCursor, QShortcut, QKeySequence}`.
- Internal: `translations.{TRANSLATIONS, tr}`, `models.*`, `storage`, `logic.*`, `widgets.MultiSelectButton`, `ui.day_keys.*`.

## 4. Main Symbols (every QDialog subclass)
| Class | Purpose |
|-------|---------|
| `SetupDialog` | Tabbed editor: active days, time slots, classrooms (with capacity), lecturers (with availability), student years/branches. |
| `AddClassDialog` | Enter a single class with all attributes + constraints. |
| `EditClassDialog` | Edit an existing class. Uses `copy_editable_class_fields`. |
| `BulkAddDialog` | Grid editor for many classes at once. |
| `PlaceClassDialog` | Manual placement: pick day/slot/room from filtered lists. |
| `SelectClassDialog` | Pick a class from a list. |
| `OpenSlotsDialog` | Show all valid (day, slot, room) for a class. |
| `PostAddDialog` | Post-add chooser: auto place / manual / skip. |
| `OptimizationGoalsDialog` | 6 sliders + preset dropdown. |
| `OptimizationProgressDialog` | Cancellable progress dialog driving the QThread that runs `reschedule_all`. |
| `ScheduleAnalyticsDialog` | Shows post-reschedule analytics + before/after delta. |
| `NegotiationDialog` | Lists suggestions from `ConstraintNegotiator`; applies them on confirm. |
| `BatchResolveDialog` | Resolve unplaced classes by relaxing/unpinning per row. |
| `WarningsDialog` | Aggregated warnings. |
| `ImportPreviewDialog` | Shows `DataValidationReport`; lets user accept/reject. |
| `RecentFilesDialog` | Lists recent saves. |
| `AboutDialog` | App info + license + website. |
| `LanguageDialog` | Language picker accessible after first run (separate from `first_run.LanguageSelectorDialog`). |

## 5. Block-by-block code map (logical sections)
| Section (approx.) | Purpose |
|-------------------|---------|
| 1–32 | Imports. |
| 34–~150 | Shared `_DIALOG_STYLESHEET_TEMPLATE` and helpers. |
| ~150–~600 | `SetupDialog`. |
| ~600–~1100 | `AddClassDialog` + `EditClassDialog` (share constraint widgets). |
| ~1100–~1500 | `BulkAddDialog`. |
| ~1500–~1800 | `PlaceClassDialog`, `SelectClassDialog`, `OpenSlotsDialog`, `PostAddDialog`. |
| ~1800–~2300 | `OptimizationGoalsDialog` + `OptimizationProgressDialog`. |
| ~2300–~2900 | `ScheduleAnalyticsDialog` + `NegotiationDialog`. |
| ~2900–~3300 | `BatchResolveDialog`, `WarningsDialog`. |
| ~3300–~3800 | `ImportPreviewDialog`, `RecentFilesDialog`. |
| ~3800–4451 | `AboutDialog`, `LanguageDialog`, misc helpers. |

## 6. Runtime Behavior
Each dialog modal; main window blocked until accept/reject. Long operations (reschedule) move to QThreads with `OptimizationProgressDialog` cancellation.

## 7. Data Flow
- In: state dict + per-dialog parameters.
- Out: on accept, return values consumed by the caller (which mutates state via the workflow).

## 8. UI Flow
Triggered from menus/toolbar/context menus in `ui/app.py`.

## 9. Error Handling and Edge Cases
- Field validation via `validate_class_fields(cls)`; errors shown inline.
- Cancellable progress: `OptimizationProgressDialog` exposes a callback that returns False when user clicks Cancel.

## 10. Integration Points
Imported by `ui/app.py` essentially everywhere.

## 11. Risks and Maintenance Notes
- Large file; consider one-class-per-file split eventually.
- The shared stylesheet is at the top — changing it affects every dialog.
- Localisation: every label/title goes through `tr`.

## 12. Mini Summary
Every modal dialog except the auth/account/update/language/tutorial ones. Read alongside `ui/app.py` to see how each dialog is invoked.
