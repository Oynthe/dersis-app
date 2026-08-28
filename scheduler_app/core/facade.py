"""The optimization bridge: the eight entry points the app actually calls.

ST-ARCH-010. These functions used to live at the bottom of ``core/logic.py``
under a banner comment that already called them a bridge, and every one of them
had to import its collaborator *inside the function body* -- thirteen deferred
imports in all. That was not a style choice. ``logic.py`` holds the scheduling
primitives (``slot_index``, ``build_occupancy``, ``find_conflicts``, ...) that
``schedule_optimizer``, ``constraint_validator``, ``placement_scorer`` and nine
other ``core`` modules import at module scope; the moment ``logic`` imported any
of them back at module scope, ``import scheduler_app.core.workflow`` raised
ImportError. Measured: 0 of the 13 could be promoted where they stood.

Splitting the file removes the reason to defer. The primitives in ``logic.py``
now import nothing from ``core`` that is not strictly downward, and this module
-- which nothing inside the engine imports -- sits above all of them and can
import whatever it likes at module scope. Measured effect: the ``core``
strongly connected component went from 15 modules to 0, mutually importing
pairs from 7 to 0, deferred imports in ``logic.py`` from 13 to 0.

Two rules keep that true, and ``tests/test_import_layering.py`` enforces both:

* **No module under ``core`` may import this one** except ``workflow``. An
  engine module that imports the facade puts ``logic`` back in the knot;
  measured, it makes ``import scheduler_app.core.workflow`` raise immediately.
* **``logic.py`` must not re-export these eight names** "for compatibility".
  Both mechanisms were built and measured: a star re-export makes the cycle a
  *module-level* one and the app stops starting; a lazy PEP 562 ``__getattr__``
  runs fine and takes the component to 16 -- bigger than the 15 this split was
  meant to remove. Update the call site instead. There are two in production.

Imports here use the real ``scheduler_app.core.*`` names rather than the flat
``scheduler_app.*`` shim aliases on purpose: ``_ShimLoader`` in
``scheduler_app/__init__.py`` puts an *empty* alias module into ``sys.modules``
before ``exec_module`` runs, so a re-entrant flat-name import cannot see names
the real module has already bound. That is the mechanism that made the thirteen
deferrals load-bearing, and this module has no reason to route through it.
"""

from scheduler_app.core.candidate_generator import CandidateGenerator
from scheduler_app.core.constants import (
    DEFAULT_MULTI_START_RUNS,
    DEFAULT_MULTI_START_TIME_LIMIT,
)
from scheduler_app.core.constraint_negotiator import ConstraintNegotiator
from scheduler_app.core.constraint_validator import ConstraintValidator
from scheduler_app.core.explanation_engine import ExplanationEngine
from scheduler_app.core.logic import get_placed_classes
from scheduler_app.core.models import (
    DEFAULT_OPTIMIZER_SEED,
    PROTECTION_LOCKED,
    effective_day, effective_time, effective_room,
    cls_key,
)
from scheduler_app.core.placement_scorer import PlacementScorer
from scheduler_app.core.schedule_analytics import ScheduleAnalytics
from scheduler_app.core.schedule_optimizer import ScheduleOptimizer
from scheduler_app.i18n.translations import tr


def optimized_auto_place(state, new_cls, weights=None):
    """AI-assisted auto-placement using scored candidate ranking.

    Generates all valid candidates, scores them, and picks the best.
    Falls back to reschedule if no direct placement exists.

    Same return signature as auto_place_class.
    """
    # No seed needed: place_with_reschedule reaches only quick_place and
    # _greedy_construct, and neither draws from the RNG. The randomized paths
    # (_perturb_ordering, _lns_improve) are called only from optimize().
    optimizer = ScheduleOptimizer(state, weights=weights)
    return optimizer.place_with_reschedule(new_cls)


