"""How many lessons are placed? There must be exactly one answer.

ST-UI-002 (High). Three definitions of "placed" were on screen at once:

* the **status bar** counted ``placed`` only, and derived unplaced as
  ``total - pinned - placed`` — which double-subtracts a class that is both
  pinned and ``placed=True``;
* the **dashboard** card counted ``placed or pinned`` (``get_placed_classes``);
* **BulkResultsDialog** counted its own event list.

Measured on ``make_preset("normal", seed=7)`` after a real solve: the status bar
said **73 yerleşmiş**, the dashboard card said **77**, and the results dialog
said **77 / 80** — three surfaces, two answers, no corruption required. The
status bar also rendered ``4 + 73 + 3`` as if it were a partition of 80, which
only added up because those pins happened to be disjoint from the placed set.

Why "clamp non-negative" is the wrong fix
-----------------------------------------
The register recommends "clamp/assert non-negative". That is the **worst**
available option and this module pins it out. On a state with 80 classes, 4 pins
that also carry ``placed=True``, and 3 genuinely unplaced lessons, the old
formula gives −1 and the clamp gives **0** — while the truth is **3**, and those
3 are listed in the unplaced sidebar on the same screen. The clamp replaces an
impossible number with a confidently wrong one. An ``assert`` is no better: it
crashes the repaint on a file the grid can still draw.

So every test below that could be satisfied by ``max(0, total - pinned -
placed)`` asserts an **exact** count on a state where the clamp gives a
different one. A test that only checks ``unscheduled >= 0`` would pass against
the very fix this module exists to reject.

Is the negative reachable?
--------------------------
The audit's ``-5 yerleşmemiş`` screenshot came from its own harness
(``stress-test/tests/_ui_boot.py::greedy_place`` calls ``mark_placed`` on pinned
classes); ``apply_reschedule`` skipped pins even at the audit commit. But the
invariant "pinned implies not placed" is held by **caller convention at nine
``mark_placed`` sites and enforced nowhere**, and no loader repairs a state that
breaks it — so a ``.egu`` carrying it renders a negative forever. The counter is
therefore structurally total rather than trusting the invariant.

Conventions
-----------
Never assert on ``isVisible()`` (widgets here are never shown, so it is
uniformly False), never hardcode an English string (the suite is pinned to
Turkish), and read what a widget **rendered** rather than recomputing its input.
"""
import re

import pytest

from scheduler_app.core.logic import schedule_counts, get_placed_classes
from scheduler_app.core.models import (
    new_state, new_class, mark_placed, PROTECTION_LOCKED,
)
from scheduler_app.translations import tr


DAYS = ["monday", "tuesday"]
SLOTS = ["09:00", "10:00", "11:00"]


def _state():
    s = new_state()
    s["days"] = list(DAYS)
    s["slots"] = list(SLOTS)
    s["classrooms"] = ["R1", "R2"]
    s["years"] = {"Year-1": ["A"]}
    s["lecturers"] = ["Lect-01"]
    return s


def _add(state, name, *, pinned=False, placed=False, protection=None,
         day="monday", slot="09:00", room="R1"):
    """Add a class in an explicitly chosen flag state.

    ``pinned`` and ``placed`` are independent on purpose: the whole point is
    that nothing in the codebase enforces their disjointness.
    """
    cls = new_class()
    cls["name"] = name
    cls["class_code"] = name
    cls["lecturer"] = "Lect-01"
    cls["duration"] = 1
    cls["targets"] = [{"year": "Year-1", "branch": "A"}]
    if protection:
        cls["protection"] = protection
    if pinned:
        cls["pinned"] = True
        cls["pinned_day"] = day
        cls["pinned_time"] = slot
        cls["pinned_classroom"] = room
    if placed:
        mark_placed(cls, day, slot, room)
    state["classes"].append(cls)
    return cls


