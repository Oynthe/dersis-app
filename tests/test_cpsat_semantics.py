"""CP-SAT hard-constraint semantics — ST-SCHED-005 and ST-SCHED-006.

DERSİS ships two schedulers. The heuristic engine (greedy + LNS) validates every
candidate through ``ConstraintValidator``; the optional "deep optimization" pass
translates the problem into an OR-Tools CP-SAT model and re-derives the rules by
hand. This module pins the two places where that re-derivation lost a rule.

What is pinned here
-------------------
**ST-SCHED-005 (High) — availability is checked only at the start hour.**
``CPSATScheduler._valid_start_slots`` filters candidate *start* slots through
``apply_lecturer_availability_filters`` and then only checks that the block fits
on the grid. The hours a multi-hour class *spans* are never checked, so a
duration-3 class can be placed across two hours its lecturer is barred from.
``ConstraintValidator`` disagrees at commit time, so the lesson is reported as
placed and then quietly dropped.

**ST-SCHED-006 (High) — CP-SAT only understands ``locked``.**
``ScheduleOptimizer.optimize`` merges ``PROTECTION_SOFT`` into
``effective_protected_ids``, but ``_cpsat_optimize`` ships only the raw
``self.protected_ids`` to the solver, and ``CPSATScheduler.solve`` classifies as
protected only what is in that set. ``soft``, ``same_day`` and ``improve_only``
therefore land in ``flexible`` and move freely. Worse, a moved ``soft`` class is
skipped by the ``changes[]`` builder (it *is* in ``effective_protected_ids``),
so the move is invisible to the impact panel, the "n classes moved" status line
and the undo/rollback machinery.

Layers, and why each test sits where it does
--------------------------------------------
* **ST-SCHED-005 → ``CPSATScheduler`` directly.** ``_valid_start_slots`` is
  unambiguously the solver's own code, it runs in-process in ~30 ms, and driving
  it directly removes every downstream layer that could mask the defect.
* **ST-SCHED-006 → the real ``ScheduleOptimizer.optimize(use_cpsat=True)``,
  subprocess and all.** The `soft` half of this defect straddles two files
  (``_cpsat_optimize`` builds the wrong id set; ``CPSATScheduler`` cannot express
  ``same_day``/``improve_only`` at all), so a test that drove ``CPSATScheduler``
  directly would be pinning one particular repair. Driving the public entry point
  keeps the pins fix-agnostic: any repair, at either layer, turns them green.
* **The commit half of ST-SCHED-005 → ``SchedulingWorkflow``**, because "the user
  lost a lesson" is only observable after ``apply_reschedule``.

Traps this module has to defend against (all measured, see the report)
----------------------------------------------------------------------
**Trap A — the acceptance gate hides the bug.** ``optimize()`` keeps the CP-SAT
answer only when it beats the heuristic (``cpsat_placed_count >
heur_placed_count``, or an equal count with better quality —
``schedule_optimizer.py::optimize``). The obvious ST-SCHED-005 end-to-end fixture
(one duration-3 class, lecturer free at 09:00 *and* 13:00-15:00) is silently
repaired by that gate: the heuristic finds the legal 13:00 slot, CP-SAT proposes
the illegal 09:00 one, the qualities tie, and the heuristic answer wins. Every
protection fixture below is therefore built so that ignoring the protection lets
CP-SAT place **strictly more** classes than the heuristic can — that is what
forces its (wrong) answer through the gate. Take that property away and these
tests go green without a line of production code changing.

**Trap B — "clean because unplaced".** "Never spans an unavailable hour" is
vacuously true for a class nobody placed. ``test_cpsat_places_a_multi_hour_class
_inside_its_available_window`` is the floor: it demands that a multi-hour class
with exactly one legal window actually lands in it. It passes today and must
keep passing, so a fix that simply refuses to place multi-hour classes fails.

**Trap C — "clean because trivial".** ``CPSATScheduler.solve`` short-circuits to
``status == "TRIVIAL"`` when nothing is flexible, returning the input unchanged —
which satisfies every "the protected class did not move" assertion without ever
building a model. Each protection fixture keeps a genuinely flexible rival class
in play, and the tests assert the solver reported a real solve.

Runtime (measured, .venv-audit, idle box)
-----------------------------------------
Whole module ~5 s. The four protection tests and the end-to-end commit test each
spawn a real CP-SAT subprocess and cost ~1.2 s apiece; none is near the 10 s
``slow`` threshold, so nothing here is marked ``slow`` and CI runs all of it.
``cpsat_time_limit`` is pinned to 5 s and every model here solves to OPTIMAL in
under 50 ms, so that budget is slack, not a deadline.

Determinism
-----------
``summary['deterministic']`` is False whenever CP-SAT ran (its budget is still
wall-clock), so nothing here asserts an exact objective value or an exact cell.
Every assertion is a hard-constraint property that must hold for any answer the
search returns.
"""
import pytest

