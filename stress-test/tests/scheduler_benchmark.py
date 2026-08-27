"""DERSIS scheduling-engine scalability benchmark harness.

Phase: TEST & MEASURE (read-only w.r.t. scheduler_app). Measures the production
reschedule path across scale (tiny..pathological) x constraint density, with a
per-cell HARD wall-clock timeout enforced by running each cell in a subprocess
that the parent kills on TimeoutExpired.

Production path
---------------
workflow.reschedule({}, use_cpsat=False) delegates to
optimized_reschedule_all(...) which is a 3-line wrapper around
ScheduleOptimizer(...).optimize(). We instantiate ScheduleOptimizer directly
(same production code) so we can bound the *number of restarts* and *LNS time*
and DISABLE the worker-process scoring pool (parallel_workers=-1) for clean,
reproducible single-process timing.

CAPS APPLIED (documented, deliberate) vs production defaults:
  multi_start_runs      : 1   (prod 5)      -> one restart; real latency ~<=5x
  multi_start_time_limit: 25s (prod 120s)
  parallel_workers      : -1  (prod 0/auto) -> scoring pool DISABLED
  max_iterations        : 100000 (prod default, UNCHANGED -- the cliff driver)
  lns_iterations        : 200 (prod default)
  lns_time_limit        : 30s (prod default, bounded by multi_start_time_limit)
Everything else is production default. use_cpsat toggled per phase.

Usage:
  python scheduler_benchmark.py --phase main|curve|cpsat|determinism|infeasible|memory
  python scheduler_benchmark.py --worker <args.json> <out.json>   (internal)
"""
import os, sys, json, time, tempfile, subprocess, argparse, csv

REPO = r"C:\dev\dersis-app"
EVIDENCE = os.path.join(REPO, "stress-test", "evidence")
CSV_PATH = os.path.join(EVIDENCE, "scheduler_benchmark.csv")

# ── Scale grid params (mirror dataset_gen presets so grid_cells is derivable) ──
SCALE_SIZES = {
    "tiny":        dict(n_classes=5,    n_rooms=2,  n_slots=8, n_days=5),
    "small":       dict(n_classes=25,   n_rooms=4,  n_slots=8, n_days=5),
    "normal":      dict(n_classes=80,   n_rooms=8,  n_slots=8, n_days=5),
    "large":       dict(n_classes=250,  n_rooms=16, n_slots=8, n_days=5),
    "very_large":  dict(n_classes=600,  n_rooms=30, n_slots=8, n_days=5),
    "pathological":dict(n_classes=1200, n_rooms=40, n_slots=8, n_days=5),
}

CSV_COLS = ["scale", "n_classes", "n_rooms", "n_slots", "grid_cells", "density",
            "config", "seed", "status", "wall_seconds", "placed", "unplaced",
            "moved", "greedy_iters", "greedy_exhausted", "runs_completed",
            "opt_total_time", "q_before_total", "q_after_total",
            "cpsat_used", "cpsat_status", "peak_mem_mib", "note"]


