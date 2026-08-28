"""Protection levels on the DEFAULT (greedy + LNS) engine (ST-ARCH-001 item 5).

``protection`` is the user's promise-keeper. ``locked`` means "never move this";
``soft`` means "leave it alone"; ``same_day`` means "move it within its day if
you must"; ``improve_only`` means "move it only somewhere at least as good"
(``core/models.py``).

Every named protection test in the suite before Phase 7 lived in
``tests/test_cpsat_semantics.py`` — i.e. on the path that is **off by default**.
``use_cpsat`` is opt-in; pressing *Reschedule All* runs greedy + LNS. The
Phase 7 measurement round broke the two protections nobody had pinned there and
ran ``test_cpsat_semantics``, ``test_optimizer_occupancy``, ``test_greedy_bounds``
and ``test_validator_unification``:

    greedy+LNS ignore `improve_only`   -> GREEN, nothing failed
    greedy ignores `soft`              -> GREEN, nothing failed

Phase 3's record says ``improve_only`` was broken in both engines and fixed by
putting both sides on one scorer. That fix was unguarded on the engine users
actually run. ``locked`` is pinned already
(``test_validator_unification.py::test_reschedule_all_never_moves_a_locked_class``)
and so is the CP-SAT side of all four levels; this module is the heuristic half
of ``soft`` and ``improve_only``.

Traps this module defends against
---------------------------------
**Trap A — a fixture with no pressure.** "The protected class did not move" is
free if nothing wanted its cell. Each test therefore runs the *same instance*
a second time with ``protection`` removed and requires the optimizer to move the
class then. That control is what makes the real assertion mean something: it
proves the engine had both the motive and the opportunity.

**Trap B — clean because empty.** A run that places nothing satisfies "did not
move". Both tests assert the control run places strictly more classes than the
protected run, so an engine that returned an empty schedule fails.

Runtime: 2- and 3-class instances, ``multi_start_runs=1``, no CP-SAT, no worker
pool — measured ~0.1 s per test on the audit machine. Fast lane.
"""
import pytest

from scheduler_app.core.models import (
    PROTECTION_IMPROVE_ONLY,
    PROTECTION_NONE,
    PROTECTION_SOFT,
    mark_placed,
    new_class,
    new_state,
)

# The suite-wide fixed optimizer seed (core/models.py). Pinned explicitly so a
# failure here is reproducible from the message alone.
SEED = 20260101


def _grid(days, slots, rooms):
    state = new_state()
    state["days"] = list(days)
    state["slots"] = list(slots)
    state["classrooms"] = list(rooms)
    # 0 == "capacity unknown": keeps the (soft) capacity preference out of the
    # scoring so each fixture isolates exactly the protection under test.
    state["classroom_capacities"] = {r: 0 for r in rooms}
    state["lecturers"] = []
    state["years"] = {"Year-1": ["A", "B", "C"]}
    return state


def _add_class(state, name, lecturer, branch, **fields):
    cls = new_class()
    cls["class_code"] = cls["name"] = name
    cls["lecturer"] = lecturer
    if lecturer not in state["lecturers"]:
        state["lecturers"].append(lecturer)
    cls["targets"] = [{"year": "Year-1", "branch": branch}]
    cls["duration"] = 1
    cls["participants"] = 0
    cls.update(fields)
    state["classes"].append(cls)
    return cls


def _pos(cls):
    return (cls["placed_day"], cls["placed_time"], cls["placed_classroom"])


def _instance(level):
    """``Guard`` sits on the only cell ``Only10`` can ever use.

    One room, three hours. ``Guard`` is placed at 10:00; ``Only10`` is allowed
    monday/10:00 and nothing else. So:

        protection honoured -> Guard stays, Only10 unplaced -> 1 placed
        protection ignored  -> Guard steps aside, Only10 fits -> 2 placed

    The engine maximises placements, so ignoring the protection is strictly
    more attractive to it. That is the pressure Trap A demands.
    """
    state = _grid(["monday"], ["09:00", "10:00", "11:00"], ["R001"])
    guard = _add_class(state, "Guard", "L1", "A", protection=level)
    mark_placed(guard, "monday", "10:00", "R001")
    _add_class(state, "Only10", "L2", "B",
               allowed_days=["monday"], allowed_times=["10:00"])
    return state, guard


def _run_heuristic(state):
    """The production reschedule with the shipped default engine.

    ``use_cpsat=False`` is the default the app ships with; the point of this
    module is that it was the unguarded path.

    ``multi_start_runs=2`` is a measured minimum, not a round number. Run 0
    warm-starts from the existing placements, so it re-seats ``Guard`` on
    monday/10:00 before anything else is considered and never discovers the
    two-lesson answer — at ``multi_start_runs=1`` even an *unprotected* Guard
    stays put and the control below would be vacuous. Run 1 starts cold and
    finds it. Measured identical outcomes at 2, 3 and 5 restarts (5 is the
    shipped default), so 2 buys the whole property for a fifth of the runtime.

    ``parallel_workers=-1`` disables the scorer pool (a 2-class instance cannot
    repay a process spawn) and ``multi_start_time_limit=1e9`` takes the wall
    clock out of the answer — ``deterministic`` is asserted below, so a run that
    was truncated cannot be read as a result.
    """
    from scheduler_app.core.workflow import SchedulingWorkflow

    workflow = SchedulingWorkflow(state, lambda: {})
    return workflow.reschedule(
        {}, use_cpsat=False, seed=SEED,
        multi_start_runs=2, multi_start_time_limit=1e9,
        lns_iterations=40, parallel_workers=-1)


