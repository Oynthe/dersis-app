"""The form-UX row, triaged — ST-UI-016…020.

Phase 5 triaged this row and found most of it already false: the 33-step
tutorial does **not** fire over a modal, and the language switch is **not** a
flag-only menu entry but a titled one. Half of ST-UI-019 was closed by Phase 2's
sticky/derived split. Those verdicts were re-checked in Phase 6 and hold.

Three items were live. Two of them are here; the third — the toolbar's hidden
dropdown caret — is a stylesheet line with no assertable behaviour, and is
covered by the commit rather than by a test.

ST-UI-020 · the typed lecturer (the damaging one)
--------------------------------------------------
``AddClassDialog``'s lecturer combo is editable, which invites typing a name
that is not yet in ``state["lecturers"]`` — the obvious thing to do when adding
a class for a teacher who is not on the list. Nothing registered it, and the
consequences were both invisible and delayed:

* ``SchedulingWorkflow.reconcile_placements`` treats a lecturer absent from the
  list exactly as it treats a **deleted** one, so the next Setup OK unplaces the
  lesson and reports "N placements cleared" — attributed to whatever the user
  just changed in Setup, not to a name they typed hours earlier;
* lecturer availability is keyed on ``state["lecturer_availability"]``, and no
  UI can create a record for a name that is not in the list, so that teacher's
  unavailable hours silently never applied;
* until then the class was drawn and counted normally, so nothing looked wrong.

ST-UI-018 · one error at a time
--------------------------------
``validate_class_fields`` has always returned a *list*, and the dialog showed
``errors[0]``. A form with three mistakes therefore took three OK presses to
learn about all three.

What is deliberately NOT fixed here, and why
---------------------------------------------
The register's own remark that "AddClassDialog validates before the constraint
checkboxes are read, so contradictory allow/exclude sets are never checked" is
half right and its fix is a no-op: the ordering is real, but
``validate_class_fields`` has no contradiction check at all, so moving the reads
above the call changes nothing. Contradictions *are* detected — by
``ConstraintNegotiator``, after the class is committed, in a collapsed log line.
Moving that judgement into the validator is a behaviour change with its own
design questions and belongs in its own pass.
"""
import pytest

from scheduler_app.core.models import new_class, new_state
from scheduler_app.core.workflow import SchedulingWorkflow


def _state():
    state = new_state()
    state["days"] = ["monday"]
    state["slots"] = ["09:00"]
    state["classrooms"] = ["R001"]
    state["classroom_capacities"] = {"R001": 30}
    state["lecturers"] = ["Ayşe Yılmaz"]
    state["years"] = {"Y1": ["A"]}
    return state


def _placed(state, lecturer):
    cls = new_class()
    cls["name"] = "Matematik"
    cls["lecturer"] = lecturer
    cls["targets"] = [{"year": "Y1", "branch": "A"}]
    cls["placed"] = True
    cls["placed_day"] = "monday"
    cls["placed_time"] = "09:00"
    cls["placed_classroom"] = "R001"
    state["classes"].append(cls)
    return cls


def test_a_typed_lecturer_becomes_a_real_lecturer():
    """ST-UI-020 — registering is what stops the delayed unplacement."""
    state = _state()
    canonical = SchedulingWorkflow.register_lecturer(state, "  Bülent Çınar ")
    assert canonical == "Bülent Çınar"
    assert "Bülent Çınar" in state["lecturers"]


def test_registering_does_not_duplicate_an_existing_lecturer():
    """ST-UI-020 — case and spacing must not split one teacher into two.

    A second entry differing only in case would let the two drift: availability
    is keyed by the exact string, so the class would point at the copy with no
    record and the teacher's unavailable hours would still not apply.
    """
    state = _state()
    got = SchedulingWorkflow.register_lecturer(state, "ayşe yılmaz")
    assert got == "Ayşe Yılmaz", (
        "expected the existing spelling back, got %r" % got)
    assert state["lecturers"] == ["Ayşe Yılmaz"], state["lecturers"]


def test_a_blank_lecturer_stays_unassigned():
    """ST-UI-020 — blank means "not staffed yet", not a lecturer named ''.

    ``new_class()`` ships ``lecturer: ""`` and the core reads blank as "no
    lecturer constraint"; registering it would put "" in the list and make
    every unstaffed lesson look like it belonged to a teacher.
    """
    state = _state()
    assert SchedulingWorkflow.register_lecturer(state, "") is None
    assert SchedulingWorkflow.register_lecturer(state, "   ") is None
    assert state["lecturers"] == ["Ayşe Yılmaz"]


