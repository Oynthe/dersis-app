"""Probe 7: Recovery / rollback consistency.

 - snapshot_placements / restore_placements round-trip fidelity
 - schedule_new_classes then rollback_schedule -> new classes removed, old
   placements restored
 - reschedule then reject_reschedule(snapshots) -> placements restored
 - force a failure mid-batch (monkeypatched optimizer raises) -> is state left
   consistent? does the workflow leak half-added classes / half-applied moves?
 - apply_reschedule partial rejection path
Reports PREVENT / DETECT / REPAIR / SILENTLY-ACCEPT / CRASH + state-consistency.
"""
import _eh_sandbox
_eh_sandbox.enter()

import os
import copy
import traceback

from scheduler_app.translations import set_language
set_language("tr")

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from _fixtures.dataset_gen import make_state

from scheduler_app.core.models import new_state, new_class, mark_placed
from scheduler_app.core import workflow as wfmod
from scheduler_app.core.workflow import (
    SchedulingWorkflow, snapshot_placements, restore_placements,
)


def weights():
    return {}


def placement_fingerprint(state):
    """Return a stable dict of {uid: (placed, pinned, day, time, room)}."""
    fp = {}
    for c in state["classes"]:
        fp[c["class_uid"]] = (
            c.get("placed"), c.get("pinned"),
            c.get("placed_day"), c.get("placed_time"), c.get("placed_classroom"),
        )
    return fp


def base_placed(seed=3, n=12):
    s = make_state(n_days=5, n_slots=6, n_rooms=4, n_lecturers=6,
                   n_years=2, n_classes=n, density=0.0, seed=seed)
    wf = SchedulingWorkflow(s, weights)
    r = wf.reschedule(weights())
    wf.apply_reschedule(r)
    return s


def main():
    print("=" * 70)
    print("CASE 7a: snapshot_placements / restore_placements fidelity")
    print("=" * 70)
    s = base_placed()
    before = placement_fingerprint(s)
    snap = snapshot_placements(s)
    print(f"  snapshot captured {len(snap)} placed non-pinned classes")
    # Mutate: move everything to monday/first slot, unplace half
    for i, c in enumerate(s["classes"]):
        if i % 2 == 0:
            mark_placed(c, "monday", s["slots"][0], s["classrooms"][0])
        else:
            from scheduler_app.core.models import mark_unplaced
            mark_unplaced(c)
    restore_placements(s, snap)
    after = placement_fingerprint(s)
    if before == after:
        print("  RESULT: restore is LOSSLESS (fingerprint identical) -> rollback works")
    else:
        diffs = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
        print(f"  RESULT: restore CHANGED {len(diffs)} classes (rollback imperfect)")
        for k, v in list(diffs.items())[:5]:
            print(f"    {k[:8]}: before={v[0]} after={v[1]}")

    print("\n" + "=" * 70)
    print("CASE 7b: schedule_new_classes then rollback_schedule")
    print("=" * 70)
    s = base_placed()
    before = placement_fingerprint(s)
    n_before = len(s["classes"])
    existing_snap = snapshot_placements(s)
    wf = SchedulingWorkflow(s, weights)
    newc = new_class(); newc["name"] = "Injected"; newc["lecturer"] = s["lecturers"][0]
    newc["targets"] = [{"year": "Year-1", "branch": "A"}]; newc["duration"] = 1
    try:
        res = wf.schedule_new_classes([newc])
        print(f"  scheduled: single_success={res.single_success} "
              f"placed={len(res.placed)} added_to_state={len(s['classes'])-n_before}")
        wf.rollback_schedule([newc], existing_snap)
        after = placement_fingerprint(s)
        print(f"  after rollback: n_classes={len(s['classes'])} (was {n_before}); "
              f"injected present? {newc in s['classes']}")
        print(f"  existing placements restored EXACTLY? {before == after}")
        if before != after:
            diffs = {k: (before[k], after[k]) for k in before if before.get(k) != after.get(k)}
            print(f"    changed {len(diffs)} existing classes during rollback")
            for k, v in list(diffs.items())[:5]:
                print(f"    {k[:8]}: before={v[0]} after={v[1]}")
    except Exception as e:
        print(f"  CRASH: {type(e).__name__}: {e}")

    print("\n" + "=" * 70)
    print("CASE 7c: reschedule then reject_reschedule(snapshots)")
    print("=" * 70)
    s = base_placed()
    before = placement_fingerprint(s)
    snap = snapshot_placements(s)
    wf = SchedulingWorkflow(s, weights)
    r = wf.reschedule(weights())
    wf.apply_reschedule(r)              # commit the moves
    moved = placement_fingerprint(s)
    n_moved = sum(1 for k in before if before[k] != moved[k])
    print(f"  reschedule+apply changed {n_moved} classes")
    wf.reject_reschedule(snap)
    after = placement_fingerprint(s)
    print(f"  after reject_reschedule: restored to original? {before == after}")
    if before != after:
        diffs = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
        print(f"    STILL DIFFERENT for {len(diffs)} classes -> rollback INCOMPLETE")
        for k, v in list(diffs.items())[:6]:
            print(f"    {k[:8]}: orig={v[0]} now={v[1]}")

    print("\n" + "=" * 70)
    print("CASE 7d: FORCE failure mid schedule_new_classes (optimizer raises)")
    print("=" * 70)
    s = base_placed()
    before = placement_fingerprint(s)
    n_before = len(s["classes"])
    existing_snap = snapshot_placements(s)
    wf = SchedulingWorkflow(s, weights)
    newc = new_class(); newc["name"] = "WillFail"; newc["lecturer"] = s["lecturers"][0]
    newc["targets"] = [{"year": "Year-1", "branch": "A"}]; newc["duration"] = 1

    orig = wfmod.optimized_batch_schedule
    def boom(*a, **k):
        raise RuntimeError("injected mid-batch failure")
    wfmod.optimized_batch_schedule = boom
    crashed = False
    try:
        wf.schedule_new_classes([newc])
    except Exception as e:
        crashed = True
        print(f"  schedule_new_classes raised: {type(e).__name__}: {e}")
    finally:
        wfmod.optimized_batch_schedule = orig

    # After an exception mid-op, what state are we in?
    print(f"  n_classes now = {len(s['classes'])} (was {n_before}); "
          f"WillFail leaked into state? {newc in s['classes']}")
    after = placement_fingerprint(s)
    print(f"  existing placements still intact? {({k:before[k] for k in before}) == ({k:after.get(k) for k in before})}")
    print("  NOTE: schedule_new_classes appends new classes BEFORE calling the")
    print("        optimizer; on exception it does NOT auto-rollback -> caller")
    print("        must call rollback_schedule explicitly.")
    # Demonstrate the documented recovery
    if newc in s["classes"]:
        wf.rollback_schedule([newc], existing_snap)
        print(f"  after manual rollback_schedule: WillFail present? {newc in s['classes']}; "
              f"n_classes={len(s['classes'])}")

    print("\nDONE probe_recovery_rollback")


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
