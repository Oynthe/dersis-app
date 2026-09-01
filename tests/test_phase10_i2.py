"""I2 — a file saved by an older build opens with dangling room rules, and the
app never says so.

The defect
----------
``ui/app.py::open_file`` and ``ui/app.py::_auto_load`` normalize an opened
``.egu`` (``normalize_state_day_keys`` / ``normalize_state_classes``) and then
adopt it. Neither looks at ``required_classrooms`` / ``excluded_classrooms``,
and neither has to: ``core/models.py::find_off_grid_placements`` states the
policy this load path lives under, about itself, in writing —

    Deliberately NOT called from ``normalize_state_classes`` (and so not from
    the .egu load path): unplacing orphans at load time would silently discard
    the user's own placements with no way to see or undo it, which is the same
    class of bug in a new place. Callers decide what to do — warn, list, or
    offer to reconcile.

Phase 9 put ``self._reconcile_after_setup()`` on that line and chose **none**
of the three; ``tests/test_phase9_b4.py::test_opening_a_file_does_not_unplace_the_users_lessons``
is the contract that came out of reverting it, and everything below keeps it
green. What is still open is the other half: the caller has not decided
anything at all. It neither repairs nor speaks.

Three consequences, each measured separately below:

(a) a dangling ``required_classrooms`` name intersects nothing, so
    ``get_physical_room_candidates`` collapses to ``[]`` and the lesson cannot
    be placed by drag, by Place All Unplaced or by the solver. The only
    sentence any of the three produces is ``errors.no_compatible_classrooms``
    ("Uyumlu derslik bulunamadı"), which names neither the lesson's rule nor
    the vanished room.
(b) a dangling ``excluded_classrooms`` name matches nothing, so the exclusion
    stops applying: the forbidden room becomes a candidate and the batch
    placer puts the lesson in it. A wrong timetable rather than none.
(c) ``ui/dialogs.py::AddClassDialog`` builds its room checkboxes from the LIVE
    room list and ``_ok`` rebuilds both fields from those same registries, so
    opening Edit Class on the affected lesson and pressing OK erases the rule
    — inside a green "class updated" toast.

What "correct" means here
-------------------------
One invariant, and it is the policy's own sentence turned into an assertion:

    A load path that adopts a schedule naming a classroom that schedule's own
    room list does not contain must tell the user — naming the lesson and the
    room — before anything in the app acts on that rule or destroys it.

Every test's final assertion is that invariant. All three of *warn*, *list* and
*offer to reconcile* satisfy it; a silent sweep does not, and neither does
today's silence. The damage each consequence does is **measured** in each test
and carried in the failure text rather than asserted, deliberately: a fix that
offers to reconcile and is declined leaves the damage in place, and a probe
that pinned the damage would then go red for the wrong reason.

How the file is built
---------------------
Through ``scheduler_app.storage.save_encrypted`` — the app's own writer — and
reopened through the real ``SchedulerApp.open_file`` (``QFileDialog`` stubbed
to return the path, which is the only thing stubbed) and the real
``SchedulerApp._auto_load`` (by writing the real settings container and
constructing a real window). The "older build" is reproduced the way
``SetupDialog._ok`` reproduces one: it assigns ``state["classrooms"] = rooms``
as a plain list, so a rename reaches state as nothing but a different string.
"""
import copy

import pytest

pytest.importorskip("PyQt6.QtWidgets", reason="PyQt6 not installed")


LAB_OLD = "Fizik Lab"          # the room the rule names
LAB_NOW = "Fizik Laboratuvarı"  # what the older build renamed it to
HALL = "Amfi A"
LECTURER = "Dr. Ay"
TARGET = {"year": "Year-1", "branch": "A"}

REQUIRED_LESSON = "Fizik I"
EXCLUDED_LESSON = "Kimya I"


# ── State / file builders ───────────────────────────────────────────────────

def _base_state(rooms):
    from scheduler_app.core.models import new_state

    state = new_state()
    state["days"] = ["monday"]
    state["slots"] = ["09:00", "10:00"]
    state["classrooms"] = list(rooms)
    state["classroom_capacities"] = {r: 0 for r in rooms}
    state["lecturers"] = [LECTURER]
    state["lecturer_availability"] = {}
    state["years"] = {"Year-1": ["A"]}
    state["classes"] = []
    return state


