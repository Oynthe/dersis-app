# File: `scheduler_app/core/lns_strategies.py`

## 1. File Role
Large Neighborhood Search destroy and repair strategies. Destroy strategies pick weak parts of the timetable to remove; repair strategies reinsert removed classes via scored placement.

## 2. Why this file matters
Critical. LNS quality depends entirely on these strategies.

## 3. Imports and Dependencies
- stdlib: `random`.
- Internal: `logic.{slot_index, total_duration, _active_targets}`, `models.cls_key`, `placement_scorer.DEFAULT_WEIGHTS`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `DestroyStrategy(state, weights=None)` | Abstract base. `.select(solution, flexible, destroy_size, pinned_ids, protected_ids)` → list of indices to remove. |
| `LecturerGapDestroy`, `StudentGapDestroy`, `FragmentDestroy`, `RoomSwitchDestroy`, `RandomDestroy`, `WorstScoreDestroy`, `ConflictClusterDestroy` | Concrete strategies. |
| `RepairStrategy(state, weights=None)` | `.repair(solution, removed_indices, validator, generator, scorer)` → updated solution. Re-inserts using scored candidate selection. |
| `AdaptiveStrategySelector(strategies)` | Epsilon-greedy selector with per-strategy success counters. `.choose()`, `.report(strategy, improvement)`. |
| `get_destroy_strategy(name, state, weights=None)` | Factory by name. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–11 | docstring | |
| 14–20 | imports | |
| 23–~80 | base class | abstract interface. |
| ~80–~450 | concrete destroys | each picks indices based on its heuristic. |
| ~450–~580 | `RepairStrategy` | scored insertion using generator + scorer. |
| ~580–656 | `AdaptiveStrategySelector` + factory | strategy bandit. |

## 6. Runtime Behavior
Created once per LNS loop. Each destroy call is cheap; the repair call is the heavy part because it scores candidates.

## 7. Data Flow
- In: solution list, flexible classes, sizes, pinned/protected sets.
- Out: indices to remove (destroy) or updated solution (repair).

## 8. UI Flow
Not applicable.

## 9. Error Handling and Edge Cases
- Pinned and protected IDs are never removed.
- `destroy_size` is capped at the number of available flexible classes.
- `ConflictClusterDestroy` uses `ConflictAnalyzer.connected_components`; needs a graph.

## 10. Integration Points
Consumed by `ScheduleOptimizer._lns_loop`.

## 11. Risks and Maintenance Notes
- Adding a new destroy strategy: subclass `DestroyStrategy`, register via `get_destroy_strategy`, add to the adaptive selector's pool.
- Repair quality depends on the candidate generator and scorer being correctly initialised with the *current* validator state (post-destroy).

## 12. Mini Summary
Seven destroy strategies + one repair strategy + adaptive bandit selector. The LNS engine's tactics.
