"""A lesson that is on the timetable must appear on the timetable.

ST-UI-001 (Critical). The grid built an ``occupied[(row, col)]`` dict, so a
second lesson claiming a cell **overwrote** the first and rendered nowhere at
all. The loser is not merely invisible — it is *unreachable*: it is still
``placed`` or ``pinned``, so the unplaced sidebar excludes it too, and with no
``LessonItem`` it cannot be clicked, selected, edited, unplaced or dragged. The
only surface that still knows it exists is the status-bar counter, which is the
contradiction the finding is named for.

Why this is still live after Phase 3
------------------------------------
The optimizer no longer *proposes* collisions. But an infeasible **pin** is
committed rather than cleared, deliberately, because the pin is an instruction
the user typed (ST-SCHED-002) — the engine names those classes in
``summary['infeasible_fixed']`` and ``apply_reschedule`` returns them. So
collisions reach the grid by design, and the grid hides them.

Measured on the audit's own ``large`` preset after a real solve and commit: one
genuine physical-room double-book, ``R005 friday 13:00``, both classes pinned.
The room view drew neither of them; the group view drew one.

Two things the register does not say, both of which shape these tests
--------------------------------------------------------------------
1. **The collision is order-dependent, not "last wins".** A *span* row against a
   *start* row either overdraws (both blocks emitted, painted on top of each
   other) or hides one, depending purely on ``state["classes"]`` order. The one
   real collision on ``large`` is exactly this shape, so a fix that only handles
   "two starts in one cell" does not fix it.
2. **Two blocks in one cell is not the same as a conflict.** Two online lessons
   share an hour legitimately — they consume no room. A purely geometric
   detector paints a red pill on a correct timetable; a naive sweep of ``large``
   reported 14 collisions of which 13 were legal online concurrency.

So the module asserts two *different* properties, and they must not be conflated:

* **completeness** — geometric, per view: every placed lesson the filter accepts
  produces exactly one block. This is the fix for the silent drop.
* **conflict** — a validator verdict, view-independent: these two lessons cannot
  coexist. This is the fix for the missing warning.

A cell may satisfy the first and not the second (legal shared cell), and a
conflict may exist where the current view shows only one block (same group,
two different rooms).

Fail-now / pass-after: ST-UI-001 is fixed in Phase 4, so nothing here is
``xfail``.
"""
import pytest

from scheduler_app.translations import tr
from scheduler_app.core.logic import (
    find_schedule_conflicts, conflict_partner_index,
    assign_component_lanes, _sweep_lanes,
    build_virtual_classroom_day_layout, display_room,
)
from scheduler_app.core.models import (
    new_state, new_class, mark_placed, cls_key, LOCATION_ONLINE,
)


DAYS = ["monday", "tuesday"]
SLOTS = ["09:00", "10:00", "11:00", "12:00"]


def _state():
    s = new_state()
    s["days"] = list(DAYS)
    s["slots"] = list(SLOTS)
    s["classrooms"] = ["R001", "R002"]
    s["years"] = {"Year-1": ["A", "B"]}
    s["lecturers"] = ["Lect-01", "Lect-02"]
    return s


def _add(state, code, slot, room, lecturer="Lect-01", day="monday",
         duration=1, branch="A", location_type=None, pin=False,
         joint=True, targets=None):
    cls = new_class()
    cls["class_code"] = code
    cls["name"] = f"Lesson {code}"
    cls["lecturer"] = lecturer
    cls["duration"] = duration
    cls["participants"] = 10
    cls["joint_session"] = joint
    cls["targets"] = targets or [{"year": "Year-1", "branch": branch}]
    if location_type is not None:
        cls["location_type"] = location_type
    if pin:
        cls["pinned"] = True
        cls["pinned_day"] = day
        cls["pinned_time"] = slot
        cls["pinned_classroom"] = room
    else:
        mark_placed(cls, day, slot, room)
    state["classes"].append(cls)
    return cls


def _room_filter(room):
    return lambda c: display_room(c) == room


def _blocks(state, filter_fn, mode=None):
    from scheduler_app.ui.renderer import RendererAdapter, FILTER_MODE_DEFAULT
    blocks, _occ = RendererAdapter.filtered_blocks(
        state, filter_fn, mode=mode or FILTER_MODE_DEFAULT)
    return blocks


def _codes(blocks):
    return sorted(b["cls"]["class_code"] for b in blocks)


# ══════════════════════════════════════════════════════════════════════
#  1. The detector: what actually conflicts
# ══════════════════════════════════════════════════════════════════════

def test_two_lessons_in_one_room_at_one_hour_are_a_conflict():
    """ST-UI-001 — the plainest double-booking must be reported.

    A failure means the product has no way to know that two lessons are in one
    room at one hour, and therefore no way to tell the user.
    """
    s = _state()
    _add(s, "AAA", "09:00", "R001", lecturer="Lect-01")
    _add(s, "BBB", "09:00", "R001", lecturer="Lect-02", branch="B")

    conflicts = find_schedule_conflicts(s)

    assert len(conflicts) == 1, conflicts
    rec = conflicts[0]
    assert {rec["a"]["class_code"], rec["b"]["class_code"]} == {"AAA", "BBB"}
    assert rec["day"] == "monday" and rec["slot"] == "09:00"
    assert "room" in rec["kinds"]


