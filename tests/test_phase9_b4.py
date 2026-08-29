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
