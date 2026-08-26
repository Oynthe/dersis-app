"""Probe 3: undo-history corruption + multi-select drag drops only one.

Drives the real drag handlers (_start_drag_unplaced / _start_drag_gfx /
_execute_drop) but replaces QDrag.exec so we control drop success/failure
without a live mouse.  We assert on _undo_stack contents and on how many
classes actually moved.

Run: .venv-audit/Scripts/python.exe stress-test/tests/probe_undo_and_drag_integrity.py
"""
import os
import sys
import copy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ui_boot import boot, load_state, repo_root  # noqa: E402

sys.path.insert(0, os.path.join(repo_root(), "stress-test", "tests"))
from _fixtures.dataset_gen import make_state  # noqa: E402


def clsname(c):
    return c.get("class_code") or c.get("name")


def build_state():
    # Simple, fully feasible state: 5 days x 8 slots x 4 rooms, low density,
    # 6 single-target 1-hour classes, no pins/constraints.
    st = make_state(n_days=5, n_slots=8, n_rooms=4, n_lecturers=4,
                    n_years=1, branches_per_year=2, n_classes=6,
                    density=0.0, online_fraction=0.0, max_duration=1, seed=1)
    yr = next(iter(st["years"]))
    br = st["years"][yr][0]
    for c in st["classes"]:
        c["duration"] = 1
        c["pinned"] = False
        c["placed"] = False
        c["allowed_days"] = []
        c["allowed_times"] = []
        c["excluded_days"] = []
        c["required_classrooms"] = []
        c["location_type"] = "face_to_face"
        c["joint_session"] = True
        c["targets"] = [{"year": yr, "branch": br}]
    return st


