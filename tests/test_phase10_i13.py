"""Item 13 / ST-UI-020 — the empty state, and the "terminology drift" claim.

Two claims live under one finding and they are measured separately here.

(a) "The empty state offers no guidance."
    Partly true, and the true part is sharper than the finding. Guidance
    exists, but only as a **modal question asked once**: ``FirstRunController.
    _step_setup`` on first run, and ``SchedulerApp._check_setup`` after File >
    New. Neither leaves anything behind. Once the question is answered — or on
    the second launch, where the ``initial_setup_prompt_handled`` flag already
    stands — the window itself is *completely* blank: every one of the four
    timetable scenes holds zero items, the Open-Slots panel holds zero rows,
    the Unplaced panel holds zero rows, and nothing on screen names a next
    step.

(b) "Terminology drifts across screens."
    True and measurable. One field — the lesson's *name* — is headed by four
    different translation keys on four production surfaces.

Conventions (from ``test_placement_vocabulary.py``): never ``isVisible()``
(these windows are never shown, so it is uniformly False), never hardcode an
English string in an assertion (the suite is pinned to Turkish), read what a
widget rendered rather than recomputing its input.
"""
import csv
import os

import pytest

pytest.importorskip("PyQt6.QtWidgets", reason="PyQt6 not installed")

from PyQt6.QtWidgets import (  # noqa: E402
    QAbstractButton, QGraphicsSimpleTextItem, QGraphicsTextItem, QLabel,
    QMessageBox,
)

pytestmark = pytest.mark.ui


TAB_VIEWS = ["grid_view1", "grid_view2", "grid_view3", "grid_view4"]
TABS = ["tab_classroom", "tab_group", "tab_lecturer", "tab_everything",
        "dashboard_widget"]


# ── helpers ──────────────────────────────────────────────────────────────

def _visible_texts(root):
    """Label and button captions under *root* that are not explicitly hidden."""
    out = []
    for w in root.findChildren(QLabel):
        if not w.isHidden() and w.text().strip():
            out.append(w.text().strip())
    for w in root.findChildren(QAbstractButton):
        if not w.isHidden() and w.text().strip():
            out.append(w.text().strip())
    return out


def _scene_texts(view):
    scene = view.scene()
    if scene is None:
        return []
    out = []
    for it in scene.items():
        if isinstance(it, QGraphicsTextItem):
            out.append(it.toPlainText())
        elif isinstance(it, QGraphicsSimpleTextItem):
            out.append(it.text())
    return [t for t in out if t.strip()]


def _walk_every_tab(w, qapp):
    """Visit all five tabs so each one has actually rendered, and collect.

    The status bar is deliberately NOT collected. It always differs between a
    blank and a populated app because it carries the counts, and "0 ders" is a
    tally, not guidance — including it would make the comparison below pass
    against a screen with nothing on it.
    """
    texts, scene_items = [], 0
    for i, name in enumerate(TABS):
        w.notebook.setCurrentIndex(i)
        qapp.processEvents()
        texts.extend(_visible_texts(getattr(w, name)))
        if i < len(TAB_VIEWS):
            st = _scene_texts(getattr(w, TAB_VIEWS[i]))
            texts.extend(st)
            scene_items += len(getattr(w, TAB_VIEWS[i]).scene().items()) \
                if getattr(w, TAB_VIEWS[i]).scene() else 0
    texts.extend(_visible_texts(w._open_slots_scroll))
    texts.extend(_visible_texts(w.unplaced_list))
    texts.extend(_visible_texts(w.warning_log))
    return texts, scene_items


def _populated():
    from scheduler_app.core.models import new_state, new_class, mark_placed
    s = new_state()
    s["days"] = ["monday", "tuesday"]
    s["slots"] = ["09:00", "10:00"]
    s["classrooms"] = ["R001", "R002"]
    s["classroom_capacities"] = {"R001": 30, "R002": 30}
    s["lecturers"] = ["Lect-1"]
    s["years"] = {"Year-1": ["A"]}
    for i, nm in enumerate(("Fizik", "Kimya")):
        c = new_class()
        c["name"] = nm
        c["class_code"] = nm[:3]
        c["lecturer"] = "Lect-1"
        c["duration"] = 1
        c["targets"] = [{"year": "Year-1", "branch": "A"}]
        s["classes"].append(c)
    mark_placed(s["classes"][0], "monday", "09:00", "R001")
    return s


