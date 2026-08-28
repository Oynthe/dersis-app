"""One hard-constraint validator — ST-ARCH-004, ST-SCHED-007, ST-SCHED-009.

DERSİS decides "may this lesson sit in this cell?" in four different places, and
they do not agree:

**A — the authority.** ``scheduler_app/core/constraint_validator.py``,
``ConstraintValidator``: ``respects_constraints`` (:51), ``check_placement``
(:90), ``check_placement_explained`` (:116), ``find_conflicts`` (:212). It is
occupancy-map based and checks grid membership, allow/exclude days and times,
required/excluded rooms, room capacity, and lecturer availability across the
class's *whole* duration.

**B — the deprecated weaker pair** in ``scheduler_app/core/logic.py``:
``respects_constraints`` (its own docstring said ``.. deprecated::``) skipped
grid membership and lecturer availability entirely and only checked room
capacity when a ``state`` was handed in; ``find_conflicts`` checks occupancy
and availability but explicitly *not* the class's own constraints. Production
drag-and-drop and the class editor used this pair, through
``SchedulingWorkflow.find_drop_classroom``, ``validate_drop_constraints``,
``apply_class_edit`` and ``validate_placements_after_edit``.

Phase 3 routed all four of those through ``ConstraintValidator``;
**Phase 6 (ST-ARCH-011) deleted ``logic.respects_constraints`` outright**, so
the weaker half of the pair no longer exists to be called back. ``logic``
still owns ``find_conflicts``, which is live and deliberately narrower —
Trap 3 below is about exactly that.

**C — the legacy solver family**, formerly in ``logic.py``:
``_check_placement_fast``, ``_get_valid_slots`` and ``_solve_backtrack``,
driving ``batch_schedule``, ``auto_place_class`` and ``reschedule_all``. It
filtered candidate cells and then tested them with ``_check_placement_fast``,
which looked at occupancy only — so lecturer availability was never consulted
anywhere in that pipeline.

**Deleted in Phase 6 (ST-ARCH-011).** Phase 3 had already reduced the three
entry points to one-line forwards onto the optimized engine, so the divergence
was closed; Phase 6 removed the ~200 lines. Section 5 below now drives
``optimized_reschedule_all`` / ``optimized_auto_place`` /
``optimized_batch_schedule`` directly, which is what it was reaching through
the forwards anyway.

**D — CP-SAT's independent re-encoding** in ``cpsat_scheduler.py``. Deliberately
out of scope for this module; another agent owns it.

What this module pins
---------------------
1. ``ConstraintValidator`` is the yardstick. Section 2 asserts its verdict on a
   hand-built case matrix *first*, so that when a drag-and-drop assertion later
   disagrees with it we know which of the two is wrong.
2. The real drag-and-drop path (``validate_drop`` → ``find_drop_classroom`` →
   ``validate_drop_constraints`` → the conflicts gate, exactly as
   ``ui/app.py::_execute_drop`` at :3910-3984 sequences them) must reach the
   authority's verdict on every cell.
3. The class editor must not leave a lesson parked on a cell its own freshly
   edited constraints forbid (ST-ARCH-004's sharpest user-visible edge).
4. The solver entry points must not place a lecturer who is unavailable and
   must not move a ``protection="locked"`` lesson (ST-SCHED-007).
5. ``check_placement`` returning False must always be *explainable*
   (ST-SCHED-009).

How to read a failure here
--------------------------
Everything here is live. Drag-and-drop (§3) and the class editor (§4) run on
every mouse gesture; the §5 entry points are what Generate calls.

This was not always so. Until Phase 6, §5 drove the deprecated
``batch_schedule`` / ``auto_place_class`` / ``reschedule_all`` names, and a
failure there was **latent** — a loaded gun rather than a wound, since nothing
called them but one unused import. ST-ARCH-011 unloaded the gun by deleting
them, so those cases now point at the optimized engine and a failure is
something a user hits today.

Traps this module has to defend against
---------------------------------------
**Trap 1 — "they agree because they both say no."** An equivalence assertion
over a matrix of invalid placements is satisfied by two validators that reject
everything, and would survive ``check_placement`` being stubbed to
``return False``. Every matrix here therefore carries cases whose ground-truth
answer is *valid*, ``test_case_matrix_is_not_all_negative`` asserts that in one
place, and ``test_drop_harness_can_say_both_yes_and_no`` proves the drop harness
itself is not stuck on one answer.

**Trap 2 — "nothing was placed, so nothing was misplaced."** "The legacy solver
must not place an unavailable lecturer" is vacuously true if the solver placed
nothing at all. Every §5 test therefore carries a companion class with an
unrestricted lecturer that the same call *must* place.

**Trap 3 — self-conflict.** ``logic.find_conflicts`` skips the candidate by
object identity (``existing is candidate``, logic.py:255) while
``ConstraintValidator`` skips it only via an explicit ``exclude_ids``. Comparing
the two on an already-placed class without passing ``exclude_ids`` produces a
fake divergence. ``_authority`` below always excludes the class under test.
"""
import pytest

from scheduler_app.core.constraint_validator import ConstraintValidator
from scheduler_app.core.models import (
    needs_physical_room,
    cls_key,
    get_room_candidates,
    mark_placed,
    new_class,
    new_lecturer_availability,
    new_state,
)
from scheduler_app.core.workflow import SchedulingWorkflow
import scheduler_app.core.logic as engine


_T_A = {"year": "Year-1", "branch": "A"}
_T_B = {"year": "Year-1", "branch": "B"}


