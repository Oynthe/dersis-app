"""Probe: Parallel candidate scoring uses a REDUCED objective vs sequential.

The main-process scorer is built WITH a conflict_graph + propagator
(schedule_optimizer.py:331-336), so score_with_lookahead() adds the
`neighbor_impact_penalty` term. The parallel worker rebuilds the scorer
WITHOUT conflict_graph or propagator (parallel_scorer.py:120), so that
term is silently dropped. Because parallelism only engages when
max_workers>1 AND candidate_count>=8 (min_candidates), the *ranking* of
candidates — and therefore the chosen placement — depends on the host's
CPU count and problem size: machine-dependent, non-reproducible results.

Part 1 (deterministic): score identical candidates+lookahead with the
   two scorer configurations the two code paths actually construct, and
   compare the resulting rankings.
Part 2 (faithful): drive score_candidates_with_lookahead() sequentially
   vs through a real ParallelScorerPool(max_workers=2) and compare the
   chosen (top-1) candidate.
"""
import os
import sys
import tempfile

_sb = tempfile.mkdtemp(prefix="dersis_audit_")
os.environ["HOME"] = _sb
os.environ["USERPROFILE"] = _sb
sys.path.insert(0, r"C:\dev\dersis-app")
sys.path.insert(0, r"C:\dev\dersis-app\stress-test\tests")

from _fixtures.dataset_gen import make_state
from scheduler_app.core.models import cls_key
from scheduler_app.constraint_validator import ConstraintValidator
from scheduler_app.candidate_generator import CandidateGenerator
from scheduler_app.placement_scorer import PlacementScorer
from scheduler_app.conflict_graph import ConflictGraphBuilder
from scheduler_app.constraint_propagator import ConstraintState, ConstraintPropagator
from scheduler_app.parallel_scorer import ParallelScorerPool


def setup():
    st = make_state(n_classes=40, n_rooms=6, n_lecturers=6,
                    n_years=3, density=0.45, seed=7)
    flexible = [c for c in st["classes"] if not c["pinned"]]
    exclude = {cls_key(c) for c in flexible}
    validator = ConstraintValidator(st, exclude_ids=exclude)
    generator = CandidateGenerator(st, validator=validator)
    graph = ConflictGraphBuilder(st, flexible).build()
    cs = ConstraintState(st, validator, generator, flexible)
    prop = ConstraintPropagator(cs)
    return st, flexible, validator, generator, graph, prop


def pick_target(flexible, generator, graph):
    """Choose a class with many candidates and graph neighbors."""
    best = None
    best_key = (-1, -1)
    for cls in flexible:
        cands = generator.generate(cls)
        idx = graph.get_index(cls)
        deg = graph.degree(idx) if idx is not None else 0
        key = (min(len(cands), 20), deg)
        if len(cands) >= 8 and key > best_key:
            best_key = key
            best = (cls, cands)
    return best