from scheduler_app.core.cpsat_scheduler import HAS_ORTOOLS
from scheduler_app.core.models import (
    PROTECTION_IMPROVE_ONLY,
    PROTECTION_LOCKED,
    PROTECTION_SAME_DAY,
    PROTECTION_SOFT,
    mark_placed,
    new_class,
    new_state,
)

from _support.schedule_oracle import (
    check_schedule,
    format_violations,
    hard_violation_count,
)

# ortools is a hard dependency (requirements.txt:15, pinned in
# requirements-lock.txt:18). A machine without it cannot verify ST-SCHED-005 or
# ST-SCHED-006 at all, so the guard test below fails loudly rather than letting
# the whole module evaporate into a green run of zero assertions.
requires_ortools = pytest.mark.skipif(
    not HAS_ORTOOLS,
    reason="ortools is not installed — CP-SAT semantics cannot be verified")

# CP-SAT solves every model in this file in <50 ms; the budget only has to be
# comfortably larger than that.
CPSAT_TIME_LIMIT = 5.0

# The suite-wide fixed seed (scheduler_app/core/models.DEFAULT_OPTIMIZER_SEED).
SEED = 20260101


# ---------------------------------------------------------------------------
# Fixture builders (hand-built, no dataset_gen — every cell has to be countable
# by hand for the "CP-SAT can place strictly more" arithmetic to be checkable)
# ---------------------------------------------------------------------------
def _grid(days, slots, rooms):
    state = new_state()
    state["days"] = list(days)
    state["slots"] = list(slots)
    state["classrooms"] = list(rooms)
    # 0 == "capacity unknown", which keeps the oracle's (soft) capacity rule
    # quiet so each fixture isolates exactly the invariant under test.
    state["classroom_capacities"] = {r: 0 for r in rooms}
    state["lecturers"] = []
    state["years"] = {"Year-1": ["A", "B", "C", "D", "E"]}
    return state


def _add_class(state, name, lecturer, branch, duration=1, **fields):
    cls = new_class()
    cls["class_code"] = cls["name"] = name
    cls["lecturer"] = lecturer
    if lecturer and lecturer not in state["lecturers"]:
        state["lecturers"].append(lecturer)
    cls["targets"] = [{"year": "Year-1", "branch": branch}]
    cls["duration"] = duration
    cls["participants"] = 0
    cls.update(fields)
    state["classes"].append(cls)
    return cls


def _set_availability(state, lecturer, allowed_hours):
    state["lecturer_availability"][lecturer] = {
        "allowed_days": [],
        "allowed_hours": list(allowed_hours),
        "excluded_days": [],
        "excluded_hours": [],
    }


def _occupied_hours(state, slot, duration):
    """The grid hours a block of *duration* starting at *slot* actually covers."""
    idx = state["slots"].index(slot)
    return state["slots"][idx:idx + duration]


def _pos(cls):
    return (cls["placed_day"], cls["placed_time"], cls["placed_classroom"])


