"""Setup-dialog placement reconciliation (ST-DATA-004).

ST-DATA-004 (High): ``SetupDialog._ok`` (``ui/dialogs.py:1813-1821``) assigns the
new ``days`` / ``slots`` / ``classrooms`` / ``lecturers`` lists straight into the
live ``state`` dict, and ``SchedulerApp.edit_setup`` (``ui/app.py:2828-2838``)
just calls ``refresh_grid()`` afterwards. Nothing looks at ``state["classes"]``.
So the moment a user unchecks a weekday, deletes a slot line, or removes a
classroom / lecturer row, every class that was placed on the axis value they
just deleted keeps ``placed=True`` pointing at something that no longer exists.

That is the *normal-UI trigger* for ST-DATA-003 (dangling ``placed_time`` ->
``slot_index`` blows up). ST-DATA-003 is owned at the core/engine layer by
another module; here we only assert that the orphans are never created, plus
one trailing "and the user's click did not throw" assertion per test, because
today deleting a time slot raises ``ValueError: '12:00' is not in list`` out of
``edit_setup`` itself (see the run notes at the bottom of this docstring).

What "correct" means here
-------------------------
One invariant, four axes:

    A class may not occupy the timetable unless every axis value it references
    still exists in the setup.

Concretely, after the new setup is applied:

* ``placed=True`` implies ``placed_day in state["days"]``,
  ``placed_time in state["slots"]``, ``placed_classroom in
  state["classrooms"]`` (for classes that need a physical room) and
  ``lecturer in state["lecturers"]``;
* ``pinned=True`` implies the same for ``pinned_day`` / ``pinned_time`` /
  ``pinned_classroom`` and ``lecturer`` -- a pin is a promise about a cell, and
  a cell that was deleted cannot be promised.

Precedent for that reading already exists in production: ``ui/day_keys.py``
``normalize_state_day_keys()`` lines 81-90 does exactly this for the *day* axis
(``pinned_day=None`` + ``pinned=False``, ``placed_day=None`` + ``placed=False``).
It just never runs at setup-OK time -- it is reached only as a side effect of
``_auto_save()`` at the tail of ``refresh_grid()`` (ST-ARCH-007), silently and
inside a bare ``except: pass`` (ST-DATA-005). The fix should generalize those
semantics to all four axes and run them deliberately, at the point of change.

``protection="locked"`` is treated differently from ``pinned`` on purpose:
"locked" means *the optimizer may not move this*, not *this may live in a slot
that was deleted*. So a locked class is unplaced like any other, but its
``protection`` value is user data and must survive untouched. Full rationale in
``test_pinned_and_locked_classes_are_reconciled_coherently``.

How the flow is driven
----------------------
``SetupDialog.exec`` is monkeypatched to a shim that mutates the *real* dialog's
widgets and then calls the *real* ``SetupDialog._ok``. Nothing about the
reconciliation is re-implemented here. The whole ``SchedulerApp.edit_setup`` ->
``SetupDialog._ok`` -> ``state_data`` -> ``refresh_grid`` path runs for real, so
the tests stay green whether the fix lands inside ``_ok``, inside
``edit_setup``, or (recommended) in a core-layer ``reconcile_placements(state)``
that both call.

The user-facing notification is asserted across every channel the app actually
has -- ``QMessageBox`` statics *and* instances, ``_show_toast``, the
``WarningLogPanel`` and the ``logging`` module -- so the assertion is about
observable behaviour, not one implementation. ``_refresh_warnings()`` re-emits
its whole suggestion list into the warning log on every repaint, so each
notification assertion first subtracts the messages a bare second
``refresh_grid()`` reproduces (see ``_residual_messages``).

Non-vacuity
-----------
Two gates, both added after a measured hole. ``_assert_seed_is_live`` runs in
the ``seeded`` fixture and proves the schedule the assertions talk about was
actually built -- without it, stubbing ``mark_placed`` to a no-op makes 8 of
these 19 tests pass against a correct fix, because a timetable with nothing on
it has no orphans on it either. ``_apply_setup`` additionally asserts
``dlg.result is True``, because ``SetupDialog._ok`` has three early ``return``
paths that never write the setup back and that the no-removal cases could not
otherwise distinguish from success.

The last test in the module is a *guard*, not a fail-now assertion: it pins down
that a class with **no lecturer assigned** is not a class whose lecturer was
deleted. ``lecturer`` defaults to ``""`` in ``new_class()`` and the core reads
blank as "no lecturer constraint", so the obvious one-line reconciliation
(``cls["lecturer"] in state["lecturers"]``) would silently unplace every
not-yet-staffed lesson on the first Setup OK.

Fail-now / pass-after: no ``xfail`` markers.

Status observed on ``fix/phase-1-data-correctness`` before the fix
------------------------------------------------------------------
* ``slot`` -- ``edit_setup()`` raises ``ValueError: '12:00' is not in list``
  (``core/logic.py:18`` ``slot_index`` via ``_refresh_open_slots``). The app
  throws in the user's face on a plain Setup edit.
* ``classroom`` / ``lecturer`` -- no crash, 5 dangling references survive
  (4 placed victims + 1 dangling pin).
* ``day`` -- the state invariant *accidentally* holds, because
  ``refresh_grid() -> _auto_save() -> normalize_state_day_keys()`` cleans the
  day axis after the fact. Nobody tells the user. Those parametrizations are
  kept as regression guards for a fix that centralizes reconciliation and could
  otherwise drop the day cleanup on the floor.
"""
import copy
import re