def _add(state, name, required=(), excluded=()):
    from scheduler_app.core.models import new_class

    cls = new_class()
    cls["class_code"] = name.replace(" ", "")
    cls["name"] = name
    cls["lecturer"] = LECTURER
    cls["targets"] = [dict(TARGET)]
    cls["duration"] = 1
    cls["participants"] = 0
    cls["required_classrooms"] = list(required)
    cls["excluded_classrooms"] = list(excluded)
    state["classes"].append(cls)
    return cls


def _required_state(rooms):
    """One lesson that must be in LAB_OLD."""
    state = _base_state(rooms)
    _add(state, REQUIRED_LESSON, required=[LAB_OLD])
    return state


def _excluded_state(rooms):
    """One lesson that must NOT be in LAB_OLD."""
    state = _base_state(rooms)
    _add(state, EXCLUDED_LESSON, excluded=[LAB_OLD])
    return state


def _write_egu(path, state):
    """Save through the app's own storage layer, exactly as File > Save does."""
    from scheduler_app import storage
    from scheduler_app.core.models import normalize_state_classes
    from scheduler_app.i18n.day_keys import normalize_state_day_keys

    normalize_state_day_keys(state)
    normalize_state_classes(state)
    storage.save_encrypted(state, str(path))
    return str(path)


def _dangling(state, cls, field):
    rooms = set(state.get("classrooms") or [])
    return [r for r in (cls.get(field) or []) if r not in rooms]


def _find(state, name):
    for c in state["classes"]:
        if c["name"] == name:
            return c
    raise AssertionError("no class named %r in %r"
                         % (name, [c["name"] for c in state["classes"]]))


# ── Channel capture ─────────────────────────────────────────────────────────

class _Said:
    """Everything the app said, on every channel it has.

    ``_show_toast`` (which mirrors into ``warning_log``), the four
    ``QMessageBox`` statics, and ``_deferred_warning`` (the startup modal
    ``_report_settings_problem`` uses when the widgets do not exist yet).
    """

    def __init__(self):
        self.toasts = []
        self.boxes = []
        self.deferred = []

    def install(self, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox
        from scheduler_app.ui.app import SchedulerApp

        monkeypatch.setattr(
            SchedulerApp, "_show_toast",
            lambda win, message, kind="info": self.toasts.append(
                (str(message), kind)))
        monkeypatch.setattr(
            SchedulerApp, "_deferred_warning",
            lambda win, title, text: self.deferred.append((str(title),
                                                           str(text))))
        for name in ("information", "warning", "critical", "question"):
            monkeypatch.setattr(
                QMessageBox, name,
                staticmethod(
                    (lambda n: lambda *a, **k: (
                        self.boxes.append(
                            (n, " | ".join(str(x) for x in a[1:3]))),
                        QMessageBox.StandardButton.Yes)[1])(name)))
        return self

    @property
    def text(self):
        parts = [m for m, _k in self.toasts]
        parts += [t for _n, t in self.boxes]
        parts += ["%s %s" % (t, b) for t, b in self.deferred]
        return "\n".join(parts)

    def __repr__(self):
        return ("_Said(toasts=%r, boxes=%r, deferred=%r)"
                % (self.toasts, self.boxes, self.deferred))


def _open_through_the_app(win, monkeypatch, path):
    """Drive the real File > Open. Only the file chooser is stubbed."""
    from PyQt6.QtWidgets import QFileDialog

    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (path, "")))
    win.open_file()


# ── The three placement routes ──────────────────────────────────────────────

def _candidates(state, cls):
    from scheduler_app.core.models import get_physical_room_candidates
    return get_physical_room_candidates(state, cls)


def _drag_route(win, cls, day, slot):
    """Drive the real drop handler exactly as a finished drag does."""
    win._dragging_cls = cls
    win._dragging_classes = [cls]
    win._drag_backup = None
    win._drag_undo_entry = None
    win._drag_undo_pushed = False
    win._execute_drop(day, slot)


