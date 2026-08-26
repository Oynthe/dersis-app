"""Timetable-wide schedule optimizer with hybrid heuristic + CP-SAT.

Reevaluates all classes after data entry and searches for a better
overall arrangement. Preserves pinned and protected placements.

Architecture:
  Phase 1: Greedy construction with difficulty-aware ordering and
           look-ahead placement scoring.
  Phase 2: Iterative LNS improvement with adaptive strategy selection —
           repeatedly destroy weak parts of the schedule and repair them
           using scored candidate placement.
  Phase 3: Multi-start — run multiple independent optimization passes
           from perturbed starting states, keep the overall best.
  Phase 4 (optional): CP-SAT deep optimization — translate the problem
           into a Google OR-Tools constraint model and solve for a
           globally optimal assignment, seeded with the heuristic result.

The optimizer generates all valid candidates for each class, scores them
with look-ahead, and picks the best — never takes the first available slot.
"""

import copy
import math
import multiprocessing
import random
import time

from scheduler_app.logic import (
    slot_index, total_duration, get_placed_classes, classroom_of,
)
from scheduler_app.models import cls_key, DEFAULT_OPTIMIZER_SEED
from scheduler_app.core.solver_worker import SolveCancelled

# The shipped search budget. Named because the progress UI needs the same
# denominators the optimizer actually runs (ST-PERF-001): a second copy of
# these numbers elsewhere means the bar stops short of the end, or saturates
# early, the moment the two drift.
DEFAULT_MULTI_START_RUNS = 5
DEFAULT_LNS_ITERATIONS = 200
from scheduler_app.constraint_validator import ConstraintValidator
from scheduler_app.candidate_generator import CandidateGenerator
from scheduler_app.placement_scorer import PlacementScorer
from scheduler_app.timetable_scorer import TimetableScorer
from scheduler_app.lns_strategies import (
    RepairStrategy, get_destroy_strategy, AdaptiveStrategySelector,
)
from scheduler_app.conflict_graph import ConflictGraphBuilder, ConflictAnalyzer
from scheduler_app.constraint_propagator import ConstraintState, ConstraintPropagator
from scheduler_app.parallel_scorer import ParallelScorerPool


def _make_cpsat_state_snapshot(state):
    """Create a deep-copyable state snapshot for the CP-SAT subprocess.

    Includes all data that CPSATScheduler needs: days, slots, classrooms,
    classroom_capacities, lecturer_availability, and serialized classes.
    """
    def _copy_class(cls):
        return {
            "name": cls.get("name", ""),
            "lecturer": cls.get("lecturer", ""),
            "targets": [dict(t) for t in cls.get("targets", [])],
            "duration": cls.get("duration", 1),
            "participants": cls.get("participants", 0),
            "location_type": cls.get("location_type", "face_to_face"),
            "joint_session": cls.get("joint_session", True),
            "allowed_days": list(cls.get("allowed_days") or []),
            "excluded_days": list(cls.get("excluded_days") or []),
            "allowed_times": list(cls.get("allowed_times") or []),
            "excluded_times": list(cls.get("excluded_times") or []),
            "required_classrooms": list(cls.get("required_classrooms") or []),
            "excluded_classrooms": list(cls.get("excluded_classrooms") or []),
            "placed": cls.get("placed", False),
            "placed_day": cls.get("placed_day"),
            "placed_time": cls.get("placed_time"),
            "placed_classroom": cls.get("placed_classroom"),
            "pinned": cls.get("pinned", False),
            "pinned_day": cls.get("pinned_day"),
            "pinned_time": cls.get("pinned_time"),
            "pinned_classroom": cls.get("pinned_classroom"),
            "protection": cls.get("protection", "none"),
            "class_uid": cls.get("class_uid"),
        }
    return {
        "days": list(state["days"]),
        "slots": list(state["slots"]),
        "classrooms": list(state["classrooms"]),
        "classroom_capacities": dict(state.get("classroom_capacities", {})),
        "lecturer_availability": copy.deepcopy(
            state.get("lecturer_availability", {})),
        "classes": [_copy_class(c) for c in state["classes"]],
    }


def _cpsat_subprocess_worker(state_snap, weights, time_limit,
                             protected_indices, heuristic_indices,
                             result_queue, seed=DEFAULT_OPTIMIZER_SEED):
    """Run CP-SAT solver in an isolated subprocess.

    All arguments are plain serializable Python objects. Results are
    placed in *result_queue* as a dict.  If the solver crashes at the
    native level the subprocess dies without affecting the main process.
    """
    try:
        from scheduler_app.cpsat_scheduler import CPSATScheduler, HAS_ORTOOLS
        if not HAS_ORTOOLS:
            result_queue.put(None)
            return

        classes = state_snap["classes"]
        protected_ids = {cls_key(classes[i]) for i in protected_indices
                         if 0 <= i < len(classes)}

        solver = CPSATScheduler(
            state_snap, weights=weights,
            time_limit=time_limit,
            protected_ids=protected_ids,
            seed=seed)

        heuristic_solution = None
        if heuristic_indices:
            heuristic_solution = [
                (classes[idx], day, slot, room)
                for idx, day, slot, room in heuristic_indices
                if 0 <= idx < len(classes)
            ]

        placed, unplaced, info = solver.solve(
            heuristic_solution=heuristic_solution)

        if placed is None:
            result_queue.put({"status": "failed", "info": info})
            return

        cls_to_idx = {cls_key(c): i for i, c in enumerate(classes)}
        placed_out = [(cls_to_idx[cls_key(c)], d, s, r)
                      for c, d, s, r in placed]
        unplaced_out = [(cls_to_idx[cls_key(c)], reason)
                        for c, reason in unplaced]

        result_queue.put({
            "status": "ok",
            "placed": placed_out,
            "unplaced": unplaced_out,
            "info": info,
        })
    except Exception as exc:
        try:
            result_queue.put({"status": "error", "error": str(exc)})
        except Exception:
            pass