import pytest

pytestmark = pytest.mark.ui


# ── The fixture timetable ───────────────────────────────────────────────────

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday"]
SLOTS = ["09:00", "10:00", "11:00", "12:00"]
ROOMS = ["R001", "R002", "R003"]
LECTURERS = ["Lect-001", "Lect-002", "Lect-003"]

AXES = ("day", "slot", "classroom", "lecturer")

#: The one axis value each parametrization deletes from the setup.
REMOVED = {
    "day": "friday",
    "slot": "12:00",
    "classroom": "R003",
    "lecturer": "Lect-003",
}

#: Per axis: (lecturer, day, slot, room, pinned, protection) for 8 classes.
#: Indices 0-3 are the victims (they reference the removed value), 4-6 are
#: placed controls that must survive untouched, 7 is an unplaced control.
#: Index 2 is a pinned victim and index 3 a ``locked`` victim; index 5 is a
#: pinned control and index 6 a ``locked`` control, so a fix that special-cases
#: pinned/locked classes cannot pass by wiping them wholesale either.
_LAYOUTS = {
    "day": [
        ("Lect-001", "friday",    "09:00", "R001", False, "none"),
        ("Lect-002", "friday",    "10:00", "R002", False, "none"),
        ("Lect-001", "friday",    "11:00", "R001", True,  "none"),
        ("Lect-002", "friday",    "12:00", "R002", False, "locked"),
        ("Lect-001", "monday",    "09:00", "R001", False, "none"),
        ("Lect-002", "tuesday",   "10:00", "R002", True,  "none"),
        ("Lect-003", "wednesday", "11:00", "R003", False, "locked"),
        ("Lect-003", None,        None,    None,   False, "none"),
    ],
    "slot": [
        ("Lect-001", "monday",    "12:00", "R001", False, "none"),
        ("Lect-002", "tuesday",   "12:00", "R002", False, "none"),
        ("Lect-001", "wednesday", "12:00", "R001", True,  "none"),
        ("Lect-002", "thursday",  "12:00", "R002", False, "locked"),
        ("Lect-001", "monday",    "09:00", "R001", False, "none"),
        ("Lect-002", "tuesday",   "10:00", "R002", True,  "none"),
        ("Lect-003", "wednesday", "11:00", "R003", False, "locked"),
        ("Lect-003", None,        None,    None,   False, "none"),
    ],
    "classroom": [
        ("Lect-001", "monday",    "09:00", "R003", False, "none"),
        ("Lect-002", "tuesday",   "10:00", "R003", False, "none"),
        ("Lect-001", "wednesday", "11:00", "R003", True,  "none"),
        ("Lect-002", "thursday",  "12:00", "R003", False, "locked"),
        ("Lect-001", "monday",    "10:00", "R001", False, "none"),
        ("Lect-002", "tuesday",   "11:00", "R002", True,  "none"),
        ("Lect-003", "wednesday", "09:00", "R001", False, "locked"),
        ("Lect-003", None,        None,    None,   False, "none"),
    ],
    "lecturer": [
        ("Lect-003", "monday",    "09:00", "R001", False, "none"),
        ("Lect-003", "tuesday",   "10:00", "R002", False, "none"),
        ("Lect-003", "wednesday", "11:00", "R001", True,  "none"),
        ("Lect-003", "thursday",  "12:00", "R002", False, "locked"),
        ("Lect-001", "monday",    "10:00", "R001", False, "none"),
        ("Lect-002", "tuesday",   "11:00", "R002", True,  "none"),
        ("Lect-001", "wednesday", "09:00", "R003", False, "locked"),
        ("Lect-002", None,        None,    None,   False, "none"),
    ],
}

VICTIM_IDX = (0, 1, 2, 3)
PLACED_CONTROL_IDX = (4, 5, 6)
UNPLACED_CONTROL_IDX = (7,)
PINNED_VICTIM_IDX, LOCKED_VICTIM_IDX = 2, 3
PINNED_CONTROL_IDX, LOCKED_CONTROL_IDX = 5, 6

#: Fields whose values define "this class kept its placement exactly".
PLACEMENT_FIELDS = (
    "placed", "placed_day", "placed_time", "placed_classroom",
    "pinned", "pinned_day", "pinned_time", "pinned_classroom",
    "lecturer", "protection", "duration", "location_type",
)

_PIN_AXIS_KEY = {
    "pinned_day": "days",
    "pinned_time": "slots",
    "pinned_classroom": "classrooms",
}

#: Per axis: (the ``placed_*`` field, the ``pinned_*`` field) that carries it.
_AXIS_FIELDS = {
    "day": ("placed_day", "pinned_day"),
    "slot": ("placed_time", "pinned_time"),
    "classroom": ("placed_classroom", "pinned_classroom"),
    "lecturer": ("lecturer", "lecturer"),
}


# ── Observability plumbing ──────────────────────────────────────────────────

