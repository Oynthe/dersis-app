"""Shared constants: the search budget, colors, dimensions, theme values.

This module imports nothing, from `scheduler_app` or anywhere else, and that
is the point: it is where a number goes when two modules that must not import
each other both need it.
"""

# ── The shipped search budget (ST-ARCH-010, ST-PERF-001) ─────────────
# Named because the progress UI needs the same denominators the optimizer
# actually runs: a second copy of these numbers elsewhere means the bar stops
# short of the end, or saturates early, the moment the two drift.
#
# They live here rather than in `schedule_optimizer` because `solver_worker`
# needs them too, and `schedule_optimizer` imports `solver_worker` for
# `SolveCancelled`. Reading them back out of the optimizer meant a deferred
# import inside `run_reschedule` and the last mutually importing pair in
# `core`. `schedule_optimizer` re-exports both names, so
# `schedule_optimizer.DEFAULT_MULTI_START_RUNS` still resolves.
DEFAULT_MULTI_START_RUNS = 5
DEFAULT_LNS_ITERATIONS = 200

YEAR_COLORS = [
    "#3B82F6",  # blue
    "#10B981",  # green
    "#F59E0B",  # amber
    "#EF4444",  # red
    "#8B5CF6",  # purple
    "#EC4899",  # pink
    "#06B6D4",  # cyan
    "#84CC16",  # lime
]

# ── In-cell text colours (ST-UI-005) ─────────────────────────────────
#
# These are painted on a lesson-cell background of
# ``lighten_color(get_year_color(state, year), f)`` — which is not one colour
# but **24**: the eight ``YEAR_COLORS`` at each of the three factors the code
# uses (0.45 joint cell, 0.50 sequential sub-block, 0.60 everything-matrix).
# Every value below clears WCAG 2.1 AA against the *darkest* of them,
# ``#f69898`` (relative luminance 0.443, from year colour ``#EF4444``). All of
# this text is under 14 pt, so the threshold is 4.5:1 throughout — none of it
# qualifies for the 3:1 large-text allowance.
#
# The audit reported single ratios ("class code 3.40:1"). The real quantity is a
# range across the eight year colours, and for the code (3.15–4.56) and the
# lecturer (3.56–5.16) that range *straddles* 4.5 — so the same element was
# compliant or not depending on which year a class belonged to. Re-run
# ``tests/test_cell_contrast.py`` before lightening any of these: a value that
# passes on the pale year colours can still fail on the saturated ones.
#
# The same hexes are consumed by ``data_io/exporter.py`` for the XLSX and PDF
# cells, which paint them on the *same* lightened background — so the failure
# shipped in print too, and fixing only the screen would have created a fresh
# screen-vs-export divergence.
#
#                            was        was-worst   now-worst
CELL_FG_CODE = "#1E3A8A"    # #1D4ED8    3.15:1  ->  4.86:1
CELL_FG_NAME = "#1E293B"    # unchanged  6.87:1      6.87:1
CELL_FG_LECTURER = "#334155"  # #475569  3.56:1  ->  4.86:1
CELL_FG_ROOM = "#0F3D24"    # #16A34A    1.55:1  ->  5.76:1
CELL_FG_BRANCH = "#4C1D95"  # #6D28D9    3.58:1  ->  5.15:1
# The "sequential" marker on a multi-part lesson.
#
# Deliberately NOT violet. It used to be #7C3AED — byte-identical to the
# improve_only badge — and the two are drawn on ADJACENT LINES of the last
# section of a sequential cell (renderer.py, the `i == n - 1` blocks), so
# "↑ İYİLEŞTİRME" sat directly above "ARDIŞIK" in one indistinguishable colour.
# A sequential cell can show three violets at once: the branch letter
# (CELL_FG_BRANCH), the improve_only badge, and this.
#
# Slate rather than a fourth violet, because the encoding is also wrong: ARDIŞIK
# is *structural* — it says the lesson runs in parts, like the branch letter
# says which group it is for — while the badges say what the scheduler is
# allowed to do with it. Separating the two families by hue is a better cue than
# two more shades of purple. dE76 44.5 from the improve_only badge and 64.0 from
# the branch letter, against 0.0 and 20.6 before.
CELL_FG_SEQUENTIAL = "#0F172A"  # #7C3AED  2.87:1  ->  8.38:1

# Secondary text in the open-slots sidebar panel, painted on #FFFFFF.
OPEN_SLOTS_FG_ROOM = "#4B5563"  # was #9CA3AF, 2.54:1 -> 7.56:1

MIN_CELL_W = 150
MIN_CELL_H = 70
EMPTY_BG = "#F8FAFC"
HEADER_BG_DARK = "#334155"
TIME_BG = "#475569"
CORNER_BG = "#94A3B8"

# Show Everything / matrix colors (blue theme — consistent with rest of app)
MATRIX_BORDER = "#CBD5E1"
MATRIX_DAY_BG = "#334155"
MATRIX_DAY_FG = "#FFFFFF"
MATRIX_BRANCH_BG = "#475569"
MATRIX_BRANCH_FG = "#FFFFFF"
MATRIX_SESSION_BG = "#F1F5F9"
MATRIX_TIME_BG = "#475569"
MATRIX_CELL_FG = "#1E293B"
MATRIX_CORNER_BG = "#94A3B8"
