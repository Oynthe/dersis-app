# 07 — Scheduling and Optimization Map

## Overview

The scheduling engine is split into **hard constraints** (deterministic), **soft scoring** (weighted heuristics), and **search** (greedy / LNS / CP-SAT). Everything operates on the `state` dict — no shared mutable globals. The same algorithms power both interactive placement (one class at a time) and global reschedules.

Quality scores are **"lower is better"** throughout the codebase.

## 1. Hard constraints

The authoritative list, enforced by `ConstraintValidator` (`core/constraint_validator.py`):

| Constraint | Check |
|------------|-------|
| Lecturer double-booking | Lecturer cannot be in two classes at the same `(day, slot)`. |
| Room double-booking (face-to-face only) | Physical classroom cannot be used twice. |
| Student group double-booking | Each `(year, branch)` can't attend two classes simultaneously. |
| Lecturer availability | `lecturer_available_at(state, lec, day, slot)` for every needed slot. |
| Allowed/excluded days/times | Excluded takes precedence over allowed; non-empty allowed implies "must be in list". |
| Allowed/excluded classrooms (face-to-face only) | Same precedence rule. |
| Room capacity | `participants ≤ room_capacity` (0 capacity == unlimited). |
| Duration fit | `slot_index(start) + duration ≤ len(state["slots"])`. |
| Pinned position respected | Pinned classes cannot be moved by any path. |
| Joint vs sequential rule | Joint → all targets share the block. Sequential → each target gets its own consecutive sub-block (offset = `target_idx * duration`). |

The validator pre-builds three occupancy maps `(day, slot) → set` keyed by room, lecturer, and `(year, branch)` so each per-slot check is **O(1)**.

## 2. Soft scoring (PlacementScorer, TimetableScorer)

### 2.1 The 14 weighted components

Defaults defined in `core/placement_scorer.py::DEFAULT_WEIGHTS`:

| Key | Default | What it penalises (positive) or rewards (negative) |
|-----|---------|----------------------------------------------------|
| `lecturer_gap` | 5.0 | Per gap slot between the lecturer's classes on the same day. |
| `lecturer_cluster` | 2.5 | Bonus (negative score) when the lecturer already has a class on that day. |
| `student_gap` | 2.5 | Per gap slot for a student group. |
| `student_cluster` | 1.2 | Bonus when the group already has a class on that day. |
| `day_overload` | 1.5 | Penalty when a day exceeds ~60 % occupancy for that lecturer. |
| `fragmentation` | 1.8 | Penalty for isolated single-class days. |
| `early_slot_bonus` | 0.3 | Mild preference for earlier slots. |
| `day_spread` | 0.05 | Tiny bias toward earlier days (tidiness). |
| `slot_position` | 0.01 | Tiny bias toward earlier slots. |
| `room_switch_penalty` | 0.8 | Penalty for switching rooms on the same day per entity. |
| `end_of_day_penalty` | 0.6 | Penalty for the last slot of the day. |
| `midday_bonus` | 0.2 | Mild preference for mid-morning slots. |
| `lookahead_penalty` | 3.0 | Per-class difficulty increase caused by this placement. |
| `neighbor_impact_penalty` | 4.0 | Penalty for constraining conflict-graph neighbours. |
| `stability_penalty` | 2.0 | Penalty for moving a class away from its previous position. |

The 14-weight profile can be **overridden** by:
- `optimization_goals.py::compute_weights(goals)` — turns 6 user-slider goals into a weight dict.
- `PreferenceLearner.get_weights()` — adds learned deltas (clamped to ±2× default).
- Explicit `weights=` parameter to `ScheduleOptimizer`, `optimized_*` helpers.

### 2.2 Per-candidate scoring (`PlacementScorer.score`)