class _Recorder:
    """Stand-in for a modal ``QMessageBox`` static; records instead of blocking."""

    def __init__(self, ret):
        self._ret = ret
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self._ret

    def texts(self):
        out = []
        for args, kwargs in self.calls:
            out.extend(a for a in args if isinstance(a, str))
            out.extend(v for v in kwargs.values() if isinstance(v, str))
        return out


class _Channels:
    """Every user-visible notification channel the app owns, in one place.

    ``messages()`` is the union of the four ``QMessageBox`` statics, modal
    ``QMessageBox`` *instances*, ``SchedulerApp._show_toast``, the
    ``WarningLogPanel`` and ``logging`` records at WARNING or above. The fix is
    free to pick any of them; the tests only care that the user is told.
    """

    def __init__(self, boxes, toasts, logs, caplog):
        self.boxes = boxes
        self.toasts = toasts          # list[(message, kind)]
        self.logs = logs              # list[(message, kind)] from WarningLogPanel
        self._caplog = caplog

    def reset(self):
        for rec in self.boxes.values():
            rec.calls.clear()
        self.toasts.clear()
        self.logs.clear()
        self._caplog.clear()

    def messages(self):
        out = []
        for rec in self.boxes.values():
            out.extend(rec.texts())
        out.extend(m for m, _kind in self.toasts)
        out.extend(m for m, _kind in self.logs)
        out.extend(r.getMessage() for r in self._caplog.records if r.levelno >= 30)
        return [m for m in out if isinstance(m, str)]


def _mentions_count(messages, n):
    """True if some message contains ``n`` as a standalone integer token."""
    pattern = re.compile(r"(?<!\d)%d(?!\d)" % n)
    return any(pattern.search(m) for m in messages)


def _residual_messages(window, channels, emitted):
    """Drop every message a bare second ``refresh_grid()`` reproduces.

    ``_refresh_warnings()`` re-emits the app's whole suggestion list into the
    warning log on every repaint ("crowded days", "no room has capacity for 55
    participants", ...). That is unrelated background chatter for this finding,
    and it carries digits, which would poison both the "was a count reported?"
    and the "was nothing reported?" assertions. Replaying the refresh against
    the *final* state reproduces exactly that chatter, so subtracting it leaves
    only what the setup change itself said.

    Call this only after the state assertions: ``refresh_grid()`` autosaves,
    and autosave normalizes.
    """
    channels.reset()
    try:
        window.refresh_grid()
    except Exception:
        # Pre-fix, an orphaned placement can make the repaint throw; whatever
        # chatter it managed to emit before dying still counts as noise.
        pass
    noise = set(channels.messages())
    return [m for m in emitted if m not in noise]


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def channels(qapp, monkeypatch, caplog):
    """Neutralize every modal and capture every notification channel.

    An unpatched modal blocks the whole suite under the offscreen platform, so
    the patching is a hard requirement rather than a convenience. Both the
    ``QMessageBox`` statics and ``QMessageBox.exec`` are covered, because a fix
    that builds a message box *instance* would otherwise hang the run.
    """
    from PyQt6.QtWidgets import QDialog, QMessageBox

    from scheduler_app.ui.tier_enforcement import UpgradeDialog
    from scheduler_app.ui.widgets import WarningLogPanel

    boxes = {
        "information": _Recorder(QMessageBox.StandardButton.Ok),
        "warning": _Recorder(QMessageBox.StandardButton.Ok),
        "critical": _Recorder(QMessageBox.StandardButton.Ok),
        # "Yes" so that neither the setup dialog's own "data still missing,
        # continue anyway?" prompt nor a confirm-style reconciliation warning
        # can silently abort the flow under test.
        "question": _Recorder(QMessageBox.StandardButton.Yes),
    }
    for name, rec in boxes.items():
        monkeypatch.setattr(QMessageBox, name, staticmethod(rec))

    instance_box = _Recorder(QMessageBox.StandardButton.Yes.value)
    boxes["instance"] = instance_box

    def _fake_exec(self):
        return instance_box(self.windowTitle(), self.text(),
                            self.informativeText(), self.detailedText())

    monkeypatch.setattr(QMessageBox, "exec", _fake_exec)
    monkeypatch.setattr(UpgradeDialog, "exec",
                        lambda self: QDialog.DialogCode.Rejected.value)

    logs = []
    real_log = WarningLogPanel.log

    def spy_log(self, message, kind="info"):
        logs.append((message, kind))
        return real_log(self, message, kind)

    monkeypatch.setattr(WarningLogPanel, "log", spy_log)

    caplog.set_level(0)
    return _Channels(boxes, [], logs, caplog)


_TIER_REGISTRIES = (
    "_gated_widgets", "_gated_actions", "_on_tier_changed",
    "_export_submenu_refreshers",
)