def test_two_online_lessons_sharing_an_hour_are_not_a_conflict():
    """ST-UI-001 — legal concurrency must not be reported as a clash.

    A failure means every school that teaches two online lessons at the same
    time sees a red ÇAKIŞMA warning on a perfectly correct timetable, and
    learns to ignore the one indicator that matters.

    This is the case a purely geometric "two blocks in one cell" detector gets
    wrong: on the audit's ``large`` preset a naive sweep reports 14 collisions,
    of which 13 are exactly this.
    """
    s = _state()
    _add(s, "NET1", "09:00", None, lecturer="Lect-01",
         location_type=LOCATION_ONLINE)
    _add(s, "NET2", "09:00", None, lecturer="Lect-02", branch="B",
         location_type=LOCATION_ONLINE)

    assert find_schedule_conflicts(s) == []

    # Anti-vacuity: the same two lessons DO conflict once they share a lecturer,
    # so this is not passing because the detector ignores online lessons.
    s2 = _state()
    _add(s2, "NET1", "09:00", None, lecturer="Lect-01",
         location_type=LOCATION_ONLINE)
    _add(s2, "NET2", "09:00", None, lecturer="Lect-01", branch="B",
         location_type=LOCATION_ONLINE)
    assert [r["kinds"] for r in find_schedule_conflicts(s2)] == [("lecturer",)]


def test_a_span_row_conflicts_with_a_start_row():
    """ST-UI-001 — a long lesson must clash with one starting underneath it.

    A failure means the one real collision the ``large`` preset actually
    produces — ``R005 friday 13:00``, a 3-hour pinned lesson overlapping a
    2-hour pinned lesson that started an hour earlier — is not detected at all.
    A detector that only compares *start* cells cannot see it.
    """
    s = _state()
    _add(s, "LONG", "09:00", "R001", duration=2, lecturer="Lect-01")
    _add(s, "SHORT", "10:00", "R001", duration=1, lecturer="Lect-02",
         branch="B")

    conflicts = find_schedule_conflicts(s)

    assert len(conflicts) == 1, conflicts
    assert conflicts[0]["slot"] == "10:00", "the contested cell is the second hour"
    assert "room" in conflicts[0]["kinds"]


def test_a_lecturer_in_two_rooms_at_once_is_a_conflict():
    """ST-UI-001 — a clash the classroom view can never show geometrically.

    A failure means a lecturer booked into two different rooms in the same hour
    is reported nowhere: each room's own view holds exactly one lesson, so
    nothing about the grid looks wrong.
    """
    s = _state()
    _add(s, "AAA", "09:00", "R001", lecturer="Lect-01")
    _add(s, "BBB", "09:00", "R002", lecturer="Lect-01", branch="B")

    conflicts = find_schedule_conflicts(s)

    assert len(conflicts) == 1
    assert conflicts[0]["kinds"] == ("lecturer",)
    # Each room view shows one lesson, so geometry alone sees nothing wrong.
    assert _codes(_blocks(s, _room_filter("R001"))) == ["AAA"]
    assert _codes(_blocks(s, _room_filter("R002"))) == ["BBB"]


def test_one_student_group_in_two_rooms_at_once_is_a_conflict():
    """ST-UI-001 — a double-booked class of students must be reported.

    A failure means Year-1/A is told to be in two rooms at nine o'clock and
    nothing in the product says so.
    """
    s = _state()
    _add(s, "AAA", "09:00", "R001", lecturer="Lect-01", branch="A")
    _add(s, "BBB", "09:00", "R002", lecturer="Lect-02", branch="A")

    conflicts = find_schedule_conflicts(s)

    assert len(conflicts) == 1
    assert conflicts[0]["kinds"] == ("target",)


def test_a_pair_is_reported_once_no_matter_how_many_hours_it_shares():
    """ST-UI-001 — one clash is one entry, not one per contested hour.

    A failure means a 3-hour double-booking produces three warning-log lines
    for one problem, and the panel that Phase 2 taught not to spam
    (ST-PERF-003) starts spamming again.
    """
    s = _state()
    _add(s, "AAA", "09:00", "R001", duration=3, lecturer="Lect-01")
    _add(s, "BBB", "09:00", "R001", duration=3, lecturer="Lect-01", branch="B")

    conflicts = find_schedule_conflicts(s)

    assert len(conflicts) == 1, conflicts
    # All three kinds accumulate onto the single record.
    assert set(conflicts[0]["kinds"]) == {"room", "lecturer"}


def test_conflicts_are_ordered_deterministically():
    """ST-UI-001 — the same timetable must report the same list every time.

    A failure means the warning log reshuffles itself on every repaint, and
    Phase 2's ``set_derived`` no-op check (which compares the new list against
    the old) stops recognising an unchanged schedule — re-rendering the whole
    document on every refresh.
    """
    s = _state()
    _add(s, "DDD", "11:00", "R001", lecturer="Lect-01", day="tuesday")
    _add(s, "CCC", "11:00", "R001", lecturer="Lect-02", day="tuesday",
         branch="B")
    _add(s, "AAA", "09:00", "R001", lecturer="Lect-01")
    _add(s, "BBB", "09:00", "R001", lecturer="Lect-02", branch="B")

    first = find_schedule_conflicts(s)
    again = find_schedule_conflicts(s)

    assert len(first) == 2
    assert [(r["day"], r["slot"]) for r in first] == [
        ("monday", "09:00"), ("tuesday", "11:00")]
    assert [cls_key(r["a"]) for r in first] == [cls_key(r["a"]) for r in again]


