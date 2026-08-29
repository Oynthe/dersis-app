"""B4 — a resolved room *name* in ``required_classrooms`` is a snapshot, and
nothing reconciles it when Setup renames the room.

The defect
----------
``data_io/importer.py`` (the ``required_room_type`` / ``allowed_rooms`` block
around line 481) resolves a room *type* down to literal room **names** and
stores them in ``cls["required_classrooms"]``. Its own comment argues the point:
``required_classrooms`` is "the one room constraint the solver, the conflict
graph, the negotiator and the class dialog all already read".

What no one taught that field is that a room name is not a stable key. The only
repair that runs after a Setup change (``ui/app.py:3707``
``_reconcile_after_setup`` -> ``SchedulingWorkflow.reconcile_placements``,
``core/workflow.py:970``) and after an import (``ui/app.py:5258``) reads exactly
four axis sets — days, slots, classrooms, lecturers — and then only ever looks
at ``placed_*`` and ``pinned_*``. It never reads ``required_classrooms`` or
``excluded_classrooms``.

``SetupDialog._ok`` (``ui/dialogs.py:1966``) assigns ``self.state["classrooms"]
= rooms`` straight from the table, so *renaming* a room is indistinguishable
from *deleting one and adding another*. Renaming "Lab 1" to "Lab A" therefore
leaves every class that required "Lab 1" pointing at a room that does not exist:

1. ``reconcile_placements`` returns ``[]`` — the user is told nothing.
2. ``get_physical_room_candidates`` returns ``[]`` — the class can never be
   placed again, by drag, by the greedy pass or by the solver.
3. The Edit Class dialog rebuilds its room checkboxes from the *live* room
   list, so the blocking constraint is not shown at all; and because
   ``AddClassDialog._ok`` rebuilds ``required_classrooms`` from those same
   checkboxes, pressing OK without touching anything silently deletes it.

What "correct" means here
-------------------------
One invariant, stated the same way ``test_setup_reconcile`` states its own:

    After the reconcile that follows a setup change, no class may reference a
    classroom that the setup does not contain — in a *constraint* as much as in
    a placement — and any class the repair touched must be reported to the
    caller so the user can be told.

The tests below assert that invariant and its consequences through observable
state and through the real production functions (``reconcile_placements``,
``get_physical_room_candidates`` / ``get_room_candidates``, and a real
``AddClassDialog``). None of them names a private method or asserts that a
particular line ran, so a fix is free to land in ``reconcile_placements``, in a
constraint-aware helper it calls, or in a rename-tracking Setup — whichever the
fixer prefers.

How the rename is driven
------------------------
``SetupDialog`` is not opened. It writes the new room list into state with a
plain assignment (``ui/dialogs.py:1966``), and a rename reaches state as
nothing more than a different string in ``state["classrooms"]``, so
``_rename_room`` below reproduces it exactly. Everything *after* that point —
the repair, the candidate computation and the dialog — is the real production
code. ``test_the_app_level_reconcile_reports_it`` additionally drives the real
``SchedulerApp._reconcile_after_setup``, the call site Setup actually uses.

The Edit Class dialog IS driven for real (a real ``AddClassDialog`` with
``edit_cls=``, constructed offscreen); the tests read its real checkbox
registries and call its real ``_ok``.
"""
import re

import pytest

pytest.importorskip("PyQt6.QtWidgets", reason="PyQt6 not installed")


LAB_OLD = "Lab 1"
LAB_NEW = "Lab A"
HALL = "Hall A"
LECTURER = "Dr. Ay"
TARGET = {"year": "Year-1", "branch": "A"}


# ── Fixtures / builders ─────────────────────────────────────────────────────

def _make_state():
    """A minimal but complete state: two rooms, one face-to-face class."""
    from scheduler_app.core.models import new_state, new_class

    state = new_state()
    state["days"] = ["Monday"]
    state["slots"] = ["09:00"]
    state["classrooms"] = [LAB_OLD, HALL]
    state["classroom_capacities"] = {LAB_OLD: 0, HALL: 0}
    state["lecturers"] = [LECTURER]
    state["years"] = {"Year-1": ["A"]}

    cls = new_class()
    cls["class_code"] = "PHY101"
    cls["name"] = "Fizik Lab"
    cls["lecturer"] = LECTURER
    cls["targets"] = [dict(TARGET)]
    cls["duration"] = 1
    cls["participants"] = 0
    state["classes"] = [cls]
    return state, cls