@pytest.fixture
def window(qapp, dersis_home, channels, monkeypatch):
    """A real, fully constructed ``SchedulerApp`` -- never shown.

    Neutralized so the flow is deterministic and non-blocking:

    * the first-run controller (tutorial overlay / setup wizard), armed by a
      ``QTimer`` in ``__init__``;
    * the licence tier, pinned to Institutional so the classroom/lecturer limit
      checks inside ``SetupDialog._ok`` cannot short-circuit the OK handler;
    * ``_run_impact_analysis``, a *separate* post-save feature that pops its own
      "reschedule now?" modal and, on Yes, runs the whole optimizer. It cannot
      be where reconciliation belongs -- it runs *after* ``refresh_grid()``,
      which is already too late, and it is shared with a dozen unrelated call
      sites -- so stubbing it removes noise without removing coverage.

    Isolation: every ``SchedulerApp`` registers gated ``QAction``s and a
    tier-change callback into the process-wide ``TierEnforcement`` singleton and
    never unregisters them, so the registries are snapshotted and restored.
    """
    from PyQt6.QtCore import QCoreApplication, QEvent

    from scheduler_app.plans import TIER_INSTITUTIONAL
    from scheduler_app.ui.first_run import FirstRunController
    from scheduler_app.ui.tier_enforcement import TierEnforcement

    monkeypatch.setattr(FirstRunController, "start", lambda self: None)

    enforcer = TierEnforcement.instance()
    prev_slug, prev_confirmed = enforcer._tier_slug, enforcer._tier_confirmed
    prev_registries = {
        name: list(getattr(enforcer, name))
        for name in _TIER_REGISTRIES if hasattr(enforcer, name)
    }
    enforcer._tier_slug, enforcer._tier_confirmed = TIER_INSTITUTIONAL, True

    from scheduler_app.ui.app import SchedulerApp

    monkeypatch.setattr(SchedulerApp, "_run_impact_analysis",
                        lambda self, before: None)

    real_toast = SchedulerApp._show_toast

    def spy_toast(self, message, kind="info"):
        channels.toasts.append((message, kind))
        return real_toast(self, message, kind)

    monkeypatch.setattr(SchedulerApp, "_show_toast", spy_toast)

    win = SchedulerApp()
    try:
        yield win
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()
        # deleteLater() only queues a DeferredDelete event, and processEvents()
        # outside an event loop does not deliver those. Without this explicit
        # flush every SchedulerApp in the module stays alive with its ~19
        # top-level widgets, and constructing the next one gets steadily slower
        # (measured: 0.07 s -> 0.55 s over 8 windows, i.e. the module's runtime
        # grows quadratically). Targeted at this window only, so it cannot
        # collect an object another fixture is still holding.
        QCoreApplication.sendPostedEvents(win, QEvent.Type.DeferredDelete)
        enforcer._tier_slug, enforcer._tier_confirmed = prev_slug, prev_confirmed
        for name, value in prev_registries.items():
            setattr(enforcer, name, value)


@pytest.fixture
def seeded(window, make_state, channels):
    """Return ``load(axis)`` -> installs that axis' fixture timetable."""
    from scheduler_app.core.models import mark_placed

    def load(axis):
        state = make_state(
            n_days=len(DAYS), n_slots=len(SLOTS), n_rooms=len(ROOMS),
            n_lecturers=len(LECTURERS), n_years=1, branches_per_year=2,
            n_classes=len(_LAYOUTS[axis]), density=0.0, online_fraction=0.0,
            max_duration=1, seed=7,
        )
        # Guard the generator: the layouts below address these axes by value.
        assert state["days"] == DAYS
        assert state["slots"] == SLOTS
        assert state["classrooms"] == ROOMS
        assert state["lecturers"] == LECTURERS

        for cls, (lect, day, slot, room, pin, prot) in zip(
                state["classes"], _LAYOUTS[axis]):
            cls["duration"] = 1
            cls["lecturer"] = lect
            cls["protection"] = prot
            cls["pinned"] = pin
            cls["pinned_day"] = day if pin else None
            cls["pinned_time"] = slot if pin else None
            cls["pinned_classroom"] = room if pin else None
            if day is not None:
                mark_placed(cls, day, slot, room)

        # Keep the dict identity: SchedulingWorkflow holds a reference to it.
        window.state_data.clear()
        window.state_data.update(state)
        window.mark_current_state_as_baseline()
        # Non-vacuity gate: prove the schedule under test actually exists.
        _assert_seed_is_live(window.state_data, axis)
        channels.reset()
        return window.state_data

    return load


# ── Driving the real dialog ─────────────────────────────────────────────────

class _Applied:
    """Outcome of one real Setup -> OK round trip."""

    def __init__(self, dialog, error):
        self.dialog = dialog
        self.error = error


def _apply_setup(window, monkeypatch, mutate):
    """Run the real File -> Setup flow, applying ``mutate(dlg)`` in the dialog.

    ``SetupDialog.exec`` is replaced by a shim (an exec'd modal would block
    forever offscreen). The shim edits the live dialog's widgets exactly as a
    user would and then calls the genuine ``SetupDialog._ok`` -- validation,
    tier checks, result marshalling and all.

    An exception escaping ``edit_setup`` is captured rather than propagated so
    the state assertions -- which are the finding -- get evaluated and reported
    first. Every test then asserts on it via ``_assert_no_crash``, last.
    """
    from PyQt6.QtWidgets import QDialog

    from scheduler_app.ui.dialogs import SetupDialog

    captured = {}

    def fake_exec(self):
        captured["dialog"] = self
        mutate(self)
        self._ok()
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(SetupDialog, "exec", fake_exec)
    error = None
    try:
        window.edit_setup()
    except Exception as exc:  # noqa: BLE001 - re-asserted by the caller
        error = exc
    assert "dialog" in captured, "edit_setup never opened a SetupDialog"
    # Second non-vacuity gate. ``_ok`` has four early ``return`` paths (missing
    # data declined, two tier-limit refusals) that leave ``result`` False and
    # never write the setup back. On the no-removal cases the state is already
    # equal to what OK would have written, so ``_assert_axis_removed``-style
    # guards cannot see the difference -- only ``result`` can.
    assert captured["dialog"].result is True, (
        "SetupDialog._ok returned without committing (result is not True): the "
        "OK handler bailed out early, so no reconciliation could have run")
    return _Applied(captured["dialog"], error)


