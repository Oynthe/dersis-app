"""The warning log must describe the timetable that exists **now**, exactly once.

ST-PERF-003 (High) · ``app.py:3117-3154`` (``_refresh_warnings``),
``app.py:3156-3214`` (``_run_auto_negotiation``), ``widgets.py:213-239``
(``WarningLogPanel.log``)
    ``_refresh_warnings`` **appends** to a list that is never reset, and
    ``WarningLogPanel.log`` re-renders the whole list into fresh HTML on every
    single append. Refresh *k* therefore stores *k* copies of the same warnings
    and re-renders *k* times as much text as refresh 1 — quadratic work and
    unbounded memory for a panel whose entire content is a pure function of the
    current state. The audit measured 12 refreshes on the 250-class state
    growing ``warning_log._messages`` 138 → 1656 (+138 each), process RSS
    +480 MB, and per-refresh time 2081 ms → 4816 ms.

ST-PERF-006 (Medium) · ``app.py:2993-3115`` (``_refresh_open_slots``)
    The open-slots panel tears down and rebuilds its entire widget tree, and
    re-runs the full occupancy/validity analysis, on **every** refresh — even
    when nothing about the state has changed.

Why this matters to a user: ``refresh_grid`` is not a rare event. It runs after
every edit, every drag, every delete, every setup change and every autosave. So
the panel that is supposed to tell a teacher "Monday is overloaded" instead
accumulates a scrollback of every *past* Monday, including days that were fixed
half an hour ago, and the app gets measurably slower the longer it stays open.

What "correct" means here (the decision this module encodes)
------------------------------------------------------------
"Just call ``warning_log.clear()`` at the top of ``_refresh_warnings``" is the
obvious fix and it is **wrong**, because the panel holds two different kinds of
content:

* **Derived** — the workload warnings and the auto-negotiation diagnostics.
  These are a pure function of ``state_data`` and *must* be rebuilt: a heavy-day
  warning has to vanish the moment the day stops being heavy (section 3 below).
* **Historical** — everything pushed by an event that happened once and will
  never be recomputed: ``_show_toast`` mirrors, import results, optimizer
  negotiation outcomes, and above all ``_report_settings_problem``, which is the
  only channel telling a user their work is *not being saved*. Wiping those on
  the next repaint would replace a performance bug with a data-loss bug.

Every assertion below is therefore phrased as *"the log holds what the current
state implies, and nothing else"* — never as *"the log is empty"*.

Conventions
-----------
* **fail-now / pass-after**, no ``xfail``: both findings are being fixed in
  Phase 2.
* **No wall-clock assertion in the fast lane.** Growth in stored-message count
  and in rendered-document size *is* the O(n²) rebuild; asserting on those is
  exact, so it cannot flake on a loaded CI box. Exactly one timing assertion
  exists (``test_refresh_cost_does_not_grow_with_refresh_count``), it is
  ``slow``, it compares medians rather than samples, and its threshold is
  argued in its own docstring.
* Every count assertion carries a non-vacuity guard, so a rewrite that moves the
  message store somewhere this module cannot see fails loudly instead of
  passing on an empty list. **No non-vacuity guard is expressed in
  milliseconds**: a guard of the form "this took at least N ms, so real work
  happened" turns into a false failure the moment the fix makes the work fast.
  (The measured margin was thin — a fixed warnings pass over the 250-class
  state takes ~40 ms, and the plan additionally proposes caching the settings
  read that is most of that.)
* Each finding is pinned from **both** directions: something must stop growing,
  *and* something must still change when the state changes. A fix that stops
  doing the work by not doing the job passes half of that and fails the other.
* The state is **never** mutated between the refreshes of a growth test. That is
  the whole point: identical input, identical output, N times over.

Coupling to private attributes
------------------------------
``warning_log._messages`` is named in the finding itself and in the roadmap's
completion criteria ("no unbounded ``warning_log._messages`` growth"), so this
module reads it deliberately. ``_stored()`` falls back to the panel's rendered
document if the attribute is renamed, so a rewrite gets a real assertion rather
than a silent pass.

Status and discrimination (verified empirically, not argued)
------------------------------------------------------------
Four fixes were built as pytest plugins that monkeypatch the production classes
— no production file was touched — and this module was run against each.

============================================  ======  ======
Tree                                          passed  failed
============================================  ======  ======
as it stands today                                 7      13
``_refresh_warnings``/``_refresh_open_slots``
stubbed to do nothing (vacuity probe)              1      19
*lazy* fix: ``warning_log.clear()`` first          18       2
*fingerprint* fix keyed on grid shape only         19       1
*full* fix, notice posted once                    20       0
============================================  ======  ======

Read the middle three rows as the three ways this can be got wrong.

* **Vacuity.** With both refresh methods stubbed out, 19 of 20 fail. The one
  survivor is ``test_the_fixture_state_survives_a_refresh``, which is a harness
  premise ("``refresh_grid`` does not mutate the state") and claims nothing
  about the panels.
* **The lazy fix** wipes the panel and rebuilds only the derived half. Exactly
  the two tests that exist to catch it fail: the "your settings could not be
  saved" notice is destroyed by the next repaint (1 -> 0), and ST-PERF-006 is
  untouched.
* **The fingerprint fix keyed on ``days``/``slots``/``classrooms``** is the
  plausible ST-PERF-006 mistake: none of those change while a user drags
  lessons, so the open-slots panel is built once and frozen for the session.
  Every other assertion in this module passes;
  ``test_open_slots_panel_still_updates_when_the_state_changes`` is the only
  thing standing between that and a green suite.
* **The full fix** passes all 20 in 6.5 s, against 22.9 s today.

One more trap, worth naming here because it bites the *recommended* fix rather
than a sloppy one: giving ``WarningLogPanel`` the ``add()`` method that
``_report_settings_problem`` already calls makes the settings notice arrive down
two channels (``add()`` and the ``_show_toast`` mirror) and the user reads it
twice. Measured: that variant scores 19/1, failing
``test_settings_failure_is_reported_once_and_survives_the_rebuild`` at 2 hits —
and suppressing the mirror with a ``_show_toast(..., log=False)`` switch turns
three tests in ``tests/test_settings_recovery.py`` red, because Phase 1's
ST-DATA-005 probe replaces ``_show_toast`` with a spy that has no such keyword.
The variant that works — sticky/derived split, the redundant ``log.add(...)``
call sites deleted — scores 20/0 here and 136/0 across
``test_settings_recovery`` + ``test_state_transactions`` + ``test_import_ui_flow``
+ ``test_setup_reconcile`` + ``test_grid_integrity`` + this module.

Runtime: 22.9–23.4 s for the whole module across six runs, 13.4–14.2 s for
``-m "not slow"`` (the CI lane); on a box with sixteen CPU burners saturating
twelve cores the whole module took 47.5 s. All of that spread is in the two
``slow`` tests, which is why they are marked.
"""
import copy
import statistics
import time