def _rename_room(state, old, new):
    """Apply a Setup room rename exactly as ``SetupDialog._ok`` applies one."""
    state["classrooms"] = [new if r == old else r for r in state["classrooms"]]
    caps = state.get("classroom_capacities") or {}
    state["classroom_capacities"] = {
        (new if r == old else r): c for r, c in caps.items()
    }


def _required_lab_then_renamed():
    """A class requiring "Lab 1", after Setup renamed "Lab 1" to "Lab A"."""
    state, cls = _make_state()
    cls["required_classrooms"] = [LAB_OLD]
    _rename_room(state, LAB_OLD, LAB_NEW)
    return state, cls


def _excluded_lab_then_renamed():
    """A class excluding "Lab 1", after Setup renamed "Lab 1" to "Lab A"."""
    state, cls = _make_state()
    cls["excluded_classrooms"] = [LAB_OLD]
    _rename_room(state, LAB_OLD, LAB_NEW)
    return state, cls


def _reconcile(state):
    from scheduler_app.core.workflow import SchedulingWorkflow
    return SchedulingWorkflow.reconcile_placements(state)


def _dangling(cls, state, field):
    rooms = set(state.get("classrooms") or [])
    return [r for r in cls.get(field) or [] if r not in rooms]


# ── Leg 1: the repair must notice, and must say so ──────────────────────────

def test_reconcile_reports_the_class_whose_required_room_was_renamed():
    state, cls = _required_lab_then_renamed()

    affected = _reconcile(state)

    assert any(c is cls for c in affected), (
        "reconcile_placements reported %d affected classes after Setup renamed "
        "%r to %r, but %r still requires %r — a room the setup no longer "
        "contains. The user gets no warning and no toast, so the only signal "
        "that the class just became impossible to schedule is that it never "
        "gets scheduled again.\n"
        "  state['classrooms']          = %r\n"
        "  cls['required_classrooms']   = %r"
        % (len(affected), LAB_OLD, LAB_NEW, cls["name"], LAB_OLD,
           state["classrooms"], cls["required_classrooms"])
    )


def test_reconcile_leaves_no_dangling_required_classroom():
    state, cls = _required_lab_then_renamed()

    _reconcile(state)

    dangling = _dangling(cls, state, "required_classrooms")
    assert dangling == [], (
        "After the Setup rename %r -> %r and the reconcile that follows it, %r "
        "still requires %r. reconcile_placements repairs placed_* and pinned_* "
        "against the four axis sets but never reads required_classrooms, so a "
        "constraint may name a room that does not exist.\n"
        "  state['classrooms']        = %r\n"
        "  cls['required_classrooms'] = %r\n"
        "  dangling                   = %r"
        % (LAB_OLD, LAB_NEW, cls["name"], dangling, state["classrooms"],
           cls["required_classrooms"], dangling)
    )


def test_reconcile_leaves_no_dangling_excluded_classroom():
    state, cls = _excluded_lab_then_renamed()

    affected = _reconcile(state)

    dangling = _dangling(cls, state, "excluded_classrooms")
    assert dangling == [], (
        "After the Setup rename %r -> %r and the reconcile that follows it, %r "
        "still excludes %r, a room the setup no longer contains. The exclusion "
        "is now void — the class is free to be scheduled into %r, the very "
        "room the user forbade — and nothing anywhere says the constraint "
        "stopped applying. reconcile reported %d affected classes.\n"
        "  state['classrooms']        = %r\n"
        "  cls['excluded_classrooms'] = %r"
        % (LAB_OLD, LAB_NEW, cls["name"], dangling, LAB_NEW, len(affected),
           state["classrooms"], cls["excluded_classrooms"])
    )


def test_the_app_level_reconcile_reports_it(make_app):
    """The same claim at the call site Setup actually uses (ui/app.py:3707)."""
    state, cls = _required_lab_then_renamed()
    win = make_app()
    win.state_data = state

    affected = win._reconcile_after_setup()

    assert any(c is cls for c in affected), (
        "SchedulerApp._reconcile_after_setup — the one repair edit_setup runs "
        "after the user presses OK in Setup — reported %d affected classes. It "
        "only shows its 'placements cleared' toast when that list is "
        "non-empty, so renaming %r to %r produces no toast, no dialog and no "
        "log line, while %r is left requiring the vanished %r.\n"
        "  state['classrooms']        = %r\n"
        "  cls['required_classrooms'] = %r"
        % (len(affected), LAB_OLD, LAB_NEW, cls["name"], LAB_OLD,
           state["classrooms"], cls["required_classrooms"])
    )


# ── Leg 2: the consequence, through the real candidate functions ────────────

