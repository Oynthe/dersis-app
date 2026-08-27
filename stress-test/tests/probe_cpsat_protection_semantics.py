"""Probe: CP-SAT deep mode honors PROTECTION_LOCKED but IGNORES
soft / same_day / improve_only protection — protected placements are
silently moved and committed.

Runs two layers of evidence:
  A. Direct CPSATScheduler.solve() — shows which protection kinds the
     solver itself understands (only 'locked' + explicit protected_ids).
  B. Full workflow: ScheduleOptimizer.optimize(use_cpsat=True) — shows
     that soft/same_day/improve_only protection is NOT forwarded to the
     CP-SAT subprocess model, so CP-SAT moves those classes and the
     result is accepted.

Deterministic, self-contained, sandboxed. No storage side effects.
"""
import os
import sys
import tempfile

_sb = tempfile.mkdtemp(prefix="dersis_audit_")
os.environ["HOME"] = _sb
os.environ["USERPROFILE"] = _sb
sys.path.insert(0, r"C:\dev\dersis-app")

from scheduler_app.core.models import (
    new_state, new_class, mark_placed, cls_key,
    PROTECTION_LOCKED, PROTECTION_SOFT, PROTECTION_SAME_DAY,
    PROTECTION_IMPROVE_ONLY,
)
from scheduler_app.cpsat_scheduler import CPSATScheduler, HAS_ORTOOLS


def base_state():
    st = new_state()
    st["days"] = ["monday"]
    st["slots"] = [f"{9+i:02d}:00" for i in range(8)]  # 09:00..16:00
    st["classrooms"] = ["R001"]
    st["classroom_capacities"] = {"R001": 0}
    st["lecturers"] = ["Lect-1", "Lect-2"]
    st["years"] = {"Year-1": ["A"]}
    return st


def make_cls(code, lecturer, protection, placed_slot, duration=1):
    c = new_class()
    c["class_code"] = code
    c["name"] = code
    c["lecturer"] = lecturer
    c["targets"] = [{"year": "Year-1", "branch": "A"}]
    c["duration"] = duration
    c["participants"] = 0
    c["protection"] = protection
    if placed_slot is not None:
        mark_placed(c, "monday", placed_slot, "R001")
    return c


def find(placed_list, code):
    for c, d, s, r in placed_list:
        if c["class_code"] == code:
            return (d, s, r)
    return None


def layer_a():
    print("=" * 64)
    print("LAYER A — direct CPSATScheduler.solve(), protected_ids=empty")
    print("=" * 64)
    results = {}
    for prot in (PROTECTION_LOCKED, PROTECTION_SOFT, PROTECTION_SAME_DAY,
                 PROTECTION_IMPROVE_ONLY):
        st = base_state()
        # Class placed at a deliberately-late slot (14:00). A correctly
        # protected class must stay; a class treated as flexible will be
        # pulled toward slot 0 by the slot_position penalty.
        prot_cls = make_cls("PROT", "Lect-1", prot, "14:00")
        # A filler flexible class so the model is non-trivial.
        filler = make_cls("FILL", "Lect-2", "none", None)
        st["classes"] = [prot_cls, filler]

        solver = CPSATScheduler(st, time_limit=3.0, protected_ids=set())
        placed, unplaced, info = solver.solve()
        pos = find(placed or [], "PROT")
        moved = (pos is not None and pos[1] != "14:00")
        results[prot] = (pos, moved, info.get("status"))
        verdict = "MOVED (protection ignored)" if moved else "stayed at 14:00"
        print(f"  protection={prot:<12} status={info.get('status'):<9} "
              f"final={pos}  -> {verdict}")
    return results


def layer_b():
    print("=" * 64)
    print("LAYER B — full ScheduleOptimizer.optimize(use_cpsat=True)")
    print("           (soft protection, protected_ids not passed)")
    print("=" * 64)
    from scheduler_app.schedule_optimizer import ScheduleOptimizer

    # Scenario engineered so that CP-SAT's optimum is strictly better than
    # the heuristic's ONLY by relocating the soft-protected class, and the
    # heuristic cannot achieve it because the anchor is PINNED.
    #   - ANCHOR: pinned (immovable) at 09:00, lecturer Lect-1, online.
    #   - SOFT:   soft-protected at 15:00, SAME lecturer Lect-1, online.
    # Heuristic keeps SOFT at 15:00 -> big lecturer gap (09:00..15:00).
    # CP-SAT treats SOFT as flexible -> pulls it beside the anchor,
    # collapsing the lecturer gap -> lower tt score -> accepted.
    st = base_state()
    anchor = make_cls("ANCHOR", "Lect-1", "none", None)
    anchor["location_type"] = "online"
    anchor["pinned"] = True
    anchor["pinned_day"] = "monday"
    anchor["pinned_time"] = "09:00"
    anchor["pinned_classroom"] = None

    soft = make_cls("SOFT", "Lect-1", PROTECTION_SOFT, "15:00")
    soft["location_type"] = "online"
    soft["placed_classroom"] = None

    st["classes"] = [anchor, soft]
    before = (soft["placed_day"], soft["placed_time"])

    opt = ScheduleOptimizer(
        st, weights=None,
        multi_start_runs=2, multi_start_time_limit=8.0,
        lns_iterations=40, lns_time_limit=2.0,
        use_cpsat=True, cpsat_time_limit=4.0,
        parallel_workers=-1)  # disable parallel to keep it clean
    placed, unplaced, changes, summary = opt.optimize()

    pos = None
    for c, d, s, r in placed:
        if c["class_code"] == "SOFT":
            pos = (d, s)
    print(f"  SOFT before      : {before}")
    print(f"  SOFT after opt   : {pos}")
    print(f"  cpsat_used       : {summary.get('cpsat_used')}")
    print(f"  cpsat_status     : {summary.get('cpsat_status')}")
    moved = pos is not None and pos != before
    in_changes = any(ch["cls"]["class_code"] == "SOFT" for ch in changes)
    print(f"  SOFT moved       : {moved}")
    print(f"  SOFT in changes[]: {in_changes}")
    return before, pos, moved, summary


if __name__ == "__main__":
    print("HAS_ORTOOLS:", HAS_ORTOOLS)
    a = layer_a()
    b = layer_b()

    print("\n" + "=" * 64)
    print("SUMMARY")
    print("=" * 64)
    locked_ok = not a[PROTECTION_LOCKED][1]
    soft_ignored_direct = a[PROTECTION_SOFT][1]
    sameday_ignored = a[PROTECTION_SAME_DAY][1]
    improve_ignored = a[PROTECTION_IMPROVE_ONLY][1]
    print(f"LOCKED respected by CPSAT        : {locked_ok}")
    print(f"SOFT ignored (direct)            : {soft_ignored_direct}")
    print(f"SAME_DAY ignored (direct)        : {sameday_ignored}")
    print(f"IMPROVE_ONLY ignored (direct)    : {improve_ignored}")
    before, pos, moved_b, _ = b
    print(f"SOFT moved via full workflow     : {moved_b}")
