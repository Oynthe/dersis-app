"""Export smoke + known-defect pins for ``scheduler_app.data_io.exporter``.

Covers the public entry point ``export_schedule(schedule, format, filepath,
mode=...)`` for all three formats (``xlsx`` / ``csv`` / ``pdf``) and all four
layout modes, plus the four export findings from the stress-test audit:

======================  ==========================================================
ST-FUNC-004 (High)      PDF embeds no Unicode font -> Turkish letters unrenderable
ST-FUNC-005 (High)      xlsx export crashes on Excel-illegal sheet-title chars
ST-FUNC-006 (Medium)    CSV leaks the internal day key (``monday``, not the
                        localized ``Pazartesi``)
ST-FUNC-013 (Low)       PDF silently omits placements that fall outside the
                        grid -- and, in the ``everything`` / ``group`` layouts,
                        any placement with no target group at all
======================  ==========================================================

Every state here is hand-built and fully placed via ``mark_placed`` -- the
optimizer is never invoked, so the whole module is deterministic and fast.

Scope note: **Excel is now one engine.** ST-ARCH-003 (Phase 6) moved the UI's
writer into ``data_io/exporter.py`` and deleted the unused one, so
``export_schedule(state, "xlsx", ...)`` is what a user gets and these tests pin
the real thing. ``mode`` reaches Excel too, which it never did before.

CSV is still two writers: this module's ``_export_csv`` emits the *timetable*
(one row per occupied slot), while ``ui/app.py::export_csv`` emits a *class
list* (one row per class-target, different columns). Those are different
products, not a duplicate, so unifying them would silently change the file a
user has been getting -- see PROGRESS.md.

**Which one does a user reach?** Measured in Phase 7: only the second.
``export_schedule(..., "csv", ...)`` is called nowhere in production -- the
menu at ``ui/app.py:1000`` is wired to ``ui/app.py::export_csv``. So every CSV
assertion in *this* module is a library guard, not a user-facing pin, and
ST-FUNC-006's real pins moved to ``tests/test_export_csv_live.py``, which
drives the writer a user actually gets.

Convention: pins that describe *silent* data loss accept either legitimate fix
-- keep the data, or tell the user it was dropped. Tests that merely read the
CSV day column accept the raw key *and* the localized name, so that fixing
ST-FUNC-006 does not turn unrelated guards red.
"""
import base64
import csv
import re
import warnings
import zlib

import pytest

from scheduler_app.core.models import mark_placed, new_class, new_state
from scheduler_app.data_io.exporter import FinalSchedule, export_schedule
from scheduler_app.translations import tr

MODES = ("everything", "classroom", "group", "lecturer")

# Characters Excel forbids in a worksheet title but which are perfectly legal
# in a lecturer / room / branch name (ST-FUNC-005).
SHEET_TITLE_ILLEGAL_CHARS = ("/", "\\", ":", "?", "*", "[", "]")

# openpyxl's raw, untranslated complaint. Its presence in a user-visible error
# is the ST-FUNC-005 signature.
_OPENPYXL_SHEET_TITLE_ERROR = "found in sheet title"


# ── Optional dependencies ───────────────────────────────────────────────────

@pytest.fixture(scope="module")
def openpyxl_mod():
    """openpyxl, or skip: the xlsx path is an optional-dependency feature."""
    return pytest.importorskip("openpyxl", reason="openpyxl not installed")


@pytest.fixture(scope="module")
def reportlab_mod():
    """reportlab, or skip: the pdf path is an optional-dependency feature."""
    return pytest.importorskip("reportlab", reason="reportlab not installed")


# ── Deterministic fixture data ──────────────────────────────────────────────

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday"]
SLOTS = ["09:00", "10:00", "11:00", "12:00", "13:00"]
ROOMS = ["A-101", "B-202"]
LECT_A = "Ayşe Öğretmen"
LECT_B = "İsmail Çınar"
YEAR_1 = "1. Sınıf"
YEAR_2 = "2. Sınıf"

# (code, name, lecturer, day, slot, room, year, branch, duration)
PLACEMENTS = [
    ("D001", "Türkçe Dilbilgisi", LECT_A, "monday", "09:00", "A-101", YEAR_1, "A", 1),
    ("D002", "İş Sağlığı", LECT_B, "tuesday", "10:00", "B-202", YEAR_1, "B", 2),
    ("D003", "Çağdaş Fizik", LECT_A, "wednesday", "11:00", "A-101", YEAR_2, "A", 1),
    ("D004", "Ölçme ve Değerlendirme", LECT_B, "friday", "09:00", "B-202", YEAR_1, "A", 1),
]

# D002 lasts two slots, so it occupies two (day, slot) cells => two CSV rows.
EXPECTED_CSV_DATA_ROWS = 5


def _empty_state(lecturers=None, rooms=None, years=None):
    """A configured but class-free state (grid, rooms, lecturers, groups)."""
    state = new_state()
    state["days"] = list(DAYS)
    state["slots"] = list(SLOTS)
    state["classrooms"] = list(rooms if rooms is not None else ROOMS)
    state["classroom_capacities"] = {r: 30 for r in state["classrooms"]}
    state["lecturers"] = list(
        lecturers if lecturers is not None else [LECT_A, LECT_B])
    state["years"] = dict(
        years if years is not None else {YEAR_1: ["A", "B"], YEAR_2: ["A"]})
    return state


def _place(state, code, name, lecturer, day, slot, room, year, branch, duration):
    cls = new_class()
    cls["class_code"] = code
    cls["name"] = name
    cls["lecturer"] = lecturer
    cls["targets"] = [{"year": year, "branch": branch}]
    cls["duration"] = duration
    cls["participants"] = 20
    mark_placed(cls, day, slot, room)
    state["classes"].append(cls)
    return cls


def _full_state(n_classes=None):
    """A small, fully-placed, entirely deterministic schedule."""
    state = _empty_state()
    for spec in PLACEMENTS[:n_classes]:
        _place(state, *spec)
    return state


@pytest.fixture
def state():
    return _full_state()