def test_class_is_still_placeable_after_its_required_room_is_renamed():
    from scheduler_app.core.models import (
        get_physical_room_candidates, get_room_candidates)

    state, cls = _required_lab_then_renamed()
    _reconcile(state)

    physical = get_physical_room_candidates(state, cls)
    assert physical, (
        "get_physical_room_candidates returned %r for %r after Setup renamed "
        "%r to %r and the reconcile ran. Every room in the school was filtered "
        "out by `r in cls['required_classrooms']` (core/models.py:557-558) "
        "because that list still names the old room. A class with no room "
        "candidates can never be placed — not by drag, not by the greedy pass, "
        "not by the solver — and it is unplaceable for a reason no screen in "
        "the app displays.\n"
        "  state['classrooms']        = %r\n"
        "  cls['required_classrooms'] = %r"
        % (physical, cls["name"], LAB_OLD, LAB_NEW, state["classrooms"],
           cls["required_classrooms"])
    )

    assert get_room_candidates(state, cls) == physical, (
        "get_room_candidates disagrees with get_physical_room_candidates for a "
        "face-to-face class: %r vs %r"
        % (get_room_candidates(state, cls), physical)
    )


# ── Leg 3: the Edit Class dialog, driven for real ───────────────────────────

def _open_edit_dialog(state, cls):
    """Construct the real AddClassDialog in edit mode, offscreen, unshown."""
    from scheduler_app.ui.dialogs import AddClassDialog
    return AddClassDialog(None, state, edit_cls=cls)


def test_edit_class_dialog_shows_every_room_constraint_in_effect(qapp):
    state, cls = _required_lab_then_renamed()
    _reconcile(state)

    dlg = _open_edit_dialog(state, cls)
    try:
        offered = set(dlg.req_room_cbs)
        missing = [r for r in cls.get("required_classrooms") or []
                   if r not in offered]
        checked = sorted(r for r, cb in dlg.req_room_cbs.items()
                         if cb.isChecked())
        assert not missing, (
            "The Edit Class dialog offers a 'Required Classrooms' checkbox per "
            "room in state['classrooms'], so after Setup renamed %r to %r it "
            "offers %r and ticks %r — while %r is in fact still constrained to "
            "%r and cannot be placed anywhere. The one constraint that is "
            "blocking the class is the one constraint the dialog does not "
            "show, so the user sees a class with no room requirement that "
            "nonetheless refuses to be scheduled.\n"
            "  cls['required_classrooms'] = %r\n"
            "  checkboxes offered         = %r\n"
            "  checkboxes ticked          = %r\n"
            "  not representable          = %r"
            % (LAB_OLD, LAB_NEW, sorted(offered), checked, cls["name"],
               cls["required_classrooms"], cls["required_classrooms"],
               sorted(offered), checked, missing)
        )
    finally:
        dlg.deleteLater()


def test_reopening_edit_class_and_pressing_ok_keeps_the_room_constraints(
        qapp, monkeypatch):
    from scheduler_app.ui import dialogs as dlg_mod

    errors = []
    monkeypatch.setattr(
        dlg_mod.QMessageBox, "critical",
        staticmethod(lambda *a, **k: errors.append(a[2] if len(a) > 2 else a)))

    state, cls = _required_lab_then_renamed()
    _reconcile(state)
    before = list(cls.get("required_classrooms") or [])

    dlg = _open_edit_dialog(state, cls)
    try:
        dlg._ok()
        assert dlg.result is not None, (
            "AddClassDialog._ok refused the unmodified class: %r" % (errors,))
        after = list(dlg.result.get("required_classrooms") or [])
    finally:
        dlg.deleteLater()

    assert set(after) == set(before), (
        "Opening Edit Class on %r and pressing OK without touching anything "
        "changed its required classrooms from %r to %r. _ok rebuilds the field "
        "from the checkbox registry (ui/dialogs.py:2695), and the registry was "
        "built from the live room list, so a constraint naming the renamed %r "
        "is not merely hidden — it is destroyed by the act of looking at the "
        "class. That is silent, undoable-only-by-luck data loss on a field the "
        "user never edited.\n"
        "  before OK = %r\n"
        "  after  OK = %r"
        % (cls["name"], before, after, LAB_OLD, before, after)
    )


# ── Field-name guard: `excluded_classrooms` is the state field ──────────────

