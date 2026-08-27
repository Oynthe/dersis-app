"""PROBE 6: divide-by-zero / degenerate-state robustness for analytics.py
(compute_all_metrics) and schedule_analytics.ScheduleAnalytics.analyze.

Cases:
  1. Empty state (no classes, no days/slots).
  2. Classes present but ZERO slots and ZERO days.
  3. Placed class whose slot is NOT in state['slots'] (slot_index ValueError).
  4. Single day/slot (capacity edge).
  5. ScheduleAnalytics.analyze on empty placements + on state with 0 slots*days.
  6. make_preset presets run through compute_all_metrics + analyze_schedule.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "tests")))
import _fixtures.sandbox  # noqa: F401

import traceback
from _fixtures.dataset_gen import make_preset
from scheduler_app.core.analytics import compute_all_metrics
from scheduler_app.core.schedule_analytics import ScheduleAnalytics
from scheduler_app.core.logic import analyze_schedule
from scheduler_app.core.models import (
    new_state, new_class, mark_placed, effective_room, effective_day,
    effective_time,
)


def try_call(label, fn):
    try:
        out = fn()
        print(f"[OK]   {label} -> {out if not isinstance(out, dict) else '<dict ok>'}")
        return out, None
    except Exception as e:
        print(f"[RAISE] {label}: {type(e).__name__}: {e}")
        return None, e


def main():
    results = {}

    # 1. fully empty state
    st1 = new_state()
    print("=== Case 1: fully empty state ===")
    _, e = try_call("compute_all_metrics(empty)", lambda: compute_all_metrics(st1))
    results["empty_metrics"] = e
    _, e = try_call("analyze_schedule(empty)", lambda: analyze_schedule(st1))
    results["empty_analyze"] = e

    # 2. classes present but zero slots/days
    st2 = new_state()
    st2["years"] = {"Y1": ["A"]}; st2["lecturers"] = ["L1"]
    c = new_class(); c["name"] = "X"; c["lecturer"] = "L1"
    c["targets"] = [{"year": "Y1", "branch": "A"}]
    st2["classes"] = [c]  # unplaced, no days/slots
    print("\n=== Case 2: classes but zero days/slots (unplaced) ===")
    _, e = try_call("compute_all_metrics", lambda: compute_all_metrics(st2))
    results["zero_grid_metrics"] = e

    # 3. placed class whose slot NOT in state['slots']
    st3 = new_state()
    st3["days"] = ["monday"]; st3["slots"] = ["09:00", "10:00"]
    st3["classrooms"] = ["R1"]; st3["classroom_capacities"] = {"R1": 0}
    st3["lecturers"] = ["L1"]; st3["years"] = {"Y1": ["A"]}
    c3 = new_class(); c3["name"] = "Y"; c3["lecturer"] = "L1"
    c3["targets"] = [{"year": "Y1", "branch": "A"}]; c3["duration"] = 1
    mark_placed(c3, "monday", "23:59", "R1")  # 23:59 NOT in slots
    st3["classes"] = [c3]
    print("\n=== Case 3: placed class with slot not in slots list ===")
    _, e = try_call("compute_all_metrics(bad slot)", lambda: compute_all_metrics(st3))
    results["bad_slot_metrics"] = e
    _, e = try_call("analyze_schedule(bad slot)", lambda: analyze_schedule(st3))
    results["bad_slot_analyze"] = e

    # 4. single day / single slot
    st4 = new_state()
    st4["days"] = ["monday"]; st4["slots"] = ["09:00"]
    st4["classrooms"] = ["R1"]; st4["classroom_capacities"] = {"R1": 0}
    st4["lecturers"] = ["L1"]; st4["years"] = {"Y1": ["A"]}
    c4 = new_class(); c4["name"] = "Z"; c4["lecturer"] = "L1"
    c4["targets"] = [{"year": "Y1", "branch": "A"}]; c4["duration"] = 1
    mark_placed(c4, "monday", "09:00", "R1")
    st4["classes"] = [c4]
    print("\n=== Case 4: single day/slot ===")
    _, e = try_call("compute_all_metrics(1x1)", lambda: compute_all_metrics(st4))
    results["single_metrics"] = e
    r, e = try_call("analyze_schedule(1x1)", lambda: analyze_schedule(st4))

    # 5. ScheduleAnalytics.analyze empty placements + 0-capacity grid
    print("\n=== Case 5: ScheduleAnalytics edge ===")
    sa_empty = ScheduleAnalytics(new_state())
    _, e = try_call("analyze([]) empty placements", lambda: sa_empty.analyze([]))
    # 0 slots*days but non-empty placements -> _analyze_rooms capacity=0
    st5 = new_state()
    st5["classrooms"] = ["R1"]; st5["lecturers"] = ["L1"]; st5["years"] = {"Y1": ["A"]}
    c5 = new_class(); c5["name"] = "Q"; c5["lecturer"] = "L1"
    c5["targets"] = [{"year": "Y1", "branch": "A"}]; c5["duration"] = 1
    # placements referencing days/slots that don't exist in state
    sa5 = ScheduleAnalytics(st5)  # _total_slots=0, _total_days=0
    _, e = try_call("analyze(placements) with 0 slots*days grid",
                    lambda: sa5.analyze([(c5, "monday", "09:00", "R1")]))
    results["zero_capacity_rooms"] = e

    # 6. all presets
    print("\n=== Case 6: presets through compute_all_metrics + analyze_schedule ===")
    for name in ["tiny", "small", "normal"]:
        st = make_preset(name, seed=3)
        # place a few classes to exercise the placed path
        for i, cls in enumerate(st["classes"][:min(5, len(st["classes"]))]):
            from scheduler_app.core.models import class_uses_physical_room
            room = st["classrooms"][0] if (st["classrooms"] and class_uses_physical_room(cls)) else None
            mark_placed(cls, st["days"][0], st["slots"][0], room)
        _, e1 = try_call(f"compute_all_metrics({name})", lambda st=st: compute_all_metrics(st))
        _, e2 = try_call(f"analyze_schedule({name})", lambda st=st: analyze_schedule(st))

    print("\n=== VERDICT (exceptions raised) ===")
    for k, v in results.items():
        if v is not None:
            print(f"  {k}: {type(v).__name__}: {v}")
    if not any(results.values()):
        print("  no exceptions in the tracked cases")


if __name__ == "__main__":
    main()