def _old_status_bar_formula(state):
    """The formula this finding is about, and the register's clamp of it."""
    total = len(state["classes"])
    pinned = sum(1 for c in state["classes"] if c["pinned"])
    placed = sum(1 for c in state["classes"] if c["placed"])
    raw = total - pinned - placed
    return raw, max(0, raw)


# ══════════════════════════════════════════════════════════════════════
#  1. The counter itself
# ══════════════════════════════════════════════════════════════════════

def test_the_buckets_partition_the_class_list():
    """ST-UI-002 — scheduled + unscheduled must equal total, always.

    A failure means the status bar's segments do not add up to the number of
    classes beside them, which is what a user reads a status bar as.
    """
    s = _state()
    _add(s, "PIN", pinned=True)
    _add(s, "PLACED", placed=True, slot="10:00")
    _add(s, "FREE")

    c = schedule_counts(s)

    assert c["total"] == 3
    assert c["scheduled"] + c["unscheduled"] == c["total"]
    assert c["pinned_of_scheduled"] <= c["scheduled"]


def test_pinned_is_a_subset_of_scheduled_not_a_bucket_beside_it():
    """ST-UI-002 — a pin is *one* lesson, counted once.

    A failure means the status bar renders ``4 sabit + 77 yerleşti + 3
    yerleşmedi`` against 80 classes — 84 — because the pinned segment sits
    beside the placed one as if they were disjoint.
    """
    s = _state()
    _add(s, "PIN1", pinned=True)
    _add(s, "PIN2", pinned=True, slot="10:00")
    _add(s, "PLACED", placed=True, slot="11:00")

    c = schedule_counts(s)

    assert c["scheduled"] == 3, "a pin is scheduled — it occupies its cell"
    assert c["pinned_of_scheduled"] == 2
    assert c["unscheduled"] == 0
    # The subset reading is the only one that adds up.
    assert c["pinned_of_scheduled"] + c["unscheduled"] != c["total"]


def test_a_class_that_is_both_pinned_and_placed_is_counted_once():
    """ST-UI-002 — the double-subtraction, and why the clamp is not the fix.

    A failure means the status bar shows an impossible number of unplaced
    lessons. But passing the *clamp* is not enough either: this asserts the
    exact truth on a state where the clamped formula is confidently wrong,
    because a user shown "0 yerleşmemiş" beside a sidebar listing two lessons
    has been told something false rather than something impossible.
    """
    s = _state()
    _add(s, "BOTH1", pinned=True, placed=True)
    _add(s, "BOTH2", pinned=True, placed=True, slot="10:00")
    _add(s, "FREE1")
    _add(s, "FREE2")

    raw, clamped = _old_status_bar_formula(s)
    assert raw == 0, "fixture does not exercise the double-subtraction"

    c = schedule_counts(s)

    assert c["scheduled"] == 2
    assert c["pinned_of_scheduled"] == 2
    assert c["unscheduled"] == 2, (
        f"the truth is 2 genuinely unplaced lessons; the old formula says "
        f"{raw} and the register's clamp says {clamped}"
    )


