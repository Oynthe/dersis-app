"""PROBE 1 + 7: Dashboard quality-score room-key bug.

dashboard.py:441 builds placements with `room = cls.get("room", "")`, but the
class dict (models.new_class) has NO "room" key. So room is ALWAYS "" and gets
passed as room_override into ScheduleAnalytics.analyze / TimetableScorer.
Because get_active_physical_classroom returns room_override verbatim when it is
not the _ROOM_UNSET sentinel, "" wins over the real placed_classroom.

This probe:
  A. Hand-builds a KNOWN small schedule (4 classes, 2 rooms) with real rooms.
  B. Reproduces the dashboard's exact placement-building (room = cls.get("room",""))
     and runs ScheduleAnalytics.analyze + TimetableScorer.score_detailed.
  C. Runs the SAME analysis the CORRECT way (room = effective_room(cls), i.e.
     what logic.analyze_schedule does).
  D. Independently recomputes room utilization + room_switching by hand.
  E. Prints all three side by side.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "tests")))
import _fixtures.sandbox  # noqa: F401  (must be first)

from scheduler_app.core.models import (
    new_state, new_class, mark_placed, effective_room,
)
from scheduler_app.core.schedule_analytics import ScheduleAnalytics
from scheduler_app.core.timetable_scorer import TimetableScorer
from scheduler_app.core.models import effective_day, effective_time


def build_known_state():
    """4 classes, 2 rooms, 1 lecturer shared, 5 days x 4 slots."""
    st = new_state()
    st["days"] = ["monday", "tuesday", "wednesday", "thursday", "friday"]
    st["slots"] = ["09:00", "10:00", "11:00", "12:00"]
    st["classrooms"] = ["R1", "R2"]
    st["classroom_capacities"] = {"R1": 0, "R2": 0}
    st["lecturers"] = ["Prof-A"]
    st["years"] = {"Year-1": ["A"]}

    classes = []
    # Class 1: Monday 09:00 R1 (Prof-A, Year1/A)
    c1 = new_class(); c1["name"] = "C1"; c1["lecturer"] = "Prof-A"
    c1["targets"] = [{"year": "Year-1", "branch": "A"}]; c1["duration"] = 1
    mark_placed(c1, "monday", "09:00", "R1")
    # Class 2: Monday 11:00 R2 (Prof-A) -> same lecturer switches room same day
    c2 = new_class(); c2["name"] = "C2"; c2["lecturer"] = "Prof-A"
    c2["targets"] = [{"year": "Year-1", "branch": "A"}]; c2["duration"] = 1
    mark_placed(c2, "monday", "11:00", "R2")
    # Class 3: Tuesday 09:00 R1 (Prof-A)
    c3 = new_class(); c3["name"] = "C3"; c3["lecturer"] = "Prof-A"
    c3["targets"] = [{"year": "Year-1", "branch": "A"}]; c3["duration"] = 1
    mark_placed(c3, "tuesday", "09:00", "R1")
    # Class 4: Tuesday 10:00 R1 (Prof-A)
    c4 = new_class(); c4["name"] = "C4"; c4["lecturer"] = "Prof-A"
    c4["targets"] = [{"year": "Year-1", "branch": "A"}]; c4["duration"] = 1
    mark_placed(c4, "tuesday", "10:00", "R1")

    classes = [c1, c2, c3, c4]
    st["classes"] = classes
    return st


def dashboard_placements(state):
    """EXACT reproduction of dashboard.py:436-442."""
    placements = []
    for cls in state["classes"]:
        day = effective_day(cls)
        slot = effective_time(cls)
        if day and slot:
            room = cls.get("room", "")  # <-- THE BUG: no "room" key exists
            placements.append((cls, day, slot, room))
    return placements


def correct_placements(state):
    """What logic.analyze_schedule does: room = effective_room(cls)."""
    placements = []
    for cls in state["classes"]:
        day = effective_day(cls)
        slot = effective_time(cls)
        if day and slot:
            room = effective_room(cls)
            placements.append((cls, day, slot, room))
    return placements


def main():
    st = build_known_state()

    # Sanity: confirm no "room" key in class dicts
    keys_with_room = [c["name"] for c in st["classes"] if "room" in c]
    print(f"[fact] classes containing a literal 'room' key: {keys_with_room} "
          f"(expected: [] -> cls.get('room','') always returns '')")
    print(f"[fact] c1['placed_classroom'] = {st['classes'][0]['placed_classroom']!r}")
    print(f"[fact] cls.get('room','') for c1 = {st['classes'][0].get('room','')!r}")
    print()

    dash_p = dashboard_placements(st)
    corr_p = correct_placements(st)
    print("Dashboard placement tuples (room field):",
          [(p[0]["name"], p[3]) for p in dash_p])
    print("Correct  placement tuples (room field):",
          [(p[0]["name"], p[3]) for p in corr_p])
    print()

    # ── ScheduleAnalytics room metrics ──
    sa = ScheduleAnalytics(st)
    dash_report = sa.analyze(dash_p)
    corr_report = sa.analyze(corr_p)

    print("=== ScheduleAnalytics.analyze -> room_metrics ===")
    print("DASHBOARD path room_metrics:", dash_report["room_metrics"])
    print("CORRECT   path room_metrics:", corr_report["room_metrics"])
    print()
    print(f"DASHBOARD global_score={dash_report['global_score']:.3f} grade={dash_report['grade']}")
    print(f"CORRECT   global_score={corr_report['global_score']:.3f} grade={corr_report['grade']}")
    print()

    # ── TimetableScorer breakdown (room_switching) ──
    scorer = TimetableScorer(st)
    dash_bd = scorer.score_detailed(dash_p)
    corr_bd = scorer.score_detailed(corr_p)
    print("=== TimetableScorer.score_detailed -> room_switching ===")
    print(f"DASHBOARD room_switching = {dash_bd['room_switching']}")
    print(f"CORRECT   room_switching = {corr_bd['room_switching']}")
    print(f"DASHBOARD full breakdown = {dash_bd}")
    print(f"CORRECT   full breakdown = {corr_bd}")
    print()

    # ── Independent hand recomputation ──
    total_capacity = len(st["slots"]) * len(st["days"])  # 4*5 = 20
    # R1 used slots: c1(mon09),c3(tue09),c4(tue10) = 3 ; R2 used: c2(mon11)=1
    r1_used = 3
    r2_used = 1
    print("=== INDEPENDENT HAND RECOMPUTATION ===")
    print(f"capacity_per_room = slots*days = {len(st['slots'])}*{len(st['days'])} = {total_capacity}")
    print(f"R1 utilization = {r1_used}/{total_capacity} = {r1_used/total_capacity:.4f}")
    print(f"R2 utilization = {r2_used}/{total_capacity} = {r2_used/total_capacity:.4f}")
    print(f"avg_utilization (hand) = {((r1_used+r2_used)/total_capacity)/2:.4f}")
    # Room switching: Prof-A on monday uses R1 and R2 -> 1 switch * 0.8 = 0.8
    print("room_switching (hand): Prof-A on monday uses {R1,R2} -> "
          "(2-1)*0.8 = 0.8; tuesday only R1 -> 0. TOTAL = 0.8")
    print()

    # ── Verdict ──
    dash_room_zero = (dash_report["room_metrics"]["summary"]["total_rooms"] == 0)
    corr_room_ok = (corr_report["room_metrics"]["summary"]["total_rooms"] == 2)
    print("=== VERDICT ===")
    print(f"Dashboard path zeroes room metrics: {dash_room_zero}")
    print(f"Correct path yields 2 rooms with util: {corr_room_ok}")
    print(f"Dashboard room_switching==0 (bug): {dash_bd['room_switching']==0}")
    print(f"Correct room_switching==0.8: {abs(corr_bd['room_switching']-0.8)<1e-9}")


if __name__ == "__main__":
    main()