def main():
    app, window, sandbox = boot("undo_drag")
    from scheduler_app.core.models import mark_placed
    out = {}

    # ============================================================
    # SCENARIO A: single unplaced-class drag pops an UNRELATED undo entry
    # ============================================================
    st = build_state()
    load_state(app, window, st)
    classes = st["classes"]

    # Perform a legitimate, undoable action first: place class[0] via the
    # normal push_undo path so the undo stack has one meaningful entry whose
    # snapshot represents "class[0] still unplaced".
    window._push_undo("PLACE_C0")
    mark_placed(classes[0], "monday", st["slots"][0], st["classrooms"][0])
    window.refresh_grid()

    a = {}
    a["undo_stack_len_before_drag"] = len(window._undo_stack)
    a["undo_top_label_before"] = window._undo_stack[-1][0] if window._undo_stack else None
    # Snapshot: does the top undo entry have class[0] unplaced? (correct target)
    top_snapshot = window._undo_stack[-1][1]
    c0_in_snapshot = next(x for x in top_snapshot if clsname(x) == clsname(classes[0]))
    a["c0_placed_in_top_undo_snapshot"] = c0_in_snapshot["placed"]

    # Now drag an UNPLACED class (class[1]) from the unplaced panel onto a cell.
    # We patch QDrag.exec to (a) do nothing itself and (b) let us invoke the
    # real _execute_drop, mimicking a successful drop over (tuesday, slot0).
    from PyQt6.QtGui import QDrag
    orig_exec = QDrag.exec

    def fake_exec_success_execute_drop(self, *a_, **k_):
        window._execute_drop("tuesday", st["slots"][0])
        return None

    QDrag.exec = fake_exec_success_execute_drop
    try:
        window._dragging_cls = None
        window._dragging_classes = []
        window._start_drag_unplaced([classes[1]], window.unplaced_list)
    finally:
        QDrag.exec = orig_exec

    a["class1_placed_after_drag"] = classes[1]["placed"]
    a["undo_stack_len_after_drag"] = len(window._undo_stack)
    a["undo_top_label_after"] = window._undo_stack[-1][0] if window._undo_stack else None
    # Was the original PLACE_C0 entry preserved, or did the drop's pop() eat it?
    labels_after = [lbl for lbl, _ in window._undo_stack]
    a["labels_after_drag"] = labels_after
    a["place_c0_entry_survived"] = "PLACE_C0" in labels_after

    # Now perform undo. undo() swaps in a *deepcopy* of the snapshot, so we
    # must re-read from window.state_data (not the stale local `classes`).
    def cur(code):
        return next(x for x in window.state_data["classes"]
                    if clsname(x) == code)
    c0code, c1code = clsname(classes[0]), clsname(classes[1])
    window.undo()
    a["after_undo1_class1_placed"] = cur(c1code)["placed"]
    a["after_undo1_class0_placed"] = cur(c0code)["placed"]
    can_undo2 = bool(window._undo_stack)
    a["can_undo_again"] = can_undo2
    if can_undo2:
        window.undo()
        a["after_undo2_class0_placed"] = cur(c0code)["placed"]
    else:
        a["after_undo2_class0_placed"] = "NO UNDO ENTRY LEFT (C0 placement no longer undoable)"
    out["scenarioA_unplaced_drag_undo"] = a

    # ============================================================
    # SCENARIO B: multi-select drag from grid moves only ONE class
    # ============================================================
    st = build_state()
    load_state(app, window, st)
    classes = st["classes"]
    # Place three classes at distinct cells.
    mark_placed(classes[0], "monday", st["slots"][0], st["classrooms"][0])
    mark_placed(classes[1], "monday", st["slots"][1], st["classrooms"][0])
    mark_placed(classes[2], "monday", st["slots"][2], st["classrooms"][0])
    window.refresh_grid()

    b = {}
    b["before"] = {clsname(c): (c["placed_day"], c["placed_time"])
                   for c in classes[:3]}

    # Simulate multi-selection of the three placed classes.
    window._selected_classes = [classes[0], classes[1], classes[2]]

    # Build a dummy graphics item stub with the methods _start_drag_gfx touches.
    class DummyItem:
        def scene(self):
            return None
        def boundingRect(self):
            from PyQt6.QtCore import QRectF
            return QRectF(0, 0, 10, 10)
        def set_ghost(self, *a):
            pass

    # Patch QDrag.exec so the "drop" targets (wednesday, slot0) via _execute_drop.
    from PyQt6.QtGui import QDrag
    orig_exec = QDrag.exec

    def fake_exec_move(self, *a_, **k_):
        window._execute_drop("wednesday", st["slots"][0])
        return None

    QDrag.exec = fake_exec_move
    try:
        # Drag the group, primary = classes[0]
        window._start_drag_gfx(classes[0], DummyItem())
    finally:
        QDrag.exec = orig_exec

    b["after"] = {clsname(c): (c["placed_day"], c["placed_time"])
                  for c in classes[:3]}
    moved = [clsname(c) for c in classes[:3]
             if b["before"][clsname(c)] != b["after"][clsname(c)]]
    b["classes_selected_for_drag"] = 3
    b["classes_actually_moved"] = moved
    b["n_moved"] = len(moved)
    out["scenarioB_multiselect_drag"] = b

    # ============================================================
    # SCENARIO C: cancelled grid drag restores state and undo stack
    # (sanity baseline: how the pop() is *supposed* to work)
    # ============================================================
    st = build_state()
    load_state(app, window, st)
    classes = st["classes"]
    mark_placed(classes[0], "monday", st["slots"][0], st["classrooms"][0])
    window.refresh_grid()
    window._selected_classes = [classes[0]]
    pre_len = len(window._undo_stack)

    orig_exec = QDrag.exec

    def fake_exec_cancel(self, *a_, **k_):
        # simulate a drop that never succeeded
        window._drag_success = False
        return None

    QDrag.exec = fake_exec_cancel
    try:
        window._start_drag_gfx(classes[0], DummyItem())
    finally:
        QDrag.exec = orig_exec
    c = {}
    c["undo_len_before"] = pre_len
    c["undo_len_after_cancelled_drag"] = len(window._undo_stack)
    c["class0_restored_placed"] = classes[0]["placed"]
    c["class0_day"] = classes[0]["placed_day"]
    out["scenarioC_cancelled_drag"] = c

    import json
    print(json.dumps(out, indent=2, ensure_ascii=False))
    window.close()


if __name__ == "__main__":
    main()
