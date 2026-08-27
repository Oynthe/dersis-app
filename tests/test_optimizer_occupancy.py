"""Occupancy bookkeeping inside the optimizer (ST-SCHED-001, ST-SCHED-010).

``tests/test_scheduler_invariants.py`` pins the *symptom*: the raw optimizer
proposal for ``small``/``normal`` contains hard-constraint violations. This
module pins the *mechanism*, one layer down, at the seam where the corruption is
introduced — and it does so on instances small enough to run in CI's non-slow
lane, which the spine's ST-SCHED-001 pins (all ``slow``) do not.

What is actually broken
-----------------------
**ST-SCHED-001.** ``ScheduleOptimizer._greedy_construct``
(``scheduler_app/core/schedule_optimizer.py:636-757``) records its answer as a
*snapshot*: ``solve()`` copies ``solution`` into ``best_solution`` at a leaf and
then keeps searching. There are two exits.

* Every class placed → ``solve`` returns True, every frame returns True without
  running its matching ``_remove``, and the occupancy maps still describe the
  answer. This is why ``tiny`` has always been clean.
* Partial best, or the 100 000-iteration budget runs out → every frame falls
  through to ``_remove`` and the stack unwinds completely. The occupancy maps go
  back to the baseline while ``best_solution`` still claims a full set of
  placements.

In the second case ``_greedy_construct`` hands its caller a solution and a
validator that disagree about *every* cell in it, and ``_lns_improve`` then runs
its whole repair loop against that near-empty grid. Measured on this module's
6-class over-subscribed micro instance, against unmodified HEAD:

    greedy 4/6 placed, 0 violations, 4 of 4 placements unknown to the validator
    LNS out 4 placed, 2-4 violations   (room_double_book, group_clash)

and on the 25-class ``small`` preset:

    greedy 20/24 placed, 0 violations, 20 of 20 placements unknown
    LNS out 20 placed, 14-18 violations on 4 of the 5 multi-start runs

Note where the violations come from: the greedy's own answer is sound in every
single run. **LNS introduces all of them**, because it is validating against a
map that was emptied behind its back. That attribution is what
``test_lns_never_introduces_a_conflict_the_greedy_did_not_have`` pins, and it is
the thing the spine's oracle-level pins cannot tell you.

**ST-SCHED-010.** ``ConstraintValidator``'s occupancy cells
(``scheduler_app/core/constraint_validator.py:36-38``) are ref-count-free
``set``s, so two classes contributing the same claim to one cell collapse into
one entry — and ``remove_placement`` (``:283-298``) uses ``discard``, so
removing *either* of them erases the claim of the other. Two classes pinned to
the same room/day/hour is an ordinary state (the user types it in; the spine's
``test_colliding_pins_are_not_silently_committed`` covers it, and
``workflow.apply_reschedule`` deliberately registers an infeasible pin anyway —
``workflow.py:533-536``). Confirmed against HEAD:
``check_placement_explained(PinA, monday, 09:00, R001)`` returns ``(True, [])``
while PinB sits in exactly that room, because the temporary self-removal at
``constraint_validator.py:170-177`` took PinB's claim down with it.

Traps this module defends against
---------------------------------
**Trap 1 — "clean because empty" at the seam.** ``desync == 0`` is trivially
true of a solution with no placements. Verified empirically: stubbing
``_greedy_construct`` to return ``[None] * n`` gives ``(placed, desync) =
(0, 0)`` on all five multi-start runs and satisfies every white-box assertion
here. Each seam test therefore carries a per-run floor on ``greedy_placed``.

**Trap 2 — "failing for the wrong reason".** The seam invariant only bites on
the branch where the greedy does *not* fully succeed. If the greedy ever started
placing everything, the pin would go green without the defect being fixed. The
falls-short tests therefore also assert ``0 < greedy_placed < n``, i.e. that the
partial-success branch was genuinely exercised.

**Trap 3 — "must be False" is satisfied by a validator that refuses
everything.** The ST-SCHED-010 pins assert that a cell stays *occupied*. A
validator broken the other way (blanket refusal) would satisfy that. The control
test ``test_two_pins_on_one_cell_are_both_visible_to_the_validator`` — which is
deliberately *not* under an xfail marker, so it cannot be absorbed by one —
holds the other end: the same validator must still say Yes to a free cell.

**Trap 4 — the obvious ST-SCHED-010 fix breaks locked classes.** Turning the
cells into ``{entity: refcount}`` and nothing else makes
``schedule_optimizer.py:387-397`` a leak: ``exclude_ids`` there covers only
``all_flexible``, so a ``locked``/``protected`` class is claimed once by
``build_occupancy`` (it is ``placed``) and a second time by the explicit
``validator.add_placement`` loop. With sets that collapsed harmlessly; with
ref-counts its cell needs two releases and nothing ever issues the second, so
the cell is occupied forever. ``test_optimizer_validator_holds_one_claim_per_
locked_class`` passes today and is the guard for that regression — it is the one
test here that must stay green through the fix, not flip.

Representation independence
---------------------------
``_norm()`` reads a ``set`` cell and a ``{entity: refcount}`` cell identically,
so every assertion below is about *behaviour*, not about which container
``ConstraintValidator`` happens to use. Nothing here has to be rewritten when
ST-SCHED-010 lands.

Deleting the pins is part of the fix
------------------------------------
The six ``xfail(strict=True)`` markers below are measured against commit
``e286a25`` (Phase 2 merge), the state of ``scheduler_app/`` before the Phase 3
work. They are **not** deferrals to a later phase — they are the
red-when-fixed signal ``tests/README.md`` describes, and ST-SCHED-001/010 are
the current phase's work. Verified against an in-progress Phase 3 working tree
(ref-counted occupancy cells + a resynchronisation pass at the end of
``_greedy_construct`` + ``locked``/``protected`` added to the optimizer's
``exclude_ids``): all six flip to ``XPASS(strict)`` and the four unpinned tests
stay green. So a red run here after the fix lands is the expected transition,
and the correct response is to delete the six decorators in the same commit —
along with the ST-SCHED-001 markers in ``test_scheduler_invariants.py``, which
go red at the same moment for the same reason.

Runtime (measured, .venv-audit, this machine)
---------------------------------------------
``pytest tests/test_optimizer_occupancy.py -m "not slow"``   ~3-5 s
``pytest tests/test_optimizer_occupancy.py``                 ~35-50 s

The whole non-slow half is optimizer time on instances of 2-6 classes plus one
``tiny`` (5-class) run; the slow half is the single 25-class ``small`` reschedule
(~28 s at HEAD, ~42 s once the fix lands and the search stops being wasted).
Everything is deterministic: ``seed`` defaults to ``DEFAULT_OPTIMIZER_SEED`` and
``deterministic_budget=True``, CP-SAT is never enabled, and every measurement
above reproduced identically across repeated invocations.
"""
import math

