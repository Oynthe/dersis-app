"""Hard-constraint invariants for the DERSİS scheduling engine.

This is the correctness spine of the suite. It promotes the stress-test audit's
INDEPENDENT oracle (``tests/_support/schedule_oracle.py``) into real regression
tests, so that "the schedule DERSİS commits contains no hard-constraint
violation" becomes an enforced property rather than an audit anecdote.

Layout of this module (order matters — read it top to bottom)
------------------------------------------------------------
1. **Oracle self-tests.** Hand-built states with known, deliberate breakages.
   Without these, every other assertion here could be vacuously true because
   the oracle degraded to a no-op. They run first and cost nothing.
2. **Committed-state invariants.** What the user actually ends up with after
   ``apply_reschedule`` — must be clean.
3. **Raw-optimizer invariants** (ST-SCHED-001). What the optimizer *proposed*,
   before ``apply_reschedule`` throws the losers away — currently dirty.
4. **Nothing-silently-dropped** (ST-SCHED-001). A class the optimizer placed and
   the commit step discarded is data loss from the user's point of view.
5. **Pin / lock invariants** (ST-SCHED-002, ST-SCHED-007).

Two traps this module has to defend against
-------------------------------------------
**Trap 1 — "clean because empty".** ``hard_violation_count`` of a schedule with
zero placements is zero. An optimizer that proposed *nothing* would therefore
satisfy every "is clean" assertion here. Verified empirically: stubbing
``optimized_reschedule_all`` to return ``([], [], [], None)`` turned all three
``tiny`` guards green. The preset fixtures below therefore refuse to yield an
audit whose placement count is degenerate (``_MIN_PLACED_FRACTION``). A fixture
failure surfaces as an ERROR for every consumer and — unlike an assertion in the
test body — cannot be swallowed by an ``xfail`` marker.

**Trap 2 — the oracle cannot audit pins from a committed state.** For a class
with ``pinned=True`` the oracle's ``_effective()`` reads position out of the very
``pinned_day/pinned_time/pinned_classroom`` fields its ``pinned_moved`` rule
compares against, so that rule is *structurally* vacuous on a committed-state
audit — passing ``pinned_baseline`` does not rescue it, because nothing in the
production path ever rewrites a pin field. (Confirmed: forcing the pinned class's
``placed_*`` to a different cell still yields ``pinned_moved == 0``.) The pin test
below therefore checks the class dict directly and uses the oracle only where it
is meaningful: on a ``placements=`` audit of the RAW proposal, where positions
come from the optimizer's own tuples.

Non-determinism warning (ST-SCHED-013)
--------------------------------------
The optimizer seeds nothing — LNS and multi-start use the unseeded global
``random`` — *and* it is wall-clock-bound (``multi_start_time_limit=120.0``,
``lns_time_limit``), so it cannot be made reproducible from a test even by
seeding. Identical input gives different output run to run. Measured
"comes out clean by luck" rates, per single trial:

    ``small``   2 clean / 15 runs  (~13 %)
    ``normal``  1 clean / 13 runs  (~8 %)

The `small` pins therefore aggregate ``RAW_TRIALS`` independent trials and
assert the invariant on *every* one, which is both the semantically correct
statement ("the optimizer must never propose an invalid schedule") and what
makes ``strict=True`` safe there. The `normal` pins run one trial and are
``strict=False``; see their docstrings. Trial outcomes were checked for
correlation with machine load — 6/6 `small` runs stayed dirty under full CPU
saturation — so aggregating independent trials is sound.

Runtime (measured on the audit machine, .venv-audit)
----------------------------------------------------
``pytest tests/test_scheduler_invariants.py -m "not slow"``  ~2-10 s
``pytest tests/test_scheduler_invariants.py``                ~220-350 s

The fast half is not perfectly steady (2.1/2.2/2.2 s idle, ~10 s on a loaded
box) because the pin and lock tests run the real optimizer on a 4-class grid;
it is an order of magnitude inside the 30 s budget either way. The slow half is
all optimizer time: ``small`` costs ~30-50 s per trial (x3) and ``normal`` is
hard-capped at 120 s by ``multi_start_time_limit=120.0``.
Note that CI currently runs ``pytest -m "not slow"``, so the ST-SCHED-001 pins
below only execute when someone runs the module without that filter.
"""
import math

