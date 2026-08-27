"""Confirm (non)determinism: 3 identical-seed, identical-config runs of the
production optimizer (parallel_workers=-1). Compares placed count, moved count,
and the post-optimization quality total. Any variance => non-deterministic."""
import os, sys, tempfile, time
_SB = tempfile.mkdtemp(prefix="dersis_det_home_")
os.environ["HOME"] = _SB
os.environ["USERPROFILE"] = _SB
REPO = r"C:\dev\dersis-app"
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "stress-test", "tests"))
from _fixtures.dataset_gen import make_preset  # noqa


def run_once():
    from scheduler_app.schedule_optimizer import ScheduleOptimizer
    state = make_preset("small", density=0.3, seed=42)
    opt = ScheduleOptimizer(state, weights={}, max_iterations=100000,
                            lns_iterations=200, lns_time_limit=30.0,
                            multi_start_runs=1, multi_start_time_limit=25.0,
                            use_cpsat=False, parallel_workers=-1)
    t0 = time.perf_counter()
    placed, unplaced, changes, summary = opt.optimize()
    dt = time.perf_counter() - t0
    q = summary.get("after", {}).get("total")
    return dt, len(placed), len(changes), round(float(q), 4)


if __name__ == "__main__":
    rows = [run_once() for _ in range(3)]
    print("run  wall     placed  moved  q_after_total")
    for i, (dt, p, m, q) in enumerate(rows, 1):
        print(f"{i}    {dt:6.3f}s  {p:4d}   {m:4d}   {q}")
    qs = {q for _, _, _, q in rows}
    ps = {p for _, p, _, _ in rows}
    print("DETERMINISTIC placed-count:", len(ps) == 1, "  values:", sorted(ps))
    print("DETERMINISTIC quality:", len(qs) == 1, "  values:", sorted(qs))
