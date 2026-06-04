# File: `scheduler_app/core/timetable_scorer.py`

## 1. File Role
Whole-timetable quality evaluation. Used by LNS to compare candidate solutions and by the dashboard for the global score.

## 2. Why this file matters
Critical. LNS optimization decisions are accept/reject based on this score.

## 3. Imports and Dependencies
- Internal: `logic.{slot_index, total_duration, _active_targets}`, `models.get_effective_room_resource_for_class`, `placement_scorer.DEFAULT_WEIGHTS`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `TimetableScorer(state, weights=None)` | Independent of validator — builds its own occupancy snapshot per call. |
| `.score(placements)` → float | Lower is better. Aggregates lecturer/group/day/room metrics. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–17 | docstring | Component priority. |
| 19–23 | imports | |
| 26–~80 | `__init__` + state caching | Stores total slots/days for normalisation. |
| ~80–248 | `score` | Builds `lect_days`, `group_days`, `lect_rooms`, etc.; sums weighted contributions. |

## 6. Runtime Behavior
Called once per candidate solution. Lightweight relative to lookahead scoring.

## 7. Data Flow
- In: list of `(cls, day, slot, room)` tuples (entire schedule).
- Out: float.

## 8. UI Flow
Not applicable directly. Score surfaces via `ScheduleAnalytics`.

## 9. Error Handling and Edge Cases
- Empty placements → 0.0.
- Sequential classes (`is_sequential_class`) handled correctly via per-target iteration.

## 10. Integration Points
Consumed by `ScheduleOptimizer._lns_loop`, `CPSATScheduler.objective`, `logic.analyze_schedule`.

## 11. Risks and Maintenance Notes
- Weight profile must stay aligned with `PlacementScorer.DEFAULT_WEIGHTS` or the two will reward different things.
- Operates on snapshot, not the live state; safe to call concurrently from multiple threads/processes.

## 12. Mini Summary
Aggregates a full schedule into a single quality score. Used by LNS accept/reject and the dashboard.
