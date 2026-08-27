"""Diagnose WHY the committed schedule (post apply_reschedule) still contains
oracle-flagged violations on the 'large' preset with apply_rejected==0.

For each violating class we report:
  - pinned? protection? location_type?
  - what the PRODUCTION ConstraintValidator.check_placement says about the
    committed placement (excluding the class itself), i.e. does production
    agree the placement is valid?
This separates "pinned bypasses validation" from "validator misses conflicts".
"""
import _sandbox
_sandbox.enter()

from _fixtures.dataset_gen import make_preset
from scheduler_app.core.workflow import SchedulingWorkflow
from scheduler_app.models import (
    cls_key, needs_physical_room, effective_day, effective_time, effective_room,
    lecturer_available_at, room_fits_class,
)
from scheduler_app.core.constraint_validator import ConstraintValidator
import schedule_oracle as ora


def main(preset="large", seed=42):
    state = make_preset(preset, seed=seed)
    by_uid = {c["class_uid"]: c for c in state["classes"]}
    wf = SchedulingWorkflow(state, lambda: {})
    res = wf.reschedule({}, use_cpsat=False)
    rejected = wf.apply_reschedule(res)
    print(f"preset={preset} rejected={len(rejected)}")

    audit = ora.check_schedule(state)
    print(f"committed violations by category: {audit['counts']}")
    print(f"total placed blocks: {audit['n_placed']}\n")

    # Group violations by class uid
    seen = set()
    n = 0
    for v in audit["violations"]:
        uid = v["uid"]
        cat = v["category"]
        tagkey = (uid, cat)
        if tagkey in seen:
            continue
        seen.add(tagkey)
        n += 1
        if n > 20:
            break
        cls = by_uid.get(uid)
        if cls is None:
            print(f"[{cat}] uid={uid} (not found)")
            continue
        d, s, r = effective_day(cls), effective_time(cls), effective_room(cls)
        # production validator opinion (exclude this class)
        v2 = ConstraintValidator(state, exclude_ids={uid})
        room_arg = r if needs_physical_room(cls) else None
        try:
            prod_ok = v2.check_placement(cls, d, s, room_arg)
        except Exception as e:  # noqa
            prod_ok = f"EXC {type(e).__name__}: {e}"
        # direct capacity / availability spot checks
        cap_ok = room_fits_class(state, r, cls) if needs_physical_room(cls) else True
        avail_ok = lecturer_available_at(state, cls.get("lecturer"), d, s) if s else "n/a"
        print(f"[{cat}] {cls.get('name')} uid={uid[:8]} "
              f"pinned={cls.get('pinned')} prot={cls.get('protection')} "
              f"loc={cls.get('location_type')}")
        print(f"     committed day={d} slot={s} room={r} "
              f"participants={cls.get('participants')} dur={cls.get('duration')}")
        print(f"     production check_placement(excl self) = {prod_ok} | "
              f"room_fits={cap_ok} avail_at_start={avail_ok}")
    # Count how many violating classes are pinned
    viol_uids = {v["uid"] for v in audit["violations"]}
    pinned_viol = sum(1 for u in viol_uids if by_uid.get(u, {}).get("pinned"))
    print(f"\nDistinct violating classes: {len(viol_uids)}; "
          f"of which pinned: {pinned_viol}")


if __name__ == "__main__":
    main()
