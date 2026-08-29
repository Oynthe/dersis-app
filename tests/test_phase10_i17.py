"""Phase 10 · Item 17 — the Add Class form calls itself "Edit Class" on its
second showing, while the user is still adding.

Phase 9's B3 fix gave the lecturer-collision prompt a real "No": ``ui/app.py``
``_class_form_result`` re-shows the form seeded with everything the user typed
instead of throwing it away. The only channel ``AddClassDialog`` has for a seed
is ``edit_cls=``, and its caption is derived from exactly that::

    scheduler_app/ui/dialogs.py::AddClassDialog.__init__
    title = ("✎  " + tr("dialogs.edit_class.title")) if edit_cls \\
            else ("➕  " + tr("toolbar.add_single"))

So the second showing of the ADD form is captioned with the EDIT title. Every
field is right; only the caption lies, and only on the add path's second
showing — which is the moment the user is least sure what the app just did with
their entry, because they have only ever answered a question about a teacher.

What this module measures
-------------------------
``test_the_add_form_is_still_called_add_after_the_collision_prompt``
    RED today. Drives ``SchedulerApp.add_class`` -> the real
    ``_class_form_result`` -> the real ``AddClassDialog.__init__``, with a
    lecturer name that folds onto a teacher already on the roster so the prompt
    fires, and No as the answer. Nothing is planted: ``exec`` is replaced with a
    stand-in that fills the dialog's own widgets and calls the dialog's own
    ``_ok``, so the class dict that becomes the seed is built by production
    code, and the caption read back is the one production code set.

``test_the_edit_form_is_still_called_edit_after_the_collision_prompt``
    GREEN today, and the reason a naive fix is a new defect. The same loop
    serves ``_edit_class``, where "Edit Class" is CORRECT on both showings. Any
    fix that keys off "is this the second showing?" rather than "which path am
    I on?" turns this green test red.

``test_the_add_form_is_called_add_when_nothing_collides``
    GREEN today. The single-showing add path, so the failure above cannot be
    read as "the add caption was never right".

``test_the_caption_is_set_once_in_init_and_survives_the_dialog_being_used``
    GREEN today, and it is the evidence for the *recommended* fix rather than
    the prescribed one. The title is written once, in ``__init__``, and no
    later code path re-derives it — so a caller that calls
    ``dlg.setWindowTitle(...)`` after construction wins, and stays won through
    a full edit-and-OK cycle. That formulation costs **zero** McCabe in both
    god-object modules; the prescribed keyword-only ``title=None`` consumed as
    ``title or (...)`` costs +1 in ``ui/dialogs.py``, which is at 885/885.
"""
import pytest

from PyQt6.QtWidgets import QDialog, QMessageBox

# The two real Turkish given names `fold_text` merges (see tests/test_phase9_b3.py):
# "Ilgın" = I + dotless ı, "İlgin" = dotted İ + i.
EXISTING = "Ilgın"
TYPED = "İlgin"
SAFE = "Bülent Çınar"


def _assert_the_pair_still_collides():
    from scheduler_app.i18n.text_fold import fold_text

    assert (EXISTING, TYPED) == ("Ilgın", "İlgin"), (
        "the two spellings were mangled by this file's encoding: %r / %r"
        % (EXISTING, TYPED))
    assert fold_text(EXISTING) == fold_text(TYPED), (
        "fold_text no longer merges %r and %r, so the collision prompt will "
        "never fire and this module measures nothing" % (EXISTING, TYPED))


def _captions():
    """The two captions ``AddClassDialog.__init__`` can produce, right now.

    Read through ``tr()`` at call time rather than hard-coded: the suite runs
    pinned to Turkish, and a probe that compared against English would pass for
    the wrong reason.
    """
    from scheduler_app.i18n.translations import tr

    return ("➕  " + tr("toolbar.add_single"),
            "✎  " + tr("dialogs.edit_class.title"))


# ── driving the real dialog ─────────────────────────────────────────────────

def _fill(name="Fizik", lecturer=TYPED, participants=27):
    """A step that types into the dialog's OWN widgets, as a user would."""
    def _step(dlg):
        dlg.name_edit.setText(name)
        dlg.lecturer_combo.setCurrentText(lecturer)
        dlg.participants_spin.setValue(participants)
        assert dlg.target_vars, (
            "the form offers no target groups, so validate_class_fields will "
            "reject every OK and the re-show loop never runs")
        first = sorted(dlg.target_vars)[0]
        dlg.target_vars[first].setChecked(True)
    return _step


