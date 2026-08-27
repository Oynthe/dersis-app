"""A user who is told a lesson could not be placed must be told what to change.

ST-SCHED-014 + ST-UI-015. Phase 3 built the diagnosis and the UI throws it away.
This module is the consuming half, plus two defects in the negotiator that make
the naive version of the panel worse than no panel at all.

``tests/test_unplaced_diagnostics.py`` guards the engine side — that the data is
*produced*. This module guards that it is *reachable*, and that the machinery
the panel leans on actually works.

The two defects, both measured
------------------------------
1. ``RelaxationSuggester._suggest_move_conflicts`` is **structurally incapable of
   returning anything**. It accumulates ``blocker_counts[id(existing)]`` — a
   CPython object address — and then resolves it through
   ``{cls_key(c): c for c in placed_classes}``, whose keys are uuid strings. The
   ``.get()`` always misses, so ``blocker`` is always ``None`` and the loop
   always ``continue``s. Measured across the presets: **0 suggestions of type
   ``move_conflicting``, out of 4 total on ``small`` and 4 on ``normal``.**

   This matters more than its size. The most common unplaced reason is "all
   remaining candidate slots are occupied", and the one suggestion that helps a
   user act on it is "move *Ders 6* out of Wednesday 12:00 and 16 slots open up".
   That is exactly what this function is for and exactly what never arrived, so
   a panel built on it renders the problem and an empty list of remedies.

2. ``_suggest_move_conflicts`` reads a **stored** placement through
   ``logic.slot_index``, whose own docstring forbids that. One lesson left on an
   hour the user deleted makes it raise. Measured: with a single off-grid class,
   3 of 4 ``negotiate_class`` calls die with
   ``ValueError: '20:00' is not in list``. Today that only bites a user who
   opens the Negotiation tab; a panel that diagnoses on every row selection makes
   it routine.

   The guard is the Phase 1 trap in miniature — switching to ``find_slot_index``
   and skipping turns a crash into a silent drop. It is nevertheless the right
   disposal here, because such a lesson genuinely blocks nothing:
   ``ConstraintValidator.add_placement`` already returns early on exactly the
   same condition, so counting it as a blocker would contradict the validator
   that decides ``check_placement``. What makes it honest rather than silent is
   that the count is reported — see
   ``test_skipped_off_grid_blockers_are_counted_not_swallowed``.

Conventions: never assert on ``isVisible()``; never hardcode an English string
(the suite is pinned to Turkish); read what a widget rendered.
"""
import pytest

from scheduler_app.core.constraint_negotiator import ConstraintNegotiator
from scheduler_app.core.logic import find_valid_options
from scheduler_app.core.models import new_state, new_class, mark_placed
from scheduler_app.translations import tr


DAYS = ["monday", "tuesday"]
SLOTS = ["09:00", "10:00", "11:00", "12:00"]


def _state(rooms=("R001",)):
    s = new_state()
    s["days"] = list(DAYS)
    s["slots"] = list(SLOTS)
    s["classrooms"] = list(rooms)
    s["years"] = {"Year-1": ["A"]}
    s["lecturers"] = ["Lect-01", "Lect-02"]
    return s


def _add(state, name, *, placed_at=None, lecturer="Lect-01", duration=1,
         room="R001", required_rooms=None, participants=0):
    cls = new_class()
    cls["name"] = name
    cls["class_code"] = name
    cls["lecturer"] = lecturer
    cls["duration"] = duration
    cls["participants"] = participants
    cls["targets"] = [{"year": "Year-1", "branch": "A"}]
    if required_rooms is not None:
        cls["required_classrooms"] = list(required_rooms)
    if placed_at:
        mark_placed(cls, placed_at[0], placed_at[1], room)
    state["classes"].append(cls)
    return cls


