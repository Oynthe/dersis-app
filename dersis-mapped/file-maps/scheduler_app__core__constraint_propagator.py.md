# File: `scheduler_app/core/constraint_propagator.py`

## 1. File Role
Incremental constraint propagation for fast lookahead. `ConstraintState` caches per-class valid-placement counts; `ConstraintPropagator` performs reversible add/remove operations that only touch the classes affected by a change. Replaces O(all-classes × all-slots) recomputation with O(affected) work.

## 2. Why this file matters
Supporting (but a major performance lever). Without it, lookahead scoring would be too slow for large schedules.

## 3. Imports and Dependencies
- Internal: `logic.{total_duration, slot_index, _active_targets}`, `models.{needs_physical_room, get_physical_room_candidates, cls_key}`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `ConstraintState(state, validator, generator, classes)` | Holds `_valid_counts` cache + `_entity_to_classes` reverse index. `.get_valid_count(cls)`. |
| `ConstraintPropagator(cs)` | Reversible add/remove placement; calls `cs.invalidate(...)` for affected classes. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–13 | docstring | |
| 16–21 | imports | |
| 24–~150 | `ConstraintState` | Constructor builds reverse index; `_build_entity_index` walks every class's lecturer/rooms/group keys. Lazy init of `_valid_counts`. |
| ~150–235 | `ConstraintPropagator` | `simulate_add(cls, day, slot, room)`, `simulate_remove(...)`. Each updates the validator's occupancy maps AND invalidates affected `_valid_counts`. |

## 6. Runtime Behavior
Created once per optimization. Updated thousands of times during LNS — must remain cheap.

## 7. Data Flow
- In: state + validator + generator + flexible classes.
- Out: cached valid-count integers per class.

## 8. UI Flow
Not applicable.

## 9. Error Handling and Edge Cases
- Lazy-initialised counts: first `get_valid_count(cls)` call computes from scratch, subsequent calls are O(1).
- Reverse index handles lecturer + (year,branch) + physical room keys.

## 10. Integration Points
Consumed by `PlacementScorer.score_candidates_with_lookahead`, `logic.analyze_constraint_propagation`.

## 11. Risks and Maintenance Notes
- Reverse index is built once at construction — if classes are added mid-optimization, you must rebuild.
- The propagator must be paired with corresponding validator updates; using one without the other will desync.

## 12. Mini Summary
Caches and incrementally updates per-class valid-placement counts so lookahead scoring is fast.