def _solver_route(win):
    """Drive the production solver entry point (``core/solver_worker`` uses it).

    A one-run budget: the same function, the same code path, a search budget
    the test can afford.
    """
    result = win._workflow.reschedule(
        win._workflow.get_weights(), use_cpsat=False,
        multi_start_runs=1, multi_start_time_limit=1.0)
    return result


# ══════════════════════════════════════════════════════════════════════════
#  (a) a dangling `required_classrooms` name
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.ui
def test_opening_a_file_with_a_dangling_required_room_says_nothing(
        make_app, monkeypatch, tmp_path):
    """Three placement routes refuse the lesson; none of them, and not the
    load, says the rule is the reason."""
    stranded = _required_state([LAB_NOW, HALL])
    path = _write_egu(tmp_path / "stranded_required.egu", stranded)

    said = _Said().install(monkeypatch)
    win = make_app()
    _open_through_the_app(win, monkeypatch, path)

    state = win.state_data
    cls = _find(state, REQUIRED_LESSON)

    # Anti-vacuity: the file really loaded, and the rule really is dangling.
    assert win.current_file == path, (
        "open_file did not adopt the file: current_file=%r" % (win.current_file,))
    assert cls["required_classrooms"] == [LAB_OLD], (
        "the load changed the rule: %r" % (cls["required_classrooms"],))
    assert _dangling(state, cls, "required_classrooms") == [LAB_OLD], (
        "the fixture is not dangling: rooms=%r rule=%r"
        % (state["classrooms"], cls["required_classrooms"]))

    at_open = copy.deepcopy(said.toasts), copy.deepcopy(said.boxes)

    # ── route 1: candidates ────────────────────────────────────────────
    cands = _candidates(state, cls)

    # ── route 2: drag ──────────────────────────────────────────────────
    boxes_before = len(said.boxes)
    _drag_route(win, cls, "monday", "09:00")
    drag_boxes = said.boxes[boxes_before:]
    drag_placed = bool(cls.get("placed"))

    # ── route 3: Place All Unplaced (Ctrl+P) ───────────────────────────
    toasts_before = len(said.toasts)
    win.place_all_unplaced_classes()
    batch_toasts = said.toasts[toasts_before:]
    batch_placed = bool(cls.get("placed"))

    # ── route 4: the solver ────────────────────────────────────────────
    result = _solver_route(win)
    solver_placed = [c for c, *_ in result.placed
                     if c.get("name") == REQUIRED_LESSON]
    solver_unplaced = [(c.get("name"), r) for c, r in result.unplaced]

    # Snapshot BEFORE the Setup contrast below, or the contrast would satisfy
    # the assertion: the Setup path already names the room (B4).
    text = said.text

    # ── the contrast: the ONE gesture that does speak ──────────────────
    # `_reconcile_after_setup` is what Setup OK runs, and since B4 it names
    # the lesson and the room it deleted. Any unrelated Setup edit — adding a
    # day — therefore destroys this rule, and only that route says so. The
    # load path, which is where the rule arrives, does not.
    toasts_before = len(said.toasts)
    win._reconcile_after_setup()
    setup_toasts = said.toasts[toasts_before:]
    after_setup = list(cls["required_classrooms"])

    measured = (
        "  candidates                 %r\n"
        "  drag -> placed             %r,  message(s) %r\n"
        "  place-all -> placed        %r,  toast(s)   %r\n"
        "  solver -> placed           %r,  unplaced   %r\n"
        "  channels at open           toasts=%r boxes=%r deferred=%r\n"
        "  contrast: an unrelated Setup OK erases it %r -> %r and DOES say "
        "so: %r"
        % (cands, drag_placed, drag_boxes, batch_placed, batch_toasts,
           bool(solver_placed), solver_unplaced,
           at_open[0], at_open[1], said.deferred,
           [LAB_OLD], after_setup, setup_toasts))

    assert LAB_OLD in text and REQUIRED_LESSON in text, (
        "Opening a schedule whose lesson %r requires the classroom %r — a room "
        "the file's OWN classroom list (%r) does not contain — told the user "
        "nothing that names either one.\n"
        "core/models.py::find_off_grid_placements states the rule this load "
        "path lives under: 'Callers decide what to do — warn, list, or offer "
        "to reconcile.' open_file decides none of the three; it is silent, and "
        "the lesson is now unplaceable by every route in the app for a reason "
        "no screen displays:\n%s\n"
        "Everything the user was told, on every channel:\n  %r"
        % (REQUIRED_LESSON, LAB_OLD, stranded["classrooms"], measured, said))