import pytest

pytestmark = [pytest.mark.ui]


# A token that can only come from the settings-write failure this module
# injects, so the rate-limit assertion cannot match some unrelated message.
_SENTINEL = "ST-PERF-003-SENTINEL"

# 20 refreshes is roughly one minute of ordinary editing — every edit, drag,
# delete and autosave triggers one — and a little past the 12 the audit used.
_REFRESHES = 20


# ── Helpers ─────────────────────────────────────────────────────────────────

def _rendered(panel):
    """The text the expanded log area actually shows the user."""
    area = getattr(panel, "_log_area", None)
    if area is not None and hasattr(area, "toPlainText"):
        return area.toPlainText()
    raw = getattr(panel, "_messages", None) or []
    return "\n".join(
        m[0] if isinstance(m, (tuple, list)) else str(m) for m in raw)


def _stored(panel):
    """Every message the panel is holding, oldest first, as plain strings."""
    raw = getattr(panel, "_messages", None)
    if raw is None:
        # The store was renamed by a rewrite. Fall back to what the panel
        # renders so these assertions keep biting instead of silently passing.
        return [ln for ln in _rendered(panel).splitlines() if ln.strip()]
    return [m[0] if isinstance(m, (tuple, list)) else str(m) for m in raw]


def _counts(panel):
    """``{message: how many copies the panel is holding}``."""
    out = {}
    for msg in _stored(panel):
        out[msg] = out.get(msg, 0) + 1
    return out


def _flush_deleted(qapp):
    """Actually run the deletions ``deleteLater()`` only *posts*.

    ``QApplication.processEvents()`` deliberately skips ``DeferredDelete``
    events posted outside the current loop level, so without this the widget
    counts below would measure the harness rather than the app.
    """
    from PyQt6.QtCore import QCoreApplication, QEvent

    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _open_slots_widgets(window):
    """Live child widgets of the open-slots panel."""
    from PyQt6.QtWidgets import QWidget

    return window._open_slots_layout.parentWidget().findChildren(QWidget)


def _open_slot_rows(window):
    """What the open-slots panel is *telling the user*: ``[(day, time, room)]``.

    Read out of the live layout in order — a day header ``QLabel`` followed by
    the ``slotRow`` widgets that belong to it. This is content, not mechanism,
    so it holds whether the panel is rebuilt, reused, or skipped.
    """
    from PyQt6.QtWidgets import QLabel

    layout = window._open_slots_layout
    rows = []
    day = None
    for i in range(layout.count()):
        w = layout.itemAt(i).widget()
        if w is None:
            continue
        if w.objectName() == "slotRow":
            labels = w.findChildren(QLabel)
            if len(labels) >= 2:
                rows.append((day, labels[0].text(), labels[-1].text()))
        elif isinstance(w, QLabel):
            day = w.text()
    return rows