def test_the_state_field_is_excluded_classrooms_not_excluded_rooms():
    """Passes today. It pins the name a fix must aim at.

    The handoff for this defect used both ``excluded_classrooms`` and
    ``excluded_rooms``. Only the first exists in state: ``excluded_rooms`` is
    the *spreadsheet column* (``data_io/schema.py:51``), which
    ``importer.py:699`` copies into ``cls["excluded_classrooms"]``. A repair
    written against ``excluded_rooms`` would read a key that is never present
    and silently do nothing, which is why this is asserted behaviourally rather
    than by looking the key up.
    """
    from scheduler_app.core.models import new_class, get_physical_room_candidates

    defaults = new_class()
    assert "excluded_classrooms" in defaults, (
        "new_class() no longer ships 'excluded_classrooms': %r"
        % (sorted(defaults),))
    assert "excluded_rooms" not in defaults, (
        "new_class() now ships 'excluded_rooms' too — this defect's fix must "
        "be told which of the two is authoritative: %r" % (sorted(defaults),))

    state, cls = _make_state()
    cls["excluded_classrooms"] = [HALL]
    assert HALL not in get_physical_room_candidates(state, cls), (
        "'excluded_classrooms' was ignored by get_physical_room_candidates")

    state, cls = _make_state()
    cls["excluded_rooms"] = [HALL]
    assert HALL in get_physical_room_candidates(state, cls), (
        "'excluded_rooms' now excludes rooms in state — the two field names "
        "have converged and this defect's fix must target both")


# ── Where this repair may NOT run: the .egu load path ───────────────────────
#
# `core/models.py`'s `find_off_grid_placements` states the policy in writing:
# it is "Deliberately NOT called from ``normalize_state_classes`` (and so not
# from the .egu load path): unplacing orphans at load time would silently
# discard the user's own placements with no way to see or undo it, which is
# the same class of bug in a new place. Callers decide what to do — warn,
# list, or offer to reconcile."
#
# B4 put `_reconcile_after_setup()` into `open_file` and chose none of the
# three: it repaired unconditionally, then cleared the undo stack, then
# recorded the result as the clean baseline. Measured on the file this test
# builds: 2 of 2 lessons unplaced, one count-only toast, undo depth 0, Ctrl+Z
# a no-op. One save afterwards and the placements are gone from disk. The call
# was reverted; this test is what stops it coming back.
#
# It does NOT forbid `open_file` from telling the user anything — a warn, a
# list or an offer-to-reconcile is exactly what the policy asks for, and all
# three keep this green. It forbids the silent destruction.


def _stranded_egu(tmp_path):
    """A saved .egu that a repair sweep would want to "fix".

    Both lessons name a teacher who is not in the file's own ``lecturers``
    list — the "file from an older build" case B4 exists to rescue — and both
    require a room the file's own ``classrooms`` list does not contain. Every
    branch of ``reconcile_placements`` therefore has something to do: unplace
    on ``not lecturer_ok``, and empty ``required_classrooms``.
    """
    from scheduler_app import storage
    from scheduler_app.core.models import new_state, new_class, mark_placed

    state = new_state()
    state["days"] = ["monday"]
    state["slots"] = ["09:00", "10:00"]
    state["classrooms"] = [LAB_OLD, HALL]
    state["classroom_capacities"] = {LAB_OLD: 0, HALL: 0}
    state["lecturers"] = []
    state["years"] = {"Year-1": ["A"]}
    state["classes"] = []
    for i in range(2):
        cls = new_class()
        cls["class_code"] = "PHY10%d" % i
        cls["name"] = "Fizik %d" % i
        cls["lecturer"] = LECTURER
        cls["targets"] = [dict(TARGET)]
        cls["duration"] = 1
        cls["participants"] = 0
        cls["required_classrooms"] = ["GHOST_LAB"]
        state["classes"].append(cls)
        mark_placed(cls, "monday", state["slots"][i], LAB_OLD)

    path = str(tmp_path / "stranded.egu")
    storage.save_encrypted(state, path)
    return path


