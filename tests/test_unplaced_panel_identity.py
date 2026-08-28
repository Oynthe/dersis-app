"""ST-ARCH-015 — the unplaced sidebar must address classes by identity.

The defect, as shipped in v1.0.0: ``_unplaced_indices`` stored *positions* into
``state_data["classes"]``, rebuilt only by ``_refresh_unplaced_panel``. But
``refresh_grid`` (app.py) calls ``_render_current_tab`` -> ``_clear_class_selection``
-> ``_refresh_open_slots`` -> ``_open_slots_fingerprint`` -> ``selected_classes``
**before** ``_update_side_panels`` rebuilds those positions. So on any repaint
that follows a shrink of the classes list, with a row selected in the sidebar,
the reader indexed the new short list with an old long-list position.

Two distinct user-visible failures, both live:

1. **The process died.** ``IndexError`` inside a Qt slot; PyQt6 answers an
   unhandled exception in a slot with ``qFatal()``, so DERSIS exited at
   ``0xC0000409`` with no dialog, no traceback, and any edit still inside the
   1.5 s autosave debounce (ST-PERF-002) unwritten. Measured directly: a slot
   that raises exits 3221226505 with empty stdout and empty stderr.
2. **Silently the wrong class.** When the shrink happened at the *front*, the
   stale position still resolved -- to a different class than the one the user
   had highlighted. That one never raised, so no crash report could exist.

Reachable from Ctrl+Z (app.py, "actions.undo"), from deleting classes, and from
any of the ``refresh_grid()`` call sites. The fix addresses classes by
``class_uid``, whose own docstring in ``models.cls_key`` says it exists so that
identity "survives serialization, copying, and list mutations".
"""
import pytest

from scheduler_app.core.models import cls_key


def _cls(name, code):
    return {
        "name": name, "class_code": code, "lecturer": "Lect-01",
        "duration": 1, "participants": 10,
        "targets": [{"year": "Y1", "branch": "A"}],
        "allowed_days": [], "allowed_times": [],
        "excluded_days": [], "excluded_times": [],
        "required_classrooms": [], "excluded_classrooms": [],
        "placed": False, "placed_day": None, "placed_time": None,
        "placed_classroom": None, "pinned": False, "protection": "none",
        "is_online": False, "joint_class_group": "", "sequential": False,
    }


def _app_with(make_app, n):
    app = make_app()
    app.state_data.update({
        "days": ["monday"], "slots": ["09:00", "10:00"],
        "classrooms": ["R001"], "classroom_capacities": {"R001": 30},
        "lecturers": ["Lect-01"], "lecturer_availability": {},
        "years": {"Y1": ["A"]},
        "classes": [_cls("Ders-%d" % i, "K%d" % i) for i in range(n)],
    })
    app._workflow.state = app.state_data
    app.refresh_grid()
    return app


@pytest.mark.ui
def test_undo_does_not_kill_the_app_when_a_sidebar_row_is_selected(make_app):
    """A failure here means Ctrl+Z closes DERSIS without saving or warning."""
    app = _app_with(make_app, 3)
    app._push_undo("add")
    app.state_data["classes"].append(_cls("Ders-yeni", "K9"))
    app.refresh_grid()
    app.unplaced_list.setCurrentRow(app.unplaced_list.count() - 1)
    assert len(app.unplaced_list.selected_classes()) == 1, "fixture: no selection"

    app.undo()   # raised IndexError -> qFatal before the fix

    assert len(app.state_data["classes"]) == 3


@pytest.mark.ui
def test_deleting_classes_does_not_kill_the_app(make_app):
    """The commoner route: the sidebar has a selection and classes go away."""
    app = _app_with(make_app, 4)
    app.unplaced_list.setCurrentRow(3)
    assert len(app.unplaced_list.selected_classes()) == 1, "fixture: no selection"

    del app.state_data["classes"][0:2]
    app.refresh_grid()

    assert len(app.state_data["classes"]) == 2


@pytest.mark.ui
def test_a_selected_row_never_resolves_to_a_different_class(make_app):
    """The silent half: the panel must not hand back the wrong lesson.

    Removing the FIRST class leaves every later position resolving -- to its
    neighbour. A position-keyed panel reports the wrong class with no error at
    all, which is why this one could never have been noticed as a crash.
    """
    app = _app_with(make_app, 4)
    app.unplaced_list.setCurrentRow(2)
    picked = app.unplaced_list.selected_classes()
    assert len(picked) == 1, "fixture: no selection"
    chosen_uid = cls_key(picked[0])

    # Shrink at the FRONT and read back without repainting. This is the window
    # the crash path actually runs in: _render_current_tab reads the selection
    # before _update_side_panels rebuilds the sidebar. Calling refresh_grid()
    # here instead would clear the Qt selection and the assertion below would
    # be vacuous -- measured: it passes against the unfixed code.
    app.state_data["classes"].pop(0)

    still = app.unplaced_list.selected_classes()
    assert len(still) == 1, (
        "the selection vanished; this test needs it to survive to say anything")
    assert cls_key(still[0]) == chosen_uid, (
        "the sidebar resolved the selected row to %r, but the user had "
        "highlighted %r -- a stored position followed the shift instead of "
        "the class" % (still[0]["name"], picked[0]["name"]))


@pytest.mark.ui
def test_the_panel_stores_identities_rather_than_positions(make_app):
    """Pins the mechanism, so a revert to positional indexing goes red.

    Without this, the three tests above can all be satisfied by bounds-checking
    a position -- which fixes the crash and leaves the wrong-class half alive.
    """
    app = _app_with(make_app, 3)
    assert not hasattr(app, "_unplaced_indices"), (
        "the positional index is back; it cannot survive a list mutation")
    uids = app._unplaced_uids
    assert uids == [cls_key(c) for c in app.state_data["classes"]], uids
    assert all(isinstance(u, str) for u in uids), uids
