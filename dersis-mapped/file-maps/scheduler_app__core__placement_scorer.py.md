# File: `scheduler_app/core/placement_scorer.py`

## 1. File Role
Soft-objective scoring of candidate placements. 14 weighted components. Lower score = better. Supports lookahead (future-impact penalty) and neighbour-impact penalty using the conflict graph + propagator. Optional parallel batch scoring.

## 2. Why this file matters
**Critical.** Defines what "good schedule" means quantitatively.

## 3. Imports and Dependencies
- Internal: `logic.{slot_index, total_duration, _active_targets}`, `models.{get_effective_room_resource_for_class, effective_day, cls_key}`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `DEFAULT_WEIGHTS` (dict) | 14 keys: `lecturer_gap`, `lecturer_cluster`, `student_gap`, `student_cluster`, `day_overload`, `fragmentation`, `early_slot_bonus`, `day_spread`, `slot_position`, `room_switch_penalty`, `end_of_day_penalty`, `midday_bonus`, `lookahead_penalty`, `neighbor_impact_penalty`, `stability_penalty`. |
| `PlacementScorer(state, validator, weights=None, conflict_graph=None, propagator=None, parallel_pool=None, previous_placements=None)` | Main scorer. |
| `.score(cls, day, slot, room)` → float | Single-candidate scoring. |
| `.score_explained(cls, day, slot, room)` → (float, breakdown_dict) | For `ExplanationEngine`. |
| `.score_candidates(cls, candidates)` → ranked list | No lookahead. |
| `.score_candidates_with_lookahead(cls, candidates, remaining, generator)` | Heavy version. Uses propagator + optional parallel pool. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–34 | docstring | Component priority order. |
| 36–53 | `DEFAULT_WEIGHTS` | The 14-key weight profile. |
| 56–~150 | `__init__` + weight merge | Accepts overrides, normalises. |
| ~150–~300 | `score` + per-component helpers | Lecturer/student gap, fragmentation, day overload, room switch, time quality, structural bias. |
| ~300–~390 | `score_explained` | Same components but returns a breakdown dict (component → value). |
| ~390–490 | `score_candidates*` | Sorting; parallel pool dispatch; lookahead via propagator's `simulate_add`/`simulate_remove`. |

## 6. Runtime Behavior
Created once per optimization pass. Heavy lookahead path uses the propagator to amortise work.

## 7. Data Flow
- In: state, validator (with occupancy), optional graph/propagator/pool.
- Out: float scores; ranked lists of candidates.

## 8. UI Flow
Not applicable directly. `score_explained` feeds the explanation engine which feeds the UI.

## 9. Error Handling and Edge Cases
- Weight overrides are clamped at runtime by `PreferenceLearner` (in `learning/preference_learner.py`).
- Missing class fields (lecturer empty, no targets) reduce components silently — score remains finite.
- Parallel pool is optional; falls back to single-process if `None`.

## 10. Integration Points
Consumed by `ScheduleOptimizer`, `lns_strategies.RepairStrategy`, `logic.score_placement`, `score_placement_explained`.

## 11. Risks and Maintenance Notes
- The default weights are the **single source** consulted by `optimization_goals.py` and `preference_learner.py`. Adding a key here requires updating both.
- `score_explained`'s breakdown dict shape is consumed by `ExplanationEngine._COMPONENT_INFO`.

## 12. Mini Summary
The 14-component soft-objective scorer. Defaults in `DEFAULT_WEIGHTS`. Supports lookahead and parallel batch scoring.
