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

from scheduler_app.models import cls_key, DEFAULT_OPTIMIZER_SEED
# Used by the unplaced-reason fallback below. Was missing, so the
# `generator is None` branch raised NameError instead of reporting.
from scheduler_app.translations import tr
from scheduler_app.core.infeasibility import diagnose_infeasibility

# The shipped search budget. Defined in `core/constants.py` and re-exported
# here so that `schedule_optimizer.DEFAULT_MULTI_START_RUNS` keeps resolving:
# `solver_worker` needs the same two numbers for the progress bar's
# denominators (ST-PERF-001) and cannot import this module at module scope,
# because this module imports it. That was `core`'s last mutually importing
# pair (ST-ARCH-010).
from scheduler_app.core.constants import (  # noqa: F401
    DEFAULT_MULTI_START_RUNS, DEFAULT_LNS_ITERATIONS,
)
from scheduler_app.constraint_validator import (
    ConstraintValidator, screen_placements,
)
from scheduler_app.candidate_generator import CandidateGenerator
from scheduler_app.placement_scorer import PlacementScorer
from scheduler_app.timetable_scorer import TimetableScorer
from scheduler_app.lns_strategies import (
    RepairStrategy, AdaptiveStrategySelector,
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
                             result_queue, seed=DEFAULT_OPTIMIZER_SEED,
                             language=None):
    """Run CP-SAT solver in an isolated subprocess.

    All arguments are plain serializable Python objects. Results are
    placed in *result_queue* as a dict.  If the solver crashes at the
    native level the subprocess dies without affecting the main process.

    *language* is the parent's UI language, and it has to be passed explicitly.
    ``translations._current_lang`` is a module global, and Windows
    multiprocessing uses **spawn** -- the child re-imports the module from
    scratch, so the global returns to its default. Measured: a parent running
    Turkish got 'Optimum' while the child produced 'Optimal', and those strings
    are not diagnostics. They are the unplaced reasons the user reads in the
    results dialog, so a Turkish school running Thorough mode got a list of
    English sentences with no way to tell why.
    """
    try:
        if language:
            from scheduler_app.translations import set_language
            set_language(language)
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
        # improve_only baselines: a class may only move to a placement that
        # scores at least as well. Rebound per run once `scorer` exists (see
        # below); initialised here so a run that never executes still leaves a
        # defined name.
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

            # improve_only baselines are computed per run, below, against that
            # run's PlacementScorer — see the comment there for why.
            improve_only_classes = [
                c for c in flexible
                if c.get("protection") == PROTECTION_IMPROVE_ONLY and c["placed"]
            ]

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

                # Build fresh validator for each run.
                # ST-SCHED-010: `locked` and `protected` classes are `placed`,
                # so build_occupancy() already counts them. They must be
                # excluded there or the explicit add loop below claims their
                # cells a second time, and with ref-counted occupancy a cell
                # claimed twice needs two releases to free — which nothing does.
                exclude_ids = ({cls_key(c) for c in all_flexible}
                               | {cls_key(c) for c in locked}
                               | {cls_key(c) for c in protected})
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

                # ── improve_only baselines, in the right currency ───────────
                #
                # ST-SCHED-006. "Move this class only somewhere at least as
                # good" is enforced in `_greedy_construct` and in
                # `RepairStrategy` as `candidate_score <= baseline`, where the
                # candidate scores come from PlacementScorer. The baseline used
                # to come from TimetableScorer.placement_score — a different
                # function on a different scale. Measured over ten placed
                # classes on the `normal` preset: TimetableScorer spans
                # -0.20..0.60 while PlacementScorer spans -3.67..8.34, and the
                # mismatched comparison kept 15 of 71 candidates against 22 for
                # a same-currency one.
                #
                # The damaging half is not the count. For four of those ten
                # classes the old gate kept ZERO candidates — including the
                # class's own current placement, which by definition is not
                # worse than itself. An improve_only class could therefore be
                # forced UNPLACED by the very protection meant to keep it safe.
                # Scoring both sides with `scorer` makes "stay where you are"
                # always admissible.
                #
                # Per run, not once up front: `scorer` is bound to this run's
                # validator, whose occupancy already excludes every flexible
                # class, so the baseline measures the placement against the
                # fixed part of the timetable — the same footing the candidates
                # are scored on.
                improve_only_scores = {
                    cls_key(c): scorer.score(c, c["placed_day"],
                                             c["placed_time"],
                                             c["placed_classroom"])
                    for c in improve_only_classes
                }

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
                    warm_start=ws, same_day_map=same_day_map,
                    deadline=global_start + self.multi_start_time_limit)

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
                    improve_only_scores=improve_only_scores,
                    deadline=global_start + self.multi_start_time_limit)

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
            self._cpsat_failure = None
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

            # ── ST-SCHED-001: assert and repair before proposing ───────────
            #
            # A proposal that breaks a hard constraint is a solver bug, not a
            # thing to hand to the commit step and let it quietly prune. This
            # screens the whole proposal through the same rule
            # apply_reschedule uses (ST-ARCH-004), so anything that gets past
            # here is committable as-is.
            #
            # It is a safety net, not the fix — with the greedy occupancy
            # resync above, `repaired_conflicts` should be 0 on any instance
            # whose own pins are satisfiable. A non-zero count in the summary
            # means the engine produced something it should not have, and is
            # worth reporting rather than hiding.
            screened, screen_conflicts = screen_placements(
                self.state, heuristic_placed, immovable_ids=immovable_ids)
            screened_keys = {cls_key(c) for c, _, _, _ in screened}
            # Pinned/locked clashes are reported but still committed — the pin
            # is the user's instruction (ST-SCHED-002). Only genuinely dropped
            # placements carry a repair reason.
            repair_reasons = {
                cls_key(c): reasons
                for c, _d, _s, _r, reasons in screen_conflicts
                if cls_key(c) not in screened_keys
            }
            infeasible_fixed = [
                c.get("name", "?")
                for c, _d, _s, _r, _reasons in screen_conflicts
                if cls_key(c) in screened_keys
            ]
            heuristic_placed = screened

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
                    # A class the repair pass had to drop knows exactly why it
                    # was dropped; do not overwrite that with the generator's
                    # generic "all slots occupied" guess.
                    repaired = repair_reasons.get(cls_key(cls))
                    if repaired:
                        reason = "; ".join(repaired)
                    else:
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
                # Why deep mode did not contribute, when it was asked for and
                # did not run. None when it ran, or was never requested.
                "cpsat_failure": (getattr(self, "_cpsat_failure", None)
                                  if self.use_cpsat and not cpsat_used
                                  else None),
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
                # ST-SCHED-001. How many of its own placements the optimizer
                # had to withdraw because they broke a hard constraint. Any
                # value above zero is an engine defect, not a property of the
                # instance, and the caller should say so rather than let the
                # classes quietly disappear.
                "repaired_conflicts": len(repair_reasons),
                "repaired_classes": sorted(
                    c.get("name", "?") for c, _d, _s, _r, _rs in screen_conflicts
                    if cls_key(c) not in screened_keys),
                # ST-SCHED-002. Pinned or locked classes whose fixed position
                # clashes with another fixed position. These are committed
                # anyway — the pin is what the user asked for — so the only
                # correct response is to name them.
                "infeasible_fixed": sorted(infeasible_fixed),
                # ST-SCHED-014. The global constraint that makes this instance
                # impossible, or None. Always present so callers can read it
                # without guessing. This is arithmetic, not search: it names
                # what no amount of rearranging could fix, which is the one
                # thing a list of unplaced classes can never say.
                "infeasibility": diagnose_infeasibility(self.state),
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
                          warm_start=None, deadline=None, same_day_map=None):
        """Greedy phase with look-ahead scoring and difficulty ordering.

        Places each class in its best-scored valid slot, using look-ahead
        to avoid creating future bottlenecks. Falls back to backtracking
        if a class can't be placed.

        If warm_start is provided (dict cls_key -> (day, slot, room)),
        valid previous placements are pre-populated before the greedy
        loop, reducing churn.

        ``deadline`` is an absolute ``time.time()`` value at which the search
        gives up and returns the best solution found so far (ST-PERF-008).
        It is an *emergency* cap, not a routine bound: reaching it means the
        answer depended on how fast this machine is, so it sets
        ``_clock_capped`` and the run stops claiming reproducibility.

        Returns (best_solution, greedy_stats). On return the validator's
        occupancy maps describe exactly ``best_solution`` — see the resync at
        the end of this method (ST-SCHED-001).
        """
        improve_only_scores = improve_only_scores or {}
        same_day_map = same_day_map or {}
        warm_start = warm_start or {}
        n = len(flexible)
        solution = [None] * n
        best_solution = [None] * n
        best_count = [0]
        iterations = [0]
        # ST-PERF-004: leaves visited since the incumbent last improved.
        # See the stopping condition in enter() below.
        stale_leaves = [0]
        no_improve_limit = max(500, 4 * n)
        # Set when the search stops for a reason other than exhausting the
        # tree. The driver then unwinds directly instead of continuing to try
        # options it has already decided not to explore — see `_stop()`.
        aborted = [False]

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
        # Seed the incumbent with the warm start. Without this, a run whose
        # iteration budget expires before the search reaches its first leaf
        # returns the empty `best_solution` and silently throws away placements
        # the user already had — and, since the resync below trusts
        # `best_solution`, would then unplace them in the occupancy maps too.
        if warm_placed:
            best_solution[:] = list(solution)
            best_count[0] = len(warm_placed)

        _cancel_token = self.cancel_token

        # ── ST-SCHED-012: an explicit stack, not Python recursion ──────────
        #
        # `solve(idx)` used to recurse once per class (plus a second, tail
        # recursion for the "skip this class" branch), so the interpreter stack
        # grew to one frame per flexible class. At ~1000 classes that hits
        # CPython's recursion limit; the 1200-class `pathological` preset died
        # outright. The loop below is an exact translation of that recursion —
        # same visit order, same iteration accounting, same return values — so
        # the answer for any instance that used to fit on the stack is
        # unchanged, and depth is now bounded by the heap instead.
        #
        # A frame is {idx, scored, k, applied}:
        #   k        - index of the next option to try. k == len(scored) is the
        #              "leave this class unplaced" branch; k > len(scored) means
        #              the frame is exhausted.
        #   applied  - the placement this frame currently has in the occupancy
        #              maps, or None. Exactly what has to be undone when a
        #              child fails.
        stack = []

        def _record_incumbent():
            """Offer the current `solution` to the incumbent.

            Normally only leaves do this. A stop can fire mid-descent, and at
            that moment `solution` is a complete, internally consistent partial
            answer — every `_add` so far is matched by an entry in it. Without
            this, a stop before the first leaf leaves `best_solution` all-None
            and the resync below dutifully strips every placement the search had
            already made: the run returns nothing rather than what it had.
            """
            placed_count = sum(1 for a in solution if a is not None)
            if placed_count > best_count[0]:
                best_count[0] = placed_count
                best_solution[:] = list(solution)

        def _stop():
            """Abandon the search, keeping the best answer found so far."""
            _record_incumbent()
            aborted[0] = True
            return ('ret', False)

        def enter(idx):
            """Emulate entering ``solve(idx)``.

            Returns ``('ret', bool)`` when the call completes without branching,
            or ``('push', None)`` when it pushed a frame to branch on.
            """
            while True:
                if _cancel_token is not None:
                    _cancel_token.raise_if_cancelled()
                if iterations[0] >= self.max_iterations:
                    return _stop()
                # ── ST-PERF-004: stop when the search stops finding anything ──
                #
                # The DFS tries every candidate for a class and then tries
                # skipping it, so the tree is exponential and has no natural
                # end: it ran until `max_iterations` ran out, every time, at
                # every scale. `budget_exhausted` was True on 100 % of measured
                # runs from 25 classes upward — the optimizer's own admission
                # that it stopped because the clock ran out rather than because
                # it was done.
                #
                # And it bought nothing. Placements are IDENTICAL at every
                # budget from 100 to 100 000 iterations: `small` 21, `normal`
                # 76, `large` 231 (from 500 up). What the budget did buy was
                # wall clock — the full shipped pipeline on `normal` measured
                # 257 s at 100 000 against 175 s at 2 000, and `small` 43.8 s
                # against 10.3 s, for the same answer.
                #
                # The incumbent only ever changes at a leaf, so "leaves since
                # the last improvement" is the honest measure of progress.
                # Scaled by n because a bigger instance needs more leaves before
                # the same conclusion is safe.
                if stale_leaves[0] >= no_improve_limit:
                    return _stop()
                # ST-PERF-008: the greedy phase had no wall-clock bound at all.
                # `multi_start_time_limit` was only consulted between runs and
                # inside LNS, so a single construction could overrun the whole
                # budget on its own — measured at 125-291 s against a 5 s budget
                # on `very_large`.
                #
                # Checked on EVERY node, not on a sampled subset. Sampling every
                # Nth node bounds the number of nodes between two looks at the
                # clock, which is not the quantity that matters: one node calls
                # generator.generate() (days x slots x rooms) and then scores
                # every candidate against the look-ahead window, so its cost
                # grows with the instance and the interval between two samples
                # is unbounded in seconds. Sampling every 512 nodes still
                # overran a 5 s budget to 65-168 s. time.time() costs tens of
                # nanoseconds against a node costing microseconds to
                # milliseconds, so the honest check is also the cheap one.
                #
                # Firing costs reproducibility, so it says so (ST-SCHED-013) —
                # the same contract the LNS emergency cap already follows.
                if deadline is not None and time.time() >= deadline:
                    self._clock_capped = True
                    return _stop()
                iterations[0] += 1

                if idx == n:
                    placed_count = sum(1 for a in solution if a is not None)
                    if placed_count > best_count[0]:
                        best_count[0] = placed_count
                        best_solution[:] = list(solution)
                        stale_leaves[0] = 0
                    else:
                        stale_leaves[0] += 1
                    return ('ret', placed_count == n)

                # Skip warm-started classes (already placed). This was
                # `return solve(idx + 1)` — a tail call, so it needs no frame.
                if idx in warm_placed:
                    idx += 1
                    continue

                cls = flexible[idx]
                candidates = generator.generate(cls)

                # ST-SCHED-006: honour `same_day` here, not only in LNS repair.
                # RepairStrategy has always filtered its candidates this way,
                # but greedy construction did not, so a `same_day` class the
                # greedy put on the wrong day stayed there unless LNS happened
                # to destroy and repair it — the protection level silently held
                # or not depending on the search.
                fixed_day = same_day_map.get(cls_key(cls))
                if fixed_day is not None:
                    candidates = [c for c in candidates if c[0] == fixed_day]

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

                stack.append({"idx": idx, "scored": scored, "k": 0,
                              "applied": None})
                return ('push', None)

        def advance():
            """Try the top frame's next option, or retire the frame."""
            frame = stack[-1]
            idx = frame["idx"]
            scored = frame["scored"]
            k = frame["k"]
            frame["k"] = k + 1
            if k < len(scored):
                day, slot, room, _score = scored[k]
                solution[idx] = (day, slot, room)
                _add(flexible[idx], day, slot, room)
                frame["applied"] = (day, slot, room)
                return enter(idx + 1)
            if k == len(scored):
                # "Try skipping this class" — nothing placed, nothing to undo.
                frame["applied"] = None
                return enter(idx + 1)
            stack.pop()
            return ('ret', False)

        action, value = enter(0)
        while True:
            if action == 'push':
                action, value = advance()
                continue
            # action == 'ret'
            if not stack:
                break
            if value:
                # The child succeeded. The recursion did `return True` WITHOUT
                # undoing its placement, so this frame's `applied` stays in the
                # occupancy maps; just propagate.
                stack.pop()
                continue
            frame = stack[-1]
            if frame["applied"] is not None:
                _remove(flexible[frame["idx"]], *frame["applied"])
                solution[frame["idx"]] = None
                frame["applied"] = None
            if aborted[0]:
                # ST-PERF-008. Without this the deadline bounded the search but
                # not the return: `advance()` would go on re-applying and
                # re-removing every untried candidate of every frame still on
                # the stack, one `enter()` short-circuit at a time. That is real
                # occupancy work, O(depth x candidates) of it, done entirely
                # past the deadline and counted by nothing. Unwind instead.
                stack.pop()
                continue
            action, value = advance()

        # ── ST-SCHED-001: resynchronise occupancy with the answer ──────────
        #
        # This is the defect that made the raw optimizer output invalid.
        # `solve()` records its answer as a SNAPSHOT (`best_solution`) taken at
        # a leaf, but keeps mutating `solution` and the occupancy maps as the
        # search continues. There are two exits:
        #
        #   * Full success — every class placed. `solve` returns True and each
        #     frame returns True without running its matching `_remove`, so the
        #     occupancy maps still describe the answer. (This is why the `tiny`
        #     preset was always clean: 5/5 classes placed.)
        #   * Anything else — a partial best, or the iteration budget running
        #     out. Every frame falls through to `_remove` and the stack unwinds
        #     completely, emptying the occupancy maps back to the baseline —
        #     while `best_solution` still claims a full set of placements.
        #
        # In the second case the caller was handed a solution and a validator
        # that disagreed about every cell in it. Measured on the 25-class
        # `small` preset: 20 placements returned, 0 of them known to the
        # validator. `_lns_improve` then ran its whole repair loop against a
        # near-empty grid and stacked classes on top of each other, which is
        # exactly the 18 room/lecturer/group double-books the oracle reported.
        #
        # `solution` is a faithful mirror of what the occupancy maps hold at
        # this point (every `_add` in the loop above is paired with an assignment
        # to `solution[idx]`, and every `_remove` with clearing it), so the two
        # can be reconciled index by index.
        for i in range(n):
            if solution[i] == best_solution[i]:
                continue
            if solution[i] is not None:
                _remove(flexible[i], *solution[i])
            if best_solution[i] is not None:
                _add(flexible[i], *best_solution[i])
            solution[i] = best_solution[i]

        greedy_stats = {
            "iterations_used": iterations[0],
            "max_iterations": self.max_iterations,
            "budget_exhausted": iterations[0] >= self.max_iterations,
            # True when the search ended because it had stopped finding better
            # solutions — a real stopping condition rather than a timeout.
            "converged": (aborted[0]
                          and stale_leaves[0] >= no_improve_limit),
            "no_improve_limit": no_improve_limit,
            "warm_started": len(warm_placed),
        }
        return best_solution, greedy_stats

    def _lns_improve(self, flexible, solution, validator, generator,
                     scorer, tt_scorer, time_limit_override=None,
                     run_number=0, total_runs=1,
                     conflict_graph=None, analyzer=None,
                     propagator=None, same_day_map=None,
                     improve_only_scores=None, deadline=None):
        """LNS improvement phase with adaptive strategy selection.

        Uses AdaptiveStrategySelector to learn which destroy strategies
        produce better improvements and adjust selection probabilities.

        ``deadline`` is the absolute ``time.time()`` at which the *whole solve*
        must stop — ``global_start + multi_start_time_limit``, the same value
        ``_greedy_construct`` already receives. It is not the same thing as
        ``time_limit_override``, which is a duration for this phase alone.
        Passing the duration and comparing it against a clock started at the top
        of this method is what let a capped solve overrun its budget: measured
        through ``optimized_reschedule_all(make_preset('normal'), 8.0)``, the
        run-0 LNS phase was entered at t=1.58 s and ran to t=9.71 s, 1.71 s past
        a deadline it had no way to see.
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
            # The solve-wide emergency cap, checked against the absolute
            # deadline rather than this phase's own stopwatch. `elapsed` below
            # measures only how long THIS phase has run; comparing it to
            # `multi_start_time_limit`, the budget for the entire solve, let
            # each phase spend the whole budget over again. Same shape as the
            # greedy phase's check.
            if deadline is not None and time.time() >= deadline:
                self._clock_capped = True
                break
            if deterministic_loop:
                # Iteration-bounded: only the emergency cap can cut this short,
                # and doing so costs reproducibility (reported in the summary).
                # With no absolute deadline supplied -- a direct caller rather
                # than optimize() -- fall back to the phase-local stopwatch so
                # the cap still exists at all.
                if deadline is None and elapsed >= self.multi_start_time_limit:
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
            # The likeliest cause on a frozen build, and the one that used to
            # be indistinguishable from "deep mode simply did nothing".
            self._cpsat_failure = "import_failed"
            return None
        if not HAS_ORTOOLS:
            self._cpsat_failure = "ortools_missing"
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

        # The child is a spawn, so it re-imports translations and would answer
        # in English regardless of the UI language. Carry it across explicitly.
        from scheduler_app.translations import get_language
        proc = multiprocessing.Process(
            target=_cpsat_subprocess_worker,
            args=(state_snap, dict(self.weights or {}),
                  self.cpsat_time_limit, protected_indices,
                  heuristic_indices, result_queue, self.seed,
                  get_language()))
        proc.start()

        # Poll until the subprocess finishes, calling progress callback
        # so the Qt event loop can repaint the status bar.
        timeout = self.cpsat_time_limit + 30  # generous grace period
        deadline = time.time() + timeout
        while proc.is_alive() and time.time() < deadline:
            proc.join(timeout=0.25)
            if self.progress_callback:
                self.progress_callback(0, 0, 0, -1, 0)

        # ST-ARCH-011/ST-SCHED-014: every one of these returns None, and the
        # caller then falls back to the heuristic result with no message at
        # all. The user asked for Thorough and silently got Quick. Record WHY,
        # so `summary['cpsat_failure']` can say so.
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=5)
            self._cpsat_failure = "timeout"
            return None

        if proc.exitcode != 0:
            # Subprocess died: native segfault, abort, or -- much more likely
            # on a frozen build -- an ImportError re-importing the module chain
            # in the spawned child.
            self._cpsat_failure = "subprocess_exit_%s" % proc.exitcode
            return None

        if result_queue.empty():
            self._cpsat_failure = "no_result"
            return None

        try:
            result = result_queue.get_nowait()
        except Exception:
            self._cpsat_failure = "no_result"
            return None

        if result is None:
            self._cpsat_failure = "unavailable"
            return None
        if result.get("status") != "ok":
            self._cpsat_failure = str(result.get("status") or "error")
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

        # ST-PERF-008: this is `optimized_auto_place`, i.e. the user adding one
        # class and waiting. It shares `multi_start_time_limit` with the full
        # reschedule because it is the same search; without a deadline here the
        # bound only ever applied to the Generate button.
        solution, _ = self._greedy_construct(
            combined, validator, generator, scorer,
            improve_only_scores=improve_only_scores,
            deadline=time.time() + self.multi_start_time_limit)

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
