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


# ===========================================================================
# The prompt's own preconditions — everything the guard promises to be quiet
# about. Added after an adversarial round found the first three of these
# false. Before Phase 9's remediation the whole new API surface
# (find_lecturer_collision, register_lecturer's collision half,
# _same_name_other_case) was reachable only through the one end-to-end probe
# above, which drives `add_class` with a one-name roster; four mutations of
# the decision line survived it.
# ===========================================================================

# "İlgin" written in NFD — LATIN CAPITAL LETTER I followed by COMBINING DOT
# ABOVE. It renders identically to TYPED on screen and is what a macOS
# clipboard hands over (this repo ships Dersis-mac.spec), so it reaches the
# lecturer combo through ordinary use, not through a fuzzer.
TYPED_NFD = "İlgin"

# Two spellings of one plain-ASCII name. `_turkish_fold_case` maps every
# capital I to a dotless ı, so "Yilmaz" and "YILMAZ" land on "yilmaz" and
# "yılmaz" — different strings, measured. That is correct Turkish casing and
# the wrong answer to the question the guard asks, which is only "is this
# difference worth interrupting a human over?".
ASCII_LISTED = "Ayse Yilmaz"
ASCII_SHOUTED = "AYSE YILMAZ"


def _roster(*names):
    return {"lecturers": list(names)}


@pytest.mark.parametrize("roster,typed,why", [
    ((EXISTING,), EXISTING, "the exact spelling, and the only entry"),
    ((EXISTING, TYPED), TYPED, "the exact spelling, second on the roster"),
    ((TYPED, EXISTING), EXISTING, "the exact spelling, second on the roster"),
    (("Sıla", "Sila"), "Sila", "the other real pair, exact, second"),
    ((TYPED,), "İLGİN", "a listed teacher re-typed in Turkish capitals"),
    ((ASCII_LISTED,), ASCII_SHOUTED, "a listed teacher shouted in ASCII"),
    ((TYPED,), TYPED_NFD, "the same name pasted from an NFD source"),
    ((TYPED_NFD,), TYPED, "an NFD name on the roster, typed as NFC"),
    ((EXISTING,), "", "blank"),
    ((EXISTING,), None, "unset"),
    ((EXISTING,), "Bülent Çınar", "nothing on the roster folds onto it"),
])
def test_the_form_keeps_quiet_when_it_has_nothing_to_say(roster, typed, why):
    """B3 — every case ``find_lecturer_collision`` documents as silent.

    A prompt that fires on the harmless cases is a prompt users learn to click
    through before it ever reaches the harmful one, so each row here is load
    bearing for the ONE row that matters
    (``test_a_genuine_collision_is_still_reported``). Three of them were
    measured false on the shipped fix:

    * the exact spelling second on the roster — the loop returned at the FIRST
      fold match and never saw it, so ``('Ilgın', 'İlgin')`` / ``'İlgin'``
      reported ``'Ilgın'`` where the docstring promised None. Reachable
      through ordinary use: ``SetupDialog._ok`` writes ``state["lecturers"]``
      from its table with no fold check and no dedup, so a school with both
      teachers has both;
    * the ASCII shout — ``'Ayse Yilmaz'`` / ``'AYSE YILMAZ'`` reported
      ``'Ayse Yilmaz'``, which is the exact case the second identity rule was
      added to keep quiet;
    * NFD — ``'İlgin'`` / ``'İlgin'`` reported ``'İlgin'``, a modal
      quoting two strings that look the same on screen.

    Liveness, not just noise: a prompt here has nothing patching QMessageBox
    in front of it under the offscreen platform, so a run that reaches one
    HANGS rather than fails. Measured — a mutation that made the exact match
    prompt blocked ``tests/test_ui_affordances.py`` past a 150 s timeout.
    """
    from scheduler_app.core.workflow import SchedulingWorkflow

    got = SchedulingWorkflow.find_lecturer_collision(_roster(*roster), typed)
    assert got is None, (
        "the class form raised the collision prompt on a case it documents as "
        "silent (%s): roster=%r typed=%r reported=%r" % (why, roster, typed, got))


