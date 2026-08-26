"""A repaint must not be a disk write, and a click must not be a repaint.

ST-PERF-002 (High) · ``app.py:2098-2102`` (``refresh_grid``),
``app.py:1951-1986`` (``_auto_save``), ``app.py:1887-1926``
(``_read_settings_container``)
    ``refresh_grid`` ends with ``self._auto_save()``, and ``_auto_save``
    *decrypts, re-serialises, re-encrypts and rewrites the entire settings
    container* every single time. The audit measured one such round trip at
    16.8 ms on the 80-class state (74 KB container) and 33.6 ms at 250 classes
    (232 KB), inside a ``refresh_grid`` that itself costs 0.65 s → 2.5-4.7 s.
    Measured again on this tree, unchanged: **20 refreshes produce exactly 20
    ``save_encrypted`` calls** — 467 KB of AES-GCM traffic at 25 classes,
    1.5 MB at 80, 4.6 MB at 250, to persist a state that never changed.

ST-UI-009 (Medium) · ``app.py:3269-3322`` (``_apply_class_selection``),
``app.py:3233-3244`` (``_clear_class_selection``), ``app.py:3367-3379``
(``_select_empty_slot``), ``app.py:1484`` (``unplaced_list.itemSelectionChanged``)
    Every selection — clicking a lesson, clicking an empty slot, clicking a row
    in the Unplaced list — runs a full ``_refresh_open_slots``: the panel's
    entire widget tree is torn down and rebuilt and the whole validity analysis
    is re-run. Selecting a class **changes nothing that is ever saved**, so none
    of the persistence machinery has any business running; and re-clicking what
    is *already* selected changes nothing at all, yet costs the same rebuild
    (measured on this tree: 39 ms for a re-click at 80 classes, 81 ms for
    re-clicking an already-selected empty slot).

Why this matters to a user: at 250 classes the audit clocked 306-563 ms of
latency on a **selection**. Half a second of frozen UI to draw a blue border
around the thing they just clicked, and a quarter of a megabyte of encryption
to record that they clicked it.

What "correct" means here (the policy this module encodes)
----------------------------------------------------------
The roadmap says "debounce/delta autosave". Debouncing is the easy half; the
half that can hurt is that **a debounce which drops the final write is a
data-loss bug strictly worse than the slowness it fixes**. So the policy
asserted below has three parts, and section 3 exists to make the third
non-negotiable:

1. **Coalesced.** A burst of refreshes produces at most one write, not one per
   refresh. Asserted as a *count*, never as a latency — see the conventions.
2. **Delta.** A refresh that changed nothing writes nothing. This is what makes
   selection free: no state change, no fingerprint change, no container rewrite.
3. **Durable.** The pending write always lands: on its own once the user pauses,
   and unconditionally on ``closeEvent``. Section 3 asserts all three routes and
   deliberately does not care which mechanism delivers them.

And one thing the fix must **not** do. "Skip side-panel recompute on
selection-only changes" cannot be read as "the open-slots panel never recomputes
on selection": that panel is *deliberately* contextual — with one class selected
it answers "where can this class legally go?" (app.py:3022-3040) — so the
cheapest way to zero every count in section 2 is also the way that deletes a
feature. What is waste is a recompute for a selection that **did not change**,
and that is what section 2 asserts, alongside a guard
(``..._still_filters_the_open_slots_panel_to_it``) that the contextual mode
still works.

One honest correction to the register while we are here. ST-UI-009 says a
selection triggers "a full scene rebuild + encrypted autosave". Measured on this
tree — and on the audit's own baseline commit, so this is not something Phase 0
or 1 changed — it does neither: ``_select_class_gfx``,
``_select_empty_slot`` and the Unplaced list's ``itemSelectionChanged`` reach
``_refresh_open_slots`` and nothing else. The 306-563 ms is that one panel
rebuild plus ``find_valid_options`` over the whole grid. The two tests asserting
"a selection writes nothing" therefore **pass today**; they are kept because the
obvious way to implement a debounce is a dirty flag, and the obvious way to get
it wrong is to set that flag somewhere every repaint path reaches — at which
point a click would start writing, and nothing else in the suite would notice.

Phase 1 rewrote ``_auto_save`` (ST-DATA-005/ST-DATA-014): it returns a bool,
reports failures through ``_report_settings_problem``, and never writes a
container it failed to read. This module must not let that regress, so
section 4 pins the parts of that contract a debounce could plausibly break —
``_auto_save()`` called directly still writes *immediately and synchronously*,
and a failing write on close still reaches the user. ``tests/
test_settings_recovery.py`` owns the rest of that contract and already drives
``_auto_save()`` directly for exactly this reason.

Conventions
-----------
* **fail-now / pass-after**, no ``xfail``: both findings are being fixed in
  Phase 2.
* **No wall-clock assertion anywhere in this module.** This was measured, not
  assumed. The obvious timing test — "refresh 20 costs what refresh 1 did" —
  was run four times against this unchanged tree on the ``small`` preset and
  produced ratios of 1.56, 2.01, 2.60 and 3.61. A threshold low enough to fail
  reliably today (~1.5) is one a *fixed* tree would trip on a loaded CI box,
  which is how a performance test gets deleted by the next person. Every
  assertion here is therefore a **count**: ``save_encrypted`` calls, side-panel
  recomputes, container rewrites. Those are exact, machine-independent, and are
  the finding itself rather than a proxy for it. The one timing figure that
  *does* belong to Phase 2's completion criteria ("a single refresh on 250
  classes is < 300 ms") is a wall-clock statement about the warnings pass and
  lives with its owner in ``tests/test_warning_log_growth.py``.
* **Scale is irrelevant to a count**, so most of the module runs at a handful of
  classes and finishes in seconds. The 250-class cases in section 5 exist to
  show the same counts at the scale the finding was measured at. They are
  **not** marked ``slow``: measured on this tree they cost 1.9 s, 0.4 s and
  0.5 s including fixture setup — 3.7 s for all three — and ``pytest.ini``
  reserves ``slow`` for "more than ~10 s". Marking them would have excluded the
  only department-scale coverage in the module from the CI lane
  (``-m "not slow"``), which is precisely where "a fix that quietly
  special-cases small states" would slip through.
* Every count assertion carries a **non-vacuity guard**: a test that would pass
  because the fixture rendered nothing, because the click did not actually
  select anything, or because ``refresh_grid`` never reached the code being
  counted, fails loudly instead. This was checked by stubbing, not asserted —
  see the discrimination list below.

A blind spot to know about before adding counters here
------------------------------------------------------
``_Counters`` patches **instance** attributes, which is right for everything
reached through a normal ``self.x()`` call chain but cannot see a call delivered
through a Qt signal that was connected to a *bound method* at ``__init__`` time:
PyQt captured the original function then, and a later attribute rebind is
invisible to it. Exactly one counted name is connected that way —
``self.unplaced_list.itemSelectionChanged.connect(self._refresh_open_slots)``
(app.py:1484) — so ``..._selecting_in_the_unplaced_list_...`` deliberately does
not count that edge and proves the signal fired by its observable effect
instead. Everything the unplaced path could newly reach (``refresh_grid`` and
its callees) is still counted, because those go through ``self.``.

Scope boundary with the sibling Phase-2 modules
-----------------------------------------------
``tests/test_warning_log_growth.py`` owns ST-PERF-003/006: unbounded
``warning_log._messages``, the O(n^2) HTML rebuild, and the open-slots panel
rebuilding on an *unchanged refresh*. Nothing here re-asserts any of that. This
module only ever asks what a **selection** costs and what a **refresh** writes.
Where the two touch — ``refresh_grid`` calls ``_refresh_open_slots`` twice per
call, once via ``_render_current_tab``'s selection clear and once via
``_update_side_panels`` — is noted in the implementation plan, not asserted
twice.

Measured against the code as it stands (Windows, ``.venv-audit``): **7 of the 19
tests fail**. The module runs in 11.5 s unfixed and 16 s once the fix is
simulated (20 s / 58 s with eight CPU burners pinned against it — same verdicts
in every case, which is the whole reason the assertions are counts).

Discrimination — every line below was measured by patching the production
methods at runtime from a scratch pytest plugin; the production tree was never
modified. Against:

* the full fix (debounced + delta autosave with a guaranteed flush, plus the two
  selection short-circuits) → 19/19 pass, and the 57 tests in
  ``test_settings_recovery`` / ``test_setup_reconcile`` / ``test_import_ui_flow``
  / ``test_state_transactions`` plus the 59 in ``test_grid_integrity`` stay
  green.
* the autosave half only → the three re-selection tests still fail.
* the selection half only → the four autosave tests still fail. No cross-talk.
* a ``closeEvent`` that accepts without writing → ``..._closing_the_window_...``,
  ``..._a_deletion_is_persisted_...``, ``..._moving_a_placed_lesson_...``,
  ``..._a_change_outside_the_class_list_...`` and
  ``..._failing_save_on_close_...``. (Note that a ``closeEvent`` still calling
  ``_auto_save()`` directly is *not* broken and correctly passes — the bug is
  losing the write, not which name performs it.)
* a debounce that arms no timer, so only the close ever flushes → five
  failures, led by ``..._an_edit_is_persisted_without_any_further_user_action``.
  This is the user who walks away, and it is worth its own variant because it
  looks completely correct in interactive use and only loses work on a crash.
* a debounce with no delta check ("defer, don't eliminate") →
  ``..._repainting_an_unchanged_schedule_...`` is the single extra failure.
* a delta check whose fingerprint is taken **before** ``_auto_save``'s
  ``normalize_state_*`` call → the same single failure, because normalization
  mutates ``state_data`` and the fingerprint therefore never matches.
* **a delta check whose fingerprint is too coarse.** Three separate variants —
  fingerprinting only the class *names*, only ``len(state["classes"])``, and
  only ``state["classes"]`` — each pass every autosave and durability test in
  the original draft of this module. They are caught by
  ``..._moving_a_placed_lesson_to_another_slot_is_persisted`` and
  ``..._a_change_outside_the_class_list_is_persisted``, which exist for exactly
  that reason: the two commonest DERSİS edits (drag a lesson; change the room
  list in Setup) change no class name and no class count, and one of them
  changes nothing inside ``state["classes"]`` at all.
* the lazy "fix" that simply deletes ``_refresh_open_slots()`` from the
  selection path → caught by ``..._still_filters_the_open_slots_panel_to_it``,
  and by that test alone, which is why that guard exists.
* the code under test stubbed out entirely (selection a no-op; open-slots a
  no-op; ``refresh_grid`` a no-op; ``refresh_grid`` no longer saving) → each
  caught, by 11, 9, 13 and 8 tests respectively. Nothing here passes against a
  tree that does nothing.
"""
import copy
import os
import time

