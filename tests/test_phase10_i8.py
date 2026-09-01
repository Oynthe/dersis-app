"""Item 8 / ST-PERF-006 — the warnings sidebar recomputes everything, always.

The defect
----------
``ui/app.py::_refresh_warnings`` (line 4312) has no fingerprint guard, unlike
``_refresh_open_slots`` (line 4149) forty lines above it. Every call runs
``_run_auto_negotiation``, which constructs a ``ConstraintNegotiator`` and calls
``neg.negotiate_class(cls)`` once per unplaced class, and then
``_conflict_log_entries``, which sweeps the whole timetable again.

``_refresh_warnings`` has exactly one caller — ``_update_side_panels`` — which
has exactly one caller, ``refresh_grid``. So the cost is paid once per
*state-changing gesture*, not once per Qt repaint: drag-and-drop, place,
unplace, add/edit/remove a class, a protection toggle, undo, redo, open, import,
"solve finished", F5, and a language switch. 32 call sites, all in ``app.py``.
Measured on this tree (offscreen Qt, .venv-audit, ``make_preset("large")``):

    250 classes, 235 unplaced (just imported, not yet solved)
        _refresh_warnings          744 ms / call, 235 negotiate_class calls
        _refresh_warnings again    747 ms / call, 235 negotiate_class calls
        refresh_grid (whole)       740 ms       -> the panel is 99.7% of it
        _refresh_open_slots        151 ms first call, 0.1 ms every call after
                                   (that panel HAS the guard)

    250 classes, 213 placed, 22 unplaced (an ordinary mid-session timetable)
        _refresh_warnings           47.6 ms / call, 22 negotiate_class calls
        refresh_grid (whole)        47-49 ms     -> the panel is ~97% of it
        one _set_protection click   50.8 ms, 1x _refresh_warnings, 22x negotiate

So an ordinary editing session pays 47 ms of pure recomputation on every single
click, and a session that has just imported a term's worth of lessons and has
not solved yet pays three quarters of a second on every single click. Nothing
about the state has changed between two of them.

What these tests pin
--------------------
Section 1 is the defect: two refreshes with an unchanged timetable must not run
the negotiation pass twice. RED today.

Section 2 is the guard's safety net, and it is the more important half. A stale
sidebar is worse than a slow one, so every input that can change what the panel
shows gets its own case: a placement, a conflict, a constraint edit, the grid
shape, a new class, the UI language, and the panel's own Clear button. These are
GREEN today (there is no guard to be stale) and are here so the fix cannot buy
its speed with a wrong answer.

Section 3 is a finding about the guard the handoff says to copy.

Measured facts a fix should not have to re-derive
-------------------------------------------------
* ``_refresh_warnings`` reads, transitively, essentially the WHOLE state: days,
  slots, classrooms, capacities, lecturers and their availability, years, and
  every field of every class (the negotiator's validator/generator read the
  constraint fields; ``find_off_grid_placements`` and ``find_schedule_conflicts``
  read the placements). A curated field tuple like ``_open_slots_fingerprint``
  is the wrong instrument here.
* ``app.py`` already has the right instrument: ``_state_fingerprint()``
  (line 2426), built for the autosave debounce. It sha256s the whole state as
  JSON **and includes ``get_language()``**. Measured cost: **2.17 ms** at 250
  classes, **5.00 ms** at 600 — against the 47 ms / 744 ms it would save.
* Two inputs are NOT in the state and not in that fingerprint:
  - the panel's own Clear button (``WarningLogPanel._clear_btn`` is wired
    straight to ``clear()``, which empties ``_derived`` with no signal out).
    Measured: with a naive early-return guard bolted on, 29 derived findings
    were cleared and never came back. Section 2's ``clear_button`` case.
  - ``settings/negotiation_settings.egu``'s ``auto_apply_low_risk``, read from
    disk by ``_get_negotiation_auto_apply`` on every pass (0.26 ms). Nothing in
    the application ever writes that key — ``grep -rn auto_apply`` finds one
    reader and no writer — so it can only change out of band, between runs. It
    is the one input this module does not fingerprint, deliberately.

Findings guarded here: ST-PERF-006.
"""
import time