# ── PDF introspection helpers ───────────────────────────────────────────────
#
# No PDF parser (pypdf/pdfminer/fitz) is installed in the audit venv, so these
# decode reportlab's output directly. reportlab writes content streams through
# ``/Filter [/ASCII85Decode /FlateDecode]``; undoing both gives the raw page
# operators, in which drawn text appears as literal ``(...)`` strings.

_STREAM_RE = re.compile(rb"stream\r?\n(.*?)endstream", re.DOTALL)
_BASEFONT_RE = re.compile(rb"/BaseFont\s*/([A-Za-z0-9+#\-,]+)")
_FONTFILE_RE = re.compile(rb"/FontFile\d?")
_OBJ_RE = re.compile(rb"(\d+)\s+0\s+obj\b(.*?)\bendobj", re.DOTALL)
_FONT_RES_NAME_RE = re.compile(rb"/Name\s*/([A-Za-z0-9+#\-]+)")
_TF_RE = re.compile(rb"/([A-Za-z0-9+#\-]+)\s+[\d.]+\s+Tf")
_BFCHAR_RE = re.compile(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]{4,})>")


def _decode_pdf_stream(body):
    data = body.strip()
    if data.endswith(b"~>"):
        try:
            data = base64.a85decode(data, adobe=True)
        except ValueError:
            return body
    try:
        return zlib.decompress(data)
    except zlib.error:
        return data


def pdf_content_text(raw):
    """Return every decoded content stream of *raw* concatenated."""
    return b"\n".join(_decode_pdf_stream(b) for b in _STREAM_RE.findall(raw))


def pdf_base_fonts(raw):
    """The set of ``/BaseFont`` names the PDF references."""
    return {m.decode("latin-1") for m in _BASEFONT_RE.findall(raw)}


def pdf_embedded_font_programs(raw):
    """The ``/FontFile*`` keys present, i.e. actually embedded font programs."""
    return [m.decode("latin-1") for m in _FONTFILE_RE.findall(raw)]


def pdf_zapfdingbats_runs(raw):
    """Font resources bound to ZapfDingbats that a content stream selects.

    reportlab's response to a codepoint the current font cannot encode is not
    the missing-glyph box the audit register described. It splits the paragraph
    at that character and switches to ZapfDingbats, in which it draws the ASCII
    letter ``n`` -- a filled block. So the page shows a solid blob mid-word
    (which reads as redaction, not as a font problem) *and* the text layer is
    falsified: Ctrl-F for "Öğretmen" finds nothing, copy-paste yields
    "Önretmen".

    This is the assertion ``pdf_embedded_font_programs`` cannot make. An
    exporter that embedded a Unicode font for some styles while leaving others
    on Helvetica would satisfy that check and still substitute here.
    """
    dingbats = set()
    for _num, body in _OBJ_RE.findall(raw):
        if b"/BaseFont" in body and b"ZapfDingbats" in body:
            dingbats.update(m.decode("latin-1")
                            for m in _FONT_RES_NAME_RE.findall(body))
    selected = {m.decode("latin-1")
                for m in _TF_RE.findall(pdf_content_text(raw))}
    return dingbats & selected


def pdf_text_layer_codepoints(raw):
    """Unicode codepoints the PDF's ``/ToUnicode`` CMaps make recoverable.

    An embedded TrueType face is subsetted, so a non-ASCII character is drawn
    as a subset index rather than as its own bytes -- searching the content
    stream for "ğ" proves nothing either way. The CMap is what a reader, a
    Ctrl-F, or a copy-paste actually resolves the glyph through, so it is the
    honest place to assert that the letter survived into the document.
    """
    found = set()
    for blob in (_decode_pdf_stream(b) for b in _STREAM_RE.findall(raw)):
        if b"beginbfchar" not in blob:
            continue
        for _src, dst in _BFCHAR_RE.findall(blob):
            found.add(int(dst[:4], 16))
    return found


def assert_well_formed_pdf(path, min_size=1000):
    """A genuine structural check, not an ``os.path.exists`` rubber stamp."""
    raw = path.read_bytes()
    assert len(raw) >= min_size, f"{path.name} is implausibly small ({len(raw)} B)"
    assert raw.startswith(b"%PDF-"), f"{path.name} lacks the %PDF- header"
    assert raw.rstrip().endswith(b"%%EOF"), f"{path.name} lacks the %%EOF trailer"
    return raw


def read_csv_rows(path):
    """Parse the exported CSV through the stdlib csv module."""
    # utf-8-sig so a future BOM (the ST-FUNC-006 fix) does not break the parse.
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.reader(fh))


def xlsx_cell_texts(worksheet):
    """Every non-empty cell of *worksheet* rendered as text."""
    return [str(c.value) for row in worksheet.iter_rows()
            for c in row if c.value is not None]


def xlsx_workbook_texts(wb):
    """Every non-empty cell of every sheet, as text.

    ST-ARCH-003. Assertions used to read ``wb.sheetnames[0]`` because the dead
    exporter always wrote a single master sheet first. The engine a user
    actually gets writes one sheet per year / room / group / lecturer
    depending on ``mode``, so "is it in the workbook" has to mean the workbook.
    """
    return [t for ws in wb.worksheets for t in xlsx_cell_texts(ws)]


def accepted_day_cell_values(day_key):
    """Both spellings a CSV day cell may legitimately carry for *day_key*.

    ST-FUNC-006 is pinned separately (``test_csv_day_column_is_localized``).
    Every *other* test that happens to look at the day column must accept the
    localized form too, otherwise this module would go red the moment that
    finding is fixed -- i.e. it would be pinning the bug in place.
    """
    return {day_key, tr(f"weekdays.{day_key}")}


def ascii_day_headers():
    """Localized weekday labels findable as literal bytes in a content stream.

    Still only the ASCII ones, but for a different reason since ST-FUNC-004 was
    fixed: the PDF now embeds a *subsetted* TrueType face, in which ASCII keeps
    its own code while "Salı" and "Çarşamba" are drawn as subset indices. Their
    survival is asserted through the ``/ToUnicode`` CMap instead -- see
    ``pdf_text_layer_codepoints`` and
    ``test_pdf_text_layer_keeps_every_turkish_letter``.
    """
    return [tr(f"weekdays.{d}") for d in DAYS
            if tr(f"weekdays.{d}").isascii()]