def _run_optimizer(state):
    """The production reschedule with CP-SAT enabled. Returns optimize()'s tuple.

    Real ``ScheduleOptimizer``, real CP-SAT subprocess. ``parallel_workers=-1``
    disables the scorer pool (these instances are far too small to need it and a
    second process pool only adds spawn latency); ``multi_start_runs=1`` and the
    short LNS budget keep the heuristic phase down to a few hundred ms.
    """
    from scheduler_app.core.schedule_optimizer import ScheduleOptimizer

    optimizer = ScheduleOptimizer(
        state, weights=None,
        use_cpsat=True, cpsat_time_limit=CPSAT_TIME_LIMIT,
        multi_start_runs=1, lns_iterations=20, parallel_workers=-1,
        seed=SEED)
    return optimizer.optimize()


def _assert_cpsat_really_ran(summary, placed):
    """Trap C. Refuse to draw any conclusion from a run CP-SAT sat out."""
    assert summary["cpsat_used"] is True, (
        "CP-SAT never ran, so this test says nothing about CP-SAT semantics; "
        f"summary={ {k: summary[k] for k in ('cpsat_used', 'cpsat_status')} }")
    assert summary["cpsat_status"] in ("OPTIMAL", "FEASIBLE"), (
        "CP-SAT did not actually solve a model — status "
        f"{summary['cpsat_status']!r}. 'TRIVIAL' means every class was frozen "
        "and solve() returned its input unchanged, which satisfies every "
        "'did not move' assertion below without proving anything.")
    assert placed, "the optimizer proposed an empty schedule"


# ---------------------------------------------------------------------------
# The protection fixtures.
#
# `Guard` sits on monday/10:00/R001, the only room. `Only10` can be placed
# nowhere but monday/10:00 — so it fits if and only if Guard vacates.
#
#   honouring the protection  ->  Guard stays, Only10 unplaced  ->  1 placed
#   ignoring  the protection  ->  Guard moves, Only10 placed    ->  2 placed
#
# That 2 > 1 is what drives the wrong answer through optimize()'s acceptance
# gate (Trap A). Without it the heuristic's correct answer would win and these
# tests would pass while the defect is untouched.
# ---------------------------------------------------------------------------
def _protection_instance(level):
    state = _grid(["monday"], ["09:00", "10:00", "11:00"], ["R001"])
    guard = _add_class(state, "Guard", "L1", "A", protection=level)
    mark_placed(guard, "monday", "10:00", "R001")
    rival = _add_class(state, "Only10", "L2", "B",
                       allowed_days=["monday"], allowed_times=["10:00"])
    return state, guard, rival


def _same_day_instance():
    """Tuesday holds exactly 4 cells and 4 tuesday-only classes want them.

    ``Same`` is one of five classes competing for 4 tuesday cells + 4 monday
    cells, so the only way to place all five is to move ``Same`` off tuesday —
    precisely what ``same_day`` forbids.
    """
    state = _grid(["monday", "tuesday"], ["09:00", "10:00"], ["R001", "R002"])
    same = _add_class(state, "Same", "L1", "A", protection=PROTECTION_SAME_DAY)
    mark_placed(same, "tuesday", "10:00", "R001")
    for i, branch in enumerate("BCDE"):
        _add_class(state, f"Tue{i}", f"L{i + 2}", branch,
                   allowed_days=["tuesday"])
    return state, same


# ===========================================================================
# 0. THE MODULE MUST NOT SILENTLY EVAPORATE
# ===========================================================================
def test_this_module_cannot_silently_skip_itself():
    """Guards ST-SCHED-005 / ST-SCHED-006 against a green run of zero tests.

    Every other test here is skipped when ortools is missing. This one never is.
    A failure means nobody is checking CP-SAT's hard-constraint semantics —
    either the dependency vanished from the environment, or someone disabled the
    file wholesale — and the two High findings above are unguarded.
    """
    assert HAS_ORTOOLS is True, (
        "ortools is NOT installed in this interpreter, so every CP-SAT test in "
        "tests/test_cpsat_semantics.py is being skipped and ST-SCHED-005 / "
        "ST-SCHED-006 are completely unguarded. ortools is a hard dependency "
        "(requirements.txt:15, requirements-lock.txt:18) — install it rather "
        "than accepting a silently green module.")

    # ...and nobody may re-arm the skip unconditionally.
    module_marks = [m.name for m in globals().get("pytestmark", [])]
    assert "skip" not in module_marks and "skipif" not in module_marks, (
        f"a module-level skip was added to this file: {module_marks}")

    hard_skipped = []
    for name, obj in sorted(globals().items()):
        if not name.startswith("test_") or not callable(obj):
            continue
        for mark in getattr(obj, "pytestmark", []):
            if mark.name == "skip":
                hard_skipped.append(name)
            elif mark.name == "skipif" and mark.args and mark.args[0] is True:
                hard_skipped.append(name)
    assert not hard_skipped, (
        "these CP-SAT tests are unconditionally skipped, which makes this "
        f"module report success while checking nothing: {hard_skipped}")