import pytest

pytestmark = [pytest.mark.ui]


# A second refresh over an unchanged timetable is allowed this fraction of the
# first one's wall clock. Not zero: publishing the same list to the panel and
# hashing the state are real work (2.17 ms at 250 classes). Today the ratio is
# ~1.0, so nothing about the exact value decides the verdict.
_MAX_REPEAT_RATIO = 0.30


# ── helpers ─────────────────────────────────────────────────────────────────

def _derived(window):
    """The findings the warnings panel currently attributes to this timetable."""
    return list(window.warning_log._derived)


def _place(cls, day, slot, room):
    cls["placed"] = True
    cls["placed_day"] = day
    cls["placed_time"] = slot
    cls["placed_classroom"] = room


def _place_greedily(state):
    """Pack placeable classes into the first free room-slot, deterministically.

    Deliberately not the optimizer: this module is about the cost of a repaint,
    and a solve would add minutes plus a dependency on the seeding.
    """
    from scheduler_app.core.logic import total_duration

    occupied = set()
    n_slots = len(state["slots"])
    placed = 0
    for cls in state["classes"]:
        if cls["pinned"] or cls.get("location_type") != "face_to_face":
            continue
        span = total_duration(cls)
        done = False
        for room in state["classrooms"]:
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


class _NegotiationCounter:
    """Counts real ``negotiate_class`` calls, wall clock included.

    Patched onto the class in ``scheduler_app.core.constraint_negotiator``;
    ``app.py`` imports it through the ``scheduler_app.constraint_negotiator``
    shim, which resolves to the same module object, so one patch covers both.
    """

    def __init__(self, monkeypatch):
        from scheduler_app.core import constraint_negotiator as cn

        self.calls = 0
        self.seconds = 0.0
        real = cn.ConstraintNegotiator.negotiate_class
        counter = self

        def counting(inner_self, cls):
            counter.calls += 1
            t0 = time.perf_counter()
            try:
                return real(inner_self, cls)
            finally:
                counter.seconds += time.perf_counter() - t0

        monkeypatch.setattr(cn.ConstraintNegotiator, "negotiate_class", counting)

    def mark(self):
        return self.calls

    def since(self, mark):
        return self.calls - mark


class _WarningsTimer:
    """Wall clock for each ``_refresh_warnings`` a gesture triggers.

    Patched onto the *instance*, so the production class is untouched and this
    sees exactly the calls ``_update_side_panels`` makes — guard included, once
    there is one.
    """

    def __init__(self, window):
        self.durations = []
        real = window._refresh_warnings

        def timed():
            t0 = time.perf_counter()
            try:
                return real()
            finally:
                self.durations.append(time.perf_counter() - t0)

        window._refresh_warnings = timed