For a single (cls, day, slot, room):
1. Compute per-slot occupancy changes simulating the placement.
2. For each of the components above, add `weight * raw_metric` to the score (or subtract for bonuses).
3. Optionally apply look-ahead: simulate the placement, ask `ConstraintPropagator.get_valid_count(other_cls)` for each still-unscheduled class, sum the reduction in valid-count, multiply by `lookahead_penalty`.
4. Optionally apply neighbour impact: walk neighbours in the `ConflictGraph` and add `neighbor_impact_penalty * Δ(neighbour's valid count)`.
5. Optionally apply stability: if `previous_placements[cls_key]` differs from candidate, add `stability_penalty`.

`score_explained` returns `(score, breakdown_dict)` for the explanation engine.

### 2.3 Whole-timetable scoring (`TimetableScorer.score`)

Used by LNS to compare destroy/repair candidates. Builds:
- `lect_days[lecturer][day] = sorted list of slot indices`
- `group_days[(year,branch)][day] = sorted list`
- `lect_rooms[(lecturer,day)] = set of rooms`
- per-lecturer / per-group / per-day totals

Then sums gap, cluster, fragmentation, overload, room-switch, time-quality components weighted by the same 14-weight profile.

## 3. Candidate generation

`CandidateGenerator(state, validator=…).generate(cls)`:
1. Compute the **search space**: filter days, slots, rooms by the class's own constraints (`allowed_*`, `excluded_*`, `required_classrooms`, `excluded_classrooms`) and by lecturer availability.
2. Drop slots that can't fit `total_duration(cls)`.
3. For each remaining `(day, slot, room)` triple, call `validator.check_placement(cls, day, slot, room)` (O(1) hits against the occupancy maps).
4. Return the list of legal candidates.

If the list is empty, `unplaced_reason(cls)` produces a human-readable reason string keyed off translation keys (`negotiation.no_room_capacity`, `negotiation.all_slots_occupied`, etc.).

## 4. Search strategies

### 4.1 Backtracking + scoring fast path (`logic.py::_solve_backtrack`)

Older greedy fallback used by `batch_schedule` and `auto_place_class` when the AI optimizer isn't invoked. Picks classes in order of `_constraint_tightness`, tries options sorted by `_score_placement` (a lighter inline scoring function focused on lecturer/group gaps + structural tidiness), backtracks up to ~50–100 k iterations, keeps the best partial solution.

### 4.2 ScheduleOptimizer (`schedule_optimizer.py`)

Main optimization pipeline. Phases:

| Phase | Method | What it does |
|-------|--------|--------------|
| **1. Greedy construct** | `_greedy_construct` | Sort by difficulty (smallest valid-count first), iterate; for each class generate candidates → score with look-ahead → pick best. |
| **2. LNS improve** | `_lns_loop` | Pick a destroy strategy (`AdaptiveStrategySelector`), remove `destroy_size` classes (default ~20 % of flexible), repair with `RepairStrategy`, accept if quality improves. Repeats until `time_budget` or `no_improve_limit`. |
| **3. Multi-start** | `_multi_start` | Run N independent passes (default 5) with perturbed orderings; keep overall-best result. Wall-clock cap `multi_start_time_limit` (default 120 s). |
| **4. CP-SAT refinement** | `_cpsat_refine` | If `use_cpsat=True` and `ortools` is importable, run `CPSATScheduler.solve_with_seed(best)` for `cpsat_time_limit` (default 15 s). |
| **Final** | `_build_result` | Diff against initial placements, build `(placed, unplaced, changes, summary)` tuple. `summary` includes before/after quality, count of improvements. |

### 4.3 LNS strategies (`lns_strategies.py`)

| Destroy strategy | What it removes |
|------------------|-----------------|
| `LecturerGapDestroy` | Classes contributing to lecturer-day gaps. |
| `StudentGapDestroy` | Classes contributing to student-group gaps. |
| `FragmentDestroy` | Isolated single-class days. |
| `RoomSwitchDestroy` | Classes on a day where a room is used by another class right next to it. |
| `RandomDestroy` | Uniformly random. |
| `WorstScoreDestroy` | Classes with highest per-class score contribution. |
| `ConflictClusterDestroy` | A connected component of the conflict graph (via `ConflictAnalyzer.connected_components`). |