@pytest.mark.parametrize("roster,typed,expected", [
    ((EXISTING,), TYPED, EXISTING),
    ((TYPED,), EXISTING, TYPED),
    (("Sıla",), "Sila", "Sıla"),
    ((EXISTING, "Bülent Çınar"), TYPED, EXISTING),
])
def test_a_genuine_collision_is_still_reported(roster, typed, expected):
    """The other half: silence must not have been bought by saying nothing.

    Without this every row above is satisfiable by ``return None``, which is
    exactly the mutation that killed the end-to-end probe and nothing else.
    """
    from scheduler_app.core.workflow import SchedulingWorkflow

    got = SchedulingWorkflow.find_lecturer_collision(_roster(*roster), typed)
    assert got == expected, (
        "roster=%r typed=%r: expected the prompt to name %r, got %r"
        % (roster, typed, expected, got))


@pytest.mark.parametrize("roster,typed", [
    ((EXISTING, TYPED), TYPED),
    ((TYPED, EXISTING), EXISTING),
    (("Sıla", "Sila"), "Sila"),
    ((EXISTING,), TYPED),
    ((EXISTING,), EXISTING),
    ((TYPED,), "İLGİN"),
    ((ASCII_LISTED,), ASCII_SHOUTED),
    ((TYPED,), TYPED_NFD),
    ((), "Bülent Çınar"),
    ((EXISTING, TYPED), "İLGİN"),
])
def test_silence_means_the_lesson_goes_to_the_teacher_who_was_typed(roster, typed):
    """The invariant that makes the two functions one decision, not two.

    ``find_lecturer_collision`` only ever answers a question about what
    ``register_lecturer`` is *about to do*, and the caller runs them back to
    back on the same state. Two separate loops over the same roster is how the
    shipped fix drifted: silencing the exact-match case without touching
    ``register_lecturer`` would have turned a wrong-but-loud outcome into a
    wrong-and-silent one — measured on the shipped tree, roster
    ``['Ilgın', 'İlgin']`` and typed ``'İlgin'`` gave ``collision='Ilgın'``
    *and* ``register_lecturer -> 'Ilgın'``, so the teacher who is on the
    roster, with their own availability record, could not be given a class at
    all.

    So: whatever the prompt names must be the name that is actually assigned,
    and saying nothing must mean the assignment is the same human the user
    typed — not merely that nobody was told.
    """
    from scheduler_app.core.workflow import (
        SchedulingWorkflow, _same_name_other_case)

    collision = SchedulingWorkflow.find_lecturer_collision(_roster(*roster), typed)
    state = _roster(*roster)
    assigned = SchedulingWorkflow.register_lecturer(state, typed)

    if collision is not None:
        assert collision == assigned, (
            "the prompt names %r but the lesson is written to %r, so the "
            "question the user answered was about a different teacher "
            "(roster=%r typed=%r)" % (collision, assigned, roster, typed))
        return

    assert assigned == typed or _same_name_other_case(assigned, typed), (
        "nothing was said, and the lesson still went to a different spelling: "
        "roster=%r typed=%r assigned=%r. Silence is only honest when the "
        "assignment is the same teacher, in the same name, differing at most "
        "in case." % (roster, typed, assigned))


# ===========================================================================
# "No" must give the form back — the regression Phase 9's own fix introduced
# ===========================================================================

def _install_class_form(monkeypatch, script):
    """Replace the modal ``AddClassDialog`` with a scripted stand-in.

    Returns the ``seeds`` list: one entry per showing, holding the ``edit_cls``
    the form was constructed with. That list *is* the finding — a second entry
    carrying the user's data is the form coming back, and no second entry at
    all is the form being thrown away.

    Runs out loudly rather than quietly: a re-open loop with no exit would
    otherwise spin forever under the offscreen platform, and a hung job says
    much less than a failed one.
    """
    from scheduler_app.ui import app as app_module

    seeds = []
    pending = list(script)

    class _Form:
        DialogCode = app_module.AddClassDialog.DialogCode

        def __init__(self, parent, state, edit_cls=None):
            seeds.append(edit_cls)
            assert pending, (
                "the class form has been shown %d times and the script holds "
                "%d results — nothing is ending the re-open loop"
                % (len(seeds), len(script)))
            self.result = pending.pop(0)

        def exec(self):
            return self.DialogCode.Accepted

    monkeypatch.setattr(app_module, "AddClassDialog", _Form)
    return seeds