def _saturated():
    """One room, one lecturer, every hour of Monday taken; one lesson left over.

    The leftover's only obstacle is that the cells are occupied — the single
    most common unplaced reason, and the one the suggester exists to answer.
    """
    s = _state()
    s["days"] = ["monday"]
    for i, slot in enumerate(SLOTS):
        _add(s, f"SITTING{i}", placed_at=("monday", slot))
    victim = _add(s, "VICTIM")
    return s, victim


# ══════════════════════════════════════════════════════════════════════
#  1. The suggester must actually suggest something
# ══════════════════════════════════════════════════════════════════════

def test_a_blocked_class_is_told_which_lesson_to_move():
    """ST-UI-015 — "all slots are occupied" must come with something to do.

    A failure means the panel tells a user their lesson does not fit and offers
    nothing: the blockers are counted under a CPython object address and looked
    up by uuid, so the lookup always misses and the suggestion list is empty.
    The user is left to find the blocking lesson by eye across the whole week.
    """
    state, victim = _saturated()
    assert find_valid_options(state, victim) == [], "fixture is not saturated"

    report = ConstraintNegotiator(state).negotiate_class(victim)
    moves = [s for s in report["suggestions"]
             if s.get("type") == "move_conflicting"]

    assert moves, (
        f"no move_conflicting suggestion for a class whose only problem is "
        f"that the cells are taken; got {[s.get('type') for s in report['suggestions']]}"
    )
    top = moves[0]
    assert top["details"]["blocker_name"].startswith("SITTING")
    assert top["details"]["freed_slots"] > 0
    assert top["details"]["blocker_name"] in top["description"]


def test_the_named_blocker_is_one_that_actually_blocks():
    """ST-UI-015 — the suggestion must name a lesson that is really in the way.

    A failure means the panel sends the user to move an innocent lesson, which
    is worse than saying nothing: they disrupt a class and the problem remains.
    """
    s = _state(rooms=("R001", "R002"))
    s["days"] = ["monday"]
    s["years"] = {"Year-1": ["A", "B"]}
    # R001 is full on Monday; R002 is free, and the lesson sitting in it shares
    # neither the room, the lecturer, nor the student group with the victim —
    # so it blocks nothing on any of the three axes the validator checks.
    for i, slot in enumerate(SLOTS):
        _add(s, f"BLOCK{i}", placed_at=("monday", slot), room="R001")
    innocent = _add(s, "INNOCENT", placed_at=("monday", "09:00"), room="R002",
                    lecturer="Lect-02")
    innocent["targets"] = [{"year": "Year-1", "branch": "B"}]
    victim = _add(s, "VICTIM", required_rooms=["R001"])
    assert find_valid_options(s, victim) == []

    report = ConstraintNegotiator(s).negotiate_class(victim)
    named = {sg["details"]["blocker_name"] for sg in report["suggestions"]
             if sg.get("type") == "move_conflicting"}

    assert named, "no blocker named at all"
    assert innocent["name"] not in named, (
        "the panel named a lesson in a different room that blocks nothing"
    )
    assert all(n.startswith("BLOCK") for n in named), named


def test_a_pinned_blocker_is_never_suggested_for_moving():
    """ST-UI-015 — do not tell the user to move something they pinned.

    A failure means the panel's advice contradicts the user's own instruction,
    which Phase 3 established the engine must never do (ST-SCHED-002).
    """
    s = _state()
    s["days"] = ["monday"]
    for i, slot in enumerate(SLOTS):
        cls = _add(s, f"PINNED{i}")
        cls["pinned"] = True
        cls["pinned_day"] = "monday"
        cls["pinned_time"] = slot
        cls["pinned_classroom"] = "R001"
    victim = _add(s, "VICTIM")

    report = ConstraintNegotiator(s).negotiate_class(victim)
    named = {sg["details"]["blocker_name"] for sg in report["suggestions"]
             if sg.get("type") == "move_conflicting"}

    assert named == set(), f"suggested moving a pinned lesson: {named}"


# ══════════════════════════════════════════════════════════════════════
#  2. Diagnosing must not crash on a timetable the app supports
# ══════════════════════════════════════════════════════════════════════