# ══════════════════════════════════════════════════════════════════════════
#  (b) a dangling `excluded_classrooms` name
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.ui
def test_opening_a_file_with_a_dangling_excluded_room_says_nothing(
        make_app, monkeypatch, tmp_path):
    """The exclusion stops forbidding: the room becomes a candidate and the
    batch placer uses it. A wrong timetable, silently."""
    # The control: the same rule in a file whose room list still contains the
    # room it names. This is what the user's rule is worth when it is intact.
    intact = _excluded_state([LAB_OLD, HALL])
    intact_cands = _candidates(intact, _find(intact, EXCLUDED_LESSON))

    stranded = _excluded_state([LAB_NOW, HALL])
    path = _write_egu(tmp_path / "stranded_excluded.egu", stranded)

    said = _Said().install(monkeypatch)
    win = make_app()
    _open_through_the_app(win, monkeypatch, path)

    state = win.state_data
    cls = _find(state, EXCLUDED_LESSON)

    assert cls["excluded_classrooms"] == [LAB_OLD], (
        "the load changed the rule: %r" % (cls["excluded_classrooms"],))
    assert _dangling(state, cls, "excluded_classrooms") == [LAB_OLD], (
        "the fixture is not dangling: rooms=%r rule=%r"
        % (state["classrooms"], cls["excluded_classrooms"]))

    stranded_cands = _candidates(state, cls)
    win.place_all_unplaced_classes()
    landed = cls.get("placed_classroom")

    text = said.text

    # The asymmetry that makes this the worse of the two fields. An unrelated
    # Setup OK deletes a dangling EXCLUSION too, but `reconcile_placements`
    # tracks only `required_classrooms` in `lost_room_requirements` — by an
    # explicit judgement in its own comment — so the exclusion dies with only
    # a count, and re-adding the room does not bring it back.
    toasts_before = len(said.toasts)
    win._reconcile_after_setup()
    setup_toasts = said.toasts[toasts_before:]
    after_setup = list(cls["excluded_classrooms"])

    measured = (
        "  rule                       excluded_classrooms = %r\n"
        "  rooms in the intact file   %r  -> candidates %r\n"
        "  rooms in the opened file   %r  -> candidates %r\n"
        "  Place All Unplaced put it in %r\n"
        "  channels                   toasts=%r boxes=%r deferred=%r\n"
        "  an unrelated Setup OK then erases it %r -> %r, saying only: %r"
        % ([LAB_OLD], intact["classrooms"], intact_cands,
           state["classrooms"], stranded_cands, landed,
           said.toasts, said.boxes, said.deferred,
           [LAB_OLD], after_setup, setup_toasts))

    assert LAB_OLD in text and EXCLUDED_LESSON in text, (
        "Opening a schedule whose lesson %r forbids the classroom %r — a room "
        "the file's OWN classroom list does not contain — told the user "
        "nothing that names either one. The exclusion is a membership test "
        "(core/models.py::get_physical_room_candidates), so a name that "
        "matches nothing forbids nothing: the room the user ruled out is a "
        "candidate again and the placer used it. That is a WRONG timetable, "
        "not a missing one, which is why it must be said out loud.\n%s\n"
        "Everything the user was told, on every channel:\n  %r"
        % (EXCLUDED_LESSON, LAB_OLD, measured, said))


# ══════════════════════════════════════════════════════════════════════════
#  (c) Edit Class + OK erases the rule the load never mentioned
# ══════════════════════════════════════════════════════════════════════════