import pytest

from _support.schedule_oracle import (
    audit_preset,
    check_schedule,
    format_violations,
    hard_violation_count,
)

# How many independent optimizer runs the `small` ST-SCHED-001 pins perform.
# The optimizer is non-deterministic (ST-SCHED-013); with a measured per-run
# "clean by luck" rate of ~13 % on `small`, three trials put the false-XPASS
# probability at ~0.2 %, which is what makes strict=True defensible there.
# Raising this makes the pins more reliable and proportionally slower
# (~30 s per trial).
RAW_TRIALS = 3

# Anti-vacuity floor (Trap 1). The optimizer must propose at least this fraction
# of the instance's classes before any "the schedule is clean" assertion in this
# module means anything. Deliberately far below observed behaviour (tiny 5/5,
# small 21/25, normal 76/80) so this is a degeneracy detector, not a quality
# target — quality regressions are ST-SCHED-001's job, not this gate's.
_MIN_PLACED_FRACTION = 0.5

_T1 = {"year": "Year-1", "branch": "A"}
_T2 = {"year": "Year-1", "branch": "B"}


# ---------------------------------------------------------------------------
# Hand-built micro-fixtures (no optimizer, no I/O)
# ---------------------------------------------------------------------------
def _mini_state():
    """A 2-day x 3-slot x 2-room grid — small enough to reason about by hand."""
    from scheduler_app.core.models import new_state

    state = new_state()
    state["days"] = ["monday", "tuesday"]
    state["slots"] = ["09:00", "10:00", "11:00"]
    state["classrooms"] = ["R001", "R002"]
    # 0 == "capacity unknown"; keeps the oracle's (soft) capacity rule quiet so
    # these fixtures isolate exactly the invariant under test.
    state["classroom_capacities"] = {"R001": 0, "R002": 0}
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


def _run_workflow(state):
    """Run the production reschedule + commit path. Returns (result, rejected)."""
    from scheduler_app.core.workflow import SchedulingWorkflow

    wf = SchedulingWorkflow(state, lambda: {})
    result = wf.reschedule({}, use_cpsat=False)
    rejected = wf.apply_reschedule(result)
    return result, rejected


def _effective_pos(cls):
    """(day, slot, room) as the *production code* reads it (pin wins over placed)."""
    from scheduler_app.core.models import (
        effective_day, effective_room, effective_time)

    return (effective_day(cls), effective_time(cls), effective_room(cls))


# ---------------------------------------------------------------------------
# Shared, cached workflow runs. Each optimizer run is expensive, so the audits
# are computed once per module and shared by the assertions that read them.
# A module-scoped fixture used only by @slow tests is never built during a
# `-m "not slow"` run.
# ---------------------------------------------------------------------------
def _guard_not_degenerate(audit, label):
    """Trap 1: refuse to hand out an audit that would make 'is clean' vacuous.

    Raised from fixture setup on purpose — a setup failure is an ERROR for every
    consumer and cannot be absorbed by an ``xfail(strict=True)`` marker the way a
    failed assertion inside a test body would be.
    """
    n_classes = len(audit["state"]["classes"])
    floor = max(1, math.ceil(n_classes * _MIN_PLACED_FRACTION))
    n_raw = audit["raw"]["n_placed"]
    assert n_raw >= floor, (
        f"DEGENERATE OPTIMIZER RUN on `{label}`: only {n_raw} of {n_classes} "
        f"classes were proposed (floor {floor}). Every 'schedule is clean' "
        "assertion in this module is vacuous on a run this empty, so the "
        "fixture refuses to yield it.")
    n_committed = sum(1 for c in audit["state"]["classes"]
                      if c.get("placed") or c.get("pinned"))
    assert n_committed >= floor, (
        f"DEGENERATE COMMIT on `{label}`: only {n_committed} of {n_classes} "
        f"classes survived apply_reschedule (floor {floor}).")
    return audit


@pytest.fixture(scope="module")
def tiny_audit():
    """One production reschedule+commit cycle over the 5-class `tiny` preset."""
    return _guard_not_degenerate(audit_preset("tiny", seed=42), "tiny")