@pytest.mark.ui
def test_opening_a_file_does_not_unplace_the_users_lessons(
        make_app, monkeypatch, tmp_path):
    """The .egu load path may not repair by destroying.

    `open_file` runs `_undo_stack.clear()` and `mark_current_state_as_baseline()`
    immediately after the load, so anything it changes is unrecoverable AND
    recorded as "no unsaved changes". That is the one place in the app where
    an automatic repair has no way back, which is why the policy singles it
    out.
    """
    from PyQt6.QtWidgets import QFileDialog, QMessageBox

    path = _stranded_egu(tmp_path)
    win = make_app()

    for name in ("question", "information", "warning", "critical"):
        monkeypatch.setattr(
            QMessageBox, name,
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (path, "")))

    win.open_file()

    classes = win.state_data["classes"]
    # Anti-vacuity: the file really was loaded, so the assertions below are
    # about the opened schedule and not about an empty default state.
    assert len(classes) == 2, (
        "the file did not load: state has %d classes, the .egu had 2"
        % len(classes))
    assert win.current_file == path, (
        "open_file did not adopt the file: current_file is %r"
        % (win.current_file,))

    placed = [c for c in classes if c.get("placed")]
    assert len(placed) == 2, (
        "opening a saved schedule unplaced %d of its 2 lessons. The undo "
        "stack is cleared one statement later and the result is marked as "
        "the clean baseline, so this is silent, unrecoverable data loss on "
        "File > Open — the exact thing core/models.py:690-694 forbids. "
        "Placements now: %r"
        % (2 - len(placed),
           [(c["name"], c.get("placed"), c.get("placed_day"),
             c.get("placed_time"), c.get("placed_classroom"))
            for c in classes]))

    still_required = [c.get("required_classrooms") for c in classes]
    assert still_required == [["GHOST_LAB"], ["GHOST_LAB"]], (
        "opening a saved schedule deleted a room constraint the user wrote: "
        "%r. `[]` means 'any room' everywhere in core, so the lesson that "
        "must be in one particular lab is now free to be auto-scheduled into "
        "a lecture hall — with no undo entry and no record of the room name "
        "that was dropped." % (still_required,))


# ── The fourth call site: Setup opened from a lesson's context menu ─────────
#
# `_edit_lecturer_from_class` (reached from the unplaced-list context menu,
# Edit > Edit Lecturer) opens the FULL `SetupDialog` — every tab editable,
# only `focus_lecturer=` differs from `edit_setup` — and then runs the same
# `_reconcile_after_setup()`. `edit_setup` holds a `copy.deepcopy` across the
# dialog and commits it when the dialog is accepted; this site recorded
# nothing at all, so the same room rename was undoable through one entry point
# and permanent through the other. Measured before the fix:
#
#     edit_setup                -> required [], undo 0->1, undo() restores ['Lab 1']
#     _edit_lecturer_from_class -> required [], undo 0->0, undo() a no-op
#
# and renaming the room BACK does not restore the constraint, because the name
# is what the sweep deleted.


class _FakeSetupDialog:
    """A `SetupDialog` that renames a room and is accepted.

    The real dialog reaches state exactly this way: `SetupDialog._ok` assigns
    `self.state["classrooms"] = rooms` from its table, so a rename arrives as
    nothing but a different string in the list (`_rename_room` above). What
    matters to these tests is the surface `app.py` uses — construct, `exec()`,
    read `.result` — and that the state has changed by the time `.result` is
    read.
    """

    def __init__(self, parent, state, **kwargs):
        self._state = state
        self.result = False

    def exec(self):
        _rename_room(self._state, LAB_OLD, LAB_NEW)
        self.result = True
        return 1


def _app_on(make_app, state):
    win = make_app()
    win.state_data = state
    win._workflow.state = state
    return win


@pytest.mark.ui
def test_editing_a_lecturer_from_a_class_is_one_undoable_action(
        make_app, monkeypatch):
    """The context-menu route into Setup must be as undoable as the menu one."""
    state, cls = _make_state()
    cls["required_classrooms"] = [LAB_OLD]
    win = _app_on(make_app, state)
    monkeypatch.setattr("scheduler_app.ui.app.SetupDialog", _FakeSetupDialog)

    assert win._undo_stack == [], "the fixture started with undo history"

    win._edit_lecturer_from_class(win.state_data["classes"][0])

    # Anti-vacuity: the destructive repair really did run, so there is
    # something for undo to be responsible for.
    assert win.state_data["classrooms"] == [LAB_NEW, HALL], (
        "the stand-in dialog did not rename the room: %r"
        % (win.state_data["classrooms"],))
    assert win.state_data["classes"][0]["required_classrooms"] == [], (
        "the reconcile did not drop the dangling room name, so this test is "
        "not measuring the destruction it claims to: %r"
        % (win.state_data["classes"][0]["required_classrooms"],))

    assert len(win._undo_stack) == 1, (
        "Setup opened from a lesson's context menu recorded %d undo entries. "
        "It opens the same full SetupDialog as File > Setup and runs the same "
        "repair, which deletes the room NAME — so with no undo entry the "
        "user's 'this lesson must be in the physics lab' is gone for good, "
        "and putting the room name back does not bring it back."
        % len(win._undo_stack))

    win.undo()
    live = win.state_data["classes"][0]
    assert live["required_classrooms"] == [LAB_OLD], (
        "one Ctrl+Z after Setup-from-a-class did not restore the room "
        "constraint: %r" % (live["required_classrooms"],))
    assert win.state_data["classrooms"] == [LAB_OLD, HALL], (
        "the undo restored the constraint without restoring the room list it "
        "refers to — a half-transaction undo, which is the ST-ARCH-012 "
        "failure: %r" % (win.state_data["classrooms"],))


