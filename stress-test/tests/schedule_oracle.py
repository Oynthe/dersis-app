"""schedule_oracle.py -- INDEPENDENT correctness oracle for DERSIS schedules.

Given a fully-placed state (or an explicit list of placements), this module
re-derives cell-level occupancy FROM SCRATCH -- deliberately NOT reusing the
production ConstraintValidator / build_occupancy code, so that bugs in that
code cannot mask themselves. It reports every hard-invariant violation:

    (a) room double-booking          -> two face-to-face classes, same room/day/slot
    (b) lecturer double-booking      -> one lecturer, two classes, same day/slot
    (c) target (year/branch) clash   -> one student group, two classes, same day/slot
    (d) off-grid placement           -> day/slot not in the grid, or block overflow
    (e) lecturer-availability breach  -> class occupies a slot the lecturer is barred from
    (f) capacity violation           -> room capacity < participants
    (g) pinned / locked not respected -> pinned class off its pin, or locked class moved

The only production helpers used are pure structural readers
(total_duration, target activity for non-joint blocks). Availability and
capacity are re-implemented locally and defensively (missing keys tolerated)
so the oracle never crashes on malformed data.

Run directly to exercise the production workflow.reschedule on presets
tiny..large and print a violation report:

    .venv-audit/Scripts/python.exe stress-test/tests/schedule_oracle.py
"""
import os
import sys

# ---- sandbox MUST come before scheduler_app import ----
if __name__ == "__main__" or "scheduler_app" not in sys.modules:
    try:
        import _sandbox
    except ImportError:
        sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
        import _sandbox
    _sandbox.enter()


# --------------------------------------------------------------------------
# Pure structural helpers (re-implemented locally; no production occupancy).
# --------------------------------------------------------------------------
def _total_duration(cls):
    if cls.get("joint_session", True) or len(cls.get("targets", [])) <= 1:
        return cls.get("duration", 1)
    return cls.get("duration", 1) * len(cls["targets"])


def _active_targets(cls, slot_offset):
    """Targets active at a given slot offset (mirrors non-joint sub-blocks)."""
    targets = cls.get("targets", []) or []
    if not cls.get("joint_session", True) and len(targets) > 1:
        dur = cls.get("duration", 1) or 1
        tidx = slot_offset // dur
        if tidx >= len(targets):
            tidx = len(targets) - 1
        return [targets[tidx]]
    return targets


def _uses_physical_room(cls):
    lt = cls.get("location_type", "face_to_face")
    return lt == "face_to_face"


def _effective(cls):
    """Return (day, start_slot, room, is_pinned) for a placed/pinned class."""
    if cls.get("pinned"):
        return (cls.get("pinned_day"), cls.get("pinned_time"),
                cls.get("pinned_classroom"), True)
    return (cls.get("placed_day"), cls.get("placed_time"),
            cls.get("placed_classroom"), False)


def _avail_ok(state, lecturer, day, slot):
    """Defensive re-implementation of lecturer_available_at semantics.

    Missing keys are treated as 'no restriction' (fully available), matching
    the documented default -- but WITHOUT raising KeyError on partial dicts.
    excluded takes precedence over allowed.
    """
    if not lecturer:
        return True
    avail = (state.get("lecturer_availability", {}) or {}).get(lecturer)
    if not avail:
        return True
    ad = avail.get("allowed_days") or []
    ah = avail.get("allowed_hours") or []
    ed = avail.get("excluded_days") or []
    eh = avail.get("excluded_hours") or []
    if ed and day in ed:
        return False
    if ad and day not in ad:
        return False
    if eh and slot in eh:
        return False
    if ah and slot not in ah:
        return False
    return True


def _room_capacity(state, room):
    return (state.get("classroom_capacities", {}) or {}).get(room, 0)