@pytest.fixture(scope="module")
def small_trials():
    """`RAW_TRIALS` independent reschedule+commit cycles over `small` (25)."""
    return [_guard_not_degenerate(audit_preset("small", seed=42), f"small#{i}")
            for i in range(RAW_TRIALS)]


@pytest.fixture(scope="module")
def normal_audit():
    """One production reschedule+commit cycle over the 80-class `normal`."""
    return _guard_not_degenerate(audit_preset("normal", seed=42), "normal")


# ===========================================================================
# 1. ORACLE SELF-TESTS — prove the oracle is not a no-op
# ===========================================================================
def test_oracle_flags_a_known_room_double_booking():
    """Guards the oracle itself (no finding ID).

    If this fails, the oracle has stopped detecting collisions and every other
    invariant assertion in this module is vacuous — meaning a user could be
    shipped a timetable with two classes in one room and nothing would notice.
    """
    from scheduler_app.core.models import mark_placed

    state = _mini_state()
    alpha = _mk_class("Alpha", "Lect-A", _T1)
    beta = _mk_class("Beta", "Lect-B", _T2)
    state["classes"] = [alpha, beta]

    # Same room, same day, same slot; distinct lecturers and distinct student
    # groups, so a room collision is the ONLY thing wrong with this state.
    mark_placed(alpha, "monday", "09:00", "R001")
    mark_placed(beta, "monday", "09:00", "R001")

    audit = check_schedule(state)

    assert audit["n_placed"] == 2, "oracle did not even see both classes"
    assert audit["counts"] == {"room_double_book": 2}, (
        "oracle must report exactly the room double-booking (one entry per "
        f"colliding class) and nothing else:\n{format_violations(audit)}")
    assert {v["cls"] for v in audit["violations"]} == {"Alpha", "Beta"}
    assert hard_violation_count(audit) == 2


def test_oracle_reports_a_clean_schedule_as_clean():
    """Guards the oracle itself (no finding ID).

    If this fails the oracle cries wolf on a valid timetable, which would make
    every invariant test in this module red for the wrong reason.
    """
    from scheduler_app.core.models import mark_placed

    state = _mini_state()
    alpha = _mk_class("Alpha", "Lect-A", _T1)
    beta = _mk_class("Beta", "Lect-B", _T2)
    state["classes"] = [alpha, beta]

    mark_placed(alpha, "monday", "09:00", "R001")
    mark_placed(beta, "monday", "09:00", "R002")

    audit = check_schedule(state)

    assert audit["n_placed"] == 2
    assert audit["counts"] == {}, format_violations(audit)
    assert hard_violation_count(audit) == 0


@pytest.mark.parametrize("shared,expected", [
    ("lecturer", "lecturer_double_book"),
    ("target", "group_clash"),
])
def test_oracle_flags_known_lecturer_and_group_clashes(shared, expected):
    """Guards the oracle itself (no finding ID).

    Room collisions are only one third of the oracle's job; if it silently
    stopped tracking lecturers or student groups, a teacher could be timetabled
    into two rooms at once and this suite would call the schedule clean.
    """
    from scheduler_app.core.models import mark_placed

    state = _mini_state()
    alpha = _mk_class("Alpha", "Lect-A", _T1)
    beta = _mk_class("Beta", "Lect-B", _T2)
    if shared == "lecturer":
        beta["lecturer"] = "Lect-A"
    else:
        beta["targets"] = [dict(_T1)]
    state["classes"] = [alpha, beta]

    # Different rooms, so the room rule stays quiet and the expected category
    # is the only one that can fire.
    mark_placed(alpha, "monday", "09:00", "R001")
    mark_placed(beta, "monday", "09:00", "R002")

    audit = check_schedule(state)

    assert audit["counts"] == {expected: 2}, format_violations(audit)