def test_an_off_grid_placement_conflicts_with_nothing():
    """ST-UI-001 / ST-DATA-003 — a lesson on a deleted hour occupies no cell.

    A failure means a stale placement pointing at an hour the user removed is
    reported as clashing with whatever now sits at that index — a conflict the
    user cannot see, cannot reach and cannot resolve.
    """
    s = _state()
    _add(s, "AAA", "09:00", "R001", lecturer="Lect-01")
    ghost = _add(s, "GHOST", "09:00", "R001", lecturer="Lect-01", branch="B")
    ghost["placed_time"] = "23:00"

    assert find_schedule_conflicts(s) == []

    # Anti-vacuity: it really is still placed, and really would clash on-grid.
    assert ghost["placed"] is True
    ghost["placed_time"] = "09:00"
    assert len(find_schedule_conflicts(s)) == 1


def test_partner_index_is_symmetric():
    """ST-UI-001 — both lessons in a clash must know about each other.

    A failure means the grid marks one of two colliding lessons and leaves the
    other looking fine, so the user fixes the wrong one.
    """
    s = _state()
    a = _add(s, "AAA", "09:00", "R001", lecturer="Lect-01")
    b = _add(s, "BBB", "09:00", "R001", lecturer="Lect-02", branch="B")

    index = conflict_partner_index(find_schedule_conflicts(s))

    assert index[cls_key(a)] == {cls_key(b)}
    assert index[cls_key(b)] == {cls_key(a)}


# ══════════════════════════════════════════════════════════════════════
#  2. The geometry: every lesson gets a block
# ══════════════════════════════════════════════════════════════════════

def test_both_lessons_in_a_contested_cell_are_rendered():
    """ST-UI-001 — neither of two colliding lessons may vanish from the grid.

    A failure means the user prints and publishes a timetable that is missing a
    real lesson, with nothing on screen to warn them, and cannot even click the
    missing lesson to fix it because it has no item in the scene.
    """
    s = _state()
    _add(s, "AAA", "09:00", "R001", lecturer="Lect-01")
    _add(s, "BBB", "09:00", "R001", lecturer="Lect-02", branch="B")

    assert _codes(_blocks(s, _room_filter("R001"))) == ["AAA", "BBB"]


def test_rendering_a_contested_cell_does_not_depend_on_class_order():
    """ST-UI-001 — the same timetable must draw the same way every time.

    A failure means which of two lessons you can see depends on the order the
    user happened to create them in. Measured before the fix: a 2-hour lesson
    at 09:00 against a 1-hour lesson at 10:00 rendered *both* (overdrawn, one
    painted over the other) when the long one came first, and dropped the short
    one entirely when it came second.
    """
    def build(order):
        s = _state()
        specs = [("LONG", "09:00", 2), ("SHORT", "10:00", 1)]
        for code, slot, dur in (specs if order == "long-first"
                                else list(reversed(specs))):
            _add(s, code, slot, "R001", duration=dur,
                 lecturer="Lect-01" if code == "LONG" else "Lect-02",
                 branch="A" if code == "LONG" else "B")
        return s

    long_first = _codes(_blocks(build("long-first"), _room_filter("R001")))
    short_first = _codes(_blocks(build("short-first"), _room_filter("R001")))

    assert long_first == short_first == ["LONG", "SHORT"]


def test_three_lessons_in_one_cell_all_render():
    """ST-UI-001 — an N-way pile-up must not become a 1-way one.

    A failure means two of three lessons disappear. Measured before the fix:
    three classes in one cell rendered exactly one.
    """
    s = _state()
    for i, lect in enumerate(["Lect-01", "Lect-02", "Lect-01"]):
        _add(s, f"T{i}", "09:00", "R001", lecturer=lect,
             branch="A" if i % 2 == 0 else "B")

    assert _codes(_blocks(s, _room_filter("R001"))) == ["T0", "T1", "T2"]


def test_an_uncontested_lesson_keeps_the_full_column():
    """ST-UI-001 — one collision must not reshape the rest of the week.

    A failure means a single double-booking at Monday 09:00 halves the width of
    every other Monday hour, because the lane count was taken per *day* rather
    than per contested run. That is right for the online view, where
    concurrency is normal, and wrong for a room timetable where a collision is
    an exception.
    """
    s = _state()
    _add(s, "AAA", "09:00", "R001", lecturer="Lect-01")
    _add(s, "BBB", "09:00", "R001", lecturer="Lect-02", branch="B")
    _add(s, "CCC", "12:00", "R001", lecturer="Lect-01")

    by_code = {b["cls"]["class_code"]: b
               for b in _blocks(s, _room_filter("R001"))}

    assert by_code["AAA"]["lane_count"] == 2
    assert by_code["BBB"]["lane_count"] == 2
    assert by_code["CCC"]["lane_count"] == 1, (
        "an uncontested lesson was narrowed by a collision three hours away"
    )