@pytest.mark.ui
def test_a_cancelled_lecturer_edit_records_nothing(make_app, monkeypatch):
    """The other half of the gate, and the half Phase 4 got wrong on Setup.

    A snapshot recorded before the dialog and popped on cancel cannot put back
    the redo stack it cleared, nor the entry it evicted at the 50-entry cap.
    The entry must be COMMITTED only once the dialog is accepted — which is
    what `edit_setup` does and what this asserts for its sibling.
    """
    state, cls = _make_state()
    cls["required_classrooms"] = [LAB_OLD]

    class _Cancelled(_FakeSetupDialog):
        def exec(self):
            self.result = False
            return 0

    win = _app_on(make_app, state)
    monkeypatch.setattr("scheduler_app.ui.app.SetupDialog", _Cancelled)
    win._push_undo("earlier")
    win.undo()
    assert len(win._redo_stack) == 1, "the fixture armed no redo entry"

    win._edit_lecturer_from_class(win.state_data["classes"][0])

    assert win._undo_stack == [], (
        "a CANCELLED Setup-from-a-class left an undo entry behind: %r"
        % ([e[0] for e in win._undo_stack],))
    assert len(win._redo_stack) == 1, (
        "a CANCELLED Setup-from-a-class destroyed the pending redo entry: "
        "depth is %d, it was 1" % len(win._redo_stack))
    assert win.state_data["classes"][0]["required_classrooms"] == [LAB_OLD], (
        "a cancelled dialog changed the state: %r"
        % (win.state_data["classes"][0]["required_classrooms"],))


# ── Emptying a requirement is not the same event as narrowing one ──────────
#
# The sweep above argues, in its own comment, that dropping a dangling room
# name is safe "ONLY because the class then lands in `affected`". `affected`
# was a COUNT to both call sites — not the class, not the field, not the room
# — so the two outcomes it covered were reported with one identical sentence.
# Measured on ONE Setup OK renaming "Lab 1" -> "Lab A" over two classes:
#
#   required ['Lab 1','Lab 2'] -> ['Lab 2']  candidates ['Lab 2']   (harmless)
#   required ['Lab 1']         -> []         candidates ALL rooms   ("any room")
#   the single message for both: "2 class(es) were repaired ..."
#
# and then, through the app's own placer, `place_batch` put the physics-lab
# lesson in "Hall A" — the lecture hall. That is the ST-FUNC-009 inversion
# arriving through the repair written to prevent it, and the user could not
# have stopped it: the message named neither the lesson nor the room, and the
# room name is what the sweep deleted, so it cannot be recovered by putting
# the room back.
#
# These tests pin the distinction, not the wording: they read the room and
# lesson names, which are the user's own data and identical in all 22 locales.

LAB_2 = "Lab 2"
OTHER = "Kimya Lab"


def _add_class(state, code, name, required):
    from scheduler_app.core.models import new_class

    cls = new_class()
    cls["class_code"] = code
    cls["name"] = name
    cls["lecturer"] = LECTURER
    cls["targets"] = [dict(TARGET)]
    cls["duration"] = 1
    cls["participants"] = 0
    cls["required_classrooms"] = list(required)
    state["classes"].append(cls)
    return cls


def _narrowed_and_emptied():
    """One Setup rename, two classes: one keeps a requirement, one loses it.

    The pair is the whole point. A fix that reported *every* repaired class in
    detail would pass a test built on the emptied class alone while making an
    ordinary rename a wall of text.
    """
    state, narrowed = _make_state()
    state["classrooms"] = [LAB_OLD, LAB_2, HALL]
    state["classroom_capacities"] = {LAB_OLD: 0, LAB_2: 0, HALL: 0}
    narrowed["name"] = OTHER
    narrowed["required_classrooms"] = [LAB_OLD, LAB_2]
    emptied = _add_class(state, "PHY102", "Fizik Lab", [LAB_OLD])
    _rename_room(state, LAB_OLD, LAB_NEW)
    return state, narrowed, emptied