# ===========================================================================
# 2. COMMITTED STATE IS CLEAN
# ===========================================================================
@pytest.mark.engine
def test_committed_schedule_is_clean_tiny(tiny_audit):
    """Regression guard for the ST-SCHED-001 completion criterion.

    A failure means DERSİS saved a timetable a school cannot actually run:
    two classes in one room, one teacher in two places, or a student group
    double-booked.
    """
    # Sharper than the fixture's degeneracy floor: a 5-class instance on a
    # 2-room grid is trivially satisfiable and every class must land. Measured
    # 5/5 placed in 25/25 consecutive runs, so a shortfall here is a real
    # regression, not optimizer noise.
    state = tiny_audit["state"]
    scheduled = [c for c in state["classes"] if c.get("placed") or c.get("pinned")]
    assert len(scheduled) == len(state["classes"]) == 5, (
        "the optimizer failed to schedule every class of the trivial `tiny` "
        f"instance: {len(scheduled)}/{len(state['classes'])} placed")

    audit = tiny_audit["applied"]
    assert hard_violation_count(audit) == 0, (
        "committed `tiny` schedule contains hard-constraint violations:\n"
        + format_violations(audit))


@pytest.mark.engine
@pytest.mark.slow
def test_committed_schedule_is_clean_small(small_trials):
    """Regression guard for the ST-SCHED-001 completion criterion.

    Checks every optimizer trial, because the optimizer is non-deterministic
    (ST-SCHED-013) and one clean run proves nothing. A failure means the app
    committed an unrunnable timetable at 25-class scale.

    NOTE: this currently passes only because ``apply_reschedule`` silently
    DROPS the colliding classes — see the ST-SCHED-001 pins below, which are
    what actually expose the defect this test is too generous to catch.
    """
    for i, trial in enumerate(small_trials):
        audit = trial["applied"]
        assert hard_violation_count(audit) == 0, (
            f"committed `small` schedule (trial {i}) contains hard-constraint "
            "violations:\n" + format_violations(audit))


@pytest.mark.engine
@pytest.mark.slow
def test_committed_schedule_is_clean_normal(normal_audit):
    """Regression guard for the ST-SCHED-001 completion criterion.

    A failure means an 80-class department schedule was committed with real
    double-bookings in it.
    """
    audit = normal_audit["applied"]
    assert hard_violation_count(audit) == 0, (
        "committed `normal` schedule contains hard-constraint violations:\n"
        + format_violations(audit))


# ===========================================================================
# 3. RAW OPTIMIZER OUTPUT IS CLEAN  (ST-SCHED-001)
# ===========================================================================
@pytest.mark.engine
def test_raw_optimizer_output_is_clean_tiny(tiny_audit):
    """Guards ST-SCHED-001 at trivial scale (currently PASSES — no xfail).

    At 5 classes the optimizer's occupancy bookkeeping happens to hold: 25
    consecutive standalone audits plus every pytest run of this module came out
    with zero violations and 5/5 classes placed, so this is a plain assertion
    rather than a pin despite the RNG. If it ever goes red, the ST-SCHED-001
    collision bug has spread down to instances small enough that a user would
    notice immediately.
    """
    audit = tiny_audit["raw"]
    assert hard_violation_count(audit) == 0, (
        "raw optimizer proposal for `tiny` contains hard-constraint "
        "violations:\n" + format_violations(audit))


@pytest.mark.engine
@pytest.mark.slow
@pytest.mark.xfail(strict=True, reason=(
    "ST-SCHED-001 — the optimizer's internal occupancy bookkeeping lets two "
    "distinct flexible classes take the same room/lecturer/group cell, so the "
    "proposed schedule is invalid before apply_reschedule prunes it; "
    "fixed in Phase 3"))
def test_raw_optimizer_output_is_clean_small(small_trials):
    """Pins ST-SCHED-001 (Critical).

    The optimizer must never *propose* a schedule that breaks a hard
    constraint. Today it does, and the only reason the user does not see the
    double-booking is that ``apply_reschedule`` throws one of the colliding
    classes away (see ST-SCHED-001's cover-up, pinned separately below).

    Every trial must be clean: the optimizer is non-deterministic
    (ST-SCHED-013), so a single clean run is luck, not correctness.
    """
    dirty = [(i, t["raw"]) for i, t in enumerate(small_trials)
             if hard_violation_count(t["raw"]) > 0]
    assert not dirty, (
        f"{len(dirty)}/{len(small_trials)} raw optimizer proposals for `small` "
        "contain hard-constraint violations:\n"
        + "\n".join(f"-- trial {i} --\n{format_violations(a)}"
                    for i, a in dirty))