def test_an_unregistered_lecturer_is_what_costs_the_placement():
    """ST-UI-020 — the mechanism, so the fix above is visibly load-bearing.

    This is the damage, reproduced directly: reconcile_placements cannot tell a
    typed-but-unregistered lecturer from a deleted one.
    """
    state = _state()
    typed = _placed(state, "Bülent Çınar")      # never registered
    known = _placed(state, "Ayşe Yılmaz")

    affected = SchedulingWorkflow.reconcile_placements(state)

    assert typed in affected and not typed["placed"], (
        "the lesson with an unregistered lecturer survived; this test can no "
        "longer show what registering prevents")
    assert known["placed"], "the registered lecturer's lesson was unplaced too"


def test_registering_first_saves_the_placement():
    """ST-UI-020 — and with the registrar in front, the lesson survives."""
    state = _state()
    name = SchedulingWorkflow.register_lecturer(state, "Bülent Çınar")
    cls = _placed(state, name)

    affected = SchedulingWorkflow.reconcile_placements(state)

    assert cls not in affected and cls["placed"], (
        "the lesson was unplaced even though its lecturer is registered")


@pytest.mark.ui
def test_the_add_class_paths_register_before_snapshotting():
    """ST-UI-020 — order matters, and it is not visible from behaviour.

    Registering AFTER ``_push_undo`` would leave the undo snapshot holding a
    state whose lecturer list lacks the typed name, so undoing an unrelated
    later action would re-create the original defect -- a class pointing at a
    lecturer the restored state does not list.
    """
    import inspect
    import re

    from scheduler_app.ui.app import SchedulerApp

    # Every spelling of "take the snapshot", and the search runs over CODE
    # ONLY. Both halves are load-bearing and both were learned here:
    #
    #  * Phase 10 replaced `add_class`'s `_push_undo(...)` with a held
    #    `copy.deepcopy` committed by `_commit_undo_entry` on a state
    #    comparison (B1/B2, third site), so a single literal `_push_undo`
    #    no longer describes both methods. `_edit_class` still uses it.
    #  * With comments included, this assertion passed on the Phase 10 tree
    #    for the worst possible reason: the new code carries a COMMENT
    #    explaining why `_push_undo` was removed, `str.index` found the word
    #    there, and the test went green while measuring prose. Stripping
    #    comments is what keeps it looking at the program.
    snapshot_markers = ("_push_undo", "copy.deepcopy(self.state_data)",
                        "_commit_undo_entry(")

    def _code_only(src):
        """*src* with `#` comments and docstrings blanked, offsets preserved.

        Blanked rather than deleted so every index below still refers to the
        same character position in the original source.
        """
        out = re.sub(r'(?m)#[^\n]*', lambda m: " " * len(m.group(0)), src)
        return re.sub(r'(?s)("""|\x27\x27\x27).*?\1',
                      lambda m: " " * len(m.group(0)), out)

    for method in (SchedulerApp.add_class, SchedulerApp._edit_class):
        src = _code_only(inspect.getsource(method))
        assert "register_lecturer" in src, (
            "%s does not register the typed lecturer" % method.__name__)
        present = [m for m in snapshot_markers if m in src]
        assert present, (
            "%s takes no undo snapshot by any spelling this test knows about; "
            "it looked for %r. If the call was renamed again, add the new name "
            "here rather than deleting the test." % (method.__name__,
                                                     snapshot_markers))
        assert src.index("register_lecturer") < min(src.index(m)
                                                    for m in present), (
            "%s registers the lecturer after taking the undo snapshot, so "
            "undo can restore a state that does not know the name"
            % method.__name__)


@pytest.mark.ui
def test_the_class_form_reports_every_mistake_at_once():
    """ST-UI-018 — a form with three errors must not need three attempts.

    A failure means the user fixes the one field they were told about, presses
    OK, and is told about the next one -- as many rounds as they made mistakes,
    with the validator having known all of them the whole time.
    """
    import inspect
    from scheduler_app.ui import dialogs

    src = inspect.getsource(dialogs.AddClassDialog)
    assert "errors[0])" not in src.replace(" ", ""), (
        "AddClassDialog still shows only the first validation error")
    assert "for e in errors" in src, (
        "AddClassDialog does not render the full error list")


def test_the_validator_really_returns_more_than_one_error():
    """ST-UI-018 — anti-vacuity for the test above.

    If ``validate_class_fields`` only ever produced one error, showing "all of
    them" would be a distinction without a difference and the guard above would
    describe nothing.
    """
    from scheduler_app.core.models import validate_class_fields

    broken = new_class()
    broken["name"] = ""
    broken["duration"] = 0
    broken["targets"] = []
    errors = validate_class_fields(broken)
    assert len(errors) >= 2, (
        "validate_class_fields returned %d error(s) for a class with a blank "
        "name, zero duration and no targets: %r" % (len(errors), errors))
