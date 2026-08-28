"""The shell must fit the screen it is on, and the sidebar must yield to the grid.

ST-UI-013 (Medium · High) — *the shipped default window is smaller than the
timetable it draws.*

Measured natively in Phase 7 (Windows platform plugin, Segoe UI 9pt, 1536x960
@ dpr 1.25), Turkish, 5 days x 8 periods, at the app's own hard-coded default of
1150x720 (``ui/app.py``): the scene is **841x607** and the viewport **769x457**
— *both* scrollbars, on every launch, on every machine, because the window size
was never saved and never restored. The right sidebar is a flat **350 px**, and
its cost is exact and constant: **314 px**, the difference between 350 and the
36 px collapsed width. That is two whole day columns at 1000 px (3 -> 5) and the
entire sixth day of a six-day week at 1366x768 (viewport 985 vs scene 992).

Two corrections this module encodes, because the register and Phase 6 each got
one of them wrong:

*The locale-dependent number is the tab bar, not the sidebar.* All five tabs
stay reachable only while ``W >= tabBar().sizeHint() + 359``: ko 913, en 1050,
tr 1148, pl 1169, ru 1214, **id 1232** — a 319 px spread. The shipped 1150
clears Turkish by **0 px** and fails id, pl and ru outright. And the tabs do not
*truncate*: ``elideMode == ElideNone``, ``usesScrollButtons == True``, so whole
tabs — the Quality Dashboard among them — go behind an arrow rather than
eliding. The sidebar's own ``minimumSizeHint`` (ko 210 ... tr 301 ... ru 362) is
separately locale-dependent but binds only in pl and ru, where it exceeds 350.

*A plain ``resizeEvent`` breakpoint does not work.* It was built and measured:
at 1000 px the user clicks Expand, a 1 px nudge arrives, and it re-collapses —
``[["auto-collapse",1000],["auto-collapse",1001]]``. Hysteresis alone does not
fix that; only a user-intent flag does. So the app tracks
``_sidebar_intent in {"auto","open","closed"}`` and the resize handler acts only
while it is ``"auto"``.

Why every assertion here is a *relation*
----------------------------------------
``QT_QPA_PLATFORM=offscreen`` has no Segoe UI at all: measured, it inflates the
sidebar's size hint 1.83x (301 -> 552) and the tab bar's 1.76x (789 -> 1388),
and its only screen is 800x800. An absolute pixel asserted here would say
nothing about the app a user runs. Every number below is therefore a comparison
between two quantities read out of the *same process*.

And the trap that makes a green test worthless here: ``make_app`` builds a
**never-shown** window whose splitter sits at its own 640 px size hint and
ignores ``resize()`` entirely. Two of the tests proposed for this row passed
against the unfixed tree for exactly that reason. Every test below therefore
shows the window and asserts, via ``_assert_really_laid_out``, that the splitter
is tracking the window before it measures anything.
"""
import pytest

pytest.importorskip("PyQt6.QtWidgets", reason="PyQt6 not installed")

from PyQt6.QtCore import QRect  # noqa: E402

from scheduler_app.ui.renderer import COL_DAY_W, GRID_GAP  # noqa: E402

pytestmark = pytest.mark.ui


# ── helpers ─────────────────────────────────────────────────────────────────

def _shown(make_app, qapp):
    """A real, laid-out main window. Never a never-shown one."""
    win = make_app()
    win.show()
    qapp.processEvents()
    _assert_really_laid_out(win)
    return win


def _assert_really_laid_out(win):
    """Refuse to measure a window whose splitter is at its own size hint.

    ``make_app`` hands back a window that was never shown; its splitter is
    640 px wide no matter what ``resize()`` was called with, so a test that
    measures it is measuring the fixture rather than the app.
    """
    assert win.splitter.width() > win.width() * 0.9, (
        "the splitter (%d) is not tracking the window (%d) — this window was "
        "never shown, and nothing measured from it means anything"
        % (win.splitter.width(), win.width()))


def _handle_width(win):
    """Live splitter handle width. ``handleWidth()`` returns -1 under Fusion."""
    handle = win.splitter.handle(1) if win.splitter.count() > 1 else None
    if handle is not None and handle.width() > 0:
        return handle.width()
    return max(win.splitter.handleWidth(), 0)