def _placed_map(result):
    return {c["name"]: (d, s, r) for c, d, s, r in result.placed}


def _assert_a_real_solve(result, label):
    """Trap B. Refuse to draw a conclusion from a run that did nothing."""
    assert result.placed, f"{label}: the optimizer proposed an empty schedule"
    assert result.summary["cpsat_used"] is False, (
        f"{label}: CP-SAT ran, so this says nothing about the heuristic engine")
    assert result.summary["deterministic"] is True, (
        f"{label}: the run was clock-capped, so its answer is not reproducible "
        "and a green result here would be luck")


# ===========================================================================
# soft
# ===========================================================================
@pytest.mark.engine
def test_reschedule_all_leaves_a_softly_protected_lesson_where_it_is():
    """Pins ST-ARCH-001 item 5 — ``soft`` on the default engine.

    A failure means a user ticked "Softly Protected" on a lesson, pressed
    *Reschedule All*, and the app moved it anyway to fit something else in.
    ``optimize()`` folds ``soft`` into ``effective_protected_ids``
    (``core/schedule_optimizer.py``) precisely so this cannot happen; before
    Phase 7 nothing checked that it still did.
    """
    state, guard = _instance(PROTECTION_SOFT)
    before = _pos(guard)

    result = _run_heuristic(state)
    _assert_a_real_solve(result, "soft")
    got = _placed_map(result)

    assert got.get("Guard") == before, (
        f"a protection='soft' lesson moved from {before} to {got.get('Guard')} "
        "during a default (use_cpsat=False) Reschedule All")

    # Trap A: the same instance without the protection. If the engine does NOT
    # move Guard here, the fixture applied no pressure and the assertion above
    # was free.
    control_state, control_guard = _instance(PROTECTION_NONE)
    control = _run_heuristic(control_state)
    _assert_a_real_solve(control, "soft/control")
    control_got = _placed_map(control)

    assert control_got.get("Guard") != _pos(control_guard), (
        "TOOTHLESS FIXTURE: with the protection removed the engine still left "
        "Guard on monday/10:00, so 'it did not move' proves nothing about "
        "protection. Restore the pressure before trusting this module.")
    assert len(control.placed) > len(result.placed), (
        "TOOTHLESS FIXTURE: dropping the protection bought the engine no extra "
        f"placement ({len(control.placed)} vs {len(result.placed)}), so it had "
        "no reason to move Guard in the first place.")


# ===========================================================================
# improve_only
# ===========================================================================
@pytest.mark.engine
def test_reschedule_all_never_moves_an_improve_only_lesson_somewhere_worse():
    """Pins ST-ARCH-001 item 5 — ``improve_only`` on the default engine.

    ``improve_only`` means "move it only to somewhere at least as good", judged
    with ``TimetableScorer.placement_score`` (lower is better) — the same
    function that builds the optimizer's own baseline. A failure means the app
    took a lesson out of a good mid-morning hour and demoted it to suit another
    class: exactly the trade the level forbids.

    Phase 3's record says this was broken in both engines and fixed by putting
    both sides on one scorer. This is the heuristic side of that fix.
    """
    from scheduler_app.core.timetable_scorer import TimetableScorer

    state, guard = _instance(PROTECTION_IMPROVE_ONLY)
    scorer = TimetableScorer(state)
    before = _pos(guard)
    baseline = scorer.placement_score(guard, *before)

    # Fixture sanity: every alternative hour must be strictly worse, or "did
    # not get worse" is satisfiable by moving and the test proves nothing.
    alternatives = {s: scorer.placement_score(guard, "monday", s, "R001")
                    for s in state["slots"] if s != before[1]}
    assert alternatives and all(v > baseline for v in alternatives.values()), (
        f"TOOTHLESS FIXTURE: baseline {baseline} at {before[1]}, alternatives "
        f"{alternatives} — at least one is not strictly worse")

    result = _run_heuristic(state)
    _assert_a_real_solve(result, "improve_only")
    landed = _placed_map(result).get("Guard")

    if landed is not None and landed != before:
        after = scorer.placement_score(guard, *landed)
        assert after <= baseline, (
            f"a protection='improve_only' lesson was moved from {before} "
            f"(score {baseline}) to {landed} (score {after}); lower is better, "
            "so this move made it worse")

    # Trap A / Trap B: with the level removed the engine must take the trade.
    control_state, control_guard = _instance(PROTECTION_NONE)
    control = _run_heuristic(control_state)
    _assert_a_real_solve(control, "improve_only/control")
    control_landed = _placed_map(control).get("Guard")

    assert control_landed != _pos(control_guard), (
        "TOOTHLESS FIXTURE: without the protection the engine still left Guard "
        "where it was, so the assertion above was free.")
    assert control.summary["cpsat_used"] is False
    assert len(control.placed) > len(result.placed), (
        "TOOTHLESS FIXTURE: the engine gained no placement by moving Guard "
        f"({len(control.placed)} vs {len(result.placed)}), so it had no motive "
        "to violate the protection.")