def test_diagnosis_survives_a_lesson_left_on_a_deleted_hour():
    """ST-DATA-003 / ST-UI-015 — one orphan must not break the explanation.

    A failure means that after a user shortens the teaching day in Setup, asking
    why a lesson could not be placed raises ``ValueError: '20:00' is not in
    list`` — so the one screen that exists to explain a problem is itself broken
    by the problem. Measured before the fix: 3 of 4 calls died.
    """
    state, victim = _saturated()
    orphan = state["classes"][0]
    orphan["placed_time"] = "20:00"
    assert "20:00" not in state["slots"]

    report = ConstraintNegotiator(state).negotiate_class(victim)

    # Anti-vacuity: it did not merely survive by returning nothing — the other
    # three sitting lessons are still found as blockers.
    moves = [s for s in report["suggestions"]
             if s.get("type") == "move_conflicting"]
    assert moves, "survived the orphan by giving up on the whole question"
    named = {m["details"]["blocker_name"] for m in moves}
    assert orphan["name"] not in named


def test_skipped_off_grid_blockers_are_counted_not_swallowed():
    """ST-DATA-003 — a lesson skipped as off-grid must still be reported.

    Skipping it is correct: ``ConstraintValidator.add_placement`` returns early
    on the identical condition, so an orphaned lesson occupies no cell and
    blocks nothing — counting it would contradict the validator that decides
    ``check_placement``. But a guard that only skips is the Phase 1 trap
    (a crash becomes a silent drop), and ``find_off_grid_placements``' only
    caller is the exporter, so nothing in the UI mentions those lessons.

    A failure means the timetable holds lessons that are in no cell, in no
    export page, and in no explanation — invisible three ways.
    """
    state, victim = _saturated()
    state["classes"][0]["placed_time"] = "20:00"
    state["classes"][1]["placed_day"] = "saturday"

    report = ConstraintNegotiator(state).negotiate_class(victim)

    assert report.get("off_grid_blockers") == 2, (
        f"expected 2 orphaned lessons to be reported, got "
        f"{report.get('off_grid_blockers')!r}"
    )
    # Anti-vacuity: a clean board reports zero rather than always reporting.
    clean, clean_victim = _saturated()
    clean_report = ConstraintNegotiator(clean).negotiate_class(clean_victim)
    assert clean_report.get("off_grid_blockers") == 0


# ══════════════════════════════════════════════════════════════════════
#  3. PlaceClassDialog must not dead-end
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.ui
def test_the_place_dialog_explains_a_class_it_cannot_place(qapp):
    """ST-UI-015 — "0 geçerli yerleştirme bulundu" over an empty table.

    A failure means the dialog's most important case is a dead end: an empty
    table, no reason, and an ENABLED "Yerleştir" button which, when pressed,
    says "select a placement option first" — instructing the user to pick a row
    from a table with no rows.
    """
    from scheduler_app.ui.dialogs import PlaceClassDialog

    state, victim = _saturated()
    dlg = PlaceClassDialog(None, state)
    try:
        assert dlg.tree.topLevelItemCount() == 0, "fixture is placeable"
        explanation = dlg._explain_label.text()
        assert explanation.strip(), "empty table and no reason given"
        # It names the problem AND offers something to do about it, rather than
        # restating the count the info label already shows.
        assert tr("dialogs.place.no_options_title") in explanation
        assert tr("dialogs.place.what_to_change") in explanation, (
            f"reason given but nothing to change: {explanation}"
        )
        # And it names a specific lesson to move, not a generic sentence.
        assert any(c["name"] in explanation for c in state["classes"]
                   if c["name"].startswith("SITTING")), explanation
    finally:
        dlg.deleteLater()