def _lock(window, cls):
    """One real gesture: the user sets a lesson's protection level.

    ``_set_protection`` is the whole production path — it writes the class,
    raises a toast and ends in ``refresh_grid()`` — and it is the cheapest
    gesture in the app that has no dialog in front of it.
    """
    from scheduler_app.core.models import PROTECTION_LOCKED, PROTECTION_NONE

    level = (PROTECTION_NONE if cls.get("protection") == PROTECTION_LOCKED
             else PROTECTION_LOCKED)
    window._set_protection(cls, level)


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def message_boxes(monkeypatch):
    """Neutralize every modal a refresh or a close can raise.

    An unpatched modal blocks the whole suite under the offscreen platform, and
    ``_show_toast`` arms a 3 s ``QTimer`` that would outlive the window.
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


@pytest.fixture
def window(make_app, message_boxes, monkeypatch):
    """A real, never-shown ``SchedulerApp`` with the licence tier pinned.

    ``make_app`` already disarms ``FirstRunController`` and restores the
    ``TierEnforcement`` registries; the tier itself is pinned here so no entity
    gate can short-circuit the repaint being measured.
    """
    from scheduler_app.plans import TIER_INSTITUTIONAL
    from scheduler_app.ui.tier_enforcement import TierEnforcement

    enforcer = TierEnforcement.instance()
    monkeypatch.setattr(enforcer, "_tier_slug", TIER_INSTITUTIONAL,
                        raising=False)
    monkeypatch.setattr(enforcer, "_tier_confirmed", True, raising=False)
    return make_app()


@pytest.fixture
def small_window(window):
    """A hand-built timetable small enough for exact assertions.

    Four one-hour lessons in R001 (the classroom tab 0 filters to by default),
    one class that can never be placed — its ``allowed_days`` is Saturday on a
    Mon-Fri grid — so ``_run_auto_negotiation`` has real per-class work to do
    and "the warnings changed" is an assertion about something.
    """
    from scheduler_app.core.models import new_class
    from scheduler_app.i18n.day_keys import DAY_KEYS

    state = window.state_data
    state["days"] = list(DAY_KEYS[:5])
    state["slots"] = ["09:00", "10:00", "11:00", "12:00"]
    state["classrooms"] = ["R001", "R002"]
    state["classroom_capacities"] = {"R001": 40, "R002": 40}
    state["lecturers"] = ["Lect-1", "Lect-2"]
    state["years"] = {"Year-1": ["A"]}

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
        _place(add("Ders %d" % (i + 1)), DAY_KEYS[i], state["slots"][0], "R001")
    add("Ders Imkansiz", lecturer="Lect-2", allowed_days=["saturday"])
    window.refresh_grid()
    return window


# ── 1. the defect ───────────────────────────────────────────────────────────

def _mid_session(window, make_preset):
    """A 250-class timetable with most of it placed — an ordinary session."""
    state = make_preset("large")
    n_placed = _place_greedily(state)
    window.state_data.clear()
    window.state_data.update(state)
    unplaced = [c for c in state["classes"]
                if not c["placed"] and not c["pinned"]]
    return state, n_placed, unplaced


def test_an_unchanged_timetable_does_not_re_run_the_negotiation_pass(
        window, make_preset, monkeypatch):
    """ST-PERF-006 — RED today.

    One real gesture (``_set_protection``, which ends in ``refresh_grid()``),
    then two refreshes with **nothing** touched in between. The gesture has to
    recompute — that is the sanity half, and it is what stops this test from
    passing on a fix that simply never refreshes. The two after it must not: they
    rebuild, from scratch, an answer that is already on screen. F5 is literally
    that gesture, and so is every click that leaves the timetable where it was.

    Measured on this tree: 22 / 22 / 22 on a mid-session 250-class timetable.
    """
    state, n_placed, unplaced = _mid_session(window, make_preset)

    counter = _NegotiationCounter(monkeypatch)
    window.refresh_grid()                      # warm every panel and widget

    mark = counter.mark()
    _lock(window, state["classes"][0])         # a real click that changes state
    on_change = counter.since(mark)

    mark = counter.mark()
    window.refresh_grid()                      # F5, nothing changed
    repeat_one = counter.since(mark)

    mark = counter.mark()
    window.refresh_grid()                      # F5 again
    repeat_two = counter.since(mark)

    assert on_change == len(unplaced), (
        "sanity: the gesture changed the timetable, so the panel must "
        "recompute all %d unplaced classes; it ran %d"
        % (len(unplaced), on_change))
    assert (repeat_one, repeat_two) == (0, 0), (
        "ST-PERF-006: %d classes (%d placed, %d unplaced). Two refresh_grid "
        "calls over an UNCHANGED timetable ran "
        "ConstraintNegotiator.negotiate_class %d and %d times — the same %d the "
        "preceding gesture ran, for an answer that is already on screen. "
        "_refresh_open_slots, forty lines above _refresh_warnings in the same "
        "file, answers the same repeat in 0.1 ms because it fingerprints its "
        "inputs first."
        % (len(state["classes"]), n_placed, len(unplaced),
           repeat_one, repeat_two, on_change))


def test_the_repeat_costs_what_the_first_one_cost(
        window, make_preset, monkeypatch):
    """ST-PERF-006 — RED today. The same defect, in wall clock.

    A ratio, not a millisecond budget: what is wrong here is not that the panel
    is expensive, it is that the expense is paid again for an answer that cannot
    have changed. A fix that made the negotiation twice as fast still fails
    this; a fix that skips the repeat passes it on any machine.

    Measured on this tree, mid-session 250-class timetable — 41.8 ms for the
    gesture that changed something, then 46.0 ms and 43.2 ms for two refreshes
    that changed nothing (110% of the first). With the fingerprint guard built
    against ``_state_fingerprint()`` (see the report): 47 ms, then 2.2 ms and
    2.1 ms, ratio 0.05.
    """
    _mid_session(window, make_preset)

    counter = _NegotiationCounter(monkeypatch)
    window.refresh_grid()

    timer = _WarningsTimer(window)
    whole = time.perf_counter()
    _lock(window, window.state_data["classes"][0])
    whole = time.perf_counter() - whole
    window.refresh_grid()
    window.refresh_grid()

    assert len(timer.durations) == 3, (
        "expected one _refresh_warnings per refresh_grid, saw %d"
        % len(timer.durations))
    changed, repeat_one, repeat_two = timer.durations
    share = changed / whole if whole else 0.0

    assert max(repeat_one, repeat_two) <= changed * _MAX_REPEAT_RATIO, (
        "ST-PERF-006: _refresh_warnings cost %.1f ms for the gesture that "
        "changed the timetable and then %.1f ms and %.1f ms for two refreshes "
        "that changed nothing (%.0f%% of the first, budget %.0f%%). It is "
        "%.0f%% of the %.1f ms that gesture cost end to end, and %d "
        "negotiate_class calls of it are pure repetition."
        % (changed * 1000, repeat_one * 1000, repeat_two * 1000,
           100.0 * max(repeat_one, repeat_two) / changed,
           100.0 * _MAX_REPEAT_RATIO, 100.0 * share, whole * 1000,
           counter.calls))


def test_a_freshly_imported_term_pays_three_quarters_of_a_second_per_click(
        window, make_preset, monkeypatch):
    """ST-PERF-006 — RED today. The worst realistic case, which is not exotic.

    A user who has just imported a term's timetable and has not solved it yet
    has 235 unplaced classes. Every gesture until they press Solve pays a full
    negotiation pass over all 235. Measured: 744 / 747 / 748 / 758 ms across
    four consecutive calls with the state untouched — 99% of it inside
    ``negotiate_class``.
    """
    state = make_preset("large")
    window.state_data.clear()
    window.state_data.update(state)
    unplaced = [c for c in state["classes"]
                if not c["placed"] and not c["pinned"]]
    assert len(unplaced) > 200, "preset changed shape: %d unplaced" % len(unplaced)

    counter = _NegotiationCounter(monkeypatch)
    window.refresh_grid()

    timer = _WarningsTimer(window)
    mark = counter.mark()
    _lock(window, state["classes"][0])
    on_change = counter.since(mark)

    mark = counter.mark()
    window.refresh_grid()
    repeated = counter.since(mark)

    assert on_change == len(unplaced), (
        "sanity: the gesture must recompute all %d unplaced classes; it ran %d"
        % (len(unplaced), on_change))
    assert repeated == 0, (
        "ST-PERF-006: %d unplaced classes. A refresh over the timetable the "
        "gesture before it just drew ran negotiate_class %d more times and took "
        "%.0f ms (the gesture itself took %.0f ms). Every click, drag, undo and "
        "protection toggle in this state pays that."
        % (len(unplaced), repeated,
           timer.durations[-1] * 1000, timer.durations[0] * 1000))


# ── 2. what the guard must never break ──────────────────────────────────────
#
# GREEN today — there is no guard yet, so nothing can be stale. They exist so
# the fix cannot buy its speed by showing a timetable that no longer exists.
# Each case makes ONE kind of change through the state the production code
# reads and then drives the real ``refresh_grid()``.

def _mutate_place_the_unplaceable(window):
    state = window.state_data
    cls = next(c for c in state["classes"] if c["name"] == "Ders Imkansiz")
    cls["allowed_days"] = [state["days"][4]]
    _place(cls, state["days"][4], state["slots"][1], "R002")


def _mutate_unplace_a_placed_lesson(window):
    cls = next(c for c in window.state_data["classes"] if c["placed"])
    cls["placed"] = False
    cls["placed_day"] = cls["placed_time"] = cls["placed_classroom"] = None


def _mutate_create_a_room_conflict(window):
    state = window.state_data
    placed = [c for c in state["classes"] if c["placed"]]
    a, b = placed[0], placed[1]
    _place(b, a["placed_day"], a["placed_time"], a["placed_classroom"])


def _mutate_tighten_a_constraint(window):
    cls = next(c for c in window.state_data["classes"]
               if c["name"] == "Ders Imkansiz")
    cls["allowed_hours"] = ["23:00"]
    cls["required_classrooms"] = ["R404"]


def _mutate_add_a_day(window):
    from scheduler_app.i18n.day_keys import DAY_KEYS

    window.state_data["days"] = list(DAY_KEYS[:6])


def _mutate_shorten_the_day(window):
    """One slot per day, without moving a single lesson.

    The heavy-day threshold is ``len(state["slots"]) * 0.75``, so at four slots
    a day holding one lesson is not heavy (1 < 3.0) and at one slot the same
    day, holding the same lesson, is (1 >= 0.75). Nothing about any placement
    changed; the sidebar's answer did.

    Measured while writing this: the intermediate version of this case cut the
    grid from four slots to two, and the findings came back **identical** —
    1 < 1.5 either way. A fingerprint over placements alone would have looked
    green against it.
    """
    window.state_data["slots"] = ["09:00"]


def _mutate_add_a_class(window):
    from scheduler_app.core.models import new_class

    state = window.state_data
    cls = new_class()
    cls["name"] = "Yeni Ders"
    cls["class_code"] = "YeniDers"
    cls["lecturer"] = "Lect-2"
    cls["targets"] = [{"year": "Year-1", "branch": "A"}]
    cls["duration"] = 1
    cls["allowed_days"] = ["sunday"]
    state["classes"].append(cls)


def _mutate_add_a_branch(window):
    from scheduler_app.core.models import new_class

    state = window.state_data
    state["years"] = {"Year-1": ["A"], "Year-2": ["B"]}
    cls = new_class()
    cls["name"] = "Ikinci Sinif Dersi"
    cls["class_code"] = "ISD"
    cls["lecturer"] = "Lect-2"
    cls["targets"] = [{"year": "Year-2", "branch": "B"}]
    cls["duration"] = 1
    _place(cls, state["days"][0], state["slots"][2], "R002")
    state["classes"].append(cls)


def _mutate_switch_language(window):
    from scheduler_app.translations import set_language

    set_language("en")


@pytest.mark.parametrize("name,mutate", [
    ("place_a_class",        _mutate_place_the_unplaceable),
    ("unplace_a_class",      _mutate_unplace_a_placed_lesson),
    ("create_a_conflict",    _mutate_create_a_room_conflict),
    ("tighten_a_constraint", _mutate_tighten_a_constraint),
    ("add_a_day",            _mutate_add_a_day),
    ("shorten_the_day",      _mutate_shorten_the_day),
    ("add_a_class",          _mutate_add_a_class),
    ("add_a_year_branch",    _mutate_add_a_branch),
    ("switch_language",      _mutate_switch_language),
])
def test_every_input_the_panel_reads_still_updates_it(small_window, name, mutate):
    """The anti-staleness contract for any fingerprint guard added here.

    ``switch_language`` is the case a hand-rolled field tuple gets wrong: the
    panel's text comes from ``tr()`` and ``day_label()``, which are not in the
    state at all. ``_state_fingerprint()`` covers it (it hashes
    ``get_language()``); ``_open_slots_fingerprint()`` does not — see section 3.

    ``shorten_the_day`` is the case a placement-only fingerprint gets wrong: the
    heavy-day threshold is ``len(state["slots"]) * 0.75``, so shortening the day
    changes which days are called heavy without moving a single lesson.
    """
    from scheduler_app.translations import set_language

    before = _derived(small_window)
    assert before, "fixture produced no derived findings to compare against"
    try:
        mutate(small_window)
        small_window.refresh_grid()
        after = _derived(small_window)
    finally:
        set_language("tr")

    assert after != before, (
        "changing %s left the warnings sidebar showing the identical %d "
        "findings. Either this input does not need to be in the fingerprint, or "
        "the sidebar is stale.\n  before: %r\n  after:  %r"
        % (name, len(before), before[:3], after[:3]))


def test_the_clear_button_does_not_switch_the_sidebar_off_for_good(small_window):
    """The trap the obvious fix falls into. GREEN today; RED under a naive guard.

    ``WarningLogPanel._clear_btn`` is wired straight to ``clear()``, which empties
    ``_derived`` and tells nobody. The panel's content is therefore an input to
    ``_refresh_warnings`` that is not in ``state_data`` and not in
    ``_state_fingerprint()``.

    Measured with a naive ``if fp == self._warnings_fp: return`` bolted onto
    ``_refresh_warnings``: 29 findings cleared, and the next refresh_grid put
    back **0** of them. Today, with no guard, it puts back all 29.
    """
    before = _derived(small_window)
    assert before, "fixture produced no derived findings to clear"

    small_window.warning_log._clear_btn.click()
    assert _derived(small_window) == [], "Clear did not clear the derived findings"

    small_window.refresh_grid()

    assert _derived(small_window) == before, (
        "after the panel's Clear button, the next refresh restored %d of the %d "
        "findings this timetable still has. A fingerprint guard that returns "
        "early cannot see that the panel was emptied, so the sidebar stays "
        "blank for the rest of the session — a stale sidebar is worse than a "
        "slow one." % (len(_derived(small_window)), len(before)))


# ── 3. the guard the handoff says to copy ───────────────────────────────────

def test_the_open_slots_fingerprint_is_blind_to_the_ui_language(small_window):
    """RED today — and it is why ``_open_slots_fingerprint`` is not the model.

    ``_open_slots_fingerprint`` hashes days, slots, classrooms, the class count,
    the placements and the selection. The panel it guards renders
    ``display_day(day).upper()`` and two ``tr()`` strings, none of which are in
    that tuple. ``_set_language`` calls ``_refresh_open_slots()`` and then
    ``refresh_grid()``; both hit the guard, both find the same fingerprint, and
    both return without redrawing.

    Measured: day headers ``['PAZARTESI', 'SALI', 'ÇARŞAMBA']`` before the
    switch to English and ``['PAZARTESI', 'SALI', 'ÇARŞAMBA']`` after it.

    Recorded here rather than fixed here: this is the shape item 8 was told to
    copy, and copying it verbatim would put the same bug in the warnings panel.
    ``_state_fingerprint()`` is the shape that does not have it.
    """
    from scheduler_app.translations import set_language

    def headers():
        layout = small_window._open_slots_layout
        out = []
        for i in range(layout.count()):
            item = layout.itemAt(i)
            widget = item.widget() if item is not None else None
            if widget is not None and hasattr(widget, "text"):
                out.append(widget.text())
        return out

    before = headers()
    assert before, "the open-slots panel drew nothing to compare"
    try:
        set_language("en")
        small_window._refresh_open_slots()   # exactly what _set_language does
        after = headers()
    finally:
        set_language("tr")

    assert after != before, (
        "the open-slots panel is still rendering the previous language after a "
        "language switch: %r. _open_slots_fingerprint hashes days, slots, "
        "classrooms, the class count, the placements and the selection — and "
        "not get_language() — so the guard skips the redraw the switch asked "
        "for." % (before[:3],))
