"""Probe 7: SetupDialog OK does not reconcile placed classes with removed
days / slots / rooms / lecturers (dialogs.py:1813 overwrites state wholesale).

Drives the REAL SetupDialog._ok() after unchecking a day button, deleting a
room row, deleting a slot line, and deleting a lecturer row, then checks
state["classes"] for placements that now reference removed entities.

Run: .venv-audit/Scripts/python.exe stress-test/tests/probe_setup_dialog_reconcile.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ui_boot import boot, load_state, repo_root  # noqa: E402

sys.path.insert(0, os.path.join(repo_root(), "stress-test", "tests"))
from _fixtures.dataset_gen import make_state  # noqa: E402


def main():
    app, window, sandbox = boot("setup_reconcile")
    from scheduler_app.core.models import mark_placed
    from scheduler_app.ui.dialogs import SetupDialog
    from PyQt6.QtWidgets import QMessageBox, QDialog

    # Build a small, fully-populated placed state.
    st = make_state(n_days=5, n_slots=8, n_rooms=4, n_lecturers=4,
                    n_years=1, branches_per_year=2, n_classes=6,
                    density=0.0, online_fraction=0.0, max_duration=1, seed=5)
    days = st["days"]                     # mon..fri
    slots = st["slots"]                   # 09:00..16:00
    rooms = st["classrooms"]              # R001..R004
    lects = st["lecturers"]               # Lect-001..004
    for c in st["classes"]:
        c["duration"] = 1
        c["placed"] = False
        c["pinned"] = False

    remove_day = days[4]        # friday
    remove_slot = slots[7]      # last slot 16:00
    remove_room = rooms[3]      # R004
    remove_lect = lects[3]      # Lect-004

    # Place classes so each depends on one entity we will remove.
    mark_placed(st["classes"][0], remove_day, slots[0], rooms[0])   # on removed DAY
    mark_placed(st["classes"][1], days[0], remove_slot, rooms[0])   # on removed SLOT
    mark_placed(st["classes"][2], days[0], slots[0], remove_room)   # in removed ROOM
    st["classes"][3]["lecturer"] = remove_lect
    mark_placed(st["classes"][3], days[1], slots[1], rooms[1])      # taught by removed LECTURER
    mark_placed(st["classes"][4], days[1], slots[2], rooms[1])      # unaffected control

    load_state(app, window, st)

    dlg = SetupDialog(window, st)
    for _ in range(2):
        app.processEvents()

    # Sanity: the dialog mirrors the state it was given.
    pre = {
        "day_buttons_checked": [k for k, b in dlg.day_buttons.items() if b.isChecked()],
        "rooms_rows": dlg.rooms_table.rowCount(),
        "lec_rows": dlg.lec_table.rowCount(),
        "slots_lines": len([l for l in dlg.slots_text.toPlainText().splitlines() if l.strip()]),
    }

    # --- Simulate the user removing entities in the dialog UI ---
    # 1) Uncheck the day.
    if remove_day in dlg.day_buttons:
        dlg.day_buttons[remove_day].setChecked(False)
    # 2) Remove the slot line from the slots text box.
    kept_slots = [s for s in slots if s != remove_slot]
    dlg.slots_text.setPlainText("\n".join(kept_slots))
    # 3) Remove the room row (find row whose col-0 text == remove_room).
    for r in range(dlg.rooms_table.rowCount()):
        it = dlg.rooms_table.item(r, 0)
        if it and it.text().strip() == remove_room:
            dlg.rooms_table.removeRow(r)
            break
    # 4) Remove the lecturer row.
    for r in range(dlg.lec_table.rowCount()):
        it = dlg.lec_table.item(r, 0)
        if it and it.text().strip() == remove_lect:
            dlg.lec_table.removeRow(r)
            break

    # Auto-answer any dialogs and neutralize accept() so nothing blocks/closes.
    orig_q = QMessageBox.question
    orig_accept = QDialog.accept
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    QDialog.accept = lambda self: None
    try:
        dlg._ok()
    finally:
        QMessageBox.question = orig_q
        QDialog.accept = orig_accept

    # --- Inspect resulting state for orphaned placements ---
    s = window.state_data
    post_state = {
        "days": s["days"],
        "slots": s["slots"],
        "classrooms": s["classrooms"],
        "lecturers": s["lecturers"],
    }
    day_removed = remove_day not in s["days"]
    slot_removed = remove_slot not in s["slots"]
    room_removed = remove_room not in s["classrooms"]
    lect_removed = remove_lect not in s["lecturers"]

    orphans = []
    for c in s["classes"]:
        if not c.get("placed"):
            continue
        probs = []
        if c["placed_day"] not in s["days"]:
            probs.append(f"placed_day={c['placed_day']!r} not in state days")
        if c["placed_time"] not in s["slots"]:
            probs.append(f"placed_time={c['placed_time']!r} not in state slots")
        if c["placed_classroom"] not in s["classrooms"]:
            probs.append(f"placed_classroom={c['placed_classroom']!r} not in state rooms")
        if c["lecturer"] not in s["lecturers"]:
            probs.append(f"lecturer={c['lecturer']!r} not in state lecturers")
        if probs:
            orphans.append({"class": c.get("class_code") or c["name"],
                            "placed": (c["placed_day"], c["placed_time"], c["placed_classroom"]),
                            "issues": probs})

    out = {
        "dialog_mirrors_state": pre,
        "removed": {"day": remove_day, "slot": remove_slot,
                    "room": remove_room, "lecturer": remove_lect},
        "removal_applied_to_state": {
            "day_removed": day_removed, "slot_removed": slot_removed,
            "room_removed": room_removed, "lecturer_removed": lect_removed},
        "post_state_days": post_state["days"],
        "post_state_slots_count": len(post_state["slots"]),
        "post_state_rooms": post_state["classrooms"],
        "post_state_lecturers": post_state["lecturers"],
        "orphaned_placements": orphans,
        "n_orphans": len(orphans),
        "conclusion": ("SetupDialog._ok overwrote days/slots/rooms/lecturers "
                       "without unplacing or clearing dependent classes — "
                       f"{len(orphans)} placed classes now reference removed entities"),
    }

    import json
    print(json.dumps(out, indent=2, ensure_ascii=False))
    window.close()


if __name__ == "__main__":
    main()