# ---------------------------------------------------------------------------
# Hand-built fixtures. No optimizer, no Qt, no storage.
# ---------------------------------------------------------------------------
def _grid():
    """A 2-day x 3-slot x 2-room grid, small enough to reason about by hand.

    R001 has capacity 0 ("unknown" — treated as unlimited by
    ``models.room_fits_class``); R002 caps at 10 so capacity cases have
    something to bite on.
    """
    state = new_state()
    state["days"] = ["monday", "tuesday"]
    state["slots"] = ["09:00", "10:00", "11:00"]
    state["classrooms"] = ["R001", "R002"]
    state["classroom_capacities"] = {"R001": 0, "R002": 10}
    state["lecturers"] = ["Lect-A", "Lect-B", "Lect-C"]
    state["years"] = {"Year-1": ["A", "B"]}
    state["lecturer_availability"] = {}
    state["classes"] = []
    return state


def _mk(state, name, lecturer="Lect-A", duration=1, target=_T_A,
        participants=0, **fields):
    """Create a class, append it to *state* and return it."""
    cls = new_class()
    cls["class_code"] = name
    cls["name"] = name
    cls["lecturer"] = lecturer
    cls["targets"] = [dict(target)]
    cls["duration"] = duration
    cls["participants"] = participants
    cls.update(fields)
    state["classes"].append(cls)
    return cls


def _unavailable(state, lecturer, **windows):
    """Give *lecturer* an availability window (excluded_days, allowed_hours, ...)."""
    avail = new_lecturer_availability()
    avail.update(windows)
    state.setdefault("lecturer_availability", {})[lecturer] = avail


def _authority(state, cls):
    """A ConstraintValidator that excludes *cls* itself (Trap 3)."""
    return ConstraintValidator(state, exclude_ids={cls_key(cls)})


def _authority_verdict(state, cls, day, slot):
    """(valid, room) — does the authority accept this cell for ANY legal room?

    Scans exactly ``models.get_room_candidates``, which yields the ``[None]``
    sentinel for classes that need no physical room, so online and
    lecturer-office lessons are judged on time alone.
    """
    validator = _authority(state, cls)
    for room in get_room_candidates(state, cls):
        if validator.check_placement(cls, day, slot, room):
            return True, room
    return False, None


def _drop_verdict(state, cls, day, slot):
    """(valid, room, stage) — the production drag-and-drop verdict.

    Mirrors ``ui/app.py::_execute_drop`` (:3910-3984) phase for phase: basic
    validation, then room selection, then classroom-level constraints, then the
    conflicts gate. Everything below the QMessageBox calls is reproduced; the
    QMessageBox calls themselves are the only thing left out, which is why this
    needs no QApplication.
    """
    validation = SchedulingWorkflow.validate_drop(state, cls, day, slot)
    if not validation.valid:
        return False, None, "validate_drop"
    room, conflicts = SchedulingWorkflow.find_drop_classroom(
        state, cls, day, slot)
    # `room is None` is the correct answer for an online / lecturer-office
    # lesson: get_room_candidates yields the None sentinel for anything that
    # needs no room. Only a face-to-face lesson has failed when it gets None.
    # ui/app.py guards this the same way, in both _get_drop_classroom and
    # _execute_drop (ST-ARCH-004, Phase 3).
    if room is None and needs_physical_room(cls):
        return False, None, "no_compatible_classrooms"
    constraint_check = SchedulingWorkflow.validate_drop_constraints(
        state, cls, day, slot, room)
    if not constraint_check.valid:
        return False, room, "validate_drop_constraints"
    if conflicts:
        return False, room, "conflicts"
    return True, room, "accepted"


# ---------------------------------------------------------------------------
# The case matrix. Each builder returns (state, cls, day, slot); `expected` is
# the ground truth, derived by hand from the finding text — NOT from whatever
# either implementation happens to answer.
# ---------------------------------------------------------------------------
def _case_lecturer_fully_unavailable():
    state = _grid()
    cls = _mk(state, "Fully-Unavailable")
    _unavailable(state, "Lect-A", excluded_days=["monday", "tuesday"])
    return state, cls, "monday", "09:00"


def _case_lecturer_leaves_midblock():
    state = _grid()
    cls = _mk(state, "Midblock", duration=2)
    _unavailable(state, "Lect-A", excluded_hours=["10:00"])
    return state, cls, "monday", "09:00"


def _case_off_grid_day():
    state = _grid()
    return state, _mk(state, "Ghost-Day"), "saturday", "09:00"


def _case_off_grid_hour():
    state = _grid()
    return state, _mk(state, "Ghost-Hour"), "monday", "17:00"


def _case_required_room_missing():
    state = _grid()
    cls = _mk(state, "Needs-Deleted-Room", required_classrooms=["R999"])
    return state, cls, "monday", "09:00"


def _case_required_room_present():
    state = _grid()
    cls = _mk(state, "Needs-R002", required_classrooms=["R002"])
    return state, cls, "monday", "09:00"


def _case_capacity_too_small_everywhere():
    state = _grid()
    state["classroom_capacities"] = {"R001": 20, "R002": 10}
    cls = _mk(state, "Too-Many-Students", participants=40)
    return state, cls, "monday", "09:00"


def _case_excluded_time():
    state = _grid()
    cls = _mk(state, "Not-At-Nine", excluded_times=["09:00"])
    return state, cls, "monday", "09:00"


def _case_day_not_allowed():
    state = _grid()
    cls = _mk(state, "Tuesdays-Only", allowed_days=["tuesday"])
    return state, cls, "monday", "09:00"


def _case_duration_overflow():
    state = _grid()
    cls = _mk(state, "Three-Hours", duration=3)
    return state, cls, "monday", "11:00"


def _case_lecturer_already_busy():
    state = _grid()
    blocker = _mk(state, "Blocker")
    mark_placed(blocker, "monday", "09:00", "R001")
    cls = _mk(state, "Same-Lecturer", target=_T_B)
    return state, cls, "monday", "09:00"


def _case_clean():
    state = _grid()
    return state, _mk(state, "Perfectly-Fine"), "monday", "09:00"