def _install_real_form(monkeypatch, steps):
    """Replace only ``exec`` — the real ``AddClassDialog`` is what is measured.

    Returns ``titles``: the window caption of every showing, read back off the
    widget AFTER construction (and after anything the caller does to it), which
    is where a caller-side ``setWindowTitle`` fix would land.

    ``_ok`` is the dialog's own accept handler, so ``dlg.result`` — the dict
    that becomes the next showing's ``edit_cls`` seed — is built entirely by
    production code. Nothing here plants a seed or a caption.
    """
    from scheduler_app.ui.dialogs import AddClassDialog

    titles = []
    pending = list(steps)

    def _exec(dlg):
        titles.append(dlg.windowTitle())
        assert pending, (
            "the class form has been shown %d times and the script holds %d "
            "steps — nothing is ending the re-show loop (captions so far: %r)"
            % (len(titles), len(steps), titles))
        pending.pop(0)(dlg)
        dlg._ok()
        assert dlg.result is not None, (
            "the dialog rejected its own input on showing %d, so OK never "
            "went through and the loop cannot advance" % len(titles))
        return AddClassDialog.DialogCode.Accepted

    monkeypatch.setattr(AddClassDialog, "exec", _exec)
    return titles


def _install_answers(monkeypatch, answers):
    """Script ``QMessageBox.question``; an unscripted one asserts rather than
    defaulting, so a prompt that should not have fired names itself."""
    asked = []
    pending = list(answers)

    def _question(parent, title, text, *args, **kwargs):
        asked.append(text)
        assert pending, (
            "an unscripted modal question fired; body: %r" % (text,))
        return (QMessageBox.StandardButton.Yes if pending.pop(0)
                else QMessageBox.StandardButton.No)

    monkeypatch.setattr(QMessageBox, "question", staticmethod(_question))
    return asked


def _quiet(monkeypatch):
    """Silence the channels not under test, and stop any modal blocking.

    ``QMessageBox.critical`` is what ``_ok`` raises on a validation failure; it
    would block forever under the offscreen platform, and recording it is what
    turns "the form rejected the input" into a readable assertion instead of a
    hang. ``_show_toast`` arms a 3 s QTimer that would outlive the test.
    """
    from scheduler_app.ui.app import SchedulerApp
    from scheduler_app.ui.tier_enforcement import UpgradeDialog

    complaints = []

    def _critical(parent, title, text, *args, **kwargs):
        complaints.append(text)
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "critical", staticmethod(_critical))
    monkeypatch.setattr(SchedulerApp, "_show_toast", lambda *a, **kw: None)
    monkeypatch.setattr(SchedulerApp, "_run_impact_analysis",
                        lambda *a, **kw: None)
    monkeypatch.setattr(UpgradeDialog, "exec",
                        lambda self: QDialog.DialogCode.Rejected.value)
    return complaints


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


# ── the defect ──────────────────────────────────────────────────────────────

@pytest.mark.ui
def test_the_add_form_is_still_called_add_after_the_collision_prompt(
        qapp, dersis_home, make_app, monkeypatch):
    """Item 17 — the caption flips to "Edit Class" mid-add.

    The user picks Add Class, types a teacher whose name folds onto one already
    on the roster, is asked whether they are the same person, and answers No.
    The form comes back with everything they typed — and now says it is editing
    a class. Nothing is being edited; there is no class yet.
    """
    _assert_the_pair_still_collides()

    add_caption, edit_caption = _captions()
    window, state = _window_with(make_app, [EXISTING])
    complaints = _quiet(monkeypatch)
    titles = _install_real_form(
        monkeypatch, [_fill(lecturer=TYPED), _fill(lecturer=SAFE)])
    asked = _install_answers(monkeypatch, [False])

    window.add_class()

    assert complaints == [], (
        "the form rejected its own input: %r" % (complaints,))
    assert len(asked) == 1, (
        "the collision prompt did not fire, so the second showing never "
        "happened and this test measures nothing: %r" % (asked,))
    assert len(titles) == 2, (
        "the form was shown %d time(s); B3's re-show loop did not run" % len(titles))
    assert [c["name"] for c in state["classes"]] == ["Fizik"], (
        "the class was not added, so the flow did not complete: %r"
        % ([c["name"] for c in state["classes"]],))

    assert titles[1] == add_caption, (
        "the Add Class form calls itself %r on its second showing.\n"
        "  showing 1 : %r\n"
        "  showing 2 : %r\n"
        "  add caption  : %r\n"
        "  edit caption : %r\n"
        "The user is adding a class — there is no class to edit, and none was "
        "created. The caption is derived in AddClassDialog.__init__ from the "
        "truthiness of `edit_cls`, and `edit_cls` is the only channel "
        "_class_form_result has for handing the user's entry back."
        % (titles[1], titles[0], titles[1], add_caption, edit_caption))


# ── the controls, all green today ───────────────────────────────────────────

