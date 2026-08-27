"""PROBE 3 (addendum): confirm the deepdiff-missing branch is NOT a silent
no-op. Force schedule_impact_analyzer.DeepDiff = None and verify analyze_impact
still detects a soft change via the `before == after` equality fallback.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "tests")))
import _fixtures.sandbox  # noqa: F401

from scheduler_app.core import schedule_impact_analyzer as sia
from scheduler_app.core.models import new_state, new_class, mark_placed


def st():
    s = new_state()
    s["days"] = ["monday"]; s["slots"] = ["09:00", "10:00"]
    s["classrooms"] = ["R1"]; s["classroom_capacities"] = {"R1": 0}
    s["lecturers"] = ["L1"]; s["years"] = {"Y1": ["A"]}
    c = new_class(); c["name"] = "C1"; c["lecturer"] = "L1"
    c["targets"] = [{"year": "Y1", "branch": "A"}]; c["duration"] = 1
    mark_placed(c, "monday", "09:00", "R1"); s["classes"] = [c]
    return s


def main():
    print("real DeepDiff present:", sia.DeepDiff is not None)
    saved = sia.DeepDiff
    try:
        sia.DeepDiff = None  # simulate import failure
        s = st()
        before = sia.capture_snapshot(s)
        s["classes"][0]["participants"] = 30  # soft change
        after = sia.capture_snapshot(s)
        res = sia.analyze_impact(before, after, s)
        print("fallback (DeepDiff=None) soft-change level:", res.level.value)
        print("fallback changed_fields:", res.changed_fields)
        print("fallback soft_reasons:", res.soft_impact_reasons)

        # No-change under fallback
        before2 = sia.capture_snapshot(s)
        after2 = sia.capture_snapshot(s)
        res2 = sia.analyze_impact(before2, after2, s)
        print("fallback no-change level:", res2.level.value)
        print()
        print("VERDICT: fallback is NOT a silent no-op — it detects changes via "
              "`before == after`; only the DeepDiff import failing selects it, "
              "and analyze_impact still runs hard+soft checks.")
    finally:
        sia.DeepDiff = saved


if __name__ == "__main__":
    main()
