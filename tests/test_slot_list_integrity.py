"""Editing the time-slot list must not silently change what a lesson means.

ST-UI-021 (new). The roadmap cites ST-UI-014 for "structured time-slot entry",
but that register entry is "Inconsistent destructive-action protection across
four delete paths; setup edits are irreversible and unconfirmed" — a different
finding. The real source is ``09-ui-ux-audit.md``'s SetupDialog section: the
time slots are a free-text box with no uniqueness check, while the whole engine
indexes rows by ``slots.index()``.

(ST-UI-014's *second* clause does turn out to be load-bearing here, though —
``edit_setup`` never pushes an undo snapshot, so anything this path clears is
irreversible. See section 4.)

What the grid actually requires — proved, not assumed
-----------------------------------------------------
``grep`` for ``strptime`` / ``%H:%M`` / ``split(":")`` across ``scheduler_app/``
returns **zero hits**. Nothing parses a slot as a time. Duration is counted in
*rows*; ``get_consecutive_slots`` slices the list. So ``"1. Ders"``,
``"08:00-08:45"`` and ``"Öğle Arası"`` are all first-class, and the contract is:

============  ========  =========================================================
Property      Required  Why
============  ========  =========================================================
HH:MM         **no**    no parser exists; a format rule would reject real setups
sorted        **no**    and applying it CORRUPTS data — see section 2
unique        **yes**   every lookup is ``list.index()``; first match wins
non-blank     yes       already enforced
equal length  **no**    a break is just another row, or simply omitted
============  ========  =========================================================

So: one hard rule, and two things that must never be done automatically.

The two measurements this module exists to pin
----------------------------------------------
1. **A duplicate costs one usable hour per day, permanently and invisibly.**
   With ``09:00`` typed twice on a four-hour day: the grid draws 4 rows, only 3
   can ever hold a lesson, and 3 of 4 classes place. ``find_valid_options``
   still offers 4 candidates — one is a phantom that resolves to the same cell.

2. **Sorting a CLEAN schedule produces 6 hard violations.** On
   ``["09:00","10:00","11:00","12:00","1. Ara","13:00"]`` with a 2-hour lesson
   at 12:00 and a 1-hour lesson at 13:00 — oracle-clean — sorting alone gives a
   double-booked room and a lecturer in two places at once, because the 2-hour
   lesson stops covering ``["12:00","1. Ara"]`` and starts covering
   ``["12:00","13:00"]``. ``reconcile_placements`` reports ``[]``, because it is
   a membership test and every label is still a member.

Why the detector compares CELLS and not EDITS
---------------------------------------------
A reorder, a mid-list substitution and an insertion are the same defect seen
three ways. An edit-shaped detector ("did the common prefix reorder?") catches
only the ones someone thought of — it misses ``["08:00","09:00","10:00"]`` ->
``["08:00","08:30","10:00"]``, where no label moved relative to any other and a
2-hour lesson at 08:00 still silently changes which hour it occupies. Comparing
the tuple of covered cells is the level at which the engine defines the thing.
"""
import pytest

from scheduler_app.core.logic import (
    parse_slot_lines, slot_meaning_changes, get_consecutive_slots,
    find_valid_options, SLOT_ERROR_DUPLICATE, SLOT_ERROR_BLANK,
)
from scheduler_app.core.models import new_state, new_class, mark_placed
from scheduler_app.core.workflow import SchedulingWorkflow


def _state(slots):
    s = new_state()
    s["days"] = ["monday"]
    s["slots"] = list(slots)
    s["classrooms"] = ["R1"]
    s["years"] = {"Y": ["A"]}
    s["lecturers"] = ["L1"]
    return s


def _add(state, name, slot=None, dur=1, room="R1", lecturer="L1", pin=False):
    cls = new_class()
    cls["name"] = name
    cls["class_code"] = name
    cls["lecturer"] = lecturer
    cls["duration"] = dur
    cls["participants"] = 5
    cls["targets"] = [{"year": "Y", "branch": "A"}]
    if pin:
        cls["pinned"] = True
        cls["pinned_day"] = "monday"
        cls["pinned_time"] = slot
        cls["pinned_classroom"] = room
    elif slot:
        mark_placed(cls, "monday", slot, room)
    state["classes"].append(cls)
    return cls