import pytest

from _support.dataset_gen import make_preset
from _support.schedule_oracle import (
    check_schedule,
    format_violations,
    hard_violation_count,
)

# Anti-vacuity floor for the `small` seam tests (Trap 1). Deliberately far below
# the measured 20/24 so it detects degeneracy, not quality.
_MIN_PLACED_FRACTION = 0.5

_T1 = {"year": "Year-1", "branch": "A"}
_T2 = {"year": "Year-1", "branch": "B"}


# ---------------------------------------------------------------------------
# Hand-built micro-fixtures (modelled on _mini_state() in the spine module)
# ---------------------------------------------------------------------------
def _micro_state(days, slots, rooms):
    """A grid small enough to reason about by hand.

    Capacities are 0 ("unknown") so the oracle's soft capacity rule stays quiet
    and these fixtures isolate exactly the invariant under test.
    """
    from scheduler_app.core.models import new_state

    state = new_state()
    state["days"] = list(days)
    state["slots"] = list(slots)
    state["classrooms"] = list(rooms)
    state["classroom_capacities"] = {r: 0 for r in rooms}
    state["lecturers"] = ["Lect-A", "Lect-B", "Lect-C"]
    state["years"] = {"Year-1": ["A", "B"]}
    return state


def _mk_class(name, lecturer, target, duration=1):
    from scheduler_app.core.models import new_class

    cls = new_class()
    cls["class_code"] = name
    cls["name"] = name
    cls["lecturer"] = lecturer
    cls["targets"] = [dict(target)]
    cls["duration"] = duration
    cls["participants"] = 0
    return cls


def _oversubscribed_state():
    """4 cells (2 days x 2 hours x 1 room), 6 one-hour classes.

    The smallest instance found that drives ``_greedy_construct`` down its
    partial-success exit — ``solve(0)`` returns False, the stack unwinds, the
    occupancy maps empty out — while still leaving LNS the >= 3 placements it
    needs to run at all. Measured: 1855 greedy iterations, ~0.3 s per
    reschedule, identical on every invocation.
    """
    state = _micro_state(["monday", "tuesday"], ["09:00", "10:00"], ["R001"])
    state["classes"] = [
        _mk_class(f"Ders {i}", ["Lect-A", "Lect-B", "Lect-C"][i % 3],
                  _T1 if i % 2 else _T2)
        for i in range(6)
    ]
    return state


def _two_pins_on_one_cell():
    """A state a user can build by hand: two classes pinned to one room-hour.

    Distinct lecturers and distinct student groups, so the *room* is the only
    thing the two pins share — which makes the room claim the sole discriminator
    in the ST-SCHED-010 assertions below.

    ``Flex`` deliberately borrows PinA's lecturer-free identity dimensions
    (unused lecturer ``Lect-C``, PinA's target ``Year-1/A``): once PinA is
    removed from the maps, the only thing that may still refuse ``Flex`` at
    monday/09:00/R001 is PinB's room claim.
    """
    state = _micro_state(["monday", "tuesday"], ["09:00", "10:00"],
                         ["R001", "R002"])
    pin_a = _mk_class("PinA", "Lect-A", _T1)
    pin_b = _mk_class("PinB", "Lect-B", _T2)
    for cls in (pin_a, pin_b):
        cls["pinned"] = True
        cls["pinned_day"] = "monday"
        cls["pinned_time"] = "09:00"
        cls["pinned_classroom"] = "R001"
    flex = _mk_class("Flex", "Lect-C", _T1)
    state["classes"] = [pin_a, pin_b, flex]
    return state, pin_a, pin_b, flex