# ══════════════════════════════════════════════════════════════════════════════
# WORKER  (runs in a fresh subprocess; parent enforces the hard kill)
# ══════════════════════════════════════════════════════════════════════════════
def worker(args_path, out_path):
    with open(args_path) as f:
        A = json.load(f)

    # MANDATORY sandbox before any scheduler_app import
    sb = tempfile.mkdtemp(prefix="dersis_bench_home_")
    os.environ["HOME"] = sb
    os.environ["USERPROFILE"] = sb
    sys.path.insert(0, REPO)
    sys.path.insert(0, os.path.join(REPO, "stress-test", "tests"))

    from _fixtures.dataset_gen import make_preset, make_state  # noqa

    result = {"status": "ERROR", "note": ""}
    trace = A.get("tracemalloc", False)
    tm = None
    if trace:
        import tracemalloc
        tracemalloc.start()
        tm = tracemalloc

    try:
        if A.get("infeasible_kind"):
            state = _build_infeasible(make_state, A["infeasible_kind"])
        else:
            state = make_preset(A["scale"], density=A["density"], seed=A["seed"])
        n = len(state["classes"])

        if A["mode"] == "workflow":
            from scheduler_app.workflow import SchedulingWorkflow
            wf = SchedulingWorkflow(state, get_weights=lambda: {})
            t0 = time.perf_counter()
            res = wf.reschedule({}, use_cpsat=A.get("use_cpsat", False))
            wall = time.perf_counter() - t0
            summary = res.summary or {}
            placed, unplaced, changes = res.placed, res.unplaced, res.changes
            result["has_negotiation"] = res.negotiation_result is not None
            result["neg_sample"] = _neg_sample(res.negotiation_result)
        else:  # mode == "optimizer" (default production optimizer, capped)
            from scheduler_app.schedule_optimizer import ScheduleOptimizer
            opt = ScheduleOptimizer(
                state, weights={},
                max_iterations=A.get("max_iterations", 100000),
                lns_iterations=A.get("lns_iterations", 200),
                lns_time_limit=A.get("lns_time_limit", 30.0),
                multi_start_runs=A.get("multi_start_runs", 1),
                multi_start_time_limit=A.get("multi_start_time_limit", 25.0),
                use_cpsat=A.get("use_cpsat", False),
                cpsat_time_limit=A.get("cpsat_time_limit", 5.0),
                parallel_workers=-1)
            t0 = time.perf_counter()
            placed, unplaced, changes, summary = opt.optimize()
            wall = time.perf_counter() - t0

        gs = summary.get("greedy_stats", {}) or {}
        result.update({
            "status": "OK",
            "n_classes": n,
            "wall_seconds": round(wall, 4),
            "placed": len(placed),
            "unplaced": len(unplaced),
            "moved": len(changes),
            "greedy_iters": gs.get("iterations_used"),
            "greedy_exhausted": gs.get("budget_exhausted"),
            "runs_completed": summary.get("runs_completed"),
            "opt_total_time": round(summary.get("total_time", 0.0), 4),
            "q_before_total": _q(summary, "before"),
            "q_after_total": _q(summary, "after"),
            "cpsat_used": summary.get("cpsat_used"),
            "cpsat_status": summary.get("cpsat_status_label")
                            or summary.get("cpsat_status"),
        })
        if A.get("unplaced_reasons"):
            result["unplaced_reasons"] = _reason_hist(unplaced)
    except Exception as e:
        import traceback
        result["status"] = "EXC"
        result["note"] = f"{type(e).__name__}: {e}"
        result["trace"] = traceback.format_exc()[-1500:]

    if tm is not None:
        cur, peak = tm.get_traced_memory()
        result["peak_mem_mib"] = round(peak / (1024 * 1024), 2)
        tm.stop()

    with open(out_path, "w") as f:
        json.dump(result, f)


def _q(summary, which):
    d = summary.get(which) or {}
    try:
        return round(float(d.get("total", 0.0)), 3)
    except Exception:
        return None


def _reason_hist(unplaced):
    from collections import Counter
    c = Counter()
    for _cls, reason in unplaced:
        key = str(reason).split(";")[0].strip()[:60]
        c[key] += 1
    return dict(c.most_common(6))


def _neg_sample(neg):
    if not neg:
        return None
    s = json.dumps(neg, default=str)
    return s[:600]


def _build_infeasible(make_state, kind):
    """Hand-built obviously-infeasible / barely-feasible instances."""
    if kind == "oversubscribed":
        # 5 days x 6 slots x 2 rooms = 60 room-slot cells. Create 120 classes,
        # each duration 2 -> ~240 class-hours >> 60 cells. All share 1 lecturer
        # and 1 room requirement to force massive conflict. INFEASIBLE.
        state = make_state(n_days=5, n_slots=6, n_rooms=2, n_lecturers=1,
                           n_years=2, branches_per_year=1, n_classes=120,
                           density=0.0, seed=7, max_duration=2)
        for c in state["classes"]:
            c["lecturer"] = state["lecturers"][0]
            c["duration"] = 2
            c["required_classrooms"] = [state["classrooms"][0]]
            c["participants"] = 0
        return state
    if kind == "barely":
        # density 0.9 at small-normal scale: heavily constrained but maybe feasible
        from _fixtures.dataset_gen import make_preset
        return make_preset("normal", density=0.9, seed=11)
    raise ValueError(kind)