# ===========================================================================
# 1. ST-SCHED-005 — AVAILABILITY ACROSS THE WHOLE DURATION
# ===========================================================================
@pytest.mark.engine
@requires_ortools
def test_cpsat_never_spans_an_hour_the_lecturer_is_unavailable_for():
    """Pins ST-SCHED-005 (High).

    A failure means DERSİS put a three-hour lesson on a teacher's timetable
    across two hours they had explicitly marked themselves unavailable for —
    hours the app itself will refuse to commit a moment later.

    The lecturer here is free at 09:00 and again from 13:00 to 15:00. 13:00 is a
    legal three-hour window; 09:00 is not, because 10:00 and 11:00 are barred.
    CP-SAT's objective prefers the earliest start, so it takes 09:00.
    """
    from scheduler_app.core.cpsat_scheduler import CPSATScheduler

    state = _grid(["monday"], [f"{h:02d}:00" for h in range(9, 17)], ["R001"])
    _set_availability(state, "L1", ["09:00", "13:00", "14:00", "15:00"])
    big = _add_class(state, "BIG3H", "L1", "A", duration=3)

    scheduler = CPSATScheduler(state, time_limit=CPSAT_TIME_LIMIT,
                               protected_ids=set(), seed=SEED)
    placed, _unplaced, info = scheduler.solve()

    # Trap B, half one: the fixture must be solvable, or "no violation" would
    # just mean "no model". A legal 3-hour window demonstrably exists.
    assert info["status"] in ("OPTIMAL", "FEASIBLE"), f"solver bailed: {info}"
    assert placed is not None, f"solver returned no solution at all: {info}"

    audit = check_schedule(state, placements=placed)
    spans = {c["name"]: _occupied_hours(state, s, c["duration"])
             for c, _d, s, _r in placed}
    assert hard_violation_count(audit) == 0, (
        "CP-SAT placed a class across hours its lecturer is unavailable for.\n"
        f"BIG3H (duration {big['duration']}) occupies {spans.get('BIG3H')}, "
        f"lecturer L1 is available at "
        f"{state['lecturer_availability']['L1']['allowed_hours']}.\n"
        + format_violations(audit))


@pytest.mark.engine
@requires_ortools
def test_cpsat_places_a_multi_hour_class_inside_its_available_window():
    """Regression guard for the ST-SCHED-005 fix (currently PASSES).

    Trap B. The pin above is also satisfied by a solver that gives up and places
    nothing, and "a guard that turned a wrong answer into a missing answer" is
    the failure mode Phase 1 and 2 kept hitting. So: a lecturer free from 13:00
    to 15:00 has exactly one legal three-hour window, and CP-SAT must find it.

    A failure means a teacher who narrowed their availability lost the lesson
    entirely instead of having it moved into the hours they *are* free.
    """
    from scheduler_app.core.cpsat_scheduler import CPSATScheduler

    state = _grid(["monday"], [f"{h:02d}:00" for h in range(9, 17)], ["R001"])
    _set_availability(state, "L1", ["13:00", "14:00", "15:00"])
    big = _add_class(state, "BIG3H", "L1", "A", duration=3)

    scheduler = CPSATScheduler(state, time_limit=CPSAT_TIME_LIMIT,
                               protected_ids=set(), seed=SEED)
    placed, unplaced, info = scheduler.solve()

    assert info["status"] in ("OPTIMAL", "FEASIBLE"), f"solver bailed: {info}"
    got = [(d, s, r) for c, d, s, r in (placed or []) if c is big]
    assert got, (
        "CP-SAT left a placeable 3-hour class unplaced; its lecturer is free "
        "for exactly one 3-hour window (13:00-15:00) on an otherwise empty "
        f"grid. unplaced={[(c['name'], r) for c, r in (unplaced or [])]}, "
        f"info={info}")

    day, slot, _room = got[0]
    hours = _occupied_hours(state, slot, big["duration"])
    allowed = state["lecturer_availability"]["L1"]["allowed_hours"]
    assert set(hours) <= set(allowed), (
        f"placed at {day}/{slot} covering {hours}, outside the lecturer's "
        f"available hours {allowed}")

    audit = check_schedule(state, placements=placed)
    assert hard_violation_count(audit) == 0, format_violations(audit)