def part1(st, flexible, validator, generator, graph, prop):
    print("=" * 64)
    print("PART 1 — same candidates, two scorer configs (graph vs none)")
    print("=" * 64)
    picked = pick_target(flexible, generator, graph)
    if picked is None:
        print("  no suitable class found")
        return None
    cls, cands = picked
    remaining = [c for c in flexible if cls_key(c) != cls_key(cls)][:12]
    print(f"  target class      : {cls['class_code']}  "
          f"candidates={len(cands)}  neighbors={graph.degree(graph.get_index(cls))}")

    # (a) Main-process config: WITH conflict graph + propagator
    scorer_graph = PlacementScorer(
        st, validator, conflict_graph=graph, propagator=prop)
    ranked_graph = scorer_graph.score_candidates_with_lookahead(
        cls, list(cands), remaining, generator)

    # (b) Parallel-worker config: NO graph, NO propagator (what
    #     parallel_scorer._score_lookahead_batch constructs)
    scorer_none = PlacementScorer(st, validator)
    ranked_none = scorer_none.score_candidates_with_lookahead(
        cls, list(cands), remaining, generator)

    top_g = ranked_graph[0][:3]
    top_n = ranked_none[0][:3]
    # Compare full ordering
    order_g = [(d, s, r) for d, s, r, _ in ranked_graph]
    order_n = [(d, s, r) for d, s, r, _ in ranked_none]
    same_top = top_g == top_n
    same_order = order_g == order_n
    # Score deltas for the same candidates
    map_g = {(d, s, r): sc for d, s, r, sc in ranked_graph}
    map_n = {(d, s, r): sc for d, s, r, sc in ranked_none}
    deltas = [abs(map_g[k] - map_n[k]) for k in map_g if k in map_n]
    max_delta = max(deltas) if deltas else 0.0
    n_diff = sum(1 for d in deltas if d > 1e-9)
    print(f"  top-1 WITH graph  : {top_g}  score={ranked_graph[0][3]:.4f}")
    print(f"  top-1 NO   graph  : {top_n}  score={ranked_none[0][3]:.4f}")
    print(f"  identical top-1   : {same_top}")
    print(f"  identical ordering: {same_order}")
    print(f"  candidates whose score differs: {n_diff}/{len(deltas)}")
    print(f"  max per-candidate score delta : {max_delta:.4f}")
    return same_top, same_order, n_diff, max_delta


def part2(st, flexible, validator, generator, graph, prop):
    print("=" * 64)
    print("PART 2 — real ParallelScorerPool(max_workers=2) vs sequential")
    print("=" * 64)
    picked = pick_target(flexible, generator, graph)
    if picked is None:
        print("  no suitable class found")
        return None
    cls, cands = picked
    remaining = [c for c in flexible if cls_key(c) != cls_key(cls)][:12]

    # Sequential (main path, with graph+propagator, no pool)
    seq_scorer = PlacementScorer(
        st, validator, conflict_graph=graph, propagator=prop)
    seq_ranked = seq_scorer.score_candidates_with_lookahead(
        cls, list(cands), remaining, generator)
    seq_top = seq_ranked[0][:3]

    # Parallel: same graph+propagator on the scorer, but pool present so
    # the lookahead phase is dispatched to workers (which drop the graph).
    pool = ParallelScorerPool(max_workers=2, min_candidates=8)
    try:
        par_scorer = PlacementScorer(
            st, validator, conflict_graph=graph, propagator=prop,
            parallel_pool=pool)
        will_parallelize = pool.should_parallelize(
            min(15, len(seq_ranked)))
        par_ranked = par_scorer.score_candidates_with_lookahead(
            cls, list(cands), remaining, generator)
    finally:
        pool.shutdown()
    par_top = par_ranked[0][:3]

    print(f"  candidate count   : {len(cands)}  will_parallelize={will_parallelize}")
    print(f"  sequential top-1  : {seq_top}  score={seq_ranked[0][3]:.4f}")
    print(f"  parallel   top-1  : {par_top}  score={par_ranked[0][3]:.4f}")
    print(f"  SAME chosen placement: {seq_top == par_top}")
    return seq_top, par_top, will_parallelize


if __name__ == "__main__":
    ctx = setup()
    p1 = part1(*ctx)
    print()
    p2 = part2(*ctx)
    print("\n" + "=" * 64)
    print("SUMMARY")
    print("=" * 64)
    if p1:
        same_top, same_order, n_diff, max_delta = p1
        print(f"Part1 objective differs (>=1 candidate): {n_diff > 0}  "
              f"(max delta {max_delta:.4f})")
        print(f"Part1 ranking differs                  : {not same_order}")
        print(f"Part1 chosen placement differs         : {not same_top}")
    if p2:
        seq_top, par_top, wp = p2
        print(f"Part2 parallelization engaged          : {wp}")
        print(f"Part2 chosen placement differs         : {seq_top != par_top}")