def _case_online_with_stale_required_room():
    """An online lesson still carrying a required classroom that was deleted.

    Reachable in one gesture: set a required classroom, switch the lesson to
    online, or delete the room in Setup.
    """
    state = _grid()
    cls = _mk(state, "Online-Stale-Room",
              location_type="online", required_classrooms=["R999"])
    return state, cls, "monday", "09:00"


def _case_online_with_every_room_excluded():
    state = _grid()
    cls = _mk(state, "Online-All-Excluded",
              location_type="online", excluded_classrooms=["R001", "R002"])
    return state, cls, "monday", "09:00"


def _case_office_with_every_room_excluded():
    state = _grid()
    cls = _mk(state, "Office-All-Excluded",
              location_type="lecturer_office",
              excluded_classrooms=["R001", "R002"])
    return state, cls, "monday", "09:00"


# (id, builder, expected_valid, drop_path_agrees_today)
_MATRIX = [
    ("lecturer_fully_unavailable", _case_lecturer_fully_unavailable, False, True),
    ("lecturer_leaves_midblock", _case_lecturer_leaves_midblock, False, True),
    ("off_grid_day", _case_off_grid_day, False, True),
    ("off_grid_hour", _case_off_grid_hour, False, True),
    ("required_room_missing", _case_required_room_missing, False, True),
    ("required_room_present", _case_required_room_present, True, True),
    ("capacity_too_small_everywhere", _case_capacity_too_small_everywhere, False, True),
    ("excluded_time", _case_excluded_time, False, True),
    ("day_not_allowed", _case_day_not_allowed, False, True),
    ("duration_overflow", _case_duration_overflow, False, True),
    ("lecturer_already_busy", _case_lecturer_already_busy, False, True),
    ("clean", _case_clean, True, True),
    ("online_stale_required_room", _case_online_with_stale_required_room, True, False),
    ("online_every_room_excluded", _case_online_with_every_room_excluded, True, False),
    ("office_every_room_excluded", _case_office_with_every_room_excluded, True, False),
]

# ST-ARCH-004 fixed in Phase 3. The drag-and-drop path now reaches every
# verdict through ConstraintValidator (validate_drop, find_drop_classroom,
# validate_drop_constraints and check_drop_valid all share one validator), and
# find_drop_classroom asks models.get_room_candidates rather than filtering
# state["classrooms"] by hand — so a lesson that needs no room is no longer
# refused every cell once its (meaningless) room list empties. The matrix below
# is therefore unmarked: the drop path and the authority agree on every case.

def _matrix_params(pin_divergences):
    """Build the parametrize list.

    *pin_divergences* False gives an unmarked matrix (used for the authority,
    which is right about every case today); True xfails the cases where the
    drag-and-drop path is known to diverge.
    """
    params = []
    for case_id, builder, expected, drop_agrees_today in _MATRIX:
        # `drop_agrees_today` records which cases the drag-and-drop path used
        # to get wrong; every case agrees since Phase 3, so nothing is marked.
        # The field is kept because it documents the divergence set.
        params.append(pytest.param(builder, expected, id=case_id))
    return params


# ===========================================================================
# 1. ANTI-VACUITY — prove the harness and the matrix can say both answers
# ===========================================================================
def test_case_matrix_is_not_all_negative():
    """Guards this module against Trap 1 (no finding ID).

    If every case in ``_MATRIX`` expected "invalid", the equivalence tests
    below would be satisfied by two validators that reject everything — a user
    could be locked out of the whole timetable and this file would stay green.
    """
    valid_cases = [c for c in _MATRIX if c[2] is True]
    invalid_cases = [c for c in _MATRIX if c[2] is False]
    assert len(valid_cases) >= 3, (
        "the matrix needs several placements that are genuinely VALID, "
        f"got {[c[0] for c in valid_cases]}")
    assert len(invalid_cases) >= 6, (
        "the matrix needs a spread of genuinely INVALID placements, "
        f"got {[c[0] for c in invalid_cases]}")
    physical = [c for c in valid_cases if "online" not in c[0] and "office" not in c[0]]
    assert physical, (
        "at least one VALID case must be an ordinary face-to-face lesson, "
        "otherwise the 'valid' half of the matrix only exercises the "
        "non-physical-room code path")


def test_drop_harness_can_say_both_yes_and_no():
    """Guards this module against Trap 1 (no finding ID).

    ``_drop_verdict`` reproduces four decision phases from ``ui/app.py``. If a
    refactor made it wired-shut in either direction, every drag-and-drop
    assertion below would become meaningless without going red.
    """
    state, cls, day, slot = _case_clean()
    assert _drop_verdict(state, cls, day, slot)[0] is True

    state, cls, day, slot = _case_off_grid_day()
    assert _drop_verdict(state, cls, day, slot)[0] is False


def test_authority_harness_can_say_both_yes_and_no():
    """Guards this module against Trap 1 (no finding ID).

    Same argument for the yardstick: a ``ConstraintValidator`` stubbed to
    ``return False`` must not be able to make this file pass.
    """
    state, cls, day, slot = _case_clean()
    assert _authority_verdict(state, cls, day, slot)[0] is True

    state, cls, day, slot = _case_off_grid_day()
    assert _authority_verdict(state, cls, day, slot)[0] is False


# ===========================================================================
# 2. THE YARDSTICK — ConstraintValidator's verdict on the whole matrix
#    (all passing today: regression guards against a "unification" that
#     unifies downwards, onto the weaker implementation)
# ===========================================================================
@pytest.mark.parametrize("builder,expected", _matrix_params(pin_divergences=False))
def test_authority_matches_ground_truth(builder, expected):
    """Regression guard for ST-ARCH-004 (passes today — keep it passing).

    ``ConstraintValidator`` is the implementation Phase 3 unifies *onto*. If a
    case here flips, the merge moved the authority towards the deprecated
    validator instead of the other way round, and the user gets a timetable
    with a hard-constraint violation the app believes is legal.
    """
    state, cls, day, slot = builder()
    valid, room = _authority_verdict(state, cls, day, slot)
    assert valid is expected, (
        f"authority said valid={valid} (room={room!r}) for "
        f"{cls['name']} at {day}/{slot}; ground truth is {expected}")


