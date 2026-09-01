"""Phase 10, item 12 — the warning log: no timestamps, a 120 px ceiling, an unbounded history.

ST-UI-019 · ``scheduler_app/ui/widgets.py::WarningLogPanel``

Three separate claims, asserted separately, because they have three different
answers and only two of them are defects.

1. **No timestamps.**  ``log()`` stores ``(message, kind)`` and ``_line()``
   renders ``<span style="color:#991B1B">{message}</span>``.  Nothing anywhere
   in the widget reads a clock.  Measured verbatim on the real Windows
   platform::

       panel.log("Ayarlar kaydedilemedi: disk dolu", "error")
       panel._log_area.toPlainText() == 'Ayarlar kaydedilemedi: disk dolu'
       panel._sticky[0]              == ('Ayarlar kaydedilemedi: disk dolu', 'error')

   So a user looking at the expanded panel after an afternoon's work sees a
   stack of sentences in an order they have to trust, with no way to tell
   whether the save failure at the bottom happened two minutes or two hours
   ago.  ``_show_toast`` mirrors *every* toast into this list, so the panel is
   the app's only session history.

   ``test_i12_1b`` is the discriminating one: ``set_derived`` calls
   ``_render_all``, which rebuilds the whole document from ``_messages``.  A fix
   that only prefixes the clock inside ``_append_rendered`` passes 1a and fails
   1b — the timestamps silently vanish on the next repaint.

2. **The 120 px ceiling.**  It is *not* a lost-content bug: ``_log_area`` is a
   ``QTextEdit`` with ``ScrollBarAsNeeded``, so everything is reachable.  What
   120 px costs is how much is reachable *at once*, and that is measured here
   rather than argued.  On the real Windows platform with Segoe UI 9 pt the
   viewport is 118 px and a row is 15.2–15.4 px, so **7 rows** are legible and
   the 8th is clipped.  Offscreen (no Segoe UI, "Sans Serif" fallback) a row is
   13.2–13.4 px and the same panel reports **8** — which is why this module is
   meant to be run on the real platform and prints both.

   The hard number is not 120 but 160: ``_toggle_expand`` does
   ``setMaximumHeight(160 if self._expanded else 30)`` on the panel itself, and
   the panel sits in ``app.py``'s plain ``QVBoxLayout`` (``outer_layout``, line
   1927) — *not* in a splitter.  ``self.splitter`` exists in ``app.py`` but is
   ``Qt.Orientation.Horizontal`` and holds the grid and the side panel.  So the
   user cannot make the log taller on a 4K screen and cannot make it shorter on
   a laptop; 7 rows is 7 rows.  Meanwhile ``_refresh_warnings`` alone publishes
   up to ``_MAX_CONFLICT_LOG_ENTRIES = 25`` conflict rows plus two rows per
   year/branch, so one ordinary repaint routinely fills four screenfuls of a
   panel that shows one.

3. **The unbounded ``_sticky`` list.**  ``log()`` appends and nothing ever
   trims.  Measured: 100 → 100, 1000 → 1000, 10000 → 10000 entries, 358 889
   characters and 10 000 ``QTextDocument`` blocks.

   The consequence is not the bytes — it is that ``set_derived`` →
   ``_render_all`` rebuilds the document from ``_sticky + _derived`` *every
   time the derived set changes*, i.e. on every ``refresh_grid`` after an edit.
   Measured on the real platform, median over 10 alternating ``set_derived``
   calls:

       ``_sticky``      0        100       1000       10000
       ``set_derived``  0.06 ms  0.74 ms   8.43 ms    88.18 ms

   which is dead linear in the history length.  That is ST-PERF-003's own
   growth shape, re-entering through the sticky door that Phase 2 left open.

What ``tests/test_warning_log_growth.py`` already pins (checked by mutation)
---------------------------------------------------------------------------
It pins the **derived** half only, and it pins it hard.  Mutating
``set_derived`` to ``self._derived = self._derived + new`` (comment marker
``PHASE10-I12 MUTATION``, reverted with ``git checkout``, tree confirmed clean)
turned **11 of its 20** tests red.

It bounds ``_sticky`` by exactly nothing, and that was established positively,
not by reading.  Under a pytest plugin that gives **every** panel 5000 stale
history entries at construction and appends **25 junk entries on every
``log()`` call**, ``tests/test_warning_log_growth.py`` is still **20/20
green**.  This is precisely the "a test that measures nothing" shape: it holds
because ``_refresh_warnings`` publishes through ``set_derived`` and
``_report_settings_problem`` is rate-limited *at the producer*, so the seeded
fixture appends nothing sticky across 20 refreshes and every "count is
unchanged" assertion is satisfied by an unchanged pile of garbage.  Its one
sticky assertion —
``test_settings_failure_is_reported_once_and_survives_the_rebuild`` — requires
the sentinel to *survive*, which constrains a bound from below and never from
above.

A cap is therefore new ground, and the survival requirement is the one thing it
must respect: a FIFO cap evicts oldest-first, and the settings-write notice is
rate-limited to once per session, so it is likely to be among the oldest
entries in the panel.  With the cap at 400 that module stays 20/20 (measured);
with a cap in the low tens it would eventually evict the only message telling a
user their work is not being saved.

Compatibility the fix must not break (asserted green here as guards)
--------------------------------------------------------------------
``tests/test_unplaced_explanations.py`` reads ``app.warning_log._sticky[before:]``
in four places and unpacks it as ``for t, k in entries`` — **two**-tuples.  A
timestamp added as a third tuple element breaks that module.  ``test_i12_1c``
pins the shape so a fix cannot quietly widen it.

Status
------
Today, on both platforms: **8 red, 2 green** (1c and 2a are the guards).  A
candidate fix built as a pytest plugin — ``HH:mm`` stamp kept in a parallel
``_sticky_times`` list and rendered by both ``log()`` and ``_render_all``,
``_sticky`` FIFO-capped at 400, the expanded ``maximumHeight`` caps lifted —
takes this module to **10/10** and leaves
``test_warning_log_growth`` + ``test_unplaced_explanations`` +
``test_settings_recovery`` + ``test_input_escaping`` +
``test_conflict_visibility`` at **137/137**, unchanged from baseline.
No production file was edited to measure that.

Platform
--------
The geometry tests read ``QApplication.platformName()`` and put it in every
failure message.  Run this module twice::

    QT_QPA_PLATFORM=windows  ...   # the real numbers
    QT_QPA_PLATFORM=offscreen ...  # what CI sees
"""
import re
import time