# ---------------------------------------------------------------------------
# Occupancy helpers — representation-agnostic
# ---------------------------------------------------------------------------
def _norm(occ):
    """``{(day, slot): {entity, ...}}`` from either cell representation.

    A ``set`` cell and a ``{entity: refcount}`` cell both yield their entities,
    so nothing in this module cares which one ST-SCHED-010 settles on. Empty
    cells are dropped: ``set.discard`` leaves ``set()`` behind, and an empty
    cell means "free" either way.
    """
    return {key: set(cell) for key, cell in occ.items() if cell}


def _occupancy_diff(got, want):
    """Cells where two validators' occupancy maps disagree."""
    diffs = []
    for name in ("room_occ", "lect_occ", "group_occ"):
        g = _norm(getattr(got, name))
        w = _norm(getattr(want, name))
        for cell in sorted(set(g) | set(w), key=lambda c: (str(c[0]), str(c[1]))):
            if g.get(cell, set()) != w.get(cell, set()):
                diffs.append((name, cell,
                              sorted(str(x) for x in g.get(cell, ())),
                              sorted(str(x) for x in w.get(cell, ()))))
    return diffs


def _format_diff(diffs, limit=6):
    if not diffs:
        return "  (none)"
    lines = [f"  {name} at {cell}: validator has {got}, solution needs {want}"
             for name, cell, got, want in diffs[:limit]]
    if len(diffs) > limit:
        lines.append(f"  ... and {len(diffs) - limit} more cells")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The seam: capture what _greedy_construct and _lns_improve each produced
# ---------------------------------------------------------------------------
def _capture_phases(state, **reschedule_kwargs):
    """Run the production reschedule, recording both optimizer phases per run.

    Wraps ``ScheduleOptimizer._greedy_construct`` and ``._lns_improve`` — no
    production behaviour is altered; the wrappers call through and only read.
    Returns ``(runs, result)`` where ``runs`` has one dict per multi-start run:

    ``greedy_placed`` / ``n``     how much the greedy phase placed, out of how many
    ``greedy_hard``               oracle hard-violation count of the greedy answer
    ``desync``                    returned placements the validator still calls FREE
    ``forgotten``                 a sample of those, for the failure message
    ``map_diff``                  every cell where occupancy != the returned solution
    ``budget_exhausted``          whether the greedy hit ``max_iterations``
    ``lns_placed`` / ``lns_hard`` the same, for what LNS handed back

    Both audits include the immovable baseline (pinned / locked / protected), so
    a greedy or LNS placement that collides with a *frozen* class is caught too,
    not just collisions among the flexible ones.
    """
    from scheduler_app.core.constraint_validator import ConstraintValidator
    from scheduler_app.core.models import (
        cls_key, effective_day, effective_room, effective_time)
    from scheduler_app.core.schedule_optimizer import ScheduleOptimizer
    from scheduler_app.core.workflow import SchedulingWorkflow

    runs = []
    greedy_orig = ScheduleOptimizer._greedy_construct
    lns_orig = ScheduleOptimizer._lns_improve

    def _audit(st, flexible, solution):
        flex_ids = {cls_key(c) for c in flexible}
        placements = [
            (c, effective_day(c), effective_time(c), effective_room(c))
            for c in st["classes"]
            if cls_key(c) not in flex_ids and (c.get("placed") or c.get("pinned"))
        ]
        placements += [(flexible[i], a[0], a[1], a[2])
                       for i, a in enumerate(solution) if a is not None]
        return check_schedule(st, placements=placements)

    def _greedy(self, flexible, validator, generator, scorer, **kw):
        solution, stats = greedy_orig(
            self, flexible, validator, generator, scorer, **kw)

        # (a) Which returned placements does the occupancy map still call free?
        #     `check_placement` never excludes the class itself, so a placement
        #     the maps know about is refused; one they have no record of is
        #     accepted. `respects_constraints` cannot cause a false negative
        #     here — the greedy only ever placed candidates that already passed
        #     it, and it does not read occupancy.
        forgotten = [(flexible[i].get("name"), a)
                     for i, a in enumerate(solution)
                     if a is not None
                     and validator.check_placement(flexible[i], *a)]

        # (b) The same question in both directions: rebuild, from scratch, the
        #     occupancy the returned solution implies and diff it. Catches an
        #     over-claim (a cell held for a placement that was withdrawn) as
        #     well as the under-claim above.
        expected = ConstraintValidator(
            self.state, exclude_ids={cls_key(c) for c in flexible})
        for i, a in enumerate(solution):
            if a is not None:
                expected.add_placement(flexible[i], a[0], a[1], a[2])

        audit = _audit(self.state, flexible, solution)
        runs.append({
            "n": len(flexible),
            "greedy_placed": sum(1 for a in solution if a is not None),
            "greedy_hard": hard_violation_count(audit),
            "greedy_audit": audit,
            "desync": len(forgotten),
            "forgotten": forgotten[:5],
            "map_diff": _occupancy_diff(validator, expected),
            "budget_exhausted": stats["budget_exhausted"],
            "lns_placed": None,
            "lns_hard": None,
            "lns_audit": None,
        })
        return solution, stats

    def _lns(self, flexible, solution, validator, generator, scorer,
             tt_scorer, **kw):
        out, stats = lns_orig(self, flexible, solution, validator, generator,
                              scorer, tt_scorer, **kw)
        if runs:
            audit = _audit(self.state, flexible, out)
            runs[-1]["lns_placed"] = sum(1 for a in out if a is not None)
            runs[-1]["lns_hard"] = hard_violation_count(audit)
            runs[-1]["lns_audit"] = audit
        return out, stats

    ScheduleOptimizer._greedy_construct = _greedy
    ScheduleOptimizer._lns_improve = _lns
    try:
        wf = SchedulingWorkflow(state, lambda: {})
        result = wf.reschedule({}, use_cpsat=False, **reschedule_kwargs)
    finally:
        ScheduleOptimizer._greedy_construct = greedy_orig
        ScheduleOptimizer._lns_improve = lns_orig

    assert runs, ("the optimizer never called _greedy_construct — this module's "
                  "whole instrument is a no-op, so nothing below proved anything")
    return runs, result