# ══════════════════════════════════════════════════════════════════════════
#  1. Smoke ×3 — the roadmap's Phase 0 requirement
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.excel
def test_xlsx_export_is_a_readable_workbook(state, tmp_path, openpyxl_mod):
    """Guards the xlsx export smoke path (roadmap Phase 0, ST-ARCH-001).

    A failure means the user's "Export to Excel" produces a file Excel cannot
    open, or one that has silently lost the timetable content.
    """
    out = tmp_path / "schedule.xlsx"
    export_schedule(state, "xlsx", str(out))

    assert out.exists()
    assert out.stat().st_size > 4000, "workbook is implausibly small"

    wb = openpyxl_mod.load_workbook(out)
    # ST-ARCH-003: the default mode is the everything-matrix, one sheet per
    # year, named for the year and localized. The `T_`/`R_`/`B_` prefixed
    # sheets this used to assert belonged to the exporter's own Excel writer,
    # which had no production caller and has been deleted.
    assert wb.sheetnames == [YEAR_1, YEAR_2], wb.sheetnames

    # Whatever the layout, nothing the user placed may be missing from it.
    texts = xlsx_workbook_texts(wb)
    for _code, name, *_rest in PLACEMENTS:
        assert any(name in t for t in texts), (
            f"course {name!r} never made it into the workbook")
    assert any(LECT_A in t for t in texts), "lecturer missing from the workbook"
    assert any("A-101" in t for t in texts), "room missing from the workbook"
    # The grid the user configured is drawn: every slot and every day appears.
    for slot in SLOTS:
        assert any(slot in t for t in texts), f"slot {slot} missing"
    for day in DAYS:
        assert any(tr(f"weekdays.{day}") in t for t in texts), (
            f"day {day} missing")


def test_csv_export_parses_with_expected_shape(state, tmp_path):
    """Guards the CSV export smoke path (roadmap Phase 0, ST-ARCH-001).

    A failure means the exported CSV is unparseable or has dropped/duplicated
    lesson rows, so any downstream tool reading it sees the wrong timetable.
    """
    out = tmp_path / "schedule.csv"
    export_schedule(state, "csv", str(out))

    rows = read_csv_rows(out)
    assert rows, "CSV is empty"

    header = rows[0]
    assert header == [
        tr("labels.day"), tr("labels.time"), tr("labels.class_code"),
        tr("labels.course"), tr("labels.lecturer"), tr("labels.classroom"),
        tr("labels.year"), tr("labels.branch"),
    ]

    data = rows[1:]
    assert len(data) == EXPECTED_CSV_DATA_ROWS, \
        f"expected {EXPECTED_CSV_DATA_ROWS} lesson rows, got {len(data)}"
    assert all(len(r) == len(header) for r in data), "ragged CSV rows"

    by_code = {}
    for row in data:
        by_code.setdefault(row[2], []).append(row)
    assert sorted(by_code) == ["D001", "D002", "D003", "D004"]
    # A two-slot class occupies two cells and therefore emits two rows.
    assert len(by_code["D002"]) == 2
    assert {r[1] for r in by_code["D002"]} == {"10:00", "11:00"}

    d001 = by_code["D001"][0]
    assert d001[1:] == ["09:00", "D001", "Türkçe Dilbilgisi", LECT_A,
                        "A-101", YEAR_1, "A"]
    # The day column is asserted loosely on purpose: its *spelling* is the
    # ST-FUNC-006 pin below, but the value must still identify Monday.
    assert d001[0] in accepted_day_cell_values("monday"), \
        f"D001's day column says {d001[0]!r}, which is not Monday at all"


@pytest.mark.pdf
def test_pdf_export_is_a_well_formed_document(state, tmp_path, reportlab_mod):
    """Guards the PDF export smoke path (roadmap Phase 0, ST-ARCH-001).

    A failure means the printed timetable -- the artifact teachers actually
    post on the wall -- is a truncated or corrupt PDF, or has lost lessons.
    """
    out = tmp_path / "schedule.pdf"
    export_schedule(state, "pdf", str(out))

    raw = assert_well_formed_pdf(out)
    content = pdf_content_text(raw)
    assert content, "no decodable content stream in the PDF"

    # Control for the ST-FUNC-013 pin below: on-grid placements DO get drawn.
    for code, *_rest in PLACEMENTS:
        assert code.encode() in content, \
            f"class code {code} was not drawn into the PDF"


# ══════════════════════════════════════════════════════════════════════════
#  2. All modes + degenerate states
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.pdf
@pytest.mark.parametrize("mode", MODES)
def test_pdf_export_every_mode(state, tmp_path, reportlab_mod, mode):
    """Guards all four PDF layout modes (ST-ARCH-001 regression net).

    A failure means one of the four "Export PDF" layouts a user can pick from
    the dialog produces a broken or empty document.
    """
    out = tmp_path / f"{mode}.pdf"
    export_schedule(state, "pdf", str(out), mode=mode)

    raw = assert_well_formed_pdf(out)
    content = pdf_content_text(raw)
    for code, *_rest in PLACEMENTS:
        assert code.encode() in content, f"{code} missing from mode={mode}"


@pytest.mark.excel
@pytest.mark.parametrize("mode", MODES)
def test_xlsx_export_every_mode(state, tmp_path, openpyxl_mod, mode):
    """Guards that every export mode is at least accepted by the xlsx path.

    A failure means picking a layout mode in the export dialog crashes the
    Excel export. (The xlsx path currently ignores ``mode`` -- see the module
    docstring's ST-ARCH-003 note -- so this asserts acceptance, not layout.)
    """
    out = tmp_path / f"{mode}.xlsx"
    export_schedule(state, "xlsx", str(out), mode=mode)

    wb = openpyxl_mod.load_workbook(out)
    texts = xlsx_cell_texts(wb[wb.sheetnames[0]])
    assert any("Türkçe Dilbilgisi" in t for t in texts)