# ══════════════════════════════════════════════════════════════════════════════
# PARENT  (spawns workers, enforces hard timeout, writes CSV)
# ══════════════════════════════════════════════════════════════════════════════
def run_cell(cell, hard_kill):
    """Run one cell in a subprocess; kill after hard_kill seconds."""
    tmp = tempfile.mkdtemp(prefix="dersis_bench_io_")
    args_path = os.path.join(tmp, "args.json")
    out_path = os.path.join(tmp, "out.json")
    with open(args_path, "w") as f:
        json.dump(cell, f)

    py = sys.executable
    t0 = time.perf_counter()
    killed = False
    try:
        subprocess.run([py, os.path.abspath(__file__), "--worker",
                        args_path, out_path],
                       timeout=hard_kill,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.TimeoutExpired:
        killed = True
    wall_outer = time.perf_counter() - t0

    if killed:
        return {"status": "TIMEOUT_KILLED", "wall_seconds": round(wall_outer, 2),
                "note": f"hard-killed at {hard_kill}s"}
    if os.path.exists(out_path):
        with open(out_path) as f:
            return json.load(f)
    return {"status": "NO_OUTPUT", "note": "worker produced no result file"}


def sizes_for(scale):
    s = SCALE_SIZES[scale]
    grid = s["n_days"] * s["n_slots"] * s["n_rooms"]
    return s, grid


def to_row(scale, density, config, cell_args, res):
    s, grid = sizes_for(scale) if scale in SCALE_SIZES else ({}, "")
    return {
        "scale": scale,
        "n_classes": res.get("n_classes", s.get("n_classes", "")),
        "n_rooms": s.get("n_rooms", ""),
        "n_slots": s.get("n_slots", ""),
        "grid_cells": grid,
        "density": density,
        "config": config,
        "seed": cell_args.get("seed", ""),
        "status": res.get("status", ""),
        "wall_seconds": res.get("wall_seconds", ""),
        "placed": res.get("placed", ""),
        "unplaced": res.get("unplaced", ""),
        "moved": res.get("moved", ""),
        "greedy_iters": res.get("greedy_iters", ""),
        "greedy_exhausted": res.get("greedy_exhausted", ""),
        "runs_completed": res.get("runs_completed", ""),
        "opt_total_time": res.get("opt_total_time", ""),
        "q_before_total": res.get("q_before_total", ""),
        "q_after_total": res.get("q_after_total", ""),
        "cpsat_used": res.get("cpsat_used", ""),
        "cpsat_status": res.get("cpsat_status", ""),
        "peak_mem_mib": res.get("peak_mem_mib", ""),
        "note": res.get("note", ""),
    }


def append_csv(rows):
    os.makedirs(EVIDENCE, exist_ok=True)
    exists = os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS)
        if not exists:
            w.writeheader()
        for r in rows:
            w.writerow(r)


def log(*a):
    print(*a, flush=True)


# ── Phase definitions ─────────────────────────────────────────────────────────
def phase_main():
    """Single-restart production optimizer across scale x density."""
    plan = [
        ("tiny",       [0.1, 0.3, 0.5, 0.7]),
        ("small",      [0.1, 0.3, 0.5, 0.7]),
        ("normal",     [0.1, 0.3, 0.5, 0.7]),
        ("large",      [0.1, 0.5, 0.7]),
        ("very_large", [0.3]),
    ]
    hard_kill = 55
    rows = []
    for scale, densities in plan:
        for d in densities:
            cell = dict(mode="optimizer", scale=scale, density=d, seed=42,
                        multi_start_runs=1, multi_start_time_limit=25.0,
                        max_iterations=100000, lns_iterations=200,
                        lns_time_limit=30.0, use_cpsat=False,
                        unplaced_reasons=True)
            res = run_cell(cell, hard_kill)
            log(f"[main] {scale:11s} d={d} -> {res.get('status')} "
                f"wall={res.get('wall_seconds')} placed={res.get('placed')} "
                f"unplaced={res.get('unplaced')} "
                f"greedy_iters={res.get('greedy_iters')} "
                f"exhausted={res.get('greedy_exhausted')}")
            rows.append(to_row(scale, d, "single_restart_prod", cell, res))
    append_csv(rows)


def phase_curve():
    """Construction-scaling curve: fixed small greedy budget, LNS off, so the
    per-run construction machinery (graph build, candidate gen, greedy) is the
    signal. Fittable even where full runs time out."""
    hard_kill = 60
    rows = []
    scales = ["tiny", "small", "normal", "large", "very_large", "pathological"]
    for scale in scales:
        cell = dict(mode="optimizer", scale=scale, density=0.3, seed=42,
                    multi_start_runs=1, multi_start_time_limit=60.0,
                    max_iterations=800, lns_iterations=1, lns_time_limit=0.2,
                    use_cpsat=False)
        res = run_cell(cell, hard_kill)
        log(f"[curve] {scale:11s} -> {res.get('status')} "
            f"wall={res.get('wall_seconds')} n={res.get('n_classes')} "
            f"placed={res.get('placed')}")
        rows.append(to_row(scale, 0.3, "construction_curve_maxit800", cell, res))
    append_csv(rows)


