"""Calibration: single-run ScheduleOptimizer timing at normal scale so the
benchmark harness timeouts can be set from evidence, not guesses."""
import os, sys, tempfile, time
_SB = tempfile.mkdtemp(prefix="dersis_calib_home_")
os.environ["HOME"] = _SB
os.environ["USERPROFILE"] = _SB
REPO = r"C:\dev\dersis-app"
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "stress-test", "tests"))
from _fixtures.dataset_gen import make_preset  # noqa: E402


def one(scale, density, max_iter, lns_iter, lns_tl, msr=1, mstl=20.0):
    from scheduler_app.schedule_optimizer import ScheduleOptimizer
    state = make_preset(scale, density=density, seed=42)
    n = len(state["classes"])
    opt = ScheduleOptimizer(
        state, weights={}, max_iterations=max_iter,
        lns_iterations=lns_iter, lns_time_limit=lns_tl,
        multi_start_runs=msr, multi_start_time_limit=mstl,
        use_cpsat=False, parallel_workers=-1)
    t0 = time.perf_counter()
    placed, unplaced, changes, summary = opt.optimize()
    dt = time.perf_counter() - t0
    gs = summary.get("greedy_stats", {})
    print(f"scale={scale} dens={density} maxit={max_iter} "
          f"n={n} wall={dt:.3f}s placed={len(placed)} unplaced={len(unplaced)} "
          f"greedy_iters={gs.get('iterations_used')} "
          f"exhausted={gs.get('budget_exhausted')} "
          f"runs={summary.get('runs_completed')}")
    return dt


if __name__ == "__main__":
    # Compare production greedy budget (100k) vs a capped budget at normal scale
    print("--- normal, capped greedy budget (2000) to isolate per-iter cost ---")
    one("normal", 0.1, 2000, 15, 2.0)
    one("normal", 0.5, 2000, 15, 2.0)
    print("--- normal, PRODUCTION greedy budget (100000) ---")
    one("normal", 0.3, 100000, 15, 2.0)
