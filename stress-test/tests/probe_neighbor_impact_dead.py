"""Probe: the `neighbor_impact_penalty` objective term is DEAD CODE.

PlacementScorer._neighbor_impact() guards each neighbor with:
    if nbr_id not in remaining_ids: continue
    if nbr_id in before_counts:    continue
But score_with_lookahead builds `before_counts` with exactly the keys
{cls_key(rc) for rc in remaining_classes} == remaining_ids. So every
neighbor satisfies one of the two `continue`s and the penalty body never
runs — _neighbor_impact always returns 0.0. The conflict-graph
`neighbor_impact_penalty` (DEFAULT_WEIGHTS = 4.0) therefore has ZERO
effect on scoring. This also explains why the parallel worker (which
omits the graph) produces identical rankings to the sequential path.

We instrument _neighbor_impact across many real classes and record its
return values.
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
import scheduler_app.placement_scorer as ps_mod
from scheduler_app.placement_scorer import PlacementScorer
from scheduler_app.conflict_graph import ConflictGraphBuilder
from scheduler_app.constraint_propagator import ConstraintState, ConstraintPropagator


def main():
    st = make_state(n_classes=45, n_rooms=6, n_lecturers=6,
                    n_years=3, density=0.5, seed=11)
    flexible = [c for c in st["classes"] if not c["pinned"]]
    exclude = {cls_key(c) for c in flexible}
    validator = ConstraintValidator(st, exclude_ids=exclude)
    generator = CandidateGenerator(st, validator=validator)
    graph = ConflictGraphBuilder(st, flexible).build()
    cs = ConstraintState(st, validator, generator, flexible)
    prop = ConstraintPropagator(cs)

    scorer = PlacementScorer(st, validator, conflict_graph=graph,
                             propagator=prop)

    # Instrument _neighbor_impact to record every return value.
    returns = []
    calls = [0]
    orig = scorer._neighbor_impact

    def wrapped(cls, generator_, remaining_ids, before_counts):
        calls[0] += 1
        r = orig(cls, generator_, remaining_ids, before_counts)
        returns.append(r)
        return r

    scorer._neighbor_impact = wrapped

    total_neighbors_examined = 0
    classes_with_neighbors = 0
    scored = 0
    for cls in flexible:
        idx = graph.get_index(cls)
        deg = graph.degree(idx) if idx is not None else 0
        if deg > 0:
            classes_with_neighbors += 1
            total_neighbors_examined += deg
        cands = generator.generate(cls)
        if not cands:
            continue
        remaining = [c for c in flexible if cls_key(c) != cls_key(cls)][:15]
        scorer.score_candidates_with_lookahead(
            cls, list(cands)[:6], remaining, generator)
        scored += 1

    nonzero = [r for r in returns if abs(r) > 1e-12]
    print("=" * 60)
    print("neighbor_impact_penalty weight :",
          ps_mod.DEFAULT_WEIGHTS["neighbor_impact_penalty"])
    print(f"classes scored                 : {scored}")
    print(f"classes with >=1 graph neighbor: {classes_with_neighbors}")
    print(f"total graph degree (edges*2)   : {total_neighbors_examined}")
    print(f"_neighbor_impact calls         : {calls[0]}")
    print(f"_neighbor_impact NONZERO returns: {len(nonzero)}")
    print(f"max |return|                   : {max([abs(r) for r in returns], default=0.0)}")
    print("=" * 60)
    print(f"DEAD TERM CONFIRMED (all returns 0): {len(nonzero) == 0 and calls[0] > 0}")


if __name__ == "__main__":
    main()