# ===========================================================================
# 3. DIFFERENTIAL — production drag-and-drop vs the authority
# ===========================================================================
@pytest.mark.parametrize("builder,expected", _matrix_params(pin_divergences=True))
def test_drag_and_drop_matches_the_authority(builder, expected):
    """ST-ARCH-004 — drag-and-drop must reach the authority's verdict.

    A failure means the user is told a lesson cannot go in a cell that is
    perfectly legal (or, in the other direction, is allowed to drop a lesson
    the optimizer would refuse) purely because the drop path routes through
    ``logic.respects_constraints`` and a room picker that predates online
    lessons.
    """
    state, cls, day, slot = builder()
    authority_valid, _ = _authority_verdict(state, cls, day, slot)
    drop_valid, room, stage = _drop_verdict(state, cls, day, slot)

    assert authority_valid is expected, (
        "matrix ground truth disagrees with the authority — fix the matrix, "
        "not the assertion below")
    assert drop_valid is expected, (
        f"drag-and-drop said valid={drop_valid} (stopped at {stage!r}, "
        f"room={room!r}) for {cls['name']} at {day}/{slot}; "
        f"ConstraintValidator says {authority_valid}")


def test_drag_and_drop_agrees_with_the_authority_on_every_cell_of_the_grid():
    """ST-ARCH-004 — the two verdicts must agree cell by cell, not just on
    the one cell a hand-written case happens to pick.

    A failure means whole columns or rows of the timetable are silently
    undroppable (or wrongly droppable) for some lesson.
    """
    state = _grid()
    blocker = _mk(state, "Blocker", lecturer="Lect-B", target=_T_B)
    mark_placed(blocker, "monday", "10:00", "R001")
    victim = _mk(state, "Victim", lecturer="Lect-B", duration=2, target=_T_B)
    _unavailable(state, "Lect-B", excluded_hours=["11:00"])

    disagreements = []
    accepted = 0
    for day in state["days"]:
        for slot in state["slots"]:
            authority_valid, _ = _authority_verdict(state, victim, day, slot)
            drop_valid, room, stage = _drop_verdict(state, victim, day, slot)
            accepted += 1 if authority_valid else 0
            if authority_valid != drop_valid:
                disagreements.append(
                    (day, slot, authority_valid, drop_valid, stage))

    # Trap 1: on this board the authority must accept SOMETHING, otherwise
    # "the two agree" would only mean "both refuse the entire timetable".
    assert accepted > 0, (
        "the authority rejected every cell — this scenario proves nothing")
    assert disagreements == [], (
        "drag-and-drop and ConstraintValidator disagree on "
        f"{len(disagreements)} of {len(state['days']) * len(state['slots'])} "
        f"cells: {disagreements}")


def test_drop_room_picker_returns_a_room_the_authority_would_accept():
    """ST-ARCH-004 — the room ``find_drop_classroom`` hands back must be one
    ``models.get_room_candidates`` considers legal for the lesson.

    A failure means a drag-and-drop commits a *physical classroom* onto an
    online lesson (``ui/app.py:3979`` calls ``mark_placed(cls, day, slot,
    room)`` unconditionally), while ``apply_reschedule`` stores ``None`` for the
    same lesson — so the same online lesson shows a room or no room depending
    on whether the user dragged it or the optimizer placed it, and exports and
    room-load analytics disagree with the timetable.
    """
    state = _grid()
    cls = _mk(state, "Online-Lesson", location_type="online")

    room, conflicts = SchedulingWorkflow.find_drop_classroom(
        state, cls, "monday", "09:00")
    candidates = get_room_candidates(state, cls)

    assert candidates == [None], (
        "precondition: get_room_candidates yields the None sentinel for a "
        f"lesson that needs no room, got {candidates!r}")
    assert room in candidates, (
        f"find_drop_classroom picked {room!r} for an online lesson; the "
        f"authority's candidate list is {candidates!r} (conflicts={conflicts})")


# ===========================================================================
# 4. THE CLASS EDITOR — the same weak pair, on a path that MUTATES state
# ===========================================================================
_EDITS = [
    ("allowed_days_now_excludes_current_day", {"allowed_days": ["tuesday"]}),
    ("excluded_days_now_covers_current_day", {"excluded_days": ["monday"]}),
    ("participants_now_exceed_room_capacity", {"participants": 99}),
    ("current_room_is_now_excluded", {"excluded_classrooms": ["R002"]}),
    ("required_room_is_now_somewhere_else", {"required_classrooms": ["R001"]}),
]


@pytest.mark.parametrize("edit", [e[1] for e in _EDITS], ids=[e[0] for e in _EDITS])
def test_editing_a_class_does_not_leave_it_on_a_now_illegal_cell(edit):
    """ST-ARCH-004 — after an edit, a lesson that is still placed must be
    legally placed.

    A failure means the user tightens a lesson's constraints in the edit dialog
    ("only Tuesdays", "40 students now"), DERSİS reports nothing, and the
    lesson stays on the Monday cell / in the too-small room it was already in —
    a hard-constraint violation the app itself introduced and will happily
    export.
    """
    state = _grid()
    state["classroom_capacities"] = {"R001": 0, "R002": 10}
    cls = _mk(state, "Edited", participants=5)
    mark_placed(cls, "monday", "09:00", "R002")

    updated = {k: cls[k] for k in (
        "class_code", "name", "lecturer", "targets", "duration",
        "participants", "location_type", "joint_session", "pinned",
        "pinned_day", "pinned_time", "pinned_classroom", "protection",
        "allowed_days", "allowed_times", "excluded_days", "excluded_times",
        "required_classrooms", "excluded_classrooms")}
    updated.update(edit)

    result = SchedulingWorkflow.apply_class_edit(state, cls, updated)

    # Trap 1: this assertion is vacuous if the edit cleared the placement for
    # some unrelated reason, so record which branch we are in.
    if not cls["placed"]:
        assert result.placement_cleared, (
            "the lesson was unplaced but apply_class_edit did not report it")
        return

    validator = _authority(state, cls)
    assert validator.check_placement(
        cls, cls["placed_day"], cls["placed_time"], cls["placed_classroom"]
    ), (
        f"after {edit}, {cls['name']} is still placed at "
        f"{cls['placed_day']}/{cls['placed_time']}/{cls['placed_classroom']} "
        "but ConstraintValidator rejects that cell; reasons: "
        + repr(validator.check_placement_explained(
            cls, cls["placed_day"], cls["placed_time"],
            cls["placed_classroom"])[1]))


