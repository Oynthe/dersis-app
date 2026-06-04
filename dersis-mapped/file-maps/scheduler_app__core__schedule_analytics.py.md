# File: `scheduler_app/core/schedule_analytics.py`

## 1. File Role
Higher-level quality analysis: global score, A-F grade, per-lecturer/per-group/per-room metrics, day balance, insights (localised actionable suggestions).

## 2. Why this file matters
Critical. Drives the analytics dashboard and the post-reschedule dialog.

## 3. Imports and Dependencies
- Internal: `logic.{slot_index, total_duration, _active_targets}`, `models.get_effective_room_resource_for_class`, `translations.tr`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `ScheduleAnalytics(state)` | Analyses a complete schedule. |
| `.analyze(placements)` → dict | Keys: `global_score`, `grade`, `lecturer_metrics`, `group_metrics`, `room_metrics`, `day_balance`, `insights`, `total_classes`. |
| `._empty_report()` | Default report when no placements. |
| Internal grading helpers (score → letter). | |
| Insight generators (high-gap lecturer, high room utilisation, fragmented days, …). | Each returns a localised string from `analytics.*` keys. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–10 | docstring | |
| 12–16 | imports | |
| 19–~80 | `__init__` + `analyze` setup | Build per-entity day-slot lists. |
| ~80–~300 | metric computation | gaps, compactness, balance. |
| ~300–~400 | grading | linear mapping from `global_score` to A-F. |
| ~400–474 | insight generation | thresholded localised messages. |

## 6. Runtime Behavior
Called from `logic.analyze_schedule`, which is invoked by the dashboard and the post-reschedule dialog.

## 7. Data Flow
- In: placements list.
- Out: structured dict.

## 8. UI Flow
Consumed by `ui/dashboard.DashboardWidget` and `ScheduleAnalyticsDialog`.

## 9. Error Handling and Edge Cases
- Empty placements → `_empty_report()`.
- Locale-aware messages via `tr()`.

## 10. Integration Points
Called via `logic.analyze_schedule` wrapper.

## 11. Risks and Maintenance Notes
- The grade thresholds and insight thresholds are hardcoded constants — tune carefully.
- Insight strings must exist in every supported language.

## 12. Mini Summary
A-F grade + structured metrics + localised insights. The dashboard's data source.
