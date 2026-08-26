"""PROBE 5: TimetableScorer vs parallel_scorer serialization consistency.

Two sub-tests:

(A) FIELD-PRESERVATION: score a full placement via TimetableScorer on the
    ORIGINAL class dicts, then serialize every class with
    parallel_scorer._serialize_class + create_state_snapshot, rebuild the same
    placements against the serialized dicts, and re-score. If _serialize_class
    drops a scoring-relevant field, the two scores diverge.

(B) OBJECTIVE-IDENTITY: the parallel worker actually scores with
    PlacementScorer (per-placement, incremental), NOT TimetableScorer
    (whole-timetable). This sub-test quantifies how different those two
    objectives are for the same placement set, to document that "the parallel
    path" and "the dashboard/quality path" optimise different numbers.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "tests")))
import _fixtures.sandbox  # noqa: F401

from _fixtures.dataset_gen import make_preset
from scheduler_app.core.timetable_scorer import TimetableScorer
from scheduler_app.core import parallel_scorer as ps
from scheduler_app.core.models import mark_placed, effective_room
from scheduler_app.core.logic import total_duration


def place_all(state, seed=0):
    """Deterministically place every class at a legal-looking slot (ignores
    hard conflicts — we only need a consistent placement set to score)."""
    days = state["days"]; slots = state["slots"]; rooms = state["classrooms"]
    n = 0
    for i, cls in enumerate(state["classes"]):
        td = total_duration(cls)
        # pick a start slot that fits
        max_start = max(0, len(slots) - td)
        si = (i * 3 + seed) % (max_start + 1)
        day = days[(i + seed) % len(days)]
        slot = slots[si]
        room = rooms[(i + seed) % len(rooms)] if rooms else None
        # only physical face_to_face gets a room; others None
        from scheduler_app.core.models import class_uses_physical_room
        if not class_uses_physical_room(cls):
            room = None
        mark_placed(cls, day, slot, room)
        n += 1
    return n


def build_placements(state):
    pls = []
    for cls in state["classes"]:
        if cls["placed"]:
            pls.append((cls, cls["placed_day"], cls["placed_time"],
                        effective_room(cls)))
    return pls


def main():
    state = make_preset("small", seed=7)
    place_all(state, seed=1)
    orig_pls = build_placements(state)
    print(f"placed classes: {len(orig_pls)}")

    # ── (A) field preservation ──
    scorer = TimetableScorer(state)
    orig_detailed = scorer.score_detailed(orig_pls)
    orig_score = scorer.score(orig_pls)

    snap_state = ps.create_state_snapshot(state)
    # Map original cls -> serialized cls positionally
    ser_classes = snap_state["classes"]
    ser_by_index = {id(o): ser_classes[i] for i, o in enumerate(state["classes"])}
    ser_pls = []
    for cls, day, slot, room in orig_pls:
        sc = ser_by_index[id(cls)]
        ser_pls.append((sc, day, slot, room))

    scorer_snap = TimetableScorer(snap_state)
    ser_detailed = scorer_snap.score_detailed(ser_pls)
    ser_score = scorer_snap.score(ser_pls)

    print("\n=== (A) TimetableScorer: ORIGINAL vs SERIALIZED classes ===")
    print(f"original score()          = {orig_score:.6f}")
    print(f"serialized score()        = {ser_score:.6f}")
    print(f"delta                     = {abs(orig_score - ser_score):.2e}")
    print("original score_detailed   =", {k: round(v,4) for k,v in orig_detailed.items()})
    print("serialized score_detailed =", {k: round(v,4) for k,v in ser_detailed.items()})
    max_div = max(abs(orig_detailed[k] - ser_detailed[k]) for k in orig_detailed)
    print(f"max per-category divergence = {max_div:.2e}")
    print("field-preservation AGREES:", max_div < 1e-9 and abs(orig_score-ser_score) < 1e-9)

    # Check which serialized fields are missing vs original
    orig_keys = set(state["classes"][0].keys())
    ser_keys = set(ser_classes[0].keys())
    print("\nfields dropped by _serialize_class:", sorted(orig_keys - ser_keys))
    print("fields added by _serialize_class:", sorted(ser_keys - orig_keys))

    # ── (B) objective identity: TimetableScorer vs PlacementScorer worker ──
    from scheduler_app.core.constraint_validator import ConstraintValidator
    from scheduler_app.core.placement_scorer import PlacementScorer
    from scheduler_app.core.models import cls_key
    # Sum of PlacementScorer.score over each placement (incremental objective)
    # Build validator with all placed excluded (so each is scored vs the rest)
    total_ps = 0.0
    for cls, day, slot, room in orig_pls:
        v = ConstraintValidator(state, exclude_ids={cls_key(cls)})
        pscorer = PlacementScorer(state, v)
        total_ps += pscorer.score(cls, day, slot, room)
    print("\n=== (B) objective identity ===")
    print(f"TimetableScorer.score (whole-timetable) = {orig_score:.4f}")
    print(f"sum PlacementScorer.score (parallel worker objective) = {total_ps:.4f}")
    print("NOTE: parallel worker calls PlacementScorer.score/score_with_lookahead,")
    print("      NOT TimetableScorer. These are different objective functions.")
    print(f"ratio = {total_ps/orig_score if orig_score else float('nan'):.3f}")


if __name__ == "__main__":
    main()