def _toasts_of(win, monkeypatch):
    """Capture what `_show_toast` was given, message text only."""
    said = []
    monkeypatch.setattr(type(win), "_show_toast",
                        lambda self, message, kind="info": said.append(message))
    return said


def test_reconcile_records_which_lesson_lost_its_room_requirement_entirely():
    state, narrowed, emptied = _narrowed_and_emptied()

    report = _reconcile(state)

    # Anti-vacuity: both classes really were repaired, so a report that
    # singles one out is choosing, not merely reflecting an empty sweep.
    assert len(report) == 2, (
        "the fixture did not repair both classes: %r"
        % ([c["name"] for c in report],))
    assert narrowed["required_classrooms"] == [LAB_2], (
        "the narrowed class is not narrowed: %r"
        % (narrowed["required_classrooms"],))
    assert emptied["required_classrooms"] == [], (
        "the emptied class is not emptied: %r"
        % (emptied["required_classrooms"],))

    lost = [(c["name"], list(rooms))
            for c, rooms in report.lost_room_requirements]
    assert lost == [("Fizik Lab", [LAB_OLD])], (
        "reconcile_placements reported %r as having lost its room requirement "
        "outright. It must report exactly the classes whose "
        "required_classrooms went from non-empty to EMPTY, together with the "
        "room names it deleted — an empty list means 'any room' everywhere in "
        "core (core/models.py:557), so that class can now be auto-scheduled "
        "into a lecture hall, and the room name is the only thing that lets a "
        "user put the requirement back. %r was merely narrowed and is still "
        "constrained to %r, so it is not this event.\n"
        "  lost_room_requirements = %r"
        % (lost, narrowed["name"], narrowed["required_classrooms"], lost))


def test_the_message_names_the_lesson_and_the_room_it_may_no_longer_require(
        make_app, monkeypatch):
    state, narrowed, emptied = _narrowed_and_emptied()
    win = _app_on(make_app, state)
    said = _toasts_of(win, monkeypatch)

    win._reconcile_after_setup()

    assert said, "the repair told the user nothing at all"
    text = "\n".join(said)
    assert emptied["name"] in text and LAB_OLD in text, (
        "after Setup renamed %r to %r, %r lost its ONLY room requirement and "
        "can now be scheduled in any room — including a lecture hall, measured "
        "through place_batch. The user was told %r, which names neither the "
        "lesson nor the room, so there is nothing to act on: the room name is "
        "gone from the state and putting the room back does not restore the "
        "requirement.\n"
        "  said = %r"
        % (LAB_OLD, LAB_NEW, emptied["name"], text, said))
    assert narrowed["name"] not in text, (
        "%r was merely narrowed to %r — still constrained, still a legal "
        "schedule — and naming it here makes an ordinary Setup rename read "
        "like the dangerous case. Only the emptied requirement is news.\n"
        "  said = %r"
        % (narrowed["name"], narrowed["required_classrooms"], said))
    assert re.search(r"(?<!\d)2(?!\d)", text), (
        "the total repaired count (2) is no longer stated: %r" % (said,))


def test_a_narrowed_room_requirement_alone_stays_a_one_line_repair(
        make_app, monkeypatch):
    """The quiet case must stay quiet.

    A school that renames one room used by one lesson that has other rooms to
    fall back on has lost nothing it can act on, and
    `tests/test_setup_reconcile.py::test_setup_without_removals_changes_and_warns_nothing`
    holds the neighbouring "changed nothing" case. This holds the line one step
    in: repaired, still constrained, one sentence.
    """
    state, narrowed = _make_state()
    state["classrooms"] = [LAB_OLD, LAB_2, HALL]
    state["classroom_capacities"] = {LAB_OLD: 0, LAB_2: 0, HALL: 0}
    narrowed["required_classrooms"] = [LAB_OLD, LAB_2]
    _rename_room(state, LAB_OLD, LAB_NEW)
    win = _app_on(make_app, state)
    said = _toasts_of(win, monkeypatch)

    win._reconcile_after_setup()

    assert narrowed["required_classrooms"] == [LAB_2], (
        "the fixture did not narrow anything: %r"
        % (narrowed["required_classrooms"],))
    assert len(said) == 1 and "\n" not in said[0], (
        "a narrowing repair now emits %d message(s) / %d line(s). The class is "
        "still constrained to %r and the schedule it produces is still legal, "
        "so this is the ordinary case and it may not grow: %r"
        % (len(said), sum(m.count("\n") + 1 for m in said),
           narrowed["required_classrooms"], said))
    assert LAB_OLD not in said[0], (
        "the narrowing repair names the dropped room %r as if the requirement "
        "were gone: %r" % (LAB_OLD, said[0]))