def _current_view(win):
    views = [win.grid_view1, win.grid_view2, win.grid_view3, win.grid_view4]
    idx = win.notebook.currentIndex()
    return views[idx] if 0 <= idx < len(views) else win.grid_view1


def _notebook_content_width(win):
    view = _current_view(win)
    scene = view.scene()
    scene_w = 0.0
    if scene is not None:
        zoom = getattr(view, "_zoom_pct", 100) or 100
        scene_w = scene.sceneRect().width() * zoom / 100.0
    chrome = max(win.notebook.width() - view.viewport().width(), 0)
    tab_w = win.notebook.tabBar().sizeHint().width()
    return int(round(max(tab_w, scene_w + chrome)))


def _width_the_sidebar_costs(win, sidebar_w=None):
    """The splitter width at which the sidebar can stay open, from live Qt.

    Deliberately re-derived here from the same widgets the application reads,
    rather than by calling the app's own helper: these tests have to run — and
    to *fail* — on a tree where no such helper exists yet.
    """
    if sidebar_w is None:
        # The remembered width is only what the splitter is asked for; the
        # panel's own minimumSizeHint is a floor under it, and that floor is
        # locale-dependent (ko 210 ... tr 301 ... ru 362, natively).
        sidebar_w = max(win._sidebar_saved_width,
                        win._sidebar_panel.minimumSizeHint().width())
    return _notebook_content_width(win) + sidebar_w + _handle_width(win)


def _set_splitter_width(win, qapp, target):
    """Resize the window so its splitter ends up ``target`` px wide."""
    if win.isMaximized():
        win.showNormal()
        qapp.processEvents()
    margin = win.width() - win.splitter.width()
    win.resize(max(target + margin, win.minimumWidth()), win.height())
    qapp.processEvents()


def _load(win, qapp, state):
    win.state_data = state
    win.refresh_grid()
    qapp.processEvents()


def _restore_language(win, qapp):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
    win._set_language("tr")
    QApplication.instance().setLayoutDirection(Qt.LayoutDirection.LeftToRight)
    qapp.processEvents()


# ── 1. The window remembers where it was ────────────────────────────────────

def test_a_first_run_opens_to_the_screen_instead_of_guessing_1150x720(
        make_app, qapp):
    """A window nobody has sized yet must take the screen, not a constant.

    The hard-coded 1150x720 was measured to put an 841x607 scene into a 769x457
    viewport in Turkish at 5x8 — two scrollbars before the user has done
    anything. With no saved geometry there is nothing better to know than "as
    much as this screen has", so that is what it opens with.
    """
    win = _shown(make_app, qapp)
    assert win.isMaximized(), (
        "a first run opened at a fixed size instead of using the screen")


def test_the_window_you_left_is_the_window_you_come_back_to(make_app, qapp):
    """Geometry survives a close, so the resize is done once and not daily."""
    first = _shown(make_app, qapp)
    first.showNormal()
    qapp.processEvents()
    # Derived from the window itself: no absolute pixel is asserted, and the
    # height axis is the one the 800x800 offscreen screen does not clamp.
    # Clear of the minimum, so a restore that merely clamped would not pass.
    left_at = first.minimumHeight() + 97
    assert left_at != first.height()
    first.resize(first.width(), left_at)
    qapp.processEvents()
    left_at = first.height()
    first.close()
    qapp.processEvents()

    again = _shown(make_app, qapp)
    assert not again.isMaximized(), (
        "the second launch ignored the saved geometry and maximized again")
    assert again.height() == left_at, (
        "reopened at %d px high after being left at %d"
        % (again.height(), left_at))


def test_a_saved_geometry_that_lands_on_no_screen_is_refused():
    """A monitor that has been unplugged must not take the window with it."""
    from scheduler_app.ui.app import SchedulerApp

    screens = [QRect(0, 0, 1920, 1080)]
    assert SchedulerApp._rect_is_on_a_screen(QRect(100, 80, 1200, 800), screens)
    assert SchedulerApp._rect_is_on_a_screen(QRect(-200, 0, 1200, 800), screens)
    assert not SchedulerApp._rect_is_on_a_screen(
        QRect(2400, 1200, 1200, 800), screens), (
        "a frame entirely off every screen was accepted as usable")
    assert not SchedulerApp._rect_is_on_a_screen(
        QRect(1900, 1060, 1200, 800), screens), (
        "a frame with only its corner on screen was accepted as usable")
    assert not SchedulerApp._rect_is_on_a_screen(QRect(0, 0, 0, 0), screens)


