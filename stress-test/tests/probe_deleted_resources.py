"""Probe 4 (+3 pins): Deleted-resource references / orphaned state.

Place classes normally, then delete the room / lecturer / day / slot / year
from state and run refresh-equivalent operations: analytics, export (all fmts),
reschedule, and occupancy helpers. Determine per operation whether DERSIS
PREVENTs / DETECTs / REPAIRs / SILENTLY-ACCEPTs / CRASHes the orphaned ref.

Guarded under __main__ because reschedule() uses a ProcessPoolExecutor.
"""
import _eh_sandbox
_eh_sandbox.enter()

import os
import traceback

from scheduler_app.translations import set_language
set_language("tr")

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from _fixtures.dataset_gen import make_state

from scheduler_app.core import analytics
from scheduler_app.data_io import exporter
from scheduler_app.core.workflow import SchedulingWorkflow
from scheduler_app.core.logic import occupied_slots_of, get_placed_classes

EVID = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "evidence"))
os.makedirs(EVID, exist_ok=True)


def weights():
    return {}


def try_call(label, fn):
    try:
        fn()
        print(f"  OK       {label}")
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


def build_placed_state(seed=7):
    s = make_state(n_days=5, n_slots=6, n_rooms=4, n_lecturers=6,
                   n_years=2, n_classes=20, density=0.0, seed=seed)
    wf = SchedulingWorkflow(s, weights)
    r = wf.reschedule(weights())
    wf.apply_reschedule(r)
    placed = [c for c in s["classes"] if c["placed"]]
    print(f"  setup: {len(placed)}/{len(s['classes'])} classes placed")
    return s


def run_ops(state, tag):
    try_call("analytics.compute_all_metrics", lambda: analytics.compute_all_metrics(state))
    try_call("analytics.room_utilization", lambda: analytics.room_utilization(state))
    try_call("analytics.busiest_slots", lambda: analytics.busiest_slots(state))
    try_call("analytics.lecturer_gap_distribution",
             lambda: analytics.lecturer_gap_distribution(state))

    def _occ_all():
        for c in get_placed_classes(state):
            occupied_slots_of(state, c)
    try_call("occupied_slots_of(all placed)", _occ_all)
    for fmt in ("csv", "xlsx", "pdf"):
        p = os.path.join(EVID, f"orphan_{tag}.{fmt}")
        try_call(f"export {fmt}", lambda p=p, f=fmt: exporter.export_schedule(state, f, p))
    wf = SchedulingWorkflow(state, weights)
    try_call("workflow.reschedule", lambda: wf.reschedule(weights()))


def main():
    print("#" * 72)
    print("# DELETE A SLOT (shrink grid) after placement -> overflow orphans")
    print("#" * 72)
    s = build_placed_state()
    removed = s["slots"].pop()
    print(f"  removed slot {removed!r}; classes still reference it via placed_time/span")
    run_ops(s, "slot")

    print("\n" + "#" * 72)
    print("# DELETE A DAY after placement")
    print("#" * 72)
    s = build_placed_state()
    used_days = {c["placed_day"] for c in s["classes"] if c["placed"]}
    victim_day = next(iter(used_days))
    s["days"].remove(victim_day)
    print(f"  removed day {victim_day!r} (still referenced by placed classes)")
    run_ops(s, "day")

    print("\n" + "#" * 72)
    print("# DELETE A ROOM after placement")
    print("#" * 72)
    s = build_placed_state()
    used_rooms = {c["placed_classroom"] for c in s["classes"]
                  if c["placed"] and c["placed_classroom"]}
    victim_room = next(iter(used_rooms))
    s["classrooms"].remove(victim_room)
    s["classroom_capacities"].pop(victim_room, None)
    print(f"  removed room {victim_room!r} (still referenced by placed classes)")
    run_ops(s, "room")

    print("\n" + "#" * 72)
    print("# DELETE A LECTURER after placement")
    print("#" * 72)
    s = build_placed_state()
    victim_lect = s["lecturers"][0]
    s["lecturers"].remove(victim_lect)
    s["lecturer_availability"].pop(victim_lect, None)
    print(f"  removed lecturer {victim_lect!r} (classes still reference it)")
    run_ops(s, "lecturer")

    print("\n" + "#" * 72)
    print("# DELETE A YEAR after placement (targets orphaned)")
    print("#" * 72)
    s = build_placed_state()
    victim_year = next(iter(s["years"].keys()))
    del s["years"][victim_year]
    print(f"  removed year {victim_year!r} (targets still reference it)")
    run_ops(s, "year")

    print("\nDONE probe_deleted_resources")


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
