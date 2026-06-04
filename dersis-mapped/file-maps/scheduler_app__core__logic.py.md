# File: `scheduler_app/core/logic.py`

## 1. File Role
Core scheduling primitives + legacy solver + bridge to the AI optimization stack. Provides conflict detection, occupancy maps, fast backtracking solver, legacy heuristic scoring, the `optimized_*` entry points that delegate to `ScheduleOptimizer`, and analytics wrappers.

## 2. Why this file matters
**Critical.** Despite the existence of the newer `core/workflow.py`, much code (UI, exporter, tests) still calls into this module directly. The `optimized_auto_place`, `optimized_batch_schedule`, `optimized_reschedule_all` functions are the AI bridge consumed by the workflow layer.

## 3. Imports and Dependencies
- Internal: `scheduler_app.constants.YEAR_COLORS`, `scheduler_app.models` (many helpers), `scheduler_app.translations.tr`, `scheduler_app.ui.day_keys.display_day`.
- Lazy internal: `scheduler_app.schedule_optimizer.ScheduleOptimizer`, `constraint_validator.ConstraintValidator`, `candidate_generator.CandidateGenerator`, `placement_scorer.PlacementScorer`, `explanation_engine.ExplanationEngine`, `schedule_analytics.ScheduleAnalytics`, `conflict_graph.{ConflictGraphBuilder, ConflictAnalyzer}`, `constraint_propagator.{ConstraintState, ConstraintPropagator}`, `constraint_negotiator.ConstraintNegotiator`.

## 4. Main Symbols (selected)
| Symbol | Lines | Purpose |
|--------|-------|---------|
| `slot_index(state, slot)`, `slots_fit(state, start, duration)`, `total_duration(cls)` | 17–34 | Slot arithmetic. |
| `build_virtual_classroom_day_layout(state, filter_fn)` | 37–141 | Lane-assigned virtual-classroom view layout for renderer. |
| `get_consecutive_slots(state, start, duration)`, `get_placed_classes(state)`, `occupied_slots_of(state, cls)`, `target_for_slot_offset(cls, offset)`, `classroom_of(cls)`, `targets_overlap(targets_a, targets_b)`, `_active_targets(cls, offset)` | 144–205 | Occupancy primitives. |
| `_detect_occupancy_conflicts(state, cand, day, slot, room)` | 208–247 | Single source of truth for occupancy conflict detection. Yields `(existing, slot_name, conflict_type)`. |
| `find_conflicts(state, cand, day, slot, room)` | 250–288 | Translated conflict messages. |
| `respects_constraints(cand, day, slot, room, state=None)` | 291–314 | **Deprecated** — use `ConstraintValidator`. |
| `find_valid_options(state, cand)` | 317–336 | All legal (day, slot, room). |
| `find_conflicting_classes(state, cand, day, slot, room)` | 339–372 | Returns the actual class dicts in conflict. |
| `_unplace`, `_find_candidate_slots`, `cascade_relocate` | 375–482 | Pinned-class cascade-relocation algorithm with rollback. |
| `get_year_color(state, year_name)` | 485–489 | Colour cycle. |
| `build_occupancy(state, exclude_ids=None)` | 492–519 | Three `(day,slot)→set` maps. |
| `_check_placement_fast`, `_add_to_occupancy`, `_remove_from_occupancy`, `_get_valid_slots`, `_compactness_gap`, `_score_placement`, `_constraint_tightness`, `_solve_backtrack`, `_unplaced_reason` | 522–777 | Fast inline solver used by `batch_schedule` and `auto_place_class`. |
| `batch_schedule(state, new_classes)` | 780–898 | Two-phase placement (preserve existing, then full reschedule). |
| `auto_place_class(state, new_cls)` | 901–1027 | Same two-phase logic for one class. |
| `reschedule_all(state)` | 1030–1102 | Legacy full re-optimization. |
| `lighten_color(hex_color, factor=0.45)` | 1105–1112 | Colour helper. |
| `optimized_auto_place`, `optimized_reschedule_all`, `optimized_batch_schedule` | 1121–1275 | AI bridge — delegate to `ScheduleOptimizer`. |
| `score_placement`, `score_placement_explained`, `check_placement_explained` | 1278–1322 | Convenience scoring/check wrappers. |
| `analyze_schedule`, `analyze_conflict_graph`, `analyze_constraint_propagation` | 1325–1431 | Analytics wrappers. |
| `negotiate_after_optimization`, `apply_negotiation_suggestion` | 1434–1456 | Negotiator wrappers. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–15 | imports + docstring | Pull in models, day_keys, constants, translations. |
| 17–34 | slot arithmetic | Used everywhere. |
| 37–141 | `build_virtual_classroom_day_layout` | Multi-lane day layout for virtual-classroom views. |
| 144–205 | occupancy primitives | `get_placed_classes`, `occupied_slots_of`, etc. |
| 208–288 | conflict detection | `_detect_occupancy_conflicts` + `find_conflicts`. |
| 291–336 | option enumeration + legacy validator | `find_valid_options`. |
| 339–372 | who-conflicts list | for relocation. |
| 375–482 | cascade relocation | for pinned classes that displace others. |
| 485–489 | year colour | small. |
| 492–519 | `build_occupancy` | the canonical occupancy builder. |
| 522–588 | fast occupancy mutations + slot listing | used inside solver. |
| 591–669 | scoring helpers | inline scoring used by legacy backtracker. |
| 672–684 | tightness | constrains ordering. |
| 686–777 | `_solve_backtrack` + `_unplaced_reason` | legacy backtracking solver with iteration cap. |
| 780–898 | `batch_schedule` | two-phase placement. |
| 901–1027 | `auto_place_class` | single-class two-phase. |
| 1030–1102 | `reschedule_all` | legacy global re-opt. |
| 1105–1112 | colour utility | for renderer/exporter. |
| 1115–1456 | AI bridge + analytics + negotiation wrappers | new code paths used by `core/workflow.py`. |