def _install_answers(monkeypatch, answers):
    """Script ``QMessageBox.question``; return the list of bodies it was given.

    An empty script means "nothing may ask anything", and asserts rather than
    defaulting, so a prompt that should not have fired names itself instead of
    silently taking Yes.
    """
    from PyQt6.QtWidgets import QMessageBox

    asked = []
    pending = list(answers)

    def _question(parent, title, text, *args, **kwargs):
        asked.append(text)
        assert pending, (
            "a modal question fired that this test did not script (%d so far, "
            "%d scripted); last body: %r" % (len(asked), len(answers), text))
        return (QMessageBox.StandardButton.Yes if pending.pop(0)
                else QMessageBox.StandardButton.No)

    monkeypatch.setattr(QMessageBox, "question", staticmethod(_question))
    return asked


def _quiet(window, monkeypatch):
    """Silence every channel that is not under test in this section.

    ``_show_toast`` arms a 3 s QTimer that would outlive the test
    (tests/test_refresh_cost.py), and ``_run_impact_analysis`` raises a
    ``QMessageBox.question`` of its own — scripting that one alongside the
    collision prompt would make the prompt count measure two things.
    """
    from PyQt6.QtWidgets import QDialog

    from scheduler_app.ui.app import SchedulerApp
    from scheduler_app.ui.tier_enforcement import UpgradeDialog

    monkeypatch.setattr(SchedulerApp, "_show_toast", lambda *a, **kw: None)
    monkeypatch.setattr(SchedulerApp, "_run_impact_analysis",
                        lambda *a, **kw: None)
    monkeypatch.setattr(UpgradeDialog, "exec",
                        lambda self: QDialog.DialogCode.Rejected.value)


def _window_with(make_app, roster, classes=()):
    window = make_app()
    state = window.state_data
    state["days"] = ["monday"]
    state["slots"] = ["09:00"]
    state["classrooms"] = ["R001"]
    state["classroom_capacities"] = {"R001": 30}
    state["lecturers"] = list(roster)
    state["lecturer_availability"] = {}
    state["years"] = {"Y1": ["A"]}
    state["classes"] = list(classes)
    return window, state


def _typed_class(**over):
    cls = new_class()
    cls.update(name="Fizik", lecturer=EXISTING, duration=1, participants=10,
               targets=[{"year": "Y1", "branch": "A"}])
    cls.update(over)
    return cls


@pytest.mark.ui
def test_declining_the_collision_prompt_gives_the_form_back(
        qapp, dersis_home, make_app, monkeypatch):
    """B3 regression — answering the question honestly destroyed the entry.

    The shipped message ends "Choose No to go back and give the new teacher a
    name that differs by more than that" (all 22 locales). Nothing went back:
    all three call sites were a bare ``return`` executed AFTER
    ``AddClassDialog`` had already closed. Measured on the shipped tree —
    ``add_class`` + No gave ``classes == []``, ``toasts == []`` and undo depth
    0, so the class name, targets, duration, participant count and room
    constraints the user had just typed were gone with nothing to undo.

    Before B3 no path did this, because there was no prompt: the measured case
    (Yes) improved and its unmeasured neighbour degraded silently.

    Pinned on what the user can see, not on the mechanism: the form is shown
    again, seeded with everything they entered.
    """
    _assert_the_pair_still_collides()

    window, state = _window_with(make_app, [EXISTING])
    _quiet(window, monkeypatch)

    typed = _typed_class(lecturer=TYPED, participants=27)
    # A name that shares no fold with the roster, so the second showing is
    # accepted without a second prompt.
    corrected = _typed_class(lecturer="Bülent Çınar", participants=27)
    seeds = _install_class_form(monkeypatch, [typed, corrected])
    asked = _install_answers(monkeypatch, [False])

    window.add_class()

    assert len(asked) == 1, "the collision prompt did not fire: %r" % (asked,)
    assert len(seeds) == 2, (
        "the form was shown %d time(s). Answering No discarded everything the "
        "user typed instead of giving the form back, which is what the "
        "message in all 22 locales promises. classes=%r"
        % (len(seeds), [c["name"] for c in state["classes"]]))

    back = seeds[1]
    assert back is not None, "the form came back empty"
    assert (back.get("name"), back.get("participants"), back.get("lecturer")) \
        == ("Fizik", 27, TYPED), (
        "the form came back without the user's entry: name=%r participants=%r "
        "lecturer=%r" % (back.get("name"), back.get("participants"),
                         back.get("lecturer")))

    assert [c["name"] for c in state["classes"]] == ["Fizik"], (
        "the corrected class was not added: %r"
        % ([c["name"] for c in state["classes"]],))
    assert state["classes"][0]["lecturer"] == "Bülent Çınar"


