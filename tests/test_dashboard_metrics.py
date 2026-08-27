"""Dashboard analytics must describe the schedule the user is looking at.

ST-UI-003 (High) — the Schedule Quality tab built its ``(cls, day, slot, room)``
placement tuples with ``room = cls.get("room", "")``. No class dict has a
``room`` key, so every tuple carried ``""``.

The consequence is worse than "reads a key that does not exist". ``""`` is then
passed as ``room_override`` into ``get_effective_room_resource_for_class``, and
because ``""`` is not the ``_ROOM_UNSET`` sentinel the override **wins** — so the
code actively overrode the correct ``placed_classroom`` / ``pinned_classroom``
fallback with an empty string. Every room-derived number downstream collapsed to
zero: the "Oda Değişimi" breakdown bar, and ``ScheduleAnalytics``' room metrics.

``logic.analyze_schedule(state)`` has built the same tuples correctly the whole
time (``room = effective_room(cls)``). The dashboard re-derived them, wrongly.

A second, independent defect in the same path: ``ScheduleAnalytics.analyze``
resolves its slot with the deliberately-raising ``logic.slot_index``, while its
sibling ``TimetableScorer.score_detailed`` already does
``si = find_slot_index(...); if si is None: continue`` with an explicit
ST-DATA-003 comment. That asymmetry — not the dashboard's loop — is why the
room-switch bar survived off-grid data while the quality gauge collapsed to 0.
The guard belongs in the analyser, where it also protects the one live caller
(``workflow.py``'s ``analyze_schedule(self.state, placed)``), which passes its own
list and would bypass any dashboard-side filter.

Conventions this module follows
-------------------------------
* **Never assert on ``isVisible()``.** Widgets here are constructed but never
  ``.show()``n and the platform is offscreen, so ``isVisible()`` is uniformly
  False — an assertion on it fails against a correct fix, and its negation passes
  against a permanently-on widget. Assert on rendered data instead.
* **Never hardcode an English string.** ``conftest._pinned_language`` is
  session-scoped, autouse, and sets Turkish; ``get_special_location_resource``
  returns ``'Çevrimiçi'``, not ``'Online'``. Call the accessor.
* **Cross the seam.** The agreement tests read what the widget *rendered* and
  compare it against an independently computed expectation. Comparing a helper
  against the function that now calls it is ``f(x) == f(x)`` and can never go red.
* Fail-now / pass-after: ST-UI-003 is fixed in Phase 4, so nothing here is
  ``xfail``.
"""
import pytest

from scheduler_app.core.models import (
    new_state, new_class, mark_placed, effective_room,
    get_special_location_resource, get_effective_room_resource_for_class,
    LOCATION_ONLINE,
)
from scheduler_app.core.schedule_analytics import ScheduleAnalytics
from scheduler_app.core.timetable_scorer import TimetableScorer
from scheduler_app.translations import tr

pytestmark = pytest.mark.ui


DAYS = ["monday", "tuesday"]
SLOTS = ["09:00", "10:00", "11:00", "12:00"]
ROOMS = ["R1", "R2"]


def _state():
    s = new_state()
    s["days"] = list(DAYS)
    s["slots"] = list(SLOTS)
    s["classrooms"] = list(ROOMS)
    s["years"] = {"Year-1": ["A"]}
    s["lecturers"] = ["Lect-01"]
    return s


def _add(state, name, slot, room, day="monday", lecturer="Lect-01",
         duration=1, location_type=None):
    cls = new_class()
    cls["name"] = name
    cls["class_code"] = name
    cls["lecturer"] = lecturer
    cls["duration"] = duration
    cls["participants"] = 10
    cls["targets"] = [{"year": "Year-1", "branch": "A"}]
    if location_type is not None:
        cls["location_type"] = location_type
    mark_placed(cls, day, slot, room)
    state["classes"].append(cls)
    return cls