_HARMLESS_EDITS = [
    ("rename_only", {"name": "Renamed"}),
    ("allowed_days_still_covers_current_day", {"allowed_days": ["monday"]}),
    ("excluded_day_it_is_not_on", {"excluded_days": ["tuesday"]}),
    ("participants_still_fit", {"participants": 1}),
]


@pytest.mark.parametrize(
    "edit", [e[1] for e in _HARMLESS_EDITS],
    ids=[e[0] for e in _HARMLESS_EDITS])
def test_a_harmless_edit_leaves_the_lesson_where_it_was(edit):
    """ST-ARCH-004 — the other half of the edit contract.

    ``test_editing_a_class_does_not_leave_it_on_a_now_illegal_cell`` is
    satisfied by an ``apply_class_edit`` that unplaces the lesson on EVERY
    edit, including edits that leave the cell perfectly legal — its own escape
    hatch accepts the unplaced branch as a pass. Without this companion, the
    pair would permanently certify a bulk-unplace as correct.

    A failure means renaming a lesson, or re-stating a constraint it already
    satisfies, throws its placement away and silently hands the user back an
    unscheduled course.

    The self-conflict trap this guards against is real and specific: judging
    the edited lesson with a validator that still counts the lesson's own
    room, lecturer and student group as occupied makes every legal placement
    look like a clash, so every edit unplaces.
    """
    state = _grid()
    cls = _mk(state, "Lesson", lecturer="Lect-A", target=_T_A, participants=1)
    mark_placed(cls, "monday", "09:00", "R002")
    before = (cls["placed_day"], cls["placed_time"], cls["placed_classroom"])

    updated = dict(cls)
    updated.update(edit)
    result = SchedulingWorkflow.apply_class_edit(state, cls, updated)

    assert cls["placed"], (
        f"a harmless edit {edit!r} unplaced {cls['name']!r} from {before}; "
        "placement_cleared="
        f"{result.placement_cleared}")
    assert (cls["placed_day"], cls["placed_time"],
            cls["placed_classroom"]) == before
    assert not result.placement_cleared

    # And the whole-timetable sweep must agree with the per-edit decision.
    invalidated = SchedulingWorkflow.validate_placements_after_edit(state)
    assert cls["name"] not in invalidated, (
        "validate_placements_after_edit unplaced a lesson that "
        "apply_class_edit had just judged legal — the two disagree")



def test_placement_sweep_after_an_edit_catches_own_constraint_violations():
    """ST-ARCH-004 — the post-edit sweep must catch a lesson parked on a day
    its own constraints forbid.

    ``validate_placements_after_edit`` is the app's "did that edit break
    anything?" pass. A failure means it reports a clean bill of health for a
    timetable that already violates a hard constraint, so the warning the user
    needed never appears.
    """
    state = _grid()
    healthy = _mk(state, "Healthy", lecturer="Lect-B", target=_T_B)
    mark_placed(healthy, "tuesday", "09:00", "R001")
    broken = _mk(state, "Broken-Day", allowed_days=["tuesday"])
    mark_placed(broken, "monday", "09:00", "R002")
    starved = _mk(state, "Broken-Capacity", lecturer="Lect-C",
                  participants=99)
    mark_placed(starved, "monday", "10:00", "R002")

    invalidated = SchedulingWorkflow.validate_placements_after_edit(state)

    # Trap 1: the sweep must leave the legal lesson alone, so a fix that just
    # unplaces everything cannot make this test pass.
    assert healthy["placed"], (
        "the sweep unplaced a perfectly legal lesson — that is a different "
        "bug, and it would make the assertion below meaningless")
    assert set(invalidated) == {"Broken-Day", "Broken-Capacity"}, (
        f"sweep reported {invalidated!r}; 'Broken-Day' sits on a day its own "
        "allowed_days forbids and 'Broken-Capacity' has 99 students in a "
        "10-seat room")


# ===========================================================================
# 5. ST-SCHED-007 — the solver entry points
#
# NOTE ON LIVENESS, revised in Phase 6. These cases used to drive
# `logic.batch_schedule` / `auto_place_class` / `reschedule_all`, and carried a
# note saying a failure was LATENT: those names had no live caller, so they
# described what a user *would* get if the family were ever rewired.
#
# ST-ARCH-011 deleted the family, which removes the hazard rather than guarding
# it. And since Phase 3 the three names had been one-line forwards, so these
# tests were already exercising the optimized engine through an alias — the
# "legacy" in their name was the only thing legacy about them.
#
# They now name the optimized entry points directly. The assertions are
# unchanged and they are no longer latent: this is the engine that runs when a
# user clicks Generate.
# ===========================================================================
def _engine_placements(entry, state, classes):
    """Run one engine entry point; return {class name: (day, slot, room)}."""
    by_uid = {cls_key(c): c for c in state["classes"]}
    if entry == "optimized_reschedule_all":
        # The shim these cases used to call dropped the 4th value; the
        # optimized entry point also returns the run summary.
        placed, _unplaced, _changes, _summary = (
            engine.optimized_reschedule_all(state))
        return {c["name"]: (d, s, r) for c, d, s, r in placed}
    if entry == "optimized_batch_schedule":
        placed, _unplaced, _rescheduled = engine.optimized_batch_schedule(
            state, list(classes))
        return {c["name"]: (d, s, r) for c, d, s, r in placed}
    if entry == "optimized_auto_place":
        out = {}
        for cls in classes:
            placed_ok, placements, _rescheduled = engine.optimized_auto_place(
                state, cls)
            if placed_ok:
                for uid, position in placements.items():
                    out[by_uid[uid]["name"]] = position
        return out
    raise AssertionError(f"unknown entry point {entry!r}")