@pytest.mark.ui
def test_declining_on_the_edit_path_does_not_discard_the_edit(
        qapp, dersis_home, make_app, monkeypatch):
    """The same regression on ``_edit_class``, where there is more to lose.

    Measured on the shipped tree: with the user's edit changing the name from
    'Fizik' to 'Fizik II' and the participant count from 10 to 27, answering
    No left the class at 'Fizik'/10 with ``toasts == []`` and undo depth 0 —
    an edit silently rolled back with no way to see or recover it.
    """
    _assert_the_pair_still_collides()

    cls = _typed_class()
    window, state = _window_with(make_app, [EXISTING], [cls])
    _quiet(window, monkeypatch)

    edited = _typed_class(name="Fizik II", participants=27, lecturer=TYPED)
    kept = _typed_class(name="Fizik II", participants=27, lecturer=EXISTING)
    seeds = _install_class_form(monkeypatch, [edited, kept])
    asked = _install_answers(monkeypatch, [False])

    window._edit_class(cls)

    assert len(asked) == 1, "the collision prompt did not fire: %r" % (asked,)
    assert len(seeds) == 2, (
        "the edit form was shown %d time(s); No threw the edit away rather "
        "than giving it back" % len(seeds))
    assert (seeds[1].get("name"), seeds[1].get("participants")) \
        == ("Fizik II", 27), (
        "the edit form came back without the user's changes: %r"
        % ((seeds[1].get("name"), seeds[1].get("participants")),))
    assert (cls["name"], cls["participants"]) == ("Fizik II", 27), (
        "the user's edit was lost: %r" % ((cls["name"], cls["participants"]),))


@pytest.mark.ui
def test_an_edit_that_never_touched_the_lecturer_asks_nothing(
        qapp, dersis_home, make_app, monkeypatch):
    """B3 regression — a modal about a field the user did not open the form for.

    Measured on the shipped tree: roster ``['Ilgın', 'İlgin']``, a class
    already on 'İlgin', the user changes ONLY the participant count 10 -> 11 —
    and the collision prompt fires. Yes then rewrote the lecturer to 'Ilgın',
    and (see above) No discarded the edit entirely.

    Two independent things now stop it and both are pinned here: 'İlgin' is on
    the roster exactly, so there is nothing to report; and an edit that leaves
    the lecturer string as it found it does not get to raise a question about
    the lecturer at all. Same shape as B4's "the dialog erases its own
    evidence", on a different field.
    """
    _assert_the_pair_still_collides()

    cls = _typed_class(lecturer=TYPED)
    window, state = _window_with(make_app, [EXISTING, TYPED], [cls])
    _quiet(window, monkeypatch)

    seeds = _install_class_form(
        monkeypatch, [_typed_class(lecturer=TYPED, participants=11)])
    asked = _install_answers(monkeypatch, [])

    window._edit_class(cls)

    assert asked == [], (
        "changing the participant count raised a modal about the lecturer: %r"
        % (asked,))
    assert len(seeds) == 1, "the form was re-opened for no reason"
    assert cls["lecturer"] == TYPED, (
        "an edit that never touched the lecturer field rewrote it from %r to "
        "%r" % (TYPED, cls["lecturer"]))
    assert cls["participants"] == 11, "the edit did not apply"
