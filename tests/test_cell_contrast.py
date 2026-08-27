"""Every colour the timetable paints text in must be legible on every year.

ST-UI-005 (High) · ``ui/renderer.py``, ``ui/badge_formatter.py``,
``data_io/exporter.py``, ``ui/app.py``
    Most in-cell text failed WCAG 2.1 AA. The room assignment — the single most
    important field in a cell — was the *least* legible element in the product
    at **1.55:1**, and the "protected" badge was worse still at 1.55:1's near
    neighbour 1.50:1.

Why this module recomputes instead of pinning hexes
---------------------------------------------------
A test that asserted ``CELL_FG_ROOM == "#0F3D24"`` would be ``f(x) == f(x)``:
it restates the constant and proves nothing about legibility. These tests
recompute the **WCAG 2.1 relative-luminance contrast ratio** from the values the
code actually holds, against the backgrounds the code actually produces, and
assert the *threshold*. That keeps them meaningful if someone retunes the
palette, adds a ninth year colour, or changes ``lighten_color``.

What the audit measured, and why the number here is bigger
----------------------------------------------------------
09-ui-ux-audit.md computed against "the four common cell backgrounds" and found
5 failing elements. There are **24** backgrounds — the eight ``YEAR_COLORS`` at
each of the three lighten factors the renderer uses (0.45 joint cell, 0.50
sequential sub-block, 0.60 everything-matrix) — and **13** failing elements.

The register also reports point values where the true quantity is a *range*:
the class code was 3.15–4.56:1 and the lecturer 3.56–5.16:1 across the eight
years. Both **straddle** the 4.5 threshold, so the same element was compliant or
not depending on which year a class belonged to. That is why
``test_every_cell_text_colour_clears_aa_on_every_year`` iterates all eight
rather than sampling one: a fix validated against a single background passes
while half the palette still fails.

Why the exporters are in here too
---------------------------------
``data_io/exporter.py`` paints the XLSX and PDF cells with the same foregrounds
on the same ``lighten_color(year, 0.45)`` background, so the failure shipped in
print. It duplicated the hexes independently, which is the shape Phase 4 spent a
whole batch closing (five occupancy builders that disagreed). The last test
below pins that the three surfaces read from one source, because a future edit
that "fixes the screen" alone is exactly how the divergence comes back.

All of this text is under 14 pt, so the AA threshold is 4.5:1 throughout; none
of it qualifies for the 3:1 large-text allowance.
"""
import re

import pytest

from scheduler_app.core.constants import (
    YEAR_COLORS,
    CELL_FG_CODE, CELL_FG_NAME, CELL_FG_LECTURER, CELL_FG_ROOM,
    CELL_FG_BRANCH, CELL_FG_SEQUENTIAL, OPEN_SLOTS_FG_ROOM,
)
from scheduler_app.core.logic import lighten_color

AA_NORMAL_TEXT = 4.5

# The three factors renderer.py applies to a year colour to get a cell
# background: 0.45 joint cell (renderer.py:95), 0.50 sequential sub-block
# (renderer.py:673), 0.60 everything-matrix (renderer.py:303).
LIGHTEN_FACTORS = (0.45, 0.50, 0.60)


