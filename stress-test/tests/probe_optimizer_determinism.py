"""Probe: ScheduleOptimizer.optimize() is non-deterministic.

The optimizer uses the process-global `random` module (unseeded) for
multi-start perturbation (schedule_optimizer.py:547), simulated-annealing
acceptance (:785), and LNS destroy/strategy selection
(lns_strategies.py:271,287,562,597). No seed is ever set, so identical
input yields different placements and different quality scores run-to-run.

We run optimize() N times on a byte-identical fixture (deep-copied each
time) with CP-SAT and parallel disabled to isolate the heuristic RNG,
then quantify: distinct solutions, distinct scores, and score spread.

Deterministic fixture (seed=42); the *optimizer* is the nondeterministic
part under test.
"""
import os
import sys
import copy
import statistics
import tempfile

_sb = tempfile.mkdtemp(prefix="dersis_audit_")
os.environ["HOME"] = _sb
os.environ["USERPROFILE"] = _sb
sys.path.insert(0, r"C:\dev\dersis-app")
sys.path.insert(0, r"C:\dev\dersis-app\stress-test\tests")

from _fixtures.dataset_gen import make_state
from scheduler_app.schedule_optimizer import ScheduleOptimizer
from scheduler_app.timetable_scorer import TimetableScorer


def solution_signature(placed):
    return frozenset(
        (c["class_code"], d, s, r) for c, d, s, r in placed)


def run_once(state):
    st = copy.deepcopy(state)
    opt = ScheduleOptimizer(
        st, weights=None,
        multi_start_runs=3, multi_start_time_limit=15.0,
        lns_iterations=120, lns_time_limit=2.0,
        use_cpsat=False, parallel_workers=-1)
    placed, unplaced, changes, summary = opt.optimize()
    tt = TimetableScorer(st, weights=None)
    score = tt.score([(c, d, s, r) for c, d, s, r in placed])
    return solution_signature(placed), score, len(placed)


def main(n_runs=5):
    print("HAS parallel disabled; cpsat disabled. Isolating heuristic RNG.")
    base = make_state(n_classes=30, n_rooms=6, n_lecturers=8,
                      n_years=3, density=0.3, seed=42)
    print(f"fixture: {len(base['classes'])} classes, "
          f"{len(base['days'])}x{len(base['slots'])} grid, "
          f"{len(base['classrooms'])} rooms")

    sigs = []
    scores = []
    counts = []
    for i in range(n_runs):
        sig, score, n_placed = run_once(base)
        sigs.append(sig)
        scores.append(score)
        counts.append(n_placed)
        print(f"  run {i+1}: score={score:.4f}  placed={n_placed}  "
              f"sig_hash={hash(sig) & 0xffffff:06x}")

    distinct_sigs = len(set(sigs))
    distinct_scores = len(set(round(s, 6) for s in scores))
    spread = max(scores) - min(scores)
    print("\n" + "=" * 56)
    print("RESULTS")
    print("=" * 56)
    print(f"  runs                 : {n_runs}")
    print(f"  distinct placements  : {distinct_sigs}")
    print(f"  distinct scores      : {distinct_scores}")
    print(f"  placed-count set     : {sorted(set(counts))}")
    print(f"  score min / max      : {min(scores):.4f} / {max(scores):.4f}")
    print(f"  score spread (max-min): {spread:.4f}")
    if len(scores) > 1:
        print(f"  score stdev          : {statistics.pstdev(scores):.4f}")
    print(f"  DETERMINISTIC        : {distinct_sigs == 1}")
    return distinct_sigs, distinct_scores, spread, scores, counts


if __name__ == "__main__":
    main()
