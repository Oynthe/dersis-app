"""Probe: optimizer runtime / placement / quality vs scale.

Bounded scaling snapshot with tight time caps. Reports wall time, placed
vs total, unplaced count, and best tt-score across preset sizes. Also
re-checks determinism at each scale with 2 back-to-back runs on identical
input (score delta > 0 => non-reproducible).
"""
import os
import sys
import copy
import time
import tempfile

_sb = tempfile.mkdtemp(prefix="dersis_audit_")
os.environ["HOME"] = _sb
os.environ["USERPROFILE"] = _sb
sys.path.insert(0, r"C:\dev\dersis-app")
sys.path.insert(0, r"C:\dev\dersis-app\stress-test\tests")

from _fixtures.dataset_gen import make_preset
from scheduler_app.schedule_optimizer import ScheduleOptimizer
from scheduler_app.timetable_scorer import TimetableScorer


def run(state, cap):
    st = copy.deepcopy(state)
    t0 = time.perf_counter()
    opt = ScheduleOptimizer(
        st, weights=None,
        multi_start_runs=2, multi_start_time_limit=cap,
        lns_iterations=60, lns_time_limit=cap / 2,
        use_cpsat=False, parallel_workers=-1)
    placed, unplaced, changes, summary = opt.optimize()
    dt = time.perf_counter() - t0
    tt = TimetableScorer(st, weights=None)
    score = tt.score([(c, d, s, r) for c, d, s, r in placed])
    return dt, len(placed), len(unplaced), score


def main():
    rows = []
    for preset, cap in [("tiny", 3.0), ("small", 4.0),
                        ("normal", 6.0), ("large", 8.0)]:
        state = make_preset(preset)
        total = len(state["classes"])
        dt1, p1, u1, s1 = run(state, cap)
        dt2, p2, u2, s2 = run(state, cap)   # determinism re-run
        rows.append((preset, total, dt1, p1, u1, s1, abs(s1 - s2), p1 == p2))
        print(f"  {preset:<10} classes={total:<5} time={dt1:6.2f}s "
              f"placed={p1}/{total} unplaced={u1} score={s1:8.3f} "
              f"| rerun score_delta={abs(s1-s2):7.3f} same_placed={p1==p2}")

    print("=" * 70)
    print(f"{'preset':<10}{'classes':>8}{'sec':>8}{'placed':>8}"
          f"{'unplaced':>9}{'score':>10}{'rerun_dScore':>14}")
    for preset, total, dt, p, u, s, ds, samep in rows:
        print(f"{preset:<10}{total:>8}{dt:>8.2f}{p:>8}{u:>9}{s:>10.3f}{ds:>14.3f}")
    nondet = sum(1 for r in rows if r[6] > 1e-6)
    print("=" * 70)
    print(f"scales where 2 identical-input runs disagreed on score: "
          f"{nondet}/{len(rows)}")


if __name__ == "__main__":
    main()