# ══════════════════════════════════════════════════════════════════════
#  1. Uniqueness is the one hard rule
# ══════════════════════════════════════════════════════════════════════

def test_a_duplicate_label_is_reported_with_its_line_number():
    """ST-UI-021 — a repeated hour must be refused, and pointed at.

    A failure means one typed character silently costs a usable hour every day
    for the rest of the file's life, with an unexplained empty row on the grid
    and one lesson that can never be placed.
    """
    slots, problems = parse_slot_lines("08:00\n09:00\n09:00\n10:00")

    assert slots == ["08:00", "09:00", "10:00"]
    assert problems == [(3, SLOT_ERROR_DUPLICATE, "09:00")]


def test_a_clean_list_reports_no_problems():
    """ST-UI-021 — ordinary input must not be obstructed.

    A failure means the dialog cries wolf on a correct setup, which is how a
    user learns to click past the one message that matters.
    """
    slots, problems = parse_slot_lines("08:00\n09:00\n10:00\n")

    assert slots == ["08:00", "09:00", "10:00"]
    assert problems == []


def test_non_clock_labels_are_accepted():
    """ST-UI-021 — the grid is ordinal, so a format rule would be wrong.

    A failure means a school that numbers its periods ("1. Ders") or names its
    break ("Öğle Arası") is blocked from a setup the engine handles perfectly:
    nothing in the package parses a slot as a time.
    """
    text = "1. Ders\n2. Ders\nÖğle Arası\n3. Ders\n08:00-08:45"
    slots, problems = parse_slot_lines(text)

    assert problems == []
    assert slots == ["1. Ders", "2. Ders", "Öğle Arası", "3. Ders",
                     "08:00-08:45"]

    # And they really do work end to end, not merely parse.
    s = _state(slots)
    cls = _add(s, "X", slot="1. Ders", dur=2)
    assert get_consecutive_slots(s, "1. Ders", 2) == ["1. Ders", "2. Ders"]
    assert find_valid_options(s, _add(s, "Y"))


def test_the_parser_never_reorders_or_deduplicates_silently():
    """ST-UI-021 — order is the user's declaration of chronology.

    A failure means the parser "helpfully" sorts or dedups, which is a silent
    repair of a silent corruption — see the whole of section 2.
    """
    text = "12:00\n1. Ara\n09:00\n09:00"
    slots, problems = parse_slot_lines(text)

    assert slots == ["12:00", "1. Ara", "09:00"], "the parser reordered"
    assert [p[1] for p in problems] == [SLOT_ERROR_DUPLICATE]


def test_a_whitespace_only_line_is_ignored_not_committed():
    """ST-UI-021 — a stray blank line must not become a nameless hour.

    A failure means an empty-string slot enters the grid, where it is
    indistinguishable from every other empty-string slot and cannot be selected.
    """
    slots, problems = parse_slot_lines("08:00\n\n   \n09:00")
    assert slots == ["08:00", "09:00"]
    assert all(p[1] != SLOT_ERROR_DUPLICATE for p in problems)


def test_the_measured_cost_of_a_duplicate():
    """ST-UI-021 — the evidence, pinned so it cannot quietly stop being true.

    A failure means the finding's central measurement no longer reproduces and
    the justification for refusing duplicates needs re-deriving.
    """
    clean = _state(["08:00", "09:00", "10:00", "11:00"])
    dupe = _state(["08:00", "09:00", "09:00", "10:00"])
    for s in (clean, dupe):
        for i in range(4):
            _add(s, f"C{i}")
        wf = SchedulingWorkflow(s, lambda: {})
        wf.apply_reschedule(wf.reschedule({}))

    n_clean = sum(1 for c in clean["classes"] if c["placed"])
    n_dupe = sum(1 for c in dupe["classes"] if c["placed"])
    cells = {(c["placed_day"], c["placed_time"])
             for c in dupe["classes"] if c["placed"]}

    assert n_clean == 4
    assert n_dupe == 3, "the duplicate no longer costs a placement"
    assert len(cells) == 3, "4 rows drawn, only 3 addressable"