@pytest.mark.parametrize("fmt,mode", (
    [("csv", "everything"), ("xlsx", "everything")]
    + [("pdf", m) for m in MODES]
))
def test_export_with_zero_placed_classes(tmp_path, fmt, mode, request):
    """Degenerate case: a configured grid with nothing scheduled yet.

    A failure means a brand-new project (rooms and groups entered, optimizer
    not yet run) crashes the exporter, or exports a blank page instead of the
    empty timetable grid the user configured.
    """
    openpyxl_mod = (request.getfixturevalue("openpyxl_mod")
                    if fmt == "xlsx" else None)
    if fmt == "pdf":
        request.getfixturevalue("reportlab_mod")

    out = tmp_path / f"empty.{fmt}"
    export_schedule(_empty_state(), fmt, str(out), mode=mode)

    assert out.exists() and out.stat().st_size > 0
    if fmt == "csv":
        rows = read_csv_rows(out)
        assert len(rows) == 1, "header-only CSV expected"
        assert rows[0][0] == tr("labels.day")
    elif fmt == "xlsx":
        wb = openpyxl_mod.load_workbook(out)
        # The configured grid must still be drawn, not just an empty sheet.
        # ST-ARCH-003: asserted across the workbook rather than against a
        # master sheet the surviving engine does not produce.
        texts = xlsx_workbook_texts(wb)
        for slot in SLOTS:
            assert any(slot in t for t in texts),                 f"slot {slot} missing from the empty workbook"
        for day in DAYS:
            assert any(tr(f"weekdays.{day}") in t for t in texts),                 f"day {day} missing from the empty workbook"
    else:
        content = pdf_content_text(assert_well_formed_pdf(out))
        # Same requirement for the PDF: the empty grid, with its slot ruler and
        # day headers, is what the user asked to print.
        for slot in SLOTS:
            assert slot.encode() in content, \
                f"slot {slot} missing from the empty mode={mode} PDF"
        for label in ascii_day_headers():
            assert label.encode() in content, \
                f"day header {label!r} missing from the empty mode={mode} PDF"


@pytest.mark.parametrize("fmt", ("csv", "xlsx", "pdf"))
def test_export_with_a_single_class(tmp_path, fmt, request):
    """Degenerate case: exactly one placed lesson.

    A failure means the smallest real schedule a user can build cannot be
    exported -- typically an off-by-one in grid/merge construction.
    """
    openpyxl_mod = (request.getfixturevalue("openpyxl_mod")
                    if fmt == "xlsx" else None)
    if fmt == "pdf":
        request.getfixturevalue("reportlab_mod")

    out = tmp_path / f"one.{fmt}"
    export_schedule(_full_state(n_classes=1), fmt, str(out))

    if fmt == "csv":
        rows = read_csv_rows(out)
        assert len(rows) == 2
        assert rows[1][2] == "D001"
    elif fmt == "xlsx":
        wb = openpyxl_mod.load_workbook(out)
        texts = xlsx_cell_texts(wb[wb.sheetnames[0]])
        assert any("Türkçe Dilbilgisi" in t for t in texts)
    else:
        raw = assert_well_formed_pdf(out)
        assert b"D001" in pdf_content_text(raw)


def _state_with_a_group_less_class():
    """A normal schedule plus one placed lesson that targets no group.

    ``new_class()`` initializes ``targets`` to ``[]``, so this is not exotic:
    it is any lesson placed before its class groups were assigned.
    """
    state = _full_state()
    cls = _place(state, "D900", "Serbest Ders", LECT_A, "thursday", "09:00",
                 ROOMS[0], YEAR_1, "A", 1)
    cls["targets"] = []
    return state


@pytest.mark.parametrize("fmt", ("csv", "xlsx"))
def test_csv_and_xlsx_keep_a_class_with_no_target_groups(tmp_path, fmt, request):
    """Degenerate case: a placed lesson that targets no year/branch group.

    A failure means a lesson the user placed before assigning it to a class
    group crashes the export or disappears from it. (The CSV writer's
    otherwise-untested ``else`` branch, exporter.py:390.)
    """
    if fmt == "xlsx":
        openpyxl_mod = request.getfixturevalue("openpyxl_mod")

    out = tmp_path / f"notargets.{fmt}"
    export_schedule(_state_with_a_group_less_class(), fmt, str(out))

    if fmt == "csv":
        rows = read_csv_rows(out)
        data = [r for r in rows[1:] if r[2] == "D900"]
        assert len(data) == 1, "the group-less lesson was dropped from the CSV"
        # Blank year/branch, but every other column intact.
        assert data[0][3:] == ["Serbest Ders", LECT_A, ROOMS[0], "", ""]
    else:
        wb = openpyxl_mod.load_workbook(out)
        # ST-ARCH-003: across the workbook. The everything-matrix has no column
        # for a lesson with no target group, so the unified engine reports it
        # on its own sheet rather than dropping it -- the ST-FUNC-013 rule.
        texts = xlsx_workbook_texts(wb)
        assert any("Serbest Ders" in t for t in texts), \
            "the group-less lesson was dropped from the workbook"


_GROUPLESS_PDF_DROP = pytest.mark.xfail(
    strict=True,
    reason="ST-FUNC-013 (new instance) — the 'everything' and 'group' PDF "
           "pages are built by filtering on each class's targets "
           "(exporter.py:831-866), so a placed class whose targets list is "
           "empty (the new_class() default) matches no page and is dropped "
           "with no warning; same 'silently omits' family as the off-grid case",
)


@pytest.mark.pdf
@pytest.mark.parametrize("mode", [
    pytest.param("everything", marks=_GROUPLESS_PDF_DROP),
    "classroom",
    pytest.param("group", marks=_GROUPLESS_PDF_DROP),
    "lecturer",
])
def test_pdf_keeps_a_class_with_no_target_groups(tmp_path, reportlab_mod, mode):
    """ST-FUNC-013 (new instance) — a group-less lesson must not vanish.

    A failure means a lesson the user placed but has not yet assigned to a
    class group is simply absent from the printed timetable, with nothing
    saying so -- the same silent data loss as the off-grid case below, but
    reachable from the default state of every freshly created class.
    """
    out = tmp_path / f"notargets_{mode}.pdf"

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        export_schedule(_state_with_a_group_less_class(), "pdf", str(out),
                        mode=mode)

    content = pdf_content_text(assert_well_formed_pdf(out))
    assert b"D001" in content, "control class D001 missing — test is broken"

    warned = any("D900" in str(w.message) for w in caught)
    assert b"D900" in content or warned, (
        f"the group-less lesson D900 is absent from the mode={mode} PDF and "
        "no warning was raised about it"
    )