def optimized_reschedule_all(state, weights=None, protected_ids=None,
                             progress_callback=None,
                             multi_start_runs=DEFAULT_MULTI_START_RUNS,
                             multi_start_time_limit=DEFAULT_MULTI_START_TIME_LIMIT,
                             use_cpsat=False, cpsat_time_limit=15.0,
                             parallel_workers=0,
                             seed=DEFAULT_OPTIMIZER_SEED,
                             cancel_token=None, **optimizer_kwargs):
    """Hybrid timetable-wide reschedule with multi-start LNS + optional CP-SAT.

    Preserves pinned and protected placements. Uses greedy construction
    with look-ahead scoring followed by iterative LNS destroy-repair
    for better overall quality. Runs multiple independent optimization
    passes from perturbed starting states and keeps the best.
    Optionally refines with Google OR-Tools CP-SAT constraint solver.

    Args:
        multi_start_runs / multi_start_time_limit: the shipped search budget.
                          These defaults ARE the budget the app runs — this is
                          the signature `SchedulingWorkflow.reschedule` calls
                          with no overrides — so they must not be literals
                          here. `core.constants` is the one definition;
                          `solver_worker` scales the progress bar off the same
                          two names, and a literal reintroduced here would
                          shadow them silently (measured: the bar stops at
                          62.5 % on a solve that ran to completion).
                          `tests/test_solver_work.py` fails on a literal.
        parallel_workers: Number of worker processes for parallel
                          candidate evaluation. 0 = auto, negative = disabled.
        seed: RNG seed; the default makes the result reproducible
              (ST-SCHED-013). None draws a fresh seed. The seed used is
              reported back as summary['seed'].

    Returns:
        (placed_list, unplaced_list, changes, summary) where summary
        contains before/after quality breakdown and improvement stats.
    """
    optimizer = ScheduleOptimizer(
        state, weights=weights, protected_ids=protected_ids,
        progress_callback=progress_callback,
        multi_start_runs=multi_start_runs,
        multi_start_time_limit=multi_start_time_limit,
        use_cpsat=use_cpsat, cpsat_time_limit=cpsat_time_limit,
        parallel_workers=parallel_workers, seed=seed,
        cancel_token=cancel_token, **optimizer_kwargs)
    return optimizer.optimize()


def optimized_batch_schedule(state, new_classes, weights=None):
    """AI-assisted batch scheduling with candidate scoring.

    Phase 1: Place new classes using scored candidates.
    Phase 2: Full reschedule if any fail.

    Same return signature as batch_schedule.
    """
    new_ids = {cls_key(c) for c in new_classes}
    # ST-SCHED-007: a `protection="locked"` class is not flexible. Phase 2
    # re-solves everything in `existing_flexible` from scratch, so including
    # locked classes here let adding one new lesson relocate a lesson the user
    # had explicitly frozen — observed moving monday/09:00 -> tuesday/10:00.
    # Excluded here, they stay in build_occupancy() and act as fixed points
    # the reschedule has to work around, which is what "locked" means.
    existing_flexible = [c for c in state["classes"]
                         if c["placed"] and not c["pinned"]
                         and c.get("protection") != PROTECTION_LOCKED
                         and cls_key(c) not in new_ids]

    new_pinned = [c for c in new_classes if c["pinned"]]
    new_flexible = [c for c in new_classes if not c["pinned"]]

    # ── Phase 1: Place new classes around existing ──
    validator = ConstraintValidator(state, exclude_ids=new_ids)
    generator = CandidateGenerator(state, validator=validator)
    scorer = PlacementScorer(state, validator, weights=weights)

    placed_result = []
    unplaced_result = []

    # Place new pinned classes
    pinned_ok = True
    for cls in new_pinned:
        day, slot, room = cls["pinned_day"], cls["pinned_time"], cls["pinned_classroom"]
        if validator.check_placement(cls, day, slot, room):
            validator.add_placement(cls, day, slot, room)
            placed_result.append((cls, day, slot, room))
        else:
            pinned_ok = False
            conflicts = validator.find_conflicts(cls, day, slot, room)
            reason = "; ".join(conflicts) if conflicts else tr("conflicts.batch_conflict")
            unplaced_result.append((cls, reason))

    # Sort by difficulty (hardest first)
    new_flexible_sorted = validator.sort_by_difficulty(new_flexible)

    # Try placing each using scored candidates with look-ahead
    all_placed = True
    for pos, cls in enumerate(new_flexible_sorted):
        candidates = generator.generate(cls)
        if candidates:
            remaining = new_flexible_sorted[pos + 1:]
            if remaining and len(candidates) > 1:
                scored = scorer.score_candidates_with_lookahead(
                    cls, candidates, remaining, generator)
            else:
                scored = scorer.score_candidates(cls, candidates)
            best = scored[0]
            day, slot, room = best[0], best[1], best[2]
            validator.add_placement(cls, day, slot, room)
            placed_result.append((cls, day, slot, room))
        else:
            all_placed = False
            break

    if all_placed and pinned_ok:
        # Every already-placed class keeps its position, locked ones included —
        # they are absent from `existing_flexible` by design now, and leaving
        # them out of the result would report them as no longer placed.
        for cls in state["classes"]:
            if (cls["placed"] and not cls["pinned"]
                    and cls_key(cls) not in new_ids):
                placed_result.append((cls, cls["placed_day"],
                                      cls["placed_time"],
                                      cls["placed_classroom"]))
        return placed_result, unplaced_result, False

    # ── Phase 2: Full reschedule ──
    # Remove phase 1 partial results
    placed_result = []
    unplaced_result = []

    all_exclude = new_ids | {cls_key(c) for c in existing_flexible}
    validator2 = ConstraintValidator(state, exclude_ids=all_exclude)

    # Place pinned
    for cls in new_pinned:
        day, slot, room = cls["pinned_day"], cls["pinned_time"], cls["pinned_classroom"]
        if validator2.check_placement(cls, day, slot, room):
            validator2.add_placement(cls, day, slot, room)
            placed_result.append((cls, day, slot, room))
        else:
            conflicts = validator2.find_conflicts(cls, day, slot, room)
            reason = "; ".join(conflicts) if conflicts else tr("conflicts.pinned_conflict")
            unplaced_result.append((cls, reason))

    combined = existing_flexible + new_flexible
    combined = validator2.sort_by_difficulty(combined)

    generator2 = CandidateGenerator(state, validator=validator2)
    scorer2 = PlacementScorer(state, validator2, weights=weights)

    # Greedy with backtracking via optimizer.
    # ST-PERF-008: bounded like every other greedy phase. This one is reached
    # from the "add classes" and "place batch" buttons, so an unbounded search
    # here is a frozen window with no progress dialog behind it.
    import time as _time
    optimizer = ScheduleOptimizer(state, weights=weights)
    solution, _greedy_stats = optimizer._greedy_construct(
        combined, validator2, generator2, scorer2,
        deadline=_time.time() + optimizer.multi_start_time_limit)

    for i, cls in enumerate(combined):
        if solution[i] is not None:
            day, slot, room = solution[i]
            placed_result.append((cls, day, slot, room))
        else:
            reason = generator2.unplaced_reason(cls)
            unplaced_result.append((cls, reason))

    return placed_result, unplaced_result, True


