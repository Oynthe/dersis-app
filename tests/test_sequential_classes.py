"""Sequential (non-joint) multi-target classes (ST-ARCH-001, top-10 item 7).

A class with more than one target group can be taught two ways:

* **joint** (``joint_session=True``, the default) — every group sits in the
  room together for the whole block. One hour of teaching, all groups busy for
  that hour.
* **sequential** (``joint_session=False``) — the lecturer teaches the same
  material to each group in turn. An N-target, ``duration=D`` class therefore
  occupies ``D * N`` consecutive slots, and each group is busy for **its own**
  sub-block only.

The whole model lives behind ``logic.total_duration`` and the per-slot target
resolution the validator family uses. The Phase 7 measurement round collapsed
that resolution to "every slot belongs to every target" — deleting sequential
semantics outright — and ran the **entire CI lane**: ``EXIT=0``. The fixtures
explain why: ``tests/_support/dataset_gen.py`` never sets ``joint_session``, so
``tiny``/``small``/``normal`` contain **0** non-joint multi-target classes
between them, and no test passed ``joint=False``.

**What a failure here costs a user.** Sequential teaching is how a lecturer
covers two branches of the same year with one preparation. Treat every
sub-block as busy for every group and the second branch's other lessons all
become "conflicting": the timetable the app can find shrinks, lessons go
unplaced, and the reason it gives is a clash that does not exist. Treat the
sub-blocks as busy for *nobody* and it double-books the group instead.

**Written against public behaviour on purpose.** These tests drive
``ConstraintValidator`` and ``CandidateGenerator``, not the private helper that
resolves a slot offset to a target. That helper is being moved between modules
in this phase; the promise it implements is not.

Cost: hand-built 2-day grids, no optimizer, no Qt — milliseconds, fast lane.
"""
import pytest

from scheduler_app.core.constraint_validator import ConstraintValidator
from scheduler_app.core.models import mark_placed, new_class, new_state
from scheduler_app.logic import total_duration

_YEAR = "Year-1"


def _grid():
    """A 1-day x 4-slot x 2-room grid, small enough to reason about by hand."""
    state = new_state()
    state["days"] = ["monday"]
    state["slots"] = ["09:00", "10:00", "11:00", "12:00"]
    state["classrooms"] = ["R001", "R002"]
    # 0 == "capacity unknown": keeps the (soft) capacity rule out of the way so
    # each assertion below is about group occupancy and nothing else.
    state["classroom_capacities"] = {"R001": 0, "R002": 0}
    state["lecturers"] = []
    state["years"] = {_YEAR: ["A", "B"]}
    return state


def _add(state, name, lecturer, branches, duration=1, joint=True):
    cls = new_class()
    cls["class_code"] = cls["name"] = name
    cls["lecturer"] = lecturer
    if lecturer not in state["lecturers"]:
        state["lecturers"].append(lecturer)
    cls["targets"] = [{"year": _YEAR, "branch": b} for b in branches]
    cls["duration"] = duration
    cls["joint_session"] = joint
    cls["participants"] = 0
    state["classes"].append(cls)
    return cls


# ===========================================================================
# 1. THE BLOCK LENGTH
# ===========================================================================
def test_a_sequential_class_books_one_sub_block_per_group():
    """Pins ST-ARCH-001 item 7 — the block-length half.

    A 1-hour lesson taught to two branches in turn is two hours of the
    lecturer's week, not one. Get this wrong and the app promises a teacher a
    free hour they are in fact teaching in.
    """
    state = _grid()
    joint = _add(state, "Joint", "L1", ["A", "B"], duration=1, joint=True)
    seq = _add(state, "Seq", "L2", ["A", "B"], duration=1, joint=False)
    solo = _add(state, "Solo", "L3", ["A"], duration=1, joint=False)

    assert total_duration(joint) == 1
    assert total_duration(seq) == 2, (
        "a non-joint 2-target 1-hour class must occupy 2 consecutive slots — "
        "one sub-block per group")
    assert total_duration(solo) == 1, (
        "joint_session is meaningless for a single-target class and must not "
        "double its block")