def test_a_clean_timetable_renders_exactly_as_before():
    """ST-UI-001 — the fix must be invisible on a schedule with no clashes.

    A failure means every existing user's timetable is re-laid-out by a change
    that was supposed to affect only conflicted cells.
    """
    s = _state()
    _add(s, "AAA", "09:00", "R001", lecturer="Lect-01")
    _add(s, "BBB", "10:00", "R001", lecturer="Lect-02", branch="B")
    _add(s, "CCC", "11:00", "R001", lecturer="Lect-01", duration=2)

    blocks = _blocks(s, _room_filter("R001"))

    assert _codes(blocks) == ["AAA", "BBB", "CCC"]
    assert all(b["lane"] == 0 and b["lane_count"] == 1 for b in blocks)
    assert {b["cls"]["class_code"]: (b["row"], b["span"]) for b in blocks} == {
        "AAA": (0, 1), "BBB": (1, 1), "CCC": (2, 2)}


# ══════════════════════════════════════════════════════════════════════
#  3. The lane algorithm, and the refactor that must not change behaviour
# ══════════════════════════════════════════════════════════════════════

def _entries(*specs):
    return [{"row": r, "end_row": r + sp, "span": sp, "order": o,
             "lane": 0, "lane_count": 1, "name": n}
            for n, r, sp, o in specs]


def test_component_lanes_split_only_contested_runs():
    """ST-UI-001 — lane counts are per overlapping run, not per column.

    A failure means the whole column adopts the widest pile-up in it.
    """
    entries = _entries(("A", 0, 1, 0), ("B", 0, 1, 1),
                       ("E", 2, 1, 2),
                       ("C", 5, 1, 3), ("D", 5, 1, 4))
    assign_component_lanes(entries)
    got = {e["name"]: (e["lane"], e["lane_count"]) for e in entries}

    assert got["A"][1] == 2 and got["B"][1] == 2
    assert got["C"][1] == 2 and got["D"][1] == 2
    assert got["E"] == (0, 1), "an isolated lesson was split"


def test_a_long_lesson_joins_the_runs_it_spans():
    """ST-UI-001 — a lesson overlapping two pile-ups makes them one run.

    A failure means the long lesson is assigned a lane in one run and a
    different width in another, so it is drawn twice at two widths or clipped.
    """
    entries = _entries(("LONG", 0, 5, 0), ("A", 0, 1, 1), ("B", 3, 1, 2))
    assign_component_lanes(entries)
    got = {e["name"]: (e["lane"], e["lane_count"]) for e in entries}

    assert {v[1] for v in got.values()} == {2}, got
    assert got["LONG"][0] != got["A"][0]
    assert got["LONG"][0] != got["B"][0]


def test_extracting_the_sweep_did_not_change_the_online_view():
    """ST-UI-001 — the virtual-classroom layout must be byte-identical.

    ``build_virtual_classroom_day_layout`` had the lane sweep inlined; it was
    extracted so the default view could reuse the algorithm. A failure means
    the extraction changed the layout of the online timetable, which was never
    broken.

    The expectations are the documented contract of that function: a day-wide
    lane count, and a minimum of 1 so an empty day still renders one column.
    """
    s = _state()
    _add(s, "N1", "09:00", None, lecturer="Lect-01", location_type=LOCATION_ONLINE)
    _add(s, "N2", "09:00", None, lecturer="Lect-02", branch="B",
         location_type=LOCATION_ONLINE)
    _add(s, "N3", "12:00", None, lecturer="Lect-01", location_type=LOCATION_ONLINE)

    layout = build_virtual_classroom_day_layout(
        s, lambda c: c.get("location_type") == LOCATION_ONLINE)
    groups = {g["day"]: g for g in layout["day_groups"]}

    # Monday holds the pile-up, so the whole day is 2 lanes wide -- including
    # the uncontested 12:00 lesson. That is this view's deliberate behaviour.
    assert groups["monday"]["lane_count"] == 2
    assert groups["tuesday"]["lane_count"] == 1, "an empty day must still be 1"
    assert {b["cls"]["class_code"] for b in layout["blocks"]} == {"N1", "N2", "N3"}
    assert all(b["lane_count"] == 2 for b in layout["blocks"])


def test_the_sweep_returns_the_peak_concurrency():
    """ST-UI-001 — the extracted sweep's return value is the day-wide count.

    A failure means ``build_virtual_classroom_day_layout`` gets the wrong
    subcolumn count from the helper it now delegates to.
    """
    assert _sweep_lanes([]) == 0
    assert _sweep_lanes(_entries(("A", 0, 1, 0))) == 1
    assert _sweep_lanes(_entries(("A", 0, 1, 0), ("B", 1, 1, 1))) == 1
    assert _sweep_lanes(_entries(("A", 0, 2, 0), ("B", 1, 1, 1))) == 2
    assert _sweep_lanes(
        _entries(("A", 0, 1, 0), ("B", 0, 1, 1), ("C", 0, 1, 2))) == 3


def test_a_placement_on_a_day_the_grid_does_not_have_conflicts_with_nothing():
    """ST-UI-001 / ST-FUNC-013 — a lesson on a removed DAY is drawn nowhere.

    A failure means the warning log shows a red "Çakışma — Cumartesi 09:00"
    line for two lessons that appear on no tab and in no export grid, so the
    user is told to resolve a clash they cannot see, select or move.

    Distinct from the off-grid *slot* case above: ``occupied_slots_of``
    deliberately does not filter by day (an off-grid day still consumes real
    hours, and the CSV export is required to keep reporting it), so the day
    check has to be made explicitly by the conflict scan.
    """
    s = _state()
    a = _add(s, "AAA", "09:00", "R001", lecturer="Lect-01")
    b = _add(s, "BBB", "09:00", "R001", lecturer="Lect-01", branch="B")
    for cls in (a, b):
        cls["placed_day"] = "saturday"
    assert "saturday" not in s["days"]

    assert find_schedule_conflicts(s) == []

    # Anti-vacuity: the very same pair on a day the grid HAS is a conflict, so
    # this is not passing because the scan stopped seeing these two classes.
    for cls in (a, b):
        cls["placed_day"] = "monday"
    assert len(find_schedule_conflicts(s)) == 1