def score_placement(state, cls, day, slot, room, weights=None):
    """Score a single placement using the PlacementScorer.

    Convenience function for UI display and feedback logging.
    """
    exclude = {cls_key(cls)}
    validator = ConstraintValidator(state, exclude_ids=exclude)
    scorer = PlacementScorer(state, validator, weights=weights)
    return scorer.score(cls, day, slot, room)


def score_placement_explained(state, cls, day, slot, room, weights=None):
    """Score a placement and return (score, breakdown, explanation).

    Returns:
        (float, dict, dict) — numerical score, component breakdown,
        and human-readable explanation from ExplanationEngine.
    """
    exclude = {cls_key(cls)}
    validator = ConstraintValidator(state, exclude_ids=exclude)
    scorer = PlacementScorer(state, validator, weights=weights)
    score, breakdown = scorer.score_explained(cls, day, slot, room)
    engine = ExplanationEngine()
    explanation = engine.explain_placement(cls, day, slot, room, breakdown)
    return score, breakdown, explanation


def analyze_schedule(state, placements=None):
    """Analyze full timetable quality and return structured analytics.

    If placements is None, builds list from currently placed classes.

    Returns:
        dict with global_score, grade, per-entity metrics, and insights.
    """
    if placements is None:
        placements = []
        for cls in get_placed_classes(state):
            day = effective_day(cls)
            slot = effective_time(cls)
            room = effective_room(cls)
            placements.append((cls, day, slot, room))

    analytics = ScheduleAnalytics(state)
    return analytics.analyze(placements)


def negotiate_after_optimization(state, placed_list, unplaced_list):
    """Run constraint negotiation after an optimization pass.

    Called when ScheduleOptimizer leaves unplaced classes.

    Returns:
        dict with negotiation results or None if all placed.
    """
    negotiator = ConstraintNegotiator(state)
    return negotiator.negotiate_after_optimization(placed_list, unplaced_list)


def apply_negotiation_suggestion(cls, suggestion):
    """Apply a single constraint relaxation suggestion to a class.

    Modifies the class dict in-place.

    Returns:
        True if the suggestion was applied.
    """
    return ConstraintNegotiator.apply_suggestion(None, cls, suggestion)