| Repair strategy | What it does |
|-----------------|--------------|
| `RepairStrategy.repair` | Re-insert removed classes one by one using scored candidate selection (same as phase 1). Returns the resulting partial solution. |

`AdaptiveStrategySelector` keeps per-strategy success rates and biases toward the most productive strategies (epsilon-greedy).

### 4.4 CP-SAT solver (`cpsat_scheduler.py`)

Encodes the same problem as a CP-SAT model:

- Decision variables per flexible class: one for `day_idx`, one for `slot_idx`, optionally `room_idx`.
- Hard constraints:
  - `AddNoOverlap` on intervals per lecturer.
  - `AddNoOverlap` on intervals per `(year,branch)`.
  - `AddNoOverlap` on intervals per room (face-to-face only).
  - `OnlyEnforceIf` for allowed/excluded days/times/rooms.
  - Equality enforcement for pinned classes.
  - Capacity check.
- Objective: weighted sum of penalty terms mirroring `TimetableScorer` (compactness, fragmentation, day balance, room switching).
- Seeded with the heuristic solution (`solver.parameters.cp_model_presolve = True` and `SearchForAllSolutions=False`).
- Returns improved schedule or `None` if no improvement found within `time_limit`.

The module is **lazily imported** (`from ortools.sat.python import cp_model`, guarded by `HAS_ORTOOLS`). If `ortools` is missing the whole CP-SAT phase is skipped silently.

## 5. Conflict graph (`conflict_graph.py`)

`ConflictGraph` is an adjacency-list graph where nodes are class indices and edges carry a conflict type (`lecturer`, `group`, `room_constraint`) and a weight.

`ConflictGraphBuilder.build(state, classes)` → walks every pair and emits edges.

`ConflictAnalyzer`:
- `connected_components()` — BFS-based, used by `ConflictClusterDestroy`.
- `centrality_ranking()` — picks high-degree nodes first (used by greedy ordering when enabled).

Used both by `ScheduleOptimizer` (during greedy/LNS) and by analytics (`logic.py::analyze_conflict_graph`).

## 6. Constraint propagation (`constraint_propagator.py`)

`ConstraintState` caches per-class valid-placement counts; `ConstraintPropagator` performs reversible add/remove operations that update only the affected classes (those sharing entity keys via `_entity_to_classes` reverse index). This replaces the O(all classes × all slots) recomputation pattern with O(affected) work, enabling fast look-ahead inside `PlacementScorer.score_candidates_with_lookahead`.

## 7. Constraint negotiation (`constraint_negotiator.py`)

Triggered whenever a class cannot be placed (manual or auto) or after `optimized_reschedule_all` leaves unplaced classes.

| Class | Role |
|-------|------|
| `InfeasibilityAnalyzer` | For each unplaced class, classifies the reason: no_days, no_times, no_rooms, lecturer_conflict, room_conflict, group_conflict, capacity, mixed. |
| `RelaxationSuggester` | Generates ranked suggestions: "Allow Tuesday", "Unpin class X", "Increase room R capacity to 30", "Remove constraint Z from class Y". Uses conflict graph + propagator. |
| `NegotiationReportBuilder` | Combines analysis + suggestions into a user-readable report dict. |
| `ConstraintNegotiator` | Orchestrates the above. `negotiate_after_optimization(placed, unplaced)` is the main entry point. `apply_suggestion(cls, suggestion)` mutates the class to apply a single relaxation. |

User-facing UI: `NegotiationDialog` in `dialogs.py`.

User settings (auto-apply low-risk suggestions, severity threshold, etc.) live in `~/Documents/Dersis/settings/negotiation_settings.egu`.

## 8. Explanation engine (`explanation_engine.py`)

Turns the structured breakdown produced by `PlacementScorer.score_explained` into a human-readable dict:

- **Pros** — components whose contribution improved the score below a threshold.
- **Cons** — components whose contribution worsened the score.
- **Rejection reasons** — translation keys grouped by category (`room_conflicts`, `lecturer_conflicts`, `group_conflicts`, `capacity_violations`, `constraint_violations`).
- **Summary line** — a single localised sentence such as "Best slot found: small lecturer gap, no room switch, slight early-day bias."

