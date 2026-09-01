"""Pair-specific student-group overlap permissions."""

from scheduler_app.core.constraint_validator import ConstraintValidator
from scheduler_app.core.cpsat_scheduler import CPSATScheduler, HAS_ORTOOLS
from scheduler_app.core.logic import (
    find_schedule_conflicts,
    student_overlap_allowed,
    student_targets_conflict,
)
from scheduler_app.core.models import (
    COURSE_REQUIREMENT_ELECTIVE,
    COURSE_REQUIREMENT_REQUIRED,
    LOCATION_ONLINE,
    STUDENT_OVERLAP_ELECTIVES_ONLY,
    STUDENT_OVERLAP_SAME_GROUP,
    mark_placed,
    new_class,
    new_state,
)


def _state():
    state = new_state()
    state["days"] = ["monday"]
    state["slots"] = ["09:00"]
    state["classrooms"] = ["R1", "R2"]
    state["classroom_capacities"] = {"R1": 0, "R2": 0}
    state["years"] = {"1": ["A"]}
    state["lecturers"] = ["L1", "L2"]
    return state


def _lesson(code, lecturer, *, requirement=COURSE_REQUIREMENT_REQUIRED,
            group="", policy=STUDENT_OVERLAP_SAME_GROUP,
            location=LOCATION_ONLINE):
    cls = new_class()
    cls["class_code"] = cls["name"] = code
    cls["lecturer"] = lecturer
    cls["targets"] = [{"year": "1", "branch": "A"}]
    cls["location_type"] = location
    cls["course_requirement"] = requirement
    cls["student_overlap_group"] = group
    cls["student_overlap_policy"] = policy if group else "never"
    return cls


def test_same_group_permission_is_pair_specific():
    aes_a = _lesson("AES-A", "L1", group="AES")
    aes_b = _lesson("AES-B", "L2", group="AES")
    ordinary = _lesson("CORE", "L2")

    assert student_overlap_allowed(aes_a, aes_b)
    assert not student_targets_conflict(
        aes_a, aes_a["targets"], aes_b, aes_b["targets"])
    assert student_targets_conflict(
        aes_a, aes_a["targets"], ordinary, ordinary["targets"])


def test_elective_only_permission_allows_only_s_s_pairs():
    elective_a = _lesson(
        "S1", "L1", requirement=COURSE_REQUIREMENT_ELECTIVE,
        group="YIDE-MA", policy=STUDENT_OVERLAP_ELECTIVES_ONLY)
    elective_b = _lesson(
        "S2", "L2", requirement=COURSE_REQUIREMENT_ELECTIVE,
        group="YIDE-MA", policy=STUDENT_OVERLAP_ELECTIVES_ONLY)
    required = _lesson(
        "Z", "L2", requirement=COURSE_REQUIREMENT_REQUIRED,
        group="YIDE-MA", policy=STUDENT_OVERLAP_ELECTIVES_ONLY)

    assert student_overlap_allowed(elective_a, elective_b)
    assert not student_overlap_allowed(elective_a, required)
    assert not student_overlap_allowed(required, required)


def test_validator_and_conflict_report_accept_permitted_target_overlap():
    state = _state()
    first = _lesson("AES-A", "L1", group="AES")
    second = _lesson("AES-B", "L2", group="AES")
    mark_placed(first, "monday", "09:00", None)
    state["classes"] = [first, second]

    validator = ConstraintValidator(state)
    assert validator.check_placement(second, "monday", "09:00", None)

    mark_placed(second, "monday", "09:00", None)
    assert find_schedule_conflicts(state) == []


def test_lecturer_and_room_conflicts_are_never_waived():
    state = _state()
    first = _lesson("A", "L1", group="PRACTICE")
    second = _lesson("B", "L1", group="PRACTICE")
    mark_placed(first, "monday", "09:00", None)
    state["classes"] = [first, second]
    assert not ConstraintValidator(state).check_placement(
        second, "monday", "09:00", None)

    first["lecturer"] = "L1"
    second["lecturer"] = "L2"
    first["location_type"] = "face_to_face"
    second["location_type"] = "face_to_face"
    mark_placed(first, "monday", "09:00", "R1")
    assert not ConstraintValidator(state).check_placement(
        second, "monday", "09:00", "R1")


def test_cpsat_can_place_two_permitted_lessons_in_one_target_cell():
    if not HAS_ORTOOLS:
        return
    state = _state()
    state["classes"] = [
        _lesson("AES-A", "L1", group="AES"),
        _lesson("AES-B", "L2", group="AES"),
    ]

    placed, unplaced, info = CPSATScheduler(
        state, time_limit=3.0, seed=20260101, require_all=True).solve()

    assert info["status"] in ("OPTIMAL", "FEASIBLE")
    assert len(placed or []) == 2, unplaced
