# File: `scheduler_app/core/schedule_optimizer.py`

## 1. File Role
Top-level optimization pipeline: greedy construction → LNS improvement → multi-start → optional CP-SAT refinement. 1044 lines.

## 2. Why this file matters
**Critical.** The orchestrator for global rescheduling. Plus, `optimized_*` helpers in `core/logic.py` delegate here.

## 3. Imports and Dependencies
- stdlib: `copy`, `math`, `multiprocessing`, `random`, `time`.
- Internal: many — `logic`, `models.cls_key`, `constraint_validator`, `candidate_generator`, `placement_scorer`, `timetable_scorer`, `lns_strategies`, `conflict_graph`, `constraint_propagator`, `parallel_scorer`.
- Lazy: `cpsat_scheduler.CPSATScheduler` (only if `use_cpsat=True` and `ortools` importable).

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `_make_cpsat_state_snapshot(state)` | Builds a deep-copyable state for the CP-SAT subprocess (separate from `parallel_scorer` snapshots). |
| `ScheduleOptimizer(state, weights=None, protected_ids=None, progress_callback=None, multi_start_runs=5, multi_start_time_limit=120.0, use_cpsat=False, cpsat_time_limit=15.0, parallel_workers=0)` | The pipeline. |
| `.optimize()` → `(placed, unplaced, changes, summary)` | Full multi-phase. Called by `optimized_reschedule_all`. |
| `.place_with_reschedule(new_cls)` | Auto-place a single class with optional full reschedule on failure. Called by `optimized_auto_place`. |
| `._greedy_construct(classes, validator, generator, scorer)` | Phase 1. Returns `(solution_list, stats_dict)`. |
| `._lns_loop(initial, ...)` | Phase 2. Destroy → repair → accept-if-better loop. |
| `._multi_start(...)` | Phase 3. Run N independent passes. |
| `._cpsat_refine(best_solution)` | Phase 4. Subprocess CP-SAT seeded with heuristic. |
| `._build_result(...)` | Diff against previous placements; build summary dict. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–18 | docstring + architecture summary | The four phases. |
| 21–35 | imports | |
| 38–~90 | `_make_cpsat_state_snapshot` | Deep-copy state with serialised classes for the CP-SAT subprocess. |
| ~90–~300 | `ScheduleOptimizer.__init__` + `optimize` | Pipeline driver; honours protected IDs. |
| ~300–~520 | `_greedy_construct` | Difficulty-ordered greedy with lookahead scoring + propagator updates. |
| ~520–~780 | `_lns_loop` + adaptive strategy selection | Iterative destroy/repair. |
| ~780–~900 | `_multi_start` | Run multiple independent passes with perturbed orderings; keep best. |
| ~900–~1000 | `_cpsat_refine` | Spawns subprocess; runs CP-SAT for `cpsat_time_limit`. |
| ~1000–1044 | `_build_result` + helpers | Compute changes, summary, before/after quality. |

## 6. Runtime Behavior
Synchronous. Long-running. Driven by a QThread in the UI with a `progress_callback` updating the `OptimizationProgressDialog`.

## 7. Data Flow
- In: state, weights, protected_ids, callback, knobs.
- Out: placed/unplaced lists, change records, summary dict (before/after quality, strategy stats, time spent per phase).

## 8. UI Flow
Driven by `ui/app.py` reschedule action → `OptimizationProgressDialog` (cancellable via `progress_callback`).

## 9. Error Handling and Edge Cases
- Protected IDs are excluded from destroy/move operations.
- CP-SAT phase silently skipped if `ortools` is missing.
- Multi-start ensures at least one phase succeeds even if a single run gets stuck on a bad neighbourhood.
- `progress_callback` may raise `OptimizationCancelled` (or return False); the optimizer respects cancellation between phases.

## 10. Integration Points
- Called via `core.logic.optimized_*` wrappers.
- Uses every other major core module.

## 11. Risks and Maintenance Notes
- Large file with many tunables; adding a new knob requires plumbing through constructor + each phase.
- The CP-SAT phase's subprocess is heavy; users on small machines might want `use_cpsat=False` by default.
- Strategy selector state is in-memory only — not persisted across runs.

## 12. Mini Summary
Greedy + LNS + multi-start + optional CP-SAT. `optimize()` is the entry point used by global reschedule; `place_with_reschedule()` is the auto-place entry point.