def test_a_block_overrunning_the_day_still_clashes_over_the_hours_it_covers():
    """ST-UI-001 — a lesson that overruns the grid still double-books.

    ``find_conflicting_classes`` returns ``[]`` outright when ``slots_fit``
    fails, because it is answering "may I place this candidate here?". This
    scan answers "what is already wrong?", and a 3-hour lesson in the last
    2 hours of the day really is sitting on top of whatever else is there.

    A failure means that after the user shortens the teaching day in Setup —
    which leaves exactly this state, and which ``reconcile_placements`` does
    not catch — a real double-booking silently stops being reported.
    """
    s = _state()
    over = _add(s, "OVER", "11:00", "R001", duration=4, lecturer="Lect-01")
    _add(s, "UNDER", "11:00", "R001", duration=1, lecturer="Lect-02",
         branch="B")

    from scheduler_app.core.logic import slots_fit
    assert not slots_fit(s, "11:00", 4), "fixture does not overrun the grid"

    conflicts = find_schedule_conflicts(s)
    assert len(conflicts) == 1, conflicts
    assert "room" in conflicts[0]["kinds"]
    assert over["placed"] is True


def test_a_blank_lecturer_is_not_a_lecturer_clash():
    """ST-UI-001 — two lessons with no lecturer do not share a lecturer.

    ``_detect_occupancy_conflicts`` compares lecturer names with no truthiness
    guard, which is harmless when screening one candidate and wrong when
    reporting a defect: a failure means every pair of lessons that has not been
    assigned a lecturer yet is reported to the user as a lecturer conflict.
    """
    s = _state()
    _add(s, "AAA", "09:00", "R001", lecturer="", branch="A")
    _add(s, "BBB", "09:00", "R002", lecturer="", branch="B")

    assert find_schedule_conflicts(s) == []

    # Anti-vacuity: give them the same real lecturer and it IS a clash.
    s["classes"][0]["lecturer"] = "Lect-01"
    s["classes"][1]["lecturer"] = "Lect-01"
    assert [r["kinds"] for r in find_schedule_conflicts(s)] == [("lecturer",)]


# ══════════════════════════════════════════════════════════════════════
#  4. The exports must agree with the screen
# ══════════════════════════════════════════════════════════════════════

def _pdf_text(path):
    """Every decoded content stream of the PDF, concatenated.

    Mirrors ``test_export_smoke.pdf_content_text``. The ASCII85 step is not
    optional: reportlab emits ``~>``-terminated a85 streams, and a decoder that
    only tries zlib returns the raw compressed bytes, so every needle misses
    and the test fails against a perfectly good export.
    """
    import base64
    import re
    import zlib

    raw = path.read_bytes()
    out = []
    for body in re.findall(rb"stream\r?\n(.*?)endstream", raw, re.DOTALL):
        data = body.strip()
        if data.endswith(b"~>"):
            try:
                data = base64.a85decode(data, adobe=True)
            except ValueError:
                out.append(body)
                continue
        try:
            out.append(zlib.decompress(data))
        except zlib.error:
            out.append(data)
    return b"\n".join(out)


@pytest.mark.pdf
@pytest.mark.parametrize("mode", ["classroom", "group"])
def test_the_pdf_shows_both_lessons_of_a_contested_cell(tmp_path, mode):
    """ST-UI-001 — the printout must not drop a lesson the screen shows.

    A failure means the screen and the printed PDF of the same view disagree
    about *which* of two double-booked lessons exists: the grid used to keep
    the LAST claimant (dict overwrite) while ``_build_filtered_table`` kept the
    FIRST (an explicit ``continue``). A user who checked the timetable on
    screen and then printed it got two different, both-incomplete documents.

    reportlab's ``SPAN`` merges rows, not columns, so a PDF cell cannot be
    split into lanes; every claimant is stacked into the cell instead — which
    is what the XLSX writer already did, so this makes the surfaces agree
    rather than inventing a fourth behaviour.
    """
    pytest.importorskip("reportlab")
    from scheduler_app.data_io.exporter import export_schedule

    s = _state()
    _add(s, "AAA111", "09:00", "R001", lecturer="Lect-01")
    _add(s, "ZZZ999", "09:00", "R001", lecturer="Lect-02", branch="B")

    on_screen = set(_codes(_blocks(s, _room_filter("R001"))))
    assert on_screen == {"AAA111", "ZZZ999"}, "the grid regressed"

    out = tmp_path / f"conflict_{mode}.pdf"
    export_schedule(s, "pdf", str(out), mode=mode)
    text = _pdf_text(out)

    for code in ("AAA111", "ZZZ999"):
        assert code.encode() in text, (
            f"{code} is on the timetable and on screen, but missing from the "
            f"mode={mode} PDF"
        )