@pytest.fixture
def room_switch_state():
    """One lecturer, four consecutive hours, alternating rooms.

    Three genuine R1->R2->R1->R2 switches in a single day, so a room-switch
    penalty of exactly zero can only mean the room was never read.
    """
    s = _state()
    for i, (slot, room) in enumerate(zip(SLOTS, ["R1", "R2", "R1", "R2"])):
        _add(s, f"C{i}", slot, room)
    return s


def _dashboard(qapp, state):
    from scheduler_app.ui.dashboard import DashboardWidget
    w = DashboardWidget()
    w.refresh(state)
    return w


def _rendered_breakdown(widget):
    """The score-breakdown bars as ``{label: value}``, straight off the chart."""
    return dict(widget._breakdown_chart._data)


# ══════════════════════════════════════════════════════════════════════
#  1. The room actually reaches the analytics
# ══════════════════════════════════════════════════════════════════════

def test_room_switching_bar_is_not_zero_when_rooms_actually_switch(
        qapp, room_switch_state):
    """ST-UI-003 — the "Oda Değişimi" bar must report real room switches.

    A failure means a schedule that marches one lecturer across three room
    changes in a morning is graded as if they never left the room, so the one
    dashboard number that is *about* rooms is permanently 0.0 and silently
    endorses a timetable that wastes the lecturer's day.
    """
    widget = _dashboard(qapp, room_switch_state)
    bars = _rendered_breakdown(widget)
    label = tr("dashboard.room_switching")

    assert label in bars, f"no room-switching bar was rendered at all: {bars}"
    assert bars[label] > 0, (
        "the rendered room-switching bar is 0.0 on a schedule with three real "
        "R1->R2 switches — the room never reached the scorer"
    )


def test_rendered_breakdown_matches_an_independently_scored_timetable(
        qapp, room_switch_state):
    """ST-UI-003 — the dashboard must score the same timetable as everyone else.

    A failure means the grade on the Kalite Paneli disagrees with the grade in
    the placement-results dialog for the very same schedule, and a user has two
    numbers and no way to tell which one is about their timetable.

    The expectation is built from ``effective_room`` — the currency
    ``logic.analyze_schedule`` and ``apply_reschedule`` already use — and is
    compared against what the widget *rendered*, so this cannot degenerate into
    comparing a helper with the function that calls it.
    """
    expected_raw = TimetableScorer(room_switch_state).score_detailed([
        (c, c["placed_day"], c["placed_time"], effective_room(c))
        for c in room_switch_state["classes"]
    ])
    expected = round(abs(expected_raw["room_switching"]), 1)

    bars = _rendered_breakdown(_dashboard(qapp, room_switch_state))

    # Anti-vacuity: a zero expectation would make the assertion below true
    # against the very bug this test exists for.
    assert expected > 0, "fixture does not produce a room switch — test is broken"
    assert bars[tr("dashboard.room_switching")] == pytest.approx(expected)


def test_room_metrics_see_every_room_that_is_in_use(qapp, room_switch_state):
    """ST-UI-003 — room utilization must count the rooms the schedule uses.

    A failure means the room-utilization analytics report an empty building
    (``total_rooms=0``, ``avg_utilization=0.0``) for a timetable that is using
    every room it has.
    """
    placements = [
        (c, c["placed_day"], c["placed_time"], effective_room(c))
        for c in room_switch_state["classes"]
    ]
    metrics = ScheduleAnalytics(room_switch_state).analyze(placements)["room_metrics"]

    assert metrics["summary"]["total_rooms"] == 2, metrics["summary"]
    assert metrics["summary"]["avg_utilization"] > 0
    # per_room maps a room to a dict, not to a float.
    assert set(metrics["per_room"]) == {"R1", "R2"}
    assert metrics["per_room"]["R1"]["used_slots"] == 2


# ══════════════════════════════════════════════════════════════════════
#  2. A lesson that needs no room must not invent one
# ══════════════════════════════════════════════════════════════════════