# ══════════════════════════════════════════════════════════════════════
#  2. Changing the list must not silently move a lesson
# ══════════════════════════════════════════════════════════════════════

def test_sorting_the_list_is_detected_as_a_meaning_change():
    """ST-UI-021 — the headline corruption.

    A failure means an auto-sort (or a user reordering by hand) takes a clean
    timetable to a double-booked room and a lecturer in two places at once,
    with nothing anywhere saying so: ``reconcile_placements`` is a membership
    test and every label is still a member.
    """
    slots = ["09:00", "10:00", "11:00", "12:00", "1. Ara", "13:00"]
    s = _state(slots)
    x = _add(s, "X", slot="12:00", dur=2)
    _add(s, "Y", slot="13:00", dur=1)

    assert SchedulingWorkflow.reconcile_placements(s) == [], (
        "reconcile already catches this — the detector may be redundant"
    )

    changed = slot_meaning_changes(s, sorted(slots))

    assert [c["name"] for c, _b, _a in changed] == ["X"]
    _cls, before, after = changed[0]
    assert before == ("12:00", "1. Ara")
    assert after == ("12:00", "13:00"), "X now sits on top of Y"


def test_a_mid_list_substitution_is_detected():
    """ST-UI-021 — the case an edit-shaped detector misses.

    ``["08:00","09:00","10:00"]`` -> ``["08:00","08:30","10:00"]``: no label
    moved relative to any other, so a "did the common order change?" test sees
    nothing. A 2-hour lesson at 08:00 still stops covering 09:00 and starts
    covering 08:30.

    A failure means the detector only catches the reorders someone thought of.
    """
    s = _state(["08:00", "09:00", "10:00"])
    _add(s, "X", slot="08:00", dur=2)

    changed = slot_meaning_changes(s, ["08:00", "08:30", "10:00"])

    assert len(changed) == 1
    _cls, before, after = changed[0]
    assert before == ("08:00", "09:00")
    assert after == ("08:00", "08:30")


def test_inserting_a_slot_above_a_block_lesson_is_detected():
    """ST-UI-021 — an insertion shifts everything below it.

    A failure means adding an early-morning period silently drags every
    multi-hour lesson in the day one row later.
    """
    s = _state(["09:00", "10:00", "11:00"])
    _add(s, "X", slot="10:00", dur=2)

    changed = slot_meaning_changes(s, ["08:00", "09:00", "10:00", "11:00"])

    # X still starts at 10:00 and still covers 10:00+11:00 -- an insertion
    # ABOVE it does not move it, because the reference is by name.
    assert changed == [], (
        "a pure prepend does not change which cells a named start covers"
    )

    # But replacing the row BELOW it does.
    changed2 = slot_meaning_changes(s, ["09:00", "10:00", "11:30"])
    assert [c["name"] for c, _b, _a in changed2] == ["X"]


def test_a_pinned_lesson_is_included_in_the_detection():
    """ST-UI-021 / ST-SCHED-002 — a pin is a by-name reference too.

    ``pinned_time`` resolves through the same ordered list as ``placed_time``,
    and ``validate_placements_after_edit`` explicitly skips pinned classes. So
    a detector that ignores pins lets a reorder silently move the one thing the
    user said must not move — and, worse, the repair sweep then unplaces some
    *other* lesson to resolve the collision the pin caused.

    A failure means exactly that.
    """
    slots = ["09:00", "10:00", "11:00", "12:00", "1. Ara", "13:00"]
    s = _state(slots)
    _add(s, "X", slot="12:00", dur=2, pin=True)
    _add(s, "Y", slot="13:00", dur=1)

    changed = slot_meaning_changes(s, sorted(slots))

    assert [c["name"] for c, _b, _a in changed] == ["X"], (
        "the pinned lesson was not checked"
    )