@pytest.mark.pdf
def test_the_pdf_appendix_names_a_conflict(tmp_path):
    """ST-UI-001 — a stacked cell is easy to miss; the appendix spells it out.

    A failure means a double-booking is printed but never called one, so the
    reader has to notice two lessons crammed into a cell on a dense A3 page.
    """
    pytest.importorskip("reportlab")
    from scheduler_app.data_io.exporter import export_schedule
    from scheduler_app.translations import tr

    s = _state()
    _add(s, "AAA111", "09:00", "R001", lecturer="Lect-01")
    _add(s, "ZZZ999", "09:00", "R001", lecturer="Lect-02", branch="B")

    out = tmp_path / "appendix.pdf"
    export_schedule(s, "pdf", str(out), mode="classroom")
    text = _pdf_text(out)

    # `b"AAA111" in text` alone passes with the entire appendix stubbed out --
    # both codes are in the stacked GRID cell too. The discriminating needle is
    # the appendix TITLE, which appears nowhere else; its sibling test
    # `test_a_clean_timetable_gets_no_appendix` asserts the same needle is
    # ABSENT on a clean schedule, so the pair brackets the behaviour.
    title_needle = tr("export.appendix_title").split()[0].encode()
    assert title_needle in text, (
        "the appendix page was not emitted for a conflicted timetable"
    )
    assert b"AAA111" in text and b"ZZZ999" in text


@pytest.mark.pdf
def test_a_clean_timetable_gets_no_appendix(tmp_path):
    """ST-UI-001 — the appendix must not appear when there is nothing to say.

    A failure means every export grows a page of blank apology, training the
    user to ignore it before it ever carries a real message.
    """
    pytest.importorskip("reportlab")
    from scheduler_app.data_io.exporter import export_schedule
    from scheduler_app.translations import tr

    s = _state()
    _add(s, "AAA111", "09:00", "R001", lecturer="Lect-01")
    _add(s, "BBB222", "10:00", "R002", lecturer="Lect-02", branch="B")
    assert find_schedule_conflicts(s) == []

    out = tmp_path / "clean.pdf"
    export_schedule(s, "pdf", str(out), mode="classroom")
    text = _pdf_text(out)

    # The appendix title's first ASCII word is enough of a needle and does not
    # depend on the Turkish glyphs surviving the base-14 font (ST-FUNC-004).
    assert b"AAA111" in text, "control lesson missing - the export is broken"
    assert tr("export.appendix_title").split()[0].encode() not in text


@pytest.mark.excel
@pytest.mark.ui
@pytest.mark.parametrize("mode", ["classroom", "everything"])
def test_the_workbook_shows_both_lessons_of_a_contested_cell(
        make_app, tmp_path, mode):
    """ST-UI-001 — the user-facing Excel export must keep both lessons.

    This used to reach ``ui/app.py::_write_excel``, because the app called
    ``export_schedule`` only for PDF and the exporter's own Excel writer was
    not what a user got. ST-ARCH-003 unified them in Phase 6: there is one
    writer now and ``export_schedule`` is the way in, so this test exercises
    the user's path by exercising the public entry point.

    Its *filtered* sheets already stacked both lessons; the **everything**
    matrix in the same workbook still kept only the last writer, so one
    workbook disagreed with itself. A failure means a school's printed Excel
    timetable is missing a lesson that the sheet next to it shows.
    """
    openpyxl = pytest.importorskip("openpyxl")
    from scheduler_app.data_io.exporter import export_schedule
    s = _state()
    _add(s, "AAA111", "09:00", "R001", lecturer="Lect-01")
    _add(s, "ZZZ999", "09:00", "R001", lecturer="Lect-02", branch="B")

    out = tmp_path / f"conflict_{mode}.xlsx"
    export_schedule(s, "xlsx", str(out), mode=mode)

    wb = openpyxl.load_workbook(out)
    text = " ".join(
        str(c.value)
        for sheet in wb.sheetnames
        for row in wb[sheet].iter_rows()
        for c in row if c.value is not None)

    for code in ("AAA111", "ZZZ999"):
        assert code in text, (
            f"{code} is on the timetable but missing from the mode={mode} "
            f"workbook"
        )


# ══════════════════════════════════════════════════════════════════════
#  5. The mark must survive all the way into the scene
# ══════════════════════════════════════════════════════════════════════
#
# Everything above asserts on the ADAPTER's blocks. That is one layer short:
# the adapter stamped the flags correctly for both render modes, and the
# virtual-classroom scene builder silently dropped them when constructing its
# LessonItems — so the Online / Lecturer-office tab drew a genuine clash with a
# normal border and a silent tooltip while every other tab painted it red.
# Nothing caught it, because no test in the repository built a TimetableScene.

def _scene_items(qapp, state, filter_fn, mode):
    """``(scene, {class_code: LessonItem})``.

    The scene is returned, not discarded: dropping the last Python reference
    lets Qt delete the C++ objects, and every item access then raises
    ``RuntimeError: wrapped C/C++ object of type LessonItem has been deleted``.
    """
    from scheduler_app.ui.renderer import TimetableScene
    scene = TimetableScene()
    scene.build_filtered(state, filter_fn, None, mode=mode)
    return scene, {it.cls["class_code"]: it for it in scene.lesson_items}