# ===========================================================================
# 2. ST-SCHED-006 — EVERY PROTECTION LEVEL
# ===========================================================================
@pytest.mark.engine
@requires_ortools
def test_cpsat_reschedule_honors_locked_protection():
    """Regression guard for ST-SCHED-006's one working level (PASSES today).

    ``protection='locked'`` is the user saying "never touch this one", and it is
    the only level ``CPSATScheduler.solve`` currently understands
    (cpsat_scheduler.py::CPSATScheduler.solve). A failure means a deep-optimization run
    relocated a lesson the user had frozen.

    Kept alongside the three failing levels on purpose: it is what proves the
    fixture actually applies pressure — the identical instance with
    ``protection='soft'`` *does* move (next test), so a "nothing ever moves
    here" explanation is ruled out.
    """
    state, guard, rival = _protection_instance(PROTECTION_LOCKED)
    before = _pos(guard)

    placed, _unplaced, _changes, summary = _run_optimizer(state)
    _assert_cpsat_really_ran(summary, placed)

    got = {c["name"]: (d, s, r) for c, d, s, r in placed}
    assert got.get("Guard") == before, (
        f"a protection='locked' class moved from {before} to "
        f"{got.get('Guard')} during a use_cpsat=True reschedule")


@pytest.mark.engine
@requires_ortools
def test_cpsat_reschedule_honors_soft_protection():
    """Pins ST-SCHED-006 (High) — ``soft``.

    ``optimize()`` treats a placed ``PROTECTION_SOFT`` class as immovable
    (``schedule_optimizer.py::optimize`` puts it in ``protected``), and the heuristic
    engine honours that. Turning on deep optimization silently drops the
    promise: a lesson the user asked to leave alone is relocated.

    Same instance as the ``locked`` test, one field different.
    """
    state, guard, rival = _protection_instance(PROTECTION_SOFT)
    before = _pos(guard)

    placed, _unplaced, _changes, summary = _run_optimizer(state)
    _assert_cpsat_really_ran(summary, placed)

    got = {c["name"]: (d, s, r) for c, d, s, r in placed}
    assert got.get("Guard") == before, (
        f"a protection='soft' class moved from {before} to {got.get('Guard')} "
        "during a use_cpsat=True reschedule; the heuristic engine keeps it put, "
        "so enabling deep optimization is what breaks the promise")


@pytest.mark.engine
@requires_ortools
def test_cpsat_reschedule_honors_same_day_protection():
    """Pins ST-SCHED-006 (High) — ``same_day``.

    ``protection='same_day'`` means "move it within its day if you must, but
    never off that day" (models.py). The heuristic honours it through
    ``same_day_map`` (schedule_optimizer.py::optimize); CP-SAT has no such notion,
    so a lesson a school had fixed to Tuesday reappears on Monday.

    Moving it is the *only* way to place all five classes here, so the wrong
    answer is what wins optimize()'s acceptance gate (Trap A). Leaving ``Same``
    on tuesday — even at the cost of one unplaced class — is the correct answer,
    and this test accepts it wherever on tuesday it lands, or unplaced.
    """
    state, same = _same_day_instance()
    before_day = same["placed_day"]

    placed, _unplaced, _changes, summary = _run_optimizer(state)
    _assert_cpsat_really_ran(summary, placed)

    got = {c["name"]: (d, s, r) for c, d, s, r in placed}
    landed = got.get("Same")
    assert landed is None or landed[0] == before_day, (
        f"a protection='same_day' class left {before_day!r}: it is now on "
        f"{landed[0]!r} ({landed}). Same-day protection permits a different "
        "hour or room, never a different day.")


