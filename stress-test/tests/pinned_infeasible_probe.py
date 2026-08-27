"""Deterministic minimal repro: infeasible / mutually-conflicting PINNED
placements are committed to the schedule WITHOUT validation.

workflow.optimize() copies every pinned class into result.placed using its
pinned_* fields unconditionally, and workflow.apply_reschedule() skips pinned
classes (`if cls_item['pinned']: continue`). So a pin that overflows room
capacity, sits where the lecturer is unavailable, or collides with another
pin, is accepted verbatim. The final rendered schedule then violates hard
constraints -- even though the production ConstraintValidator flags each as
invalid.
"""
import _sandbox
_sandbox.enter()

from scheduler_app.core.models import new_state, new_class, needs_physical_room
from scheduler_app.core.workflow import SchedulingWorkflow
from scheduler_app.core.constraint_validator import ConstraintValidator
import schedule_oracle as ora


def st_base():
    st = new_state()
    st["days"] = ["monday", "tuesday"]
    st["slots"] = ["09:00", "10:00", "11:00"]
    st["classrooms"] = ["R1", "R2"]
    st["classroom_capacities"] = {"R1": 10, "R2": 0}   # R1 capacity 10
    st["lecturers"] = ["L1", "L2", "L3"]
    st["years"] = {"Year-1": ["A", "B"]}
    # L3 is excluded from monday entirely
    st["lecturer_availability"]["L3"] = {
        "allowed_days": [], "allowed_hours": [],
        "excluded_days": ["monday"], "excluded_hours": [],
    }
    return st


def pin(name, lect, day, time, room, **kw):
    c = new_class()
    c.update({
        "name": name, "lecturer": lect, "duration": 1,
        "targets": [{"year": "Year-1", "branch": "A"}],
        "pinned": True, "pinned_day": day, "pinned_time": time,
        "pinned_classroom": room,
    })
    c.update(kw)
    return c


def main():
    st = st_base()
    # P1 & P2: two pins colliding in the SAME room+slot (room double-book +
    # they also share Year-1/A -> group clash + can share lecturer)
    p1 = pin("P1_collide", "L1", "monday", "09:00", "R1")
    p2 = pin("P2_collide", "L2", "monday", "09:00", "R1")
    # P3: capacity overflow (50 participants into R1 cap 10)
    p3 = pin("P3_overcap", "L1", "tuesday", "09:00", "R1", participants=50,
             targets=[{"year": "Year-1", "branch": "B"}])
    # P4: lecturer L3 unavailable on monday
    p4 = pin("P4_unavail", "L3", "monday", "11:00", "R2",
             targets=[{"year": "Year-1", "branch": "B"}])
    st["classes"] += [p1, p2, p3, p4]

    wf = SchedulingWorkflow(st, lambda: {})
    res = wf.reschedule({}, use_cpsat=False)
    print("result.placed (pins are copied in verbatim):")
    for c, d, s, r in res.placed:
        print(f"   {c['name']}: {d}/{s}/{r}")
    rejected = wf.apply_reschedule(res)
    print(f"apply_reschedule rejected = {rejected} "
          f"(pins are SKIPPED, never validated)")

    audit = ora.check_schedule(st)
    print(f"\nORACLE committed violations: {audit['counts']}")
    for v in audit["violations"]:
        print(f"   [{v['category']}] {v['cls']}: {v['detail']}")

    # Confirm production validator agrees each pin is invalid
    print("\nProduction ConstraintValidator opinion on each pin (excl self):")
    for c in (p1, p2, p3, p4):
        v = ConstraintValidator(st, exclude_ids={c["class_uid"]})
        room = c["pinned_classroom"] if needs_physical_room(c) else None
        ok = v.check_placement(c, c["pinned_day"], c["pinned_time"], room)
        print(f"   {c['name']}: check_placement = {ok}")

    committed_bad = len(audit["violations"]) > 0
    print(f"\n  -> committed schedule contains hard violations from pins? "
          f"{committed_bad}")
    return committed_bad


if __name__ == "__main__":
    main()