class ScheduleOptimizer:
    """Timetable-wide reschedule optimizer using hybrid heuristic + CP-SAT.

    Constructs a high-quality schedule from scratch (keeping pinned classes
    fixed) using greedy placement + iterative destroy-repair improvement.
    Runs multiple independent passes from perturbed orderings and keeps
    the best result. Optionally refines with CP-SAT constraint solver.
    """

    def __init__(self, state, weights=None, max_iterations=100000,
                 lns_iterations=DEFAULT_LNS_ITERATIONS, lns_time_limit=30.0,
                 lns_no_improve_limit=50, destroy_fraction=0.25,
                 protected_ids=None, progress_callback=None,
                 multi_start_runs=DEFAULT_MULTI_START_RUNS,
                 multi_start_time_limit=3600.0,
                 use_cpsat=False, cpsat_time_limit=15.0,
                 parallel_workers=0,
                 sa_initial_temp=2.0, sa_cooling_rate=0.995,
                 seed=DEFAULT_OPTIMIZER_SEED, deterministic_budget=True,
                 cancel_token=None):
        """
        Args:
            state: The schedule state dict.
            weights: Optional weight overrides for PlacementScorer.
            max_iterations: Cap for greedy backtracking phase.
            lns_iterations: Maximum LNS destroy-repair cycles per run.
            lns_time_limit: Time limit in seconds for LNS phase per run.
            lns_no_improve_limit: Stop LNS after this many iterations
                                  without improvement.
            destroy_fraction: Fraction of flexible classes to destroy per
                              iteration (0.0 to 1.0).
            protected_ids: Set of class ids that must not move (in addition
                           to pinned classes).
            progress_callback: Optional callable(iteration, best_score,
                               current_score, run_number, total_runs)
                               for UI progress updates.
            multi_start_runs: Number of independent optimization runs.
            multi_start_time_limit: Total time limit across all runs.
            use_cpsat: Whether to run CP-SAT solver after heuristic phase.
            cpsat_time_limit: Time limit for CP-SAT solver in seconds.
            parallel_workers: Number of worker processes for parallel
                              candidate evaluation. 0 = auto (uses
                              min(cpu_count, 4)), negative = disabled.
            seed: RNG seed. The default makes identical input produce an
                  identical timetable (ST-SCHED-013). Pass ``None`` to draw a
                  fresh seed from OS entropy — the deliberate "randomize" path.
                  The seed actually used is reported as ``summary['seed']`` so a
                  user or support case can replay a run exactly.
            deterministic_budget: When True (default), the LNS phase is bounded
                  by *iteration* counts rather than by the wall clock, so a slow
                  machine reaches the same answer as a fast one. A seed alone
                  does not achieve reproducibility: with a clock-bounded search,
                  the same seed does a different amount of work per machine and
                  lands somewhere else. ``multi_start_time_limit`` still applies
                  as an emergency cap; when it fires the run is truncated and
                  ``summary['deterministic']`` reports False.

        Note: with ``deterministic_budget=True`` (the default) ``lns_time_limit``
        no longer bounds the default path — it applies only when the budget is
        wall-clock, or when a caller passes an explicit per-run override.
        """
        self.state = state
        self.weights = weights
        self.max_iterations = max_iterations
        self.lns_iterations = lns_iterations
        self.lns_time_limit = lns_time_limit
        self.lns_no_improve_limit = lns_no_improve_limit
        self.destroy_fraction = destroy_fraction
        self.protected_ids = protected_ids or set()
        self.progress_callback = progress_callback
        self.multi_start_runs = multi_start_runs
        self.multi_start_time_limit = multi_start_time_limit
        self.use_cpsat = use_cpsat
        self.cpsat_time_limit = cpsat_time_limit
        self.parallel_workers = parallel_workers
        self.sa_initial_temp = sa_initial_temp
        self.sa_cooling_rate = sa_cooling_rate
        # Draw a randomized seed from OS entropy, never from the process-global
        # `random`: consuming that stream would make this run perturb — and be
        # perturbed by — every other user of it.
        if seed is None:
            seed = random.SystemRandom().getrandbits(63)
        self.seed = int(seed)
        self._rng = random.Random(self.seed)
        self.deterministic_budget = deterministic_budget
        self._clock_capped = False
        # ST-PERF-001. None on every path that is not a user-cancellable
        # reschedule (auto-place, batch schedule), where _check_cancelled is a
        # single `is not None` test — measured at no cost on an 80-class
        # greedy-dominated run.
        self.cancel_token = cancel_token

    def _check_cancelled(self):
        """Raise SolveCancelled if the user has asked this solve to stop."""
        if self.cancel_token is not None:
            self.cancel_token.raise_if_cancelled()

    def optimize(self):
        """Run multi-start timetable optimization.

        Returns:
            (placed_list, unplaced_list, changes, summary) where:
            - placed_list: [(cls, day, slot, room), ...]
            - unplaced_list: [(cls, reason), ...]
            - changes: list of dicts with old/new placements for moved classes
            - summary: dict with optimization statistics and quality comparison
        """
        # Separate pinned/locked/protected from flexible
        from scheduler_app.models import (
            PROTECTION_LOCKED, PROTECTION_SOFT, PROTECTION_SAME_DAY,
            PROTECTION_IMPROVE_ONLY,
        )
        pinned = [c for c in self.state["classes"] if c["pinned"]]
        # "locked" placed classes are treated identically to pinned
        locked = [c for c in self.state["classes"]
                  if not c["pinned"]
                  and c.get("protection") == PROTECTION_LOCKED and c["placed"]]
        # Merge explicit protected_ids with "soft" protection
        effective_protected_ids = set(self.protected_ids)
        for c in self.state["classes"]:
            if (not c["pinned"] and c.get("protection") == PROTECTION_SOFT
                    and c["placed"]):
                effective_protected_ids.add(cls_key(c))
        immovable_ids = ({cls_key(c) for c in pinned}
                         | {cls_key(c) for c in locked})
        protected = [c for c in self.state["classes"]
                     if not c["pinned"]
                     and c.get("protection") != PROTECTION_LOCKED
                     and cls_key(c) in effective_protected_ids
                     and c["placed"]]
        flexible = [c for c in self.state["classes"]
                    if not c["pinned"]
                    and cls_key(c) not in immovable_ids
                    and cls_key(c) not in effective_protected_ids]
        # Build same-day constraint map for "same_day" protected classes
        same_day_map = {}
        for c in flexible:
            if c.get("protection") == PROTECTION_SAME_DAY and c["placed"]:
                same_day_map[cls_key(c)] = c["placed_day"]
        # Build improve-only baseline scores: class can only move to
        # a placement with equal or better (lower) score
        improve_only_scores = {}
        flexible_placed = [c for c in flexible if c["placed"]]
        flexible_unplaced = [c for c in flexible if not c["placed"]]
        all_flexible = flexible_placed + flexible_unplaced

        # Build conflict graph once for all runs
        graph_builder = ConflictGraphBuilder(self.state, all_flexible)
        conflict_graph = graph_builder.build()

        # Create parallel scorer pool if enabled
        parallel_pool = None
        if self.parallel_workers >= 0:
            workers = self.parallel_workers if self.parallel_workers > 0 else None
            parallel_pool = ParallelScorerPool(max_workers=workers)

        # ST-PERF-001: the pool must be shut down even when SolveCancelled
        # unwinds through here, or a cancelled solve leaves its worker
        # processes alive until the next garbage collection.
        try:
            # Save old placements for change tracking
            old_placements = {}
            for cls in flexible_placed:
                old_placements[cls_key(cls)] = (
                    cls["placed_day"], cls["placed_time"], cls["placed_classroom"])

            # Score the initial timetable before optimization
            tt_scorer = TimetableScorer(self.state, weights=self.weights)

            # Compute baseline scores for improve_only classes
            for c in flexible:
                if (c.get("protection") == PROTECTION_IMPROVE_ONLY
                        and c["placed"]):
                    improve_only_scores[cls_key(c)] = tt_scorer.placement_score(
                        c, c["placed_day"], c["placed_time"],
                        c["placed_classroom"])

            before_placements = []
            for cls in pinned:
                before_placements.append((cls, cls["pinned_day"],
                                          cls["pinned_time"],
                                          cls["pinned_classroom"]))
            for cls in locked:
                before_placements.append((cls, cls["placed_day"],
                                          cls["placed_time"],
                                          cls["placed_classroom"]))
            for cls in protected:
                before_placements.append((cls, cls["placed_day"],
                                          cls["placed_time"],
                                          cls["placed_classroom"]))
            for cls in flexible_placed:
                before_placements.append((cls, cls["placed_day"],
                                          cls["placed_time"],
                                          cls["placed_classroom"]))
            # A placement the user orphaned (by deleting the hour or day it sits
            # on) describes no real cell, so it cannot contribute a "before" score.
            # Drop it here rather than deeper down: this is the layer that knows
            # these tuples came from stored state (ST-DATA-003).
            on_grid_days = set(self.state.get("days", []))
            on_grid_slots = set(self.state.get("slots", []))
            before_placements = [
                (c, d, sl, rm) for (c, d, sl, rm) in before_placements
                if d in on_grid_days and sl in on_grid_slots
            ]
            before_detailed = tt_scorer.score_detailed(before_placements)

            # ── Multi-start optimization ──
            global_best_solution = None
            global_best_quality = float("inf")
            global_best_ordered = None
            global_best_generator = None
            global_start = time.time()
            n_runs = max(1, self.multi_start_runs)

            # `run`, `greedy_stats` and `lns_stats` are bound inside the loop but
            # read by the summary below; initialise them so a capped run still
            # produces a summary instead of an UnboundLocalError.
            run = 0
            greedy_stats = {}
            lns_stats = {}

            for run in range(n_runs):
                # Emergency wall-clock cap. `run > 0` guarantees at least one
                # complete run: breaking on run 0 leaves global_best_ordered None
                # and optimize() then dies with `TypeError: 'NoneType' object is
                # not iterable` while building the heuristic result.
                self._check_cancelled()
                elapsed_total = time.time() - global_start
                if run > 0 and elapsed_total >= self.multi_start_time_limit:
                    self._clock_capped = True
                    break

                # Build fresh validator for each run
                exclude_ids = {cls_key(c) for c in all_flexible}
                validator = ConstraintValidator(self.state,
                                               exclude_ids=exclude_ids)
                for cls in locked:
                    validator.add_placement(
                        cls, cls["placed_day"], cls["placed_time"],
                        cls["placed_classroom"])
                for cls in protected:
                    validator.add_placement(
                        cls, cls["placed_day"], cls["placed_time"],
                        cls["placed_classroom"])

                generator = CandidateGenerator(self.state, validator=validator)

                # Build constraint propagator for incremental valid-count caching
                cs = ConstraintState(self.state, validator, generator, all_flexible)
                propagator = ConstraintPropagator(cs)

                scorer = PlacementScorer(self.state, validator,
                                         weights=self.weights,
                                         conflict_graph=conflict_graph,
                                         propagator=propagator,
                                         parallel_pool=parallel_pool,
                                         previous_placements=old_placements)

                # Graph-enhanced ordering: first run uses conflict-graph
                # difficulty, subsequent runs shuffle with controlled
                # randomization
                analyzer = ConflictAnalyzer(conflict_graph, validator)
                ordered = analyzer.difficulty_ranking(all_flexible)
                if run > 0:
                    ordered = self._perturb_ordering(ordered)

                # Phase 1: Greedy construction
                # First run uses warm-start from existing placements
                ws = old_placements if run == 0 else None
                solution, greedy_stats = self._greedy_construct(
                    ordered, validator, generator, scorer,
                    propagator=propagator,
                    improve_only_scores=improve_only_scores,
                    warm_start=ws)

                # Remaining time budget for this run's LNS. Under a
                # deterministic budget there is none: LNS stops on iteration count,
                # not on how fast this particular machine happens to be.
                if self.deterministic_budget:
                    per_run_limit = None
                else:
                    elapsed_total = time.time() - global_start
                    remaining_time = self.multi_start_time_limit - elapsed_total
                    per_run_limit = min(
                        self.lns_time_limit,
                        remaining_time / max(1, n_runs - run))

                # Phase 2: LNS improvement with adaptive strategies
                # Reset propagator caches before LNS (greedy changed state)
                if propagator is not None:
                    propagator.cs.reset()
                solution, lns_stats = self._lns_improve(
                    ordered, solution, validator, generator, scorer,
                    tt_scorer, time_limit_override=per_run_limit,
                    run_number=run, total_runs=n_runs,
                    conflict_graph=conflict_graph, analyzer=analyzer,
                    propagator=propagator, same_day_map=same_day_map,
                    improve_only_scores=improve_only_scores)

                # Evaluate this run's quality
                run_placements = []
                for cls in pinned:
                    run_placements.append((cls, cls["pinned_day"],
                                           cls["pinned_time"],
                                           cls["pinned_classroom"]))
                for cls in locked:
                    run_placements.append((cls, cls["placed_day"],
                                           cls["placed_time"],
                                           cls["placed_classroom"]))
                for cls in protected:
                    run_placements.append((cls, cls["placed_day"],
                                           cls["placed_time"],
                                           cls["placed_classroom"]))
                for i, cls in enumerate(ordered):
                    if solution[i] is not None:
                        run_placements.append(
                            (cls, solution[i][0], solution[i][1], solution[i][2]))

                run_quality = tt_scorer.score(run_placements)
                run_placed_count = sum(1 for s in solution if s is not None)

                # Keep best: prefer more classes placed, then lower quality score
                if global_best_solution is None:
                    is_better = True
                else:
                    best_placed_count = sum(
                        1 for s in global_best_solution if s is not None)
                    is_better = (run_placed_count > best_placed_count or
                                 (run_placed_count == best_placed_count
                                  and run_quality < global_best_quality))
                if is_better:
                    global_best_solution = list(solution)
                    global_best_quality = run_quality
                    global_best_ordered = ordered
                    global_best_generator = generator

            # Build heuristic result for potential CP-SAT seeding
            ordered = global_best_ordered
            solution = global_best_solution
            generator = global_best_generator

            heuristic_placed = []
            for cls in pinned:
                heuristic_placed.append((cls, cls["pinned_day"],
                                         cls["pinned_time"],
                                         cls["pinned_classroom"]))
            for cls in locked:
                heuristic_placed.append((cls, cls["placed_day"],
                                         cls["placed_time"],
                                         cls["placed_classroom"]))
            for cls in protected:
                heuristic_placed.append((cls, cls["placed_day"],
                                         cls["placed_time"],
                                         cls["placed_classroom"]))
            for i, cls in enumerate(ordered):
                if solution[i] is not None:
                    heuristic_placed.append(
                        (cls, solution[i][0], solution[i][1], solution[i][2]))

            heuristic_quality = tt_scorer.score(heuristic_placed)

            # ── Phase 4 (optional): CP-SAT deep optimization ──
            cpsat_used = False
            cpsat_status = None
            cpsat_status_label = None
            if self.use_cpsat:
                # Shut down parallel pool before CP-SAT to free resources
                if parallel_pool is not None:
                    parallel_pool.shutdown()
                    parallel_pool = None

                cpsat_result = self._cpsat_optimize(
                    heuristic_placed, tt_scorer, pinned, protected, all_flexible)
                if cpsat_result is not None:
                    cpsat_placed, cpsat_unplaced, cpsat_info = cpsat_result
                    cpsat_quality = tt_scorer.score(cpsat_placed)
                    cpsat_placed_count = len(cpsat_placed)
                    heur_placed_count = len(heuristic_placed)
                    cpsat_status = cpsat_info.get("status")
                    cpsat_status_label = cpsat_info.get("status_label", cpsat_status)
                    cpsat_used = True

                    # Accept CP-SAT result if it's better
                    if (cpsat_placed_count > heur_placed_count or
                            (cpsat_placed_count == heur_placed_count
                             and cpsat_quality < heuristic_quality)):
                        heuristic_placed = cpsat_placed
                        heuristic_quality = cpsat_quality
                        # Rebuild solution/ordered from cpsat result
                        # for change tracking below

            # Build final results with change tracking
            placed_list = []
            unplaced_list = []
            changes = []

            # Use heuristic_placed as the final result
            placed_set = set()
            for cls, day, slot, room in heuristic_placed:
                placed_list.append((cls, day, slot, room))
                placed_set.add(cls_key(cls))
                if cls["pinned"] or cls_key(cls) in immovable_ids or cls_key(cls) in effective_protected_ids:
                    continue
                old = old_placements.get(cls_key(cls))
                if old and old != (day, slot, room):
                    changes.append({
                        "cls": cls,
                        "old_day": old[0], "old_time": old[1],
                        "old_room": old[2],
                        "new_day": day, "new_time": slot, "new_room": room,
                    })
                elif not old:
                    changes.append({
                        "cls": cls,
                        "old_day": None, "old_time": None, "old_room": None,
                        "new_day": day, "new_time": slot, "new_room": room,
                    })

            for cls in all_flexible:
                if cls_key(cls) not in placed_set:
                    reason = (generator.unplaced_reason(cls)
                              if generator else tr("negotiation.no_valid_placement"))
                    unplaced_list.append((cls, reason))

            # Build after-optimization quality breakdown
            after_detailed = tt_scorer.score_detailed(placed_list)

            summary = {
                "runs_completed": min(n_runs, run + 1)
                                  if global_best_solution else 0,
                "total_time": time.time() - global_start,
                "before": before_detailed,
                "after": after_detailed,
                "improvement": {
                    k: before_detailed.get(k, 0) - after_detailed.get(k, 0)
                    for k in ["lecturer_gaps", "student_gaps", "fragmentation",
                               "day_balance", "room_switching", "time_quality",
                               "total"]
                },
                "classes_moved": len(changes),
                "classes_placed": len(placed_list),
                "classes_unplaced": len(unplaced_list),
                "cpsat_used": cpsat_used,
                "cpsat_status": cpsat_status,
                "cpsat_status_label": cpsat_status_label,
                "greedy_stats": greedy_stats,
                "lns_strategy_stats": lns_stats.get("strategy_stats", []),
                # ST-SCHED-013. `seed` is what you pass back to reproduce this exact
                # timetable. `deterministic` is False when the answer cannot be
                # reproduced: either the emergency clock cap truncated the search,
                # or CP-SAT ran (its budget is still wall-clock — Phase 3).
                "seed": self.seed,
                "deterministic": (not self._clock_capped) and (not cpsat_used),
            }

            return placed_list, unplaced_list, changes, summary
        finally:
            if parallel_pool is not None:
                parallel_pool.shutdown()

    def _perturb_ordering(self, ordered):
        """Create a perturbed class ordering for multi-start diversity.

        Maintains the general difficulty-first principle but introduces
        controlled randomness by swapping adjacent classes and shuffling
        within difficulty tiers.
        """
        perturbed = list(ordered)
        n = len(perturbed)
        if n <= 2:
            return perturbed

        # Shuffle within blocks of 3-4 adjacent classes
        block_size = min(4, max(2, n // 3))
        for start in range(0, n, block_size):
            end = min(start + block_size, n)
            block = perturbed[start:end]
            self._rng.shuffle(block)
            perturbed[start:end] = block

        return perturbed

    def _greedy_construct(self, flexible, validator, generator, scorer,
                          propagator=None, improve_only_scores=None,
                          warm_start=None):
        """Greedy phase with look-ahead scoring and difficulty ordering.

        Places each class in its best-scored valid slot, using look-ahead
        to avoid creating future bottlenecks. Falls back to backtracking
        if a class can't be placed.

        If warm_start is provided (dict cls_key -> (day, slot, room)),
        valid previous placements are pre-populated before the greedy
        loop, reducing churn.
        """
        improve_only_scores = improve_only_scores or {}
        warm_start = warm_start or {}
        n = len(flexible)
        solution = [None] * n
        best_solution = [None] * n
        best_count = [0]
        iterations = [0]

        # Use propagator for add/remove if available (keeps caches in sync)
        def _add(cls, day, slot, room):
            if propagator is not None:
                propagator.add_placement(cls, day, slot, room)
            else:
                validator.add_placement(cls, day, slot, room)

        def _remove(cls, day, slot, room):
            if propagator is not None:
                propagator.remove_placement(cls, day, slot, room)
            else:
                validator.remove_placement(cls, day, slot, room)

        # Compute adaptive lookahead depth based on constraint tightness
        if n > 0:
            avg_tightness = sum(
                validator.constraint_tightness(c) for c in flexible
            ) / n
            if avg_tightness < 50:
                lookahead_depth = 10
            elif avg_tightness < 200:
                lookahead_depth = 7
            else:
                lookahead_depth = 4
        else:
            lookahead_depth = 5

        # Pre-populate from warm-start (valid previous placements)
        valid_days = set(self.state.get("days", []))
        valid_slots = set(self.state.get("slots", []))
        warm_placed = set()
        if warm_start:
            for i, cls in enumerate(flexible):
                ws = warm_start.get(cls_key(cls))
                if (ws and ws[0] in valid_days and ws[1] in valid_slots
                        and validator.check_placement(cls, ws[0], ws[1], ws[2])):
                    solution[i] = ws
                    _add(cls, ws[0], ws[1], ws[2])
                    warm_placed.add(i)

        _cancel_token = self.cancel_token

        def solve(idx):
            if _cancel_token is not None:
                _cancel_token.raise_if_cancelled()
            if iterations[0] >= self.max_iterations:
                return False
            iterations[0] += 1

            if idx == n:
                placed_count = sum(1 for a in solution if a is not None)
                if placed_count > best_count[0]:
                    best_count[0] = placed_count
                    best_solution[:] = list(solution)
                return placed_count == n

            # Skip warm-started classes (already placed)
            if idx in warm_placed:
                return solve(idx + 1)

            cls = flexible[idx]
            candidates = generator.generate(cls)

            # Score with adaptive look-ahead for the next few classes
            remaining = [flexible[j] for j in range(idx + 1, min(idx + 1 + lookahead_depth, n))]
            if remaining and len(candidates) > 1:
                scored = scorer.score_candidates_with_lookahead(
                    cls, candidates, remaining, generator)
            else:
                scored = scorer.score_candidates(cls, candidates)

            # Enforce improve_only: skip candidates worse than baseline
            baseline = improve_only_scores.get(cls_key(cls))
            if baseline is not None:
                scored = [(d, s, r, sc) for d, s, r, sc in scored
                          if sc <= baseline]

            for day, slot, room, _ in scored:
                solution[idx] = (day, slot, room)
                _add(cls, day, slot, room)

                if solve(idx + 1):
                    return True

                _remove(cls, day, slot, room)
                solution[idx] = None

            # Try skipping this class
            if solve(idx + 1):
                return True

            return False

        solve(0)
        greedy_stats = {
            "iterations_used": iterations[0],
            "max_iterations": self.max_iterations,
            "budget_exhausted": iterations[0] >= self.max_iterations,
            "warm_started": len(warm_placed),
        }
        return best_solution, greedy_stats

    def _lns_improve(self, flexible, solution, validator, generator,
                     scorer, tt_scorer, time_limit_override=None,
                     run_number=0, total_runs=1,
                     conflict_graph=None, analyzer=None,
                     propagator=None, same_day_map=None,
                     improve_only_scores=None):
        """LNS improvement phase with adaptive strategy selection.

        Uses AdaptiveStrategySelector to learn which destroy strategies
        produce better improvements and adjust selection probabilities.
        """
        placed_indices = [i for i, s in enumerate(solution) if s is not None]
        if len(placed_indices) < 3:
            return solution, {"strategy_stats": []}

        from scheduler_app.models import PROTECTION_LOCKED
        pinned_ids = {cls_key(c) for c in self.state["classes"] if c["pinned"]}
        locked_ids = {cls_key(c) for c in self.state["classes"]
                      if not c["pinned"]
                      and c.get("protection") == PROTECTION_LOCKED
                      and c["placed"]}
        immovable_ids = pinned_ids | locked_ids

        def build_placements(sol):
            result = []
            for i in range(len(flexible)):
                if sol[i] is not None:
                    result.append(
                        (flexible[i], sol[i][0], sol[i][1], sol[i][2]))
            return result

        best_solution = list(solution)
        best_quality = tt_scorer.score(build_placements(solution))
        current_solution = list(solution)
        current_quality = best_quality

        # Calculate destroy size
        effective_protected = set(self.protected_ids)
        eligible_count = sum(
            1 for i, cls in enumerate(flexible)
            if solution[i] is not None
            and cls_key(cls) not in immovable_ids
            and cls_key(cls) not in effective_protected)
        destroy_size = max(2, int(eligible_count * self.destroy_fraction))

        # Adaptive strategy selector with conflict graph support
        # The selector keeps ONE long-lived stream and hands it to each
        # strategy it builds. Building a fresh Random(seed) per strategy would
        # replay the identical shuffle every iteration and silently kill LNS
        # exploration while still looking deterministic.
        adaptive = AdaptiveStrategySelector(
            self.state, weights=self.weights,
            conflict_graph=conflict_graph, analyzer=analyzer,
            rng=self._rng)

        no_improve_count = 0
        start_time = time.time()
        # `time_limit_override or ...` means a None override falls back to
        # lns_time_limit, so the deterministic path has to be selected
        # explicitly rather than by passing None through.
        deterministic_loop = (self.deterministic_budget
                              and time_limit_override is None)
        time_limit = time_limit_override or self.lns_time_limit

        for iteration in range(self.lns_iterations):
            self._check_cancelled()
            elapsed = time.time() - start_time
            if deterministic_loop:
                # Iteration-bounded: only the emergency cap can cut this short,
                # and doing so costs reproducibility (reported in the summary).
                if elapsed >= self.multi_start_time_limit:
                    self._clock_capped = True
                    break
            elif elapsed >= time_limit:
                break
            if no_improve_count >= self.lns_no_improve_limit:
                break

            # Progress callback with run information
            if self.progress_callback and iteration % 10 == 0:
                self.progress_callback(
                    iteration, best_quality, current_quality,
                    run_number, total_runs)

            # ── Destroy phase (adaptive selection) ──
            strategy, strategy_idx = adaptive.select(iteration)
            removed_indices = strategy.select(
                current_solution, flexible, destroy_size,
                pinned_ids=immovable_ids, protected_ids=effective_protected)

            if not removed_indices:
                adaptive.update(strategy_idx, False)
                no_improve_count += 1
                continue

            # Save removed placements and clear from occupancy
            saved = {}
            for idx in removed_indices:
                if current_solution[idx] is not None:
                    saved[idx] = current_solution[idx]
                    d, s, r = current_solution[idx]
                    if propagator is not None:
                        propagator.remove_placement(flexible[idx], d, s, r)
                    else:
                        validator.remove_placement(flexible[idx], d, s, r)
                    current_solution[idx] = None

            # ── Repair phase ──
            repair = RepairStrategy(
                self.state, validator, generator, scorer,
                weights=self.weights,
                same_day_map=same_day_map or {},
                improve_only_scores=improve_only_scores or {},
                propagator=propagator)
            repair.repair(removed_indices, current_solution, flexible)

            # Evaluate new quality
            new_quality = tt_scorer.score(build_placements(current_solution))
            new_placed = sum(1 for s in current_solution if s is not None)
            best_placed = sum(1 for s in best_solution if s is not None)
            current_placed = sum(1 for s in current_solution if s is not None)

            # Simulated annealing acceptance
            improved = (new_quality < best_quality
                        or new_placed > best_placed)
            delta = new_quality - current_quality
            temp = self.sa_initial_temp * (self.sa_cooling_rate ** iteration)
            if improved:
                accept = True
            elif new_placed >= current_placed and temp > 0.01 and delta > 0:
                # Probabilistic acceptance of slightly worse solutions
                accept = self._rng.random() < math.exp(-delta / temp)
            else:
                accept = False

            adaptive.update(strategy_idx, improved)

            if improved:
                best_quality = new_quality
                best_solution = list(current_solution)
            if accept:
                current_quality = new_quality
                if improved:
                    no_improve_count = 0
                else:
                    no_improve_count += 1
            else:
                # Revert: restore removed placements
                for idx in removed_indices:
                    if current_solution[idx] is not None:
                        d, s, r = current_solution[idx]
                        if propagator is not None:
                            propagator.remove_placement(flexible[idx], d, s, r)
                        else:
                            validator.remove_placement(flexible[idx], d, s, r)
                for idx, placement in saved.items():
                    current_solution[idx] = placement
                    if placement is not None:
                        d, s, r = placement
                        if propagator is not None:
                            propagator.add_placement(flexible[idx], d, s, r)
                        else:
                            validator.add_placement(flexible[idx], d, s, r)
                current_quality = tt_scorer.score(
                    build_placements(current_solution))
                no_improve_count += 1

        # Final progress callback
        if self.progress_callback:
            self.progress_callback(
                self.lns_iterations, best_quality, best_quality,
                run_number, total_runs)

        # Ensure validator reflects best solution
        for i in range(len(flexible)):
            if current_solution[i] != best_solution[i]:
                if current_solution[i] is not None:
                    d, s, r = current_solution[i]
                    if propagator is not None:
                        propagator.remove_placement(flexible[i], d, s, r)
                    else:
                        validator.remove_placement(flexible[i], d, s, r)
                if best_solution[i] is not None:
                    d, s, r = best_solution[i]
                    if propagator is not None:
                        propagator.add_placement(flexible[i], d, s, r)
                    else:
                        validator.add_placement(flexible[i], d, s, r)

        lns_stats = {
            "strategy_stats": adaptive.get_stats(),
        }
        return best_solution, lns_stats

    def _cpsat_optimize(self, heuristic_placed, tt_scorer,
                        pinned, protected, all_flexible):
        """Run CP-SAT solver in a subprocess, seeded with the heuristic.

        Running in a separate process isolates the native OR-Tools C++
        code from the Qt main process. If the solver crashes at the
        native level, only the subprocess dies — the main app survives.

        Returns (placed_list, unplaced_list, solve_info) or None on failure.
        """
        try:
            from scheduler_app.cpsat_scheduler import HAS_ORTOOLS
        except ImportError:
            return None
        if not HAS_ORTOOLS:
            return None

        classes = self.state["classes"]
        cls_to_idx = {cls_key(c): i for i, c in enumerate(classes)}

        # Convert heuristic_placed to index-based for serialization
        heuristic_indices = []
        for cls, day, slot, room in heuristic_placed:
            idx = cls_to_idx.get(cls_key(cls))
            if idx is not None:
                heuristic_indices.append((idx, day, slot, room))

        # Convert protected_ids to class indices
        protected_indices = set()
        for pid in self.protected_ids:
            for i, c in enumerate(classes):
                if cls_key(c) == pid:
                    protected_indices.add(i)
                    break

        state_snap = _make_cpsat_state_snapshot(self.state)
        result_queue = multiprocessing.Queue()

        proc = multiprocessing.Process(
            target=_cpsat_subprocess_worker,
            args=(state_snap, dict(self.weights or {}),
                  self.cpsat_time_limit, protected_indices,
                  heuristic_indices, result_queue, self.seed))
        proc.start()

        # Poll until the subprocess finishes, calling progress callback
        # so the Qt event loop can repaint the status bar.
        timeout = self.cpsat_time_limit + 30  # generous grace period
        deadline = time.time() + timeout
        while proc.is_alive() and time.time() < deadline:
            proc.join(timeout=0.25)
            if self.progress_callback:
                self.progress_callback(0, 0, 0, -1, 0)

        if proc.is_alive():
            proc.kill()
            proc.join(timeout=5)
            return None

        if proc.exitcode != 0:
            # Subprocess crashed (native segfault, abort, etc.)
            return None

        if result_queue.empty():
            return None

        try:
            result = result_queue.get_nowait()
        except Exception:
            return None

        if result is None or result.get("status") != "ok":
            return None

        # Map index-based results back to original class dicts
        placed_list = []
        for cls_idx, day, slot, room in result["placed"]:
            if 0 <= cls_idx < len(classes):
                placed_list.append((classes[cls_idx], day, slot, room))

        unplaced_list = []
        for cls_idx, reason in result["unplaced"]:
            if 0 <= cls_idx < len(classes):
                unplaced_list.append((classes[cls_idx], reason))

        return placed_list, unplaced_list, result.get("info", {})

    def quick_place(self, cls):
        """Find the best placement for a single class without moving others.

        Uses look-ahead scoring against remaining unplaced classes.
        Returns (day, slot, room) or None if no valid placement exists.
        """
        exclude = {cls_key(cls)}
        validator = ConstraintValidator(self.state, exclude_ids=exclude)
        generator = CandidateGenerator(self.state, validator=validator)
        scorer = PlacementScorer(self.state, validator, weights=self.weights)

        candidates = generator.generate(cls)
        if not candidates:
            return None

        # Find other unplaced classes for look-ahead
        unplaced = [c for c in self.state["classes"]
                    if not c["placed"] and not c["pinned"]
                    and cls_key(c) != cls_key(cls)]

        if unplaced and len(candidates) > 1:
            scored = scorer.score_candidates_with_lookahead(
                cls, candidates, unplaced, generator)
        else:
            scored = scorer.score_candidates(cls, candidates)

        return (scored[0][0], scored[0][1], scored[0][2])

    def place_with_reschedule(self, new_cls):
        """Place a single class, rescheduling others if necessary.

        Phase 1: Try without moving existing classes (with look-ahead).
        Phase 2: If Phase 1 fails, reschedule all flexible classes.

        Returns:
            (success, placements_dict, rescheduled) where placements_dict
            maps cls_key(cls) -> (day, slot, room) for all classes that changed.
        """
        new_id = cls_key(new_cls)

        # Phase 1: find slot without moving others
        best = self.quick_place(new_cls)
        if best:
            return True, {new_id: best}, False

        # Phase 2: full reschedule including new class
        from scheduler_app.models import is_immovable
        existing_flexible = [
            c for c in self.state["classes"]
            if c["placed"] and not c["pinned"] and not is_immovable(c)
            and cls_key(c) != new_id]

        exclude_ids = {new_id} | {cls_key(c) for c in existing_flexible}
        validator = ConstraintValidator(self.state, exclude_ids=exclude_ids)
        generator = CandidateGenerator(self.state, validator=validator)
        scorer = PlacementScorer(self.state, validator, weights=self.weights)
        tt_scorer = TimetableScorer(self.state, weights=self.weights)

        # Compute baseline scores for improve_only classes
        from scheduler_app.models import PROTECTION_IMPROVE_ONLY
        improve_only_scores = {}
        for c in existing_flexible:
            if (c.get("protection") == PROTECTION_IMPROVE_ONLY
                    and c["placed"]):
                improve_only_scores[cls_key(c)] = tt_scorer.placement_score(
                    c, c["placed_day"], c["placed_time"],
                    c["placed_classroom"])

        if new_cls["pinned"]:
            day = new_cls["pinned_day"]
            slot = new_cls["pinned_time"]
            room = new_cls["pinned_classroom"]
            if not validator.check_placement(new_cls, day, slot, room):
                return False, {}, False
            validator.add_placement(new_cls, day, slot, room)

        combined = existing_flexible + ([new_cls] if not new_cls["pinned"]
                                        else [])
        # Difficulty-aware ordering
        combined = validator.sort_by_difficulty(combined)

        preferred = {cls_key(c): (c["placed_day"], c["placed_time"],
                                 c["placed_classroom"]) for c in existing_flexible}

        solution, _ = self._greedy_construct(
            combined, validator, generator, scorer,
            improve_only_scores=improve_only_scores)

        # Find new class result
        if not new_cls["pinned"]:
            new_idx = combined.index(new_cls)
            if solution[new_idx] is None:
                return False, {}, False

        placements = {}
        if new_cls["pinned"]:
            placements[new_id] = (new_cls["pinned_day"],
                                  new_cls["pinned_time"],
                                  new_cls["pinned_classroom"])
        for i, cls in enumerate(combined):
            if solution[i] is not None:
                d, s, r = solution[i]
                if cls_key(cls) == new_id:
                    placements[new_id] = (d, s, r)
                else:
                    old = preferred.get(cls_key(cls))
                    if old != (d, s, r):
                        placements[cls_key(cls)] = (d, s, r)

        return True, placements, len(placements) > 1
