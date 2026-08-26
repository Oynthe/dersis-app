"""PROBE 5b: parallel-worker scoring vs sequential scoring (same objective).

The parallel worker (parallel_scorer._score_lookahead_batch) reconstructs a
ConstraintValidator via __new__ from an occupancy snapshot and a serialized
state, then scores candidates with PlacementScorer. This probe reproduces that
reconstruction EXACTLY and compares the resulting scores against the plain
sequential PlacementScorer path (original state + real validator) for the same
class and candidate list. If they diverge, the parallel optimizer and the
sequential optimizer would pick different placements.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "tests")))
import _fixtures.sandbox  # noqa: F401

from _fixtures.dataset_gen import make_preset
from scheduler_app.core import parallel_scorer as ps
from scheduler_app.core.constraint_validator import ConstraintValidator
from scheduler_app.core.candidate_generator import CandidateGenerator
from scheduler_app.core.placement_scorer import PlacementScorer, DEFAULT_WEIGHTS
from scheduler_app.core.models import mark_placed, cls_key, class_uses_physical_room
from scheduler_app.core.logic import total_duration


def place_some(state, frac=0.5, seed=1):
    days = state["days"]; slots = state["slots"]; rooms = state["classrooms"]
    placed = []
    cutoff = int(len(state["classes"]) * frac)
    for i, cls in enumerate(state["classes"][:cutoff]):
        td = total_duration(cls)
        max_start = max(0, len(slots) - td)
        si = (i * 2 + seed) % (max_start + 1)
        day = days[(i + seed) % len(days)]
        slot = slots[si]
        room = rooms[(i + seed) % len(rooms)] if rooms else None
        if not class_uses_physical_room(cls):
            room = None
        mark_placed(cls, day, slot, room)
        placed.append(cls)
    return placed, state["classes"][cutoff:]


def main():
    state = make_preset("small", seed=11)
    placed, unplaced = place_some(state, frac=0.5, seed=2)
    target = unplaced[0]
    remaining = unplaced[1:6]
    weights = dict(DEFAULT_WEIGHTS)

    # ── SEQUENTIAL path ──
    seq_val = ConstraintValidator(state, exclude_ids={cls_key(target)})
    seq_gen = CandidateGenerator(state, validator=seq_val)
    seq_scorer = PlacementScorer(state, seq_val, weights=weights)
    candidates = seq_gen.generate(target)
    print(f"target={target['name']} candidates={len(candidates)} remaining={len(remaining)}")
    if not candidates:
        print("no candidates; abort")
        return

    seq_scores = []
    for (day, slot, room) in candidates[:20]:
        if remaining:
            s = seq_scorer.score_with_lookahead(target, day, slot, room,
                                                remaining, seq_gen)
        else:
            s = seq_scorer.score(target, day, slot, room)
        seq_scores.append((day, slot, room, s))

    # ── PARALLEL-WORKER path (reproduce _score_lookahead_batch inline) ──
    state_snap = ps.create_state_snapshot(state)
    occ_snap = ps.create_occupancy_snapshot(seq_val)
    cls_data = ps._serialize_class(target)
    remaining_data = [ps._serialize_class(rc) for rc in remaining]

    par_results = ps._score_lookahead_batch(
        (state_snap, occ_snap, weights, cls_data,
         [(d, s, r) for d, s, r, _ in seq_scores], remaining_data))

    print("\n=== per-candidate score: SEQUENTIAL vs PARALLEL-WORKER ===")
    max_div = 0.0
    for (d, s, r, seq_s), (pd, psl, pr, par_s) in zip(seq_scores, par_results):
        div = abs(seq_s - par_s)
        max_div = max(max_div, div)
        flag = "  <-- DIVERGE" if div > 1e-6 else ""
        print(f"  {d:>9} {s} {str(r):>6}: seq={seq_s:8.4f} par={par_s:8.4f} d={div:.2e}{flag}")

    print(f"\nmax divergence over {len(seq_scores)} candidates = {max_div:.3e}")
    # Best-choice agreement
    seq_best = min(seq_scores, key=lambda x: x[3])[:3]
    par_best = min(par_results, key=lambda x: x[3])[:3]
    print(f"sequential best pick = {seq_best}")
    print(f"parallel   best pick = {par_best}")
    print("best-pick AGREES:", seq_best == par_best)


if __name__ == "__main__":
    main()