def _accept_the_form_unchanged(monkeypatch):
    """Make the real ``AddClassDialog`` run its real ``_ok`` and accept.

    Nothing about the form is faked: the checkbox registries, the validator
    and ``_ok`` itself are the shipped ones. Only ``exec()`` — which would
    block forever under the offscreen platform — is replaced, by the two
    things a user pressing OK does: run ``_ok``, return Accepted.
    """
    from scheduler_app.ui.dialogs import AddClassDialog

    def _exec(self):
        self._ok()
        return (AddClassDialog.DialogCode.Accepted if self.result
                else AddClassDialog.DialogCode.Rejected)

    monkeypatch.setattr(AddClassDialog, "exec", _exec)


@pytest.mark.ui
def test_edit_class_ok_erases_a_room_rule_the_load_never_mentioned(
        make_app, monkeypatch, tmp_path):
    """The rule the user never saw is deleted by a gesture about another field."""
    stranded = _required_state([LAB_NOW, HALL])
    path = _write_egu(tmp_path / "stranded_dialog.egu", stranded)

    said = _Said().install(monkeypatch)
    _accept_the_form_unchanged(monkeypatch)
    win = make_app()
    _open_through_the_app(win, monkeypatch, path)

    state = win.state_data
    cls = _find(state, REQUIRED_LESSON)
    before = list(cls["required_classrooms"])
    undo_before = len(win._undo_stack)
    said_at_open = copy.deepcopy(said.toasts), copy.deepcopy(said.boxes)

    # The real context-menu / double-click entry point.
    win._edit_class(cls)

    cls = _find(win.state_data, REQUIRED_LESSON)
    after = list(cls["required_classrooms"])

    measured = (
        "  required_classrooms  before Edit Class + OK  %r\n"
        "  required_classrooms  after                   %r\n"
        "  undo depth           %d -> %d\n"
        "  the user was told at open   toasts=%r boxes=%r\n"
        "  the user was told on OK     toasts=%r"
        % (before, after, undo_before, len(win._undo_stack),
           said_at_open[0], said_at_open[1], said.toasts))

    text = said.text
    assert LAB_OLD in text, (
        "%r required the classroom %r when the file was opened and requires "
        "nothing at all after Edit Class + OK, and the room name %r was never "
        "printed on any channel — not at open, and not when it was deleted. "
        "AddClassDialog builds its room checkboxes from the LIVE room list "
        "(ui/dialogs.py,2513) and _ok rebuilds both fields from those "
        "registries , so a rule naming a room that is not in the "
        "list has no checkbox to be checked and is rebuilt as []. '[]' means "
        "'any room' everywhere in core, so the lesson that had to be in the "
        "physics lab may now be auto-scheduled into a lecture hall. The room "
        "NAME is the only thing that would let a user put the rule back, and "
        "it is gone.\n%s\n"
        "Everything the user was told, on every channel:\n  %r"
        % (REQUIRED_LESSON, LAB_OLD, LAB_OLD, measured, said))


# ══════════════════════════════════════════════════════════════════════════
#  `_auto_load` — the same exposure, on the path with no widgets
# ══════════════════════════════════════════════════════════════════════════

def _plant_settings_container(state):
    """Write the real settings container ``_auto_load`` reads on launch."""
    from scheduler_app import storage

    storage.save_encrypted(
        {"state": state, "last_file": None, "language": "tr"},
        storage.settings_path())