def test_an_unreadable_saved_geometry_still_opens_a_window(make_app, qapp,
                                                           dersis_home):
    """Garbage in the settings container must cost a size, never a session."""
    from scheduler_app.storage import storage
    from scheduler_app.ui.first_run import _write_flag

    _write_flag(storage.settings_path(), "window_geometry", "%%not base64%%")
    win = _shown(make_app, qapp)
    assert win.isMaximized(), (
        "an unreadable geometry did not fall back to the first-run behaviour")
    assert win._rect_is_on_a_screen(
        win.frameGeometry(), win._available_screen_rects()), (
        "the window opened somewhere no screen covers")


# ── 2. The sidebar yields to the grid, but not to a nudge ───────────────────

def test_a_window_too_narrow_for_the_grid_collapses_the_sidebar_itself(
        make_app, qapp, make_state):
    """The 314 px the sidebar holds go to the grid when the grid needs them.

    Both halves are measured at one window width, so the comparison is between
    two states of the same window rather than between two windows.
    """
    win = _shown(make_app, qapp)
    _load(win, qapp, make_state(n_days=5, n_slots=8, n_classes=20, seed=11))
    _set_splitter_width(win, qapp, _width_the_sidebar_costs(win) - 200)
    _assert_really_laid_out(win)

    assert win._sidebar_is_collapsed, (
        "a window %d px narrower than the %d its own grid and tab bar need "
        "left the sidebar open anyway"
        % (win.splitter.width(), _width_the_sidebar_costs(win)))

    # And now the size of the favour. Re-opening the sidebar lets Qt push the
    # window back out to suit the panel's own size hint, so the two states are
    # compared in the order that holds the window still: open first, then
    # closed, with the total asserted unchanged between them.
    win._sidebar_expand_btn.click()      # the user overrules it
    qapp.processEvents()
    assert not win._sidebar_is_collapsed
    total = win.splitter.width()
    handle = _handle_width(win)
    nb_open, sb_open = win.notebook.width(), win._sidebar_panel.width()

    win._sidebar_collapse_btn.click()
    qapp.processEvents()
    assert win._sidebar_is_collapsed
    nb_closed, sb_closed = win.notebook.width(), win._sidebar_panel.width()

    assert win.splitter.width() == total, (
        "the window moved between the two measurements (%d -> %d); nothing "
        "compared across them means anything"
        % (total, win.splitter.width()))
    assert nb_closed - nb_open >= (sb_open - sb_closed) - handle, (
        "collapsing moved %d px to the grid but took %d from the sidebar"
        % (nb_closed - nb_open, sb_open - sb_closed))


def test_a_sidebar_the_user_opened_survives_a_one_pixel_resize(
        make_app, qapp, make_state):
    """Intent outranks the breakpoint, in both directions.

    The naive version of this was built and measured: at 1000 px the user
    clicks Expand and a 1 px nudge re-collapses it, logging
    ``[["auto-collapse",1000],["auto-collapse",1001]]``. Hysteresis does not
    help — the window is still below the threshold — so the handler has to know
    that the sidebar is open *because the user said so*.
    """
    win = _shown(make_app, qapp)
    _load(win, qapp, make_state(n_days=5, n_slots=8, n_classes=20, seed=11))

    _set_splitter_width(win, qapp, _width_the_sidebar_costs(win) - 200)
    assert win._sidebar_is_collapsed, "the sidebar never yielded at all"

    win._sidebar_expand_btn.click()
    qapp.processEvents()
    assert not win._sidebar_is_collapsed

    win.resize(win.width() + 1, win.height())     # the 1 px nudge
    qapp.processEvents()
    assert not win._sidebar_is_collapsed, (
        "a 1 px resize threw away the sidebar the user had just opened")

    _set_splitter_width(win, qapp, _width_the_sidebar_costs(win) - 400)
    assert not win._sidebar_is_collapsed, (
        "shrinking the window further overruled the user's own expand")

    win._sidebar_collapse_btn.click()             # and back the other way
    qapp.processEvents()
    assert win._sidebar_is_collapsed
    _set_splitter_width(win, qapp, _width_the_sidebar_costs(win) + 600)
    assert win._sidebar_is_collapsed, (
        "widening the window reopened a sidebar the user had closed")