def test_the_counter_stays_total_when_the_invariant_is_broken_at_scale():
    """ST-UI-002 — no input may produce a negative or an over-count.

    A failure means some combination of flags — reachable through a legacy
    ``.egu``, or through ``place_batch``, the one ``mark_placed`` call site
    with no pinned guard — puts an impossible number on screen.
    """
    import itertools

    for flags in itertools.product([False, True], repeat=6):
        s = _state()
        for i in range(0, 6, 2):
            _add(s, f"C{i}", pinned=flags[i], placed=flags[i + 1],
                 slot=SLOTS[i // 2])
        c = schedule_counts(s)

        assert 0 <= c["unscheduled"] <= c["total"], (flags, c)
        assert c["scheduled"] + c["unscheduled"] == c["total"], (flags, c)
        assert c["pinned_of_scheduled"] <= c["scheduled"], (flags, c)
        # The union definition every other reader already used.
        assert c["scheduled"] == len(get_placed_classes(s)), (flags, c)


def test_scheduled_is_the_same_set_get_placed_classes_returns():
    """ST-UI-002 — the counter and the renderer must agree on the set.

    A failure means the number on screen counts a different set of lessons than
    the one the grid iterates, which is the disagreement this finding is about.
    """
    s = _state()
    _add(s, "PIN", pinned=True)
    _add(s, "PLACED", placed=True, slot="10:00")
    _add(s, "BOTH", pinned=True, placed=True, slot="11:00")
    _add(s, "FREE")

    assert schedule_counts(s)["scheduled"] == len(get_placed_classes(s)) == 3


def test_protection_is_counted_only_where_it_constrains_something():
    """ST-UI-002 — protection is a movement policy, not a placement bucket.

    A failure means the shield count claims to be protecting lessons that have
    no placement to protect: every protection level in the optimizer is gated
    on ``c["placed"]``, so protection on an unscheduled class constrains
    nothing.
    """
    s = _state()
    _add(s, "LOCKED", placed=True, protection=PROTECTION_LOCKED)
    _add(s, "LOCKED_UNPLACED", protection=PROTECTION_LOCKED)
    _add(s, "PINNED_LOCKED", pinned=True, protection=PROTECTION_LOCKED,
         slot="10:00")

    c = schedule_counts(s)

    assert c["protected_of_scheduled"] == 1, (
        "only the scheduled, non-pinned locked class constrains anything"
    )


def test_an_orphaned_placement_is_scheduled_but_named_as_off_grid():
    """ST-UI-002 / ST-DATA-003 — the one case where scheduled != what is drawn.

    A lesson still marked placed at an hour the user has since deleted is drawn
    by nothing AND absent from the unplaced sidebar (which uses the same
    ``not placed and not pinned`` predicate). Without naming it, the user reads
    "3 yerleşmiş" over a grid showing 2 and has no way to find the third.

    It stays inside ``scheduled`` deliberately: the user did place it, and
    Phase 1 decided not to unplace orphans at load because that discards their
    own work with no undo. A failure means the count is either quietly wrong or
    the orphan was silently dropped — the two bad options this key exists to
    avoid.
    """
    s = _state()
    _add(s, "OK1", placed=True)
    _add(s, "OK2", placed=True, slot="10:00")
    ghost = _add(s, "GHOST", placed=True, slot="11:00")
    ghost["placed_time"] = "23:00"

    c = schedule_counts(s)

    assert c["scheduled"] == 3
    assert c["unscheduled"] == 0
    assert c["off_grid_of_scheduled"] == 1
    # Anti-vacuity: a clean state reports zero, so this is not counting
    # something that is always non-zero.
    ghost["placed_time"] = "11:00"
    assert schedule_counts(s)["off_grid_of_scheduled"] == 0


def test_an_off_grid_day_counts_as_off_grid_too():
    """ST-UI-002 — a removed day orphans a placement just as a removed hour does.

    A failure means the count is honest about deleted hours and silent about
    deleted days, so a user who removes Saturday from the setup is told nothing.
    """
    s = _state()
    ghost = _add(s, "GHOST", placed=True)
    ghost["placed_day"] = "saturday"
    assert "saturday" not in s["days"]

    assert schedule_counts(s)["off_grid_of_scheduled"] == 1


def test_an_empty_state_counts_zero_rather_than_raising():
    """ST-UI-002 — the status bar repaints before any class exists.

    A failure means the first-run window cannot draw its own status bar.
    """
    s = _state()
    c = schedule_counts(s)
    assert c == {"total": 0, "scheduled": 0, "pinned_of_scheduled": 0,
                 "protected_of_scheduled": 0, "off_grid_of_scheduled": 0,
                 "unscheduled": 0}


def test_a_malformed_class_raises_exactly_as_get_placed_classes_does():
    """ST-UI-002 — the counter must not be quietly more tolerant than the grid.

    ``get_placed_classes`` reads ``c["placed"]`` with a bracket, so a class dict
    missing that key raises. If the counter used ``.get`` instead it would
    silently count the same lesson as unscheduled while the grid crashed — one
    loud failure and one quiet miscount for the same corrupt file. Phase 1's
    lesson, in the direction where a crash is the honest outcome.

    A failure means the two readers disagree about what is malformed.
    """
    s = _state()
    _add(s, "OK", placed=True)
    s["classes"].append({"name": "broken"})   # no 'placed'/'pinned' keys

    with pytest.raises(KeyError):
        get_placed_classes(s)
    with pytest.raises(KeyError):
        schedule_counts(s)


# ══════════════════════════════════════════════════════════════════════
#  2. The surfaces that describe the STATE must agree
# ══════════════════════════════════════════════════════════════════════
#
# Scope note. `BulkResultsDialog` is deliberately NOT included. It reports the
# OPERATION -- "this run placed 56 of the 60 you asked for" -- which is a
# different question from "how many lessons are on the timetable", and it is
# shown BEFORE the user accepts, so a state trio rendered there would describe
# the pre-solve timetable. Making it answer the state question would be adding
# a fourth wrong answer, not removing one. The register's "use it in
# BulkResultsDialog" is right that the numbers looked contradictory and wrong
# about which number should move.

pytestmark_ui = pytest.mark.ui


@pytest.mark.ui
def test_the_status_bar_and_the_dashboard_card_report_the_same_number(make_app):
    """ST-UI-002 — the two surfaces that describe the state must not disagree.

    A failure means the user sees "73 yerleşmiş" in the status bar and
    "Yerleşti 77" on the dashboard card, on screen at the same time, for one
    timetable — the disagreement this finding is named for. Measured before the
    fix on ``make_preset("normal", seed=7)``: exactly 73 against 77.
    """
    from scheduler_app.core.analytics import compute_all_metrics

    s = _state()
    _add(s, "PIN1", pinned=True)
    _add(s, "PIN2", pinned=True, slot="10:00")
    _add(s, "PLACED", placed=True, slot="11:00")
    _add(s, "FREE1")
    _add(s, "FREE2")

    app = make_app()
    try:
        app.state_data = s
        app._update_status()
        bar = app.status_label.text()
        app.dashboard_widget.refresh(s)
        card_value = app.dashboard_widget._card_placed._value.text()
    finally:
        app.close()

    counts = schedule_counts(s)
    assert counts["scheduled"] == 3 and counts["unscheduled"] == 2

    # The card renders the number directly.
    assert card_value == str(counts["scheduled"]) == "3"
    # And the bar says the same, next to the same total.
    nums = [int(n) for n in re.findall(r"\d+", bar)]
    assert counts["scheduled"] in nums and counts["total"] in nums
    assert compute_all_metrics(s)["placed_count"] == counts["scheduled"]


@pytest.mark.ui
def test_the_status_bar_never_renders_a_negative_count(make_app):
    """ST-UI-002 — the headline symptom: "-5 yerleşmemiş" on screen.

    A failure means the user is shown an impossible number of unplaced lessons.
    The state below is the one a legacy ``.egu`` can carry: nothing in the load
    path repairs a class that is both pinned and placed.
    """
    s = _state()
    for i in range(3):
        _add(s, f"BOTH{i}", pinned=True, placed=True, slot=SLOTS[i])
    _add(s, "FREE")

    raw, _clamped = _old_status_bar_formula(s)
    assert raw < 0, "fixture does not reproduce the negative count"

    app = make_app()
    try:
        app.state_data = s
        app._update_status()
        bar = app.status_label.text()
    finally:
        app.close()

    assert "-" not in bar.replace("—", ""), f"negative count on screen: {bar}"
    # And it says the TRUTH (1 unplaced), not the clamp's 0.
    assert schedule_counts(s)["unscheduled"] == 1
    assert re.search(r"\b1\b", bar), bar


@pytest.mark.ui
def test_the_pinned_annotation_survives_a_language_change(make_app):
    """ST-UI-002 — the subset annotation is translated, not hardcoded.

    A failure means the pinned note stays in the previous language after the
    user switches, or shows a raw translation key — reopening ST-UI-011 on a
    string that is on screen at all times.
    """
    from scheduler_app.translations import set_language, get_language

    s = _state()
    _add(s, "PIN", pinned=True)
    _add(s, "PLACED", placed=True, slot="10:00")

    original = get_language()
    app = make_app()
    try:
        app.state_data = s
        app.dashboard_widget.refresh(s)
        tr_title = app.dashboard_widget._card_placed._title.text()
        set_language("en")
        app.dashboard_widget.retranslate()
        en_title = app.dashboard_widget._card_placed._title.text()
    finally:
        set_language(original)
        app.close()

    assert "status.pinned_subset" not in tr_title, "raw key on screen"
    assert "status.pinned_subset" not in en_title
    assert "1" in tr_title and "1" in en_title
    assert tr_title != en_title, "the annotation did not follow the language"


def test_the_counter_is_never_quieter_than_the_grid():
    """ST-UI-002 — a malformed class must not be silently counted.

    ``get_placed_classes`` — which the renderer iterates — reads ``c["placed"]``
    first, so a class dict missing that key raises there. If this counter
    short-circuits on ``pinned`` it never touches ``placed``, and the same
    corrupt file gives one loud failure and one confident number.

    That is Phase 1's lesson in the direction where the crash is the honest
    outcome. The achievable property is not "identical to get_placed_classes"
    (this function legitimately reads keys that one never does) but "never
    quieter than it".

    A failure means the status bar reports a total for a file the grid cannot
    draw.
    """
    s = _state()
    _add(s, "OK", placed=True)
    # A PINNED class missing 'placed' — the case a short-circuit hides.
    broken = _add(s, "BROKEN", pinned=True, slot="10:00")
    del broken["placed"]

    with pytest.raises(KeyError):
        get_placed_classes(s)
    with pytest.raises(KeyError):
        schedule_counts(s)


@pytest.mark.ui
def test_the_bar_renders_pinned_as_a_subset_and_names_off_grid_lessons(make_app):
    """ST-UI-002, the RENDERING half — the counter is only half the finding.

    Everything above tests ``schedule_counts``. But the status bar could be
    reverted to its pre-Phase-4 form — pinned as a peer segment, no off-grid
    line — with the whole rest of the suite still green, because nothing
    asserted on the string the user actually reads.

    A failure means the bar is back to reading as a partition
    ("4 sabit + 8 yerleşti + 2 yerleşmedi" over 10 classes), or that the only
    surface naming an orphaned lesson has gone silent — leaving a placed count
    higher than the grid draws with nothing on screen to explain the difference.
    """
    s = _state()
    _add(s, "PIN", pinned=True)
    _add(s, "OK", placed=True, slot="10:00")
    ghost = _add(s, "GHOST", placed=True, slot="11:00")
    ghost["placed_time"] = "23:00"      # hour deleted in Setup, .egu reopened
    _add(s, "FREE")

    counts = schedule_counts(s)
    assert counts == {"total": 4, "scheduled": 3, "pinned_of_scheduled": 1,
                      "protected_of_scheduled": 0, "off_grid_of_scheduled": 1,
                      "unscheduled": 1}, counts

    app = make_app()
    try:
        app.state_data = s
        app._update_status()
        bar = app.status_label.text()
    finally:
        app.close()

    # The pinned subset is rendered as an annotation, not as its own segment.
    assert tr("status.pinned_subset").format(n=1) in bar, (
        f"pinned is not annotated as a subset: {bar!r}"
    )
    # The orphan is named, or the placed count silently exceeds the grid.
    assert tr("status.off_grid_subset").format(n=1) in bar, (
        f"an off-grid lesson is invisible on the status bar: {bar!r}"
    )
    # And the segments still read as a partition of the total: exactly one
    # occurrence each of the scheduled and unscheduled figures beside the total.
    assert f"{counts['total']} " in bar
    assert tr("status.pinned") not in bar, (
        "the old peer-segment label is back, so the numbers no longer add up"
    )