@pytest.fixture
def empty(make_app):
    """A window over a genuinely empty state: no years, classes, days, slots."""
    from scheduler_app.core.models import new_state
    w = make_app()
    w.state_data.clear()
    w.state_data.update(new_state())
    w.refresh_grid()
    return w


@pytest.fixture
def populated(make_app):
    w = make_app()
    w.state_data.clear()
    w.state_data.update(_populated())
    w.refresh_grid()
    return w


# ══════════════════════════════════════════════════════════════════════
#  (a) the empty state
# ══════════════════════════════════════════════════════════════════════

def test_what_the_user_sees_on_a_blank_app(empty, qapp):
    """Documentation — the screenshot equivalent. Always green; prints."""
    w = empty
    for i, name in enumerate(TABS):
        w.notebook.setCurrentIndex(i)
        qapp.processEvents()
        print("\n=== TAB %d %r" % (i, w.notebook.tabText(i)))
        for t in _visible_texts(getattr(w, name)):
            print("    label/button:", t)
        if i < len(TAB_VIEWS):
            view = getattr(w, TAB_VIEWS[i])
            scene = view.scene()
            print("    scene items:", 0 if scene is None else len(scene.items()),
                  "texts:", _scene_texts(view))
    print("\n=== STATUS BAR:", repr(w.status_label.text()))
    print("=== OPEN SLOTS: layout items =", w._open_slots_layout.count(),
          "hint hidden =", w._open_slots_filter_hint.isHidden())
    print("=== UNPLACED  : rows =", w.unplaced_list.count())
    print("=== WARNINGS  :", _visible_texts(w.warning_log))


def test_the_content_area_of_a_blank_app_is_literally_empty(empty, qapp):
    """The measurement behind the finding. Always green; it is the evidence."""
    w = empty
    for i in range(4):
        w.notebook.setCurrentIndex(i)
        qapp.processEvents()
        scene = getattr(w, TAB_VIEWS[i]).scene()
        n = 0 if scene is None else len(scene.items())
        assert n == 0, "tab %d unexpectedly drew %d scene items" % (i, n)
    assert w._open_slots_layout.count() == 0
    assert w.unplaced_list.count() == 0


def test_a_blank_app_says_something_a_full_one_does_not(empty, populated, qapp):
    """ST-UI-020 (a) — RED today.

    The property, stated so that any real empty state satisfies it and no
    rewording of existing chrome does: **somewhere in the window, a blank app
    must show a sentence that a populated app does not.** A CTA label, an
    inline "start by setting up days and hours" block, a placeholder in the
    grid — all pass. Renaming a button does not, because the same button is on
    screen when there is data.

    Twelve characters is the floor: the dashboard already renders "—" for a
    metric it cannot compute, and an em dash is not guidance.
    """
    blank_texts, blank_items = _walk_every_tab(empty, qapp)
    full_texts, full_items = _walk_every_tab(populated, qapp)

    only_when_blank = sorted(set(blank_texts) - set(full_texts))
    print("\nstrings unique to the blank app:", only_when_blank)
    sentences = [t for t in only_when_blank if len(t) >= 12]

    assert sentences, (
        "a blank app shows nothing a populated one does not. Its four "
        "timetable scenes hold %d items in total (the populated app: %d), the "
        "Open-Slots panel holds %d rows and the Unplaced panel %d. The only "
        "strings unique to the blank app are %r — none of them a sentence. "
        "Every piece of guidance the app has is a modal it already closed."
        % (blank_items, full_items, empty._open_slots_layout.count(),
           empty.unplaced_list.count(), only_when_blank))


def test_the_setup_offer_is_made_once_and_never_again(empty, monkeypatch):
    """ST-UI-020 (a), the mechanism — GREEN today; this is the evidence.

    Drives the real ``FirstRunController._step_setup`` twice against a real
    config file. It writes ``initial_setup_prompt_handled`` **before** asking,
    so the second call is silent — and the state is still empty. Nothing
    persistent replaces the offer it withdrew, which is what makes the blank
    canvas above the *normal* screen rather than a first-launch quirk.
    """
    from scheduler_app.ui.first_run import FirstRunController

    asked = []
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: asked.append(a[2] if len(a) > 2 else "")
                     or QMessageBox.StandardButton.No))

    ctl = FirstRunController(empty)
    ctl._step_setup()
    ctl._step_setup()

    assert not empty.state_data["days"], "the fixture stopped being empty"
    assert len(asked) == 1, (
        "expected the offer exactly once over two calls, got %d" % len(asked))
    print("\nafter the one-shot offer, the classroom tab still shows only:",
          _visible_texts(empty.tab_classroom))


