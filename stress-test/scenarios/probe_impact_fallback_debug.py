"""Debug: why does the DeepDiff=None fallback miss a participants change?"""
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
    saved = sia.DeepDiff
    sia.DeepDiff = None
    try:
        s = st()
        before = sia.capture_snapshot(s)
        s["classes"][0]["participants"] = 30
        after = sia.capture_snapshot(s)

        print("before _classes[0] participants:", before["_classes"][0]["participants"])
        print("after  _classes[0] participants:", after["_classes"][0]["participants"])
        print("before == after ?", before == after)
        diff = sia._diff_snapshots(before, after)
        print("_diff_snapshots ->", diff)
        changed = sia._extract_changed_fields(diff) if diff else []
        print("_extract_changed_fields ->", changed)
        soft = sia._check_soft_impacts(before, after, diff or {})
        print("_check_soft_impacts ->", soft)
        print()
        print("Now WITH deepdiff for the same change:")
    finally:
        sia.DeepDiff = saved

    s = st()
    before = sia.capture_snapshot(s)
    s["classes"][0]["participants"] = 30
    after = sia.capture_snapshot(s)
    diff = sia._diff_snapshots(before, after)
    changed = sia._extract_changed_fields(diff)
    print("deepdiff changed_fields ->", changed)
    print("deepdiff soft ->", sia._check_soft_impacts(before, after, diff))


if __name__ == "__main__":
    main()
