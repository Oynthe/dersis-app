"""Phase 9 · B3 — the shared fold's two halves must not apply opposite policies.

``scheduler_app.i18n.text_fold.fold_text`` is deliberately one rule for every
place the app compares user-typed text (ST-FUNC-012 / ST-UI-020), and both the
Excel importer (``data_io/importer.py::_process_teachers``) and the class form
(``core/workflow.py::SchedulingWorkflow.register_lecturer``) call it. They agree
on *what* collides. They disagree completely on what to do about it:

* the importer **refuses the whole workbook** — ``errors.teacher_names_fold_together``,
  added in Phase 8, naming both spellings, the rule and the row;
* the class form **silently resolves the collision in favour of the existing
  spelling**: ``register_lecturer`` returns the name already on the roster, and
  ``ui/app.py`` (``add_class``, ``_edit_class``, ``_add_class_at``) writes that
  returned string onto ``cls["lecturer"]``. The user typed one teacher's name
  and the lesson is handed to a different human, with no dialog, no toast and
  no log line.

That is not a hypothetical pair of names. The fold sends every dotted and
dotless I to a plain ASCII ``i``, so two real and unrelated Turkish given names
collide::

    fold_text("Ilgın") == fold_text("İlgin") == "ilgin"
    fold_text("Sıla")  == fold_text("Sila")  == "sila"

Both halves are measured here on the same input:

``test_the_importer_refuses_two_teachers_whose_names_fold_together``
    passes today — it is the loud half, pinned so the asymmetry is a measured
    fact rather than a claim in a docstring.

``test_the_class_form_does_not_silently_hand_the_lesson_to_another_teacher``
    fails today — it is the silent half.

The second test asserts the **observable outcome**, not an API: the lesson may
not end up pointing at a different teacher than the one the user typed *unless
the user was told*. Any of "refuse", "prompt" or "warn and proceed" satisfies
it, because all three name the existing teacher in something the user can see.
Only silence fails it.
"""
import pytest

from scheduler_app.core.models import new_class
from scheduler_app.i18n.text_fold import fold_text

# "Ilgın"  = I (U+0049) l g ı (U+0131) n   — already on the roster
# "İlgin"  = İ (U+0130) l g i (U+0069) n   — a different person, typed by hand
EXISTING = "Ilgın"
TYPED = "İlgin"


def _assert_the_pair_still_collides():
    """Control: without this, neither test below measures anything.

    Guards two things a later change could quietly break: the literals
    surviving the file's encoding intact, and the fold still merging them.
    """
    assert (EXISTING, TYPED) == ("Ilgın", "İlgin"), (
        "the two spellings were mangled — expected U+0131 in %r and U+0130 in "
        "%r; this probe measures nothing unless they are exactly the dotless "
        "and dotted I" % (EXISTING, TYPED))
    assert EXISTING != TYPED, "the two spellings are the same string"
    assert fold_text(EXISTING) == fold_text(TYPED), (
        "fold_text no longer merges %r and %r, so this pair no longer collides "
        "and this whole file is measuring the wrong input" % (EXISTING, TYPED))


# ── The loud half: the importer refuses the workbook ────────────────────────

@pytest.mark.excel
def test_the_importer_refuses_two_teachers_whose_names_fold_together(tmp_path):
    """The importer treats the collision as fatal, and says so (Phase 8).

    Pinned here, on the same pair the class form is given below, so that the
    asymmetry between the two surfaces is measured rather than asserted. This
    test is expected to PASS: the refusal is the defensible half.
    """
    pytest.importorskip("pandas", reason="the Excel importer needs pandas")
    pytest.importorskip("openpyxl", reason="workbook fixtures need openpyxl")

    from scheduler_app.data_io.importer import load_scheduler_data_from_excel
    # tests/ is on sys.path (see tests/conftest.py); reuse the schema-driven
    # workbook builder rather than hand-rolling a second one that could drift.
    from test_import_roundtrip import build_workbook, klass, messages

    _assert_the_pair_still_collides()

    teachers = [{"teacher_id": "T001", "name": EXISTING},
                {"teacher_id": "T002", "name": TYPED}]
    path = build_workbook(tmp_path / "b3_fold.xlsx", teachers=teachers,
                          classes=[klass("C001")])
    ds = load_scheduler_data_from_excel(path)

    assert ds.report.is_valid is False, (
        "the importer accepted a roster holding both %r and %r; the two fold "
        "together, so one teacher's hours would be lost. report=%r"
        % (EXISTING, TYPED, messages(ds.report)))

    named = [line for line in messages(ds.report)
             if EXISTING in line and TYPED in line]
    assert named, (
        "the importer refused the workbook without naming both spellings, so "
        "the user cannot tell which two rows are meant: %r"
        % (messages(ds.report),))


# ── The silent half: the class form reassigns the lesson ────────────────────

