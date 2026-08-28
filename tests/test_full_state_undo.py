"""Undo covers the whole schedule, not just the lessons — ST-ARCH-012.

ST-ARCH-012 (Medium) · ``ui/app.py``
    "Undo model covers only ``state['classes']``; setup/availability edits are
    irreversible and can desync restored classes."

The second clause is the dangerous half, and Phase 4 proved it by building the
naive fix and withdrawing it. With a classes-only snapshot, "Undo: setup
change" restores the lessons **with their old placements onto the new grid** --
resurrecting the ST-DATA-003 orphans from a button labelled as a safety net.
A half-transaction undo is worse than none.

What makes the full-state version safe is that the placements and the axes they
refer to are restored together. What makes it *cheap* is measurement, not
assumption -- deep-copying the whole state against the classes alone:

    normal (80 classes)   0.788 -> 0.811 ms   +2.9%    6.1 -> 6.3 MB
    large  (250 classes)  2.543 -> 2.583 ms   +1.6%   18.7 -> 19.1 MB

across the entire 50-entry stack, because the classes list is ~97% of the bytes
either way. The audit's cost framing ("stacked on the per-refresh encryption
write") is stale: Phase 2 removed that write.

The trap this module exists for
--------------------------------
The obvious implementation rebinds ``self.state_data`` to the snapshot, and it
is silently wrong. ``SchedulingWorkflow`` holds an alias to the *same dict*
(``self._workflow.state``), and so does the debounced autosave timer. Rebinding
leaves them pointing at the pre-undo state, so the grid shows one timetable
while every validator answers about another -- and nothing raises.

The old classes-only undo was accidentally immune, because it assigned into
``state_data["classes"]`` instead of rebinding. Widening the snapshot removes
that accident, so ``test_undo_does_not_break_the_workflows_view_of_the_state``
pins the in-place contract directly.
"""
import pytest


def _cls(name, day=None, slot=None, room=None):
    return {
        "name": name, "class_code": name, "lecturer": "Lect-01",
        "duration": 1, "participants": 10,
        "targets": [{"year": "Y1", "branch": "A"}],
        "allowed_days": [], "allowed_times": [],
        "excluded_days": [], "excluded_times": [],
        "required_classrooms": [], "excluded_classrooms": [],
        "placed": bool(day), "placed_day": day, "placed_time": slot,
        "placed_classroom": room, "pinned": False, "protection": "none",
        "is_online": False, "joint_class_group": "", "sequential": False,
    }


def _app(make_app):
    app = make_app()
    app.state_data.update({
        "days": ["monday", "tuesday"],
        "slots": ["09:00", "10:00"],
        "classrooms": ["R001"], "classroom_capacities": {"R001": 30},
        "lecturers": ["Lect-01"], "lecturer_availability": {},
        "years": {"Y1": ["A"]},
        "classes": [_cls("Ders-1", "monday", "09:00", "R001")],
    })
    app._workflow.state = app.state_data
    app.refresh_grid()
    return app


@pytest.mark.ui
def test_undo_restores_the_grid_axes_not_only_the_lessons(make_app):
    """ST-ARCH-012 — the defect Phase 4 had to withdraw a fix for.

    A failure means undoing a change that removed a day puts the lessons back
    on a day the timetable no longer has: they are `placed` but drawn nowhere,
    which is the ST-DATA-003 orphan state, produced by the Undo button.
    """
    app = _app(make_app)
    app._push_undo("setup")

    # A Setup-shaped change: the axis and the placements move together.
    app.state_data["days"] = ["tuesday"]
    app.state_data["slots"] = ["10:00"]
    app.state_data["classes"][0]["placed_day"] = "tuesday"
    app.state_data["classes"][0]["placed_time"] = "10:00"
    app.refresh_grid()

    app.undo()

    assert app.state_data["days"] == ["monday", "tuesday"], (
        "undo restored the lessons but not the days they sit on")
    assert app.state_data["slots"] == ["09:00", "10:00"]
    lesson = app.state_data["classes"][0]
    assert lesson["placed_day"] in app.state_data["days"], (
        "the restored lesson is on %r, which is not a day of the restored "
        "grid -- an orphan created by Undo" % lesson["placed_day"])
    assert lesson["placed_time"] in app.state_data["slots"]


