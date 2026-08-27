"""Rigorously verify that the production optimizer's raw output
(optimized_reschedule_all -> result.placed / ScheduleOptimizer.optimize())
contains genuine hard-constraint conflicts between DISTINCT classes.

Independent pairwise overlap check (no ConstraintValidator, no oracle module),
accounting for: online/office classes (no room), duration blocks, and
non-joint sequential target sub-blocks. Prints full details of each
conflicting pair so it cannot be dismissed as a counting artifact.
"""
import _sandbox
_sandbox.enter()

import sys
from _fixtures.dataset_gen import make_preset


def block_cells(state, cls, day, start):
    """Return list of (offset, slot) the block occupies, in-grid only."""
    slots = state["slots"]
    if start not in slots:
        return []
    si = slots.index(start)
    if cls.get("joint_session", True) or len(cls.get("targets", [])) <= 1:
        td = cls.get("duration", 1)
    else:
        td = cls.get("duration", 1) * len(cls["targets"])
    out = []
    for off in range(td):
        if si + off < len(slots):
            out.append((off, slots[si + off]))
    return out


def active_targets(cls, off):
    ts = cls.get("targets", []) or []
    if not cls.get("joint_session", True) and len(ts) > 1:
        dur = cls.get("duration", 1) or 1
        idx = min(off // dur, len(ts) - 1)
        return [ts[idx]]
    return ts


def uses_room(cls):
    return cls.get("location_type", "face_to_face") == "face_to_face"


def analyze(preset, seed=42, runs=5, tlimit=90.0):
    from scheduler_app.logic import optimized_reschedule_all
    state = make_preset(preset, seed=seed)
    placed, unplaced, changes, summary = optimized_reschedule_all(
        state, weights={}, multi_start_runs=runs,
        multi_start_time_limit=tlimit, use_cpsat=False)

    # Sanity: no class appears twice
    seen = {}
    dupes = []
    for cls, d, s, r in placed:
        k = cls["class_uid"]
        if k in seen:
            dupes.append(cls.get("name"))
        seen[k] = True

    # Build per-cell occupancy from the RAW placed list
    room_map = {}
    lect_map = {}
    grp_map = {}
    for cls, day, start, room in placed:
        for off, slot in block_cells(state, cls, day, start):
            if uses_room(cls) and room:
                room_map.setdefault((day, slot, room), []).append(cls)
            if cls.get("lecturer"):
                lect_map.setdefault((day, slot, cls["lecturer"]), []).append(cls)
            for t in active_targets(cls, off):
                grp_map.setdefault((day, slot, (t["year"], t["branch"])), []).append(cls)

    def collisions(m, label):
        out = []
        for key, lst in m.items():
            uids = {c["class_uid"]: c for c in lst}
            if len(uids) > 1:
                out.append((label, key, list(uids.values())))
        return out

    cols = collisions(room_map, "ROOM") + collisions(lect_map, "LECTURER") + \
        collisions(grp_map, "GROUP")

    print(f"=== preset={preset} seed={seed} runs={runs} ===")
    print(f"placed={len(placed)} unplaced={len(unplaced)} "
          f"summary.classes_placed={summary.get('classes_placed')}")
    print(f"duplicate class entries in placed: {dupes or 'NONE'}")
    print(f"distinct-class collision cells: {len(cols)}")

    # Show up to 4 distinct conflicting pairs with full detail
    shown = set()
    n_shown = 0
    for label, key, classes in cols:
        pair = tuple(sorted(c["class_uid"] for c in classes))
        if pair in shown:
            continue
        shown.add(pair)
        n_shown += 1
        if n_shown > 5:
            break
        print(f"\n  [{label}] cell={key}")
        for c in classes:
            # find its placement in result
            pl = next((p for p in placed if p[0]["class_uid"] == c["class_uid"]), None)
            _, d, s, r = pl
            print(f"    - {c.get('name')} uid={c['class_uid'][:8]} "
                  f"pinned={c.get('pinned')} loc={c.get('location_type')} "
                  f"dur={c.get('duration')} joint={c.get('joint_session')} "
                  f"lect={c.get('lecturer')} targets={c.get('targets')}")
            print(f"        placed at day={d} start={s} room={r}")
    return len(cols), dupes


if __name__ == "__main__":
    preset = sys.argv[1] if len(sys.argv) > 1 else "small"
    runs = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    n, dupes = analyze(preset, runs=runs)
    print(f"\nRESULT: {n} distinct-class collision cells, "
          f"{len(dupes)} duplicate entries")