def test_an_unchanged_list_reports_nothing():
    """ST-UI-021 — renaming a lecturer must not look like a slot edit.

    A failure means every Setup OK warns about moved lessons, so the warning
    stops meaning anything — and, if it is wired to a repair, every Setup OK
    unplaces lessons the user never touched.
    """
    slots = ["09:00", "10:00", "11:00"]
    s = _state(slots)
    _add(s, "X", slot="09:00", dur=2)
    _add(s, "Y", slot="11:00")

    assert slot_meaning_changes(s, list(slots)) == []


def test_appending_a_slot_changes_nothing_for_existing_lessons():
    """ST-UI-021 — the commonest harmless edit must stay silent.

    A failure means adding an eighth period to the day warns about, or unplaces,
    every lesson already on the timetable. This is the case that makes a
    "does a multi-slot lesson exist?" gate wrong: such a gate fires here, where
    nothing has moved at all.
    """
    s = _state(["09:00", "10:00", "11:00"])
    _add(s, "X", slot="09:00", dur=2)
    _add(s, "Y", slot="11:00")

    assert slot_meaning_changes(s, ["09:00", "10:00", "11:00", "12:00"]) == []


def test_removing_a_slot_a_lesson_spans_is_detected():
    """ST-UI-021 — shortening a block lesson's coverage must be caught.

    A failure means deleting an hour quietly turns a 2-hour lesson into a
    1-hour one, or pushes it off the end of the day, with no message.
    """
    s = _state(["09:00", "10:00", "11:00"])
    _add(s, "X", slot="10:00", dur=2)

    changed = slot_meaning_changes(s, ["09:00", "10:00"])

    assert len(changed) == 1
    _cls, before, after = changed[0]
    assert before == ("10:00", "11:00")
    assert after == ("10:00",), "the block silently lost an hour"


# ══════════════════════════════════════════════════════════════════════
#  3. The dialog refuses what must be refused and asks about the rest
# ══════════════════════════════════════════════════════════════════════

def _setup_dialog(state):
    from scheduler_app.ui.dialogs import SetupDialog
    return SetupDialog(None, state)


@pytest.mark.ui
def test_the_dialog_flags_a_duplicate_as_the_user_types(qapp):
    """ST-UI-021 — diagnose on the way in, not only on the way out.

    A user whose saved file already carries a duplicate must see it when the
    dialog opens, not after they have made ten other edits and pressed OK.

    A failure means the only feedback is a modal at the end of the session.
    """
    s = _state(["08:00", "09:00", "10:00"])
    dlg = _setup_dialog(s)
    try:
        assert dlg._slots_status.text() == "", "cried wolf on a clean list"

        dlg.slots_text.setPlainText("08:00\n09:00\n09:00")
        assert dlg._slots_status.text().strip(), "no warning for a duplicate"
        assert "09:00" in dlg._slots_status.text()
        assert "3" in dlg._slots_status.text(), "the line number is not named"

        dlg.slots_text.setPlainText("08:00\n09:00\n10:00")
        assert dlg._slots_status.text() == "", "the warning did not clear"
    finally:
        dlg.deleteLater()


@pytest.mark.ui
def test_ok_is_refused_while_a_duplicate_stands(qapp, monkeypatch):
    """ST-UI-021 — a duplicate must never reach state["slots"].

    A failure means one typed character permanently costs a usable hour a day,
    and the only symptom is an unexplained empty row plus a lesson that will
    not place.
    """
    from PyQt6.QtWidgets import QMessageBox
    from scheduler_app.ui import dialogs as dlg_mod

    warned = []
    monkeypatch.setattr(
        dlg_mod.QMessageBox, "warning",
        lambda *a, **k: warned.append(a[2] if len(a) > 2 else "") or
        QMessageBox.StandardButton.Ok)

    s = _state(["08:00", "09:00", "10:00"])
    dlg = _setup_dialog(s)
    try:
        dlg.slots_text.setPlainText("08:00\n09:00\n09:00")
        dlg._ok()
        assert warned, "OK went through with a duplicate and said nothing"
        assert not dlg.result, "the dialog accepted a duplicated slot list"
        assert s["slots"] == ["08:00", "09:00", "10:00"], (
            "state was mutated despite the refusal"
        )
    finally:
        dlg.deleteLater()