def _assert_seam_floor(runs, floor, *, expect_partial):
    """Trap 1 + Trap 2: the run must be neither degenerate nor the easy branch.

    Kept out of the assertions it guards on purpose. When it fails the message
    says the *instrument* is broken, not the invariant.
    """
    for i, run in enumerate(runs):
        assert run["greedy_placed"] >= floor, (
            f"DEGENERATE GREEDY RUN #{i}: {run['greedy_placed']} of {run['n']} "
            f"classes placed (floor {floor}). Every occupancy assertion in this "
            "module is vacuous on a run this empty — a greedy stubbed to return "
            "[None] * n satisfies all of them.")
        if expect_partial:
            assert run["greedy_placed"] < run["n"], (
                f"RUN #{i} placed all {run['n']} classes, so _greedy_construct "
                "took its full-success exit and never unwound the stack. That is "
                "the branch that was ALREADY correct; this instance is supposed "
                "to exercise the partial-success one, so the pin would pass for "
                "the wrong reason.")


def _seam_failure_message(runs, label):
    lines = [
        f"_greedy_construct returned a solution its own ConstraintValidator has "
        f"no record of, on `{label}`.",
        "That is ST-SCHED-001 at its source: _lns_improve is handed this same "
        "validator and repairs against a grid it believes is empty, so it stacks "
        "classes on top of each other.",
    ]
    for i, run in enumerate(runs):
        if not run["desync"] and not run["map_diff"]:
            continue
        lines.append(
            f"-- run {i}: {run['desync']} of {run['greedy_placed']} returned "
            f"placements are still considered FREE "
            f"(budget_exhausted={run['budget_exhausted']}); "
            f"{len(run['map_diff'])} occupancy cells wrong")
        for name, placement in run["forgotten"][:3]:
            lines.append(f"     forgotten: {name} @ {placement}")
        lines.append(_format_diff(run["map_diff"], limit=4))
    return "\n".join(lines)


# ===========================================================================
# 1. THE SEAM — occupancy must describe exactly the solution that was returned
# ===========================================================================
@pytest.mark.engine
def test_greedy_occupancy_matches_its_answer_when_the_greedy_succeeds():
    """Guards ST-SCHED-001's already-correct branch (currently PASSES).

    This is the control for the pin below it. On a 5-class instance the greedy
    places everything, ``solve`` returns True all the way up and never runs the
    matching ``_remove`` calls, so the occupancy maps do describe the answer.
    Both assertions therefore have to be green *today* — if they are not, the
    instrument is broken and the failing-case pin next to it means nothing.

    A user-visible failure here would mean DERSİS started corrupting even the
    trivial timetables it has always got right.
    """
    runs, _result = _capture_phases(make_preset("tiny", seed=42))

    for i, run in enumerate(runs):
        assert run["greedy_placed"] == run["n"] == 5, (
            f"run {i}: the greedy did not place all 5 classes of the trivial "
            f"`tiny` instance ({run['greedy_placed']}/{run['n']}), so it took "
            "the partial-success exit and this control proves nothing")

    assert all(r["desync"] == 0 for r in runs), _seam_failure_message(runs, "tiny")
    assert all(not r["map_diff"] for r in runs), (
        "occupancy does not match the returned solution on `tiny`:\n"
        + "\n".join(_format_diff(r["map_diff"]) for r in runs if r["map_diff"]))