def test_an_online_lesson_reports_its_own_resource_not_a_classroom(
        qapp):
    """ST-UI-003 — reading the room must not give an online lesson a classroom.

    A failure means an online lesson contends for a physical room in the
    analytics: it either occupies a classroom nobody is in, or it manufactures a
    room switch every time the lecturer moves between a room and a video call.

    Asserts the observable rather than ``room is None``: a class placed
    face-to-face and later switched to online keeps its old ``placed_classroom``,
    so the invariant "the tuple carries None" does not hold in general — but
    what the analytics *resolve* it to must always be the virtual resource,
    because ``get_effective_room_resource_for_class`` tests
    ``class_uses_physical_room`` before it ever consults the override.
    """
    s = _state()
    online = _add(s, "NET", "09:00", None, location_type=LOCATION_ONLINE)
    _add(s, "ROOM", "09:00", "R1")

    resolved = get_effective_room_resource_for_class(
        online, room_override=effective_room(online))
    assert resolved == get_special_location_resource(online)
    assert resolved != "R1"

    placements = [
        (c, c["placed_day"], c["placed_time"], effective_room(c))
        for c in s["classes"]
    ]
    per_room = ScheduleAnalytics(s).analyze(placements)["room_metrics"]["per_room"]
    # Locale-proof: the label is Turkish under this suite's pinned language.
    assert set(per_room) == {"R1", get_special_location_resource(online)}


# ══════════════════════════════════════════════════════════════════════
#  3. An off-grid placement must not zero the whole score
# ══════════════════════════════════════════════════════════════════════

def test_a_placement_on_a_deleted_hour_does_not_collapse_the_grade(
        qapp, room_switch_state):
    """ST-UI-003 / ST-DATA-003 — one orphaned lesson must not zero the gauge.

    A failure means that after the user shortens the teaching day in Setup, the
    entire Kalite Paneli reads 0/100 "Zayıf" — not because the timetable got
    worse, but because one stale placement made the analyser raise and the bare
    ``except`` swallowed the whole report. The user is shown a verdict on their
    schedule that is really a verdict on one deleted hour.

    ``TimetableScorer.score_detailed`` already skips off-grid placements
    (ST-DATA-003); ``ScheduleAnalytics.analyze`` was the odd one out.
    """
    state = room_switch_state
    orphan = _add(state, "GHOST", "09:00", "R1", day="tuesday")
    # Delete the hour out from under it, exactly as SetupDialog can.
    orphan["placed_time"] = "23:00"
    assert "23:00" not in state["slots"]

    widget = _dashboard(qapp, state)

    assert widget._quality_gauge._score > 0, (
        "one placement on a deleted hour collapsed the whole quality gauge to 0"
    )
    # Anti-vacuity: the surviving on-grid lessons are still scored, so this is
    # not passing because the report is empty.
    bars = _rendered_breakdown(widget)
    assert bars[tr("dashboard.room_switching")] > 0


def test_analytics_survive_an_off_grid_placement_for_every_caller(
        room_switch_state):
    """ST-DATA-003 — the guard belongs in the analyser, not in one caller.

    ``workflow.py`` calls ``analyze_schedule(state, placed)`` with its own list
    and would bypass a dashboard-side filter entirely. A failure means the
    reschedule-results analytics still die on a stale placement that the
    dashboard has learned to tolerate — the same bug, one screen over.
    """
    state = room_switch_state
    orphan = _add(state, "GHOST", "09:00", "R1")
    orphan["placed_time"] = "23:00"

    placements = [
        (c, c["placed_day"], c["placed_time"], effective_room(c))
        for c in state["classes"]
    ]
    report = ScheduleAnalytics(state).analyze(placements)

    assert report["global_score"] > 0
    # The orphan contributes nothing rather than crashing — and, crucially, the
    # four on-grid lessons are still all counted.
    assert report["room_metrics"]["summary"]["total_rooms"] == 2
    assert report["room_metrics"]["per_room"]["R1"]["used_slots"] == 2