def _assert_no_crash(applied, what):
    """The user's click must complete. Always the *last* assertion in a test.

    Deliberately trailing: the state assertions above are ST-DATA-004 itself and
    must be the ones reported first. This one only records that pressing OK in
    Setup is currently able to throw in the user's face -- today the slot axis
    raises ``ValueError: '12:00' is not in list`` out of ``edit_setup``, which is
    ST-DATA-003's failure mode reached through ST-DATA-004's door. Reconciling
    before the repaint closes it; the assertion exists so a fix that reconciles
    *after* ``refresh_grid()`` (verified: 5 tests fail) cannot be called done.
    """
    assert applied.error is None, (
        f"{what} in Setup raised "
        f"{type(applied.error).__name__}: {applied.error}")


def _remove_from_dialog(axis, value):
    """Return a ``mutate(dlg)`` that deletes ``value`` from the given axis."""

    def mutate(dlg):
        if axis == "day":
            assert dlg.day_buttons[value].isChecked()
            dlg.day_buttons[value].setChecked(False)
        elif axis == "slot":
            lines = [s.strip() for s in dlg.slots_text.toPlainText().splitlines()
                     if s.strip()]
            assert value in lines
            dlg.slots_text.setPlainText("\n".join(s for s in lines if s != value))
        elif axis == "classroom":
            dlg.rooms_table.removeRow(_find_row(dlg.rooms_table, value))
        elif axis == "lecturer":
            dlg.lec_table.removeRow(_find_row(dlg.lec_table, value))
        else:  # pragma: no cover - guarded by the parametrization
            raise AssertionError(f"unknown axis {axis!r}")

    return mutate


def _find_row(table, text):
    for row in range(table.rowCount()):
        item = table.item(row, 0)
        if item and item.text().strip() == text:
            return row
    raise AssertionError(f"{text!r} is not in the dialog table")


def _snapshot(state):
    """{class_uid: {placement field: value}} for every class."""
    return {c["class_uid"]: {f: copy.deepcopy(c.get(f)) for f in PLACEMENT_FIELDS}
            for c in state["classes"]}


def _uids(state, indices):
    return [state["classes"][i]["class_uid"] for i in indices]


def _by_uid(state):
    return {c["class_uid"]: c for c in state["classes"]}


def _dangling(state):
    """Every reference from a scheduled class to an axis value that is gone."""
    from scheduler_app.core.models import needs_physical_room

    days, slots = set(state["days"]), set(state["slots"])
    rooms, lecturers = set(state["classrooms"]), set(state["lecturers"])

    problems = []
    for cls in state["classes"]:
        label = cls.get("class_code") or cls.get("name")
        physical = needs_physical_room(cls)
        if cls.get("placed"):
            if cls.get("placed_day") not in days:
                problems.append(f"{label}: placed_day={cls.get('placed_day')!r}")
            if cls.get("placed_time") not in slots:
                problems.append(f"{label}: placed_time={cls.get('placed_time')!r}")
            if physical and cls.get("placed_classroom") not in rooms:
                problems.append(
                    f"{label}: placed_classroom={cls.get('placed_classroom')!r}")
            # A blank lecturer references nothing, so it cannot dangle. The core
            # treats it as "no lecturer constraint" (``logic.find_conflicts``
            # line 267: ``if lecturer:``) and ``new_class()`` ships ``""`` as the
            # default, so blank is a supported value, not an orphan.
            if (cls.get("lecturer") or "") and cls["lecturer"] not in lecturers:
                problems.append(f"{label}: placed, lecturer={cls.get('lecturer')!r}")
        if cls.get("pinned"):
            if cls.get("pinned_day") not in days:
                problems.append(f"{label}: pinned_day={cls.get('pinned_day')!r}")
            if cls.get("pinned_time") not in slots:
                problems.append(f"{label}: pinned_time={cls.get('pinned_time')!r}")
            if physical and cls.get("pinned_classroom") not in rooms:
                problems.append(
                    f"{label}: pinned_classroom={cls.get('pinned_classroom')!r}")
            if (cls.get("lecturer") or "") and cls["lecturer"] not in lecturers:
                problems.append(f"{label}: pinned, lecturer={cls.get('lecturer')!r}")
    return problems


def _references(cls, axis, value):
    """True if this class occupies the timetable *via* ``value`` on ``axis``."""
    placed_field, pinned_field = _AXIS_FIELDS[axis]
    if cls.get("placed") and cls.get(placed_field) == value:
        return True
    if cls.get("pinned") and cls.get(pinned_field) == value:
        return True
    return False


