"""Probe: greedy construction recurses once per flexible class, so a
timetable with more flexible classes than the Python recursion limit
raises RecursionError.

ScheduleOptimizer._greedy_construct defines a nested solve(idx) that
calls solve(idx+1) for every class (placed OR skipped), so the first
descent reaches depth == len(flexible). With CPython's default limit
(~1000), a reschedule of ~1000+ flexible classes crashes. In the app,
_do_reschedule is wrapped in `except Exception`, so this surfaces as a
crash-report dialog rather than a result.

We run optimize() on increasing flexible-class counts with a trivial,
conflict-free, single-candidate-per-class fixture (fast, linear descent)
and record which sizes raise RecursionError.
"""
import os
import sys
import tempfile

_sb = tempfile.mkdtemp(prefix="dersis_audit_")
os.environ["HOME"] = _sb
os.environ["USERPROFILE"] = _sb
sys.path.insert(0, r"C:\dev\dersis-app")

from scheduler_app.core.models import new_state, new_class
from scheduler_app.schedule_optimizer import ScheduleOptimizer


def build_state(n):
    """n conflict-free online classes, each pinned to a single candidate
    slot (unique lecturers, no targets) so greedy descends linearly and
    cheaply to depth n."""
    st = new_state()
    st["days"] = ["monday"]
    st["slots"] = ["09:00"]
    st["classrooms"] = ["R001"]
    st["classroom_capacities"] = {"R001": 0}
    st["lecturers"] = [f"L{i}" for i in range(n)]
    st["years"] = {"Year-1": ["A"]}
    classes = []
    for i in range(n):
        c = new_class()
        c["class_code"] = f"C{i}"
        c["name"] = f"C{i}"
        c["lecturer"] = f"L{i}"     # unique -> no lecturer conflicts
        c["targets"] = []           # no group -> no group conflicts
        c["duration"] = 1
        c["location_type"] = "online"   # no room -> no room conflicts
        c["allowed_days"] = ["monday"]
        c["allowed_times"] = ["09:00"]  # exactly one candidate
        classes.append(c)
    st["classes"] = classes
    return st


def try_size(n):
    st = build_state(n)
    opt = ScheduleOptimizer(
        st, weights=None,
        multi_start_runs=1, multi_start_time_limit=30.0,
        lns_iterations=0, lns_time_limit=0.1,
        use_cpsat=False, parallel_workers=-1)
    try:
        placed, unplaced, changes, summary = opt.optimize()
        return ("ok", len(placed), None)
    except RecursionError as exc:
        return ("RecursionError", None, str(exc)[:60])
    except Exception as exc:
        return (type(exc).__name__, None, str(exc)[:80])


def main():
    print(f"sys.getrecursionlimit() = {sys.getrecursionlimit()}")
    print("=" * 56)
    results = {}
    for n in (500, 900, 980, 1100, 1300):
        outcome, placed, err = try_size(n)
        results[n] = outcome
        extra = f" placed={placed}" if placed is not None else f"  [{err}]"
        print(f"  n={n:<5} -> {outcome}{extra}")
    print("=" * 56)
    ok_sizes = [n for n, o in results.items() if o == "ok"]
    fail_sizes = [n for n, o in results.items() if o == "RecursionError"]
    print(f"largest OK size          : {max(ok_sizes) if ok_sizes else None}")
    print(f"RecursionError sizes     : {fail_sizes}")
    print(f"RECURSION LIMIT BUG CONFIRMED: {len(fail_sizes) > 0}")


if __name__ == "__main__":
    main()