@pytest.mark.engine
@pytest.mark.parametrize(
    "entry", ["optimized_reschedule_all", "optimized_auto_place",
              "optimized_batch_schedule"])
def test_solvers_never_place_an_unavailable_lecturer(entry):
    """ST-SCHED-007 — the engine Generate runs (see the section note).

    A failure means that if this solver family is ever reconnected, DERSİS will
    hand a lecturer who marked the entire week unavailable a Monday 09:00
    lesson, and only the export or the lecturer themselves would notice.
    """
    state = _grid()
    blocked = _mk(state, "Never-Available", lecturer="Lect-A", target=_T_A)
    _unavailable(state, "Lect-A", excluded_days=["monday", "tuesday"])
    companion = _mk(state, "Ordinary", lecturer="Lect-B", target=_T_B)

    placements = _engine_placements(entry, state, [blocked, companion])

    # Trap 2: "the unavailable lecturer was not placed" is free if the solver
    # placed nobody. The companion has no restrictions at all and must land.
    assert "Ordinary" in placements, (
        f"{entry} placed nothing at all — the assertion below would be "
        f"vacuous. placements={placements!r}")

    validator = _authority(state, blocked)
    position = placements.get("Never-Available")
    assert position is None, (
        f"{entry} placed a fully unavailable lecturer at {position}; "
        "ConstraintValidator.check_placement for that cell returns "
        f"{validator.check_placement(blocked, position[0], position[1], position[2])}")


@pytest.mark.engine
def test_reschedule_all_never_moves_a_locked_class():
    """ST-SCHED-007 — the engine Generate runs (see the section note).

    "Fully locked" is the strongest promise the protection dropdown makes. A
    failure means the user locks a lesson to Tuesday 11:00, runs a global
    re-optimization, and finds it on Monday 09:00 — the one outcome the setting
    exists to prevent.
    """
    state = _grid()
    locked = _mk(state, "Locked", lecturer="Lect-A", target=_T_A,
                 protection="locked")
    mark_placed(locked, "tuesday", "11:00", "R002")
    movable = _mk(state, "Movable", lecturer="Lect-B", target=_T_B)
    mark_placed(movable, "monday", "09:00", "R001")

    placed, _unplaced, changes, _summary = engine.optimized_reschedule_all(state)
    placements = {c["name"]: (d, s, r) for c, d, s, r in placed}

    # Trap 2: a solver that returned nothing would satisfy "Locked did not
    # move" for free.
    assert len(placements) == 2, (
        f"reschedule_all returned {placements!r}; both lessons must come back "
        "or the assertion below proves nothing")

    moved = [c["cls"]["name"] for c in changes]
    assert placements["Locked"] == ("tuesday", "11:00", "R002"), (
        f"reschedule_all moved the locked lesson to {placements['Locked']}; "
        f"it reported these moves: {moved}")
    assert "Locked" not in moved


@pytest.mark.engine
def test_auto_place_class_never_displaces_a_locked_class():
    """ST-SCHED-007 — the engine Generate runs (see the section note).

    Adding one new lesson must not silently rearrange a lesson the user marked
    "fully locked". A failure means the same broken promise as
    ``test_reschedule_all_never_moves_a_locked_class``, but triggered by a much
    more ordinary action.

    Anti-vacuity note. This originally constrained the newcomer to exactly the
    locked lesson's cell, so that "the locked lesson moved" and "the newcomer
    was placed" were the same event — that is how the defect was first
    observed. Once phase 2 stopped treating locked classes as movable that
    scenario became unplaceable *by construction*: the only legal home for the
    newcomer was the cell that must not be vacated, so ``auto_place_class``
    correctly refused it and the guard could never fire again. The board below
    keeps the guard honest instead: `Movable` blocks the newcomer and can step
    aside, `Locked` is in the same timetable and must not, so the displacement
    pass provably runs AND the locked lesson provably survives it.
    """
    state = _grid()
    locked = _mk(state, "Locked", lecturer="Lect-A", target=_T_A,
                 protection="locked")
    mark_placed(locked, "monday", "09:00", "R001")

    # Shares lecturer, student group and room with the newcomer, so the
    # newcomer can only land once this one moves — and it is free to.
    movable = _mk(state, "Movable", lecturer="Lect-B", target=_T_B)
    mark_placed(movable, "monday", "09:00", "R002")

    newcomer = _mk(state, "Newcomer", lecturer="Lect-B", target=_T_B,
                   allowed_days=["monday"], allowed_times=["09:00"],
                   required_classrooms=["R002"])

    placed_ok, placements, _rescheduled = engine.optimized_auto_place(
        state, newcomer)

    assert placed_ok, (
        "auto_place_class refused the newcomer outright; this scenario must "
        "exercise the phase-2 displacement pass to prove anything")
    assert cls_key(movable) in placements or cls_key(newcomer) in placements, (
        "nothing moved and nothing was placed — the displacement pass did not "
        f"run: {placements!r}")

    moved = placements.get(cls_key(locked))
    assert moved is None, (
        f"auto_place_class displaced the locked lesson to {moved}; "
        "protection='locked' promises it will not be moved to make room for "
        "anything else")
    assert (locked["placed_day"], locked["placed_time"],
            locked["placed_classroom"]) == ("monday", "09:00", "R001")