@pytest.mark.engine
@pytest.mark.slow
@pytest.mark.xfail(strict=False, reason=(
    "ST-SCHED-001 — same defect as the `small` pin, at 80-class scale where it "
    "is far denser (62-130 violation cells over ~76 placements); "
    "NOT strict, see docstring; fixed in Phase 3"))
def test_raw_optimizer_output_is_clean_normal(normal_audit):
    """Pins ST-SCHED-001 (Critical) at department scale.

    ``strict=False`` on purpose, and this is the one deviation from the suite's
    "strict pins so the fix turns the build red" convention. A single `normal`
    trial is NOT a reliable failure: over 13 measured runs it came out entirely
    clean once (raw 0 violations, 0 rejections, 77/80 placed) — roughly 8 %.
    A strict marker would therefore XPASS and break the build about one run in
    thirteen, for no reason connected to the code. Load starvation was ruled out
    as the cause (6/6 `small` runs stayed dirty under full CPU saturation), so
    this is plain ST-SCHED-013 RNG luck and cannot be engineered away here; a
    second 120 s trial would be the alternative and is not worth the wall clock.

    The red-when-fixed signal for ST-SCHED-001 lives on the two `small` pins,
    which aggregate ``RAW_TRIALS`` trials and ARE strict. When those flip, delete
    this decorator too.
    """
    audit = normal_audit["raw"]
    assert hard_violation_count(audit) == 0, (
        "raw optimizer proposal for `normal` contains hard-constraint "
        "violations:\n" + format_violations(audit))


# ===========================================================================
# 4. NOTHING IS SILENTLY DROPPED  (ST-SCHED-001)
# ===========================================================================
@pytest.mark.engine
def test_apply_reschedule_drops_nothing_tiny(tiny_audit):
    """Guards ST-SCHED-001's drop path at trivial scale (currently PASSES).

    A rejected placement means the optimizer said "this class fits here", the
    commit step disagreed, and the class silently ended up unplaced — from the
    user's side, a lesson vanished from the timetable with no message.
    """
    assert tiny_audit["rejected"] == 0, (
        f"apply_reschedule silently dropped {tiny_audit['rejected']} "
        "placement(s) on `tiny`")


@pytest.mark.engine
@pytest.mark.slow
@pytest.mark.xfail(strict=True, reason=(
    "ST-SCHED-001 — apply_reschedule silently drops the classes that lost an "
    "optimizer-produced collision, and the UI discards its rejected list; "
    "fixed in Phase 3"))
def test_apply_reschedule_drops_nothing_small(small_trials):
    """Pins ST-SCHED-001 (Critical) — the silent data-loss half.

    Every class the optimizer placed must survive the commit. Today the commit
    step re-validates, finds the collisions the optimizer created, and drops
    the losers without telling anyone: the user asked for a timetable and
    quietly got fewer lessons than the solver actually placed.

    Checked over every trial because of ST-SCHED-013's non-determinism.
    """
    dropped = [(i, t["rejected"]) for i, t in enumerate(small_trials)
               if t["rejected"] != 0]
    assert not dropped, (
        "apply_reschedule silently dropped placements on `small`: "
        + ", ".join(f"trial {i}: {n} dropped" for i, n in dropped))


@pytest.mark.engine
@pytest.mark.slow
@pytest.mark.xfail(strict=False, reason=(
    "ST-SCHED-001 — apply_reschedule silently drops 11-21 of ~76 placements at "
    "80-class scale; NOT strict, see docstring; fixed in Phase 3"))
def test_apply_reschedule_drops_nothing_normal(normal_audit):
    """Pins ST-SCHED-001 (Critical) — silent data loss at department scale.

    A whole department's worth of lessons the solver had already placed
    disappear at commit time, with no message anywhere in the UI.

    ``strict=False`` for the same reason as the raw-proposal pin above: the same
    ~8 % of `normal` runs that come out collision-free also drop nothing, so a
    strict marker XPASSes at random. The strict pins are on `small`.
    """
    assert normal_audit["rejected"] == 0, (
        f"apply_reschedule silently dropped {normal_audit['rejected']} of "
        f"{normal_audit['raw']['n_placed']} placement(s) on `normal`")