def _channels(hex_color):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _linear(c):
    c /= 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color):
    """WCAG 2.1 relative luminance."""
    r, g, b = (_linear(c) for c in _channels(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg, bg):
    """WCAG 2.1 contrast ratio between two hex colours."""
    a, b = relative_luminance(fg), relative_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def cell_backgrounds():
    """Every background a lesson cell can be painted with."""
    return [lighten_color(c, f) for c in YEAR_COLORS for f in LIGHTEN_FACTORS]


def _self_check():
    """The measuring instrument, checked against WCAG's own worked examples."""
    assert round(contrast_ratio("#FFFFFF", "#000000"), 2) == 21.0
    assert round(contrast_ratio("#FFFFFF", "#FFFFFF"), 2) == 1.0


# ── the in-cell palette, by the role each colour plays ──────────────────
CELL_TEXT = {
    "class code": CELL_FG_CODE,
    "class name": CELL_FG_NAME,
    "lecturer": CELL_FG_LECTURER,
    "room / location": CELL_FG_ROOM,
    "branch / groups": CELL_FG_BRANCH,
    "sequential marker": CELL_FG_SEQUENTIAL,
}


def badge_colours():
    """The protection/pinned badge colours, read from their single source."""
    from scheduler_app.ui import badge_formatter as bf
    out = {"pinned": bf._PINNED_COLOR}
    for prot, (_emoji, _key, colour) in bf._BADGE_MAP.items():
        out["badge:" + prot] = colour
    return out


def test_the_contrast_formula_matches_wcags_worked_examples():
    """ST-UI-005 — anti-vacuity: pin the instrument before trusting it.

    Every assertion in this module is a number produced by ``contrast_ratio``.
    A sign error or a missing gamma step there would make the whole module
    agree with itself while certifying an illegible palette.
    """
    _self_check()


@pytest.mark.parametrize("role,colour", sorted(CELL_TEXT.items()))
def test_every_cell_text_colour_clears_aa_on_every_year(role, colour):
    """ST-UI-005 — in-cell text must be readable whatever year a class is in.

    A failure means a teacher cannot read the room, the lecturer or the class
    code on some subset of their timetable — the subset being decided by which
    year the class belongs to, which is why this iterates all eight rather than
    sampling one.
    """
    worst = min(
        (contrast_ratio(colour, bg), bg) for bg in cell_backgrounds())
    ratio, bg = worst
    assert ratio >= AA_NORMAL_TEXT, (
        "%s (%s) is %.2f:1 on cell background %s — WCAG AA needs %.1f:1 for "
        "text under 14 pt" % (role, colour, ratio, bg, AA_NORMAL_TEXT)
    )


@pytest.mark.parametrize("role", sorted(badge_colours()))
def test_every_badge_colour_clears_aa_on_every_year(role):
    """ST-UI-005 — the protection badges were the worst contrast in the app.

    ``badges.protected`` measured **1.50:1**. A failure means the marker saying
    a lesson is pinned or locked — the thing that explains why the scheduler
    refuses to move it — is unreadable.
    """
    colour = badge_colours()[role]
    ratio, bg = min(
        (contrast_ratio(colour, bg), bg) for bg in cell_backgrounds())
    assert ratio >= AA_NORMAL_TEXT, (
        "%s (%s) is %.2f:1 on cell background %s — WCAG AA needs %.1f:1"
        % (role, colour, ratio, bg, AA_NORMAL_TEXT)
    )


def test_open_slots_room_label_clears_aa_on_its_white_row():
    """ST-UI-005 — the sidebar's room label measured 2.54:1 on #FFFFFF."""
    ratio = contrast_ratio(OPEN_SLOTS_FG_ROOM, "#FFFFFF")
    assert ratio >= AA_NORMAL_TEXT, (
        "open-slots room label %s is %.2f:1 on #FFFFFF"
        % (OPEN_SLOTS_FG_ROOM, ratio)
    )


def test_no_two_colours_drawn_in_one_cell_are_the_same():
    """ST-UI-005 — darkening must not collapse two roles into one colour.

    Three pairs are at risk, all of which can appear in a single cell:

    * ``same_day`` badge beside the **class code** (dE76 9.2 before)
    * ``improve_only`` badge beside the **branch letter** (6.2 before)
    * ``improve_only`` badge directly above the **ARDIŞIK marker**, which were
      byte-identical (``#7C3AED``) and are drawn on adjacent lines of a
      sequential cell's last section — the worst of the three

    The first two would have become byte-identical under the obvious fix
    (take the darkest passing member of each ramp). The third already was.
    """
    colours = badge_colours()
    same = [
        ("same_day badge", colours["badge:same_day"],
         "class code", CELL_FG_CODE,
         "any class with same_day protection"),
        ("improve_only badge", colours["badge:improve_only"],
         "branch letter", CELL_FG_BRANCH,
         "a sequential class with improve_only protection"),
        ("improve_only badge", colours["badge:improve_only"],
         "sequential marker", CELL_FG_SEQUENTIAL,
         "a sequential class with improve_only protection — adjacent lines"),
    ]
    clashes = ["%s (%s) == %s (%s), both visible on %s"
               % (a_name, a, b_name, b, where)
               for a_name, a, b_name, b, where in same if a == b]
    assert not clashes, (
        "two different things in one cell are painted the same colour:\n  "
        + "\n  ".join(clashes))


def test_the_palette_has_one_source_for_all_three_surfaces():
    """ST-UI-005 — the screen, the XLSX and the PDF must not drift apart.

    ``exporter.py`` used to hold its own copies of these hexes and paint them on
    the same lightened background, so the identical failure shipped in print.
    Phase 4's lesson is that a duplicated builder diverges: five occupancy
    builders disagreed and a user who checked on screen then printed got two
    different timetables. Fixing the renderer alone recreates that, and the
    suite would not have noticed — so pin the absence of the literals.
    """
    import inspect
    from scheduler_app.data_io import exporter
    from scheduler_app.ui import renderer

    retired = {
        "#16A34A": "room / location",
        "#1D4ED8": "class code",
        "#6D28D9": "branch / groups",
        "#7C3AED": "improve_only / sequential",
        "#D97706": "protected badge",
        "#2563EB": "same_day badge",
    }

    offenders = []
    for module, cell_only in ((renderer, True), (exporter, False)):
        src = inspect.getsource(module)
        for literal, role in retired.items():
            for m in re.finditer(re.escape(literal.lstrip("#")), src, re.I):
                line = src[:m.start()].count("\n") + 1
                text = src.splitlines()[line - 1]
                # renderer.py keeps one #1D4ED8 as the EmptySlotItem selection
                # *border* — a UI-component boundary, not text, and governed by
                # WCAG 1.4.11's 3:1 rather than 4.5:1.
                if "QPen(" in text:
                    continue
                offenders.append("%s:%d  %s (%s)"
                                 % (module.__name__, line, text.strip(), role))

    assert not offenders, (
        "retired in-cell text colours are still written as literals; they must "
        "come from scheduler_app.core.constants so the screen and both exports "
        "cannot drift:\n  " + "\n  ".join(offenders)
    )