import pytest

pytestmark = [pytest.mark.ui]


# 20 refreshes is roughly a minute of ordinary editing: every add, drag, delete,
# unplace, setup change and undo calls refresh_grid exactly once.
_REFRESHES = 20

# Ceiling on writes for a burst of _REFRESHES refreshes. Not 1, because the
# policy is deliberately mechanism-agnostic: a debounce timer that happens to
# elapse in the middle of the burst is a legitimate implementation and would
# write twice. 20 is the number this must not be.
_MAX_WRITES_PER_BURST = 2

# How long "eventually" is allowed to be for an un-nudged pending write. This is
# a *timeout*, not a latency assertion: a test using it fails only if the write
# never lands, and it returns the moment it does, so it cannot flake on a slow
# runner and costs nothing on a fast one. The value is a judgement about data
# loss, not about speed — anything longer than a few seconds means a user who
# closes the lid mid-thought loses work, which is the failure mode section 3
# exists to prevent.
_FLUSH_TIMEOUT_S = 10.0

# How long a *negative* wait watches for a write that must never come. Unlike
# the timeout above this one is always paid in full, so it is kept short; it
# only has to outlast a sane debounce interval, and a debounce longer than this
# is caught by section 3 instead.
_QUIET_WINDOW_S = 4.0


# ── Helpers ─────────────────────────────────────────────────────────────────

def _flush_deleted(qapp):
    """Actually run the deletions ``deleteLater()`` only *posts*.

    ``QApplication.processEvents()`` skips ``DeferredDelete`` events posted
    outside the current loop level, so without this every window this module
    builds would stay alive for the rest of the session and each successive
    ``SchedulerApp()`` would get slower.
    """
    from PyQt6.QtCore import QCoreApplication, QEvent

    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _place(cls, day, slot, room):
    cls["placed"] = True
    cls["placed_day"] = day
    cls["placed_time"] = slot
    cls["placed_classroom"] = room