def _place(cls, day, slot, room):
    cls["placed"] = True
    cls["placed_day"] = day
    cls["placed_time"] = slot
    cls["placed_classroom"] = room


def _place_greedily(state):
    """Pack every placeable class into the first free room-slot, deterministically.

    Deliberately not the optimizer: this module is about repaint cost, and a
    solver run would add minutes of runtime plus a dependency on ST-SCHED-013's
    seeding. Packing from the front also concentrates the load onto the early
    days, which is exactly the shape that makes the workload warnings fire.
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
        for day in state["days"]:
            for start in range(0, n_slots - span + 1):
                for room in state["classrooms"]:
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


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def message_boxes(monkeypatch):
    """Neutralize every modal a refresh can raise.

    An unpatched modal blocks the whole suite under the offscreen platform, so
    this is a hard requirement. ``_report_settings_problem`` raises one, and
    ``closeEvent`` raises another when autosave has been made to fail.
    """
    from PyQt6.QtWidgets import QDialog, QMessageBox

    from scheduler_app.ui.tier_enforcement import UpgradeDialog

    for name, ret in (("information", QMessageBox.StandardButton.Ok),
                      ("warning", QMessageBox.StandardButton.Ok),
                      ("critical", QMessageBox.StandardButton.Ok),
                      ("question", QMessageBox.StandardButton.Yes)):
        monkeypatch.setattr(
            QMessageBox, name, staticmethod(lambda *a, _r=ret, **k: _r))
    monkeypatch.setattr(
        UpgradeDialog, "exec", lambda self: QDialog.DialogCode.Rejected.value)


_TIER_REGISTRIES = (
    "_gated_widgets", "_gated_actions", "_on_tier_changed",
    "_export_submenu_refreshers",
)


@pytest.fixture
def window(qapp, dersis_home, message_boxes, monkeypatch):
    """A real, fully constructed ``SchedulerApp`` with a grid but no classes.

    The first-run controller is disarmed (it is otherwise fired by a QTimer in
    ``__init__`` and would pop the setup wizard), and the licence tier is pinned
    so no entity-limit gate can short-circuit a repaint.

    Isolation note: every ``SchedulerApp`` registers gated ``QAction``s and a
    tier-change callback into the *process-wide* ``TierEnforcement`` singleton
    and never unregisters them, so the registries are snapshotted and restored —
    otherwise this module would leak dead QActions into whatever runs next in
    the same session.
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
        # ``processEvents()`` alone does NOT run a posted DeferredDelete, so
        # without the flush every window this module builds stays alive for the
        # rest of the session and each successive ``SchedulerApp()`` gets
        # slower (measured: 1.1 s for the first, 15.4 s for the twelfth).
        _flush_deleted(qapp)
        enforcer._tier_slug, enforcer._tier_confirmed = prev_slug, prev_confirmed
        for name, value in prev_registries.items():
            setattr(enforcer, name, value)