@pytest.mark.ui
def test_the_place_button_is_disabled_when_there_is_nothing_to_place(qapp):
    """ST-UI-015 — a button that cannot succeed must not invite a click.

    A failure means the primary action is enabled over an empty table. Disabling
    it ALONE would be a regression, though — it converts a loud dead end into a
    quiet one — so the reason must be on screen too, which the test above pins.
    """
    from scheduler_app.ui.dialogs import PlaceClassDialog

    state, _victim = _saturated()
    dlg = PlaceClassDialog(None, state)
    try:
        assert not dlg._place_btn.isEnabled()
    finally:
        dlg.deleteLater()


@pytest.mark.ui
def test_the_place_button_comes_back_for_a_placeable_class(qapp):
    """ST-UI-015 — the dialog must recover when the user picks another class.

    A failure means selecting an unplaceable class permanently disables the
    dialog, so the user has to close and reopen it to place anything.
    """
    from scheduler_app.ui.dialogs import PlaceClassDialog

    state, victim = _saturated()
    # A second unplaced class that DOES fit, on a day the victim may not use.
    state["days"] = ["monday", "tuesday"]
    victim["allowed_days"] = ["monday"]
    easy = _add(state, "EASY")
    assert find_valid_options(state, easy), "EASY must be placeable"
    assert find_valid_options(state, victim) == [], "VICTIM must not be"

    dlg = PlaceClassDialog(None, state)
    try:
        names = [dlg.class_combo.itemText(i)
                 for i in range(dlg.class_combo.count())]
        easy_idx = next(i for i, n in enumerate(names) if "EASY" in n)
        victim_idx = next(i for i, n in enumerate(names) if "VICTIM" in n)

        dlg.class_combo.setCurrentIndex(victim_idx)
        assert not dlg._place_btn.isEnabled()
        dlg.class_combo.setCurrentIndex(easy_idx)
        assert dlg._place_btn.isEnabled(), (
            "the dialog stayed disabled after moving to a placeable class"
        )
        assert dlg.tree.topLevelItemCount() > 0
    finally:
        dlg.deleteLater()


# ══════════════════════════════════════════════════════════════════════
#  4. The reschedule results must carry the diagnosis Phase 3 produced
# ══════════════════════════════════════════════════════════════════════
#
# Ordering note, because it forces the design. `results_dlg.exec()` is MODAL and
# blocking, and `apply_reschedule` runs only after it returns -- so the rejected
# list does NOT exist while the dialog is on screen. Any design that puts it in
# a tab is stillborn. `summary` IS available beforehand, so the asymmetry below
# (bottleneck on the dialog, refusals in the warning log afterwards) is forced
# by that ordering, not chosen.

@pytest.mark.ui
def test_the_results_dialog_names_the_global_bottleneck(qapp):
    """ST-SCHED-014 — say why the instance cannot be built, not just which fell out.

    A failure means a user whose school is genuinely oversubscribed reads
    "all candidate slots are occupied" forty times and starts adding rooms at
    random, because nothing tells them the building offers 8 room-hours against
    14 class-hours of demand. That sentence is arithmetic, not search: it names
    what no amount of rearranging could fix.
    """
    from scheduler_app.ui.dialogs import BulkResultsDialog
    from PyQt6.QtWidgets import QLabel

    state, victim = _saturated()
    infeasibility = {
        "message": "TEST-BOTTLENECK needs 14 class-hours but only 8 are available",
        "bottlenecks": [{"type": "grid_capacity", "entity": None,
                         "required": 14, "available": 8, "message": "x"}],
    }
    dlg = BulkResultsDialog(
        None, [], [(victim, "all candidate slots are occupied")],
        infeasibility=infeasibility)
    try:
        shown = " ".join(w.text() for w in dlg.findChildren(QLabel))
        assert "TEST-BOTTLENECK" in shown, "the bottleneck sentence is not on screen"
        assert tr("dialogs.bulk_results.impossible_title") in shown
    finally:
        dlg.deleteLater()