`_COMPONENT_INFO` at the top of the file maps each scoring key to label / positive / negative translation keys.

## 9. Analytics

Two distinct modules:

| Module | Purpose |
|--------|---------|
| `core/analytics.py` | Raw per-entity computations: `lecturer_gap_distribution`, `student_idle_distribution`, `room_utilization`, busiest days/slots, underused classrooms, overloaded lecturers. Returns plain dicts. |
| `core/schedule_analytics.py` | Higher-level quality analysis: `analyze(placements)` returns global score, A-F grade, per-lecturer/per-group/per-room metrics, day balance, insights list, comparison delta. Used by the dashboard and post-reschedule dialog. |

## 10. Workflow orchestration (`core/workflow.py`)

`SchedulingWorkflow(state, weights=None, learner=None, feedback_logger=None, ...)` exposes:

| Method | Returns | Used by |
|--------|---------|---------|
| `auto_place_class(cls)` | `AutoPlaceResult` | "Auto place" menu and context. |
| `schedule_new_classes(new_classes)` | `ScheduleNewResult` | Add-class dialog, bulk add, import. |
| `place_batch(classes)` | `PlaceBatchResult` | Batch place action. |
| `reschedule_all(progress_callback=…)` | tuple `(placed, unplaced, changes, summary, report)` | Optimization progress dialog. |
| `validate_drop(cls, day, slot, room)` | `DropValidation` | Renderer drop callback. |
| `edit_class(cls, updated)` | `EditClassResult` | Edit class dialog. |
| `snapshot_placements(state)` / `restore_placements(state, snap)` | dict / None | Undo / redo. |

`workflow.py` deliberately does not import Qt. All result dataclasses are plain dataclasses; UI consumes them and updates widgets.

## 11. Parallel scoring (`parallel_scorer.py`)

`ParallelScorerPool(state, n_workers=…)`:
- Calls `create_state_snapshot(state)` and `create_occupancy_snapshot(validator)` to build picklable snapshots.
- Spins up a `concurrent.futures.ProcessPoolExecutor` of workers.
- For look-ahead scoring of many candidates, distributes batches across workers and merges results.
- `n_workers=0` → auto-detect via `os.cpu_count()`; negative → disabled (single-process scoring).

Only kicks in when candidate count × remaining-classes count exceeds a threshold, otherwise the overhead isn't worth it.

## 12. Drag-drop and protection enforcement

Drop flow:
1. User drops `LessonItem` on `EmptySlotItem(day, slot, room)`.
2. Renderer calls `workflow.validate_drop(cls, day, slot, room)`.
3. `validate_drop` invokes `ConstraintValidator.check_placement_explained(cls, day, slot, room)`.
4. If invalid → `DropValidation(valid=False, reasons=[…])`. Renderer rejects, shows toast.
5. If valid:
   - If `cls["pinned"]` or `cls["protection"] == "locked"` → reject.
   - If `cls["protection"] == "same_day"` and `day != current_day` → reject with translated reason.
   - If `cls["protection"] == "improve_only"` → compute `score_placement(state, cls, …)` for new and old positions; reject if new score ≥ old score.
   - Otherwise → `mark_placed(cls, day, slot, room)` + feedback log + repaint.

## 13. Performance characteristics observed

- `_check_placement_fast` (logic.py) is O(duration × targets) — typically ≤ 20 ops.
- `_solve_backtrack` is bounded by `max_iterations` (50k-100k depending on caller).
- `ScheduleOptimizer.optimize()` budget defaults: greedy ≤ a few seconds; LNS continues until `multi_start_time_limit` (120 s by default); CP-SAT capped at 15 s. So a worst-case reschedule of a large schedule runs ≈ 2–3 minutes wall-clock with multi-start enabled.
- Parallel scoring scales roughly linearly with `n_workers` up to a few cores; serialisation overhead dominates beyond.
