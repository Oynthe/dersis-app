# File: `scheduler_app/core/cpsat_scheduler.py`

## 1. File Role
Google OR-Tools CP-SAT wrapper. Translates the scheduling problem into a constraint model with hard constraints + soft objectives, optionally seeded with a heuristic solution. Returns improved timetable or `None`.

## 2. Why this file matters
Critical. The deep-optimization layer when fast heuristics aren't enough.

## 3. Imports and Dependencies
- stdlib: `time`.
- Third-party (lazy/guarded): `from ortools.sat.python import cp_model` (`HAS_ORTOOLS` boolean).
- Internal: `logic.{slot_index, total_duration, _active_targets}`, `models.{cls_key, room_fits_class, needs_physical_room, get_physical_room_candidates, filter_class_days, filter_class_times, apply_lecturer_availability_filters}`, `placement_scorer.DEFAULT_WEIGHTS`, `timetable_scorer.TimetableScorer`, `translations.tr`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `HAS_ORTOOLS` | Detection flag. |
| `_cpsat_status_label(status)` | Translated status string. |
| `CPSATScheduler(state, weights=None, time_limit=15.0, protected_ids=None, progress_callback=None)` | The solver. |
| `.solve(...)` / `.solve_with_seed(heuristic_solution)` | Build model, optionally seed, run solver. |
| `._build_variables`, `._add_hard_constraints`, `._add_soft_objective`, `._extract_solution` | Internal model builders. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–19 | docstring | Architecture summary. |
| 21–37 | imports + HAS_ORTOOLS guard | |
| 40–60 | `_cpsat_status_label` | Maps OR-Tools status enums to translation keys. |
| 63–~150 | `CPSATScheduler.__init__` + setup | Stores knobs, prepares helpers. |
| ~150–~400 | `_build_variables` | Per-class day/slot/room variables with valid-domain restrictions. |
| ~400–~620 | `_add_hard_constraints` | NoOverlap per lecturer/group/room; allowed/excluded; pinned equality; capacity. |
| ~620–~720 | `_add_soft_objective` | Weighted penalty terms mirroring `TimetableScorer`. |
| ~720–773 | `solve` + `_extract_solution` | Run solver, extract assignment, return updated placements. |

## 6. Runtime Behavior
Synchronous; runs within the time limit. Designed to be called in a subprocess (`ScheduleOptimizer._cpsat_refine`).

## 7. Data Flow
- In: state snapshot, optional seed solution.
- Out: list of `(day, slot, room)` per class — or None on no improvement.

## 8. UI Flow
Not applicable directly; progress is reported through the optimizer.

## 9. Error Handling and Edge Cases
- `HAS_ORTOOLS=False` → constructor raises a translated error (caller is expected to check first).
- Pinned classes are encoded as equality constraints — infeasibility would surface as `INFEASIBLE` status.
- Wall-clock cap honoured via solver parameters.

## 10. Integration Points
Called by `ScheduleOptimizer._cpsat_refine`. Optionally exposed via `optimized_reschedule_all(use_cpsat=True)`.

## 11. Risks and Maintenance Notes
- Encoding must mirror `ConstraintValidator` exactly or the two engines disagree.
- New constraints must be added both to `_add_hard_constraints` and to the heuristic validator.
- Soft-objective weights must match `TimetableScorer` so the two scoring functions are comparable.

## 12. Mini Summary
CP-SAT wrapper for global deep optimization. Falls back gracefully when `ortools` isn't installed.