@pytest.mark.engine
def test_greedy_occupancy_matches_its_answer_when_the_greedy_falls_short():
    """Pins ST-SCHED-001 (Critical) at its source, in the non-slow lane.

    This is the highest-value assertion in the module: it catches the defect at
    the exact seam where it is introduced rather than three layers downstream in
    the committed timetable. A failure means every later phase of the optimizer
    is reasoning about a timetable grid that does not exist, which is how a
    school ends up with two lessons booked into one room.

    Measured against unmodified HEAD: 4 of 4 returned placements unknown to the
    validator, and 12 occupancy cells wrong, on all five multi-start runs.
    """
    runs, _result = _capture_phases(_oversubscribed_state())
    _assert_seam_floor(runs, floor=1, expect_partial=True)

    assert all(r["desync"] == 0 for r in runs), _seam_failure_message(
        runs, "6 classes / 4 cells")
    assert all(not r["map_diff"] for r in runs), (
        "the greedy's occupancy maps do not match the solution it returned:\n"
        + "\n".join(_format_diff(r["map_diff"]) for r in runs if r["map_diff"]))


@pytest.mark.engine
def test_greedy_answer_is_internally_conflict_free():
    """Regression guard for ST-SCHED-001 (currently PASSES — read the docstring).

    The greedy's *answer* has always been sound: ``best_solution`` is snapshotted
    at a leaf, while the occupancy maps were still consistent, so it never
    contains a double-booking. Measured 0 violations on every multi-start run of
    both instances, on unmodified HEAD.

    Keeping this green is what makes the attribution test below meaningful: the
    conflicts in the raw proposal are not something the greedy produced, so they
    have to have been introduced afterwards. A failure here would mean the defect
    has spread into the construction phase itself and the fix cannot simply
    resynchronise the maps with ``best_solution`` — that snapshot would no longer
    be trustworthy either.
    """
    for label, state, floor in (
            ("tiny", make_preset("tiny", seed=42), 5),
            ("6 classes / 4 cells", _oversubscribed_state(), 1)):
        runs, _result = _capture_phases(state)
        _assert_seam_floor(runs, floor=floor, expect_partial=False)
        for i, run in enumerate(runs):
            assert run["greedy_hard"] == 0, (
                f"the greedy phase itself proposed an invalid schedule on "
                f"`{label}` (run {i}, {run['greedy_placed']} placed):\n"
                + format_violations(run["greedy_audit"]))


# ===========================================================================
# 2. PER-PHASE ATTRIBUTION — which phase actually breaks the schedule
# ===========================================================================
@pytest.mark.engine
def test_lns_never_introduces_a_conflict_the_greedy_did_not_have():
    """Pins ST-SCHED-001 (Critical) — the attribution the spine cannot give you.

    ``test_scheduler_invariants.py`` can only say "the raw proposal is dirty".
    This says *which phase dirtied it*: the greedy answer is conflict-free in
    every run and the LNS output is not, so the repair phase is where a valid
    schedule becomes an invalid one. For a user that is the difference between
    "the solver is bad at packing" and "the solver is confidently writing lessons
    on top of each other".

    Runs in the non-slow lane so CI carries this signal; the ``small``-scale
    version below only confirms it does not depend on the instance.

    Measured against unmodified HEAD: greedy 0 violations on 5/5 runs, LNS 2 or 4
    violations on 5/5 runs (room_double_book, group_clash).
    """
    runs, _result = _capture_phases(_oversubscribed_state())
    _assert_seam_floor(runs, floor=1, expect_partial=True)

    for i, run in enumerate(runs):
        # Anti-vacuity: LNS must actually have had a schedule to work on. It
        # returns immediately when fewer than 3 placements exist
        # (schedule_optimizer.py:770-772), and "LNS broke nothing" would then be
        # true because LNS did nothing.
        assert run["lns_hard"] is not None, (
            f"run {i}: _lns_improve was never called, so this proves nothing "
            "about the repair phase")
        assert run["lns_placed"] >= 3, (
            f"run {i}: LNS was handed only {run['lns_placed']} placements and "
            "returns without running its loop below 3 — vacuous")

    dirty = [(i, r) for i, r in enumerate(runs) if r["lns_hard"] > r["greedy_hard"]]
    assert not dirty, (
        f"{len(dirty)}/{len(runs)} multi-start runs came out of LNS with MORE "
        "hard-constraint violations than went in:\n"
        + "\n".join(
            f"-- run {i}: greedy {r['greedy_hard']} violation(s) from "
            f"{r['greedy_placed']} placements -> LNS {r['lns_hard']} from "
            f"{r['lns_placed']}\n{format_violations(r['lns_audit'], limit=4)}"
            for i, r in dirty))


