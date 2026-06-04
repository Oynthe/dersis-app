# File: `scheduler_app/core/analytics.py`

## 1. File Role
Pure numerical analytics over the current state: lecturer gaps, student idle time, room utilisation, busiest days/slots, underused classrooms, overloaded lecturers. No UI, no scoring.

## 2. Why this file matters
Supporting. The dashboard depends on these computations.

## 3. Imports and Dependencies
- Internal: `logic.{get_placed_classes, occupied_slots_of, classroom_of, slot_index, total_duration}`, `models.{effective_day, effective_time}`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `_build_day_slot_map(state, placed, key_fn)` | Group placements by a key into `{key: {day: [slot_indices]}}`. |
| `_count_gaps(sorted_indices)` | Idle-period count between sorted slot indices. |
| `lecturer_gap_distribution(state)` | `{lecturer: {day: gaps}}` + `{lecturer: total_gaps}`. |
| `student_idle_distribution(state)` | similar per (year, branch). |
| `room_utilization(state)` | per-room utilisation ratios. |
| `busiest_days(state)`, `busiest_slots(state)` | rankings. |
| `underused_classrooms(state, threshold=...)`, `overloaded_lecturers(state, threshold=...)` | thresholded filters. |
| `compute_all_metrics(state)` | Combines into a single dict consumed by the dashboard. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–10 | docstring | |
| 12–17 | imports | |
| 20–48 | private helpers | shared structures + gap counting. |
| 50–214 | analysis functions | each returns plain dicts/lists. |

## 6. Runtime Behavior
Stateless; called whenever the dashboard refreshes (after schedule changes).

## 7. Data Flow
state → plain dicts.

## 8. UI Flow
Consumed by `ui/dashboard.DashboardWidget`.

## 9. Error Handling and Edge Cases
- Empty placements → empty dicts.
- Zero-duration / single-class days → 0 gaps.

## 10. Integration Points
Dashboard only (in current code).

## 11. Risks and Maintenance Notes
- Distinct from `ScheduleAnalytics` (which produces the global score & grade). Don't confuse the two.

## 12. Mini Summary
Per-entity metrics. Pure functions returning plain dicts. Dashboard fuel.