# ===========================================================================
# 5. PINS AND LOCKS
# ===========================================================================
@pytest.mark.engine
def test_feasible_pin_is_honored():
    """Regression guard for ST-SCHED-002's happy path.

    A failure means a class the user explicitly pinned to a day/hour/room was
    moved somewhere else behind their back, or another lesson was dropped on
    top of it.

    See Trap 2 in the module docstring: the oracle's ``pinned_moved`` rule is
    only usable on the RAW proposal (positions come from the optimizer's own
    tuples). The committed half is checked against the class dict directly.
    """
    state = _mini_state()
    pinned = _mk_class("PinA", "Lect-A", _T1)
    pinned["pinned"] = True
    pinned["pinned_day"] = "monday"
    pinned["pinned_time"] = "09:00"
    pinned["pinned_classroom"] = "R001"
    flexible = [_mk_class(f"Flex{i}", "Lect-B", _T2) for i in range(3)]
    state["classes"] = [pinned] + flexible

    # Captured BEFORE the run, so the raw check cannot be satisfied by the app
    # rewriting the pin fields to match wherever it actually put the class.
    PIN = ("monday", "09:00", "R001")
    pin_baseline = {pinned["class_uid"]: PIN}

    result, rejected = _run_workflow(state)

    # -- the optimizer proposed the pin, at the pin -------------------------
    proposed = {c["class_uid"] for c, _, _, _ in result.placed}
    assert pinned["class_uid"] in proposed, (
        "the pinned class is missing from the optimizer's proposal entirely")
    raw = check_schedule(state, placements=result.placed,
                         pinned_baseline=pin_baseline)
    assert raw["counts"].get("pinned_moved", 0) == 0, (
        "optimizer proposed moving a pinned class off its pin:\n"
        + format_violations(raw))

    # -- the commit kept it there ------------------------------------------
    # Read straight off the class dict; the oracle cannot see this (Trap 2).
    assert pinned["pinned"] is True, "the commit step silently cleared the pin"
    assert (pinned["pinned_day"], pinned["pinned_time"],
            pinned["pinned_classroom"]) == PIN, (
        "the commit step rewrote the pin fields to hide a move: "
        f"{(pinned['pinned_day'], pinned['pinned_time'], pinned['pinned_classroom'])}")
    assert _effective_pos(pinned) == PIN, (
        "the class's effective position (what every view renders) is no longer "
        f"the pin: {_effective_pos(pinned)} != {PIN}")
    if pinned["placed"]:
        # apply_reschedule currently `continue`s past pinned classes so this
        # stays False; a Phase-1 fix may legitimately start writing placed_*.
        # Either way it must not contradict the pin.
        assert (pinned["placed_day"], pinned["placed_time"],
                pinned["placed_classroom"]) == PIN, (
            "committed placement contradicts the pin: "
            f"{(pinned['placed_day'], pinned['placed_time'], pinned['placed_classroom'])}"
            f" != {PIN}")

    # -- and nothing was dropped on top of it ------------------------------
    # Non-vacuity: this half only means something if the flexible classes were
    # actually committed somewhere.
    assert all(c["placed"] for c in flexible), (
        "the flexible classes were not committed, so 'nothing collides with "
        "the pin' is vacuously true: "
        + repr([(c["name"], c["placed"]) for c in flexible]))
    applied = check_schedule(state)
    assert hard_violation_count(applied) == 0, (
        "committed schedule around a feasible pin is not clean:\n"
        + format_violations(applied))
    assert rejected == [], f"unexpected rejections: {rejected}"


@pytest.mark.engine
@pytest.mark.xfail(strict=True, reason=(
    "ST-SCHED-002 — apply_reschedule skips validation for pinned classes "
    "(`if cls_item['pinned']: continue`), so two classes pinned to the same "
    "room/day/slot are committed as-is and reported to nobody; "
    "fixed in Phase 1"))