def _assert_seed_is_live(state, axis):
    """Prove the fixture built the schedule it claims, before anything is removed.

    Without this every "no orphan survives" assertion is satisfiable by a state
    in which nothing was ever placed. Measured, not theorised: stubbing
    ``mark_placed`` to a no-op makes ``test_removal_leaves_no_orphaned_placements``
    and ``test_pinned_and_locked_classes_are_reconciled_coherently`` pass on all
    four axes against a correct fix -- 8 of 19 tests green on an empty schedule.
    This is the ST-DATA-004 shape of the degenerate-input hole Phase 0 found in
    the optimizer tests (a schedule with zero placements has zero violations).
    """
    removed = REMOVED[axis]
    classes = state["classes"]
    assert len(classes) == len(_LAYOUTS[axis]), "fixture built the wrong class count"

    placed = tuple(i for i, c in enumerate(classes) if c["placed"])
    assert placed == VICTIM_IDX + PLACED_CONTROL_IDX, (
        f"fixture did not place the classes it declares: placed indices "
        f"{placed}, expected {VICTIM_IDX + PLACED_CONTROL_IDX}")
    pinned = tuple(i for i, c in enumerate(classes) if c["pinned"])
    assert pinned == (PINNED_VICTIM_IDX, PINNED_CONTROL_IDX), (
        f"fixture did not pin the classes it declares: pinned indices {pinned}")

    for i in VICTIM_IDX:
        assert _references(classes[i], axis, removed), (
            f"fixture class #{i} is supposed to be a victim of removing "
            f"{axis} {removed!r} but does not reference it")
    for i in PLACED_CONTROL_IDX + UNPLACED_CONTROL_IDX:
        assert not _references(classes[i], axis, removed), (
            f"fixture class #{i} is supposed to be a control but references "
            f"{axis} {removed!r}")

    assert classes[LOCKED_VICTIM_IDX]["protection"] == "locked"
    assert classes[LOCKED_CONTROL_IDX]["protection"] == "locked"
    assert _dangling(state) == [], (
        f"the fixture state is already inconsistent before any removal: "
        f"{_dangling(state)}")


def _assert_axis_removed(state, axis, value):
    """Guard: the edit really reached state, so no test can pass vacuously."""
    key = {"day": "days", "slot": "slots",
           "classroom": "classrooms", "lecturer": "lecturers"}[axis]
    assert value not in state[key], (
        f"SetupDialog OK did not remove {value!r} from state[{key!r}] "
        f"-- this test never exercised a removal")


# ══════════════════════════════════════════════════════════════════════════
#  1. No orphaned placements survive the setup change
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("axis", AXES)
def test_removal_leaves_no_orphaned_placements(window, seeded, monkeypatch, axis):
    """ST-DATA-004 — applying a setup that deletes a day/slot/room/lecturer must
    leave no class ``placed`` on an axis value that no longer exists.

    A failure means a normal UI edit silently produces a schedule referencing a
    deleted weekday, hour, room or teacher -- the corruption that ST-DATA-003
    then crashes on in analytics, export and reschedule.
    """
    removed = REMOVED[axis]
    state = seeded(axis)
    victims = _uids(state, VICTIM_IDX)

    applied = _apply_setup(window, monkeypatch, _remove_from_dialog(axis, removed))

    _assert_axis_removed(state, axis, removed)
    assert _dangling(state) == [], (
        f"removing {axis} {removed!r} left dangling references: "
        f"{_dangling(state)}")

    classes = _by_uid(state)
    for uid in victims:
        assert classes[uid]["placed"] is False, (
            f"class {classes[uid].get('class_code')} is still placed after its "
            f"{axis} {removed!r} was removed")

    _assert_no_crash(applied, f"removing {axis} {removed!r}")


# ══════════════════════════════════════════════════════════════════════════
#  2. The user is told, with a count
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("axis", AXES)
def test_removal_tells_the_user_how_many_classes_were_unplaced(
        window, seeded, channels, monkeypatch, axis):
    """ST-DATA-004 — dropping placements must be reported to the user with a count.

    A failure means the app silently deleted part of a finished timetable: the
    user unchecks one weekday and four lessons vanish with nothing on screen to
    say so.

    Channel-agnostic on purpose (message box, toast, warning log or ``logging``
    all count). The count asserted is the *total* number of classes that lose
    their placement, which is what the register asks for ("warn the user with
    counts"); an implementation that reports several split counts must still
    include the total.
    """
    removed = REMOVED[axis]
    state = seeded(axis)
    n = len(VICTIM_IDX)

    applied = _apply_setup(window, monkeypatch, _remove_from_dialog(axis, removed))
    emitted = channels.messages()

    _assert_axis_removed(state, axis, removed)
    said = _residual_messages(window, channels, emitted)
    assert said, (
        f"removing {axis} {removed!r} unplaced {n} classes and told the user "
        f"nothing at all")
    assert _mentions_count(said, n), (
        f"removing {axis} {removed!r} unplaced {n} classes; no notification "
        f"mentions that count. Messages seen: {said!r}")

    _assert_no_crash(applied, f"removing {axis} {removed!r}")