def _place_greedily(state, room_first=True):
    """Pack placeable classes into the first free room-slot, deterministically.

    Deliberately not the optimizer: this module is about click and repaint cost,
    and a solver run would add minutes of runtime plus a dependency on
    ST-SCHED-013's seeding. Packing room-major means the *first* classroom fills
    up first, which matters because tab 0 is filtered by classroom and defaults
    to index 0 — without that the timetable this module clicks on would be
    empty and every selection assertion would be vacuous.
    """
    from scheduler_app.core.logic import total_duration

    occupied = set()
    n_slots = len(state["slots"])
    placed = 0
    rooms = state["classrooms"] if room_first else list(reversed(state["classrooms"]))
    for cls in state["classes"]:
        if cls["pinned"] or cls.get("location_type") != "face_to_face":
            continue
        span = total_duration(cls)
        done = False
        for room in rooms:
            for day in state["days"]:
                for start in range(0, n_slots - span + 1):
                    keys = [(day, state["slots"][start + k], room)
                            for k in range(span)]
                    if any(k in occupied for k in keys):
                        continue
                    occupied.update(keys)
                    _place(cls, day, state["slots"][start], room)
                    placed += 1
                    done = True
                    break
                if done:
                    break
            if done:
                break
    return placed


def _lesson_items(window):
    """Every clickable lesson graphic in the visible timetable tab."""
    scene = window.grid_view1.scene()
    if scene is None:
        return []
    return [it for it in getattr(scene, "lesson_items", []) or [] if it is not None]


def _empty_slot_items(window):
    """Every clickable empty-slot graphic in the visible timetable tab."""
    from scheduler_app.ui.renderer import EmptySlotItem

    scene = window.grid_view1.scene()
    if scene is None:
        return []
    return [it for it in scene.items() if isinstance(it, EmptySlotItem)]


class _Spy:
    """A counting wrapper that still calls through to the real callable."""

    def __init__(self, real):
        self._real = real
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self._real(*args, **kwargs)


class _Counters:
    """Call counts for the side-panel and repaint entry points of one window.

    Patched onto the *instance*, so the production class is untouched and two
    windows in one session cannot see each other's counts.
    """

    NAMES = ("_render_current_tab", "_update_side_panels", "_refresh_open_slots",
             "_refresh_warnings", "_refresh_unplaced_panel",
             "_run_auto_negotiation", "_auto_save")

    def __init__(self, window):
        self._window = window
        self._spies = {}
        for name in self.NAMES:
            spy = _Spy(getattr(window, name))
            self._spies[name] = spy
            setattr(window, name, spy)

    def snapshot(self):
        return {name: spy.calls for name, spy in self._spies.items()}

    def since(self, mark):
        return {name: spy.calls - mark[name] for name, spy in self._spies.items()}

    def __getitem__(self, name):
        return self._spies[name].calls


class _SaveSpy:
    """Counts writes of the *settings container* specifically.

    ``storage.save_encrypted`` also writes saved timetables, negotiation
    settings and the learner's model; counting those as autosaves would make
    every number here unreadable. Only writes whose destination is the window's
    own ``_config_path`` are counted, and the container's size on disk is
    tracked alongside so the failure message can state the finding in the unit
    that hurts — bytes of AES-GCM per click.
    """

    def __init__(self, config_path):
        self.config_path = os.path.abspath(config_path)
        self.calls = 0
        self.bytes_written = 0
        self.other_calls = 0

    def install(self, monkeypatch):
        from scheduler_app import storage
        import scheduler_app.storage.storage as storage_mod

        real = storage_mod.save_encrypted

        def counting_save(data, path, *args, **kwargs):
            result = real(data, path, *args, **kwargs)
            if os.path.abspath(path) == self.config_path:
                self.calls += 1
                try:
                    self.bytes_written += os.path.getsize(path)
                except OSError:
                    pass
            else:
                self.other_calls += 1
            return result

        monkeypatch.setattr(storage, "save_encrypted", counting_save)
        monkeypatch.setattr(storage_mod, "save_encrypted", counting_save)
        return self

    def describe(self, n_refreshes):
        kb = self.bytes_written / 1024.0
        return (f"{self.calls} encrypted rewrites of the settings container for "
                f"{n_refreshes} refreshes ({kb:.0f} KB written)")


def _container(window):
    """The decrypted settings container as it currently sits on disk."""
    from scheduler_app import storage

    if not os.path.exists(window._config_path):
        return None
    return storage.load_encrypted(window._config_path)


def _persisted_class_names(window):
    data = _container(window)
    if not data:
        return []
    return [c.get("name") for c in (data.get("state") or {}).get("classes", [])]


def _persisted_placement(window, name):
    """Where the container on disk currently believes *name* is placed."""
    data = _container(window)
    if not data:
        return None
    for c in (data.get("state") or {}).get("classes", []):
        if c.get("name") == name:
            return (c.get("placed_day"), c.get("placed_time"),
                    c.get("placed_classroom"))
    return None


def _persisted_classrooms(window):
    data = _container(window)
    if not data:
        return []
    return list((data.get("state") or {}).get("classrooms", []))


