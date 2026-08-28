"""The colour key must not claim a mapping the palette cannot make.

ST-UI-006 (High) · ``ui/widgets.py``, ``ui/app.py``
    "Color is the only encoding of class grouping (year color) and there is no
    legend anywhere."

The finding is right that nothing explained the colours, and its remedy -- a
legend mapping swatch to year -- is correct below nine years and quietly wrong
above. ``get_year_color`` is::

    YEAR_COLORS[sorted(state["years"]).index(name) % 8]

with **eight** colours, so a ninth year reuses the first one. That is not an
edge case: professional allows 15 years, max 40, institutional unlimited, and a
Turkish K-12 school running grades 1-12 is the ordinary case.

Measured natively on real year names (``"1. Sınıf"`` … ``"12. Sınıf"``), twelve
years produce **four** colliding pairs::

    #3B82F6  1. Sınıf + 6. Sınıf        #10B981  7. Sınıf + 10. Sınıf
    #F59E0B  8. Sınıf + 11. Sınıf       #EF4444  9. Sınıf + 12. Sınıf

Note which pairs those are. The handoff predicted "Year-01 and Year-09", from
the modulo alone -- but year names are free text and ``sorted()`` is
lexicographic, so ``"10. Sınıf"`` sorts between ``"1."`` and ``"2."`` and the
collisions land elsewhere. **Which** years share a colour depends on how the
school names them, which is exactly why the legend has to be built from the
live year list rather than from an index.

So the legend groups by COLOUR, not by year: a swatch shared by two years is
drawn once, listing both. The ambiguity becomes visible rather than being
papered over -- which is the finding's real content, because above eight years
the colour encoding is not merely inaccessible, it is ambiguous for sighted
users too.

Why there are no pixel assertions here
---------------------------------------
``tests/README.md`` forbids them, and this module is a live demonstration of
why: measured under ``QT_QPA_PLATFORM=offscreen`` a 12-chip legend is 1449 px
and "does not fit any supported window"; measured natively with real Segoe UI
it is **738 px** and fits comfortably. The offscreen font fallback is
fixed-pitch, so every advance is roughly doubled. A width-based decision taken
in CI would have killed a feature that is fine. These tests assert grouping and
content, both of which are platform-independent.
"""
import pytest

pytest.importorskip("PyQt6.QtWidgets", reason="PyQt6 not installed")

from scheduler_app.core.logic import get_year_color  # noqa: E402
from scheduler_app.core.constants import YEAR_COLORS  # noqa: E402
from scheduler_app.ui.widgets import YearLegend  # noqa: E402

pytestmark = pytest.mark.ui


def _state(n):
    """A school with *n* years, named the way a Turkish school names them."""
    return {"years": {"%d. Sınıf" % i: ["A"] for i in range(1, n + 1)}}


def _legend(qapp, state):
    legend = YearLegend()
    legend.update_years(state)
    return legend


def test_below_the_wrap_every_year_gets_its_own_swatch(qapp):
    """ST-UI-006 — the ordinary case must be a plain one-to-one key."""
    legend = _legend(qapp, _state(8))
    groups = legend.year_groups()
    assert len(groups) == 8, groups
    assert all(len(names) == 1 for _c, names in groups), groups
    assert len({c for c, _n in groups}) == 8, "two chips share a colour at 8"


def test_a_ninth_year_is_shown_sharing_a_swatch_not_given_a_new_one(qapp):
    """ST-UI-006 — the case that makes the naive legend a lie.

    A failure means the legend shows nine chips with nine labels while the grid
    paints two of those years identically: the key tells the user a colour
    identifies a year, and on this school it does not.
    """
    state = _state(9)
    legend = _legend(qapp, state)
    groups = legend.year_groups()

    assert len(groups) == 8, (
        "the legend drew %d chips for 9 years; there are only %d colours"
        % (len(groups), len(YEAR_COLORS)))
    shared = [names for _c, names in groups if len(names) > 1]
    assert len(shared) == 1 and len(shared[0]) == 2, shared
    # And the two it names really are the two the grid paints the same.
    a, b = shared[0]
    assert get_year_color(state, a) == get_year_color(state, b)