# ══════════════════════════════════════════════════════════════════════════
#  3. Unaffected classes keep their placements exactly
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("axis", AXES)
def test_unaffected_classes_keep_their_placements(
        window, seeded, monkeypatch, axis):
    """ST-DATA-004 — classes that do not reference the removed value must be
    left exactly as they were.

    A failure means the app threw away a whole term's work to clean up four
    lessons. This is the assertion that stops "reconciliation" from being
    implemented as "unplace everything and let the user re-run the optimizer".
    """
    removed = REMOVED[axis]
    state = seeded(axis)
    before = _snapshot(state)
    controls = _uids(state, PLACED_CONTROL_IDX + UNPLACED_CONTROL_IDX)
    placed_before = sum(1 for c in state["classes"] if c["placed"])

    applied = _apply_setup(window, monkeypatch, _remove_from_dialog(axis, removed))

    _assert_axis_removed(state, axis, removed)
    classes = _by_uid(state)
    for uid in controls:
        after = {f: classes[uid].get(f) for f in PLACEMENT_FIELDS}
        assert after == before[uid], (
            f"class {classes[uid].get('class_code')} does not reference "
            f"{axis} {removed!r} but its placement changed")

    placed_after = sum(1 for c in state["classes"] if c["placed"])
    assert placed_after == placed_before - len(VICTIM_IDX), (
        f"expected exactly {len(VICTIM_IDX)} classes to lose their placement, "
        f"got {placed_before - placed_after}")

    _assert_no_crash(applied, f"removing {axis} {removed!r}")


# ══════════════════════════════════════════════════════════════════════════
#  4. Pinned / locked classes are handled coherently
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("axis", AXES)
def test_pinned_and_locked_classes_are_reconciled_coherently(
        window, seeded, monkeypatch, axis):
    """ST-DATA-004 — a pin or a lock must not keep a class in a cell that was
    deleted.

    "Coherently" is defined here as:

    * **pinned** — the pin is a user promise about a specific cell. When that
      cell stops existing the promise is unsatisfiable, so the pin is cleared
      (``pinned=False``, the dangling ``pinned_*`` coordinate set to ``None``)
      and the class is unplaced. Not invented for the test:
      ``ui/day_keys.py:81-90`` already implements exactly this for the day axis;
      the fix generalizes it to slots, rooms and lecturers.
    * **locked** (``protection="locked"``) — the lock says *the optimizer may
      not move this*, which cannot be read as *this may sit in a deleted slot*.
      The class is unplaced like any other, but ``protection`` is user data and
      must be preserved so the lock still applies once it is re-placed.

    A failure means either a ghost pin that re-creates the orphan on the next
    optimizer run (``effective_day``/``effective_time`` read the pin first, so a
    pinned-but-unplaced class still claims a cell), or a silently discarded
    protection setting.

    The pinned victim on the *lecturer* axis is the one genuinely arguable case:
    its day/time/room are all still valid, only the teacher is gone. It is
    asserted the same way for consistency with the invariant "a class may not
    occupy the timetable unless every axis value it references exists" -- and is
    flagged in the module report as a maintainer call.
    """
    removed = REMOVED[axis]
    state = seeded(axis)
    pinned_victim, locked_victim = _uids(
        state, (PINNED_VICTIM_IDX, LOCKED_VICTIM_IDX))
    controls = _uids(state, (PINNED_CONTROL_IDX, LOCKED_CONTROL_IDX))
    before = _snapshot(state)

    applied = _apply_setup(window, monkeypatch, _remove_from_dialog(axis, removed))

    _assert_axis_removed(state, axis, removed)
    classes = _by_uid(state)

    pv = classes[pinned_victim]
    assert pv["placed"] is False, "pinned victim kept a placement on a deleted axis"
    assert pv["pinned"] is False, (
        f"pinned victim is still pinned after its {axis} {removed!r} was removed "
        f"(pinned_day={pv.get('pinned_day')!r}, "
        f"pinned_time={pv.get('pinned_time')!r}, "
        f"pinned_classroom={pv.get('pinned_classroom')!r}, "
        f"lecturer={pv.get('lecturer')!r})")
    for field, axis_key in _PIN_AXIS_KEY.items():
        value = pv.get(field)
        assert value is None or value in state[axis_key], (
            f"pinned victim kept a dangling {field}={value!r}")

    lv = classes[locked_victim]
    assert lv["placed"] is False, "locked victim kept a placement on a deleted axis"
    assert lv["protection"] == "locked", (
        "reconciliation discarded the user's protection setting")

    # A fix must not treat "pinned/locked" as "safe to wipe" either.
    for uid in controls:
        after = {f: classes[uid].get(f) for f in PLACEMENT_FIELDS}
        assert after == before[uid], (
            f"unaffected pinned/locked class {classes[uid].get('class_code')} "
            f"was modified by removing {axis} {removed!r}")

    _assert_no_crash(applied, f"removing {axis} {removed!r}")


# ══════════════════════════════════════════════════════════════════════════
#  5. Guard: a setup change that removes nothing must change nothing
# ══════════════════════════════════════════════════════════════════════════