def phase_cpsat():
    """CP-SAT deep path on small & normal, time_limit ~5s."""
    hard_kill = 100
    rows = []
    for scale in ["small", "normal"]:
        cell = dict(mode="optimizer", scale=scale, density=0.3, seed=42,
                    multi_start_runs=1, multi_start_time_limit=20.0,
                    max_iterations=100000, lns_iterations=200,
                    lns_time_limit=10.0, use_cpsat=True, cpsat_time_limit=5.0)
        res = run_cell(cell, hard_kill)
        log(f"[cpsat] {scale:11s} -> {res.get('status')} "
            f"wall={res.get('wall_seconds')} placed={res.get('placed')} "
            f"cpsat_used={res.get('cpsat_used')} "
            f"cpsat_status={res.get('cpsat_status')}")
        rows.append(to_row(scale, 0.3, "cpsat_deep_tl5", cell, res))
    append_csv(rows)


def phase_determinism():
    """Same seed twice, identical config -> compare wall & placement & quality."""
    hard_kill = 60
    rows = []
    for rep in (1, 2):
        cell = dict(mode="optimizer", scale="small", density=0.3, seed=42,
                    multi_start_runs=1, multi_start_time_limit=25.0,
                    max_iterations=100000, lns_iterations=200,
                    lns_time_limit=30.0, use_cpsat=False)
        res = run_cell(cell, hard_kill)
        log(f"[determinism rep{rep}] -> {res.get('status')} "
            f"wall={res.get('wall_seconds')} placed={res.get('placed')} "
            f"q_after={res.get('q_after_total')} "
            f"greedy_iters={res.get('greedy_iters')}")
        rows.append(to_row("small", 0.3, f"determinism_rep{rep}", cell, res))
    append_csv(rows)


def phase_infeasible():
    hard_kill = 90
    rows = []
    # (a) hand-built oversubscribed (class-hours >> grid capacity) via workflow
    cell = dict(mode="workflow", infeasible_kind="oversubscribed", seed=7,
                use_cpsat=False, unplaced_reasons=True)
    res = run_cell(cell, hard_kill)
    log(f"[infeasible oversubscribed] -> {res.get('status')} "
        f"wall={res.get('wall_seconds')} placed={res.get('placed')} "
        f"unplaced={res.get('unplaced')} "
        f"has_negotiation={res.get('has_negotiation')}")
    if res.get("unplaced_reasons"):
        log("   unplaced reasons:", res["unplaced_reasons"])
    if res.get("neg_sample"):
        log("   negotiation sample:", res["neg_sample"][:300])
    rows.append(to_row("infeasible_oversub", 0.0, "workflow_infeasible", cell, res))

    # (b) barely-feasible: normal @ density 0.9 via optimizer
    cell2 = dict(mode="optimizer", infeasible_kind="barely", seed=11,
                 multi_start_runs=1, multi_start_time_limit=25.0,
                 max_iterations=100000, lns_iterations=200, lns_time_limit=30.0,
                 use_cpsat=False, unplaced_reasons=True)
    res2 = run_cell(cell2, hard_kill)
    log(f"[barely-feasible normal d=0.9] -> {res2.get('status')} "
        f"wall={res2.get('wall_seconds')} placed={res2.get('placed')} "
        f"unplaced={res2.get('unplaced')} "
        f"greedy_exhausted={res2.get('greedy_exhausted')}")
    if res2.get("unplaced_reasons"):
        log("   unplaced reasons:", res2["unplaced_reasons"])
    rows.append(to_row("barely_normal_d0.9", 0.9, "optimizer_barely", cell2, res2))
    append_csv(rows)


def phase_memory():
    """tracemalloc peak on a subset (separate from timing sweep)."""
    hard_kill = 70
    rows = []
    for scale in ["small", "normal", "large"]:
        cell = dict(mode="optimizer", scale=scale, density=0.3, seed=42,
                    multi_start_runs=1, multi_start_time_limit=25.0,
                    max_iterations=100000, lns_iterations=200,
                    lns_time_limit=15.0, use_cpsat=False, tracemalloc=True)
        res = run_cell(cell, hard_kill)
        log(f"[memory] {scale:11s} -> {res.get('status')} "
            f"wall={res.get('wall_seconds')} peak_mib={res.get('peak_mem_mib')} "
            f"placed={res.get('placed')}")
        rows.append(to_row(scale, 0.3, "memory_tracemalloc", cell, res))
    append_csv(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", nargs=2, metavar=("ARGS", "OUT"))
    ap.add_argument("--phase", choices=["main", "curve", "cpsat", "determinism",
                                        "infeasible", "memory", "all"])
    a = ap.parse_args()
    if a.worker:
        worker(a.worker[0], a.worker[1])
        return
    dispatch = {
        "main": phase_main, "curve": phase_curve, "cpsat": phase_cpsat,
        "determinism": phase_determinism, "infeasible": phase_infeasible,
        "memory": phase_memory,
    }
    if a.phase == "all":
        for fn in dispatch.values():
            fn()
    elif a.phase:
        dispatch[a.phase]()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
