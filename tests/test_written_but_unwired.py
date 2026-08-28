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