def test_colliding_pins_are_not_silently_committed():
    """Pins ST-SCHED-002 (High).

    Two classes pinned to the same room, day and hour cannot both happen. The
    app must either refuse one pin or report the clash. Today it commits both
    and the quality panel calls the timetable clean, so a user who pins by hand
    can ship a schedule with invisible double-bookings.

    Expectation encoded (see report — the finding does not prescribe a shape):
    a committed schedule may not contain a hard violation UNLESS the workflow
    surfaced it, either through ``apply_reschedule``'s rejected list or through
    ``result.unplaced``. Both possible Phase-1 fixes satisfy that. Note the
    first half also covers a fix that clears one pin: an unpinned, unplaced
    class stops occupying the cell, so the violation disappears.
    """
    state = _mini_state()
    pin_a = _mk_class("PinA", "Lect-A", _T1)
    pin_b = _mk_class("PinB", "Lect-B", _T2)
    for cls in (pin_a, pin_b):
        cls["pinned"] = True
        cls["pinned_day"] = "monday"
        cls["pinned_time"] = "09:00"
        cls["pinned_classroom"] = "R001"
    state["classes"] = [pin_a, pin_b, _mk_class("Flex", "Lect-C", _T2)]

    result, rejected = _run_workflow(state)
    applied = check_schedule(state)

    surfaced = bool(rejected) or bool(result.unplaced)
    assert hard_violation_count(applied) == 0 or surfaced, (
        "two classes pinned to the same room/day/slot were committed with "
        "hard-constraint violations and NOTHING was reported "
        f"(rejected={rejected!r}, unplaced={result.unplaced!r}):\n"
        + format_violations(applied))


@pytest.mark.engine
def test_locked_class_is_not_moved_by_reschedule():
    """Regression guard for ST-SCHED-002/ST-SCHED-007's lock semantics.

    ``protection='locked'`` is the user saying "never touch this one". A
    failure means a full reschedule quietly relocated a lesson the user had
    frozen — the legacy solver family does exactly that (ST-SCHED-007), so this
    guards the optimized path against inheriting the same bug.
    """
    from scheduler_app.core.models import mark_placed

    state = _mini_state()
    locked = _mk_class("Locked", "Lect-A", _T1)
    locked["protection"] = "locked"
    mark_placed(locked, "tuesday", "11:00", "R002")
    LOCK = ("tuesday", "11:00", "R002")

    # Same lecturer as the locked class, so the solver is under real pressure
    # around the frozen cell rather than ignoring it.
    flexible = [_mk_class(f"Flex{i}", "Lect-A", _T2) for i in range(3)]
    for cls in flexible:
        mark_placed(cls, "monday", "09:00", "R001")  # deliberately colliding
    state["classes"] = [locked] + flexible

    locked_baseline = {locked["class_uid"]: LOCK}

    result, _rejected = _run_workflow(state)

    # The optimizer must not even PROPOSE moving it. (Measured: it proposes the
    # lock at its baseline in 6/6 runs, so this is a real assertion, not a
    # hopeful one — and it catches a regression one step earlier than the
    # committed-state check below.)
    raw_lock = [(d, s, r) for c, d, s, r in result.placed if c is locked]
    assert raw_lock in ([], [LOCK]), (
        f"optimizer proposed moving a protection='locked' class: {raw_lock}")

    audit = check_schedule(state, locked_baseline=locked_baseline)

    assert audit["counts"].get("locked_moved", 0) == 0, (
        "a protection='locked' class was moved or unplaced by reschedule:\n"
        + format_violations(audit))
    assert (locked["placed"], locked["placed_day"], locked["placed_time"],
            locked["placed_classroom"]) == (True,) + LOCK
    assert hard_violation_count(audit) == 0, (
        "committed schedule around a locked class is not clean:\n"
        + format_violations(audit))

    # Non-vacuity: the reschedule must actually have done work, otherwise
    # "locked did not move" would be trivially true.
    moved = [c for c in flexible
             if (c["placed_day"], c["placed_time"], c["placed_classroom"])
             != ("monday", "09:00", "R001")]
    assert moved, ("no flexible class moved — the reschedule was a no-op, so "
                   "this test proved nothing about lock handling")