def test_export_schedule_accepts_a_finalschedule_wrapper(state, tmp_path):
    """Guards the documented ``FinalSchedule`` entry point alongside raw dicts.

    A failure means callers that wrap their state (as the signature invites)
    get a different result from callers that pass the dict directly.
    """
    from_dict = tmp_path / "dict.csv"
    from_wrapper = tmp_path / "wrapper.csv"
    export_schedule(state, "csv", str(from_dict))
    export_schedule(FinalSchedule(state), "csv", str(from_wrapper))
    # Sanity first: two identically-empty files would also compare equal.
    assert len(read_csv_rows(from_dict)) == EXPECTED_CSV_DATA_ROWS + 1
    assert from_dict.read_bytes() == from_wrapper.read_bytes()


def test_unsupported_format_raises_a_translated_error(state, tmp_path):
    """Guards the format-dispatch guard in ``export_schedule``.

    A failure means a typo'd format silently writes nothing, or surfaces an
    untranslated developer string to the user.
    """
    with pytest.raises(ValueError) as excinfo:
        export_schedule(state, "docx", str(tmp_path / "x.docx"))
    assert "docx" in str(excinfo.value)
    assert str(excinfo.value) != "errors.unsupported_format"


# ══════════════════════════════════════════════════════════════════════════
#  3. ST-FUNC-005 — xlsx crashes on Excel-illegal sheet-title characters
# ══════════════════════════════════════════════════════════════════════════

def _state_with_illegal_name(field, char):
    """Build a state whose lecturer / room / branch name contains *char*."""
    if field == "lecturer":
        lecturer = f"Dr. Ayşe {char} Yılmaz"
        state = _empty_state(lecturers=[lecturer])
        _place(state, "D001", "Ders", lecturer, "monday", "09:00",
               ROOMS[0], YEAR_1, "A", 1)
    elif field == "room":
        room = f"Lab {char} 1"
        state = _empty_state(rooms=[room])
        _place(state, "D001", "Ders", LECT_A, "monday", "09:00",
               room, YEAR_1, "A", 1)
    elif field == "branch":
        branch = f"A{char}B"
        state = _empty_state(years={YEAR_1: [branch]})
        _place(state, "D001", "Ders", LECT_A, "monday", "09:00",
               ROOMS[0], YEAR_1, branch, 1)
    else:  # pragma: no cover - guard against a typo in the parametrization
        raise AssertionError(f"unknown field {field!r}")
    return state


# ST-FUNC-005 is CLOSED, and closed by deletion rather than by a fix.
#
# The 11 xfail(strict=True) pins that used to sit here all went XPASS the moment
# ST-ARCH-003 unified the two Excel writers, because the crash never existed in
# the engine a user could reach. `data_io/exporter.py` built sheet titles
# straight from the name; `ui/app.py`'s writer -- the one the Excel menu
# actually called -- has routed every title through `_sheet_name_for_export`,
# which strips `[]:*?/\` and truncates to 31, since before the audit. The audit
# attributed the crash to the export the user runs (02-functional-inventory.md
# "F2 Export Excel"), and it was in the copy with no callers.
#
# So the finding as written was never reachable in production. Deleting the dead
# writer is what removed it. The parametrization stays as a live regression
# guard on the surviving engine.
@pytest.mark.excel
@pytest.mark.parametrize("field,char", (
    [("lecturer", c) for c in SHEET_TITLE_ILLEGAL_CHARS]
    + [("room", "/"), ("room", "["), ("branch", "/"), ("branch", ":")]
))
def test_xlsx_export_survives_illegal_sheet_title_chars(
        tmp_path, openpyxl_mod, field, char):
    """ST-FUNC-005 — names containing ``/ \\ : ? * [ ]`` must not break export.

    A failure means a school whose room is called "Lab / 1" or whose teacher is
    "Dr. Ayşe [Fen]" simply cannot export to Excel: the app dies on openpyxl's
    raw ``ValueError`` with no explanation and no file.

    Passes since ST-ARCH-003 deleted the writer that had the bug; see the note
    above. Kept because the surviving engine's sanitisation is one helper, and
    a refactor that bypasses ``_sheet_name_for_export`` would reintroduce the
    crash on the path a user can actually reach.
    """
    state = _state_with_illegal_name(field, char)
    out = tmp_path / "illegal.xlsx"

    error = None
    try:
        export_schedule(state, "xlsx", str(out))
    except Exception as exc:  # noqa: BLE001 - the point is to inspect it
        error = exc

    if error is not None:
        # A clear, user-facing refusal is an acceptable fix; leaking
        # openpyxl's internal sheet-title ValueError is not.
        assert _OPENPYXL_SHEET_TITLE_ERROR not in str(error), (
            f"xlsx export leaked openpyxl's sheet-title error for "
            f"{field}={char!r}: {error!r}"
        )
        return

    wb = openpyxl_mod.load_workbook(out)
    assert wb.sheetnames, "export produced a workbook with no sheets"
    texts = xlsx_cell_texts(wb[wb.sheetnames[0]])
    assert any("Ders" in t for t in texts)


@pytest.mark.parametrize("fmt", ("csv", "pdf"))
def test_csv_and_pdf_tolerate_illegal_sheet_title_chars(tmp_path, fmt, request):
    """Regression guard scoping ST-FUNC-005 to the xlsx path only.

    A failure means the sheet-title bug has spread: the CSV/PDF exports would
    start rejecting names that only Excel's worksheet naming rules dislike.
    """
    if fmt == "pdf":
        request.getfixturevalue("reportlab_mod")

    state = _state_with_illegal_name("lecturer", "[")
    lecturer = state["lecturers"][0]
    out = tmp_path / f"illegal.{fmt}"
    export_schedule(state, fmt, str(out))

    if fmt == "csv":
        rows = read_csv_rows(out)
        data = [r for r in rows[1:] if r[2] == "D001"]
        assert data, "the lesson vanished from the CSV"
        # The bracketed name must survive verbatim, not be silently scrubbed.
        assert data[0][4] == lecturer, \
            f"lecturer column says {data[0][4]!r}, expected {lecturer!r}"
    else:
        content = pdf_content_text(assert_well_formed_pdf(out))
        assert b"D001" in content, "the lesson vanished from the PDF"


