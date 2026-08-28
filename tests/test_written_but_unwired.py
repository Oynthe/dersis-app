"""Two Phase-5 safety mechanisms that were written and never called.

ST-ARCH-011 set out to delete dead code. Two of the symbols it found had no
callers *and should have had them* -- deleting them would have removed a fix
rather than dead weight. Both are wired up now, and pinned here.

ST-UI-007 (Qt half) · ``core/text_safety.qt_tooltip``
    Phase 5 escaped user text for reportlab and for the spreadsheet, and wrote
    ``qt_tooltip`` for the Qt half -- then never called it. ``setToolTip``
    sniffs its argument with ``Qt.mightBeRichText``, so the *format* of a grid
    tooltip depended on the user's own class name. Measured:
    ``mightBeRichText("Fizik <b>I</b>\\nLect-01\\nR001")`` is **True** and the
    tags are consumed, so the tooltip reads "Fizik I"; the same string for
    ``"<Vekil> Dersi"`` is **False** and renders literally. Two lessons, one
    grid, two different rendering rules, decided by the data.

ST-ARCH-011 · ``SchedulerApp._flush_before_state_swap``
    Zero callers, and its own docstring describes the loss: the ST-PERF-002
    autosave debounce reads ``self.state_data`` when it fires, so an edit made
    inside the 1.5 s window and followed by File > New or File > Open never
    reaches disk -- the timer persists whatever the state points at *then*.
"""
import pytest

pytest.importorskip("PyQt6.QtWidgets", reason="PyQt6 not installed")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QTextDocument  # noqa: E402

from scheduler_app.core.text_safety import qt_tooltip  # noqa: E402


# ── ST-UI-007: the grid tooltip ────────────────────────────────────────────

# Names whose *rendering rule* Qt currently decides from the content itself.
MARKUP_NAMES = [
    "Fizik <b>I</b>",        # mightBeRichText True  -> tags eaten today
    "<Vekil> Dersi",         # False -> literal today; must stay literal
    "A < B dersi",           # False today, but one <b> away from flipping
    "Matematik & Geometri",  # the ampersand must not become &amp; on screen
]


def _visible(tooltip_string):
    """What the user actually reads, once Qt has parsed the tooltip as rich."""
    doc = QTextDocument()
    doc.setHtml(tooltip_string)
    return doc.toPlainText()


@pytest.mark.ui
@pytest.mark.parametrize("name", MARKUP_NAMES)
def test_a_tooltip_shows_the_class_name_the_user_typed(qapp, name):
    """ST-UI-007 — a lesson's own name must survive its tooltip.

    A failure means hovering a lesson called "Fizik <b>I</b>" shows "Fizik I":
    the app quietly disagrees with the class list about what the class is
    called, and the user has no way to tell which one is right.
    """
    raw = "%s\nLect-01\nR001" % name
    assert name in _visible(qt_tooltip(raw)), (
        "the tooltip for %r renders as %r" % (name, _visible(qt_tooltip(raw))))


@pytest.mark.ui
def test_the_tooltip_format_does_not_depend_on_the_users_text(qapp):
    """ST-UI-007 — the real defect: Qt picked the format from the data.

    Without the wrapper, ``mightBeRichText`` is True for some class names and
    False for others, so two lessons on the same grid follow different
    rendering rules. This asserts the *variance* is gone, which the
    per-name test above cannot: that one passes for any name Qt happens to
    treat literally.
    """
    sniffed = {Qt.mightBeRichText("%s\nLect-01" % n) for n in MARKUP_NAMES}
    assert len(sniffed) > 1, (
        "fixture is no longer adversarial: Qt now treats every sample name the "
        "same way, so this test cannot detect the divergence it exists for")

    wrapped = {Qt.mightBeRichText(qt_tooltip("%s\nLect-01" % n))
               for n in MARKUP_NAMES}
    assert wrapped == {True}, (
        "after wrapping, Qt still classifies these tooltips differently: %r"
        % wrapped)


@pytest.mark.ui
def test_the_grid_actually_uses_the_wrapper(qapp):
    """ST-UI-007 — anti-vacuity: the helper existed for a phase, unused.

    The tests above exercise ``qt_tooltip`` directly, so they would all pass
    with ``renderer.py`` still calling ``setToolTip`` on a raw string -- which
    is exactly the state Phase 5 left behind.
    """
    import inspect
    from scheduler_app.ui import renderer

    src = inspect.getsource(renderer)
    raw_sites = [
        line.strip() for line in src.splitlines()
        if "setToolTip(_conflict_tooltip(" in line
    ]
    assert not raw_sites, (
        "renderer.py sets a tooltip from an unwrapped string: %r" % raw_sites)
    assert src.count("qt_tooltip(_conflict_tooltip(") == 2, (
        "expected both lesson painters to wrap their tooltip")