# --------------------------------------------------------------------------
# The oracle
# --------------------------------------------------------------------------
def check_schedule(state, placements=None, locked_baseline=None,
                   pinned_baseline=None):
    """Independently validate a fully-placed *state*.

    Parameters
    ----------
    state : dict
        The schedule state.
    placements : optional list of (cls, day, slot, room)
        If given, validate THESE proposed placements instead of the classes'
        own placement fields. (Used to audit raw optimizer output.) Classes
        not present are treated as unplaced.
    locked_baseline : optional dict {cls_key: (day, slot, room)}
        Original positions of protection=='locked' classes; a locked class
        whose effective position differs is reported as invariant (g).
    pinned_baseline : optional dict {cls_key: (day, slot, room)}
        Expected pin positions; if omitted, pins are read from the class.

    Returns
    -------
    dict with keys:
        violations : list of dicts {type, category, cls, detail}
        counts     : dict category -> count
        n_placed   : number of placed class-blocks examined
    """
    days = list(state.get("days", []))
    slots = list(state.get("slots", []))
    day_set = set(days)
    slot_index = {s: i for i, s in enumerate(slots)}
    n_slots = len(slots)

    violations = []

    def add(cat, cls, detail):
        violations.append({
            "category": cat,
            "cls": cls.get("name") or cls.get("class_code") or cls.get("class_uid"),
            "uid": cls.get("class_uid"),
            "detail": detail,
        })

    # Resolve placement source
    if placements is not None:
        placed_entries = []
        for entry in placements:
            cls, day, slot, room = entry[0], entry[1], entry[2], entry[3]
            placed_entries.append((cls, day, slot, room, cls.get("pinned", False)))
    else:
        placed_entries = []
        for cls in state.get("classes", []):
            if not (cls.get("placed") or cls.get("pinned")):
                continue
            day, slot, room, is_pinned = _effective(cls)
            placed_entries.append((cls, day, slot, room, is_pinned))

    # Occupancy accumulators: cell -> list of (cls, room_label)
    room_cells = {}    # (day, slot, room) -> [cls, ...]
    lect_cells = {}    # (day, slot, lecturer) -> [cls, ...]
    group_cells = {}   # (day, slot, (year, branch)) -> [cls, ...]

    n_blocks = 0
    for cls, day, slot, room, is_pinned in placed_entries:
        n_blocks += 1
        # (d) off-grid day
        if day not in day_set:
            add("off_grid_day", cls,
                f"day={day!r} not in grid days={days}")
            # still try to analyse slot dimension below, but day-based
            # occupancy is meaningless off-grid; skip cell accumulation.
            # We DO still check slot validity for completeness.
        # (d) off-grid slot / overflow
        if slot not in slot_index:
            add("off_grid_slot", cls,
                f"slot={slot!r} not in grid slots={slots}")
            continue  # cannot compute offsets without a valid start index
        start_idx = slot_index[slot]
        td = _total_duration(cls)
        if start_idx + td > n_slots:
            add("duration_overflow", cls,
                f"start={slot!r} (idx {start_idx}) + duration {td} "
                f"exceeds grid length {n_slots}")
        # Accumulate occupancy only for the in-grid, on-grid-day portion
        lecturer = cls.get("lecturer", "")
        uses_room = _uses_physical_room(cls)
        for off in range(td):
            idx = start_idx + off
            if idx >= n_slots:
                break
            cur_slot = slots[idx]
            # (e) lecturer availability (only meaningful on a real grid day)
            if day in day_set and lecturer and not _avail_ok(
                    state, lecturer, day, cur_slot):
                add("availability", cls,
                    f"lecturer {lecturer!r} not available at "
                    f"{day}/{cur_slot}")
            # (f) capacity
            if uses_room and room:
                cap = _room_capacity(state, room)
                participants = cls.get("participants", 0) or 0
                if cap and participants and cap < participants:
                    add("capacity", cls,
                        f"room {room!r} cap {cap} < participants "
                        f"{participants} at {day}/{cur_slot}")
            if day not in day_set:
                continue  # do not build occupancy on a phantom day
            # room occupancy
            if uses_room and room:
                room_cells.setdefault((day, cur_slot, room), []).append(cls)
            # lecturer occupancy
            if lecturer:
                lect_cells.setdefault((day, cur_slot, lecturer), []).append(cls)
            # group occupancy
            for t in _active_targets(cls, off):
                key = (day, cur_slot, (t.get("year"), t.get("branch")))
                group_cells.setdefault(key, []).append(cls)

    # (a) room double-booking
    for (day, slot, room), lst in room_cells.items():
        uniq = _dedupe(lst)
        if len(uniq) > 1:
            for c in uniq:
                add("room_double_book", c,
                    f"room {room!r} shared at {day}/{slot} by: "
                    f"{_names(uniq)}")

    # (b) lecturer double-booking
    for (day, slot, lect), lst in lect_cells.items():
        uniq = _dedupe(lst)
        if len(uniq) > 1:
            for c in uniq:
                add("lecturer_double_book", c,
                    f"lecturer {lect!r} teaches {len(uniq)} classes at "
                    f"{day}/{slot}: {_names(uniq)}")

    # (c) target/year-branch clash
    for (day, slot, grp), lst in group_cells.items():
        uniq = _dedupe(lst)
        if len(uniq) > 1:
            for c in uniq:
                add("group_clash", c,
                    f"group {grp} attends {len(uniq)} classes at "
                    f"{day}/{slot}: {_names(uniq)}")

    # (g) pinned respected
    for cls, day, slot, room, is_pinned in placed_entries:
        if cls.get("pinned"):
            exp = None
            if pinned_baseline is not None:
                exp = pinned_baseline.get(cls.get("class_uid"))
            if exp is None:
                exp = (cls.get("pinned_day"), cls.get("pinned_time"),
                       cls.get("pinned_classroom"))
            got = (day, slot, room)
            # only compare room for physical classes
            if _uses_physical_room(cls):
                if got != exp:
                    add("pinned_moved", cls,
                        f"pinned expected {exp} but placed {got}")
            else:
                if (got[0], got[1]) != (exp[0], exp[1]):
                    add("pinned_moved", cls,
                        f"pinned expected {exp[:2]} but placed {got[:2]}")

    # (g) locked respected (needs a baseline of pre-reschedule positions)
    if locked_baseline:
        pos_by_uid = {}
        for cls, day, slot, room, is_pinned in placed_entries:
            pos_by_uid[cls.get("class_uid")] = (day, slot, room)
        for uid, base in locked_baseline.items():
            cur = pos_by_uid.get(uid)
            if cur is None:
                violations.append({
                    "category": "locked_moved", "cls": uid, "uid": uid,
                    "detail": f"locked class became UNPLACED (was {base})",
                })
            elif cur != base:
                violations.append({
                    "category": "locked_moved", "cls": uid, "uid": uid,
                    "detail": f"locked class moved from {base} to {cur}",
                })

    counts = {}
    for v in violations:
        counts[v["category"]] = counts.get(v["category"], 0) + 1
    return {"violations": violations, "counts": counts, "n_placed": n_blocks}