def _add_axes(dlg):
    """A user who only *adds*: one weekday, one slot, one room, one lecturer."""
    from PyQt6.QtWidgets import QTableWidgetItem

    dlg.day_buttons["saturday"].setChecked(True)
    lines = [s.strip() for s in dlg.slots_text.toPlainText().splitlines() if s.strip()]
    dlg.slots_text.setPlainText("\n".join(lines + ["13:00"]))
    dlg._add_room_row()
    dlg.rooms_table.setItem(dlg.rooms_table.rowCount() - 1, 0,
                            QTableWidgetItem("R004"))
    dlg._add_lec_row()
    dlg.lec_table.setItem(dlg.lec_table.rowCount() - 1, 0,
                          QTableWidgetItem("Lect-004"))


_NO_OP_CASES = {
    "no_edits": lambda dlg: None,
    "only_additions": _add_axes,
}


@pytest.mark.parametrize("case", sorted(_NO_OP_CASES))
def test_setup_without_removals_changes_and_warns_nothing(
        window, seeded, channels, monkeypatch, case):
    """ST-DATA-004 — pressing OK without deleting anything (or after only
    *adding* axes) must leave every placement untouched and warn about nothing.

    A failure means the fix over-fires: a user who opens Setup just to add a
    Saturday, or who opens it and confirms without editing, loses placements or
    is frightened by a bogus "N classes were unplaced" warning.

    "Warns about nothing" is asserted as "nothing the setup change said carries
    a number", after subtracting the warning panel's usual per-repaint chatter.
    A reconciliation warning must state a count (test 2 asserts exactly that);
    an innocuous "settings saved" confirmation does not, so a maintainer who
    adds one does not have to touch this test.
    """
    state = seeded("day")
    before = _snapshot(state)
    placed_before = sum(1 for c in state["classes"] if c["placed"])

    applied = _apply_setup(window, monkeypatch, _NO_OP_CASES[case])
    emitted = channels.messages()

    # Guard: the OK handler really ran and really wrote the setup back.
    assert state["days"][:len(DAYS)] == DAYS
    assert state["slots"][:len(SLOTS)] == SLOTS
    assert state["classrooms"][:len(ROOMS)] == ROOMS
    assert state["lecturers"][:len(LECTURERS)] == LECTURERS
    if case == "only_additions":
        assert "saturday" in state["days"] and "13:00" in state["slots"]
        assert "R004" in state["classrooms"] and "Lect-004" in state["lecturers"]

    assert _snapshot(state) == before, "a no-removal setup change moved placements"
    assert sum(1 for c in state["classes"] if c["placed"]) == placed_before

    said = _residual_messages(window, channels, emitted)
    numeric = [m for m in said if re.search(r"\d", m)]
    assert not numeric, (
        f"a setup change that removed nothing still warned the user: {numeric!r}")

    _assert_no_crash(applied, "a setup change that removed nothing")


# ══════════════════════════════════════════════════════════════════════════
#  6. Guard: an unassigned lecturer is not a deleted lecturer
# ══════════════════════════════════════════════════════════════════════════

def test_a_class_with_no_lecturer_survives_an_unrelated_removal(
        window, seeded, channels, monkeypatch):
    """ST-DATA-004 — removing a *classroom* must not unplace a class that sits in
    a different room merely because it has no lecturer assigned.

    A failure means a user who deletes one room loses every lesson they had not
    yet assigned a teacher to -- silent data loss caused by the fix rather than
    by the bug.

    This is not a hypothetical. ``lecturer`` is ``""`` in ``new_class()``
    (``core/models.py:436``) and the core deliberately reads blank as "no
    lecturer constraint" (``core/logic.py:266-267`` ``if lecturer:``), so an
    unassigned teacher is a supported state, not an orphan. A reconciliation
    written as ``cls["lecturer"] in state["lecturers"]`` -- which is exactly the
    candidate in the module's implementation plan -- treats every one of those
    classes as referencing a deleted lecturer, because ``""`` is never in
    ``state["lecturers"]`` (``SetupDialog._ok`` skips blank rows). Verified: that
    candidate fails this test; skipping the lecturer check for blank passes it.

    The class chosen is a *control* on the classroom axis (it is in ``R001``,
    not the deleted ``R003``), so nothing about the removal touches it. This
    test therefore passes both before and after the fix, on purpose: it is a
    guard against a specific wrong fix, not a fail-now assertion.
    """
    axis, removed = "classroom", REMOVED["classroom"]
    state = seeded(axis)
    blank = state["classes"][PLACED_CONTROL_IDX[0]]
    assert blank["placed"] and blank["placed_classroom"] != removed
    blank["lecturer"] = ""
    uid = blank["class_uid"]
    before = {f: copy.deepcopy(blank.get(f)) for f in PLACEMENT_FIELDS}

    applied = _apply_setup(window, monkeypatch, _remove_from_dialog(axis, removed))

    _assert_axis_removed(state, axis, removed)
    after = {f: _by_uid(state)[uid].get(f) for f in PLACEMENT_FIELDS}
    assert after == before, (
        "a placed class with no lecturer assigned was disturbed by removing "
        f"classroom {removed!r}; an unassigned teacher is not a deleted teacher "
        f"(before={before!r}, after={after!r})")

    _assert_no_crash(applied, f"removing {axis} {removed!r}")