@pytest.mark.engine
@requires_ortools
def test_cpsat_reschedule_honors_improve_only_protection():
    """Pins ST-SCHED-006 (High) — ``improve_only``.

    ``protection='improve_only'`` means "move it only to somewhere at least as
    good" (models.py). "As good" is measured with the very function that
    builds the production baseline, ``TimetableScorer.placement_score``
    (schedule_optimizer.py), where a lower score is better; the greedy engine
    enforces ``score <= baseline`` inside ``schedule_optimizer.py::optimize``.

    A failure means the app took a lesson sitting in a good mid-morning slot and
    demoted it to a worse hour to suit some other class, which is exactly the
    trade the user forbade.
    """
    from scheduler_app.core.timetable_scorer import TimetableScorer

    state, guard, rival = _protection_instance(PROTECTION_IMPROVE_ONLY)
    scorer = TimetableScorer(state)
    before = _pos(guard)
    baseline = scorer.placement_score(guard, *before)

    # Fixture sanity: the pressure has to be real. Every alternative hour must
    # be strictly worse, otherwise "did not get worse" is satisfiable by moving.
    alternatives = {s: scorer.placement_score(guard, "monday", s, "R001")
                    for s in state["slots"] if s != before[1]}
    assert all(v > baseline for v in alternatives.values()), (
        f"fixture is toothless: baseline {baseline} at {before[1]}, "
        f"alternatives {alternatives} — one of them is not worse")

    placed, _unplaced, _changes, summary = _run_optimizer(state)
    _assert_cpsat_really_ran(summary, placed)

    got = {c["name"]: (d, s, r) for c, d, s, r in placed}
    landed = got.get("Guard")
    if landed is None or landed == before:
        return  # stayed put (or was not placed) — protection respected
    after = scorer.placement_score(guard, *landed)
    assert after <= baseline, (
        f"a protection='improve_only' class was moved from {before} "
        f"(score {baseline}) to {landed} (score {after}); lower is better, so "
        "this move made the placement worse — the one thing the level forbids")


# ===========================================================================
# 3. ST-SCHED-006 — A MOVE NOBODY CAN SEE
# ===========================================================================
@pytest.mark.engine
@requires_ortools
def test_every_move_cpsat_makes_is_reported_in_changes():
    """Pins ST-SCHED-006 (High) — the invisible-move half.

    ``result.changes`` is the app's only record of what a reschedule did: the
    impact panel reads it, the status bar counts it ("n classes moved"), the
    feedback logger learns from it and rollback replays it. A move that is
    missing from it is a change to the user's timetable that no part of the app
    can see, explain or undo.

    ``optimize()`` skips protected ids when building ``changes[]`` — sound while
    protected classes really are immovable, and exactly wrong once CP-SAT starts
    moving them anyway. Measured: the ``soft`` class below is relocated from
    monday/10:00 to monday/09:00 while ``result.changes`` names only ``Only10``
    and ``summary['classes_moved']`` counts 1 — the move that touched an
    already-scheduled lesson is the one that is missing.

    Stated as a general invariant rather than a fact about this one class, so
    that once ST-SCHED-006 is fixed it keeps earning its place: whatever moves
    must be reported.
    """
    state, guard, rival = _protection_instance(PROTECTION_SOFT)
    before = {c["name"]: _pos(c) for c in state["classes"] if c["placed"]}

    placed, _unplaced, changes, summary = _run_optimizer(state)
    _assert_cpsat_really_ran(summary, placed)

    reported = {ch["cls"]["name"] for ch in changes}
    moved_silently = []
    for cls, day, slot, room in placed:
        was = before.get(cls["name"])
        if was is None or was == (day, slot, room):
            continue
        if cls["name"] not in reported:
            moved_silently.append((cls["name"], was, (day, slot, room)))

    assert not moved_silently, (
        "the optimizer moved classes and left them out of result.changes, so "
        "the impact panel, the 'n classes moved' status line, the feedback log "
        "and undo all believe nothing happened: "
        + "; ".join(f"{n}: {w} -> {g}" for n, w, g in moved_silently)
        + f"\nreported changes: {sorted(reported)}, "
        f"summary['classes_moved']={summary['classes_moved']}")