# ===========================================================================
# 2. WHO IS BUSY, AND WHEN
# ===========================================================================
def test_a_sequential_class_only_occupies_each_group_in_its_own_sub_block():
    """Pins ST-ARCH-001 item 7 — the per-slot occupancy half.

    ``Seq`` teaches branch A at 09:00 and branch B at 10:00. Branch A is
    therefore free at 10:00 and branch B free at 09:00. The measured mutation
    ("every slot covers every target") makes both branches busy for both hours,
    so the validator rejects two placements a school can actually run.
    """
    state = _grid()
    seq = _add(state, "Seq", "L1", ["A", "B"], duration=1, joint=False)
    mark_placed(seq, "monday", "09:00", "R001")

    a_rival = _add(state, "RivalA", "L2", ["A"])
    b_rival = _add(state, "RivalB", "L3", ["B"])

    v = ConstraintValidator(state)

    # Sub-block 1 (09:00) belongs to branch A; sub-block 2 (10:00) to branch B.
    assert v.check_placement(a_rival, "monday", "09:00", "R002") is False, (
        "branch A is being taught at 09:00 — a second Year-1/A lesson in that "
        "hour double-books the students")
    assert v.check_placement(b_rival, "monday", "10:00", "R002") is False, (
        "branch B is being taught at 10:00 — a second Year-1/B lesson in that "
        "hour double-books the students")

    # ...and the halves the sequential class does NOT occupy stay open.
    assert v.check_placement(a_rival, "monday", "10:00", "R002") is True, (
        "branch A is not in the room at 10:00 (that sub-block belongs to "
        "branch B), so a Year-1/A lesson must be allowed there; refusing it "
        "is the collapse of sequential semantics into joint ones, and it costs "
        "the user placements the timetable had room for")
    assert v.check_placement(b_rival, "monday", "09:00", "R002") is True, (
        "branch B is not in the room at 09:00, so a Year-1/B lesson must be "
        "allowed there")


def test_a_joint_class_occupies_every_group_for_every_hour_of_its_block():
    """Pins ST-ARCH-001 item 7 — the joint half, and the control.

    The complement of the test above, and what stops a "fix" that simply
    narrows every class to one target per slot. A joint 2-hour lesson has both
    branches in the room for both hours; nothing else may be scheduled against
    either of them.
    """
    state = _grid()
    joint = _add(state, "Joint", "L1", ["A", "B"], duration=2, joint=True)
    mark_placed(joint, "monday", "09:00", "R001")

    a_rival = _add(state, "RivalA", "L2", ["A"])
    b_rival = _add(state, "RivalB", "L3", ["B"])

    v = ConstraintValidator(state)

    assert total_duration(joint) == 2, (
        "a joint class's block is its duration, not duration x targets")
    for slot in ("09:00", "10:00"):
        assert v.check_placement(a_rival, "monday", slot, "R002") is False, (
            f"Year-1/A is in a joint lesson at {slot} and must not be "
            "double-booked")
        assert v.check_placement(b_rival, "monday", slot, "R002") is False, (
            f"Year-1/B is in a joint lesson at {slot} and must not be "
            "double-booked")
    # 11:00 is past the block: both branches are free again. Without this the
    # test above would pass for an implementation that books every group for
    # the whole week.
    assert v.check_placement(a_rival, "monday", "11:00", "R002") is True
    assert v.check_placement(b_rival, "monday", "11:00", "R002") is True