@pytest.mark.engine
def test_batch_schedule_keeps_locked_classes_put():
    """Regression guard for ST-SCHED-007 (passes today — keep it passing).

    ``batch_schedule`` is the one member of the legacy family that already
    excludes ``protection == "locked"`` from its flexible set (logic.py:888-891).
    Phase 3 rewrites all three together; if this guard goes red, the rewrite
    dropped the one protection check that was already there and locked lessons
    become movable in the entry point that ``ui/dialogs.py`` actually imports.
    """
    state = _grid()
    locked = _mk(state, "Locked", lecturer="Lect-A", target=_T_A,
                 protection="locked")
    mark_placed(locked, "monday", "09:00", "R001")
    movable = _mk(state, "Movable", lecturer="Lect-B", target=_T_B)
    mark_placed(movable, "monday", "10:00", "R001")
    # Collides with Movable on both lecturer and student group, and can only
    # go where Movable is — so phase 2 has to run and shuffle something.
    newcomer = _mk(state, "Newcomer", lecturer="Lect-B", target=_T_B,
                   allowed_days=["monday"], allowed_times=["10:00"])

    placed, _unplaced, _rescheduled = engine.optimized_batch_schedule(state, [newcomer])
    placements = {c["name"]: (d, s, r) for c, d, s, r in placed}

    # Trap 2 again: prove the solver did real work.
    assert "Newcomer" in placements, (
        f"batch_schedule did not place the newcomer: {placements!r}")

    assert placements.get("Locked", ("monday", "09:00", "R001")) == (
        "monday", "09:00", "R001"), (
        f"batch_schedule moved the locked lesson to {placements['Locked']}")
    for name, position in placements.items():
        if name == "Locked":
            continue
        assert position != ("monday", "09:00", "R001"), (
            f"batch_schedule scheduled {name} on top of the locked lesson")


@pytest.mark.parametrize(
    "builder", [_case_lecturer_fully_unavailable, _case_lecturer_leaves_midblock],
    ids=["fully_unavailable", "leaves_midblock"])
def test_authority_rejects_the_availability_cases_the_legacy_family_accepts(builder):
    """Regression guard for ST-SCHED-007 (passes today — keep it passing).

    Establishes which side of the ST-SCHED-007 divergence is wrong: the
    authority already refuses both availability cases. If this guard ever goes
    red, "unification" moved the availability check out of
    ``ConstraintValidator`` instead of into the legacy solvers, and the app
    stops honouring lecturer availability everywhere at once.
    """
    state, cls, day, slot = builder()
    validator = _authority(state, cls)
    for room in state["classrooms"] + [None]:
        assert validator.check_placement(cls, day, slot, room) is False, (
            f"authority accepted {cls['name']} at {day}/{slot} in {room!r} "
            "despite the lecturer being unavailable")


# ===========================================================================
# 6. ST-SCHED-009 — a rejection the conflict UI cannot explain
# ===========================================================================
# ST-SCHED-009 fixed in Phase 3. ConstraintValidator.find_conflicts now tests
# lecturer availability across the whole block rather than at start_slot only,
# and ends with a backstop that appends a generic reason whenever
# check_placement rejects a placement it could not otherwise explain — so the
# list is never empty for a rejection, and the conflict UI always has something
# to show.

def test_find_conflicts_explains_a_midblock_availability_rejection():
    """ST-SCHED-009 — the audit's exact case.

    A duration-2 lesson whose lecturer is free at 09:00 but not at 10:00.
    ``check_placement`` refuses it (``respects_constraints`` walks the whole
    duration, constraint_validator.py:76-87) but ``find_conflicts`` only tests
    ``start_slot`` (:250), so it returns an empty list.

    A failure means the user drops a lesson, DERSİS refuses it, and the
    "why not?" panel is blank — there is nothing to read and nothing to fix.
    """
    state = _grid()
    cls = _mk(state, "Two-Hour-Lesson", duration=2)
    _unavailable(state, "Lect-A", excluded_hours=["10:00"])
    validator = _authority(state, cls)

    assert validator.check_placement(cls, "monday", "09:00", "R001") is False, (
        "precondition: the authority must reject this placement, otherwise "
        "there is no rejection to explain")

    conflicts = validator.find_conflicts(cls, "monday", "09:00", "R001")
    assert conflicts != [], (
        "find_conflicts returned no reason at all for a placement "
        "check_placement rejects")


def _invalid_placement_cases():
    """(id, state, cls, day, slot, room) for placements check_placement refuses."""
    cases = []

    state = _grid()
    cls = _mk(state, "Full", duration=1)
    _unavailable(state, "Lect-A", excluded_days=["monday"])
    cases.append(("lecturer_unavailable_all_day", state, cls, "monday", "09:00", "R001"))

    state = _grid()
    cls = _mk(state, "Mid", duration=2)
    _unavailable(state, "Lect-A", excluded_hours=["10:00"])
    cases.append(("lecturer_leaves_midblock", state, cls, "monday", "09:00", "R001"))

    state = _grid()
    cls = _mk(state, "Tail", duration=3)
    _unavailable(state, "Lect-A", allowed_hours=["09:00", "10:00"])
    cases.append(("lecturer_gone_by_last_hour", state, cls, "monday", "09:00", "R001"))

    state = _grid()
    cls = _mk(state, "Tue", allowed_days=["tuesday"])
    cases.append(("day_not_allowed", state, cls, "monday", "09:00", "R001"))

    state = _grid()
    cls = _mk(state, "NotNine", excluded_times=["09:00"])
    cases.append(("time_excluded", state, cls, "monday", "09:00", "R001"))

    state = _grid()
    cls = _mk(state, "NeedsR002", required_classrooms=["R002"])
    cases.append(("wrong_room", state, cls, "monday", "09:00", "R001"))

    state = _grid()
    cls = _mk(state, "TooBig", participants=99)
    cases.append(("room_too_small", state, cls, "monday", "09:00", "R002"))

    state = _grid()
    cls = _mk(state, "Ghost")
    cases.append(("off_grid_day", state, cls, "saturday", "09:00", "R001"))

    state = _grid()
    cls = _mk(state, "GhostHour")
    cases.append(("off_grid_hour", state, cls, "monday", "17:00", "R001"))

    state = _grid()
    blocker = _mk(state, "Blocker")
    mark_placed(blocker, "monday", "09:00", "R001")
    cls = _mk(state, "Clash", target=_T_B)
    cases.append(("lecturer_double_booked", state, cls, "monday", "09:00", "R002"))

    return cases


