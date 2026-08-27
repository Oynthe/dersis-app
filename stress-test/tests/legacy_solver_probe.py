"""Task 3 + Task 4: legacy backtracking solver defects.

Task 3: logic.reschedule_all / batch_schedule / auto_place_class use
        _get_valid_slots -> _check_placement_fast, NEITHER of which consults
        lecturer availability. A fully-unavailable lecturer's class is still
        placed by the legacy solver. The optimized/ConstraintValidator path
        leaves it unplaced.

Task 4: logic.reschedule_all collects `flexible = placed and not pinned`
        (logic.py:1043) -- it does NOT exclude protection=='locked'. A locked
        (but non-pinned) class is unplaced and re-solved, and can be MOVED.
        Contrast batch_schedule (excludes locked) and the optimized path
        (keeps locked fixed).
"""
import _sandbox
_sandbox.enter()

from scheduler_app.core.models import new_state, new_class, mark_placed
from scheduler_app.core import logic
from scheduler_app.core.models import lecturer_available_at


def grid_state():
    st = new_state()
    st["days"] = ["monday", "tuesday", "wednesday", "thursday", "friday"]
    st["slots"] = ["09:00", "10:00", "11:00"]
    st["classrooms"] = ["R1"]
    st["classroom_capacities"] = {"R1": 0}
    st["lecturers"] = ["L1"]
    st["years"] = {"Year-1": ["A"]}
    return st


def mk(**kw):
    c = new_class()
    c.update({"name": kw.get("name", "C"), "lecturer": "L1", "duration": 1,
              "targets": [{"year": "Year-1", "branch": "A"}]})
    c.update(kw)
    return c


def task3_legacy_ignores_availability():
    print("\n===== TASK 3: legacy solver ignores lecturer availability =====")
    st = grid_state()
    # L1 fully unavailable: excluded from every day
    st["lecturer_availability"]["L1"] = {
        "allowed_days": [], "allowed_hours": [],
        "excluded_days": list(st["days"]), "excluded_hours": [],
    }
    cls = mk(name="Unavail")
    st["classes"].append(cls)

    # sanity: no (day,slot) is actually available
    any_avail = any(lecturer_available_at(st, "L1", d, s)
                    for d in st["days"] for s in st["slots"])
    print(f"any slot where L1 available? {any_avail}")

    # -- LEGACY reschedule_all --
    placed, unplaced, changes = logic.reschedule_all(st)
    legacy_placed = [(c.get("name"), d, s) for c, d, s, r in placed]
    print(f"LEGACY reschedule_all placed: {legacy_placed}  unplaced={len(unplaced)}")
    legacy_bug = any(True for c, d, s, r in placed
                     if not lecturer_available_at(st, c["lecturer"], d, s))
    print(f"  -> legacy placed a class at an unavailable slot? {legacy_bug}")

    # -- LEGACY auto_place_class --
    st2 = grid_state()
    st2["lecturer_availability"]["L1"] = dict(st["lecturer_availability"]["L1"])
    cls2 = mk(name="Unavail2")
    st2["classes"].append(cls2)
    ok, placements, resched = logic.auto_place_class(st2, cls2)
    print(f"LEGACY auto_place_class success={ok} placements={placements}")
    ap_bug = ok and any(not lecturer_available_at(st2, "L1", d, s)
                        for (d, s, r) in placements.values())
    print(f"  -> legacy auto_place put class on unavailable slot? {ap_bug}")

    # -- LEGACY batch_schedule --
    st3 = grid_state()
    st3["lecturer_availability"]["L1"] = dict(st["lecturer_availability"]["L1"])
    cls3 = mk(name="Unavail3")
    st3["classes"].append(cls3)
    bplaced, bunplaced, bres = logic.batch_schedule(st3, [cls3])
    b_bug = any(not lecturer_available_at(st3, c["lecturer"], d, s)
                for c, d, s, r in bplaced)
    print(f"LEGACY batch_schedule placed={len(bplaced)} unplaced={len(bunplaced)} "
          f"-> availability-violating placement? {b_bug}")

    # -- OPTIMIZED path (should respect availability) --
    st4 = grid_state()
    st4["lecturer_availability"]["L1"] = dict(st["lecturer_availability"]["L1"])
    cls4 = mk(name="UnavailOpt")
    st4["classes"].append(cls4)
    oplaced, ounplaced, ochanges, osum = logic.optimized_reschedule_all(
        st4, weights={}, multi_start_runs=1, multi_start_time_limit=10)
    opt_bug = any(not lecturer_available_at(st4, c["lecturer"], d, s)
                  for c, d, s, r in oplaced)
    print(f"OPTIMIZED reschedule placed={len(oplaced)} unplaced={len(ounplaced)} "
          f"-> availability-violating placement? {opt_bug}")

    return legacy_bug or ap_bug or b_bug, opt_bug


def task4_reschedule_all_moves_locked():
    print("\n===== TASK 4: legacy reschedule_all moves protection=locked =====")
    st = grid_state()
    # remove availability restrictions
    locked = mk(name="Locked", protection="locked")
    # place it at a LATE position; scorer prefers earlier -> it will move
    mark_placed(locked, "friday", "11:00", "R1")
    locked["protection"] = "locked"
    st["classes"].append(locked)

    orig = (locked["placed_day"], locked["placed_time"], locked["placed_classroom"])
    print(f"locked class starts at {orig} (protection={locked['protection']})")

    placed, unplaced, changes = logic.reschedule_all(st)
    newpos = None
    for c, d, s, r in placed:
        if c["class_uid"] == locked["class_uid"]:
            newpos = (d, s, r)
    print(f"LEGACY reschedule_all -> locked now at {newpos}; "
          f"changes recorded: {len(changes)}")
    moved = newpos is not None and newpos != orig
    print(f"  -> locked class MOVED by legacy reschedule_all? {moved}")

    # Contrast: optimized path keeps locked fixed
    st2 = grid_state()
    locked2 = mk(name="Locked2", protection="locked")
    mark_placed(locked2, "friday", "11:00", "R1")
    locked2["protection"] = "locked"
    st2["classes"].append(locked2)
    oplaced, ounpl, och, osum = logic.optimized_reschedule_all(
        st2, weights={}, multi_start_runs=1, multi_start_time_limit=10)
    onew = None
    for c, d, s, r in oplaced:
        if c["class_uid"] == locked2["class_uid"]:
            onew = (d, s, r)
    print(f"OPTIMIZED reschedule -> locked at {onew} "
          f"(expected friday/11:00/R1); moved? {onew != ('friday','11:00','R1')}")
    return moved


if __name__ == "__main__":
    legacy_bug, opt_ok_bug = task3_legacy_ignores_availability()
    moved = task4_reschedule_all_moves_locked()
    print("\n================ SUMMARY ================")
    print(f"Task3 legacy solver placed on unavailable slot : {legacy_bug}")
    print(f"Task3 optimized path ALSO violated availability : {opt_ok_bug}")
    print(f"Task4 legacy reschedule_all moved locked class  : {moved}")