def test_the_threshold_is_computed_from_the_schedule_not_from_a_constant(
        make_app, qapp, make_state):
    """A constant is wrong by 48 px in Turkish and 132 in Indonesian.

    ``COL_DAY_W`` and ``GRID_GAP`` are imported from the renderer rather than
    typed in, so the day the grid's geometry changes this test changes with it.
    """
    win = _shown(make_app, qapp)

    _load(win, qapp, make_state(n_days=4, n_slots=8, n_classes=16, seed=3))
    narrow = win._grid_content_width()
    assert win._sidebar_needed_width() == _width_the_sidebar_costs(win)

    _load(win, qapp, make_state(n_days=7, n_slots=8, n_classes=16, seed=3))
    wide = win._grid_content_width()
    assert win._sidebar_needed_width() == _width_the_sidebar_costs(win)

    assert wide - narrow >= 3 * (COL_DAY_W + GRID_GAP), (
        "three more days moved the required width by %d px, and three day "
        "columns are %d" % (wide - narrow, 3 * (COL_DAY_W + GRID_GAP)))


def test_a_sidebar_that_remembers_a_narrower_locale_still_counts_full_width(
        make_app, qapp):
    """The panel's own minimum is a floor under the width we remember.

    ``_sidebar_saved_width`` is only what the splitter is *asked* for. The
    panel's ``minimumSizeHint`` — ``12 + minSizeHint(open-slots) + 4 +
    minSizeHint(unplaced)``, both buttons bold, padded and emoji-prefixed —
    floors it, and it is locale-dependent: measured natively across the 22
    shipped locales, ko 210, ja 235, en 265, tr 301, id 320, de 349, **pl 356,
    ru 362**. The last two exceed the hard-coded 350, so in Polish and Russian
    the splitter quietly widens the sidebar past the number anyone remembered.

    Drag the sidebar narrow in Korean and switch to Russian and the remembered
    width understates the real one by 152 px — 152 px of tab bar and grid
    accounted for as if they were free.
    """
    win = _shown(make_app, qapp)
    win._sidebar_expand_btn.click()
    qapp.processEvents()
    assert not win._sidebar_is_collapsed

    real = win._sidebar_panel.minimumSizeHint().width()
    understated = real - 200
    assert understated > 0, "no room below the panel's own minimum to test with"
    stale = _width_the_sidebar_costs(win, sidebar_w=understated)

    # (a) open, in a window the understated number calls roomy and the panel
    #     itself does not.
    win._sidebar_saved_width = understated
    win._sidebar_intent = "auto"
    _set_splitter_width(win, qapp, stale + 8)
    assert win._sidebar_is_collapsed, (
        "the sidebar stayed open in a %d px window on the strength of a %d px "
        "width it does not fit into (it takes %d)"
        % (win.splitter.width(), understated, real))

    # (b) and the same understatement must not talk it back open. Read rather
    #     than typed, and with a fallback so this module still imports on a
    #     tree that has no such constant.
    import scheduler_app.ui.app as app_module
    hysteresis = (2 * _handle_width(win)
                  + getattr(app_module, "SIDEBAR_REOPEN_HYSTERESIS", 24))
    assert hysteresis + 16 < real - understated, (
        "the reopen margin swallows the difference this test is about")
    win._sidebar_saved_width = understated
    _set_splitter_width(win, qapp, stale + hysteresis + 16)
    assert win._sidebar_is_collapsed, (
        "a %d px window reopened a sidebar that needs %d px more than the "
        "width it was judged against" % (win.splitter.width(), real - understated))