@pytest.mark.ui
def test_the_edit_form_is_still_called_edit_after_the_collision_prompt(
        qapp, dersis_home, make_app, monkeypatch):
    """The same loop, the other path — where "Edit Class" is CORRECT.

    ``_edit_class`` shares ``_class_form_result``, so a fix that decides the
    caption by "is this a re-show?" rather than "which path is this?" would
    caption a genuine edit "Add Single" on its second showing. That is a new
    defect of the same shape, and this test is what catches it.
    """
    _assert_the_pair_still_collides()

    from scheduler_app.core.models import new_class

    add_caption, edit_caption = _captions()
    cls = new_class()
    cls.update(name="Fizik", lecturer=EXISTING, duration=1, participants=10,
               targets=[{"year": "Y1", "branch": "A"}])
    window, state = _window_with(make_app, [EXISTING], [cls])
    complaints = _quiet(monkeypatch)
    titles = _install_real_form(
        monkeypatch, [_fill(name="Fizik II", lecturer=TYPED),
                      _fill(name="Fizik II", lecturer=SAFE)])
    asked = _install_answers(monkeypatch, [False])

    window._edit_class(cls)

    assert complaints == [], "the form rejected its own input: %r" % (complaints,)
    assert len(asked) == 1, "the collision prompt did not fire: %r" % (asked,)
    assert len(titles) == 2, (
        "the edit form was shown %d time(s)" % len(titles))
    assert titles == [edit_caption, edit_caption], (
        "an edit was re-captioned. Both showings of the EDIT path must say "
        "%r; got %r. A fix that keys off the re-show rather than the path "
        "trades item 17 for its mirror image." % (edit_caption, titles))


@pytest.mark.ui
def test_the_add_form_is_called_add_when_nothing_collides(
        qapp, dersis_home, make_app, monkeypatch):
    """The add caption is right on a single showing — so the failure above is
    about the RE-showing, not about the add path in general."""
    add_caption, _edit = _captions()
    window, state = _window_with(make_app, [EXISTING])
    complaints = _quiet(monkeypatch)
    titles = _install_real_form(monkeypatch, [_fill(lecturer=SAFE)])
    asked = _install_answers(monkeypatch, [])

    window.add_class()

    assert complaints == [], "the form rejected its own input: %r" % (complaints,)
    assert asked == [], "a prompt fired on a name that collides with nothing: %r" % (asked,)
    assert titles == [add_caption], (
        "the add form's only showing is captioned %r, expected %r"
        % (titles, [add_caption]))


@pytest.mark.ui
def test_the_caption_is_set_once_in_init_and_survives_the_dialog_being_used(
        qapp, dersis_home, make_app, monkeypatch):
    """Evidence for the zero-cost fix: nothing re-derives the caption later.

    ``setWindowTitle`` is called exactly once inside ``AddClassDialog`` — line
    2219, in ``__init__`` — and no handler recomputes it. A caller that
    overrides it immediately after construction therefore wins, and keeps
    winning through a full fill-and-OK cycle, which is what
    ``_class_form_result`` does to the dialog between construction and return.

    That matters because it is the difference between a fix that costs +1 in
    ``ui/dialogs.py`` (885/885, zero headroom, and the file already holds the
    codebase's only F-band function) and one that costs nothing at all.
    """
    from scheduler_app.core.models import new_class
    from scheduler_app.ui.dialogs import AddClassDialog

    add_caption, edit_caption = _captions()
    window, state = _window_with(make_app, [EXISTING])
    _quiet(monkeypatch)

    seed = new_class()
    seed.update(name="Fizik", lecturer=EXISTING, duration=1, participants=27,
                targets=[{"year": "Y1", "branch": "A"}])

    # 1. The derivation is purely `edit_cls` truthiness — this is the defect's
    #    mechanism, stated as a measurement.
    assert AddClassDialog(window, state, edit_cls={}).windowTitle() == add_caption
    assert AddClassDialog(window, state, edit_cls=None).windowTitle() == add_caption
    seeded = AddClassDialog(window, state, edit_cls=seed)
    assert seeded.windowTitle() == edit_caption, (
        "a seeded form is captioned %r, not %r — the mechanism this item "
        "describes is not the one in the code"
        % (seeded.windowTitle(), edit_caption))

    # 2. A caller-side override sticks, through the whole life of the dialog.
    seeded.setWindowTitle(add_caption)
    _fill(lecturer=SAFE)(seeded)
    seeded._ok()
    assert seeded.result is not None, "the dialog rejected its own input"
    assert seeded.windowTitle() == add_caption, (
        "something inside AddClassDialog re-derived the caption after "
        "__init__ (now %r), so the caller-side fix would not hold and the "
        "keyword-only `title=` really is required — at +1 McCabe in a file "
        "with none to spare" % (seeded.windowTitle(),))