# ══════════════════════════════════════════════════════════════════════════
#  4. ST-FUNC-006 — CSV day column + encoding
# ══════════════════════════════════════════════════════════════════════════

def test_csv_day_column_is_localized(state, tmp_path):
    """ST-FUNC-006 — the CSV day column must read "Pazartesi", not "monday".

    A failure means the exported CSV a school hands to a colleague is written
    half in Turkish (the headers) and half in English programmer keys (the day
    column), so it cannot be read or re-imported without hand-editing.

    **This is not the pin any more.** Measured in Phase 7:
    ``export_schedule(..., "csv", ...)`` has no production caller, so a green
    mark here proves nothing about the file a user gets -- exactly the shape of
    ST-ARCH-003, one format later. The user-facing pin now drives the live
    writer and lives in ``tests/test_export_csv_live.py``. This one stays as a
    guard on the library entry point so the two writers cannot drift apart.
    """
    out = tmp_path / "days.csv"
    export_schedule(state, "csv", str(out))

    rows = read_csv_rows(out)
    days_seen = {r[0] for r in rows[1:]}
    localized = {tr(f"weekdays.{d}") for d in DAYS}

    assert days_seen <= localized, (
        f"CSV day column leaked internal keys: "
        f"{sorted(days_seen - localized)} (expected e.g. "
        f"{tr('weekdays.monday')!r})"
    )


def test_csv_is_utf8_with_turkish_characters_intact(state, tmp_path):
    """ST-FUNC-006 (encoding half) — regression guard on the exporter's CSV.

    A failure means Turkish names come back mangled or the file cannot be
    decoded at all.

    Same caveat as the test above: the ``UnicodeEncodeError`` the audit
    recorded is only reachable through ``ui/app.py::export_csv``, which is a
    different function and was never covered here. It is now, in
    ``tests/test_export_csv_live.py``.
    """
    out = tmp_path / "utf8.csv"
    export_schedule(state, "csv", str(out))

    raw = out.read_bytes()
    # Strict decode: any locale-codepage bytes would raise here.
    text = raw.decode("utf-8")
    if text.startswith("﻿"):  # tolerate a future BOM
        text = text[1:]

    # The header needle is taken from the translation table, not hardcoded, so
    # a wording change in tr() cannot fail this test for the wrong reason.
    for needle in (tr("labels.lecturer"), "Türkçe Dilbilgisi", "İş Sağlığı",
                   "Ölçme ve Değerlendirme", LECT_A, LECT_B, YEAR_1):
        assert needle in text, f"{needle!r} did not survive the CSV round-trip"
    assert any(not c.isascii() for c in text), \
        "no non-ASCII character in the CSV at all — the test data is broken"


# ══════════════════════════════════════════════════════════════════════════
#  5. ST-FUNC-004 — PDF cannot render Turkish letters
# ══════════════════════════════════════════════════════════════════════════

# Every Turkish-specific letter. Measured in Phase 7: only six of them ever
# broke -- ö ü ç Ö Ü Ç are WinAnsi codepoints and always drew correctly under
# Helvetica, so the register's "every Turkish-specific letter (ş ğ İ ı ö ü ç)"
# was wrong about half the list. All twelve are asserted anyway: the fix must
# not regress the six that worked.
TURKISH_LETTERS = "ğĞşŞıİöÖüÜçÇ"
TURKISH_BROKEN_UNDER_HELVETICA = "ğĞşŞıİ"


def _state_with_every_turkish_letter():
    """A one-lesson schedule whose text carries all twelve Turkish letters."""
    lecturer = "Şükrü Işık Öğretmen"
    state = _empty_state(lecturers=[lecturer], rooms=["A-101"],
                         years={"1. Sınıf": ["A"]})
    _place(state, "D001",
           "İŞ SAĞLIĞI ÜNİTESİ: Ölçme, gözlem, Değerlendirme, Çalıştay",
           lecturer, "monday", "09:00", "A-101", "1. Sınıf", "A", 1)
    blob = "".join(
        str(v) for c in state["classes"]
        for v in (c["name"], c["lecturer"])) + "1. Sınıf"
    missing = [ch for ch in TURKISH_LETTERS if ch not in blob]
    assert not missing, f"fixture text lacks {missing} — the test is broken"
    return state


@pytest.mark.pdf
def test_pdf_embeds_a_unicode_capable_font(state, tmp_path, reportlab_mod):
    """ST-FUNC-004 — the PDF must embed a font that can draw ğ Ğ ş Ş ı İ.

    A failure means every Turkish-specific letter in the printed timetable is
    a box or a wrong glyph: "Öğretmen", "Çarşamba" and "1. Sınıf" are
    unreadable on the schedule teachers actually post.

    Asserting on rendered glyphs is not possible without a rasterizer, so this
    asserts the necessary precondition instead: a base-14 Type1 font can never
    carry those glyphs, therefore a correct export must embed a font program
    (reportlab emits ``/FontFile2`` for a registered ``TTFont``). Corroborating
    evidence for the current state: ``pdfmetrics.stringWidth`` under Helvetica
    returns an identical 7.61 for ş, ğ, İ and ı -- one shared substitute glyph.
    """
    out = tmp_path / "turkish.pdf"
    export_schedule(state, "pdf", str(out))

    raw = assert_well_formed_pdf(out)
    embedded = pdf_embedded_font_programs(raw)

    assert embedded, (
        "PDF embeds no font program at all; it references only "
        f"{sorted(pdf_base_fonts(raw))}, none of which carry Turkish glyphs"
    )


