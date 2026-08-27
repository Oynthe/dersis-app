"""Task 1 (ghost-day) + Task 2 (stale allowed_times) probes.

Task 1: allowed_days is never intersected with state['days']. A class whose
        allowed_days=['saturday'] on a mon-fri grid gets a saturday candidate
        and can be PLACED on saturday. Reproduced via CandidateGenerator and
        via the full auto-place / reschedule workflow. Invariant checked:
        placed_day in state['days'].

Task 2: allowed_times containing a slot not in state['slots'] reaches
        slot_index() (candidate_generator.py get_search_space, line ~41)
        which does state['slots'].index(slot) -> uncaught ValueError.
"""
import _sandbox
_sandbox.enter()

import traceback
from scheduler_app.core.models import new_state, new_class
from scheduler_app.core.candidate_generator import CandidateGenerator
from scheduler_app.core.constraint_validator import ConstraintValidator


def base_state():
    st = new_state()
    st["days"] = ["monday", "tuesday", "wednesday", "thursday", "friday"]
    st["slots"] = ["09:00", "10:00", "11:00", "12:00"]
    st["classrooms"] = ["R1", "R2"]
    st["classroom_capacities"] = {"R1": 0, "R2": 0}
    st["lecturers"] = ["L1"]
    st["years"] = {"Year-1": ["A"]}
    return st


def mk_class(**kw):
    c = new_class()
    c.update({"name": "Ghost", "lecturer": "L1", "duration": 1,
              "targets": [{"year": "Year-1", "branch": "A"}]})
    c.update(kw)
    return c


def task1_candidate_generator():
    print("\n----- TASK 1a: CandidateGenerator emits ghost saturday -----")
    st = base_state()
    cls = mk_class(allowed_days=["saturday"])
    st["classes"].append(cls)
    gen = CandidateGenerator(st)
    days, times, rooms = gen.get_search_space(cls)
    print(f"search-space days={days}  (state days={st['days']})")
    cands = gen.generate(cls)
    ghost = [c for c in cands if c[0] not in st["days"]]
    print(f"generated {len(cands)} candidates; "
          f"{len(ghost)} on days NOT in the grid")
    for c in ghost[:5]:
        print(f"    ghost candidate: {c}")
    return bool(ghost)


def task1_full_workflow():
    print("\n----- TASK 1b: full workflow places on ghost saturday -----")
    from scheduler_app.core.workflow import SchedulingWorkflow
    st = base_state()
    cls = mk_class(allowed_days=["saturday"])
    st["classes"].append(cls)
    wf = SchedulingWorkflow(st, lambda: {})
    res = wf.auto_place(cls)
    print(f"auto_place success={res.success} placed_info={res.placed_info}")
    placed_day = res.placed_info[0] if res.placed_info else None
    ghost = res.success and placed_day not in st["days"]
    if res.success and placed_day is not None:
        # commit and inspect state
        from scheduler_app.models import mark_placed
        mark_placed(cls, res.placed_info[0], res.placed_info[1], res.placed_info[2])
        print(f"COMMITTED: cls.placed_day={cls['placed_day']} "
              f"-> in grid days? {cls['placed_day'] in st['days']}")
    print(f"GHOST PLACEMENT via auto_place: {ghost}")

    # Also via reschedule (unplace then reschedule_all-style)
    print("  -- via workflow.reschedule --")
    st2 = base_state()
    cls2 = mk_class(allowed_days=["saturday"])
    st2["classes"].append(cls2)
    wf2 = SchedulingWorkflow(st2, lambda: {})
    r2 = wf2.reschedule({}, use_cpsat=False)
    placed_days = [(c.get("name"), d) for c, d, s, rm in r2.placed]
    print(f"reschedule placed: {placed_days}")
    ghost2 = any(d not in st2["days"] for _, d in placed_days)
    print(f"GHOST PLACEMENT via reschedule: {ghost2}")
    return ghost or ghost2


def task2_stale_time_valueerror():
    print("\n----- TASK 2: stale allowed_times -> ValueError -----")
    st = base_state()
    # 20:00 is NOT in state['slots']
    cls = mk_class(allowed_times=["20:00"])
    st["classes"].append(cls)
    gen = CandidateGenerator(st)
    try:
        days, times, rooms = gen.get_search_space(cls)
        print(f"NO error; times={times}")
        # then generate
        cands = gen.generate(cls)
        print(f"generate returned {len(cands)}")
        return False
    except ValueError as e:
        print("ValueError raised in get_search_space:")
        tb = traceback.format_exc().strip().splitlines()
        for line in tb[-4:]:
            print("    " + line)
        return True
    except Exception as e:  # noqa
        print(f"OTHER exception: {type(e).__name__}: {e}")
        return True


def task2_stale_time_via_validator():
    print("\n----- TASK 2b: stale allowed_times via ConstraintValidator "
          "check_placement_explained -----")
    st = base_state()
    cls = mk_class(allowed_times=["20:00"])
    st["classes"].append(cls)
    v = ConstraintValidator(st)
    try:
        ok, reasons = v.check_placement_explained(cls, "monday", "20:00", "R1")
        print(f"ok={ok} reasons={reasons}")
        return False
    except ValueError as e:
        print(f"ValueError from slot_index('20:00'): {e}")
        return True


if __name__ == "__main__":
    r1a = task1_candidate_generator()
    r1b = task1_full_workflow()
    r2 = task2_stale_time_valueerror()
    r2b = task2_stale_time_via_validator()
    print("\n================ SUMMARY ================")
    print(f"Task1a ghost candidates generated : {r1a}")
    print(f"Task1b ghost PLACED by workflow    : {r1b}")
    print(f"Task2  ValueError in search space  : {r2}")
    print(f"Task2b ValueError via validator    : {r2b}")