def _settle(window, qapp, timeout=_FLUSH_TIMEOUT_S, until=None):
    """Spin the Qt event loop until *until* is true, or the timeout expires.

    This is how the *user* experiences a debounce — they stop typing and the
    work gets saved — so it is the only way to test one without naming the
    mechanism that implements it. A timer, an idle hook and an immediate write
    all satisfy it.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.02)
    qapp.processEvents()
    return bool(until is None or until())


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def message_boxes(monkeypatch):
    """Neutralize every modal a refresh, a selection or a close can raise.

    An unpatched modal blocks the whole suite under the offscreen platform, so
    this is a hard requirement. ``_report_settings_problem`` raises one,
    ``closeEvent`` raises another when autosave has been made to fail, and
    ``_show_toast`` starts a 3 s ``QTimer`` that would outlive the window the
    test tears down — so toasts are recorded rather than constructed.
    """
    from PyQt6.QtWidgets import QDialog, QMessageBox

    from scheduler_app.ui.app import SchedulerApp
    from scheduler_app.ui.tier_enforcement import UpgradeDialog

    for name, ret in (("information", QMessageBox.StandardButton.Ok),
                      ("warning", QMessageBox.StandardButton.Ok),
                      ("critical", QMessageBox.StandardButton.Ok),
                      ("question", QMessageBox.StandardButton.Yes)):
        monkeypatch.setattr(
            QMessageBox, name, staticmethod(lambda *a, _r=ret, **k: _r))
    monkeypatch.setattr(
        UpgradeDialog, "exec", lambda self: QDialog.DialogCode.Rejected.value)

    toasts = []
    monkeypatch.setattr(
        SchedulerApp, "_show_toast",
        lambda self, message, kind="info": toasts.append((message, kind)))
    return toasts


_TIER_REGISTRIES = (
    "_gated_widgets", "_gated_actions", "_on_tier_changed",
    "_export_submenu_refreshers",
)


@pytest.fixture
def window(qapp, dersis_home, message_boxes, monkeypatch):
    """A real, never-shown ``SchedulerApp`` with a grid and no classes.

    The first-run controller is disarmed (a ``QTimer`` in ``__init__`` would
    otherwise pop the setup wizard *and* rewrite the settings file behind the
    test's back, which would corrupt every write count in this module), and the
    licence tier is pinned so no entity gate can short-circuit a repaint.

    Isolation note: every ``SchedulerApp`` registers gated ``QAction``s and a
    tier-change callback into the process-wide ``TierEnforcement`` singleton and
    never unregisters them, so the registries are snapshotted and restored.
    """
    from scheduler_app.plans import TIER_INSTITUTIONAL
    from scheduler_app.ui.day_keys import DAY_KEYS
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

    win = SchedulerApp()
    state = win.state_data
    state["days"] = list(DAY_KEYS[:5])
    state["slots"] = ["09:00", "10:00", "11:00", "12:00"]
    state["classrooms"] = ["R001", "R002"]
    state["classroom_capacities"] = {"R001": 40, "R002": 40}
    state["lecturers"] = ["Lect-1", "Lect-2"]
    state["years"] = {"Year-1": ["A"]}
    try:
        yield win
    finally:
        win.close()
        win.deleteLater()
        _flush_deleted(qapp)
        enforcer._tier_slug, enforcer._tier_confirmed = prev_slug, prev_confirmed
        for name, value in prev_registries.items():
            setattr(enforcer, name, value)


@pytest.fixture
def clicky(window):
    """``window`` plus a timetable that can actually be clicked on.

    Four one-hour lessons placed into R001 — the classroom tab 0 filters to by
    default, so they are real ``LessonItem`` graphics a selection can land on —
    and one class that cannot be placed anywhere (its ``allowed_days`` is
    Saturday on a Mon-Fri grid). The unplaceable one is what makes
    ``_run_auto_negotiation`` do per-class work, so "a selection does not run
    the negotiation pass" is an assertion about something rather than about
    nothing.
    """
    from scheduler_app.core.models import new_class
    from scheduler_app.ui.day_keys import DAY_KEYS

    state = window.state_data

    def add(name, lecturer="Lect-1", **fields):
        cls = new_class()
        cls["name"] = name
        cls["class_code"] = name.replace(" ", "")
        cls["lecturer"] = lecturer
        cls["targets"] = [{"year": "Year-1", "branch": "A"}]
        cls["duration"] = 1
        cls.update(fields)
        state["classes"].append(cls)
        return cls

    for i in range(4):
        _place(add(f"Ders {i + 1}"), DAY_KEYS[i], state["slots"][0], "R001")
    add("Ders Imkansiz", lecturer="Lect-2", allowed_days=["saturday"])
    window.refresh_grid()
    return window


@pytest.fixture
def large(window, make_preset):
    """``window`` loaded with the audit's 250-class state, mostly placed.

    The scale ST-PERF-002 (33.6 ms and 232 KB per autosave) and ST-UI-009
    (306-563 ms per selection) were measured at. ``state_data`` is updated in
    place rather than replaced, because the ``SchedulingWorkflow`` built in
    ``__init__`` holds a reference to that exact dict.
    """
    state = make_preset("large")
    window.state_data.clear()
    window.state_data.update(state)
    placed = _place_greedily(window.state_data)
    assert placed > 100, f"fixture placed only {placed} classes"
    unplaced = [c for c in window.state_data["classes"]
                if not c["placed"] and not c["pinned"]]
    # Some classes must stay unplaced, otherwise _run_auto_negotiation returns
    # at its first line and every "a selection did not run the negotiation
    # pass" assertion below would be true of an empty pass.
    assert len(unplaced) > 10, (
        f"only {len(unplaced)} classes are unplaced; the negotiation pass "
        "would be a no-op and the selection assertions vacuous")
    window.refresh_grid()
    return window


@pytest.fixture
def saves(window, monkeypatch):
    """Counts writes to the settings container from this point on."""
    return _SaveSpy(window._config_path).install(monkeypatch)


# ══════════════════════════════════════════════════════════════════════════
#  1. ST-PERF-002 — a repaint is not a disk write
# ══════════════════════════════════════════════════════════════════════════

def test_a_burst_of_refreshes_does_not_rewrite_the_settings_file_each_time(
        clicky, saves):
    """ST-PERF-002 — 20 refreshes must not mean 20 encrypted container rewrites.

    A failure means every click the user makes pays for a full decrypt,
    re-serialise, re-encrypt and rewrite of their entire schedule — 16.8 ms at
    80 classes, 33.6 ms at 250 — to persist a state that in most cases did not
    change at all.
    """
    for _ in range(_REFRESHES):
        clicky.refresh_grid()

    assert saves.calls < _REFRESHES, (
        f"autosave is still once per refresh: {saves.describe(_REFRESHES)}")
    assert saves.calls <= _MAX_WRITES_PER_BURST, (
        f"a burst of {_REFRESHES} refreshes must coalesce into at most "
        f"{_MAX_WRITES_PER_BURST} writes; got {saves.describe(_REFRESHES)}")


def test_write_count_does_not_scale_with_the_refresh_count(clicky, saves):
    """ST-PERF-002 — doubling the number of refreshes must not double the writes.

    The trend, not a constant: whatever the debounce interval turns out to be,
    the number of container rewrites must be a function of how much the user
    *changed*, never of how many times the screen was repainted. Stated as a
    ratio so it stays true on a fast box and a slow one alike.

    A failure means the app's disk traffic is proportional to its frame count,
    which is what turned an idle DERSİS window into a machine writing a quarter
    of a megabyte of ciphertext per mouse click.
    """
    for _ in range(_REFRESHES):
        clicky.refresh_grid()
    after_first = saves.calls

    for _ in range(_REFRESHES):
        clicky.refresh_grid()
    after_second = saves.calls

    assert after_second - after_first <= _MAX_WRITES_PER_BURST, (
        f"the second burst of {_REFRESHES} refreshes added "
        f"{after_second - after_first} more container rewrites on top of the "
        f"first burst's {after_first}; writes are tracking repaints "
        f"({saves.describe(2 * _REFRESHES)})")


def test_repainting_an_unchanged_schedule_writes_nothing_at_all(
        clicky, saves, qapp):
    """ST-PERF-002 — "delta autosave": no change, no write.

    Once whatever is pending has been allowed to land, further refreshes of a
    state nobody touched must produce **zero** writes. This is the half of the
    fix that makes selection free — a selection changes nothing persistent, so
    there is nothing for the persistence layer to do.

    A failure means the debounce only delayed the pointless rewrites instead of
    eliminating them: the user still pays for the full encrypt-and-write cycle,
    just on a timer.
    """
    clicky.refresh_grid()
    landed = _settle(clicky, qapp, until=lambda: saves.calls >= 1)
    assert landed, (
        "the fixture's own state never reached disk, so 'no further writes' "
        "below would pass for the wrong reason")
    baseline = saves.calls

    for _ in range(_REFRESHES):
        clicky.refresh_grid()

    # Deterministic half: the burst itself, with no event loop in between.
    assert saves.calls == baseline, (
        f"{saves.calls - baseline} container rewrites for {_REFRESHES} "
        f"refreshes that changed nothing whatsoever "
        f"({saves.describe(_REFRESHES)})")

    # Timed half: give any pending debounce its chance to fire anyway.
    _settle(clicky, qapp, timeout=_QUIET_WINDOW_S)
    assert saves.calls == baseline, (
        f"{saves.calls - baseline} container rewrites landed within "
        f"{_QUIET_WINDOW_S:.0f}s of {_REFRESHES} refreshes that changed "
        f"nothing; the writes were deferred, not eliminated")


# ══════════════════════════════════════════════════════════════════════════
#  2. ST-UI-009 — a selection is not an edit
# ══════════════════════════════════════════════════════════════════════════

def test_selecting_a_lesson_writes_nothing_and_rebuilds_nothing(clicky, saves):
    """ST-UI-009 — clicking a lesson must not save, repaint the scene, or re-run
    the warnings and negotiation passes.

    Selecting a class records nothing that outlives the click. A failure means
    the user pays a full scene rebuild and an encrypted container rewrite —
    306-563 ms at 250 classes — to have a blue border drawn around the lesson
    they just clicked on.
    """
    items = _lesson_items(clicky)
    assert items, "the fixture rendered no lesson items; nothing to click"

    counters = _Counters(clicky)
    mark = counters.snapshot()
    clicky._select_class_gfx(items[0].cls, items[0], modifiers=None)
    delta = counters.since(mark)

    # Non-vacuity: a click that selects nothing would score zero on every
    # counter below and this test would be the loudest thing in the module
    # agreeing that a broken selection is free.
    assert clicky._selected_class is items[0].cls, (
        "the click did not select the lesson at all, so every count below "
        "would be zero for the wrong reason")

    assert saves.calls == 0, (
        f"selecting one lesson rewrote the encrypted settings container "
        f"{saves.calls} time(s) ({saves.bytes_written} bytes)")
    assert delta["_render_current_tab"] == 0, (
        "selecting a lesson rebuilt the whole timetable scene "
        f"({delta['_render_current_tab']} rebuilds)")
    assert delta["_refresh_warnings"] == 0 and delta["_run_auto_negotiation"] == 0, (
        "selecting a lesson re-ran the warnings/negotiation pass "
        f"(the audit's 4.5 s at 250 classes): {delta}")
    assert delta["_refresh_unplaced_panel"] == 0, (
        "selecting a lesson rebuilt the Unplaced panel, whose contents cannot "
        "depend on the selection")
    assert delta["_refresh_open_slots"] <= 1, (
        "selecting a lesson recomputed the open-slots panel "
        f"{delta['_refresh_open_slots']} times; the panel is contextual on the "
        "selection, so one recompute is correct and two is waste")


def test_selecting_a_lesson_still_filters_the_open_slots_panel_to_it(clicky):
    """Guard for the ST-UI-009 fix — the open-slots panel is *supposed* to
    follow the selection, and must keep doing so.

    The one side panel that legitimately depends on the selection is Open Slots:
    with a single class selected it switches from "every free room-slot" to
    "every slot this class could legally go in" (app.py:3022-3040). "Skip
    side-panel recompute on selection" must therefore not be implemented by
    deleting that call — the cheapest way to make every count in this module
    zero is also the way that silently removes the feature.

    A failure means the user selects a class and the panel that is supposed to
    answer "where can this go?" keeps showing them the unfiltered list.
    """
    items = _lesson_items(clicky)
    assert items, "the fixture rendered no lesson items; nothing to click"
    name = items[0].cls["name"]

    hint = clicky._open_slots_filter_hint
    assert name not in hint.text(), (
        "the panel already claimed to be filtered for this class before it was "
        "selected; the assertion below would be vacuous")

    clicky._select_class_gfx(items[0].cls, items[0], modifiers=None)

    assert name in hint.text() and not hint.isHidden(), (
        f"selecting {name!r} did not switch the open-slots panel into its "
        f"contextual mode; hint reads {hint.text()!r}, hidden={hint.isHidden()}")


def test_selecting_a_lesson_leaves_the_state_byte_for_byte_identical(clicky, saves):
    """ST-UI-009 — a selection must not mutate ``state_data``.

    The reason the write count above is allowed to be zero. ``_refresh_open_slots``
    runs the full validity analysis for the selected class and
    ``_run_auto_negotiation`` is able to *apply* relaxations to classes, so
    "selection is read-only" is a property worth pinning rather than assuming.

    A failure means a click silently edits the user's constraints, and the
    debounced autosave then persists an edit they never made.
    """
    items = _lesson_items(clicky)
    assert items, "the fixture rendered no lesson items; nothing to click"

    before = copy.deepcopy(clicky.state_data)
    clicky._select_class_gfx(items[0].cls, items[0], modifiers=None)

    assert clicky._selected_class is items[0].cls, (
        "the click did not select the lesson, so 'the selection changed "
        "nothing' below is a statement about nothing happening at all")
    assert clicky.state_data == before, "selecting a lesson modified state_data"
    assert saves.calls == 0, "selecting a lesson persisted something"


def test_reselecting_the_lesson_that_is_already_selected_does_no_work(clicky):
    """ST-UI-009 — clicking the selected lesson again must cost nothing.

    ``_apply_class_selection`` ends in an unconditional ``_refresh_open_slots()``
    (app.py:3322), so a second click on the same lesson tears down and rebuilds
    the entire open-slots panel and re-runs ``find_valid_options`` over the whole
    grid to arrive at the answer already on screen. Measured on this tree:
    9 ms at 25 classes, 39 ms at 80.

    A failure means the app burns hundreds of widget constructions and a full
    validity analysis to change nothing at all — the cheapest possible thing to
    get right, and the one a user triggers most often by double-clicking or
    click-dragging without moving.
    """
    items = _lesson_items(clicky)
    assert items, "the fixture rendered no lesson items; nothing to click"

    clicky._select_class_gfx(items[0].cls, items[0], modifiers=None)
    assert clicky._selected_class is items[0].cls, (
        "the first click did not select the lesson; the assertion below would "
        "be measuring a selection *change*, not a repeat")

    counters = _Counters(clicky)
    mark = counters.snapshot()
    clicky._select_class_gfx(items[0].cls, items[0], modifiers=None)
    delta = counters.since(mark)

    assert clicky._selected_class is items[0].cls, (
        "re-clicking the selected lesson deselected it")
    assert delta["_refresh_open_slots"] == 0, (
        "re-clicking the already-selected lesson rebuilt the open-slots panel "
        f"{delta['_refresh_open_slots']} time(s) for an unchanged selection")


def test_reselecting_the_empty_slot_that_is_already_selected_does_no_work(clicky):
    """ST-UI-009 — clicking the selected empty slot again must cost nothing.

    ``_select_empty_slot`` (app.py:3367-3379) calls ``_clear_class_selection()``
    — and therefore ``_refresh_open_slots()`` — *before* the ``is`` check that
    would have told it the slot was already selected. Measured on this tree:
    36 ms at 25 classes, 81 ms at 80, every time the user clicks the same blank
    cell twice.

    A failure means the same wasted rebuild as the lesson case, on the half of
    the grid that is empty.
    """
    empties = _empty_slot_items(clicky)
    assert empties, "the fixture rendered no empty slots; nothing to click"

    clicky._select_empty_slot(empties[0])
    assert clicky._selected_empty_slot is empties[0], (
        "the first click did not select the empty slot")

    counters = _Counters(clicky)
    mark = counters.snapshot()
    clicky._select_empty_slot(empties[0])
    delta = counters.since(mark)

    assert clicky._selected_empty_slot is empties[0], (
        "re-clicking the selected empty slot deselected it")
    assert delta["_refresh_open_slots"] == 0, (
        "re-clicking the already-selected empty slot rebuilt the open-slots "
        f"panel {delta['_refresh_open_slots']} time(s)")


def test_selecting_in_the_unplaced_list_writes_nothing(clicky, saves, qapp):
    """ST-UI-009 — the third selection path must be free too.

    ``unplaced_list.itemSelectionChanged`` is wired straight to
    ``_refresh_open_slots`` (app.py:1484). Highlighting a row in a list is the
    least consequential thing a user can do, and it must not reach the disk.

    Note on what is *not* asserted: that signal was connected to a **bound
    method** at ``__init__`` time, so ``_Counters`` — which rebinds instance
    attributes — is structurally unable to see that particular call, and an
    assertion about it would be unfalsifiable. The guard below therefore proves
    the signal fired by its observable effect (the open-slots panel switched
    into contextual mode for the selected class), and the counters are used only
    for what they *can* see: whether this path newly reaches ``refresh_grid``
    and its callees.

    A failure means arrow-keying down the Unplaced panel to read the class names
    encrypts and rewrites the whole schedule once per row.
    """
    assert clicky.unplaced_list.count() > 0, (
        "the fixture produced no unplaced classes; nothing to select")
    hint = clicky._open_slots_filter_hint
    assert hint.isHidden(), (
        "the open-slots panel was already in contextual mode before anything "
        "was selected; the non-vacuity guard below would prove nothing")

    counters = _Counters(clicky)
    mark = counters.snapshot()
    clicky.unplaced_list.setCurrentRow(0)
    qapp.processEvents()
    delta = counters.since(mark)

    selected = clicky.unplaced_list.selected_classes()
    assert len(selected) == 1, (
        f"setCurrentRow(0) selected {len(selected)} classes, not one; the "
        "assertions below would be about a selection that never happened")
    assert not hint.isHidden() and selected[0]["name"] in hint.text(), (
        "selecting an unplaced class did not reach the open-slots panel at "
        f"all (hint hidden={hint.isHidden()}, text={hint.text()!r}); this test "
        "would then be asserting that nothing costs nothing")

    assert saves.calls == 0, (
        f"selecting an unplaced class rewrote the settings container "
        f"{saves.calls} time(s)")
    assert delta["_render_current_tab"] == 0, (
        "selecting an unplaced class rebuilt the timetable scene")
    assert delta["_refresh_warnings"] == 0 and delta["_run_auto_negotiation"] == 0, (
        f"selecting an unplaced class re-ran the negotiation pass: {delta}")


# ══════════════════════════════════════════════════════════════════════════
#  3. Durability — the half of the fix that can lose data
# ══════════════════════════════════════════════════════════════════════════

def test_an_edit_is_persisted_without_any_further_user_action(clicky, saves, qapp):
    """ST-PERF-002 — a debounced write must actually land, on its own.

    The user adds a class and walks away. Nothing else happens: no close, no
    explicit save, no further click. Within ``_FLUSH_TIMEOUT_S`` the edit has to
    be on disk.

    A failure is the data-loss bug that a careless debounce introduces, and it
    is worse than the slowness it replaces: DERSİS is an offline app whose whole
    persistence story is this autosave, so an edit that never gets flushed is an
    edit the user loses to a power cut with no warning and no dirty-state
    indicator anywhere in the UI.
    """
    from scheduler_app.core.models import new_class

    marker = "YENİ-DERS-FLUSH-XYZZY"
    cls = new_class()
    cls["name"] = marker
    cls["class_code"] = "FLUSH1"
    cls["lecturer"] = "Lect-1"
    cls["targets"] = [{"year": "Year-1", "branch": "A"}]
    cls["duration"] = 1
    clicky.state_data["classes"].append(cls)
    clicky.refresh_grid()

    landed = _settle(clicky, qapp,
                     until=lambda: marker in _persisted_class_names(clicky))

    assert landed, (
        f"an edit made {_FLUSH_TIMEOUT_S:.0f}s ago is still not in "
        f"{clicky._config_path}; the debounce dropped it. Container currently "
        f"holds {_persisted_class_names(clicky)}")


def test_closing_the_window_persists_the_very_last_edit(clicky, saves, qapp):
    """ST-PERF-002 / ST-DATA-005 — ``closeEvent`` must flush whatever is pending.

    The event loop is deliberately **never** spun between the edit and the
    close, so any pending timer is still pending: this is the user who types
    something and immediately hits the window's X button, and it is the exact
    case a debounce loses.

    A failure means the last thing the user did before quitting is gone.
    """
    from PyQt6.QtGui import QCloseEvent
    from scheduler_app.core.models import new_class

    marker = "SON-DERS-CLOSE-XYZZY"
    cls = new_class()
    cls["name"] = marker
    cls["class_code"] = "CLOSE1"
    cls["lecturer"] = "Lect-1"
    cls["targets"] = [{"year": "Year-1", "branch": "A"}]
    cls["duration"] = 1
    clicky.state_data["classes"].append(cls)
    clicky.refresh_grid()

    # Bare call: closeEvent is a Qt virtual, where an escaping exception aborts
    # the process rather than reporting anything.
    clicky.closeEvent(QCloseEvent())

    assert marker in _persisted_class_names(clicky), (
        "the class added immediately before closing the window was never "
        f"written to {clicky._config_path}; container holds "
        f"{_persisted_class_names(clicky)}")


def test_a_deletion_is_persisted_too_not_just_an_addition(clicky, saves, qapp):
    """ST-PERF-002 — the delta check must not mistake a removal for "no change".

    A fingerprint-based "did anything change?" test is the natural way to
    implement delta autosave. This pins the *removal* direction: the container
    must shrink, not merely stop growing, so a fix that only ever appends what
    is new leaves nothing behind.

    (Honesty about its reach, since the obvious rationale is wrong: a fingerprint
    over ``len(state["classes"])`` alone still passes this test, because a
    deletion changes the count too. The variants that survive a count are caught
    by ``..._moving_a_placed_lesson_...`` and
    ``..._a_change_outside_the_class_list_...`` below, not here.)

    A failure means a class the user deleted comes back the next time they open
    DERSİS.
    """
    from PyQt6.QtGui import QCloseEvent

    doomed = clicky.state_data["classes"][0]["name"]
    clicky.refresh_grid()
    _settle(clicky, qapp,
            until=lambda: doomed in _persisted_class_names(clicky))
    assert doomed in _persisted_class_names(clicky), (
        "the fixture's schedule never reached disk, so the deletion below "
        "could not be observed")

    clicky.state_data["classes"] = [
        c for c in clicky.state_data["classes"] if c["name"] != doomed]
    clicky.refresh_grid()
    clicky.closeEvent(QCloseEvent())

    assert doomed not in _persisted_class_names(clicky), (
        f"{doomed!r} was deleted and then closed over, but the settings "
        f"container still holds {_persisted_class_names(clicky)}")


def test_moving_a_placed_lesson_to_another_slot_is_persisted(clicky, saves, qapp):
    """ST-PERF-002 — the delta check must see an edit that adds and removes nothing.

    Dragging a lesson to a different day, hour or room is the single commonest
    edit in DERSİS, and it mutates **one class dict in place**: the class list is
    the same length afterwards and every name in it is unchanged. Any "did
    anything change?" test built from a summary of the class list — the count,
    the names, the set of ids — answers "no" and skips the write.

    This is not hypothetical. Three delta implementations were built and run
    against this module: fingerprinting the class names, fingerprinting
    ``len(state["classes"])``, and fingerprinting ``state["classes"]``
    serialised. All three pass every other autosave and durability test here.
    This test and the next one are what separate them from a correct fix.

    A failure means every drag the user makes is silently thrown away, and they
    find out at the next launch.
    """
    from PyQt6.QtGui import QCloseEvent

    cls = next(c for c in clicky.state_data["classes"] if c["placed"])
    name = cls["name"]
    origin = (cls["placed_day"], cls["placed_time"], cls["placed_classroom"])

    clicky.refresh_grid()
    _settle(clicky, qapp,
            until=lambda: _persisted_placement(clicky, name) == origin)
    assert _persisted_placement(clicky, name) == origin, (
        f"{name!r}'s original placement {origin} never reached disk (found "
        f"{_persisted_placement(clicky, name)}), so the move below could not "
        "be observed as a change")

    target = (clicky.state_data["days"][-1], clicky.state_data["slots"][-1],
              clicky.state_data["classrooms"][-1])
    assert target != origin, "the fixture placed the lesson on the move target"
    cls["placed_day"], cls["placed_time"], cls["placed_classroom"] = target

    clicky.refresh_grid()
    clicky.closeEvent(QCloseEvent())

    assert _persisted_placement(clicky, name) == target, (
        f"{name!r} was moved from {origin} to {target} and the window was then "
        f"closed, but the settings container still says "
        f"{_persisted_placement(clicky, name)}")


def test_a_change_outside_the_class_list_is_persisted(clicky, saves, qapp):
    """ST-PERF-002 — the delta check must cover the whole state, not just classes.

    Removing a classroom in Setup touches ``state["classrooms"]`` and nothing
    inside ``state["classes"]`` at all. A fingerprint scoped to the class list —
    the obvious scope, since classes are what the app is *about* — reports "no
    change" and the edit never lands. Verified: that variant passes every other
    test in this module.

    A failure means the user reorganises their rooms, days or hours, quits, and
    reopens DERSİS to the old building.
    """
    from PyQt6.QtGui import QCloseEvent

    doomed_room = clicky.state_data["classrooms"][-1]
    assert not any(c.get("placed_classroom") == doomed_room
                   for c in clicky.state_data["classes"]), (
        "the fixture placed a lesson in the room this test removes; the edit "
        "would then also change state['classes'] and stop being a test of "
        "anything outside it")

    clicky.refresh_grid()
    _settle(clicky, qapp,
            until=lambda: doomed_room in _persisted_classrooms(clicky))
    assert doomed_room in _persisted_classrooms(clicky), (
        "the fixture's room list never reached disk, so its removal below "
        "could not be observed")

    clicky.state_data["classrooms"] = [
        r for r in clicky.state_data["classrooms"] if r != doomed_room]
    clicky.state_data["classroom_capacities"].pop(doomed_room, None)

    clicky.refresh_grid()
    clicky.closeEvent(QCloseEvent())

    assert doomed_room not in _persisted_classrooms(clicky), (
        f"{doomed_room!r} was removed from the room list and the window was "
        f"then closed, but the settings container still holds "
        f"{_persisted_classrooms(clicky)}")


# ══════════════════════════════════════════════════════════════════════════
#  4. Phase 1's ``_auto_save`` contract must survive the debounce
# ══════════════════════════════════════════════════════════════════════════

def test_auto_save_called_directly_still_writes_immediately(clicky, saves):
    """ST-DATA-005 guard — ``_auto_save()`` stays synchronous and unconditional.

    Phase 1 made ``_auto_save`` the app's one honest write: it returns True only
    when the bytes are down, and ``tests/test_settings_recovery.py`` drives it
    directly for exactly that reason. A fix that turns ``_auto_save`` itself
    into "arm a timer" would silently hollow out that whole module.

    A failure means no caller anywhere in the app can any longer be sure the
    user's work has actually been saved.
    """
    marker = "DOĞRUDAN-KAYIT-XYZZY"
    clicky.state_data["classes"][0]["name"] = marker

    assert clicky._auto_save() is True, "_auto_save() reported failure"
    assert saves.calls == 1, (
        f"one _auto_save() call produced {saves.calls} container writes; it "
        "must write exactly once, synchronously")
    assert marker in _persisted_class_names(clicky), (
        "_auto_save() returned True without the data reaching disk")


def test_a_failing_save_on_close_still_reaches_the_user(
        clicky, message_boxes, monkeypatch, caplog):
    """ST-DATA-005 guard — a debounce must not swallow the close-time failure.

    Phase 1 made ``closeEvent`` the one place a failed write interrupts the
    user, because quitting is the moment an unsaved change becomes permanently
    lost. Routing the close through a debounce flush must keep that: whatever
    the flush returns has to still drive the "quit anyway?" prompt.

    A failure means a user whose disk is full or whose profile is read-only
    closes DERSİS and is told nothing at all.
    """
    from PyQt6.QtGui import QCloseEvent
    from PyQt6.QtWidgets import QMessageBox

    from scheduler_app import storage
    import scheduler_app.storage.storage as storage_mod

    clicky.state_data["classes"][0]["name"] = "KAYDEDİLEMEYEN-XYZZY"

    asked = []
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: asked.append(a)
                     or QMessageBox.StandardButton.Yes))

    def boom(data, path, *args, **kwargs):
        raise OSError("simulated read-only settings path")

    monkeypatch.setattr(storage, "save_encrypted", boom)
    monkeypatch.setattr(storage_mod, "save_encrypted", boom)

    caplog.set_level(0)
    # Only signals raised *by the close* count. Building the fixture emits none
    # today (checked), but if it ever starts to, this test must not quietly
    # start passing on somebody else's toast.
    toasts_before = len(message_boxes)
    logs_before = len(caplog.records)
    clicky.closeEvent(QCloseEvent())

    told = bool(asked) or len(message_boxes) > toasts_before or bool(
        [r for r in caplog.records[logs_before:] if r.levelno >= 30
         and "scheduler_app" in (r.pathname or "").replace("\\", "/")])
    assert told, (
        "closing with an unwritable settings file produced no signal at all: "
        "no quit-anyway prompt, no toast, no log record")


# ══════════════════════════════════════════════════════════════════════════
#  5. The same counts at the scale the findings were measured at
# ══════════════════════════════════════════════════════════════════════════

def test_burst_of_refreshes_at_250_classes_does_not_rewrite_232kb_each_time(
        large, saves):
    """ST-PERF-002 at the audit's own scale — 250 classes, a 232 KB container.

    Same count as the small-scale test, at the size where each rewrite was
    measured at 33.6 ms, and kept because a fix that quietly special-cases small
    states would pass every other autosave test here. Deliberately **not**
    ``slow``: the 250-class fixture is 1.0 s to build and 1.5 s to drive on this
    tree, so excluding it from CI's ``-m "not slow"`` lane would have cost the
    module its only department-scale coverage to save two seconds.

    A failure means a department-scale timetable writes several megabytes of
    ciphertext for a minute of ordinary editing.
    """
    n = 6
    for _ in range(n):
        large.refresh_grid()

    assert saves.calls < n, (
        f"autosave is still once per refresh at 250 classes: "
        f"{saves.describe(n)}")
    assert saves.calls <= _MAX_WRITES_PER_BURST, (
        f"{n} refreshes of the 250-class state produced {saves.describe(n)}")


def test_selection_at_250_classes_writes_nothing_and_rebuilds_nothing(
        large, saves):
    """ST-UI-009 at the audit's own scale — the 306-563 ms click.

    A failure means the latency the audit measured on a *selection* at
    department scale is still there: a full scene rebuild plus a 232 KB
    encrypted rewrite to highlight one lesson.
    """
    items = _lesson_items(large)
    assert items, "the 250-class fixture rendered no lesson items"

    counters = _Counters(large)
    mark = counters.snapshot()
    large._select_class_gfx(items[0].cls, items[0], modifiers=None)
    delta = counters.since(mark)

    assert large._selected_class is items[0].cls, (
        "the click did not select the lesson, so every count below would be "
        "zero for the wrong reason")
    assert saves.calls == 0, (
        f"selecting one lesson at 250 classes rewrote the container "
        f"{saves.calls} time(s), {saves.bytes_written} bytes")
    assert delta["_render_current_tab"] == 0, (
        "selecting a lesson at 250 classes rebuilt the whole scene")
    assert delta["_refresh_warnings"] == 0 and delta["_run_auto_negotiation"] == 0, (
        f"selecting a lesson re-ran the 4.5 s warnings pass: {delta}")


def test_reselecting_at_250_classes_does_no_work(large):
    """ST-UI-009 at the audit's own scale — the free click that is not free.

    A failure means clicking the same lesson twice costs a second full
    ``find_valid_options`` sweep over a 250-class, 16-room, 5x8 grid and several
    hundred widget constructions, to redraw a border that is already there.
    """
    items = _lesson_items(large)
    assert items, "the 250-class fixture rendered no lesson items"

    large._select_class_gfx(items[0].cls, items[0], modifiers=None)
    assert large._selected_class is items[0].cls, "the first click did not select"

    counters = _Counters(large)
    mark = counters.snapshot()
    large._select_class_gfx(items[0].cls, items[0], modifiers=None)
    delta = counters.since(mark)

    assert delta["_refresh_open_slots"] == 0, (
        "re-clicking the already-selected lesson at 250 classes rebuilt the "
        f"open-slots panel {delta['_refresh_open_slots']} time(s)")
