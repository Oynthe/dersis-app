"""Task 5 + 6 + 7: ConstraintValidator / occupancy / availability integrity.

Task 5: find_conflicts() returns [] for a placement check_placement REJECTS.
        Multi-duration class, lecturer available at the start slot but not at a
        later slot in the block. respects_constraints (used by check_placement)
        checks every slot and fails; find_conflicts only checks the start slot
        for availability, so it reports no conflict -> silent disagreement.

Task 6: occupancy maps are plain sets with no refcount. When two classes
        occupy the same (room/lecturer/group) cell (reachable via infeasible
        pins), removing ONE erases the shared key, so the cell wrongly reads
        as free and a THIRD class can be validated onto it.

Task 7: malformed lecturer_availability (missing keys) raises KeyError in
        lecturer_available_at / apply_lecturer_availability_filters, because
        get_lecturer_availability returns the stored partial dict unmodified.
"""
import _sandbox
_sandbox.enter()

import traceback
from scheduler_app.core.models import (
    new_state, new_class, mark_placed,
    lecturer_available_at, apply_lecturer_availability_filters,
    get_lecturer_availability,
)
from scheduler_app.core.constraint_validator import ConstraintValidator


def base():
    st = new_state()
    st["days"] = ["monday", "tuesday"]
    st["slots"] = ["09:00", "10:00", "11:00"]
    st["classrooms"] = ["R1", "R2"]
    st["classroom_capacities"] = {"R1": 0, "R2": 0}
    st["lecturers"] = ["L1", "L2"]
    st["years"] = {"Year-1": ["A", "B"]}
    return st


def mk(**kw):
    c = new_class()
    c.update({"name": kw.get("name", "C"), "lecturer": "L1", "duration": 1,
              "targets": [{"year": "Year-1", "branch": "A"}]})
    c.update(kw)
    return c


def task5_find_conflicts_disagrees():
    print("\n===== TASK 5: find_conflicts()==[] but check_placement rejects =====")
    st = base()
    # L1 available only at 09:00; NOT at 10:00/11:00
    st["lecturer_availability"]["L1"] = {
        "allowed_days": [], "allowed_hours": ["09:00"],
        "excluded_days": [], "excluded_hours": [],
    }
    cls = mk(name="TwoHour", duration=2)  # occupies 09:00 + 10:00
    st["classes"].append(cls)
    v = ConstraintValidator(st)

    check = v.check_placement(cls, "monday", "09:00", "R1")
    conflicts = v.find_conflicts(cls, "monday", "09:00", "R1")
    respects = v.respects_constraints(cls, "monday", "09:00", "R1")
    print(f"check_placement           = {check} (expect False)")
    print(f"respects_constraints      = {respects} (expect False, slot 10:00 unavail)")
    print(f"find_conflicts            = {conflicts} (expect NON-empty)")
    print(f"lecturer avail 09:00={lecturer_available_at(st,'L1','monday','09:00')} "
          f"10:00={lecturer_available_at(st,'L1','monday','10:00')}")
    bug = (check is False) and (len(conflicts) == 0)
    print(f"  -> DISAGREEMENT (rejected but no conflict reported)? {bug}")
    return bug


def task6_occupancy_refcount():
    print("\n===== TASK 6: set-based occupancy has no refcount =====")
    st = base()
    # Two classes double-booked into the SAME room R1 monday 09:00
    # (reachable in production via two infeasible pins to the same cell).
    a = mk(name="A")
    b = mk(name="B", lecturer="L2", targets=[{"year": "Year-1", "branch": "B"}])
    mark_placed(a, "monday", "09:00", "R1")
    mark_placed(b, "monday", "09:00", "R1")
    st["classes"] += [a, b]

    v = ConstraintValidator(st)
    key = ("monday", "09:00")
    print(f"room_occ[{key}] after build = {v.room_occ.get(key)} "
          f"(set collapses two classes into one 'R1')")

    # A third class C wants R1 monday 09:00 -> correctly blocked now
    c = mk(name="C", lecturer="L-x", targets=[{"year": "Year-1", "branch": "A"}])
    before = v.check_placement(c, "monday", "09:00", "R1")
    print(f"check_placement(C, R1) BEFORE remove = {before} (expect False; room busy)")

    # Now remove ONLY class A's placement (e.g. LNS destroys A)
    v.remove_placement(a, "monday", "09:00", "R1")
    print(f"room_occ[{key}] after removing A = {v.room_occ.get(key)} "
          f"(B STILL occupies R1 here!)")
    after = v.check_placement(c, "monday", "09:00", "R1")
    print(f"check_placement(C, R1) AFTER remove of A = {after} "
          f"(BUG if True: B still there)")
    bug = (before is False) and (after is True)
    print(f"  -> refcount bug (C wrongly validated onto B's room)? {bug}")
    return bug


def task7_malformed_availability_keyerror():
    print("\n===== TASK 7: malformed lecturer_availability -> KeyError =====")
    st = base()
    # partial dict: only allowed_days present
    st["lecturer_availability"]["L1"] = {"allowed_days": ["monday"]}
    got = get_lecturer_availability(st, "L1")
    print(f"get_lecturer_availability returned (unmodified partial): {got}")

    results = {}
    # 1) lecturer_available_at
    try:
        lecturer_available_at(st, "L1", "monday", "09:00")
        results["lecturer_available_at"] = "no error"
    except KeyError as e:
        results["lecturer_available_at"] = f"KeyError: {e}"

    # 2) apply_lecturer_availability_filters
    try:
        apply_lecturer_availability_filters(st, "L1", list(st["days"]), list(st["slots"]))
        results["apply_lecturer_availability_filters"] = "no error"
    except KeyError as e:
        results["apply_lecturer_availability_filters"] = f"KeyError: {e}"

    # 3) full validator path
    cls = mk(name="P")
    st["classes"].append(cls)
    try:
        v = ConstraintValidator(st)
        v.check_placement(cls, "monday", "09:00", "R1")
        results["ConstraintValidator.check_placement"] = "no error"
    except KeyError as e:
        results["ConstraintValidator.check_placement"] = f"KeyError: {e}"
    except Exception as e:  # noqa
        results["ConstraintValidator.check_placement"] = f"{type(e).__name__}: {e}"

    for k, val in results.items():
        print(f"  {k}: {val}")
    bug = any("KeyError" in str(v) for v in results.values())
    print(f"  -> KeyError on partial availability dict? {bug}")
    return bug


if __name__ == "__main__":
    t5 = task5_find_conflicts_disagrees()
    t6 = task6_occupancy_refcount()
    t7 = task7_malformed_availability_keyerror()
    print("\n================ SUMMARY ================")
    print(f"Task5 find_conflicts disagreement : {t5}")
    print(f"Task6 occupancy refcount bug      : {t6}")
    print(f"Task7 malformed avail KeyError    : {t7}")