@pytest.mark.ui
def test_undo_does_not_break_the_workflows_view_of_the_state(make_app):
    """ST-ARCH-012 — the alias hazard, which raises nothing when it bites.

    A failure means the timetable and the constraint validator disagree about
    what the schedule *is*: the grid paints the undone state while every drop
    check, conflict sweep and export answers about the state before the undo.
    """
    app = _app(make_app)
    app._push_undo("edit")
    app.state_data["classes"].append(_cls("Ders-2"))
    app.refresh_grid()

    app.undo()

    assert app._workflow.state is app.state_data, (
        "undo rebound state_data, so SchedulingWorkflow still points at the "
        "pre-undo dict and the app now holds two different schedules")
    assert len(app._workflow.state["classes"]) == 1, (
        "the workflow sees %d classes, the window sees %d"
        % (len(app._workflow.state["classes"]),
           len(app.state_data["classes"])))


@pytest.mark.ui
def test_redo_puts_back_everything_undo_took(make_app):
    """ST-ARCH-012 — redo must be the same width as undo.

    A widened undo with a classes-only redo would restore the axes and then
    fail to move them forward again, stranding the user one step from where
    they were with no way back.
    """
    app = _app(make_app)
    app._push_undo("setup")
    app.state_data["days"] = ["tuesday"]
    app.state_data["classes"].append(_cls("Ders-2"))
    app.refresh_grid()

    app.undo()
    assert app.state_data["days"] == ["monday", "tuesday"]
    assert len(app.state_data["classes"]) == 1

    app.redo()
    assert app.state_data["days"] == ["tuesday"], (
        "redo restored the classes but not the axes")
    assert len(app.state_data["classes"]) == 2
    assert app._workflow.state is app.state_data


@pytest.mark.ui
def test_the_snapshot_is_a_copy_not_a_view(make_app):
    """ST-ARCH-012 — anti-vacuity: a shallow snapshot passes the tests above.

    ``dict(state)`` would satisfy "the days came back" for a *replaced* list
    while sharing every class dict with the live state, so an in-place edit to
    a lesson -- which is what a drag does -- would be unrecoverable. Mutate
    through the live state and check the snapshot did not follow.
    """
    app = _app(make_app)
    app._push_undo("edit")
    _label, snapshot = app._undo_stack[-1]

    app.state_data["classes"][0]["name"] = "CHANGED"
    app.state_data["classes"][0]["placed_day"] = "tuesday"
    app.state_data["years"]["Y1"].append("B")

    assert snapshot["classes"][0]["name"] == "Ders-1", (
        "the undo snapshot shares its class dicts with the live state; an "
        "edit in place is not recoverable")
    assert snapshot["classes"][0]["placed_day"] == "monday"
    assert snapshot["years"]["Y1"] == ["A"], (
        "the snapshot shares nested state containers with the live state")


@pytest.mark.ui
def test_a_cancelled_setup_leaves_the_history_alone(make_app):
    """ST-ARCH-012 — the third thing Phase 4's version got wrong.

    It pushed the snapshot BEFORE the dialog and popped it on cancel, which
    cleared the redo stack for a Setup the user backed out of, and at the
    50-entry cap evicted an undo step that popping could not put back. The
    snapshot is now taken only when the dialog is accepted.
    """
    import inspect
    from scheduler_app.ui.app import SchedulerApp

    src = inspect.getsource(SchedulerApp.edit_setup)
    before = src.index("dlg.exec()")
    pushed = src.index("self._undo_stack.append(")
    assert pushed > before, (
        "edit_setup records the undo entry before the dialog runs, so a "
        "cancelled Setup still mutates the history")
    assert "if dlg.result:" in src[:pushed], (
        "the undo entry is not gated on the dialog being accepted")


@pytest.mark.ui
def test_undo_history_is_bounded(make_app):
    """ST-ARCH-012 — the cap must still hold now that entries are wider.

    Each entry is now the whole state. Measured at +2.6-3.8% over the old
    classes-only entries, which is why the cap did not need changing -- but a
    lost cap would now cost proportionally more, so pin it.
    """
    app = _app(make_app)
    for i in range(app._max_undo + 15):
        app._push_undo("step-%d" % i)
    assert len(app._undo_stack) == app._max_undo
    # The OLDEST entries are the ones dropped, not the newest.
    assert app._undo_stack[-1][0] == "step-%d" % (app._max_undo + 14)