# ── ST-ARCH-011: the unflushed write ───────────────────────────────────────

@pytest.mark.ui
@pytest.mark.parametrize("action", ["new_schedule", "open_file"])
def test_swapping_the_schedule_lands_the_pending_write_first(
        make_app, monkeypatch, action):
    """ST-ARCH-011 — an edit must not be lost to File > New / File > Open.

    A failure means the user edits a lesson, opens another file within 1.5 s,
    and the edit is gone -- silently, with the save indicator having shown a
    pending write the whole time.
    """
    from PyQt6.QtWidgets import QFileDialog, QMessageBox

    app = make_app()
    order = []

    # No modal may open: offscreen, an unpatched one blocks the run forever.
    for name in ("question", "information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name,
                            staticmethod(lambda *a, **k:
                                         QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(app, "flush_auto_save",
                        lambda: order.append("flush"), raising=True)
    monkeypatch.setattr(app, "_check_setup", lambda *a, **k: None)
    monkeypatch.setattr(app, "refresh_grid", lambda *a, **k: None)

    if action == "new_schedule":
        monkeypatch.setattr(
            app, "_clear_impact_flags",
            lambda *a, **k: order.append("rebind"))
        app.new_schedule()
    else:
        from scheduler_app import storage
        monkeypatch.setattr(QFileDialog, "getOpenFileName",
                            staticmethod(lambda *a, **k: ("x.egu", "")))

        def _load(_path):
            order.append("rebind")
            return {"days": [], "slots": [], "classrooms": [],
                    "classroom_capacities": {}, "lecturers": [],
                    "lecturer_availability": {}, "years": {}, "classes": []}

        monkeypatch.setattr(storage, "load_encrypted", _load)
        app.open_file()

    assert "flush" in order, (
        "%s never flushed the debounced write; an edit made in the last 1.5 s "
        "is lost" % action)
    assert order.index("flush") < order.index("rebind"), (
        "%s flushed AFTER rebinding the state, which persists the wrong "
        "schedule: %r" % (action, order))


# ── ST-UI-007 · the open-slots panel, and the copy of it nobody runs ────────
#
# The Phase 7 register listed `core/text_safety.escape_qt_rich` as an unwired
# sanitiser guarding a live defect, and pointed at `WarningsDialog` and
# `OpenSlotsDialog` in `ui/dialogs.py` as the sites to wire it into. Those two
# dialogs render the defect exactly as described -- and **no code constructs
# either of them**; the only mention in the tree is an unused import in
# `ui/app.py`. The audit's own inventory says so: "Dialogs: WarningsDialog,
# OpenSlotsDialog (superseded by live panels)."
#
# The live panel is `SchedulerApp._refresh_open_slots`, and it carries the same
# defect through a different widget: `QLabel` defaults to AutoText, so Qt
# decides per string whether to parse markup. It needs the opposite remedy --
# `setTextFormat(PlainText)`, not escaping -- because a bare `html.escape` on a
# string Qt would have shown literally puts `&amp;` on screen, and "R&D Lab" is
# a plausible room name. These tests drive the panel a user can actually open.

# Room and slot labels whose *rendering rule* Qt decides from the content.
HOSTILE_NAMES = [
    "R&D Lab",           # must NOT come back as "R&amp;D Lab"
    "Lab <b>A</b>",      # a tag Qt knows: parsed, and the tags eaten
    "<Gecici> Derslik",  # a tag Qt does not know: rendered literally
    "A < B Odasi",       # not a tag at all: rendered literally
]


def _panel_state(room, slot):
    """A one-day, one-slot, one-room grid with nothing placed on it."""
    from scheduler_app.core.models import new_state

    state = new_state()
    state["days"] = ["monday"]
    state["slots"] = [slot]
    state["classrooms"] = [room]
    state["lecturers"] = ["Lect-001"]
    state["years"] = {"9": ["A"]}
    return state


def _shrunk_by_qt(label):
    """How many pixels Qt is currently eating out of *label*.

    Measured as the label's own sizeHint before and after its format is pinned
    to PlainText -- two widths off one widget in one process, so the offscreen
    platform's font never enters the comparison. Zero means Qt is drawing every
    character it was given; positive means it parsed some of them as markup and
    drew them as formatting instead.
    """
    from PyQt6.QtCore import Qt as _Qt

    before = label.sizeHint().width()
    label.setTextFormat(_Qt.TextFormat.PlainText)
    return label.sizeHint().width() - before


def _panel_labels(app, room, slot):
    from PyQt6.QtWidgets import QLabel

    app.state_data = _panel_state(room, slot)
    app.invalidate_open_slots()
    app._refresh_open_slots()
    return {lbl.text(): lbl
            for lbl in app._open_slots_container.findChildren(QLabel)}


@pytest.mark.ui
@pytest.mark.parametrize("room", HOSTILE_NAMES)
def test_the_open_slots_panel_draws_the_room_the_user_named(make_app, room):
    """ST-UI-007 -- the room in the panel must be the room in Setup.

    A failure means the free-slot list quietly disagrees with the classroom
    list about what a room is called, and nothing on screen says which one is
    right. Measured before the fix: "Lab <b>A</b>" was drawn 70px narrower
    than its own text, i.e. as "Lab A".
    """
    app = make_app()
    labels = _panel_labels(app, room, "09:00")

    assert room in labels, (
        "the open-slots panel lists no room called %r; it drew %r"
        % (room, sorted(labels)))
    assert _shrunk_by_qt(labels[room]) == 0, (
        "Qt is parsing the room name %r as markup and drawing less than the "
        "user typed" % room)


@pytest.mark.ui
@pytest.mark.parametrize("slot", HOSTILE_NAMES)
def test_the_open_slots_panel_draws_the_slot_the_user_named(make_app, slot):
    """ST-UI-007 -- and the time label beside it, from the same Setup table.

    A slot label is a by-name reference into an ordered list, so a label the
    panel draws differently from the one stored is a label the user cannot
    match to a row of the grid.
    """
    app = make_app()
    labels = _panel_labels(app, "R001", slot)

    assert slot in labels, (
        "the open-slots panel lists no slot called %r; it drew %r"
        % (slot, sorted(labels)))
    assert _shrunk_by_qt(labels[slot]) == 0, (
        "Qt is parsing the slot label %r as markup" % slot)


@pytest.mark.ui
def test_the_open_slots_filter_hint_draws_the_lesson_name(make_app):
    """ST-UI-007 -- the third label, which names the selected lesson."""
    app = make_app()
    hint = app._open_slots_filter_hint
    hint.setText("◉ Ders: Fizik <b>I</b>")

    assert _shrunk_by_qt(hint) == 0, (
        "Qt is parsing the filtered-for hint as markup, so the lesson it names "
        "is not the lesson the user selected")


@pytest.mark.ui
def test_the_panel_does_not_let_the_data_pick_the_rendering_rule(make_app):
    """ST-UI-007 -- the real defect: Qt chose the rule per string.

    The per-name tests above each pass for any name Qt happens to treat
    literally, so on their own they cannot tell a fix from a lucky fixture.
    This asserts the *variance* is gone: the fixture is checked to still
    straddle Qt's sniff, and then every name must survive it.
    """
    sniffed = {Qt.mightBeRichText(n) for n in HOSTILE_NAMES}
    assert len(sniffed) > 1, (
        "fixture is no longer adversarial: Qt now treats every sample name the "
        "same way, so this test cannot detect the divergence it exists for")

    app = make_app()
    eaten = {}
    for room in HOSTILE_NAMES:
        labels = _panel_labels(app, room, "09:00")
        eaten[room] = _shrunk_by_qt(labels[room])
    assert set(eaten.values()) == {0}, (
        "Qt still renders these room names by different rules: %r" % eaten)


@pytest.mark.ui
def test_no_dialog_reimplements_the_two_live_report_panels(qapp):
    """ST-ARCH-003 -- the superseded copy must not come back.

    `ui/dialogs.py` held a `WarningsDialog` and an `OpenSlotsDialog` that
    rendered the same two reports through `QTextEdit.append`, both carrying the
    ST-UI-007 defect and neither constructed anywhere in the tree. A fix applied
    to that copy would have looked done and changed nothing a user sees -- the
    shape Phase 6 was caught by in `data_io/exporter.py`, where 48 tests guarded
    an Excel writer with no production caller.
    """
    from scheduler_app.ui import dialogs

    reimplemented = [name for name in ("WarningsDialog", "OpenSlotsDialog")
                     if hasattr(dialogs, name)]
    assert not reimplemented, (
        "ui/dialogs.py defines %r again; the live surfaces are "
        "SchedulerApp._refresh_warnings and SchedulerApp._refresh_open_slots"
        % reimplemented)