# ===========================================================================
# 3. ST-SCHED-010 — a shared occupancy claim must not be single-owner
# ===========================================================================
def test_two_pins_on_one_cell_are_both_visible_to_the_validator():
    """Guards this module's ST-SCHED-010 fixtures (no finding ID).

    Trap 3: the two pins below assert that a cell stays *occupied*. A validator
    that had regressed into refusing everything would satisfy that without
    tracking anything. This test — deliberately not under an xfail marker, so it
    cannot be absorbed by one — holds the other end of the invariant: the same
    validator must still say Yes to a genuinely free cell, and must say No to
    the contested one before anything is removed.
    """
    from scheduler_app.core.constraint_validator import ConstraintValidator

    state, _pin_a, _pin_b, flex = _two_pins_on_one_cell()
    validator = ConstraintValidator(state)

    assert validator.check_placement(flex, "monday", "09:00", "R001") is False, (
        "the validator does not even see the two pins that occupy "
        "monday/09:00/R001, so every ST-SCHED-010 assertion here is vacuous")
    assert validator.check_placement(flex, "monday", "10:00", "R002") is True, (
        "the validator refuses a completely free cell — it is not tracking "
        "occupancy, it is just saying No, and the pins below would pass for "
        "that reason alone")


def test_removing_one_pin_leaves_the_other_pins_room_occupied():
    """Pins ST-SCHED-010 (Medium) at unit level.

    A user pins two classes to monday/09:00/R001 — an ordinary thing to do, and
    a state the app explicitly supports: ``apply_reschedule`` registers an
    infeasible pin in the occupancy map rather than clearing it
    (``workflow.py:533-536``), and the spine's
    ``test_colliding_pins_are_not_silently_committed`` covers the same shape.

    Lift *one* of them out of the maps — which ordinary production code does all
    the time, e.g. ``check_placement_explained`` temporarily removes a class's
    own placement so it cannot conflict with itself
    (``constraint_validator.py:170-177``) — and R001 reads as free, although the
    second pin is still sitting in it. A failure means DERSİS will cheerfully
    drop a third lesson into a room that already has two.
    """
    from scheduler_app.core.constraint_validator import ConstraintValidator

    state, pin_a, _pin_b, flex = _two_pins_on_one_cell()
    validator = ConstraintValidator(state)

    validator.remove_placement(pin_a, "monday", "09:00", "R001")

    # Control, on the *same* validator and after the same mutation: PinA really
    # is gone, so anything still refusing this cell is refusing it for PinB's
    # sake — which is exactly the claim under test.
    assert validator.check_placement(flex, "monday", "09:00", "R002") is True, (
        "after removing PinA the validator refuses even an empty room, so it is "
        "not the surviving pin that is being detected below")

    assert validator.check_placement(flex, "monday", "09:00", "R001") is False, (
        "PinB is still pinned to monday/09:00/R001, but removing PinA erased "
        "the room claim they shared and the validator now offers that room to a "
        "third class. Occupancy: "
        f"{_norm(validator.room_occ).get(('monday', '09:00'), set())!r}")


def test_explained_check_reports_the_room_a_second_pin_still_occupies():
    """Pins ST-SCHED-010 (Medium) through the API the UI actually asks.

    ``check_placement_explained`` is what produces the "why can't this go here?"
    reasons a user reads (``schedule_impact_analyzer.py:200``). Asked about PinA
    at PinA's own pin, it removes PinA's claims to avoid a self-conflict — and
    with set-valued cells that removal takes PinB's identical room claim with it,
    so the answer comes back ``(True, [])``: *no conflicts*. Measured on
    unmodified HEAD.

    A failure means the app tells the user two classes pinned into the same room
    at the same hour are fine.
    """
    from scheduler_app.core.constraint_validator import ConstraintValidator

    state, pin_a, _pin_b, _flex = _two_pins_on_one_cell()
    validator = ConstraintValidator(state)

    valid, reasons = validator.check_placement_explained(
        pin_a, "monday", "09:00", "R001")

    # Reasons are localized (the suite pins the UI language to Turkish), so this
    # asserts on the verdict and on there being *a* reason, never on the text.
    assert valid is False and reasons, (
        "check_placement_explained says PinA's cell is conflict-free, but PinB "
        "is pinned to that same room/day/hour: "
        f"valid={valid!r} reasons={reasons!r}")