@pytest.mark.ui
def test_auto_load_adopts_a_dangling_room_rule_without_saying_so(
        make_app, dersis_home, monkeypatch):
    """Launching DERSİS onto the autosaved state has the same exposure.

    ``_auto_load`` runs from ``__init__`` *before* ``_build_main()``, so there
    is no widget for a report — which is the constraint any fix has to design
    around, not a reason for the silence.
    """
    stranded = _required_state([LAB_NOW, HALL])
    from scheduler_app.core.models import normalize_state_classes
    from scheduler_app.i18n.day_keys import normalize_state_day_keys
    normalize_state_day_keys(stranded)
    normalize_state_classes(stranded)
    _plant_settings_container(stranded)

    said = _Said().install(monkeypatch)
    win = make_app()          # __init__ -> _auto_load -> _flush_startup_settings_report

    cls = _find(win.state_data, REQUIRED_LESSON)
    assert cls["required_classrooms"] == [LAB_OLD], (
        "_auto_load did not adopt the planted state: %r"
        % (cls["required_classrooms"],))

    measured = (
        "  loaded rule      %r   against rooms %r\n"
        "  candidates       %r\n"
        "  pending slot     %r\n"
        "  toasts %r  boxes %r  deferred %r"
        % (cls["required_classrooms"], win.state_data["classrooms"],
           _candidates(win.state_data, cls),
           win._pending_settings_report,
           said.toasts, said.boxes, said.deferred))

    text = said.text
    assert LAB_OLD in text and REQUIRED_LESSON in text, (
        "DERSİS launched onto an autosaved schedule whose lesson %r requires "
        "the classroom %r, which the schedule's own room list does not "
        "contain, and said nothing. _auto_load has no widgets — it runs from "
        "__init__ before _build_main() — and __init__ already has the machine "
        "for exactly this: _report_settings_problem stashes into "
        "_pending_settings_report and _flush_startup_settings_report drains it "
        "once the window exists. Nothing put anything in it.\n%s\n"
        "Everything the user was told, on every channel:\n  %r"
        % (REQUIRED_LESSON, LAB_OLD, measured, said))


# ══════════════════════════════════════════════════════════════════════════
#  Supporting facts — green today, and the reason the fix has to be designed
#  rather than dropped into open_file
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.ui
def test_auto_load_runs_before_any_widget_exists(make_app, monkeypatch):
    """GREEN today. Establishes the constraint the (b)/(d) fix must satisfy."""
    from scheduler_app.ui.app import SchedulerApp

    seen = {}
    real = SchedulerApp._auto_load

    def _spy(self):
        seen["status_label"] = getattr(self, "status_label", None)
        seen["warning_log"] = hasattr(self, "warning_log")
        seen["notebook"] = hasattr(self, "notebook")
        seen["pending_slot_exists"] = hasattr(self, "_pending_settings_report")
        return real(self)

    monkeypatch.setattr(SchedulerApp, "_auto_load", _spy)
    win = make_app()

    assert seen, "_auto_load was not called from __init__"
    assert seen["status_label"] is None and not seen["warning_log"], (
        "_auto_load now runs with widgets available (%r) — the constraint this "
        "test records has changed" % (seen,))
    assert seen["pending_slot_exists"], (
        "_pending_settings_report is not initialised before _auto_load: %r"
        % (seen,))
    # And after __init__ the widgets do exist, so the flush had somewhere to go.
    assert getattr(win, "status_label", None) is not None
    assert hasattr(win, "warning_log")


@pytest.mark.ui
def test_the_single_pending_slot_carries_a_settings_message_at_launch(
        make_app, dersis_home, monkeypatch):
    """GREEN today. The one existing pre-widget writer, driven for real.

    A corrupt settings container makes ``_read_settings_container`` call
    ``_report_settings_problem("corrupt", ...)`` from inside ``_auto_load``,
    where ``status_label`` is None — so the message goes into
    ``_pending_settings_report`` and is drained by
    ``_flush_startup_settings_report`` after ``_build_main()``. That single
    string slot is the whole startup reporting budget, and it is
    last-writer-wins with a per-*kind* once-per-session rate limit
    (``_settings_problems``), so a second pre-widget writer of a different kind
    would discard the first permanently.
    """
    from scheduler_app import storage

    path = storage.settings_path()
    with open(path, "wb") as fh:
        fh.write(b"EGU1" + b"\x00" * 64)   # right magic, unreadable body

    said = _Said().install(monkeypatch)
    win = make_app()

    assert win._pending_settings_report is None, (
        "the slot was never drained: %r" % (win._pending_settings_report,))
    assert "corrupt" in win._settings_problems, (
        "the corrupt container was not reported as a settings problem: %r"
        % (win._settings_problems,))
    assert said.toasts, (
        "a corrupt settings container at launch produced no toast: %r" % (said,))
    assert said.deferred, (
        "a corrupt settings container at launch produced no modal: %r" % (said,))