class _Notifications:
    """Every channel the app can tell the user something through, in one list.

    Modelled on ``tests/test_setup_reconcile.py``'s ``_Channels``: the four
    ``QMessageBox`` statics, ``QMessageBox`` *instances*, ``_show_toast`` and
    the warning-log panel. Patching them is also what stops an unpatched modal
    from blocking the whole run under the offscreen platform.

    The fix is free to pick any of these; this probe only cares that the user
    is told something naming the teacher the lesson is about to be handed to.
    """

    def __init__(self):
        self.texts = []

    def _record(self, *args, **kwargs):
        for value in list(args) + list(kwargs.values()):
            if isinstance(value, str) and value.strip():
                self.texts.append(value)

    def install(self, window, monkeypatch):
        from PyQt6.QtWidgets import QDialog, QMessageBox

        from scheduler_app.ui.app import SchedulerApp
        from scheduler_app.ui.tier_enforcement import UpgradeDialog
        from scheduler_app.ui.widgets import WarningLogPanel

        # "Yes"/"Ok" so that a confirm-style fix ("this name already belongs to
        # someone — use them?") proceeds rather than aborting the flow: whether
        # it proceeds is not what is under test, being told is.
        answers = {"information": QMessageBox.StandardButton.Ok,
                   "warning": QMessageBox.StandardButton.Ok,
                   "critical": QMessageBox.StandardButton.Ok,
                   "question": QMessageBox.StandardButton.Yes}
        for name, answer in answers.items():
            def _static(*args, _answer=answer, **kwargs):
                self._record(*args, **kwargs)
                return _answer
            monkeypatch.setattr(QMessageBox, name, staticmethod(_static))

        def _fake_exec(box):
            self._record(box.windowTitle(), box.text(), box.informativeText(),
                         box.detailedText())
            return QMessageBox.StandardButton.Yes.value
        monkeypatch.setattr(QMessageBox, "exec", _fake_exec)
        monkeypatch.setattr(UpgradeDialog, "exec",
                            lambda self: QDialog.DialogCode.Rejected.value)

        # Recorded, not called through: the real toast arms a 3 s QTimer that
        # would outlive this test (tests/test_refresh_cost.py).
        def _toast(win, message, kind="info"):
            self._record(message)
        monkeypatch.setattr(SchedulerApp, "_show_toast", _toast)

        real_log = WarningLogPanel.log

        def _log(panel, message, kind="info"):
            self._record(message)
            return real_log(panel, message, kind)
        monkeypatch.setattr(WarningLogPanel, "log", _log)
        return self

    def naming(self, needle):
        return [t for t in self.texts if needle in t]


@pytest.mark.ui
def test_the_class_form_does_not_silently_hand_the_lesson_to_another_teacher(
        qapp, dersis_home, make_app, monkeypatch):
    """B3 — the two surfaces must apply the same policy to the same collision.

    With *Ilgın* already on the roster, a user adding a class for *İlgin* — a
    different teacher whose name differs only by the dotted/dotless I — has the
    lesson silently reassigned to Ilgın. ``register_lecturer`` returns the
    existing spelling on a fold match and ``add_class`` writes it straight onto
    the class, so afterwards:

    * the timetable, every export and every report say the lesson is Ilgın's;
    * it is booked against *Ilgın's* availability, not İlgin's, so İlgin can be
      scheduled at an hour they said they cannot teach;
    * İlgin never appears in ``state["lecturers"]`` at all;
    * nothing was shown, so the user has no reason to look.

    The same two spellings make the Excel importer refuse the entire workbook.
    One of those two policies is wrong, and it is not the loud one.

    Asserted on the observable outcome rather than on any new API: the lesson
    must not point at a teacher other than the one typed *unless the user was
    told*. "Refuse", "prompt" and "warn and proceed" all pass. Silence does not.
    """
    from scheduler_app.ui import app as app_module

    _assert_the_pair_still_collides()

    window = make_app()
    notes = _Notifications().install(window, monkeypatch)

    # Mutated in place: ``SchedulerApp.__init__`` binds ``self._workflow`` to
    # this exact dict, so rebinding ``state_data`` would leave the scheduler
    # working on a different state than the one asserted on.
    state = window.state_data
    state["days"] = ["monday"]
    state["slots"] = ["09:00"]
    state["classrooms"] = ["R001"]
    state["classroom_capacities"] = {"R001": 30}
    state["lecturers"] = [EXISTING]
    state["lecturer_availability"] = {}
    state["years"] = {"Y1": ["A"]}
    state["classes"] = []

    typed_class = new_class()
    typed_class.update(name="Fizik", lecturer=TYPED, duration=1,
                       participants=10,
                       targets=[{"year": "Y1", "branch": "A"}])

    class _StubDialog:
        """``AddClassDialog`` is modal; what happens *after* OK is under test."""

        DialogCode = app_module.AddClassDialog.DialogCode

        def __init__(self, *args, **kwargs):
            self.result = typed_class

        def exec(self):
            return self.DialogCode.Accepted

    monkeypatch.setattr(app_module, "AddClassDialog", _StubDialog)

    window.add_class()

    added = list(state["classes"])
    assigned = added[0].get("lecturer") if added else None
    told = notes.naming(EXISTING)

    assert added or told, (
        "no class was added and nothing was shown, so this run never reached "
        "the collision at all (a tier gate or a missing state field blocked "
        "the flow); the probe is measuring nothing. messages=%r"
        % (notes.texts,))

    assert not (assigned == EXISTING and not told), (
        "the class form silently reassigned the lesson to another teacher.\n"
        "  typed by the user : %r\n"
        "  lesson ends up on : %r\n"
        "  state['lecturers'] : %r\n"
        "  shown to the user  : %r\n"
        "Nothing naming %r reached any channel — no message box, no toast, no "
        "warning-log line — so the user has no way to know the lesson is now "
        "%s's. The very same two spellings make the Excel importer refuse the "
        "whole workbook (errors.teacher_names_fold_together), so the two "
        "surfaces sharing one fold apply opposite policies to one collision."
        % (TYPED, assigned, state["lecturers"], notes.texts, EXISTING,
           EXISTING))