@pytest.mark.pdf
@pytest.mark.parametrize("mode", MODES)
def test_pdf_text_layer_has_no_zapfdingbats_substitution(
        tmp_path, reportlab_mod, mode):
    """ST-FUNC-004 — no Turkish letter may fall through to ZapfDingbats.

    A failure means the printed timetable draws a filled black block in the
    middle of a teacher's name and writes the wrong character into the text
    layer behind it, so the archived PDF cannot be searched for that name.

    This is the half ``test_pdf_embeds_a_unicode_capable_font`` cannot see: it
    only asks whether *some* font program is embedded, so an export that
    embedded one font and left another style on Helvetica would satisfy it
    while still substituting. Parametrized over all four modes because each
    builds its own table, and the ``everything`` layout is the only one that
    uses ``session_style``.
    """
    out = tmp_path / f"dingbats_{mode}.pdf"
    export_schedule(_state_with_every_turkish_letter(), "pdf", str(out),
                    mode=mode)

    raw = assert_well_formed_pdf(out)
    runs = pdf_zapfdingbats_runs(raw)
    assert not runs, (
        f"mode={mode}: the content stream selects ZapfDingbats resource(s) "
        f"{sorted(runs)}, i.e. reportlab could not encode a character and "
        f"substituted a filled block for it; base fonts are "
        f"{sorted(pdf_base_fonts(raw))}"
    )


@pytest.mark.pdf
def test_pdf_text_layer_keeps_every_turkish_letter(tmp_path, reportlab_mod):
    """ST-FUNC-004 — the twelve Turkish letters must be recoverable from the PDF.

    A failure means a school that archives its printed timetables cannot find
    "Öğretmen" in them with Ctrl-F, and copy-paste out of the PDF yields
    mangled names. Measured before the fix: copying "Şükrü Işık Öğretmen"
    produced "Önretmen" for the last word.
    """
    out = tmp_path / "textlayer.pdf"
    export_schedule(_state_with_every_turkish_letter(), "pdf", str(out),
                    mode="everything")

    raw = assert_well_formed_pdf(out)
    recoverable = pdf_text_layer_codepoints(raw)
    missing = [ch for ch in TURKISH_LETTERS if ord(ch) not in recoverable]
    assert not missing, (
        f"these letters have no /ToUnicode mapping in the exported PDF and so "
        f"cannot be searched or copied out of it: {missing}"
    )


@pytest.mark.pdf
def test_pdf_export_falls_back_to_helvetica_when_the_font_file_is_missing(
        tmp_path, reportlab_mod, monkeypatch):
    """ST-FUNC-004 — a build without reportlab's fonts must degrade, not crash.

    A failure means the Unicode-font fix turned a cosmetic defect into a dead
    export button on any build where ``reportlab/fonts/Vera.ttf`` did not ship.
    That is not hypothetical: ``requirements-lock.txt`` pins reportlab 4.4.10
    while the venv this suite runs in has 5.0.1, and ``Dersis-mac.spec`` does
    not collect reportlab's package data at all.
    """
    from scheduler_app.data_io import exporter

    with monkeypatch.context() as m:
        m.setattr(exporter, "_pdf_font_names", None)
        m.setattr(exporter.os.path, "exists", lambda _p: False)
        assert exporter._register_unicode_fonts() == ("Helvetica",
                                                      "Helvetica-Bold"), \
            "a missing font file must resolve to the base-14 fallback"

    # And the export must still produce a document with that fallback in force.
    monkeypatch.setattr(exporter, "_pdf_font_names", ("Helvetica",
                                                      "Helvetica-Bold"))
    out = tmp_path / "fallback.pdf"
    export_schedule(_state_with_every_turkish_letter(), "pdf", str(out))
    content = pdf_content_text(assert_well_formed_pdf(out))
    assert b"D001" in content, "the fallback export drew no lessons at all"


def _state_with_a_crowded_cell():
    """A schedule whose longest lesson overflows a default-height PDF row."""
    lecturer = "Şükrü Işık Öğretmen"
    state = _empty_state(lecturers=[lecturer, LECT_A], rooms=["A-101"],
                         years={"1. Sınıf": ["A", "B"]})
    _place(state, "D001",
           "Öğrenci Değerlendirme ve Ölçme Çalıştayı: İş Sağlığı ve "
           "Güvenliği Uygulamaları",
           lecturer, "monday", "09:00", "A-101", "1. Sınıf", "A", 1)
    _place(state, "D002", "Kontrol", LECT_A, "tuesday", "10:00", "A-101",
           "1. Sınıf", "B", 1)
    return state


@pytest.mark.pdf
@pytest.mark.parametrize("mode", MODES)
def test_pdf_rows_are_tall_enough_for_the_cells_they_hold(
        tmp_path, reportlab_mod, monkeypatch, mode):
    """A fixed-height PDF row must fit the paragraph drawn into it.

    A failure means an hour of the printed timetable is written over the hours
    above and below it: ``rowHeights`` is fixed and reportlab does not grow a
    fixed row to fit its content, it overprints the neighbours. The
    contested-cell branch has measured itself for exactly this reason since
    ST-UI-001; the ordinary occupied cell did not, and measurement says it
    should have -- 51pt of content in a 50pt row under Helvetica, which
    ST-FUNC-004's embedded (wider) face pushed to 60pt.

    Asserts a relation between two quantities measured in the same process --
    what reportlab says the paragraph needs against what the table allots it --
    never an absolute point count.
    """
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Table

    captured = []
    real_build = SimpleDocTemplate.build

    def spy(self, flowables, *args, **kwargs):
        captured.extend(f for f in flowables if isinstance(f, Table))
        return real_build(self, flowables, *args, **kwargs)

    monkeypatch.setattr(SimpleDocTemplate, "build", spy)
    export_schedule(_state_with_a_crowded_cell(), "pdf",
                    str(tmp_path / f"rows_{mode}.pdf"), mode=mode)

    assert captured, "no Table reached doc.build() -- the spy is broken"

    checked = 0
    for tbl in captured:
        heights, widths = tbl._argH, tbl._argW
        if any(h is None for h in heights):
            # Auto-sized (the appendix). reportlab grows those itself.
            continue
        spans = {(c0, r0): r1 - r0 + 1
                 for _cmd, (c0, r0), (_c1, r1) in tbl._spanCmds}
        for r, row in enumerate(tbl._cellvalues):
            if r < tbl.repeatRows:
                continue  # header rows: short labels at their own fixed height
            for c, cell in enumerate(row):
                if not isinstance(cell, Paragraph):
                    continue
                # LEFTPADDING + RIGHTPADDING = 4, TOP + BOTTOM = 6.
                _w, needed = cell.wrap(widths[c] - 4, 1e6)
                allotted = sum(heights[r:r + spans.get((c, r), 1)])
                assert needed + 6 <= allotted + 0.01, (
                    f"mode={mode}: row {r} column {c} is {allotted:.1f}pt tall "
                    f"but the cell drawn in it needs {needed + 6:.1f}pt, so it "
                    f"overprints the hours above and below it"
                )
                checked += 1
    assert checked, "no fixed-height paragraph cell was checked"


