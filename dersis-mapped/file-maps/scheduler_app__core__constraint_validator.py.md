# File: `scheduler_app/core/constraint_validator.py`

## 1. File Role
Authoritative hard-constraint engine. Single class `ConstraintValidator` with pre-built occupancy maps for O(1) conflict lookups. Used by every optimizer and the workflow's drop-validation.

## 2. Why this file matters
**Critical.** Replaces `logic.respects_constraints` (deprecated). All new constraint checks should be added here.

## 3. Imports and Dependencies
- Internal: `core.logic` (slot arithmetic, occupancy primitives, `_active_targets`, `targets_overlap`, `build_occupancy`), `translations.tr`, `core.models` (capacity, availability, room candidates, day/time filters), `ui.day_keys.display_day`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `ConstraintValidator(state, exclude_ids=None)` | Builds room/lecturer/group occupancy. |
| `.respects_constraints(cls, day, slot, room)` | Class's own constraints (allowed/excluded, capacity, lecturer availability). |
| `.check_placement(cls, day, slot, room)` | Full check (constraints + occupancy). |
| `.check_placement_explained(cls, day, slot, room)` → `(bool, [reasons])` | Same with translated reasons. |
| `.find_conflicts(cls, day, slot, room)` → `[reason]` | Translated reasons. |
| `.find_conflicting_classes(cls, day, slot, room)` | Set of existing classes blocking placement. |
| `.add_placement(cls, day, slot, room)` / `.remove_placement(cls, day, slot, room)` | Mutate occupancy maps. |
| `.sort_by_difficulty(classes)` | Order classes from most-constrained to least, used by greedy ordering. |
| `._build_occupancy()` | Internal — delegates to `logic.build_occupancy`. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–22 | docstring + imports | |
| 24–172 | `ConstraintValidator` class | constructor, occupancy maps, respects_constraints, check_placement, find_conflicts, add/remove_placement, sort_by_difficulty, check_placement_explained, helpers. |
| 173–346 | Remaining methods | per-target validation, capacity error formatting, lecturer-availability checks, multi-block (joint vs sequential) handling. |

## 6. Runtime Behavior
Lightweight. Built once per optimization (or per drop validation). The occupancy maps are kept in sync via `add_placement` / `remove_placement` during the LNS loop.

## 7. Data Flow
- In: state + exclude_ids set.
- Out: bool, lists of conflict reasons, mutated occupancy maps.

## 8. UI Flow
Not applicable directly; consumed by workflow's `validate_drop`.

## 9. Error Handling and Edge Cases
- Treats `room=None` correctly for virtual classes (no room check).
- Uses `excluded` ∩ `allowed` precedence rules from `models`.
- Sequential classes: every per-target sub-block is validated independently via `_active_targets`.

## 10. Integration Points
- Used by `CandidateGenerator`, `PlacementScorer`, `ScheduleOptimizer`, `CPSATScheduler`, `SchedulingWorkflow`, `ConstraintNegotiator`, `ConstraintPropagator`.

## 11. Risks and Maintenance Notes
- Adding a new constraint: add the check in `respects_constraints`, add a translated reason, and update `check_placement_explained`.
- The occupancy maps are mutable state — never share a `ConstraintValidator` across threads.

## 12. Mini Summary
The authoritative hard-constraint engine. Use this for every "is this placement legal?" question.