# ══════════════════════════════════════════════════════════════════════
#  4. A block that overruns the last hour must not crash the panel
# ══════════════════════════════════════════════════════════════════════
#
# Phase 1 made these readers total against a placement whose *start* slot was
# deleted (`si is None` -> skip, ST-DATA-003). It did not guard the other half:
# a block whose start slot still exists but whose DURATION now overruns the end
# of the day. `busiest_slots` then indexes `state["slots"][si + i]` past the end
# and raises, and `room_utilization` silently counts cells that do not exist.
#
# This is reachable from ordinary Setup use and `reconcile_placements` does not
# catch it, because it only checks that `placed_time` is still a member of
# `slots` — which it is. See `test_shortening_the_day_leaves_a_block_overrunning`.

def _overrun_state():
    """A 2-hour lesson at the last remaining hour of the day.

    Built the way a user builds it: place it while the day is long enough, then
    shorten the day in Setup.
    """
    s = new_state()
    s["days"] = ["monday"]
    s["slots"] = ["15:00", "16:00"]
    s["classrooms"] = ["R1"]
    s["years"] = {"Year-1": ["A"]}
    s["lecturers"] = ["Lect-01"]
    _add(s, "TwoHour", "15:00", "R1", duration=2)
    s["slots"] = ["15:00"]          # the user deletes the 16:00 hour
    return s


def test_shortening_the_day_leaves_a_block_overrunning_the_grid():
    """Documents the precondition: reconcile does not see a duration overrun.

    Not a defect assertion — it pins the shape of the state the two tests below
    are about, so that if `reconcile_placements` ever learns to catch this, the
    fixture stops being silently unreachable and this test says so.
    """
    from scheduler_app.core.workflow import SchedulingWorkflow

    state = _overrun_state()
    affected = SchedulingWorkflow.reconcile_placements(state)

    assert affected == [], (
        "reconcile_placements now catches a duration overrun — the fixture "
        "below no longer reproduces the condition it was written for"
    )
    cls = state["classes"][0]
    assert cls["placed"] and cls["placed_time"] == "15:00"
    assert cls["duration"] == 2 and len(state["slots"]) == 1


def test_a_block_overrunning_the_last_hour_does_not_crash_the_metrics():
    """ST-DATA-003 (duration half) — metrics must survive an overrunning block.

    A failure means that after the user shortens the teaching day in Setup, the
    Dashboard tab raises ``IndexError: list index out of range`` and the panel
    never renders — ``ui/app.py`` calls ``dashboard_widget.refresh(state)`` with
    no ``try``. The user's only route back is to lengthen the day again, which
    nothing on screen tells them.
    """
    from scheduler_app.core.analytics import compute_all_metrics

    metrics = compute_all_metrics(_overrun_state())

    # Anti-vacuity: the surviving hour is still counted, so this is not passing
    # because the reader learned to skip the lesson entirely.
    assert metrics["busiest_slots"] == {"15:00": 1}, metrics["busiest_slots"]


def test_an_overrunning_block_does_not_claim_hours_that_do_not_exist():
    """ST-DATA-003 (duration half) — room use must not exceed the building.

    A failure means a 2-hour lesson on a 1-hour day reports 200% utilization for
    its room, so the "Oda Kullanımı" card and the underused-rooms list both
    describe a building with more hours in it than the timetable has.
    """
    from scheduler_app.core.analytics import room_utilization

    utils = room_utilization(_overrun_state())

    assert utils["R1"] == 100.0, (
        f"one lesson on a one-hour, one-day, one-room grid must be 100%, "
        f"got {utils['R1']}%"
    )


def test_the_dashboard_still_renders_when_a_block_overruns(qapp):
    """ST-DATA-003 (duration half) — the panel must draw, not raise.

    A failure means opening the Kalite Paneli tab after a Setup edit throws out
    of ``refresh()`` into the main window.
    """
    widget = _dashboard(qapp, _overrun_state())

    # Anti-vacuity: it rendered real numbers, not an empty early-return.
    assert widget._card_placed._value.text() == "1"