@pytest.mark.ui
def test_a_harmless_append_is_committed_without_a_question(qapp):
    """ST-UI-021 — the commonest edit must not nag or unplace.

    Adding an eighth period moves nothing: every existing lesson still covers
    the same hours. A failure means a confirm dialog on an ordinary edit, which
    is how users learn to click straight through the one that matters — and, if
    the prompt were wired to a repair, it would unplace lessons nobody touched.

    This is the case that makes a "does a multi-slot lesson exist?" gate wrong:
    such a gate fires here, where nothing has changed at all.
    """
    s = _state(["09:00", "10:00", "11:00"])
    _add(s, "BLOCK", slot="09:00", dur=2)
    _add(s, "SINGLE", slot="11:00")

    dlg = _setup_dialog(s)
    try:
        dlg.slots_text.setPlainText("09:00\n10:00\n11:00\n12:00")
        assert slot_meaning_changes(s, dlg._get_current_slots()) == []
        dlg._ok()
        assert dlg.result, "a harmless append was refused"
        assert s["slots"] == ["09:00", "10:00", "11:00", "12:00"]
        assert all(c["placed"] for c in s["classes"]), (
            "an append unplaced a lesson it does not touch"
        )
    finally:
        dlg.deleteLater()


@pytest.mark.ui
def test_a_reorder_asks_before_committing_and_names_the_lessons(qapp,
                                                                monkeypatch):
    """ST-UI-021 — the user must be told which lessons an edit moves.

    A failure means the silent corruption stays silent: the measured case takes
    a clean schedule to six hard violations with nothing on screen. Telling the
    user only a COUNT would be nearly as bad — they cannot check a number.
    """
    from PyQt6.QtWidgets import QMessageBox
    from scheduler_app.ui import dialogs as dlg_mod

    asked = []

    def _question(*a, **k):
        asked.append(a[2] if len(a) > 2 else "")
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(dlg_mod.QMessageBox, "question", _question)

    slots = ["09:00", "10:00", "11:00", "12:00", "1. Ara", "13:00"]
    s = _state(slots)
    _add(s, "Uzun Ders", slot="12:00", dur=2)
    _add(s, "Kisa Ders", slot="13:00")

    dlg = _setup_dialog(s)
    try:
        dlg.slots_text.setPlainText("\n".join(sorted(slots)))
        dlg._ok()
        assert asked, "the reorder was committed with no question"
        assert "Uzun Ders" in asked[0], (
            f"the moved lesson is not named: {asked[0]!r}"
        )
        assert not dlg.result, "declining the question still committed"
        assert s["slots"] == slots, "state changed despite declining"
    finally:
        dlg.deleteLater()


@pytest.mark.ui
def test_the_lecturer_constraints_grid_never_sees_a_duplicate(qapp):
    """ST-UI-021 — a mid-edit reader must not lose an hour to a collision.

    ``LecturerConstraintsDialog`` builds a ``{slot: checkbox}`` map, so a
    repeated label costs it a checkbox — that hour's availability silently
    cannot be edited, and whatever the user had set for it is lost on save.
    The OK gate does not protect this path: it opens from a row double-click
    while the duplicate is still in the box.
    """
    s = _state(["08:00", "09:00", "10:00"])
    dlg = _setup_dialog(s)
    try:
        dlg.slots_text.setPlainText("08:00\n09:00\n09:00\n10:00")
        slots = dlg._get_current_slots()
        assert slots == ["08:00", "09:00", "10:00"], (
            "a mid-edit reader was handed a duplicated list"
        )
        assert len(slots) == len(set(slots))
    finally:
        dlg.deleteLater()
