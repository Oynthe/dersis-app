# File: `scheduler_app/core/parallel_scorer.py`

## 1. File Role
Process-based parallel candidate evaluation. Distributes lookahead candidate scoring across CPU cores via `ProcessPoolExecutor`. Workers receive picklable snapshots of state + occupancy, reconstruct an isolated validator/scorer, score, and return floats.

## 2. Why this file matters
Supporting (performance). Optional; can be disabled via `n_workers < 0`.

## 3. Imports and Dependencies
- stdlib: `os`, `concurrent.futures.ProcessPoolExecutor`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `create_occupancy_snapshot(validator)` | Picklable dict of occupancy maps. |
| `create_state_snapshot(state)` | Picklable dict of state. |
| `_serialize_class(cls)` | Subset serialisation. |
| `ParallelScorerPool(state_snapshot, occupancy_snapshot, weights, n_workers)` | The pool wrapper. `.score_batch(cls_dict, candidates, …)` distributes work. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–13 | docstring | |
| 17 | import os + ProcessPoolExecutor | |
| 22–~100 | snapshot helpers | Make state/occupancy picklable. |
| ~100–242 | `ParallelScorerPool` class | Constructor spawns workers; `score_batch` chunks candidates; result merging. |

## 6. Runtime Behavior
Pool created once per optimization, closed at the end. Workers re-import `scheduler_app.core.placement_scorer` and `constraint_validator` in their own process.

## 7. Data Flow
- In: state snapshot + occupancy snapshot + class dict + candidate list.
- Out: list of float scores in candidate order.

## 8. UI Flow
Not applicable.

## 9. Error Handling and Edge Cases
- If `n_workers <= 0` or `os.cpu_count() == 1`, the caller should skip the pool entirely.
- Worker failures (non-picklable, ImportError) propagate via `Future.exception()`.
- On Windows, requires `multiprocessing.freeze_support()` at program entry — wired in `scheduler_gui.py::main`.

## 10. Integration Points
Consumed by `PlacementScorer.score_candidates_with_lookahead` and `ScheduleOptimizer`.

## 11. Risks and Maintenance Notes
- Snapshot helpers must serialise every field the worker scorer needs. Adding a class field used by scoring → update `_serialize_class`.
- Process startup cost is non-trivial; the optimizer only enables the pool when candidates × remaining classes exceeds a threshold.

## 12. Mini Summary
Optional `ProcessPoolExecutor` pool for parallel candidate scoring. Snapshot-based, no shared mutable state.
