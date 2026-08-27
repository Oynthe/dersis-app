"""PROBE 1 (widget level): construct DashboardWidget, call refresh, inspect
the actual chart data a user would see. Confirms the room_switching breakdown
bar is 0 and correlates the room-utilization card (separate, correct path).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "tests")))
import _fixtures.sandbox  # noqa: F401

from PyQt6.QtWidgets import QApplication
from scheduler_app.translations import set_language
from scheduler_app.core.models import new_state, new_class, mark_placed
from scheduler_app.ui.dashboard import DashboardWidget

EVIDENCE = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "evidence"))
_os.makedirs(EVIDENCE, exist_ok=True)


def build_state():
    st = new_state()
    st["days"] = ["monday", "tuesday", "wednesday", "thursday", "friday"]
    st["slots"] = ["09:00", "10:00", "11:00", "12:00"]
    st["classrooms"] = ["R1", "R2"]
    st["classroom_capacities"] = {"R1": 0, "R2": 0}
    st["lecturers"] = ["Prof-A"]
    st["years"] = {"Year-1": ["A"]}
    specs = [("C1", "monday", "09:00", "R1"), ("C2", "monday", "11:00", "R2"),
             ("C3", "tuesday", "09:00", "R1"), ("C4", "tuesday", "10:00", "R1")]
    for name, d, s, r in specs:
        c = new_class(); c["name"] = name; c["lecturer"] = "Prof-A"
        c["targets"] = [{"year": "Year-1", "branch": "A"}]; c["duration"] = 1
        mark_placed(c, d, s, r)
        st["classes"].append(c)
    return st


def main():
    set_language("tr")
    app = QApplication.instance() or QApplication(_sys.argv)
    st = build_state()

    w = DashboardWidget()
    w.resize(1000, 800)
    w.refresh(st)

    # Inspect the breakdown chart data (Schedule Quality tab)
    print("=== Score-Breakdown chart (_breakdown_chart._data) — what user sees ===")
    for label, val in w._breakdown_chart._data:
        print(f"  {label!r}: {val}")

    # Inspect the room utilization card (separate analytics.py path)
    print()
    print("Room-use card value (avg_room_use):", w._card_rooms._value.text())
    print("Total-gaps card value:", w._card_gaps._value.text())

    # Inspect room utilization chart (analytics path)
    print("Room-utilization chart data:", w._rooms_chart._data)

    # Quality gauge score
    print("Quality gauge score:", w._quality_gauge._score,
          "label:", w._quality_gauge._label)

    # Screenshot the whole dashboard
    pm = w.grab()
    out = _os.path.join(EVIDENCE, "dashboard-room-switching-zero.png")
    pm.save(out)
    print("\nScreenshot saved:", out)

    # Explicit check: is room_switching bar 0 despite a real room switch?
    rs = dict(w._breakdown_chart._data)
    # find the room_switching label (translated)
    from scheduler_app.translations import tr
    rs_label = tr("dashboard.room_switching")
    print(f"\nroom_switching bar (label={rs_label!r}) value =", rs.get(rs_label))
    print("Ground truth: Prof-A switches R1->R2 on monday (real switch exists).")


if __name__ == "__main__":
    main()