def test_a_language_change_is_decided_with_the_new_language_s_measurements(
        make_app, qapp):
    """``setText`` only *posts* a layout request; the decision cannot wait.

    Measured across all 22 locales with the layout left un-activated: the
    sidebar panel reports the **previous** language's minimum every single time
    — af read 552 when it was 468, ar read 468 when it was 144, zh read 552
    when it was 252. That number is one of the two the auto-collapse threshold
    is made of, so every language change would be decided on the language
    before it.
    """
    from scheduler_app.i18n.translations import TRANSLATIONS

    win = _shown(make_app, qapp)
    win._sidebar_expand_btn.click()
    qapp.processEvents()
    try:
        stale = []
        for lang in sorted(TRANSLATIONS):
            win._set_language(lang)
            at_decision_time = win._sidebar_panel.minimumSizeHint().width()
            qapp.processEvents()
            once_settled = win._sidebar_panel.minimumSizeHint().width()
            if at_decision_time != once_settled:
                stale.append("%s (read %d, is %d)"
                             % (lang, at_decision_time, once_settled))
        assert not stale, (
            "%d of %d language changes were decided against the previous "
            "language's sidebar:\n  %s"
            % (len(stale), len(TRANSLATIONS), "\n  ".join(stale)))
    finally:
        _restore_language(win, qapp)