@pytest.mark.engine
def test_greedy_holds_exactly_one_claim_per_placement_it_returns():
    """Regression guard for the ST-SCHED-001 fix — the OVER-claim half.

    Every other assertion in this module is count-blind. ``_norm`` reduces a
    cell to ``set(cell)``, and a doubly-claimed cell still refuses
    ``check_placement`` — so a cell claimed twice looks exactly like a cell
    claimed once, and the map diff cannot see it.

    That blind spot has a specific, plausible wrong fix behind it. The
    reconciliation ``_greedy_construct`` needs at its exit has to *release* the
    stale ``solution`` claim before *adding* the ``best_solution`` one. Re-adding
    without releasing looks obviously correct against set-valued cells, where it
    is idempotent — and it passes every other test here, the invariants spine
    included. Against ref-counted cells (ST-SCHED-010) it leaves a claim that
    nothing will ever release, so the optimizer silently loses that room-hour,
    that lecturer-hour and that student-group-hour for the rest of the solve.

    The probe is the same shape as the locked-class one above, but aimed at a
    FLEXIBLE placement out of the greedy's own answer — which is where the
    reconciliation runs. ``tiny`` on purpose: 5/5 placed is the full-success
    exit, the branch where occupancy is *not* unwound and a double claim would
    survive.

    A failure means one ``remove_placement`` did not free a cell that exactly
    one class occupies, i.e. the maps hold more claims than placements.
    """
    from scheduler_app.core.schedule_optimizer import ScheduleOptimizer
    from scheduler_app.core.workflow import SchedulingWorkflow
    from _support.dataset_gen import make_preset

    state = make_preset("tiny", seed=42)
    observed = []
    orig = ScheduleOptimizer._greedy_construct

    def _greedy(self, flex, validator, generator, scorer, **kw):
        solution, stats = orig(self, flex, validator, generator, scorer, **kw)
        for i, placement in enumerate(solution):
            if placement is None:
                continue
            cls = flex[i]
            day, slot, room = placement
            before = validator.check_placement(cls, day, slot, room)
            validator.remove_placement(cls, day, slot, room)
            after = validator.check_placement(cls, day, slot, room)
            validator.add_placement(cls, day, slot, room)
            restored = validator.check_placement(cls, day, slot, room)
            observed.append((cls.get("name"), before, after, restored))
            break
        return solution, stats

    ScheduleOptimizer._greedy_construct = _greedy
    try:
        SchedulingWorkflow(state, lambda: {}).reschedule({}, use_cpsat=False)
    finally:
        ScheduleOptimizer._greedy_construct = orig

    assert observed, (
        "the greedy returned no placement at all, so nothing was measured")
    for name, before, after, restored in observed:
        # `before is False` is the ST-SCHED-001 property the rest of the module
        # already covers: the validator knows about the placement it returned.
        assert before is False, (
            f"{name!r}: the validator does not hold the placement the greedy "
            "returned — the occupancy resync is missing (ST-SCHED-001)")
        assert after is True, (
            f"{name!r}: one remove_placement did NOT free the cell, so the "
            "validator holds MORE THAN ONE claim for a single placement. The "
            "reconciliation at the end of _greedy_construct must leave the "
            "maps alone where `solution[i] == best_solution[i]` and release "
            "the stale claim where it does not — re-adding `best_solution` "
            "unconditionally is idempotent on sets and permanent on "
            "ref-counted cells (ST-SCHED-010). Verified by mutation: that "
            "variant fails this assertion and passes every other test in this "
            "module.")
        assert restored is False, (
            f"{name!r}: re-adding the placement did not restore its cell")


@pytest.mark.engine
def test_optimizer_validator_holds_one_claim_per_locked_class():
    """Regression guard for the ST-SCHED-010 fix (currently PASSES — Trap 4).

    This is the test that must stay green through Phase 3 rather than flip, and
    it exists because the obvious ST-SCHED-010 fix breaks it.

    ``ScheduleOptimizer.optimize`` builds its per-run validator with
    ``exclude_ids`` covering only ``all_flexible``
    (``schedule_optimizer.py:387-397``), so a ``locked`` class — which is
    ``placed``, and therefore already counted by ``build_occupancy`` — is then
    claimed a *second* time by the explicit ``validator.add_placement`` loop.
    Set-valued cells swallowed that. Ref-counted cells do not: the cell would
    need two releases, nothing ever issues the second, and the optimizer would
    quietly lose that room-hour for the rest of the solve.

    Measured by asking the optimizer's own validator, mid-solve, whether one
    ``remove_placement`` frees the locked class's cell. It must, because exactly
    one placement was made. A failure means every reschedule silently has fewer
    usable slots than the timetable really has, and classes start coming back
    unplaced for no reason a user can see.
    """
    from scheduler_app.core.models import PROTECTION_LOCKED, mark_placed
    from scheduler_app.core.schedule_optimizer import ScheduleOptimizer
    from scheduler_app.core.workflow import SchedulingWorkflow

    state = _micro_state(["monday", "tuesday"], ["09:00", "10:00"],
                         ["R001", "R002"])
    locked = _mk_class("Locked", "Lect-A", _T1)
    locked["protection"] = PROTECTION_LOCKED
    mark_placed(locked, "monday", "09:00", "R001")
    flexible = [_mk_class(f"Flex{i}", "Lect-B", _T2) for i in range(3)]
    state["classes"] = [locked] + flexible

    # Shares every occupancy dimension with `locked` (room, lecturer, target) and
    # is not part of the state, so it reads the maps without disturbing them.
    probe = _mk_class("Probe", "Lect-A", _T1)

    observed = []
    orig = ScheduleOptimizer._greedy_construct

    def _greedy(self, flex, validator, generator, scorer, **kw):
        solution, stats = orig(self, flex, validator, generator, scorer, **kw)
        before = validator.check_placement(probe, "monday", "09:00", "R001")
        validator.remove_placement(locked, "monday", "09:00", "R001")
        after = validator.check_placement(probe, "monday", "09:00", "R001")
        validator.add_placement(locked, "monday", "09:00", "R001")
        restored = validator.check_placement(probe, "monday", "09:00", "R001")
        observed.append((before, after, restored))
        return solution, stats

    ScheduleOptimizer._greedy_construct = _greedy
    try:
        wf = SchedulingWorkflow(state, lambda: {})
        wf.reschedule({}, use_cpsat=False)
    finally:
        ScheduleOptimizer._greedy_construct = orig

    assert observed, "the optimizer never reached _greedy_construct"
    for i, (before, after, restored) in enumerate(observed):
        assert before is False, (
            f"run {i}: the locked class's cell was not occupied at all in the "
            "optimizer's validator — the baseline this test measures is missing")
        assert after is True, (
            f"run {i}: one remove_placement did NOT free the locked class's "
            "cell, so the optimizer's validator is holding more than one claim "
            "for a single placement. See schedule_optimizer.py:387-397 — the "
            "locked class is registered by build_occupancy AND by the explicit "
            "add_placement loop, and with ref-counted cells that double claim "
            "can never be released.")
        assert restored is False, (
            f"run {i}: re-adding the locked placement did not restore its cell; "
            "this test's own mutate-and-restore probe is not balanced")


