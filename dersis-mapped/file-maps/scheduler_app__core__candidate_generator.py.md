# File: `scheduler_app/core/candidate_generator.py`

## 1. File Role
Generates all valid candidate placements for a given class. Pure enumeration; never picks a winner. Pairs with `ConstraintValidator` (for filtering) and `PlacementScorer` (for ranking).

## 2. Why this file matters
Critical. Every candidate-driven path (auto-place, optimization, look-ahead) starts here.

## 3. Imports and Dependencies
- Internal: `logic.{slot_index, slots_fit, total_duration}`, `models.{get_room_candidates, get_physical_room_candidates, needs_physical_room, filter_class_days, filter_class_times, apply_lecturer_availability_filters}`, `constraint_validator.ConstraintValidator`, `translations.tr`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `CandidateGenerator(state, validator=None, exclude_ids=None)` | Holds the state + validator. |
| `.get_search_space(cls)` → `(days, times, rooms)` | Pre-filtered by constraints + lecturer availability + duration fit. |
| `.generate(cls)` → list[(day,slot,room)] | All candidates passing `validator.check_placement`. |
| `.unplaced_reason(cls)` | Translated reason when `generate` returns []. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–18 | docstring + imports | |
| 21–60 | `CandidateGenerator.__init__` + `get_search_space` + `generate` | Core enumeration. |
| 60–170 | reason helpers + lazy validator construction | |

## 6. Runtime Behavior
Constructed per-optimization (or per-action). Stateless beyond holding the validator reference.

## 7. Data Flow
state + cls → list of legal placements.

## 8. UI Flow
Not applicable directly.

## 9. Error Handling and Edge Cases
- Returns `[None]` as room candidates for virtual classes (online / lecturer office) — placement code treats `None` as "no physical room needed".
- `unplaced_reason` cascades through specific reasons (no rooms by capacity, no allowed days, all slots occupied, etc.).

## 10. Integration Points
Consumed by `ScheduleOptimizer._greedy_construct`, `RepairStrategy.repair`, `PlacementScorer.score_candidates_with_lookahead`, `ConstraintNegotiator`.

## 11. Risks and Maintenance Notes
- Search space can be huge for unconstrained classes; the validator's O(1) check keeps total work bounded.
- Adding a new constraint must be reflected in `get_search_space` (for early filtering) AND in `validator.check_placement` (for correctness).

## 12. Mini Summary
Enumerates valid (day, slot, room) tuples for a class. Pure generator — does not pick a winner.