def test_the_search_space_offered_to_a_group_matches_who_is_actually_busy():
    """Pins ST-ARCH-001 item 7 — the candidate-generator half.

    ``check_placement`` is the veto; ``CandidateGenerator.generate`` is the list
    of cells the optimizer ever considers. If the two disagree about who a
    sequential class makes busy, the engine either never offers a legal cell
    (lessons go unplaced for no stated reason) or offers one the validator will
    reject a moment later (ST-SCHED-009's "rejection the UI cannot explain").

    Asserted as agreement between the two, in one process, rather than as a
    literal list of cells — so it survives any re-ordering of the generator.
    """
    from scheduler_app.core.candidate_generator import CandidateGenerator

    state = _grid()
    seq = _add(state, "Seq", "L1", ["A", "B"], duration=1, joint=False)
    mark_placed(seq, "monday", "09:00", "R001")
    a_rival = _add(state, "RivalA", "L2", ["A"])

    v = ConstraintValidator(state)
    generator = CandidateGenerator(state, v)
    offered = set(generator.generate(a_rival))

    assert offered, "the generator offered nothing at all — nothing below means anything"
    for day, slot, room in offered:
        assert v.check_placement(a_rival, day, slot, room) is True, (
            f"the generator offered {(day, slot, room)} for a Year-1/A lesson "
            "and the validator rejects it")
    assert ("monday", "10:00", "R002") in offered, (
        "10:00 is branch B's sub-block, so Year-1/A is free then and the "
        "optimizer must be allowed to use that hour")
    assert not any(slot == "09:00" for _d, slot, _r in offered), (
        "09:00 is branch A's own sub-block; offering it invites a "
        "double-booking the validator will reject")


# ===========================================================================
# 3. THE FIXTURES ADMIT WHAT THEY DO NOT COVER
# ===========================================================================
def test_the_shared_presets_still_contain_no_sequential_class():
    """Guards ST-ARCH-001 item 7's blast radius (no finding ID).

    Measured in Phase 7: ``tiny``/``small``/``normal`` hold 0, 4 and 14
    multi-target classes and **zero** non-joint ones, which is why collapsing
    sequential semantics left the whole CI lane green. This module is now the
    only thing standing behind that branch.

    The assertion is deliberately the *current* fact rather than a wish. If
    someone teaches ``dataset_gen`` to emit sequential classes, this test goes
    red and the reader is sent here to decide whether the invariants suite has
    picked up the coverage — at which point the right move is to delete this
    test, not to work around it.
    """
    from _support.dataset_gen import make_preset

    for preset in ("tiny", "small", "normal"):
        state = make_preset(preset, seed=42)
        sequential = [c for c in state["classes"]
                      if not c.get("joint_session", True)
                      and len(c.get("targets", [])) > 1]
        assert sequential == [], (
            f"`{preset}` now contains {len(sequential)} sequential classes. "
            "That is good news — the shared presets finally exercise the "
            "non-joint branch — but it means this module is no longer the only "
            "cover for it. Check what the invariants suite now asserts and "
            "retire this guard deliberately.")


@pytest.mark.parametrize("joint", [True, False])
def test_a_sequential_class_that_does_not_fit_the_day_is_refused(joint):
    """Pins ST-ARCH-001 item 7 — the overflow half.

    A 2-target sequential class needs twice the room on the grid that its
    ``duration`` field suggests. Started too late in the day it runs off the
    end, and the validator must say so rather than silently booking a slot that
    does not exist (ST-SCHED-004).
    """
    state = _grid()
    cls = _add(state, "Late", "L1", ["A", "B"], duration=2, joint=joint)
    v = ConstraintValidator(state)

    # Block length is 2 when joint, 4 when sequential; the grid holds 4 slots.
    last_legal = "11:00" if joint else "09:00"
    assert v.check_placement(cls, "monday", last_legal, "R001") is True
    too_late = state["slots"][state["slots"].index(last_legal) + 1]
    assert v.check_placement(cls, "monday", too_late, "R001") is False, (
        f"a {'joint' if joint else 'sequential'} class starting at {too_late} "
        "runs past the end of the day and must be refused")