# ===========================================================================
# 4. THE SAME INVARIANTS AT 25-CLASS SCALE  (slow)
# ===========================================================================
@pytest.fixture(scope="module")
def small_phases():
    """One production reschedule of the 25-class `small` preset, instrumented.

    Module-scoped and used only by ``slow`` tests, so a ``-m "not slow"`` run
    never pays for it. ~28 s at HEAD.
    """
    return _capture_phases(make_preset("small", seed=42))


@pytest.mark.engine
@pytest.mark.slow
def test_greedy_occupancy_matches_its_answer_on_small(small_phases):
    """Pins ST-SCHED-001 (Critical) at the seam, at realistic scale.

    The micro instance reaches the partial-success exit by running out of cells.
    ``small`` reaches it the other way — ``budget_exhausted=True``, the greedy
    gives up mid-search — and the outcome is the same emptied occupancy map. Both
    paths matter: a fix that only resynchronises on one of them leaves the other
    corrupting real, department-sized timetables.

    Measured against unmodified HEAD: 20 of 20 returned placements unknown to the
    validator on all five multi-start runs.
    """
    runs, _result = small_phases
    floor = max(1, math.ceil(runs[0]["n"] * _MIN_PLACED_FRACTION))
    _assert_seam_floor(runs, floor=floor, expect_partial=True)

    assert all(r["desync"] == 0 for r in runs), _seam_failure_message(runs, "small")
    assert all(not r["map_diff"] for r in runs), (
        "occupancy does not match the returned solution on `small`:\n"
        + "\n".join(_format_diff(r["map_diff"]) for r in runs if r["map_diff"]))


@pytest.mark.engine
@pytest.mark.slow
def test_lns_never_introduces_a_conflict_on_small(small_phases):
    """Pins ST-SCHED-001 (Critical) — phase attribution at 25-class scale.

    Complements the spine's ``test_raw_optimizer_output_is_clean_small``, which
    audits the best-of-five proposal and cannot say where the damage came from.
    This checks every multi-start run separately and compares the two phases, so
    a partial fix that cleans up only some runs still fails.

    Measured against unmodified HEAD: greedy 0 violations on 5/5 runs; LNS 16,
    14, 0, 18 and 18 violations. Note run 2 came out clean — that is exactly why
    this asserts per-run rather than on the aggregate, and why the spine's
    ``normal`` pin had to be ``strict=False``: judging one run is a coin flip,
    judging all five is not.
    """
    runs, _result = small_phases
    for i, run in enumerate(runs):
        assert run["lns_hard"] is not None, (
            f"run {i}: _lns_improve was never called — vacuous")
        assert run["lns_placed"] >= 3, (
            f"run {i}: LNS returns without running its loop below 3 placements "
            f"(got {run['lns_placed']}) — vacuous")

    dirty = [(i, r) for i, r in enumerate(runs) if r["lns_hard"] > r["greedy_hard"]]
    assert not dirty, (
        f"{len(dirty)}/{len(runs)} multi-start runs came out of LNS with MORE "
        "hard-constraint violations than went in:\n"
        + "\n".join(
            f"-- run {i}: greedy {r['greedy_hard']} violation(s) from "
            f"{r['greedy_placed']} placements -> LNS {r['lns_hard']} from "
            f"{r['lns_placed']}\n{format_violations(r['lns_audit'], limit=4)}"
            for i, r in dirty))