# ══════════════════════════════════════════════════════════════════════
#  (b) terminology drift
# ══════════════════════════════════════════════════════════════════════

def _csv_header(tmp_path, state):
    """The header row of the CSV ``data_io`` writes, driven for real."""
    from scheduler_app.data_io.exporter import FinalSchedule, export_schedule

    path = os.path.join(str(tmp_path), "sched.csv")
    export_schedule(FinalSchedule(state), "csv", path)
    with open(path, encoding="utf-8", newline="") as fh:
        return next(csv.reader(fh))


def test_one_lesson_has_one_name_across_the_screens_that_show_it(tmp_path):
    """ST-UI-020 (b) — RED today. Four surfaces, four words, one field.

    Every surface below writes ``cls["name"]`` into the column it heads. They
    are driven, not grepped: the CSV is exported to a real file and read back,
    the workbook headers come from the schema the importer keys on, and the
    dialog headers come from the function the Edit-Classes table uses.
    """
    from scheduler_app.data_io.schema import get_workbook_sheet_header_map
    from scheduler_app.translations import tr
    from scheduler_app.ui.dialogs import _class_io_header_map

    surfaces = {
        # File > Export CSV (ui/app.py::export_csv, the CSV a user gets)
        "app.export_csv": tr("labels.class_item"),
        # data_io CSV writer + the Excel off-grid appendix sheet
        "data_io CSV / xlsx appendix": _csv_header(tmp_path, _populated())[3],
        # the Excel import template + the importer's column map
        "import template": get_workbook_sheet_header_map("classes")[
            "course_name"],
        # Edit Classes table, class-list export, class-list import
        "Edit Classes table": _class_io_header_map()["name"],
        # the clipboard / "everything" text export
        # (``ui/app.py::_copy_to_clipboard``)
        "clipboard text export": tr("labels.session"),
    }

    assert len(set(surfaces.values())) == 1, (
        "the lesson's name column is headed by %d different words across "
        "production surfaces, all of them writing cls['name']: %s"
        % (len(set(surfaces.values())),
           "; ".join("%s -> %r" % kv for kv in sorted(surfaces.items()))))


def test_the_two_columns_of_one_dialog_row_name_the_same_object(tmp_path):
    """ST-UI-020 (b) — RED today, and only in the shipping language.

    ``_CLASS_IO_FIELDS`` puts ``labels.class_code`` and ``labels.class_name``
    side by side in the Edit-Classes table: the code *of* the lesson and the
    name *of* the lesson. English shares the noun ("Class Code" / "Class
    Name"). Turkish does not — "Sınıf Kodu" (Sınıf = classroom/grade) beside
    "Ders Adı" (Ders = lesson) — so the shipping language reads as two
    different objects on one row.
    """
    from scheduler_app.translations import set_language, get_language, tr

    original = get_language()
    failures = []
    try:
        for lang in ("en", "tr"):
            set_language(lang)
            code = tr("labels.class_code")
            name = tr("labels.class_name")
            shared = ({w.casefold() for w in code.split()}
                      & {w.casefold() for w in name.split()})
            if not shared:
                failures.append("%s: %r / %r share no word" % (lang, code, name))
    finally:
        set_language(original)

    assert not failures, (
        "adjacent columns of one Edit-Classes row name the lesson with two "
        "unrelated nouns:\n  %s" % "\n  ".join(failures))


def test_what_test_placement_vocabulary_actually_covers():
    """Documentation — the neighbouring module does NOT pin any of the above.

    Despite its name, ``tests/test_placement_vocabulary.py`` is ST-UI-002: it
    pins that ``schedule_counts``, the status bar and the dashboard card report
    the same *numbers*, that pinned renders as a subset rather than a peer
    segment, and that the annotation follows a language change. It asserts on
    exactly two catalogue strings — ``status.pinned_subset`` and
    ``status.off_grid_subset`` — and on no noun anywhere. Nothing in it would
    notice the four names above.
    """
    import re
    here = os.path.dirname(os.path.abspath(__file__))
    src = open(os.path.join(here, "test_placement_vocabulary.py"),
               encoding="utf-8").read()
    keys = sorted(set(re.findall(r'tr\("([^"]+)"\)', src)))
    assert keys == ["status.off_grid_subset", "status.pinned",
                    "status.pinned_subset"], keys
    for word in ("labels.class_name", "labels.course", "labels.class_item",
                 "labels.session"):
        assert word not in src, word