import pytest

pytestmark = [pytest.mark.ui]


# One ordinary repaint of a conflicted timetable: app.py caps its conflict rows
# at _MAX_CONFLICT_LOG_ENTRIES = 25, before the per-year/branch workload rows.
_ONE_REFRESH_ROWS = 25

# What a session history has to hold before it is allowed to forget. Argued in
# the report, not here: the point of the assertion is that *some* bound exists.
_MAX_STICKY = 1000

_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")


# ── Helpers ─────────────────────────────────────────────────────────────────

def _panel(qapp, host_h=800):
    """A real, laid-out, never-on-screen ``WarningLogPanel``, in app.py's layout.

    ``WA_DontShowOnScreen`` + ``show()``, never ``sizeHint()``.

    The panel is put in the same shape ``app.py::_build_ui`` gives it — a
    ``QVBoxLayout`` where the grid splitter carries all the stretch and the
    panel carries none (``outer_layout.addWidget(self.splitter, 1)`` then
    ``outer_layout.addWidget(self.warning_log)``, lines 1563 and 1927). A bare
    top-level panel is NOT equivalent: resizing it before ``show()`` clamps it
    against the collapsed ``maximumHeight(30)`` and it never recovers, which
    under-reports the height by 52 px. Cross-checked against a real
    ``SchedulerApp``: this host reproduces its numbers exactly (panel 147 px,
    log area 120, viewport 118).
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QVBoxLayout, QWidget
    from scheduler_app.ui.widgets import WarningLogPanel

    host = QWidget()
    host.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    lay = QVBoxLayout(host)
    lay.setContentsMargins(5, 0, 5, 0)
    lay.setSpacing(0)
    lay.addWidget(QWidget(), 1)          # stands in for the grid splitter
    p = WarningLogPanel()
    lay.addWidget(p)
    p._i12_host = host                   # keep the host alive for the test
    host.resize(1200, host_h)
    host.show()
    qapp.processEvents()
    return p


def _drop(panel):
    """Tear the panel down through the host that owns it."""
    host = getattr(panel, "_i12_host", None)
    (host or panel).deleteLater()


def _platform():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    return "%s / font %s %spt" % (
        QApplication.platformName(), app.font().family(), app.font().pointSize())


def _row_metrics(panel, qapp):
    """``(viewport_px, row_px, full_rows_visible)`` for the expanded log area."""
    area = panel._log_area
    qapp.processEvents()
    doc = area.document()
    blocks = max(doc.blockCount(), 1)
    row_px = doc.size().height() / blocks
    vp = area.viewport().height()
    return vp, row_px, int(vp // row_px) if row_px else 0


# ══════════════════════════════════════════════════════════════════════════
#  1. Timestamps
# ══════════════════════════════════════════════════════════════════════════

def test_i12_1a_a_logged_row_says_when_it_happened(qapp):
    """ST-UI-019 — the panel is the app's session history and it has no clock.

    A failure (today) means a teacher reading "Ayarlar kaydedilemedi" cannot
    tell whether their work stopped being saved a minute ago or before lunch,
    and cannot correlate it with anything else that happened.
    """
    panel = _panel(qapp)
    try:
        panel.log("Ayarlar kaydedilemedi: disk dolu", "error")
        qapp.processEvents()
        row = panel._log_area.toPlainText()
    finally:
        _drop(panel)

    assert _TIME_RE.search(row), (
        "the rendered log row carries no time of day. Row verbatim: %r "
        "(platform %s)" % (row, _platform()))


def test_i12_1b_the_time_survives_a_derived_rebuild(qapp):
    """ST-UI-019 — the discriminating half: ``_render_all`` must reproduce it.

    ``set_derived`` rebuilds the whole document from ``_messages``, so a fix
    that prefixes the clock only in ``_append_rendered`` shows timestamps until
    the next repaint and then silently drops them. This test is the only thing
    between that fix and a green suite.
    """
    panel = _panel(qapp)
    try:
        panel.log("Ayarlar kaydedilemedi: disk dolu", "error")
        # Exactly what refresh_grid does when the timetable changes.
        panel.set_derived([("Pazartesi asiri yuklu", "warning")])
        qapp.processEvents()
        row = panel._log_area.toPlainText()
    finally:
        _drop(panel)

    assert _TIME_RE.search(row), (
        "the sticky row lost its time of day when set_derived re-rendered the "
        "document. Document verbatim: %r (platform %s)" % (row, _platform()))


def test_i12_1c_a_stored_sticky_entry_is_still_a_message_kind_pair(qapp):
    """Guard, green today — the shape ``test_unplaced_explanations.py`` unpacks.

    That module does ``for t, k in app.warning_log._sticky[before:]`` in four
    places. If the timestamp fix widens the tuple, this goes red here instead
    of over there, and the fix carries a cross-module regression.
    """
    panel = _panel(qapp)
    try:
        panel.log("Ders 12 yerlestirilemedi", "error")
        entries = list(panel._sticky)
    finally:
        _drop(panel)

    assert len(entries) == 1, entries
    assert isinstance(entries[0], tuple) and len(entries[0]) == 2, (
        "_sticky entries must stay (message, kind) 2-tuples; "
        "tests/test_unplaced_explanations.py unpacks exactly two. Got: %r"
        % (entries[0],))
    text, kind = entries[0]
    assert "Ders 12 yerlestirilemedi" in text, entries[0]
    assert kind == "error", entries[0]


# ══════════════════════════════════════════════════════════════════════════
#  2. The 120 px ceiling — geometry, real platform
# ══════════════════════════════════════════════════════════════════════════

def test_i12_2a_the_log_area_is_scrollable_at_all(qapp):
    """Guard, green today — establishes that 120 px hides rows, it does not eat them.

    If this ever goes red the ceiling stops being a legibility complaint and
    becomes data loss, and the rest of section 2 is understated.
    """
    from PyQt6.QtCore import Qt

    panel = _panel(qapp)
    try:
        panel._toggle_expand()
        for i in range(_ONE_REFRESH_ROWS):
            panel.log("9-A: Pazartesi asiri yuklu (%d)" % (i + 1), "warning")
        qapp.processEvents()
        area = panel._log_area
        sb = area.verticalScrollBar()
        assert sb is not None and sb.maximum() > 0, (
            "the log area does not scroll, so the %d rows past the ceiling are "
            "unreachable, not merely off-screen" % _ONE_REFRESH_ROWS)
        assert area.verticalScrollBarPolicy() != (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        assert area.document().blockCount() == _ONE_REFRESH_ROWS
    finally:
        _drop(panel)


def test_i12_2b_a_user_who_wants_more_of_the_log_can_have_it(qapp):
    """ST-UI-019 — the ceiling measured as what it denies, not as a default.

    Deliberately **not** "the default must be taller". The panel shares its
    column with the timetable: at 1366x768 the grid gets 477 px, so spending
    another 170 px on the log by default is a real loss on the machine most
    Turkish schools actually run. What is indefensible is that the trade is
    not offered at all — ``_toggle_expand`` pins ``maximumHeight`` at 160 and
    ``_log_area`` at 120, so *asking* for a taller log does nothing.

    So this drives the ask: give the panel 500 px, exactly as dragging a
    vertical splitter handle would, and check the log area takes it.

    Default-state measurement, for the record (25 rows, ``WA_DontShowOnScreen``
    + ``show()``, 2026-08-29):

        real Windows / Segoe UI 9pt   viewport 118 px, row 15.3 px -> 7 rows
        offscreen / Sans Serif 9pt    viewport 118 px, row 13.3 px -> 8 rows

    The offscreen figure over-reports by one row, which is why this module is
    run on the real platform and both numbers are on the record.

    Cross-checked against a real ``SchedulerApp`` on the real platform: panel
    147 px, log area 120, viewport 118, **7 of 25 rows** at 1366x768, at
    1920x1080 and at 3840x2160 alike, while the grid above it went
    477 -> 789 -> 1869 px. The log is the one thing on screen that does not
    scale with the screen.
    """
    panel = _panel(qapp)
    try:
        panel._toggle_expand()
        for i in range(_ONE_REFRESH_ROWS):
            panel.log("9-A: Pazartesi asiri yuklu (%d)" % (i + 1), "warning")
        qapp.processEvents()
        vp0, row_px, default_visible = _row_metrics(panel, qapp)
        default_h = panel.height()

        # The user drags the divider down. A QSplitter handle resizes the pane
        # exactly like this; today maximumHeight(160) swallows it.
        panel.resize(panel.width(), 500)
        qapp.processEvents()
        vp, row_px, visible = _row_metrics(panel, qapp)
        panel_h, area_h = panel.height(), panel._log_area.height()
    finally:
        _drop(panel)

    assert visible >= 15, (
        "asked for 500 px of log panel and got %d; %d of %d rows are legible "
        "(viewport %d px, row %.1f px, log area height %d). Default state was "
        "%d px showing %d rows (viewport %d). One refresh publishes up to %d "
        "conflict rows alone. platform %s"
        % (panel_h, visible, _ONE_REFRESH_ROWS, vp, row_px, area_h,
           default_h, default_visible, vp0, _ONE_REFRESH_ROWS, _platform()))


def test_i12_2c_the_expanded_panel_height_is_not_hard_pinned(qapp):
    """ST-UI-019 — the mechanism behind 2b: a fixed 160 px, and no splitter.

    ``_toggle_expand`` does ``setMaximumHeight(160 if self._expanded else 30)``
    and ``app.py`` adds the panel to a plain ``QVBoxLayout``. So the ceiling is
    the same on a 1366x768 laptop and on a 4K monitor, and the user has no
    handle to drag. A failure means the panel cannot be made to show more,
    whatever the screen.

    ``app.py`` does own a ``QSplitter`` (``self.splitter``, line 1562) — but it
    is ``Qt.Orientation.Horizontal`` and separates the grid from the side
    panel. Nothing vertical exists for the log to be a pane of. Measured: with
    the window at 1366x768, 1920x1080 and 3840x2160 the expanded panel is
    147 px in all three.
    """
    panel = _panel(qapp)
    try:
        collapsed = panel.maximumHeight()
        panel._toggle_expand()
        qapp.processEvents()
        expanded = panel.maximumHeight()
        area_max = panel._log_area.maximumHeight()
    finally:
        _drop(panel)

    assert expanded >= 400, (
        "the expanded panel is pinned at maximumHeight=%d (collapsed %d, log "
        "area maximumHeight=%d), so no screen size and no user action can show "
        "more than a handful of rows. platform %s"
        % (expanded, collapsed, area_max, _platform()))


# ══════════════════════════════════════════════════════════════════════════
#  3. The unbounded _sticky list
# ══════════════════════════════════════════════════════════════════════════

def test_i12_3a_the_sticky_history_is_bounded(qapp):
    """ST-UI-019 / ST-PERF-003 — ``log()`` appends and nothing ever trims.

    Measured at the three scales the item asks for, in one test so all three
    numbers land in one failure message rather than two of them passing
    vacuously against a bound they are already under.
    """
    rows = []
    for n in (100, 1000, 10000):
        panel = _panel(qapp)
        try:
            for i in range(n):
                panel.log(
                    "Ders %d yerlestirilemedi: uygun oda yok" % i, "warning")
            rows.append((n, len(panel._sticky),
                         panel._log_area.document().blockCount(),
                         len(panel._log_area.toPlainText())))
        finally:
            _drop(panel)
        qapp.processEvents()

    trace = "; ".join(
        "log()x%d -> _sticky=%d, blocks=%d, chars=%d" % r for r in rows)
    assert rows[-1][1] <= _MAX_STICKY, (
        "the sticky history has no bound at all: %s (platform %s)"
        % (trace, _platform()))


def test_i12_3b_the_rendered_document_is_bounded_too(qapp):
    """ST-UI-019 — bounding the list without bounding the document fixes nothing.

    ``_append_rendered`` calls ``QTextEdit.append``, which has no block cap
    (``setMaximumBlockCount`` is ``QPlainTextEdit``, not ``QTextEdit``). So a
    fix that trims ``_sticky`` but leaves the document alone leaves the same
    10 000 blocks and the same characters on the heap, and the panel then
    *disagrees with its own store*.
    """
    panel = _panel(qapp)
    try:
        for i in range(10000):
            panel.log("Ders %d yerlestirilemedi: uygun oda yok" % i, "warning")
        qapp.processEvents()
        blocks = panel._log_area.document().blockCount()
        chars = len(panel._log_area.toPlainText())
        stored = len(panel._sticky)
    finally:
        _drop(panel)

    assert blocks <= _MAX_STICKY + 1, (
        "after 10000 log() calls the QTextDocument holds %d blocks and %d "
        "characters (store holds %d)" % (blocks, chars, stored))


def test_i12_3c_a_long_history_does_not_slow_every_repaint(qapp):
    """ST-UI-019 — why the unbounded list is ST-PERF-003's shape, not just bytes.

    ``set_derived`` -> ``_render_all`` rebuilds the document from
    ``_sticky + _derived``, so the cost of *every* repaint whose findings
    changed is linear in the history. Measured on the real platform, median of
    ten alternating ``set_derived`` calls:

        _sticky      0        100       1000       10000
        set_derived  0.06 ms  0.74 ms   8.43 ms    88.18 ms

    The assertion is a ratio between two medians taken in the same process on
    the same box, not an absolute millisecond threshold, so it cannot flake on
    a loaded runner: a hundredfold history must not cost a hundredfold repaint.
    """
    def median_set_derived(panel):
        times = []
        for k in range(11):
            t0 = time.perf_counter()
            panel.set_derived([("Pazartesi asiri yuklu %d" % (k % 2), "warning")])
            times.append(time.perf_counter() - t0)
        return sorted(times)[len(times) // 2] * 1000.0

    small = _panel(qapp)
    big = _panel(qapp)
    try:
        for i in range(100):
            small.log("Ders %d yerlestirilemedi" % i, "warning")
        for i in range(10000):
            big.log("Ders %d yerlestirilemedi" % i, "warning")
        t_small = median_set_derived(small)
        t_big = median_set_derived(big)
    finally:
        _drop(small)
        _drop(big)

    assert t_small > 0, "unmeasurable baseline (%r ms)" % t_small
    ratio = t_big / t_small
    assert ratio <= 8.0, (
        "a repaint costs %.2f ms with 10000 sticky entries against %.2f ms "
        "with 100 (%.0fx). _render_all rebuilds the document from "
        "_sticky + _derived, so an unbounded history is an unbounded per-"
        "repaint cost. platform %s" % (t_big, t_small, ratio, _platform()))


def test_i12_3d_the_real_production_channel_grows_without_bound(
        qapp, make_app, monkeypatch):
    """ST-UI-019 — driven through ``SchedulerApp``, not through the widget.

    ``_show_toast`` mirrors *every* toast into the sticky history
    (``app.py``, Phase 2's single-channel decision). That is the real
    growth driver: a session's toasts are unbounded in number and nothing in
    the app or the panel trims them. Nothing here plants the list it measures —
    the entries arrive because the production method put them there.

    Only the ``Toast`` *popup* is stubbed, and it has to be: on the real
    Windows platform each one is a top-level ``Qt.WindowType.ToolTip`` window
    that is shown and raised, so 2000 of them would paint 2000 windows over the
    user's desktop and arm 2000 timers. The half under test — the mirror into
    ``warning_log`` — runs unmodified.
    """
    import scheduler_app.ui.app as app_mod

    monkeypatch.setattr(
        app_mod, "Toast", lambda *a, **k: None, raising=True)

    window = make_app()
    try:
        before = len(window.warning_log._sticky)
        for i in range(2000):
            window._show_toast("Ders %d kaydedildi" % i, "success")
        after = len(window.warning_log._sticky)
    finally:
        window.close()

    assert after - before > 0, (
        "_show_toast wrote nothing to the panel, so this test measured "
        "nothing (before=%d after=%d)" % (before, after))
    assert after <= _MAX_STICKY, (
        "2000 toasts through SchedulerApp._show_toast left %d entries in "
        "warning_log._sticky (was %d)" % (after, before))
