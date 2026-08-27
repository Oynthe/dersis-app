"""Probe 3: Malformed class dicts fed to the headless API.

 - missing keys (un-normalized dict)
 - targets = []
 - targets referencing year/branch not in state['years']
 - lecturer not in state['lecturers']
 - allowed_days/times = [] vs values outside the grid
 - pinned to a nonexistent slot / room
 - protection=locked but placed=False
For each: PREVENT / DETECT / REPAIR / SILENTLY-ACCEPT / CRASH.
"""
import _eh_sandbox
_eh_sandbox.enter()

import os
import traceback

from scheduler_app.translations import set_language
set_language("tr")

from scheduler_app.core.models import new_state, new_class, mark_placed
from scheduler_app.core import analytics
from scheduler_app.data_io import exporter
from scheduler_app.core.workflow import SchedulingWorkflow

EVID = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "evidence"))
os.makedirs(EVID, exist_ok=True)


def weights():
    return {}


def try_call(label, fn):
    try:
        r = fn()
        extra = ""
        if hasattr(r, "placed"):
            extra = f" (placed={len(r.placed)} unplaced={len(r.unplaced)})"
        print(f"  OK       {label}{extra}")
        return "OK"
    except Exception as e:
        tb = traceback.format_exc().splitlines()
        loc = ""
        for ln in reversed(tb):
            if "scheduler_app" in ln:
                loc = ln.strip()
                break
        print(f"  CRASH    {label}: {type(e).__name__}: {str(e)[:90]}")
        if loc:
            print(f"           {loc}")
        return "CRASH"


def base_state():
    s = new_state()
    s["days"] = ["monday", "tuesday", "wednesday"]
    s["slots"] = ["09:00", "10:00", "11:00", "12:00"]
    s["classrooms"] = ["R1", "R2"]
    s["classroom_capacities"] = {"R1": 30, "R2": 40}
    s["lecturers"] = ["L1", "L2"]
    s["years"] = {"Y1": ["A", "B"], "Y2": ["A"]}
    return s


def ops(state, tag, do_reschedule=True):
    try_call("analytics.compute_all_metrics", lambda: analytics.compute_all_metrics(state))
    for fmt in ("csv", "xlsx", "pdf"):
        p = os.path.join(EVID, f"malformed_{tag}.{fmt}")
        try_call(f"export {fmt}", lambda p=p, f=fmt: exporter.export_schedule(state, f, p))
    if do_reschedule:
        wf = SchedulingWorkflow(state, weights)
        try_call("workflow.reschedule", lambda: wf.reschedule(weights()))


def main():
    print("=" * 70)
    print("CASE 1: class dict MISSING KEYS (raw {}, not normalized)")
    print("=" * 70)
    s = base_state()
    s["classes"] = [{"name": "Raw", "lecturer": "L1"}]  # no targets/placed/pinned/duration...
    ops(s, "missingkeys")

    print("\n" + "=" * 70)
    print("CASE 1b: missing keys but PLACED-looking, feed analytics/export")
    print("=" * 70)
    s = base_state()
    s["classes"] = [{"name": "Raw2", "lecturer": "L1", "placed": True,
                     "placed_day": "monday", "placed_time": "09:00",
                     "placed_classroom": "R1", "pinned": False}]
    # no 'targets', no 'duration', no 'joint_session'
    ops(s, "missingkeys2", do_reschedule=False)

    print("\n" + "=" * 70)
    print("CASE 2: targets = [] (empty), placed")
    print("=" * 70)
    s = base_state()
    c = new_class(); c["name"] = "NoTargets"; c["lecturer"] = "L1"
    c["targets"] = []; c["duration"] = 1
    mark_placed(c, "monday", "09:00", "R1")
    s["classes"] = [c]
    ops(s, "emptytargets", do_reschedule=False)

    print("\n" + "=" * 70)
    print("CASE 3: targets reference year/branch NOT in state['years']")
    print("=" * 70)
    s = base_state()
    c = new_class(); c["name"] = "GhostTarget"; c["lecturer"] = "L1"
    c["targets"] = [{"year": "GHOST_YEAR", "branch": "Z"}]; c["duration"] = 1
    mark_placed(c, "monday", "09:00", "R1")
    s["classes"] = [c]
    ops(s, "ghosttarget", do_reschedule=False)

    print("\n" + "=" * 70)
    print("CASE 4: lecturer NOT in state['lecturers']")
    print("=" * 70)
    s = base_state()
    c = new_class(); c["name"] = "GhostLect"; c["lecturer"] = "NOBODY"
    c["targets"] = [{"year": "Y1", "branch": "A"}]; c["duration"] = 1
    s["classes"] = [c]
    ops(s, "ghostlect")

    print("\n" + "=" * 70)
    print("CASE 5: allowed_days/times containing values OUTSIDE the grid")
    print("=" * 70)
    s = base_state()
    c = new_class(); c["name"] = "OutOfGrid"; c["lecturer"] = "L1"
    c["targets"] = [{"year": "Y1", "branch": "A"}]; c["duration"] = 1
    c["allowed_days"] = ["friday", "sunday"]      # not in grid
    c["allowed_times"] = ["23:00"]                # not in grid
    s["classes"] = [c]
    ops(s, "outofgrid")

    print("\n" + "=" * 70)
    print("CASE 6: PINNED to a nonexistent slot and room")
    print("=" * 70)
    s = base_state()
    c = new_class(); c["name"] = "BadPin"; c["lecturer"] = "L1"
    c["targets"] = [{"year": "Y1", "branch": "A"}]; c["duration"] = 1
    c["pinned"] = True
    c["pinned_day"] = "monday"
    c["pinned_time"] = "23:00"          # not in slots
    c["pinned_classroom"] = "GHOST_ROOM"  # not in classrooms
    s["classes"] = [c]
    ops(s, "badpin")

    print("\n" + "=" * 70)
    print("CASE 6b: PINNED to a nonexistent DAY")
    print("=" * 70)
    s = base_state()
    c = new_class(); c["name"] = "BadPinDay"; c["lecturer"] = "L1"
    c["targets"] = [{"year": "Y1", "branch": "A"}]; c["duration"] = 1
    c["pinned"] = True
    c["pinned_day"] = "friday"      # not in days
    c["pinned_time"] = "09:00"
    c["pinned_classroom"] = "R1"
    s["classes"] = [c]
    ops(s, "badpinday")

    print("\n" + "=" * 70)
    print("CASE 7: protection=locked but placed=False (immovable, unplaced)")
    print("=" * 70)
    s = base_state()
    c = new_class(); c["name"] = "LockedUnplaced"; c["lecturer"] = "L1"
    c["targets"] = [{"year": "Y1", "branch": "A"}]; c["duration"] = 1
    c["protection"] = "locked"
    c["placed"] = False
    s["classes"] = [c]
    ops(s, "lockedunplaced")

    print("\nDONE probe_malformed_classes")


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
