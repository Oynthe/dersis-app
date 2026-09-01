"""Same lecturer/course sections keep one physical classroom."""

from scheduler_app.core.constraint_validator import ConstraintValidator
from scheduler_app.core.cpsat_scheduler import CPSATScheduler, HAS_ORTOOLS
from scheduler_app.core.logic import find_schedule_conflicts, find_valid_options
from scheduler_app.core.models import mark_placed, new_class, new_state


def _state():
    state = new_state()
    state["days"] = ["monday"]
    state["slots"] = ["09:00", "10:00"]
    state["classrooms"] = ["R1", "R2"]
    state["classroom_capacities"] = {"R1": 0, "R2": 0}
    state["years"] = {"4": ["A", "B", "C"]}
    state["lecturers"] = ["X"]
    return state


def _section(branch, *, enabled=False):
    cls = new_class()
    cls["class_code"] = "Z101"
    cls["name"] = f"Z dersi [{branch}]"
    cls["lecturer"] = "X"
    cls["targets"] = [{"year": "4", "branch": branch}]
    cls["keep_same_classroom"] = enabled
    return cls


def test_enabling_one_section_constrains_every_matching_section():
    state = _state()
    first = _section("A", enabled=True)
    second = _section("B")
    mark_placed(first, "monday", "09:00", "R2")
    state["classes"] = [first, second]

    validator = ConstraintValidator(state)
    assert validator.check_placement(second, "monday", "10:00", "R2")
    assert not validator.check_placement(second, "monday", "10:00", "R1")
    assert {room for _day, _slot, room in find_valid_options(state, second)} == {"R2"}


def test_existing_room_hop_is_reported_even_at_a_different_time():
    state = _state()
    first = _section("A", enabled=True)
    second = _section("B")
    mark_placed(first, "monday", "09:00", "R2")
    mark_placed(second, "monday", "10:00", "R1")
    state["classes"] = [first, second]

    conflicts = find_schedule_conflicts(state)
    assert len(conflicts) == 1
    assert conflicts[0]["kinds"] == ("room_consistency",)


def test_cpsat_keeps_a_flexible_section_in_the_pinned_sections_room():
    if not HAS_ORTOOLS:
        return
    state = _state()
    first = _section("A", enabled=True)
    first["pinned"] = True
    first["pinned_day"] = "monday"
    first["pinned_time"] = "09:00"
    first["pinned_classroom"] = "R2"
    second = _section("B")
    state["classes"] = [first, second]

    placed, unplaced, info = CPSATScheduler(
        state, time_limit=3.0, seed=20260101, require_all=True).solve()

    assert info["status"] in ("OPTIMAL", "FEASIBLE")
    assert not unplaced
    by_code = {c["name"]: room for c, _day, _slot, room in placed}
    assert by_code[second["name"]] == "R2"


def test_complete_search_can_temporarily_release_a_locked_placement():
    if not HAS_ORTOOLS:
        return
    state = _state()
    locked = _section("A")
    locked["class_code"] = "A101"
    locked["allowed_times"] = ["10:00"]
    locked["protection"] = "locked"
    mark_placed(locked, "monday", "09:00", "R1")
    rival = _section("B")
    rival["class_code"] = "B101"
    rival["allowed_times"] = ["09:00"]
    state["classes"] = [locked, rival]

    placed, unplaced, info = CPSATScheduler(
        state, time_limit=3.0, seed=20260101, require_all=True,
        release_protections=True).solve()

    assert info["status"] in ("OPTIMAL", "FEASIBLE")
    assert not unplaced
    by_name = {c["name"]: slot for c, _day, slot, _room in placed}
    assert by_name[locked["name"]] == "10:00"
    assert locked["protection"] == "locked"  # label is preserved on the class