## 6. Runtime Behavior
Pure stateless functions. The `optimized_*` and `analyze_*` wrappers construct fresh helper objects per call.

## 7. Data Flow
- `state` dict in → modified in place by `_solve_backtrack`/`mark_placed` callers, or returned as part of a result tuple.
- Conflict-message strings are translated via `tr()`.

## 8. UI Flow
Not directly, but the layout helper `build_virtual_classroom_day_layout` produces the structures consumed by `ui/renderer.py`.

## 9. Error Handling and Edge Cases
- `cascade_relocate`: rolls back the state if any displacement can't be placed.
- `_solve_backtrack`: iteration cap to avoid hangs.
- Lecturer unavailability shows up as `conflicting.add(cls_key(candidate))` (sentinel — candidate itself is "in conflict").
- `respects_constraints` is deprecated; new code should use `ConstraintValidator.check_placement`.

## 10. Integration Points
- Imports `scheduler_app.models` heavily; calls into `ScheduleOptimizer` and friends through lazy imports.
- Called by `core/workflow.py`, `ui/app.py`, `ui/renderer.py`, `data_io/exporter.py`, all tests.

## 11. Risks and Maintenance Notes
- Two competing conflict-detection paths (`find_conflicts` here vs `ConstraintValidator.find_conflicts`). Keep them in sync.
- The `optimized_*` wrappers re-export the optimizer's signature; if `ScheduleOptimizer.__init__` changes, update the keyword arguments here.
- The `_solve_backtrack` solver is purely depth-first with optional preferred-slot biasing — do not rely on it for hard problems where the AI optimizer would do better.
- `_score_placement` is a *different* scoring function from `PlacementScorer.score`. They use different weight profiles.

## 12. Mini Summary
The legacy scheduling core + bridge into the AI optimizer. Read this file alongside `core/schedule_optimizer.py` and `core/constraint_validator.py` to understand the full placement pipeline.