def test_no_language_pays_for_a_tab_it_could_have_reached(make_app, qapp):
    """Every locale reaches every tab, or the sidebar is not what stopped it.

    The tab bar never elides, so a tab that does not fit is a tab behind a
    scroll arrow. Its size hint spans 319 px across the 22 shipped locales, and
    the shipped default window cleared Turkish by exactly 0 px. The property
    that has to hold in all 22 is therefore not "everything fits" — offscreen
    nothing does — but "the sidebar is not what is standing in the way".

    Both directions are checked, because the threshold moves with the language
    and only one of them is about hoarding: a locale whose tab bar does not fit
    must not still be paying for an open sidebar, *and* a locale whose tab bar
    does fit must get its sidebar back.
    """
    from scheduler_app.i18n.translations import TRANSLATIONS

    win = _shown(make_app, qapp)
    try:
        # Pass 1: what each locale would need. Nothing is asserted here; the
        # window is then set to a width that suits about half of them, so both
        # directions are actually exercised rather than assumed.
        needs = {}
        for lang in sorted(TRANSLATIONS):
            win._set_language(lang)
            qapp.processEvents()
            needs[lang] = _width_the_sidebar_costs(win)
        middling = sorted(needs.values())[len(needs) // 2]
        win._sidebar_intent = "auto"     # nobody has decided anything yet
        _set_splitter_width(win, qapp, middling)

        hoarding, hiding = [], []
        for lang in sorted(TRANSLATIONS):
            win._set_language(lang)
            qapp.processEvents()
            bar = win.notebook.tabBar()
            needed = _width_the_sidebar_costs(win)
            slack = 2 * _handle_width(win) + 32
            if bar.width() < bar.sizeHint().width() \
                    and not win._sidebar_is_collapsed:
                hoarding.append(
                    "%s (%d px of tab bar in %d px of notebook, sidebar open "
                    "at %d)" % (lang, bar.sizeHint().width(), bar.width(),
                                win._sidebar_panel.width()))
            if win.splitter.width() > needed + slack \
                    and win._sidebar_is_collapsed:
                hiding.append(
                    "%s (%d px of window, %d needed, sidebar still shut)"
                    % (lang, win.splitter.width(), needed))
        assert not hoarding, (
            "%d of %d locales lost a tab to the sidebar:\n  %s"
            % (len(hoarding), len(TRANSLATIONS), "\n  ".join(hoarding)))
        assert not hiding, (
            "%d of %d locales kept the sidebar shut in a window with room "
            "for it:\n  %s" % (len(hiding), len(TRANSLATIONS),
                               "\n  ".join(hiding)))
    finally:
        _restore_language(win, qapp)


# ── 3. The decision is re-taken whenever the grid's width changes ───────────
#
# The two tests below are about the half of ST-UI-013 the first implementation
# left out. ``_apply_sidebar_intent`` ran from ``resizeEvent``,
# ``_init_splitter_sizes`` and ``_set_language`` — every trigger it had was a
# change to the *window*. But the quantity on the other side of the comparison
# is the *content*: the scene the current tab draws. Opening a file with two
# more days, or clicking the tab that draws every group at once, moves the
# threshold by hundreds of pixels while the window never moves at all, and the
# sidebar went on holding its 314 px in exactly the state the feature exists to
# get out of.

def test_a_wider_timetable_takes_the_sidebar_s_room_with_no_resize(
        make_app, qapp, make_state):
    """Opening a file with more days re-takes the decision. Nothing resizes.

    Both thresholds are measured off this same window, one after the other,
    with the sidebar open for both, so what is asserted is that the app agrees
    with a comparison between two of its own numbers — not that either number
    has any particular value on this platform.
    """
    win = _shown(make_app, qapp)
    # "Show everything" draws every group side by side, so its scene is the one
    # that beats the tab bar's size hint offscreen as well as natively; on the
    # filtered tabs the tab bar is the wider of the two and the day count never
    # reaches the comparison at all.
    win.notebook.setCurrentIndex(3)
    qapp.processEvents()
    win._sidebar_expand_btn.click()
    qapp.processEvents()
    assert not win._sidebar_is_collapsed

    narrow = make_state(n_days=4, n_slots=8, n_classes=24, seed=7)
    wide = make_state(n_days=7, n_slots=10, n_classes=42, seed=7)

    _load(win, qapp, wide)
    wide_needs = _width_the_sidebar_costs(win)
    _load(win, qapp, narrow)
    narrow_needs = _width_the_sidebar_costs(win)
    assert wide_needs - narrow_needs > 200, (
        "three more days moved the threshold by only %d px; there is no band "
        "between them for this test to sit in"
        % (wide_needs - narrow_needs))

    # A window that fits the four-day timetable and its sidebar, and cannot fit
    # the seven-day one alongside it.
    win._sidebar_intent = "auto"          # nobody has decided anything
    _set_splitter_width(win, qapp, (narrow_needs + wide_needs) // 2)
    _assert_really_laid_out(win)
    room = win.splitter.width()
    assert narrow_needs <= room < wide_needs, (
        "the window settled at %d px, outside the %d..%d band this test needs"
        % (room, narrow_needs, wide_needs))
    assert not win._sidebar_is_collapsed, (
        "the sidebar yielded in a window that has room for it (%d px against "
        "%d needed)" % (room, narrow_needs))

    # The user opens a file with three more days in it. The window does not
    # move; only what it has to draw does.
    _load(win, qapp, wide)
    assert win.splitter.width() == room, (
        "the window moved between the two measurements (%d -> %d); nothing "
        "compared across them means anything"
        % (room, win.splitter.width()))
    assert win._sidebar_is_collapsed, (
        "a %d px window is drawing a timetable that needs %d and the sidebar "
        "is still open; the decision was only ever re-taken on a resize"
        % (room, wide_needs))


def test_switching_to_a_wider_tab_takes_the_sidebar_s_room(
        make_app, qapp, make_state):
    """Clicking a tab changes the content width without touching the window.

    ``notebook.currentChanged`` goes straight to ``_render_current_tab`` and
    never reaches ``refresh_grid``, so a fix hung off the latter alone would
    leave this path exactly as it was.
    """
    win = _shown(make_app, qapp)
    win._sidebar_expand_btn.click()
    qapp.processEvents()
    _load(win, qapp, make_state(n_days=7, n_slots=10, n_classes=42, seed=7))

    win.notebook.setCurrentIndex(0)
    qapp.processEvents()
    filtered_needs = _width_the_sidebar_costs(win)
    win.notebook.setCurrentIndex(3)
    qapp.processEvents()
    everything_needs = _width_the_sidebar_costs(win)
    assert everything_needs - filtered_needs > 200, (
        "the two tabs want widths %d px apart; there is no band between them"
        % (everything_needs - filtered_needs))

    win.notebook.setCurrentIndex(0)
    qapp.processEvents()
    win._sidebar_intent = "auto"
    _set_splitter_width(win, qapp, (filtered_needs + everything_needs) // 2)
    _assert_really_laid_out(win)
    room = win.splitter.width()
    assert filtered_needs <= room < everything_needs, (
        "the window settled at %d px, outside the %d..%d band this test needs"
        % (room, filtered_needs, everything_needs))
    assert not win._sidebar_is_collapsed, (
        "the sidebar yielded on the filtered tab, which fits (%d px against "
        "%d needed)" % (room, filtered_needs))

    win.notebook.setCurrentIndex(3)       # the user clicks "Show everything"
    qapp.processEvents()
    assert win.splitter.width() == room, (
        "the window moved when the tab changed (%d -> %d)"
        % (room, win.splitter.width()))
    assert win._sidebar_is_collapsed, (
        "switching to a tab that needs %d px left the sidebar open in a %d px "
        "window" % (everything_needs, room))