def _dedupe(lst):
    seen = set()
    out = []
    for c in lst:
        k = c.get("class_uid") or id(c)
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


def _names(lst):
    return ", ".join((c.get("name") or c.get("class_code") or "?") for c in lst)


# --------------------------------------------------------------------------
# Driver: run production workflow.reschedule on presets and report.
# --------------------------------------------------------------------------
def _run_presets(preset_names=("tiny", "small", "normal", "large")):
    import time
    import json
    from _fixtures.dataset_gen import make_preset
    from scheduler_app.core.workflow import SchedulingWorkflow
    from scheduler_app.models import cls_key

    def weights():
        return {}

    results = []
    for name in preset_names:
        state = make_preset(name, seed=42)
        n_classes = len(state["classes"])

        # snapshot locked & pinned baselines BEFORE reschedule
        locked_baseline = {}
        for c in state["classes"]:
            if c.get("protection") == "locked" and not c.get("pinned"):
                # locked but unplaced initially -> place it first via a quick
                # feasible pass is out of scope; record only if placed.
                if c.get("placed"):
                    locked_baseline[c["class_uid"]] = (
                        c["placed_day"], c["placed_time"], c["placed_classroom"])

        wf = SchedulingWorkflow(state, weights)
        t0 = time.perf_counter()
        err = None
        try:
            res = wf.reschedule(weights(), use_cpsat=False)
        except Exception as e:  # noqa
            err = f"{type(e).__name__}: {e}"
            res = None
        t1 = time.perf_counter()

        entry = {
            "preset": name, "n_classes": n_classes,
            "reschedule_sec": round(t1 - t0, 3), "error": err,
        }

        if res is not None:
            # -- Audit A: raw optimizer output (result.placed) --
            audit_raw = check_schedule(state, placements=res.placed)
            entry["raw_placed"] = len(res.placed)
            entry["raw_unplaced"] = len(res.unplaced)
            entry["raw_violations"] = audit_raw["counts"]

            # -- Audit B: committed state after apply_reschedule --
            # capture locked baseline from raw placements for the applied check
            t2 = time.perf_counter()
            rejected = wf.apply_reschedule(res)
            t3 = time.perf_counter()
            audit_applied = check_schedule(state, locked_baseline=locked_baseline)
            entry["apply_sec"] = round(t3 - t2, 3)
            entry["apply_rejected"] = len(rejected)
            entry["applied_violations"] = audit_applied["counts"]
            entry["applied_n_placed"] = audit_applied["n_placed"]
            # keep a few sample violation details
            entry["applied_samples"] = [
                f"[{v['category']}] {v['cls']}: {v['detail']}"
                for v in audit_applied["violations"][:6]
            ]
            entry["raw_samples"] = [
                f"[{v['category']}] {v['cls']}: {v['detail']}"
                for v in audit_raw["violations"][:6]
            ]
        results.append(entry)
        print(json.dumps(entry, indent=2, ensure_ascii=False), flush=True)
    return results


if __name__ == "__main__":
    names = sys.argv[1:] or ["tiny", "small", "normal", "large"]
    _run_presets(tuple(names))
