# File: `scheduler_app/ui/widgets.py`

## 1. File Role
Reusable PyQt6 widgets used across the app: `Toast`, `MultiSelectButton`, `WarningLogPanel`. (The former `AuthStatusIndicator`/`_StatusDot` connection-status widgets were removed during the offline conversion.)

## 2. Why this file matters
Supporting (UI affordances). The warning log + toasts are visible feedback channels.

## 3. Imports and Dependencies
- Third-party: `PyQt6.QtWidgets`, `PyQt6.QtCore.{QTimer, Qt, QPropertyAnimation, QEasingCurve, QEvent}`, `PyQt6.QtGui.{QColor, QAction, QPainter, QBrush, QPen}`.
- Internal: `translations.tr`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `Toast` | Floating notification window with fade-out animation. `.show_message(text, kind='info', duration=3000)`. |
| `MultiSelectButton` | Button that opens a popup menu of `QCheckBox` items. Used for day/slot multi-select. Emits `selectionChanged`. |
| `WarningLogPanel` | Bottom-dock collapsible list of warnings with level icons. Append-only. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–11 | docstring + imports | |
| ~14–~80 | `Toast` | self-positioning, animated opacity. |
| ~80–~190 | `MultiSelectButton` | popup menu with checkboxes; selection list as property. |
| ~190–end | `WarningLogPanel` | scrollable list, level → colour mapping, append API. |

## 6. Runtime Behavior
- `Toast.show_message` scheduled via `QTimer`.
- `MultiSelectButton.selectionChanged` triggered when popup closes.
- `WarningLogPanel.append_warning(text, level)` called from many places (drop rejection, optimisation failure, validation errors).

## 7. Data Flow
Local widget state; no persistence.

## 8. UI Flow
Used in the status bar (warning log), toolbar, and various dialogs.

## 9. Error Handling and Edge Cases
- Toast positioning relative to its parent; if no parent, falls back to the active window.
- `MultiSelectButton` handles empty option lists.

## 10. Integration Points
Imported by `ui/app.py` (main UI + status bar) and `ui/dialogs.py` (MultiSelectButton in many dialogs). The status bar now hosts only the bug-report button (no connection indicator).

## 11. Risks and Maintenance Notes
- Hardcoded colours; if the theme changes globally, update `Toast.COLORS`.
- `Toast` uses `QPropertyAnimation` on `windowOpacity` — must run on the main thread.

## 12. Mini Summary
Small reusable widgets: `Toast`, `MultiSelectButton`, `WarningLogPanel`. The online auth/connection indicator was removed with the offline conversion.
