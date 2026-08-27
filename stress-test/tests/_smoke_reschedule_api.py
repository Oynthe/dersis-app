"""Smoke probe: validate the reschedule production path + capped optimizer API.

Sets up an isolated HOME sandbox, builds a tiny/small state via the shared
fixture generator, and runs BOTH:
  (1) optimized_reschedule_all(...) with capped params (the function that
      workflow.reschedule delegates to), parallel_workers=-1.
  (2) the true SchedulingWorkflow(...).reschedule({}, use_cpsat=False) wrapper.
Prints timings, placed/unplaced counts, and the summary keys so the full
benchmark harness can be built against verified shapes.
"""
import os, sys, tempfile, time, json

# ── MANDATORY sandbox: must precede any scheduler_app import ──
_SB = tempfile.mkdtemp(prefix="dersis_smoke_home_")
os.environ["HOME"] = _SB
os.environ["USERPROFILE"] = _SB

REPO = r"C:\dev\dersis-app"
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "stress-test", "tests"))

from _fixtures.dataset_gen import make_preset  # noqa: E402


def run():
    from scheduler_app.logic import optimized_reschedule_all
    from scheduler_app.workflow import SchedulingWorkflow

    for scale in ("tiny", "small"):
        state = make_preset(scale, density=0.3, seed=42)
        n = len(state["classes"])

        # (1) capped optimized_reschedule_all
        t0 = time.perf_counter()
        placed, unplaced, changes, summary = optimized_reschedule_all(
            state, weights={},
            multi_start_runs=2, multi_start_time_limit=8.0,
            parallel_workers=-1, use_cpsat=False)
        dt = time.perf_counter() - t0
        print(f"[capped ORA] scale={scale} n={n} wall={dt:.3f}s "
              f"placed={len(placed)} unplaced={len(unplaced)} "
              f"moved={len(changes)}")
        print("   summary keys:", sorted(summary.keys()))
        print("   runs_completed:", summary.get("runs_completed"),
              "total_time:", round(summary.get("total_time", -1), 3),
              "greedy_stats:", summary.get("greedy_stats"))

        # (2) true production wrapper
        state2 = make_preset(scale, density=0.3, seed=42)
        wf = SchedulingWorkflow(state2, get_weights=lambda: {})
        t0 = time.perf_counter()
        res = wf.reschedule({}, use_cpsat=False)
        dt2 = time.perf_counter() - t0
        print(f"[workflow.reschedule] scale={scale} wall={dt2:.3f}s "
              f"placed={len(res.placed)} unplaced={len(res.unplaced)} "
              f"changes={len(res.changes)} "
              f"has_analytics={res.analytics is not None} "
              f"has_explanation={res.explanation is not None} "
              f"has_negotiation={res.negotiation_result is not None}")
        print()


if __name__ == "__main__":
    run()