@pytest.mark.pdf
def test_pdf_export_does_not_crash_on_turkish_text(tmp_path, reportlab_mod):
    """Regression guard for the ST-FUNC-004 fix: no crash on Turkish input.

    A failure means the Unicode-font fix (or any change to PDF styling) has
    made the export blow up on the very characters it was meant to support --
    a strictly worse outcome than boxes.
    """
    state = _empty_state()
    _place(state, "ĞŞİ", "Öğrenci Değerlendirme ve Ölçme Çalıştayı",
           "Şükrü Işık Öğretmen", "monday", "09:00", ROOMS[0], YEAR_1, "A", 1)
    # An ASCII-coded control lesson in the same document. Without it this test
    # would still pass against an exporter that wrote an empty page, since the
    # Turkish class code is (today) unrepresentable in the content stream.
    _place(state, "D900", "Kontrol", LECT_A, "tuesday", "10:00",
           ROOMS[0], YEAR_1, "A", 1)
    state["lecturers"] = ["Şükrü Işık Öğretmen", LECT_A]

    out = tmp_path / "tr.pdf"
    export_schedule(state, "pdf", str(out), mode="everything")
    content = pdf_content_text(assert_well_formed_pdf(out))
    assert b"D900" in content, "the PDF survived but drew no lessons at all"


# ══════════════════════════════════════════════════════════════════════════
#  6. ST-FUNC-013 — PDF silently omits off-grid placements
# ══════════════════════════════════════════════════════════════════════════

def _state_with_offgrid_day():
    """D001 placed on Saturday, a day absent from ``state['days']``."""
    state = _full_state()
    assert "saturday" not in state["days"]
    mark_placed(state["classes"][0], "saturday", "09:00", "A-101")
    return state


# ST-FUNC-013 closed in Phase 4. The strict xfail that used to pin this is gone
# because the defect is: `_export_pdf` now ends with an appendix table listing
# every off-grid placement (and every conflict) by class code, so the *printout*
# says what it could not draw.
#
# Worth recording why the pin survived Phase 1, which did fix the underlying
# omission: `export_schedule` has warned about every orphan since then, for PDF
# too. But the warning interpolates `cls["name"]` and the *localized* day, while
# this test looks for `"D001"` (the class CODE) or the English `"saturday"` — so
# a warning was raised on every run and the test could not see it. Adjusting the
# needle would have been pinning-by-adjustment; the appendix is the real fix,
# and it satisfies the test's own first branch.
@pytest.mark.pdf
@pytest.mark.parametrize("mode", MODES)
def test_pdf_does_not_silently_drop_offgrid_placements(
        tmp_path, reportlab_mod, mode):
    """ST-FUNC-013 — a class parked outside the grid must not vanish from PDF.

    A failure means a lesson still stored as placed (e.g. on Saturday after
    Saturday was removed from the setup) disappears from the printed timetable
    with no warning: the user prints a schedule that is quietly incomplete.
    """
    state = _state_with_offgrid_day()
    out = tmp_path / f"offgrid_{mode}.pdf"

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        export_schedule(state, "pdf", str(out), mode=mode)

    content = pdf_content_text(assert_well_formed_pdf(out))
    # Control: the on-grid classes are present, so a miss below is a real drop.
    assert b"D002" in content, "control class D002 missing — test is broken"

    # The finding is *silent* data loss. Either legitimate fix passes: draw the
    # off-grid lesson, or tell the user it was left out. Only saying nothing at
    # all fails. (Encoding just one of the two fixes would make this pin go red
    # against a correct implementation of the other.)
    warned = any(
        "D001" in str(w.message) or "saturday" in str(w.message).lower()
        for w in caught
    )
    assert b"D001" in content or warned, (
        "D001 is placed on saturday (outside state['days']) and was silently "
        f"omitted from the mode={mode} PDF — not drawn and not warned about"
    )


def test_csv_still_reports_offgrid_placements(tmp_path):
    """Companion evidence for ST-FUNC-013: the CSV keeps what the PDF drops.

    A failure means the last export format that still surfaces an off-grid
    placement has stopped doing so, hiding the data loss completely.
    """
    out = tmp_path / "offgrid.csv"
    export_schedule(_state_with_offgrid_day(), "csv", str(out))

    rows = read_csv_rows(out)
    d001 = [r for r in rows[1:] if r[2] == "D001"]
    assert d001, "CSV also dropped the off-grid class"
    # Either spelling of Saturday is fine here; pinning the raw key would make
    # this guard contradict test_csv_day_column_is_localized (ST-FUNC-006) and
    # turn the module red the day that finding is fixed.
    assert d001[0][0] in accepted_day_cell_values("saturday"), \
        f"off-grid day column says {d001[0][0]!r}, expected Saturday"


# ST-FUNC-013 / ST-SCHED-004 fixed in Phase 1: logic.find_slot_index() makes the
# stored-placement readers total, and export_schedule() warns about every
# off-grid placement instead of dropping it. The strict xfail that used to pin
# this is gone because the defect is.
@pytest.mark.parametrize("fmt", ("csv", "xlsx"))
def test_offgrid_slot_does_not_crash_csv_and_xlsx(tmp_path, fmt, request):
    """ST-FUNC-013 (adjacent) — an off-grid *time* crashes csv/xlsx export.

    A failure means that after the user shortens the day in Setup, exporting
    at all raises ``ValueError: '17:00' is not in list`` — the whole export
    dies rather than the stale placement being reconciled or reported.
    """
    if fmt == "xlsx":
        request.getfixturevalue("openpyxl_mod")

    state = _full_state()
    assert "17:00" not in state["slots"]
    mark_placed(state["classes"][0], "monday", "17:00", "A-101")

    out = tmp_path / f"offslot.{fmt}"
    export_schedule(state, fmt, str(out))
    assert out.exists() and out.stat().st_size > 0