# ===========================================================================
# 4. ST-SCHED-005 — THE COMMIT STEP THROWS IT AWAY (end to end)
# ===========================================================================
@pytest.mark.engine
@requires_ortools
def test_cpsat_reschedule_commits_every_class_it_reports_as_placed():
    """Pins ST-SCHED-005 (High) — the data-loss half, through the real workflow.

    The only test here that runs the full public path
    (``SchedulingWorkflow.reschedule(use_cpsat=True)`` then
    ``apply_reschedule``), because losing a lesson is only observable after the
    commit. From the user's side: they press "optimize", the dialog reports the
    class as scheduled, and it is not on the timetable afterwards — no message,
    because ``ui/app.py::_on_solve_finished`` throws ``apply_reschedule``'s
    rejected list away.

    The lecturer is free at 09:00 only, and the class needs three hours, so the
    heuristic engine correctly leaves it unplaced. CP-SAT "places" it at 09:00
    and thereby beats the heuristic on placed-count, which is what pushes the
    invalid proposal through the acceptance gate (Trap A) — with a lecturer who
    had a second, legal window the gate quietly repairs the bug and this test
    would pass while proving nothing.

    Encoded as "nothing the result reported as placed may be rejected at
    commit". A fix that teaches CP-SAT the real availability rule satisfies it
    by never proposing the placement; ``BIG3H`` then legitimately shows up in
    ``result.unplaced``, which the UI *does* report.
    """
    from scheduler_app.core.workflow import SchedulingWorkflow

    state = _grid(["monday"], [f"{h:02d}:00" for h in range(9, 17)],
                  ["R001", "R002"])
    _set_availability(state, "L1", ["09:00"])
    big = _add_class(state, "BIG3H", "L1", "A", duration=3)
    fillers = [_add_class(state, f"Fill{i}", f"L{i + 2}", "BCDE"[i])
               for i in range(3)]

    workflow = SchedulingWorkflow(state, lambda: {})
    result = workflow.reschedule(
        {}, use_cpsat=True, seed=SEED, cpsat_time_limit=CPSAT_TIME_LIMIT,
        multi_start_runs=1, lns_iterations=20, parallel_workers=-1)
    _assert_cpsat_really_ran(result.summary, result.placed)

    proposed = {c["name"]: (d, s, r) for c, d, s, r in result.placed}
    rejected = workflow.apply_reschedule(result)

    # Audit the timetable that was actually written, not merely the fact that
    # nothing was refused. `rejected == []` alone is satisfied just as well by
    # deleting apply_reschedule's re-validation — which would stop dropping the
    # invalid placement and start committing it, making the user's data
    # strictly worse. The oracle is independent of the production validator, so
    # it cannot be fooled by the same bug twice.
    committed_audit = check_schedule(state)
    assert hard_violation_count(committed_audit) == 0, (
        "CP-SAT's result was committed with hard-constraint violations in it:\n"
        + format_violations(committed_audit))

    # Trap B: "nothing was rejected" is free on an empty proposal. The three
    # unconstrained filler classes must have been proposed and committed.
    assert all(f["name"] in proposed for f in fillers), (
        f"degenerate run: the optimizer proposed only {sorted(proposed)}")
    assert all(f["placed"] for f in fillers), (
        "degenerate run: the filler classes did not survive the commit, so "
        "'nothing was dropped' would be vacuous")

    committed = {c["name"] for c in state["classes"] if c["placed"]}
    assert rejected == [], (
        f"apply_reschedule rejected {rejected} — classes the reschedule had "
        f"already reported as placed at {[(n, proposed[n]) for n in rejected]}. "
        "The UI discards this list, so from the user's side those lessons "
        f"vanished without a word. Committed: {sorted(committed)}; "
        f"result.unplaced={[c['name'] for c, _ in result.unplaced]}")