@pytest.mark.ui
def test_a_feasible_result_shows_no_bottleneck_banner(qapp):
    """ST-SCHED-014 — the banner must not appear when nothing is proven.

    ``diagnose_infeasibility`` is deliberately one-sided: it reports only what
    it can PROVE, and passing its checks does not mean the instance is
    satisfiable. A failure means a red "cannot be built" banner over an ordinary
    result, which teaches the user to ignore the one message that is never a
    guess.
    """
    from scheduler_app.ui.dialogs import BulkResultsDialog
    from PyQt6.QtWidgets import QLabel

    state, victim = _saturated()
    dlg = BulkResultsDialog(
        None, [], [(victim, "reason")], infeasibility=None)
    try:
        shown = " ".join(w.text() for w in dlg.findChildren(QLabel))
        assert tr("dialogs.bulk_results.impossible_title") not in shown
    finally:
        dlg.deleteLater()


@pytest.mark.ui
def test_the_full_unplaced_reason_is_reachable(qapp):
    """ST-UI-015 — the reason column elides; the text must still be readable.

    A failure means the one column that explains the failure shows
    "Lecturer 'Lect-01' is busy in 100% of candidate slo…" and the rest is
    unreachable. Measured: 163 characters needing 1956 px in a 224 px column,
    with no tooltip. The data was always present — nothing surfaced it.
    """
    from scheduler_app.ui.dialogs import BulkResultsDialog

    state, victim = _saturated()
    long_reason = (
        "Lecturer 'Lect-01' is busy in 100% of candidate slots; suitable "
        "classrooms are occupied in 100% of candidate slots; student group "
        "conflicts block 100% of candidate slots")
    from PyQt6.QtWidgets import QTreeWidget

    dlg = BulkResultsDialog(None, [], [(victim, long_reason)])
    try:
        trees = dlg.findChildren(QTreeWidget)
        item = None
        for t in trees:
            if t.topLevelItemCount() and t.columnCount() == 3:
                item = t.topLevelItem(0)
                break
        assert item is not None, "no unplaced row rendered"
        assert item.toolTip(2) == long_reason, (
            "the full reason is not reachable from the row"
        )
    finally:
        dlg.deleteLater()


@pytest.mark.ui
def test_a_refused_commit_is_reported_not_discarded(qapp, dersis_home):
    """ST-SCHED-001 — apply_reschedule's return value was thrown away.

    Each entry is a placement the optimizer proposed and the COMMIT step
    refused, which is a different category from ``result.unplaced``: the solver
    knew about those. A rejection here means the state changed between
    optimizing and applying, so the user accepted one timetable and silently
    got a different one.

    A failure means that happens with nothing on screen at all.
    """
    from scheduler_app.ui.app import SchedulerApp

    state, victim = _saturated()
    app = SchedulerApp()
    try:
        app.state_data = state
        before = len(app.warning_log._sticky)
        app._report_rejected_placements([
            {"name": "Ders 12", "class_uid": "u1",
             "reason": "Room 'R001' occupied at Monday 09:00",
             "reasons": ["Room 'R001' occupied at Monday 09:00"]},
        ])
        after = app.warning_log._sticky[before:]
    finally:
        app.close()

    # One entry per refusal, plus the toast's own mirrored summary line
    # (_show_toast mirrors into the panel — Phase 2, ST-PERF-003).
    named = [(t, k) for t, k in after if "Ders 12" in t]
    assert len(named) == 1, after
    text, kind = named[0]
    assert "R001" in text, "the reason was dropped, only the name survived"
    assert kind == "error", "a refused commit is not an ordinary warning"


@pytest.mark.ui
def test_nothing_is_reported_when_every_placement_committed(qapp, dersis_home):
    """ST-SCHED-001 — the normal case must stay silent.

    A failure means every successful reschedule adds noise to the warning log,
    which is how users learn to stop reading it (ST-PERF-003's lesson).
    """
    from scheduler_app.ui.app import SchedulerApp

    state, _victim = _saturated()
    app = SchedulerApp()
    try:
        app.state_data = state
        before = len(app.warning_log._sticky)
        app._report_rejected_placements([])
        assert len(app.warning_log._sticky) == before
    finally:
        app.close()