@pytest.fixture
def seeded(window):
    """``window`` plus a timetable with two *known*, checkable warning conditions.

    Three one-hour lessons for Year-1/A, all on Monday, on a 5-day × 4-slot
    grid. ``_refresh_warnings`` calls a day "heavy" at >= 75 % of the slots, so
    Monday (3 of 4) fires, and Tue–Fri fire the "empty day" warning.

    Plus one class that cannot be placed at all — every day of the grid is in
    its ``excluded_days`` — which is what makes ``_run_auto_negotiation`` emit
    its per-class diagnostics. Without it the negotiation half of the refresh
    would never be exercised.

    Why *excluded* days and not ``allowed_days=["saturday"]``, which reads more
    naturally: ``refresh_grid`` -> ``_auto_save`` -> ``normalize_state_day_keys``
    strips off-grid values out of ``allowed_days`` (``day_keys.py:78``), so a
    Saturday-only class silently becomes an unrestricted one on the first
    repaint and the diagnostics change between refresh 1 and refresh 2. That
    would make every "same state, same warnings" assertion below measure the
    wrong thing. ``test_the_fixture_state_survives_a_refresh`` pins the
    stability this fixture depends on.

    Returns ``(window, ghost_class)``.
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

    for i in range(3):
        _place(add(f"Ders {i + 1}"), DAY_KEYS[0], state["slots"][i], "R001")
    ghost = add("Ders Imkansiz", lecturer="Lect-2",
                excluded_days=list(DAY_KEYS[:5]))
    return window, ghost


@pytest.fixture
def large(window, make_preset):
    """``window`` loaded with the audit's 250-class state, mostly placed.

    The finding's own scale. ``state_data`` is updated in place rather than
    replaced, because the workflow object built in ``__init__`` holds a
    reference to that exact dict.
    """
    state = make_preset("large")
    window.state_data.clear()
    window.state_data.update(state)
    placed = _place_greedily(window.state_data)
    assert placed > 100, f"fixture placed only {placed} classes; warnings would be thin"
    return window


# ══════════════════════════════════════════════════════════════════════════
#  0. The premise every growth test below rests on
# ══════════════════════════════════════════════════════════════════════════

def test_the_fixture_state_survives_a_refresh(seeded):
    """ST-PERF-003 — a harness guard, currently green.

    Every assertion in this module is of the form "identical input, identical
    output, N times over". That is only meaningful if ``refresh_grid`` really
    does leave the state alone — and it does not always: ``_auto_save`` runs
    ``normalize_state_day_keys``, which rewrites class day fields. If this test
    ever goes red the fixture has drifted and the growth numbers below are
    measuring a moving target rather than the leak.
    """
    window, _ = seeded

    window.refresh_grid()
    before = copy.deepcopy(window.state_data)
    for _ in range(3):
        window.refresh_grid()

    assert window.state_data == before, (
        "refresh_grid mutated the state, so 'unchanged input' below is false")


# ══════════════════════════════════════════════════════════════════════════
#  1. Bounded state — the core assertion
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("n_refreshes", [2, 3, 5, _REFRESHES])
def test_repeated_refresh_does_not_grow_the_warning_log(seeded, n_refreshes):
    """ST-PERF-003 — N refreshes of an unchanged timetable must store what 1 stores.

    A failure means the warning panel grows without bound for as long as the
    app is open: the user's "Monday is overloaded" notice is repeated once per
    repaint until the panel is holding thousands of copies and the window is
    visibly slower.
    """
    window, _ = seeded

    window.refresh_grid()
    baseline = len(_stored(window.warning_log))
    assert baseline >= 2, (
        "fixture produced no warnings to count — the assertion below would be "
        f"vacuous (stored: {_stored(window.warning_log)!r})")

    for _ in range(n_refreshes - 1):
        window.refresh_grid()
    after = len(_stored(window.warning_log))

    assert after == baseline, (
        f"{n_refreshes} refreshes of an unchanged state stored {after} messages "
        f"where 1 refresh stored {baseline} "
        f"(+{(after - baseline) / (n_refreshes - 1):.0f} per refresh); the log "
        "is appended to and never rebuilt")


def test_warning_log_growth_is_flat_not_linear(seeded):
    """ST-PERF-003 — the stored-message count must not track the refresh count.

    The trend form of the test above: a failure means every extra repaint costs
    the user another block of duplicated warnings, for ever.
    """
    window, _ = seeded

    counts = []
    for _ in range(_REFRESHES):
        window.refresh_grid()
        counts.append(len(_stored(window.warning_log)))

    assert counts[0] >= 2, f"fixture produced no warnings: {counts!r}"
    assert max(counts) == min(counts), (
        f"stored-message count over {_REFRESHES} refreshes went "
        f"{counts[0]} -> {counts[-1]}; it must stay {counts[0]}. Full trace: {counts}")


# ══════════════════════════════════════════════════════════════════════════
#  2. Duplicates do not accumulate, and the rendered panel stays one size
# ══════════════════════════════════════════════════════════════════════════

def test_repeated_refresh_does_not_duplicate_warnings(seeded):
    """ST-PERF-003 — the same state must never yield two copies of one warning.

    A failure means the user scrolls the panel and reads the same sentence
    twenty times over, with no way to tell which copy is current.
    """
    window, _ = seeded

    window.refresh_grid()
    once = _counts(window.warning_log)
    assert once, "fixture produced no warnings to compare"

    for _ in range(_REFRESHES - 1):
        window.refresh_grid()
    many = _counts(window.warning_log)

    repeated = {msg: (many[msg], once.get(msg, 0))
                for msg in many if many[msg] > once.get(msg, 0)}
    assert not repeated, (
        f"after {_REFRESHES} refreshes these messages are stored more often "
        f"than one refresh produces them (msg -> (now, after 1 refresh)): "
        f"{repeated}")
    assert many == once, (
        f"the message multiset changed although the state did not: "
        f"{once} -> {many}")


def test_rendered_panel_content_stays_the_same_for_the_same_state(seeded):
    """ST-PERF-003 — the panel's rendered document must be a function of the state.

    A failure means the HTML the panel re-renders on every append keeps growing,
    which is the O(n²) half of the finding — the part the user feels as a
    freeze rather than as clutter.
    """
    window, _ = seeded

    window.refresh_grid()
    once = _rendered(window.warning_log)
    assert len(once) > 20, f"fixture rendered nothing to compare: {once!r}"

    for _ in range(_REFRESHES - 1):
        window.refresh_grid()
    many = _rendered(window.warning_log)

    assert len(many) == len(once), (
        f"the rendered log grew from {len(once)} to {len(many)} characters "
        f"over {_REFRESHES} refreshes of an unchanged state "
        f"({(len(many) - len(once)) / (_REFRESHES - 1):.0f} chars per refresh)")
    assert many == once, "same state, different rendered log"


# ══════════════════════════════════════════════════════════════════════════
#  3. Content is still correct — the trap in "just clear it"
# ══════════════════════════════════════════════════════════════════════════

def test_known_warning_is_present_after_every_single_refresh(seeded):
    """ST-PERF-003 — a warning that still applies must survive every rebuild.

    A failure means the fix threw the baby out: the user's overloaded Monday
    stops being reported at all, which is worse than reporting it twice.
    """
    from scheduler_app.translations import tr
    from scheduler_app.ui.day_keys import DAY_KEYS, day_label

    window, _ = seeded
    heavy = f"{tr('warnings.heavy_days_short')} {day_label(DAY_KEYS[0])}"

    for n in range(1, _REFRESHES + 1):
        window.refresh_grid()
        text = "\n".join(_stored(window.warning_log))
        assert heavy in text, (
            f"the overloaded-Monday warning ({heavy!r}) is missing after "
            f"refresh {n}; the panel holds {_stored(window.warning_log)!r}")


def test_unplaceable_class_diagnostic_is_present_after_every_refresh(seeded):
    """ST-PERF-003 — the auto-negotiation diagnostics must survive every rebuild.

    ``_run_auto_negotiation`` is the other producer feeding this panel. A
    failure means a class that cannot be scheduled at all stops being reported,
    so the user has no idea why their timetable is incomplete.
    """
    window, ghost = seeded

    for n in range(1, 6):
        window.refresh_grid()
        text = "\n".join(_stored(window.warning_log))
        assert ghost["name"] in text, (
            f"the unplaceable class {ghost['name']!r} is not reported after "
            f"refresh {n}; the panel holds {_stored(window.warning_log)!r}")


def test_warning_disappears_once_the_condition_is_fixed(seeded):
    """ST-PERF-003 — this is the bug, stated positively.

    The log describes a timetable that no longer exists. A failure means the
    user fixes the overloaded Monday, places the impossible class, and the panel
    still tells them both problems are outstanding — for the rest of the
    session, with the stale copy sitting above the fresh one.
    """
    from scheduler_app.translations import tr
    from scheduler_app.ui.day_keys import DAY_KEYS, day_label

    window, ghost = seeded
    state = window.state_data

    window.refresh_grid()
    stale_heavy = f"{tr('warnings.heavy_days_short')} {day_label(DAY_KEYS[0])}"
    before = "\n".join(_stored(window.warning_log))
    assert stale_heavy in before, "fixture never produced the heavy-day warning"
    assert ghost["name"] in before, "fixture never produced the unplaceable report"

    # The user fixes both problems: the impossible class gets a slot on Tuesday,
    # and one of Monday's three lessons moves to Wednesday so Monday drops to
    # 2 of 4 slots — below the 75 % "heavy" threshold.
    ghost["excluded_days"] = []
    _place(ghost, DAY_KEYS[1], "09:00", "R002")
    _place(state["classes"][2], DAY_KEYS[2], "09:00", "R001")

    window.refresh_grid()
    after = _stored(window.warning_log)
    text = "\n".join(after)

    assert stale_heavy not in text, (
        f"Monday is no longer overloaded but the panel still says so: {after!r}")
    assert ghost["name"] not in text, (
        f"{ghost['name']!r} is now placed but the panel still reports it as "
        f"unplaceable: {after!r}")
    # ...and the log was rebuilt, not merely emptied: Thursday and Friday are
    # still free, so that warning must be there.
    assert tr("warnings.empty_days_short") in text, (
        "the empty-day warning that DOES still apply is gone too — the log was "
        f"wiped instead of rebuilt: {after!r}")


def test_settings_failure_is_reported_once_and_survives_the_rebuild(
        seeded, monkeypatch):
    """ST-PERF-003 — a rebuild must not erase what only happened once.

    Phase 1's ``_report_settings_problem`` is rate-limited *because of this
    finding* (one entry per failure kind per session, not one per autosave).
    Three ways to break it, all checked here: a rebuild that wipes the whole
    panel drops the count to 0, dropping the rate limit pushes it to one per
    refresh, and posting the notice down two channels at once makes it 2. A
    failure at 0 is the serious one — that message is the only place the app
    tells a user their work is not being saved.

    The count-of-2 case is not hypothetical and it is the trap in the obvious
    implementation. ``_report_settings_problem`` (app.py:1869) already calls
    ``warning_log.add(message)`` — a method ``WarningLogPanel`` does not have,
    so today it raises inside ``except Exception: pass`` and the message reaches
    the panel only via the ``_show_toast`` mirror at app.py:3980. The moment the
    fix gives the panel a real ``add()`` as its sticky channel, *both* paths
    fire and the user reads the same alarming sentence twice. Measured: that
    variant scores 2 here.

    The remedy is to delete the ``log.add(...)`` blocks at app.py:1838-1842 and
    app.py:1869-1873 and let the toast mirror be the one channel — **not** to
    give ``_show_toast`` a ``log=False`` switch. ``tests/test_settings_recovery.py``
    (Phase 1, ST-DATA-005) replaces ``_show_toast`` with a spy of signature
    ``(self, message, kind="info")`` and spies on ``WarningLogPanel.log``, not on
    ``add``; a new keyword argument or a new channel makes that module report
    "the user is never told". Both variants were built and run — the switch
    reddens three ST-DATA-005 tests, the deletion keeps all 11 green.

    The heavy-day assertion is what makes the survival claim mean something: it
    proves a derived rebuild really happened around the sticky message rather
    than the message simply sitting in a panel nobody touched.
    """
    from scheduler_app import storage
    from scheduler_app.translations import tr
    from scheduler_app.ui.day_keys import DAY_KEYS, day_label

    window, _ = seeded

    def explode(*args, **kwargs):
        raise OSError(_SENTINEL)

    monkeypatch.setattr(storage, "save_encrypted", explode)

    for _ in range(_REFRESHES):
        window.refresh_grid()
        # ST-PERF-002 coalesced autosave behind a debounce timer, so a refresh
        # only REQUESTS a write. Flushing here keeps this test doing what its
        # name says — _REFRESHES genuinely attempted, genuinely failing saves — which is
        # what makes the count discriminating: 0 means a rebuild wiped the
        # notice, 2 means two channels, _REFRESHES means the rate limit was lost.
        window.flush_auto_save()

    stored = _stored(window.warning_log)
    hits = [m for m in stored if _SENTINEL in m]
    assert len(hits) == 1, (
        f"the settings-write failure is in the log {len(hits)} times after "
        f"{_REFRESHES} failing autosaves; it must be there exactly once "
        f"(0 = a rebuild wiped it, 2 = it was posted down two channels, "
        f"{_REFRESHES} = the rate limit was lost). Panel holds: {stored!r}")

    heavy = f"{tr('warnings.heavy_days_short')} {day_label(DAY_KEYS[0])}"
    assert any(heavy in m for m in stored), (
        f"the derived overloaded-Monday warning is missing, so this test never "
        f"observed a rebuild for the sticky message to survive: {stored!r}")


def test_user_clear_still_empties_the_panel(seeded):
    """ST-PERF-003 — the panel's own Clear button must keep working.

    A guard, currently green. A failure means the fix took the log away from the
    user: they press Clear and the panel still shows the old text.
    """
    window, _ = seeded

    window.refresh_grid()
    assert _stored(window.warning_log), "fixture produced nothing to clear"

    window.warning_log.clear()

    assert _stored(window.warning_log) == [], (
        f"Clear left {_stored(window.warning_log)!r} behind")
    assert _rendered(window.warning_log).strip() == "", (
        f"Clear left rendered text behind: "
        f"{_rendered(window.warning_log)!r}")


# ══════════════════════════════════════════════════════════════════════════
#  4. Rebuild cost does not grow with the refresh count
# ══════════════════════════════════════════════════════════════════════════

def test_document_the_panel_must_rerender_does_not_grow(seeded):
    """ST-PERF-003 — the exact, non-flaky statement of "refresh 20 costs what refresh 1 did".

    ``WarningLogPanel.log`` rebuilds the *entire* document from ``_messages`` on
    every append, so the cost of one refresh is proportional to the size of the
    document it renders. Measuring that size after each refresh is therefore an
    algorithmic witness for the rebuild cost — and unlike a stopwatch it is
    exact, so it cannot flake on a shared runner.

    A failure means each successive repaint asks Qt to lay out more text than
    the last one, without limit; that is what turned a 2.1 s refresh into a
    4.8 s refresh in the audit.
    """
    window, _ = seeded

    sizes = []
    for _ in range(_REFRESHES):
        window.refresh_grid()
        sizes.append(len(_rendered(window.warning_log)))

    assert sizes[0] > 20, f"fixture rendered nothing measurable: {sizes!r}"
    assert max(sizes) == min(sizes), (
        f"the document the panel re-renders grew {sizes[0]} -> {sizes[-1]} "
        f"characters ({sizes[-1] / sizes[0]:.1f}x) over {_REFRESHES} refreshes "
        f"of an unchanged state. Full trace: {sizes}")


@pytest.mark.slow
def test_refresh_cost_does_not_grow_with_refresh_count(large):
    """ST-PERF-003 — the one wall-clock assertion, at the finding's own scale.

    A failure means what the user reports as "it gets slower the longer I leave
    it open": at 250 classes the audit measured refresh 1 at 2081 ms and refresh
    12 at 4816 ms with nothing changed in between.

    Why this is not flaky, and why 2.0x with an absolute floor:

    * It times ``_refresh_warnings`` rather than the whole ``refresh_grid``.
      That is the function the finding is about, and leaving out the grid
      repaint and the encrypted autosave removes the two biggest sources of
      variance on a shared runner — while *strengthening* the signal, because
      those two costs are constant per refresh and only dilute the ratio.
    * It compares the **median of the last 5** passes with the **median of the
      first 5**, so a single scheduler hiccup cannot decide the result.
    * It is a *ratio measured inside one process on one machine*, so it does not
      depend on how fast the runner is — only on whether the work grows.
    * Measured spread on this loop, seven runs. **Unfixed**: 3.73, 4.20, 4.62,
      4.70, 5.36 idle, and 3.06 / 5.65 with sixteen CPU burners saturating a
      12-core box. **Fixed** (derived warnings rebuilt, nothing accumulated):
      0.95 under that same load, trace ``[242, 217, 246, ... 200, 210]`` ms —
      flat. 2.0x sits in the empty middle with 2x headroom on the fixed side
      and a 1.5x margin below the worst unfixed measurement. It was 2.5x in the
      first draft of this module; the loaded run that came out at 3.06x showed
      that margin was thinner than advertised, and contention *shrinks* the
      ratio (it inflates the cheap head passes more than the expensive tail
      ones), so the threshold moved down rather than up.
    * ``tail <= head + 25 ms`` is accepted regardless of the ratio. Once the
      finding is fixed a warnings pass may become cheap enough that a few
      milliseconds of noise is a large *ratio*; 25 ms of absolute growth is not
      a user-visible regression and must not redden the suite. Non-vacuity is
      guarded by the message count below, not by the clock, so this disjunct
      cannot make the test pass on an empty fixture.
    """
    window = large

    times = []
    for _ in range(_REFRESHES):
        start = time.perf_counter()
        window._refresh_warnings()
        times.append((time.perf_counter() - start) * 1000)

    # Non-vacuity, deliberately *not* expressed in milliseconds: a fix that
    # makes the pass fast must not trip the guard that proves work happened.
    produced = len(_stored(window.warning_log))
    assert produced > 10, (
        f"the 250-class fixture left only {produced} messages in the panel, so "
        f"the warnings pass did almost nothing and the timings below measure "
        f"nothing. Times (ms): {[round(t) for t in times]}")

    head = statistics.median(times[:5])
    tail = statistics.median(times[-5:])

    assert tail <= max(2.0 * head, head + 25.0), (
        f"the warnings pass grew {tail / head:.2f}x with nothing changed: median "
        f"of the first 5 passes {head:.0f} ms, median of the last 5 "
        f"{tail:.0f} ms. Full trace (ms): "
        f"{[round(t) for t in times]}")


# ══════════════════════════════════════════════════════════════════════════
#  5. Memory and widgets stay bounded
# ══════════════════════════════════════════════════════════════════════════

def test_stored_character_volume_stays_bounded(seeded):
    """ST-PERF-003 — the in-memory footprint of the log must not grow.

    RSS is the number the audit quoted (+480 MB over 12 refreshes), but it
    cannot be measured portably here — ``psutil`` is not in the environment, and
    a threshold on OS-reported RSS would flake on any runner with a different
    allocator or GC timing. The volume of text the panel *holds* is the driver
    of that growth and is exactly measurable, so that is what is asserted.

    A failure means an app left open all afternoon is holding megabytes of
    duplicated warning text, plus the Qt document built from it.
    """
    window, _ = seeded

    window.refresh_grid()
    once = sum(len(m) for m in _stored(window.warning_log))
    assert once > 20, f"fixture stored nothing measurable ({once} chars)"

    for _ in range(_REFRESHES - 1):
        window.refresh_grid()
    many = sum(len(m) for m in _stored(window.warning_log))

    assert many == once, (
        f"the log holds {many} characters after {_REFRESHES} refreshes where "
        f"one refresh holds {once} ({many / once:.1f}x), with the state "
        f"untouched throughout")


@pytest.mark.slow
def test_large_state_warning_log_stays_bounded(large):
    """ST-PERF-003 — the bounded-state assertion at the scale that was measured.

    The audit's own reproduction: 12 successive refreshes on the 250-class
    state. A failure means a department-sized timetable accumulates a hundred
    or more redundant warnings per repaint.
    """
    window = large

    window.refresh_grid()
    once = len(_stored(window.warning_log))
    assert once > 10, f"the 250-class fixture produced only {once} warnings"

    for _ in range(11):
        window.refresh_grid()
    after = len(_stored(window.warning_log))

    assert after == once, (
        f"12 refreshes of the 250-class state stored {after} messages where 1 "
        f"stored {once} (+{(after - once) / 11:.0f} per refresh)")


def test_unchanged_refresh_does_not_rebuild_the_open_slots_panel(seeded, qapp):
    """ST-PERF-006 — an unchanged state must not cost a full panel rebuild.

    ``_refresh_open_slots`` deletes every child widget and re-runs the whole
    occupancy analysis on each call, even when the state and the selection are
    byte-for-byte what they were. The audit measured 359 widgets and a 4.5 s
    warnings pass at 250 classes; ``refresh_grid`` runs on every edit and every
    autosave.

    This asserts the property rather than a mechanism: the widgets that were
    showing the correct answer are still alive afterwards. Both plausible fixes
    satisfy it — reuse the widgets, or skip the rebuild when a state fingerprint
    is unchanged. A failure means the user pays for hundreds of widget
    constructions and a full re-analysis to be shown exactly what they were
    already looking at.
    """
    from PyQt6 import sip

    window, _ = seeded

    window.refresh_grid()
    _flush_deleted(qapp)
    before = _open_slots_widgets(window)
    assert len(before) > 20, (
        f"the open-slots panel built only {len(before)} widgets; the assertion "
        "below would be vacuous")

    window.refresh_grid()
    _flush_deleted(qapp)

    destroyed = [w for w in before if sip.isdeleted(w)]
    assert not destroyed, (
        f"a refresh with an unchanged state destroyed {len(destroyed)} of "
        f"{len(before)} open-slot widgets and rebuilt them from scratch")


def test_open_slots_panel_still_updates_when_the_state_changes(seeded, qapp):
    """ST-PERF-006 — the other half: skipping work must not mean skipping the answer.

    A guard, currently green, and it is the one that decides whether the
    ST-PERF-006 fix is a fix or a regression. The test above asks the panel to
    stop rebuilding when nothing changed; the cheapest way to satisfy that is a
    state fingerprint, and the cheapest *wrong* fingerprint is one built from
    ``days``/``slots``/``classrooms`` — none of which change while a user drags
    lessons around. With such a fingerprint the panel is built once and then
    frozen for the rest of the session, every ST-PERF-006 assertion still
    passes, and the user is looking at a list of "free" slots that are not free.

    So: place a class into a slot the panel is currently offering, refresh, and
    require that exactly that ``(day, time, room)`` disappears and nothing else
    moves. A failure means the open-slots panel is lying about the timetable.
    """
    from scheduler_app.ui.day_keys import DAY_KEYS

    window, ghost = seeded
    target = (DAY_KEYS[3], "09:00", "R002")

    window.refresh_grid()
    _flush_deleted(qapp)
    before = _open_slot_rows(window)
    assert len(before) > 20, (
        f"the open-slots panel offered only {len(before)} slots; the assertion "
        "below would be vacuous")
    offered = [r for r in before if r[1] == target[1] and r[2] == target[2]]
    assert offered, (
        f"the panel never offered {target[1]}/{target[2]}, so occupying it "
        f"proves nothing. Panel shows: {before!r}")

    # The user drops the previously unplaceable lesson into Thursday 09:00 R002.
    ghost["excluded_days"] = []
    _place(ghost, *target)

    window.refresh_grid()
    _flush_deleted(qapp)
    after = _open_slot_rows(window)

    gone = [r for r in before if r not in after]
    appeared = [r for r in after if r not in before]
    assert len(after) == len(before) - 1, (
        f"one slot was just occupied, so the panel must offer one slot fewer: "
        f"{len(before)} -> {len(after)}. Vanished: {gone!r}; new: {appeared!r}")
    assert len(gone) == 1 and gone[0][1:] == target[1:], (
        f"occupying {target!r} should have removed exactly that row from the "
        f"open-slots panel; instead {gone!r} vanished and {appeared!r} appeared")


def test_open_slots_panel_does_not_leak_widgets(seeded, qapp):
    """ST-PERF-006 — repeated refreshes must not accumulate widgets.

    A guard, currently green *once deferred deletions are flushed*, and worth
    keeping: whichever way the panel is rewritten, the widget count for one
    state must not depend on how many times that state has been repainted. A
    failure means the leak the audit saw as +480 MB has simply moved from the
    warning log into the open-slots panel.

    Recorded honestly, because the fix should not make it worse: without the
    flush, ``deleteLater()`` retains ~1000 widgets per refresh until control
    returns to the event loop, so a burst of refreshes inside one Qt loop turn
    is *already* expensive.
    """
    window, _ = seeded

    window.refresh_grid()
    _flush_deleted(qapp)
    once = len(_open_slots_widgets(window))
    assert once > 20, f"the open-slots panel built only {once} widgets"

    for _ in range(9):
        window.refresh_grid()
        _flush_deleted(qapp)
    after = len(_open_slots_widgets(window))

    assert after == once, (
        f"10 refreshes of an unchanged state left {after} widgets in the "
        f"open-slots panel where 1 refresh left {once}")

