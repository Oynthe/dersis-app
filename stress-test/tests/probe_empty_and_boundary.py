"""Probe 1: Empty state and boundary conditions.

Covers briefing items 1 & 2:
 - new_state() with zero days/slots/classes -> reschedule, export (all fmts), analytics
 - single day/slot/room/class
 - duration longer than the grid (duration > n_slots)
 - participants exceeding all room capacities
 - 0-capacity rooms
Reports for each: does it PREVENT / DETECT / REPAIR / SILENTLY-ACCEPT / CRASH.
"""
import _eh_sandbox
_eh_sandbox.enter()

import os
import tempfile
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
        print(f"  OK    {label}: {repr(r)[:120]}")
        return ("OK", r)
    except Exception as e:
        tb = traceback.format_exc().splitlines()
        loc = tb[-2] if len(tb) >= 2 else ""
        print(f"  CRASH {label}: {type(e).__name__}: {e}")
        print(f"        at {loc.strip()}")
        return ("CRASH", e)


def export_all(state, tag):
    results = {}
    for fmt in ("csv", "xlsx", "pdf"):
        path = os.path.join(EVID, f"empty_{tag}.{fmt}")
        results[fmt] = try_call(
            f"export {fmt}",
            lambda p=path, f=fmt: exporter.export_schedule(state, f, p))
    return results


print("=" * 70)
print("CASE A: fully empty new_state() (0 days, 0 slots, 0 classes)")
print("=" * 70)
s = new_state()
try_call("analytics.compute_all_metrics", lambda: analytics.compute_all_metrics(s))
try_call("analytics.room_utilization", lambda: analytics.room_utilization(s))
try_call("analytics.busiest_slots", lambda: analytics.busiest_slots(s))
try_call("analytics.lecturer_load", lambda: analytics.lecturer_load(s))
wf = SchedulingWorkflow(s, weights)
try_call("workflow.reschedule", lambda: wf.reschedule(weights()))
export_all(s, "emptyA")

print()
print("=" * 70)
print("CASE B: single day / single slot / single room / single class (fits)")
print("=" * 70)
s = new_state()
s["days"] = ["monday"]
s["slots"] = ["09:00"]
s["classrooms"] = ["R1"]
s["classroom_capacities"] = {"R1": 30}
s["lecturers"] = ["L1"]
s["years"] = {"Y1": ["A"]}
c = new_class()
c["class_code"] = "C1"; c["name"] = "One"; c["lecturer"] = "L1"
c["targets"] = [{"year": "Y1", "branch": "A"}]; c["duration"] = 1
c["participants"] = 10
s["classes"] = [c]
wf = SchedulingWorkflow(s, weights)
r = try_call("workflow.reschedule (1x1 grid, 1 class)", lambda: wf.reschedule(weights()))
if r[0] == "OK":
    try_call("apply_reschedule", lambda: wf.apply_reschedule(r[1]))
    print(f"        class placed={c['placed']} day={c.get('placed_day')} time={c.get('placed_time')}")
try_call("analytics.compute_all_metrics", lambda: analytics.compute_all_metrics(s))
export_all(s, "singleB")

print()
print("=" * 70)
print("CASE C: duration (5) longer than grid (n_slots=3)")
print("=" * 70)
s = new_state()
s["days"] = ["monday", "tuesday"]
s["slots"] = ["09:00", "10:00", "11:00"]
s["classrooms"] = ["R1"]; s["classroom_capacities"] = {"R1": 30}
s["lecturers"] = ["L1"]; s["years"] = {"Y1": ["A"]}
c = new_class()
c["name"] = "TooLong"; c["lecturer"] = "L1"
c["targets"] = [{"year": "Y1", "branch": "A"}]; c["duration"] = 5
s["classes"] = [c]
wf = SchedulingWorkflow(s, weights)
r = try_call("workflow.reschedule (duration>grid)", lambda: wf.reschedule(weights()))
if r[0] == "OK":
    print(f"        unplaced={len(r[1].unplaced)} placed={len(r[1].placed)}")

print()
print("=" * 70)
print("CASE C2: duration>grid but MANUALLY force-placed then export/analytics")
print("=" * 70)
# Simulate a corrupt state where a class was placed with duration overflowing
c["duration"] = 5
mark_placed(c, "monday", "09:00", "R1")   # occupies slots 0..4 but only 3 exist
print(f"        forced placed={c['placed']} duration={c['duration']} slots={len(s['slots'])}")
try_call("analytics.compute_all_metrics (overflow placement)",
         lambda: analytics.compute_all_metrics(s))
try_call("analytics.busiest_slots (overflow placement)",
         lambda: analytics.busiest_slots(s))
export_all(s, "overflowC2")

print()
print("=" * 70)
print("CASE D: participants (55) exceed ALL room capacities (max 30)")
print("=" * 70)
s = new_state()
s["days"] = ["monday", "tuesday", "wednesday"]
s["slots"] = ["09:00", "10:00", "11:00"]
s["classrooms"] = ["R1", "R2"]
s["classroom_capacities"] = {"R1": 20, "R2": 30}
s["lecturers"] = ["L1"]; s["years"] = {"Y1": ["A"]}
c = new_class()
c["name"] = "BigClass"; c["lecturer"] = "L1"
c["targets"] = [{"year": "Y1", "branch": "A"}]; c["duration"] = 1
c["participants"] = 55
s["classes"] = [c]
wf = SchedulingWorkflow(s, weights)
r = try_call("workflow.reschedule (participants>allrooms)", lambda: wf.reschedule(weights()))
if r[0] == "OK":
    print(f"        placed={len(r[1].placed)} unplaced={len(r[1].unplaced)}")

print()
print("=" * 70)
print("CASE E: 0-capacity rooms only (0 == unlimited per get_room_capacity)")
print("=" * 70)
s = new_state()
s["days"] = ["monday"]
s["slots"] = ["09:00", "10:00"]
s["classrooms"] = ["R1"]
s["classroom_capacities"] = {"R1": 0}
s["lecturers"] = ["L1"]; s["years"] = {"Y1": ["A"]}
c = new_class()
c["name"] = "ZeroCapRoom"; c["lecturer"] = "L1"
c["targets"] = [{"year": "Y1", "branch": "A"}]; c["duration"] = 1
c["participants"] = 999
s["classes"] = [c]
wf = SchedulingWorkflow(s, weights)
r = try_call("workflow.reschedule (0-cap room, 999 participants)",
             lambda: wf.reschedule(weights()))
if r[0] == "OK":
    print(f"        placed={len(r[1].placed)} unplaced={len(r[1].unplaced)}  "
          f"(0-cap treated as unlimited -> should place)")

print()
print("DONE probe_empty_and_boundary")
