"""Probe: CP-SAT models lecturer availability only at the START slot.

A multi-hour class whose lecturer is available ONLY at the start hour is
placed by CP-SAT spanning hours the lecturer is unavailable (mid-block
violation). The heuristic engine (ConstraintValidator) checks every slot
and correctly refuses. Because CP-SAT then "places" one more class, the
optimizer accepts the CP-SAT result; apply_reschedule's validator later
rejects the invalid placement and silently unplaces it — and the caller
(ui/app.py:2713) discards apply_reschedule's returned `rejected` list.

Layers:
  A. Direct CPSATScheduler.solve() places the 3h class at 09:00 and the
     authoritative ConstraintValidator.check_placement() calls it INVALID.
  B. Full workflow: reschedule(use_cpsat=True) reports it in result.placed
     (shown to the user), but apply_reschedule() drops it to unplaced and
     returns it in `rejected` — a value the UI ignores.

Deterministic, self-contained, sandboxed.
"""
import os
import sys
import tempfile

_sb = tempfile.mkdtemp(prefix="dersis_audit_")
os.environ["HOME"] = _sb
os.environ["USERPROFILE"] = _sb
sys.path.insert(0, r"C:\dev\dersis-app")

from scheduler_app.core.models import new_state, new_class, cls_key
from scheduler_app.cpsat_scheduler import CPSATScheduler, HAS_ORTOOLS
from scheduler_app.core.constraint_validator import ConstraintValidator


def build_state():
    st = new_state()
    st["days"] = ["monday"]
    st["slots"] = [f"{9+i:02d}:00" for i in range(8)]  # 09:00..16:00
    st["classrooms"] = ["R001"]
    st["classroom_capacities"] = {"R001": 0}
    st["lecturers"] = ["Lect-1"]
    st["years"] = {"Year-1": ["A"]}
    # Lecturer available ONLY at 09:00.
    st["lecturer_availability"]["Lect-1"] = {
        "allowed_days": ["monday"],
        "allowed_hours": ["09:00"],
        "excluded_days": [],
        "excluded_hours": [],
    }
    c = new_class()
    c["class_code"] = "BIG3H"
    c["name"] = "BIG3H"
    c["lecturer"] = "Lect-1"
    c["targets"] = [{"year": "Year-1", "branch": "A"}]
    c["duration"] = 3           # occupies 09:00, 10:00, 11:00
    c["participants"] = 0
    st["classes"] = [c]
    return st, c


def layer_a():
    print("=" * 64)
    print("LAYER A — CP-SAT places a 3h class where lecturer only has 09:00")
    print("=" * 64)
    st, c = build_state()
    solver = CPSATScheduler(st, time_limit=3.0)
    placed, unplaced, info = solver.solve()
    print(f"  status            : {info.get('status')}")
    cpsat_pos = None
    for cc, d, s, r in (placed or []):
        if cc["class_code"] == "BIG3H":
            cpsat_pos = (d, s, r)
    print(f"  CP-SAT placement  : {cpsat_pos}")
    print(f"  CP-SAT unplaced   : {[(cc['class_code'], reason) for cc, reason in (unplaced or [])]}")

    # Authoritative full-block check
    valid = None
    if cpsat_pos:
        v = ConstraintValidator(st, exclude_ids={cls_key(c)})
        valid = v.check_placement(c, cpsat_pos[0], cpsat_pos[1], cpsat_pos[2])
    # Per-slot availability audit
    from scheduler_app.core.models import lecturer_available_at
    slot_avail = {s: lecturer_available_at(st, "Lect-1", "monday", s)
                  for s in ["09:00", "10:00", "11:00"]}
    print(f"  lecturer avail per slot 09/10/11 : {slot_avail}")
    print(f"  ConstraintValidator.check_placement -> {valid} "
          f"(False = CP-SAT produced an INVALID placement)")
    return cpsat_pos, valid, slot_avail


def layer_b():
    print("=" * 64)
    print("LAYER B — reschedule(use_cpsat=True) then apply_reschedule()")
    print("=" * 64)
    from scheduler_app.workflow import SchedulingWorkflow

    st, c = build_state()
    wf = SchedulingWorkflow(st, get_weights=lambda: None)
    result = wf.reschedule(weights=None, use_cpsat=True)

    reported_placed = [(cc["class_code"], d, s)
                       for cc, d, s, r in result.placed]
    reported_unplaced = [(cc["class_code"], reason)
                         for cc, reason in result.unplaced]
    print(f"  result.placed (shown to user)   : {reported_placed}")
    print(f"  result.unplaced (shown to user) : {reported_unplaced}")

    # Now COMMIT exactly as the UI does — but capture the discarded return.
    rejected = wf.apply_reschedule(result)
    committed_placed = c["placed"]
    committed_pos = (c.get("placed_day"), c.get("placed_time"))
    print(f"  apply_reschedule() returned rejected = {rejected}  "
          f"(ui/app.py:2713 discards this)")
    print(f"  after commit: BIG3H placed={committed_placed} pos={committed_pos}")

    shown_placed = any(code == "BIG3H" for code, *_ in reported_placed)
    silently_dropped = shown_placed and not committed_placed
    print(f"  shown as placed but actually dropped: {silently_dropped}")
    return reported_placed, rejected, committed_placed


if __name__ == "__main__":
    print("HAS_ORTOOLS:", HAS_ORTOOLS)
    a = layer_a()
    print()
    b = layer_b()
    print("\n" + "=" * 64)
    print("SUMMARY")
    print("=" * 64)
    cpsat_pos, valid, slot_avail = a
    print(f"CP-SAT placed 3h class at start   : {cpsat_pos}")
    print(f"Lecturer unavailable mid-block    : "
          f"{not slot_avail.get('10:00') or not slot_avail.get('11:00')}")
    print(f"Placement is invalid (validator)  : {valid is False}")
    reported_placed, rejected, committed = b
    print(f"UI shows placed, apply() drops it : "
          f"{any(code=='BIG3H' for code,*_ in reported_placed) and not committed}")
    print(f"apply_reschedule rejected list    : {rejected} (discarded by UI)")