def test_the_lost_requirement_message_does_not_grow_with_the_school(
        make_app, monkeypatch):
    """Bounded output, unbounded truth.

    Deleting a room a whole department requires is one gesture, and the toast
    is 350 px wide with a 3 s life (ui/widgets.py:48). ST-UI-B6 is the same
    shape one layer over: an unbounded report measured 24 110 px tall for 500
    rows. The COUNT must stay true; the list of names is what gets cut.
    """
    state, _first = _make_state()
    state["classes"] = []
    for i in range(40):
        _add_class(state, "PHY%03d" % i, "Ders %d" % i, [LAB_OLD])
    _rename_room(state, LAB_OLD, LAB_NEW)
    win = _app_on(make_app, state)
    said = _toasts_of(win, monkeypatch)

    win._reconcile_after_setup()

    text = "\n".join(said)
    named = text.count("(%s)" % LAB_OLD)
    assert named <= 3, (
        "the message named %d of the 40 lessons that lost their room "
        "requirement. One deleted room can be required by an entire "
        "department; the sentence has to carry the true total and a readable "
        "sample, not every name.\n  message = %r" % (named, text))
    assert named >= 1, (
        "the message named no lesson at all, so 40 lessons became placeable "
        "in any room with nothing to act on: %r" % (text,))
    assert re.search(r"(?<!\d)40(?!\d)", text), (
        "the true total (40) is missing from a truncated message — the "
        "truncation may cost names, never the size of the problem: %r"
        % (text,))


# ── The import path says the same thing ────────────────────────────────────
#
# `_import_from_excel` replaces `state["classrooms"]` wholesale, so it strands
# a PRE-EXISTING class's `required_classrooms` exactly as a Setup rename does,
# and it is the one call site whose repair the user never asked for. It reads
# the same report through the same sentence builder; this is what says so.


class _FakeReport:
    is_valid = True
    warnings = []

    def summary(self):
        return ""


def _dataset_replacing_rooms(rooms):
    from scheduler_app.data_io.importer import SchedulerDataset

    ds = SchedulerDataset()
    ds.state["classrooms"] = list(rooms)
    ds.state["classroom_capacities"] = {r: 0 for r in rooms}
    ds.state["lecturers"] = []
    ds.state["years"] = {}
    ds.state["classes"] = []
    ds.report = _FakeReport()
    return ds


@pytest.mark.ui
def test_an_import_that_strands_a_requirement_names_it_too(
        make_app, monkeypatch, tmp_path):
    from PyQt6.QtWidgets import QFileDialog

    from scheduler_app.plans import TIER_INSTITUTIONAL
    from scheduler_app.ui.tier_enforcement import TierEnforcement
    import scheduler_app.data_io.importer as importer_mod
    import scheduler_app.ui.app as app_mod

    state, cls = _make_state()
    cls["required_classrooms"] = [LAB_OLD]
    win = _app_on(make_app, state)

    enforcer = TierEnforcement.instance()
    monkeypatch.setattr(enforcer, "_tier_slug", TIER_INSTITUTIONAL)
    monkeypatch.setattr(enforcer, "_tier_confirmed", True)
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(tmp_path / "x.xlsx"), "")))
    monkeypatch.setattr(app_mod, "show_validation_report",
                        lambda *a, **k: None)
    monkeypatch.setattr(
        importer_mod, "load_scheduler_data_from_excel",
        lambda path: _dataset_replacing_rooms([LAB_NEW, HALL]))
    said = _toasts_of(win, monkeypatch)

    win._import_from_excel()

    # Anti-vacuity: the import really did land and really did strand the class.
    assert win.state_data["classrooms"] == [LAB_NEW, HALL], (
        "the stand-in workbook did not replace the room list: %r"
        % (win.state_data["classrooms"],))
    assert cls["required_classrooms"] == [], (
        "the import did not strand the requirement, so this test is not "
        "measuring what it claims: %r" % (cls["required_classrooms"],))

    text = "\n".join(said)
    assert cls["name"] in text and LAB_OLD in text, (
        "an import replaced the room list and %r silently lost its only room "
        "requirement — it can now be auto-scheduled into any room in the "
        "school. The import path reports through the same builder as Setup "
        "and must name the lesson and the room here too; the user did not "
        "even change a setup, so a bare count is doubly unactionable.\n"
        "  said = %r" % (cls["name"], said))