@pytest.mark.ui
def test_the_conflict_mark_reaches_the_items_in_every_render_mode(qapp):
    """ST-UI-001 — a clash must be red on the tab the user is actually on.

    A failure means the conflict is computed, stamped onto the block, and then
    thrown away when the graphics item is built — so the lesson renders as
    ordinary. The Online view is the one a user opens *specifically* to inspect
    online teaching, and it was the one view that did not honour the mark.

    Two online lessons sharing a lecturer is a real clash: they consume no room,
    so the room axis says nothing, but one person cannot teach both.
    """
    from scheduler_app.ui.renderer import (
        FILTER_MODE_DEFAULT, FILTER_MODE_VIRTUAL_CLASSROOM_OVERLAP)
    from scheduler_app.core.models import LOCATION_ONLINE

    s = _state()
    _add(s, "AAA", "09:00", None, lecturer="Lect-01",
         location_type=LOCATION_ONLINE)
    _add(s, "BBB", "09:00", None, lecturer="Lect-01", branch="B",
         location_type=LOCATION_ONLINE)

    # Anti-vacuity: the engine really does call this a clash.
    assert [r["kinds"] for r in find_schedule_conflicts(s)] == [("lecturer",)]

    online = lambda c: c.get("location_type") == LOCATION_ONLINE
    for mode in (FILTER_MODE_DEFAULT, FILTER_MODE_VIRTUAL_CLASSROOM_OVERLAP):
        scene, items = _scene_items(qapp, s, online, mode)
        assert set(items) == {"AAA", "BBB"}, (mode, sorted(items))
        for code, item in items.items():
            assert item._conflict is True, (
                f"{code} lost its conflict mark in mode={mode}"
            )
            assert item._conflict_labels, (
                f"{code} has no partner name to show in mode={mode}"
            )
            assert tr("conflicts.tooltip_header") in item.toolTip(), (
                f"{code}'s tooltip says nothing about the clash in mode={mode}"
            )


@pytest.mark.ui
def test_a_clean_lesson_carries_no_conflict_mark_in_either_mode(qapp):
    """ST-UI-001 — the mark must not be permanently on.

    A failure means every lesson is red, which is the same as none being red.
    """
    from scheduler_app.ui.renderer import (
        FILTER_MODE_DEFAULT, FILTER_MODE_VIRTUAL_CLASSROOM_OVERLAP)
    from scheduler_app.core.models import LOCATION_ONLINE

    s = _state()
    _add(s, "AAA", "09:00", None, lecturer="Lect-01",
         location_type=LOCATION_ONLINE)
    _add(s, "BBB", "09:00", None, lecturer="Lect-02", branch="B",
         location_type=LOCATION_ONLINE)
    assert find_schedule_conflicts(s) == []

    online = lambda c: c.get("location_type") == LOCATION_ONLINE
    for mode in (FILTER_MODE_DEFAULT, FILTER_MODE_VIRTUAL_CLASSROOM_OVERLAP):
        scene, items = _scene_items(qapp, s, online, mode)
        for code, item in items.items():
            assert item._conflict is False, (code, mode)
            assert tr("conflicts.tooltip_header") not in item.toolTip()


@pytest.mark.pdf
def test_the_pdf_everything_matrix_shows_both_lessons_of_a_contested_cell(
        tmp_path):
    """ST-UI-001 — the everything matrix was the last builder still dropping one.

    The filtered PDF tables were fixed to stack claimants; `_build_everything_table`
    kept its plain `occupied[okey] = ...` dict assignment, so a second claimant
    of a (slot, day, branch) cell overwrote the first and printed nowhere. The
    everything view is the one a head of department prints.

    A failure means the year-by-branch printout is missing a lesson that the
    per-classroom printout of the same file shows.
    """
    pytest.importorskip("reportlab")
    from scheduler_app.data_io.exporter import export_schedule

    s = _state()
    _add(s, "AAA111", "09:00", "R001", lecturer="Lect-01")
    _add(s, "ZZZ999", "09:00", "R002", lecturer="Lect-02")   # same branch A

    out = tmp_path / "everything.pdf"
    export_schedule(s, "pdf", str(out), mode="everything")
    text = _pdf_text(out)

    # Counting, not membership: the conflict APPENDIX lists both codes too, so
    # `code in text` passes even when the grid drops one. Each code appears
    # once in the grid cell and once in the appendix, so the grid contributes
    # the second occurrence.
    # Measured, not guessed: 4 occurrences when the grid stacks both, 2 when it
    # drops one (appendix only). The threshold sits between them, not at the
    # broken value -- `>= 2` passes on the defect.
    for code in ("AAA111", "ZZZ999"):
        assert text.count(code.encode()) >= 3, (
            f"{code} shares a (slot, day, branch) cell and appears "
            f"{text.count(code.encode())} time(s) — it is in the appendix but "
            f"was dropped from the everything grid"
        )