_009_XFAIL_IDS = {"lecturer_leaves_midblock", "lecturer_gone_by_last_hour"}


@pytest.mark.parametrize(
    "state,cls,day,slot,room",
    [pytest.param(
        *c[1:], id=c[0],
        )
     for c in _invalid_placement_cases()])
def test_a_rejected_placement_always_has_at_least_one_conflict(
        state, cls, day, slot, room):
    """ST-SCHED-009 — ``check_placement`` False must imply ``find_conflicts``
    non-empty.

    ``ConstraintValidator.find_conflicts`` is read by the LIVE batch scheduler
    (``logic.optimized_batch_schedule``, logic.py:1301 and :1349) to build the
    "why is this lesson unplaced?" string. An empty list there falls through to
    a generic ``tr("conflicts.batch_conflict")`` / ``tr("conflicts.
    pinned_conflict")`` placeholder, so a failure here means the user is told
    only "conflict" and never which lecturer is missing at which hour.
    """
    validator = _authority(state, cls)
    assert validator.check_placement(cls, day, slot, room) is False, (
        f"precondition: {cls['name']} at {day}/{slot}/{room} was supposed to "
        "be an INVALID placement, but check_placement accepted it — the "
        "implication below would be vacuous")

    conflicts = validator.find_conflicts(cls, day, slot, room)
    assert conflicts != [], (
        f"check_placement rejected {cls['name']} at {day}/{slot}/{room} but "
        "find_conflicts listed no reason")


@pytest.mark.parametrize(
    "state,cls,day,slot,room",
    [pytest.param(*c[1:], id=c[0]) for c in _invalid_placement_cases()])
def test_a_rejected_placement_always_has_at_least_one_explained_reason(
        state, cls, day, slot, room):
    """Regression guard for ST-SCHED-009 (passes today — keep it passing).

    ``check_placement_explained`` is the sibling of ``find_conflicts`` and, on
    this matrix, it already gets every case right — including the mid-block
    availability case the two xfail'd tests above pin. It is therefore the
    reference the Phase 3 fix should converge ``find_conflicts`` onto. If this
    guard goes red, the merge went the wrong way and the *good* explainer lost
    its coverage.
    """
    validator = _authority(state, cls)
    valid, reasons = validator.check_placement_explained(cls, day, slot, room)
    assert valid is False, (
        f"precondition: {cls['name']} at {day}/{slot}/{room} was supposed to "
        "be an INVALID placement")
    assert reasons != [], (
        f"check_placement_explained rejected {cls['name']} at "
        f"{day}/{slot}/{room} without naming a reason")


@pytest.mark.engine
def test_batch_scheduler_names_the_reason_a_pinned_lesson_could_not_be_placed():
    """ST-SCHED-009 — the live consequence, on the path the app really runs.

    ``logic.optimized_batch_schedule`` (the one ``SchedulingWorkflow`` calls at
    workflow.py:276 and :369) explains an unplaceable pinned lesson with
    ``validator.find_conflicts(...)`` and falls back to a generic string when
    that list is empty (logic.py:1301-1303, :1349-1351).

    A failure means the user pins a two-hour lesson at 09:00, the lecturer is
    away at 10:00, and the report says only "conflict with a pinned lesson" —
    it never says which lecturer is unavailable at which hour, so there is
    nothing to act on. Asserted against ``tr()`` rather than literal text, so
    this pins the *fallback*, not the wording.
    """
    from scheduler_app.core.logic import optimized_batch_schedule
    from scheduler_app.translations import tr

    state = _grid()
    cls = _mk(state, "Pinned-Two-Hour", duration=2,
              pinned=True, pinned_day="monday", pinned_time="09:00",
              pinned_classroom="R001")
    _unavailable(state, "Lect-A", excluded_hours=["10:00"])

    _placed, unplaced, _rescheduled = optimized_batch_schedule(state, [cls])

    # Trap 2: the lesson genuinely cannot be placed — that part is correct and
    # must stay true, or there is no reason string to inspect.
    reasons = {c["name"]: reason for c, reason in unplaced}
    assert "Pinned-Two-Hour" in reasons, (
        "the pinned lesson was placed despite its lecturer being away at "
        "10:00 — that is a different (worse) bug")

    generic = {tr("conflicts.batch_conflict"), tr("conflicts.pinned_conflict")}
    assert reasons["Pinned-Two-Hour"] not in generic, (
        "optimized_batch_schedule fell back to its generic placeholder "
        f"({reasons['Pinned-Two-Hour']!r}) because find_conflicts returned an "
        "empty list")


def test_find_conflicts_stays_quiet_on_a_legal_placement():
    """Regression guard for ST-SCHED-009 (passes today — keep it passing).

    The cheap way to make ``find_conflicts`` never return an empty list is to
    make it return something for everything. That would light the conflict
    panel up on a perfectly good timetable, so pin the other direction too.
    """
    state = _grid()
    cls = _mk(state, "Fine", duration=2)
    validator = _authority(state, cls)

    assert validator.check_placement(cls, "monday", "09:00", "R001") is True
    assert validator.find_conflicts(cls, "monday", "09:00", "R001") == []
    assert validator.check_placement_explained(
        cls, "monday", "09:00", "R001") == (True, [])
