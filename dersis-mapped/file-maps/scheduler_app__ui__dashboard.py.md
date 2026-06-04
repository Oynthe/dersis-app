# File: `scheduler_app/ui/dashboard.py`

## 1. File Role
Analytics dashboard tab: painted bar charts, quality gauge, per-entity tables, and insights. Uses only `QPainter` — no matplotlib.

## 2. Why this file matters
Critical (visible feature). Displays the `ScheduleAnalytics` output.

## 3. Imports and Dependencies
- Third-party: `PyQt6.QtWidgets.*`, `PyQt6.QtCore.{Qt, QRectF}`, `PyQt6.QtGui.{QColor, QPainter, QFont, QPen, QBrush}`.
- Internal: `translations.tr`, `models.{effective_day, effective_time}`, `ui.day_keys.day_label`, `analytics.compute_all_metrics`, `timetable_scorer.TimetableScorer`, `schedule_analytics.ScheduleAnalytics`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `BarChartWidget(QWidget)` | Horizontal bar chart with label + value + colour. `.set_data(data, color, suffix)`. |
| `QualityGaugeWidget(QWidget)` (logical) | Painted arc + grade letter (A-F). |
| `DashboardWidget(QWidget)` | Top-level dashboard with `QTabWidget` of sections: Overview, Lecturers, Groups, Rooms, Day Balance, Insights. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–6 | docstring | |
| 8–18 | imports | |
| 22–~80 | `BarChartWidget` | painted bars with labels + numeric suffix. |
| ~80–~200 | quality gauge | arc + grade. |
| ~200–~400 | metric tables + per-entity tabs | `QTableWidget` configs. |
| ~400–525 | `DashboardWidget` | tabs assembly + refresh logic. |

## 6. Runtime Behavior
Refreshed when the analytics tab activates or the schedule changes. Computation goes through `compute_all_metrics` and `ScheduleAnalytics.analyze`.

## 7. Data Flow
- In: state (via accessor).
- Out: paint events.

## 8. UI Flow
Tab in the main window. No user input besides scrolling.

## 9. Error Handling and Edge Cases
- Empty schedule → empty gauge + empty bars (no error).
- Localised text via `tr`.

## 10. Integration Points
- Reads state directly via the SchedulerApp's `state` property.
- Uses `core.analytics.compute_all_metrics` and `core.schedule_analytics.ScheduleAnalytics`.

## 11. Risks and Maintenance Notes
- Adding a new chart → new widget class + new tab.
- Heavy schedules can make `analyze` expensive; refresh on demand only.

## 12. Mini Summary
Pure-QPainter analytics dashboard. Reads `ScheduleAnalytics` output. One tab in the main window.