def test_a_k12_school_sees_all_four_collisions(qapp):
    """ST-UI-006 — twelve years, four shared swatches, none of them hidden."""
    state = _state(12)
    legend = _legend(qapp, state)
    groups = legend.year_groups()

    shared = sorted(tuple(sorted(names)) for _c, names in groups
                    if len(names) > 1)
    assert len(shared) == 4, (
        "expected 4 colliding pairs at 12 years, the legend shows %d: %r"
        % (len(shared), shared))
    for names in shared:
        colours = {get_year_color(state, n) for n in names}
        assert len(colours) == 1, (
            "the legend groups %r together but the grid paints them %r"
            % (names, colours))


def test_every_year_appears_exactly_once(qapp):
    """ST-UI-006 — grouping must not drop a year from the key.

    The obvious wrong implementation builds a dict keyed on colour and keeps
    the LAST year for each -- which loses one year per collision, silently, in
    the very widget meant to explain the colours.
    """
    for n in (1, 8, 9, 12, 20):
        state = _state(n)
        listed = [name for _c, names in _legend(qapp, state).year_groups()
                  for name in names]
        assert sorted(listed) == sorted(state["years"]), (
            "at %d years the legend lists %r" % (n, sorted(listed)))
        assert len(listed) == len(set(listed)), "a year is listed twice"


def test_the_legend_matches_the_grid_for_every_year(qapp):
    """ST-UI-006 — the swatch shown must be the colour actually painted.

    Pins the legend against ``get_year_color`` itself rather than against a
    copy of the palette, so a change to the wrap rule cannot leave the key
    describing the old behaviour.
    """
    state = _state(12)
    for colour, names in _legend(qapp, state).year_groups():
        for name in names:
            assert get_year_color(state, name) == colour, (
                "legend paints %r as %s, the grid paints it %s"
                % (name, colour, get_year_color(state, name)))


def test_an_empty_school_has_an_empty_legend(qapp):
    """ST-UI-006 — before Setup there are no years, and no key to show."""
    assert _legend(qapp, {"years": {}}).year_groups() == []
    assert _legend(qapp, {}).year_groups() == []


def test_rebuilding_with_the_same_years_is_a_no_op(qapp):
    """ST-UI-006 — it runs on every filter refresh; it must not churn widgets.

    ``_update_filters`` is reached from every repaint (ST-PERF-006 is the same
    lesson one panel over), so tearing down and rebuilding a dozen QLabels each
    time would be a real cost for a key that changes only in Setup.
    """
    state = _state(12)
    legend = _legend(qapp, state)
    first = [id(legend._layout.itemAt(i).widget())
             for i in range(legend._layout.count())]
    legend.update_years(state)
    again = [id(legend._layout.itemAt(i).widget())
             for i in range(legend._layout.count())]
    assert first == again, "the legend rebuilt its chips for an unchanged year list"

    state["years"]["13. Sınıf"] = ["A"]
    legend.update_years(state)
    assert [id(legend._layout.itemAt(i).widget())
            for i in range(legend._layout.count())] != again, (
        "the legend did NOT rebuild after a year was added")


def test_the_window_actually_has_a_legend(qapp, dersis_home, make_app):
    """ST-UI-006 — anti-vacuity: a widget nobody shows explains nothing.

    Every test above builds a ``YearLegend`` directly and would pass with the
    class present and unwired -- which is precisely the state Phase 5 left
    ``core/text_safety.qt_tooltip`` in for a whole phase.
    """
    app = make_app()
    assert hasattr(app, "year_legend"), (
        "the main window has no year legend")
    app.state_data.update(_state(12))
    app.state_data.update({
        "days": ["monday"], "slots": ["09:00"], "classrooms": ["R001"],
        "classroom_capacities": {"R001": 30}, "lecturers": [],
        "lecturer_availability": {}, "classes": [],
    })
    app._workflow.state = app.state_data
    app.refresh_grid()
    groups = app.year_legend.year_groups()
    assert len(groups) == 8, (
        "the wired legend shows %d chips for a 12-year school" % len(groups))