@pytest.mark.pdf
def test_a_contested_pdf_row_is_taller_than_an_uncontested_one(tmp_path):
    """ST-UI-001 — a stacked cell must not overprint the hours around it.

    `rowHeights` is FIXED in these tables: reportlab does not grow a row to fit
    its content, it draws over the neighbours. Stacking two lessons into a cell
    without re-measuring turns a silent drop into an unreadable smear, which on
    a printed timetable is worse.

    A failure means the contested row is still MIN_ROW_H and its text runs into
    the lessons above and below.
    """
    pytest.importorskip("reportlab")
    from scheduler_app.data_io import exporter

    s = _state()
    _add(s, "AAA111", "09:00", "R001", lecturer="Lect-01")
    _add(s, "ZZZ999", "09:00", "R001", lecturer="Lect-02", branch="B")
    _add(s, "SOLO01", "11:00", "R001", lecturer="Lect-01")

    captured = {}
    out = tmp_path / "heights.pdf"
    # Capture the row heights the filtered table actually asks for.
    import reportlab.platypus as platypus
    orig = platypus.Table

    class SpyTable(orig):
        def __init__(self, data, colWidths=None, rowHeights=None, **kw):
            if rowHeights and len(rowHeights) > 3:
                captured.setdefault("rows", list(rowHeights))
            super().__init__(data, colWidths=colWidths,
                             rowHeights=rowHeights, **kw)

    platypus.Table = SpyTable
    try:
        exporter.export_schedule(s, "pdf", str(out), mode="classroom")
    finally:
        platypus.Table = orig

    rows = captured.get("rows")
    assert rows, "no table with data rows was built"
    # row 0 is the header; data rows follow the slot order 09:00, 10:00, 11:00.
    contested, uncontested = rows[1], rows[3]
    assert contested > uncontested, (
        f"the contested row ({contested}) is no taller than an ordinary one "
        f"({uncontested}) — the stack will overprint its neighbours"
    )


@pytest.mark.excel
@pytest.mark.ui
def test_a_duplicate_target_class_is_not_stacked_against_itself(
        make_app, tmp_path):
    """ST-UI-001 regression — one lesson must not contest its own cell.

    The XLSX everything-matrix collects claimants inside ``for t in
    c["targets"]``, so a class carrying two IDENTICAL target dicts — which a
    user creates by typing "A, B, A" as a year's branches — claimed the same
    cell twice and was then found to be "overlapping" with *itself*. It was
    pulled out of ``occupied_start`` and rendered through the stacked-conflict
    branch: no merge, no year colour, its own name printed twice.

    A failure means an ordinary lesson is drawn as if it were double-booked.
    Asserted on the rendered cell's VALUE, FILL and MERGE RANGE against an
    identical single-target control — "the class code appears somewhere in the
    workbook" would pass on the broken code.
    """
    openpyxl = pytest.importorskip("openpyxl")

    def _book(branches, targets):
        s = _state()
        s["years"] = {"Year-1": branches}
        cls = _add(s, "DUP001", "09:00", "R001", lecturer="Lect-01", duration=2)
        cls["targets"] = targets
        from scheduler_app.data_io.exporter import export_schedule
        out = tmp_path / f"dup_{len(targets)}.xlsx"
        export_schedule(s, "xlsx", str(out), mode="everything")
        wb = openpyxl.load_workbook(out)
        ws = wb[wb.sheetnames[0]]
        cell = ws.cell(row=3, column=3)
        merged = {str(r) for r in ws.merged_cells.ranges}
        return str(cell.value), (cell.fill.start_color.rgb if cell.fill else None), merged

    control = _book(["A"], [{"year": "Year-1", "branch": "A"}])
    duplicate = _book(["A", "B"], [{"year": "Year-1", "branch": "A"},
                                   {"year": "Year-1", "branch": "A"}])

    assert duplicate[0] == control[0], (
        f"the duplicate-target class rendered differently: "
        f"{duplicate[0]!r} vs control {control[0]!r}"
    )
    assert duplicate[1] == control[1], (
        f"the duplicate-target class lost its year colour: "
        f"{duplicate[1]} vs control {control[1]}"
    )
    # Only the LESSON's own merge is comparable: a two-branch year adds header
    # spans (C1:D1, E1:F1) that the one-branch control cannot have.
    lesson_merge = "C3:C4"
    assert lesson_merge in control[2], "control lost its merge — test is broken"
    assert lesson_merge in duplicate[2], (
        f"the duplicate-target class lost its 2-hour merge; merges present: "
        f"{sorted(duplicate[2])}"
    )


@pytest.mark.pdf
def test_a_class_name_with_markup_characters_survives_the_pdf(tmp_path):
    """ST-UI-007 family — user text reaches reportlab's markup parser.

    Every PDF cell interpolates the class name, lecturer and room into markup.
    Unescaped, reportlab reads ``<Vekil>`` as an unknown TAG and drops it
    silently, so the printed timetable shows " Dersi" with the name gone.

    Measured both ways: escaped -> "Vekil" present; unescaped -> absent. A bare
    ``&`` is TOLERATED, so "Fizik & Kimya" renders either way — asserting on it
    would pin nothing, which is what a first version of this test did.

    Deliberately a CLEAN schedule with one lesson: on a conflicted one the
    appendix lists the same names through its own (separately escaped) path and
    keeps the needle alive no matter what the grid cell does.
    """
    pytest.importorskip("reportlab")
    from scheduler_app.data_io.exporter import export_schedule

    s = _state()
    only = _add(s, "AAA111", "09:00", "R001", lecturer="Lect-01")
    only["name"] = "<Vekil> Dersi"
    assert find_schedule_conflicts(s) == [], "fixture must not build an appendix"

    out = tmp_path / "markup.pdf"
    export_schedule(s, "pdf", str(out), mode="classroom")   # must not raise
    text = _pdf_text(out)

    assert b"AAA111" in text, "the lesson vanished entirely"
    assert b"Vekil" in text, (
        "'<Vekil> Dersi' lost its name — reportlab parsed the angle brackets "
        "as a tag and dropped the text"
    )
