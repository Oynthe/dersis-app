"""Scheduling logic: conflict detection, slot fitting, color helpers."""

from scheduler_app.constants import YEAR_COLORS
# ST-ARCH-010: this module holds the scheduling *primitives* only. The eight
# bridge functions that used to sit at the bottom of the file, and the 13
# deferred imports they carried, are in `scheduler_app/core/facade.py` now --
# see the note at the end of this file. Nothing imported here may import
# `logic` back; that is what keeps the module-level graph acyclic.
from scheduler_app.models import (
    PROTECTION_NONE,
    lecturer_available_at, needs_physical_room, display_room,
    get_room_candidates,
    effective_day, effective_time,
    filter_class_days, filter_class_times,
    apply_lecturer_availability_filters,
    cls_key,
)
from scheduler_app.translations import tr
from scheduler_app.i18n.day_keys import display_day


def slot_index(state, slot_name):
    """Grid index of *slot_name*. Raises ValueError if it is not on the grid.

    Deliberately still raising (ST-SCHED-004). Around forty call sites do
    ``idx + duration`` arithmetic on the result: returning None would turn every
    unguarded one from a loud ValueError into an obscure TypeError, and
    returning -1 would be worse still — -1 is a valid Python index, so lessons
    would silently land in the last hour of the day. Call sites that legitimately
    read a *stored* placement, which may be stale, use find_slot_index instead.
    """
    return state["slots"].index(slot_name)


def find_slot_index(state, slot_name):
    """Grid index of *slot_name*, or None when it is not on the grid.

    Use this wherever the slot comes from stored data (a placement, a pin, a
    saved constraint) rather than from candidate generation: a user who removes
    or renames a time slot leaves exactly those references dangling
    (ST-DATA-003).
    """
    try:
        return state["slots"].index(slot_name)
    except ValueError:
        return None


def slots_fit(state, start_slot, duration):
    idx = find_slot_index(state, start_slot)
    return idx is not None and idx + duration <= len(state["slots"])


def total_duration(cls):
    """Return the total number of slots a class occupies.

    For joint sessions (or single-target), this equals cls["duration"].
    For non-joint sessions with N targets, it equals duration * N.
    """
    if cls.get("joint_session", True) or len(cls.get("targets", [])) <= 1:
        return cls["duration"]
    return cls["duration"] * len(cls["targets"])


def build_virtual_classroom_day_layout(state, filter_fn):
    """Return a fixed-subcolumn day layout for a filtered virtual classroom view.

    The layout is scoped to already placed/pinned classes matching *filter_fn*.
    Each day gets a stable lane count equal to that day's maximum concurrency,
    with a minimum of 1 so empty days still render as a single day column.

    Returns a dict with:
        day_groups: list of day metadata in display order
        blocks: normalized lesson blocks with lane/subcolumn metadata
        occupied_subcolumns: set of (row, subcolumn) pairs covered by lessons
    """
    days = list(state.get("days", []))
    slots = list(state.get("slots", []))
    placed = get_placed_classes(state)

    day_entries = {day: [] for day in days}
    for order, cls in enumerate(placed):
        if not filter_fn(cls):
            continue
        day = effective_day(cls)
        start = effective_time(cls)
        if day not in day_entries or start not in slots:
            continue
        row = slots.index(start)
        span = min(total_duration(cls), len(slots) - row)
        if span <= 0:
            continue
        yr_name = cls["targets"][0]["year"] if cls.get("targets") else ""
        base_color = get_year_color(state, yr_name)
        day_entries[day].append({
            "cls": cls,
            "day": day,
            "day_index": days.index(day),
            "slot": start,
            "row": row,
            "end_row": row + span,
            "span": span,
            "order": order,
            "lane": 0,
            "lane_count": 1,
            "base_color": base_color,
            "bg_color": lighten_color(base_color, 0.45),
        })

    # One lane count per DAY: this view exists for the online/office filter,
    # where concurrency is the normal case, so a stable day-wide subcolumn
    # count is what the user wants. The default filtered view uses
    # assign_component_lanes instead, which splits only contested rows.
    lane_counts = {}
    for day in days:
        lane_counts[day] = max(1, _sweep_lanes(day_entries[day]))

    day_groups = []
    subcolumn_start = 0
    for day_index, day in enumerate(days):
        lane_count = lane_counts.get(day, 1)
        day_groups.append({
            "day": day,
            "day_index": day_index,
            "lane_count": lane_count,
            "subcolumn_start": subcolumn_start,
        })
        subcolumn_start += lane_count

    group_by_day = {group["day"]: group for group in day_groups}
    blocks = []
    occupied_subcolumns = set()
    for day in days:
        group = group_by_day[day]
        lane_count = group["lane_count"]
        for entry in day_entries[day]:
            block = dict(entry)
            block["lane_count"] = lane_count
            block["subcolumn"] = group["subcolumn_start"] + block["lane"]
            blocks.append(block)
            for offset in range(block["span"]):
                occupied_subcolumns.add((block["row"] + offset, block["subcolumn"]))

    blocks.sort(
        key=lambda entry: (
            entry["day_index"],
            entry["row"],
            entry["lane"],
            entry["order"],
        )
    )

    return {
        "day_groups": day_groups,
        "blocks": blocks,
        "occupied_subcolumns": occupied_subcolumns,
        "total_subcolumns": subcolumn_start,
    }


def get_consecutive_slots(state, start_slot, duration):
    """The *duration* grid slots starting at *start_slot*; [] if it is off-grid.

    Deliberately does NOT filter by day: an off-grid *day* still occupies real
    hours, and callers (CSV export in particular) are required to keep
    reporting such a placement rather than dropping it.
    """
    idx = find_slot_index(state, start_slot)
    if idx is None:
        return []
    return state["slots"][idx:idx + duration]


def get_placed_classes(state):
    return [c for c in state["classes"] if c["placed"] or c["pinned"]]


def schedule_counts(state):
    """The one placement vocabulary. Every counter a user sees comes from here.

    ST-UI-002. Three definitions of "placed" used to be on screen at once — the
    status bar counted ``placed`` only, the dashboard counted ``placed or
    pinned``, and the results dialog counted its own event list — and the status
    bar's ``total - pinned - placed`` could render a NEGATIVE number, because a
    class that is both pinned and ``placed=True`` was subtracted twice.

    **The register's own recommendation — "clamp/assert non-negative" — is the
    worst available fix.** On a state with 80 classes, 4 pins that also carry
    ``placed=True``, and 3 genuinely unplaced lessons, the old formula gives −1
    and the clamp gives **0**, while the truth is **3** — and those 3 are listed
    in the unplaced sidebar on the same screen. The clamp replaces an impossible
    number with a confidently wrong one. An ``assert`` is no better: it crashes
    the repaint on a file the grid can still draw.

    So the buckets are disjoint **by construction** rather than by trusting the
    "pinned implies not placed" invariant. That invariant is real but is held by
    caller convention at nine ``mark_placed`` sites and enforced nowhere, and no
    loader repairs a state that breaks it, so a ``.egu`` carrying it would
    render a negative forever.

    ``scheduled``               has a cell on the timetable: ``pinned or
                                placed`` — the same set as
                                :func:`get_placed_classes`. A pin carries its
                                position in ``pinned_*`` and ``apply_reschedule``
                                deliberately never calls ``mark_placed`` on one
                                (ST-SCHED-002), so pinned *is* scheduled. A pin
                                that clashes is scheduled too: it is committed
                                and it occupies the cell. Its problem is a
                                conflict to render (ST-UI-001), not a smaller
                                number.
    ``pinned_of_scheduled``     a SUBSET annotation, never a bucket of its own.
                                Rendering it as a peer segment is the other half
                                of the finding: ``4 sabit + 77 yerleşti + 3
                                yerleşmedi`` sums to 84 against 80 classes, and
                                users read a status bar as a partition.
    ``protected_of_scheduled``  the other subset annotation — a movement policy,
                                orthogonal to placement.
    ``off_grid_of_scheduled``   scheduled, but at a day or hour the grid no
                                longer has. Counted separately because it is the
                                one case where ``scheduled`` genuinely does not
                                equal what the grid draws: such a lesson is
                                drawn by nothing AND absent from the unplaced
                                panel, so without this the user reads "77
                                yerleşmiş" over a grid showing 75. It stays
                                inside ``scheduled`` — the user did place it,
                                and Phase 1 deliberately does not unplace
                                orphans at load — but it is now sayable.
    ``unscheduled``             ``total - scheduled``; identical to the
                                ``not placed and not pinned`` predicate the
                                unplaced panel and ``PlaceClassDialog`` use.

    ``0 <= unscheduled <= total``, ``pinned_of_scheduled <= scheduled`` and
    ``scheduled + unscheduled == total`` hold for **any** input, including a
    state that violates the invariant.

    Bracket access on ``classes``/``placed``/``pinned`` is deliberate, and both
    flags are read *unconditionally*: the property is **never quieter than the
    grid**. A malformed class dict must not raise in ``get_placed_classes`` —
    which the renderer iterates — while being counted silently here, because
    that is the Phase 1 lesson ("making a reader total converts a crash into a
    silent drop") in a place where the crash is the honest outcome.

    Not "byte-identical to ``get_placed_classes``", which is unachievable and
    was claimed here in error: this function legitimately needs ``pinned`` even
    when ``placed`` is truthy, and needs the effective day and slot, none of
    which that function reads. ``protection`` is the one tolerant read, because
    that key genuinely has a default.
    """
    days = set(state.get("days") or [])
    slots = set(state.get("slots") or [])
    total = scheduled = pinned_of = protected_of = off_grid_of = 0
    for cls in state["classes"]:
        total += 1
        # BOTH read unconditionally. `cls["pinned"] or cls["placed"]` would
        # short-circuit on a pinned class and never touch "placed", so a class
        # dict missing that key would raise in get_placed_classes — which the
        # grid iterates — and be counted silently here. The property that
        # matters is not "identical to get_placed_classes" (unachievable: this
        # function legitimately needs `pinned` and the effective day/slot,
        # which that one never reads) but NEVER QUIETER THAN THE GRID.
        is_placed = cls["placed"]
        is_pinned = cls["pinned"]
        if not (is_placed or is_pinned):
            continue
        scheduled += 1
        if is_pinned:
            pinned_of += 1
        elif cls.get("protection", PROTECTION_NONE) != PROTECTION_NONE:
            protected_of += 1
        if effective_day(cls) not in days or effective_time(cls) not in slots:
            off_grid_of += 1
    return {
        "total": total,
        "scheduled": scheduled,
        "pinned_of_scheduled": pinned_of,
        "protected_of_scheduled": protected_of,
        "off_grid_of_scheduled": off_grid_of,
        "unscheduled": total - scheduled,
    }


def occupied_slots_of(state, cls):
    if cls["pinned"]:
        day, start = cls["pinned_day"], cls["pinned_time"]
    elif cls["placed"]:
        day, start = cls["placed_day"], cls["placed_time"]
    else:
        return []
    td = total_duration(cls)
    return [(day, s) for s in get_consecutive_slots(state, start, td)]


def target_for_slot_offset(cls, offset):
    """For a non-joint class, return which target index owns a given slot offset.

    For joint classes, all slots belong to all targets (returns None).
    """
    if cls.get("joint_session", True) or len(cls.get("targets", [])) <= 1:
        return None
    dur = cls["duration"]
    target_idx = offset // dur
    if target_idx < len(cls["targets"]):
        return target_idx
    return len(cls["targets"]) - 1


def classroom_of(cls):
    """Return the display room/location for a placed class.

    For face-to-face classes, returns the physical classroom.
    For online/office classes, returns the virtual location label.
    """
    return display_room(cls)


def targets_overlap(targets_a, targets_b):
    for ta in targets_a:
        for tb in targets_b:
            if ta["year"] == tb["year"] and ta["branch"] == tb["branch"]:
                return True
    return False


def _active_targets(cls, slot_offset):
    """Return the targets active at *slot_offset* within a placed class block.

    For joint (or single-target) classes every slot covers all targets.
    For non-joint classes each sub-block covers one target only.
    """
    if not cls.get("joint_session", True) and len(cls.get("targets", [])) > 1:
        tidx = target_for_slot_offset(cls, slot_offset)
        if tidx is not None:
            return [cls["targets"][tidx]]
    return cls["targets"]


def _detect_occupancy_conflicts(state, candidate, day, start_slot, classroom):
    """Core conflict detection between candidate and all placed classes.

    Yields (existing, slot_name, conflict_type) tuples where conflict_type is
    one of 'room', 'lecturer', or 'target'. This is the single source of truth
    for occupancy conflict detection, used by find_conflicts() and by
    find_schedule_conflicts().
    """
    td = total_duration(candidate)
    if not slots_fit(state, start_slot, td):
        return
    needed_slots = get_consecutive_slots(state, start_slot, td)
    cand_start_idx = slot_index(state, start_slot)

    for existing in get_placed_classes(state):
        if existing is candidate:
            continue
        ex_room = classroom_of(existing)
        ex_occ = occupied_slots_of(state, existing)
        ex_start = effective_time(existing)
        # An existing placement can reference a slot/day the user has since
        # deleted. It occupies no real cell, so it blocks nothing — skip it
        # rather than crashing the drag-and-drop room picker (ST-DATA-003).
        ex_start_idx = find_slot_index(state, ex_start)
        if ex_start_idx is None or effective_day(existing) not in state["days"]:
            continue

        for i, ns in enumerate(needed_slots):
            if (day, ns) not in ex_occ:
                continue
            # Room conflicts only apply between two face-to-face classes
            if (ex_room == classroom
                    and needs_physical_room(candidate)
                    and needs_physical_room(existing)
                    and classroom is not None):
                yield existing, ns, 'room'
            if existing["lecturer"] == candidate["lecturer"]:
                yield existing, ns, 'lecturer'
            # Per-slot target overlap
            cand_targets = _active_targets(candidate, i)
            ns_idx = slot_index(state, ns)
            ex_offset = ns_idx - ex_start_idx
            ex_targets = _active_targets(existing, ex_offset)
            if targets_overlap(ex_targets, cand_targets):
                yield existing, ns, 'target'


def find_conflicts(state, candidate, day, start_slot, classroom):
    """Check occupancy conflicts (room, lecturer, student-group overlaps).

    NOTE: This does NOT check the class's own constraints (allowed/excluded
    days/times/rooms). For full validation, use
    ConstraintValidator.find_conflicts() from constraint_validator.py.
    """
    conflicts = []
    td = total_duration(candidate)
    display_day_value = display_day(day)
    if not slots_fit(state, start_slot, td):
        conflicts.append(tr("validation.duration_overflow"))
        return conflicts
    needed_slots = get_consecutive_slots(state, start_slot, td)

    # Check lecturer availability constraints for every needed slot
    lecturer = candidate.get("lecturer", "")
    if lecturer:
        for ns in needed_slots:
            if not lecturer_available_at(state, lecturer, day, ns):
                conflicts.append(
                    tr("validation.lecturer_unavailable").format(
                        lecturer, display_day_value, ns))

    for existing, ns, conflict_type in _detect_occupancy_conflicts(
            state, candidate, day, start_slot, classroom):
        if conflict_type == 'room':
            conflicts.append(
                tr("conflicts.room_occupied").format(
                    r=classroom, name=existing['name'], day=display_day_value, slot=ns))
        elif conflict_type == 'lecturer':
            conflicts.append(
                tr("conflicts.lecturer_busy").format(
                    lect=candidate['lecturer'], name=existing['name'], day=display_day_value, slot=ns))
        elif conflict_type == 'target':
            conflicts.append(
                tr("conflicts.student_overlap").format(
                    name=existing['name'], day=display_day_value, slot=ns))
    return conflicts


def find_valid_options(state, candidate):
    options = []
    days = filter_class_days(candidate, state["days"])
    times = filter_class_times(candidate, state["slots"])

    rooms = get_room_candidates(state, candidate)

    # Pre-filter days/times by lecturer availability using the canonical
    # apply_lecturer_availability_filters which enforces "excluded takes precedence".
    lecturer = candidate.get("lecturer", "")
    if lecturer:
        days, times = apply_lecturer_availability_filters(
            state, lecturer, days, times)

    for day in days:
        for slot in times:
            for room in rooms:
                if not find_conflicts(state, candidate, day, slot, room):
                    options.append((day, slot, room))
    return options


def find_schedule_conflicts(state):
    """Every hard occupancy conflict in the timetable **as it stands**.

    ST-UI-001. ``find_conflicts`` answers "would this candidate clash
    if I put it here?". This answers "what is already double-booked?" — the
    question the grid, the warning log and the exports need, and the only one an
    engine that deliberately commits an infeasible pin (ST-SCHED-002) can be
    asked without re-solving.

    Returns a list of dicts, ordered deterministically::

        {"a": cls, "b": cls, "day": day, "slot": slot, "kinds": (...)}

    ``(a, b)`` is ordered by ``cls_key`` so a pair is reported once, ``day``/
    ``slot`` is the first contested cell, and ``kinds`` is a sorted tuple drawn
    from ``'room'`` / ``'lecturer'`` / ``'target'``.

    The occupancy rules follow ``_detect_occupancy_conflicts``: a room clash
    only between two lessons that both need a physical room — two online
    lessons sharing an hour is normal and must not be reported — lecturer by
    name, and targets per slot offset via ``_active_targets``, so a non-joint
    class only blocks the group whose sub-block covers that hour.

    Four deliberate divergences from ``find_conflicts``, each because
    that function answers "may I put this candidate here?" and this one answers
    "what is already wrong?":

    1. **No lecturer-availability entry.** That function reports an unavailable
       lecturer as one more line in its list. This returns *pairs*, and "the
       lecturer is not available" is not a pair; it is reported by the
       negotiator and by the validator's reasons.
    2. **A blank lecturer is not a lecturer clash, and a blank room is not a
       room clash.** ``_detect_occupancy_conflicts`` compares
       ``existing["lecturer"] == candidate["lecturer"]`` with no truthiness
       guard, so two lessons that have *no* lecturer match each other. That is
       harmless when screening one candidate and wrong when reporting a defect
       to the user.
    3. **A block that overruns the end of the day is still scanned**, for the
       hours it does cover. ``find_conflicts`` reports the overflow and returns
       at once when ``slots_fit`` fails, scanning nothing. A lesson can end up
       overrunning after the user shortens the day in Setup, and it really does
       occupy — and double-book — the hours that remain.
    4. **A placement on a day the grid does not have is skipped.** It occupies
       no cell anyone can see, so reporting it would put a red conflict in the
       warning log for two lessons that are drawn nowhere. Those are reported
       as orphans by ``models.find_off_grid_placements`` instead, which is the
       one oracle for "not on the grid".
    """
    days = state.get("days", [])
    cells = {}
    for cls in get_placed_classes(state):
        # Divergence 4. occupied_slots_of deliberately does not filter by day
        # (an off-grid day still consumes real hours, which the CSV export must
        # keep reporting), so the day check belongs here.
        if effective_day(cls) not in days:
            continue
        room = display_room(cls)
        phys = needs_physical_room(cls)
        # occupied_slots_of slices state["slots"], so the enumerate index IS the
        # offset into the class's own block, and a block overrunning the end of
        # the day is simply shorter (divergence 3). A placement whose start slot
        # is off-grid yields [] and contributes nothing.
        for offset, (day, slot) in enumerate(occupied_slots_of(state, cls)):
            cells.setdefault((day, slot), []).append(
                (cls, room, phys, _active_targets(cls, offset)))

    pairs = {}
    for (day, slot), claims in cells.items():
        if len(claims) < 2:
            continue
        for i in range(len(claims)):
            cls_a, room_a, phys_a, tgt_a = claims[i]
            for j in range(i + 1, len(claims)):
                cls_b, room_b, phys_b, tgt_b = claims[j]
                kinds = set()
                if phys_a and phys_b and room_a and room_a == room_b:
                    kinds.add("room")
                if cls_a["lecturer"] and cls_a["lecturer"] == cls_b["lecturer"]:
                    kinds.add("lecturer")
                if targets_overlap(tgt_a, tgt_b):
                    kinds.add("target")
                if not kinds:
                    continue
                ka, kb = cls_key(cls_a), cls_key(cls_b)
                first, second = ((cls_a, cls_b) if ka <= kb else (cls_b, cls_a))
                key = (ka, kb) if ka <= kb else (kb, ka)
                rec = pairs.get(key)
                if rec is None:
                    pairs[key] = {"a": first, "b": second, "day": day,
                                  "slot": slot, "kinds": kinds}
                else:
                    rec["kinds"] |= kinds

    out = list(pairs.values())
    for rec in out:
        rec["kinds"] = tuple(sorted(rec["kinds"]))
    out.sort(key=lambda r: (
        days.index(r["day"]), r["slot"], cls_key(r["a"]), cls_key(r["b"])))
    return out


def conflict_partner_index(conflicts):
    """``{cls_key: frozenset(cls_key, ...)}`` from *conflicts*.

    The renderer needs "is this lesson in any conflict, and with what?" per
    block; recomputing that from the pair list once per block would be
    quadratic in a view that already draws every lesson.
    """
    index = {}
    for rec in conflicts:
        ka, kb = cls_key(rec["a"]), cls_key(rec["b"])
        index.setdefault(ka, set()).add(kb)
        index.setdefault(kb, set()).add(ka)
    return {k: frozenset(v) for k, v in index.items()}


SLOT_ERROR_DUPLICATE = "duplicate"


def parse_slot_lines(text):
    """Parse the Setup time-slot box into ``(slots, problems)``.

    ST-UI-021. The grid is **ordinal**: nothing in the package parses a slot as
    a time (``grep`` for ``strptime`` / ``%H:%M`` / ``split(":")`` over
    ``scheduler_app/`` returns zero hits), duration is counted in *rows*, and
    ``"1. Ders"``, ``"08:00-08:45"`` and ``"Öğle Arası"`` all work end to end —
    placement, occupancy, spanning, export. So a format rule would reject
    setups the engine handles perfectly, and there is exactly **one** hard
    requirement:

    **Uniqueness.** Every lookup is ``list.index()``, which returns the first
    match, so a repeated label makes every later row with that name permanently
    unreachable. Measured on a four-hour day with ``09:00`` typed twice: the
    grid draws 4 rows, only 3 can ever hold a lesson, and 3 of 4 classes place.
    ``reconcile_placements`` sees nothing wrong, because it is a membership test
    and every label is still a member.

    ``problems`` is a list of ``(line_number, kind, text)`` with 1-based line
    numbers, so the dialog can point at the line rather than describing it.

    Deliberately does NOT deduplicate. Dropping the second ``09:00`` shortens
    the grid by a row, which silently re-points every multi-row lesson below it
    and can push one off the end entirely — a silent repair of a silent
    corruption. The duplicate is refused and named instead.
    """
    slots = []
    problems = []
    seen = {}
    for lineno, raw in enumerate(text.split("\n"), start=1):
        value = raw.strip()
        if not value:
            # A line that is empty after stripping is simply skipped. The
            # branch that used to report SLOT_ERROR_BLANK here was
            # unreachable: `not value` means `raw` is empty or all
            # whitespace, and `raw and not raw.isspace()` is false for both.
            continue
        if value in seen:
            problems.append((lineno, SLOT_ERROR_DUPLICATE, value))
            continue
        seen[value] = lineno
        slots.append(value)
    return slots, problems


def slot_meaning_changes(state, new_slots):
    """Placed/pinned classes whose covered cells change under *new_slots*.

    ST-UI-021. A slot label is a **by-name reference into an ordered list**, so
    editing that list can silently change which hours an existing lesson
    occupies without changing anything about the lesson. Nothing catches it:
    ``reconcile_placements`` is a membership test, and every label is still a
    member.

    Measured on a clean schedule — ``["09:00","10:00","11:00","12:00","1. Ara",
    "13:00"]`` with a 2-hour lesson at 12:00 and a 1-hour lesson at 13:00, zero
    oracle violations. Sorting the list alone produces **6 hard violations**
    (a double-booked room and a lecturer in two places), because the 2-hour
    lesson stops covering ``["12:00", "1. Ara"]`` and starts covering
    ``["12:00", "13:00"]``. ``reconcile_placements`` returns ``[]``.

    This compares the thing the engine actually defines — the tuple of cells a
    class covers — rather than trying to recognise the *edits* that are
    dangerous. A reorder, a mid-list substitution (``"09:00"`` replaced by
    ``"08:30"`` between two untouched neighbours) and an insertion are all the
    same defect seen three ways, and an edit-shaped detector catches only the
    ones someone thought of.

    Returns ``[(cls, before_cells, after_cells)]``. **Pinned classes are
    included**: ``pinned_time`` is the same kind of by-name reference as
    ``placed_time``, and ``validate_placements_after_edit`` skips pins — so
    leaving them out means a reorder silently moves the one thing the user
    explicitly said must not move (ST-SCHED-002).
    """
    after = dict(state)
    after["slots"] = list(new_slots)
    changed = []
    for cls in get_placed_classes(state):
        start = effective_time(cls)
        td = total_duration(cls)
        before_cells = tuple(get_consecutive_slots(state, start, td))
        after_cells = tuple(get_consecutive_slots(after, start, td))
        if before_cells != after_cells:
            changed.append((cls, before_cells, after_cells))
    return changed


def _sweep_lanes(entries):
    """Greedy interval-graph lane assignment. Sets ``entry['lane']``; returns peak.

    Entries need ``row``, ``end_row``, ``span`` and ``order``. Lifted verbatim
    out of :func:`build_virtual_classroom_day_layout` so the default filtered
    view can reuse the *algorithm* without inheriting that function's day-wide
    subcolumn packaging.
    """
    ordered = sorted(entries, key=lambda e: (e["row"], -e["span"], e["order"]))
    active = []
    peak = 0
    for entry in ordered:
        active = [item for item in active if item["end_row"] > entry["row"]]
        used_lanes = {item["lane"] for item in active}
        lane = 0
        while lane in used_lanes:
            lane += 1
        entry["lane"] = lane
        active.append(entry)
        peak = max(peak, lane + 1)
    return peak


def assign_component_lanes(entries):
    """Lane each connected run of overlapping *entries* independently.

    Sets ``lane`` and ``lane_count`` in place.

    The difference from :func:`build_virtual_classroom_day_layout` is the whole
    point. That function gives a whole DAY one lane count, which is right for
    the online view — concurrency is normal there and the user expects wide
    days — and wrong for a room timetable, where one collision at Monday 09:00
    would halve the width of every other Monday hour and put a second empty
    drop target in each of them. Here only the rows that are actually contested
    are split; an uncontested lesson keeps the full column.
    """
    if not entries:
        return
    _sweep_lanes(entries)
    ordered = sorted(entries, key=lambda e: (e["row"], -e["span"], e["order"]))
    component, comp_end, components = [], -1, []
    for entry in ordered:
        if component and entry["row"] >= comp_end:
            components.append(component)
            component = []
        component.append(entry)
        comp_end = max(comp_end, entry["end_row"])
    if component:
        components.append(component)
    for group in components:
        n = max(e["lane"] for e in group) + 1
        for e in group:
            e["lane_count"] = n


# ═══════════════════════════════════════════════════════════════════════
#  (removed)  ST-ARCH-011  --  the cascade-relocation cluster
# ------------------------------------------------------------------
# `_unplace`, `_find_candidate_slots` and `cascade_relocate` lived here.
# Both search functions called `find_conflicting_classes`, which Phase 6
# deleted along with the rest of the original solver family -- so every
# path through this cluster raised `NameError` on its first call. Nothing
# invoked it: `cascade_relocate` had no caller in `scheduler_app/`, in
# `tests/` or in `stress-test/`, and `_find_candidate_slots` was reached
# only from inside it.
#
# It is the inverse of the case `tests/test_written_but_unwired.py` pins.
# There, a helper with no callers was a fix someone forgot to wire, and
# wiring it repaired a defect. Here, wiring any of this crashes, so it was
# never the fix it looked like -- a pinned-class cascade the app has not
# owned since Phase 6 replaced pinning with `optimized_auto_place`.
# `tests/test_calls_resolve.py` makes a third one impossible to leave
# behind.


def get_year_color(state, year_name):
    years = sorted(state["years"].keys())
    if year_name in years:
        return YEAR_COLORS[years.index(year_name) % len(YEAR_COLORS)]
    return YEAR_COLORS[0]


def occ_claim(occ, key, entity):
    """Register one claim on ``entity`` at cell ``key``.

    ST-SCHED-010. An occupancy cell used to be a ``set``, so two classes
    contributing the same claim to one cell (two lecturer-less classes; a
    locked class registered by both ``build_occupancy`` and the optimizer's
    explicit add loop; two infeasibly-pinned classes sharing a room) collapsed
    into one entry — and removing *either* of them erased the claim of the
    other, after which the validator declared an occupied cell free. Cells are
    now ``{entity: refcount}``; a cell is occupied while its count is positive.

    Every reader in the codebase tests membership (``x in occ.get(key, set())``)
    or takes ``set(cell)`` / ``bool(cell)``, all of which read a dict exactly as
    they read a set, so this changes representation without changing any read.
    """
    cell = occ.get(key)
    if cell is None:
        occ[key] = {entity: 1}
    else:
        cell[entity] = cell.get(entity, 0) + 1


def occ_release(occ, key, entity):
    """Drop one claim on ``entity`` at cell ``key``; free it at zero.

    The entry is deleted rather than left at 0 so that ``bool(cell)`` and
    ``set(cell)`` keep meaning "what is occupying this cell", which is what
    ``tests/test_state_transactions.py``'s occupancy fingerprint and the
    negotiator's blocked-slot analysis both rely on.

    A release with no matching claim is ignored rather than driven negative:
    the alternative is that one unbalanced remove poisons the cell into a
    permanently-negative count that no future claim can lift back to occupied.
    """
    cell = occ.get(key)
    if not cell:
        return
    n = cell.get(entity)
    if n is None:
        return
    if n <= 1:
        del cell[entity]
        if not cell:
            del occ[key]
    else:
        cell[entity] = n - 1


def build_occupancy(state, exclude_ids=None):
    """Build occupancy maps for rooms, lecturers, and student groups.

    Returns (room_occ, lect_occ, group_occ) where each is a dict mapping
    (day, slot) -> {identifier: refcount}. See ``occ_claim`` for why the cell
    is ref-counted rather than a plain set (ST-SCHED-010).
    """
    exclude_ids = exclude_ids or set()
    room_occ = {}
    lect_occ = {}
    group_occ = {}
    for cls in get_placed_classes(state):
        if cls_key(cls) in exclude_ids:
            continue
        day = effective_day(cls)
        start = effective_time(cls)
        room = classroom_of(cls)
        td = total_duration(cls)
        start_idx = find_slot_index(state, start)
        if start_idx is None or day not in state["days"]:
            continue  # orphaned placement — occupies no cell on this grid
        slots_list = state["slots"][start_idx:start_idx + td]
        track_room = needs_physical_room(cls) and room is not None
        for off, s in enumerate(slots_list):
            key = (day, s)
            if track_room:
                occ_claim(room_occ, key, room)
            occ_claim(lect_occ, key, cls["lecturer"])
            for t in _active_targets(cls, off):
                occ_claim(group_occ, key, (t["year"], t["branch"]))
    return room_occ, lect_occ, group_occ


def _compactness_gap(state, day, slot_idx, entity_key, occ_map, entity_getter):
    """Calculate the gap penalty for placing an entity at a given slot on a day.

    Measures how many idle slots would exist between this placement and the
    entity's other occupied slots on the same day. Lower gaps = more compact.

    Returns the number of gap slots between the nearest existing occupied slot
    and the new placement (0 if adjacent or no other slots on that day).
    """
    all_slots = state["slots"]
    # Find all slot indices where this entity is already busy on this day
    occupied_indices = []
    for si, s in enumerate(all_slots):
        key = (day, s)
        entries = occ_map.get(key, set())
        if entity_key in entries:
            occupied_indices.append(si)
    if not occupied_indices:
        return 0  # First class on this day — no gap
    # Add the candidate slot index
    # Calculate min distance to any existing occupied slot
    min_dist = min(abs(slot_idx - oi) for oi in occupied_indices)
    # Gap = distance - 1 (adjacent slots have 0 gap)
    return max(0, min_dist - 1)


# ══════════════════════════════════════════════════════════════════════════
#  (removed)  ST-ARCH-011
# ------------------------------------------------------------------
# The app's original solver family -- `batch_schedule`,
# `auto_place_class`, `reschedule_all` and the `_solve_backtrack` /
# `_get_valid_slots` / `_check_placement_fast` helpers behind them -- used to
# live here. Phase 3 found they enforced a weaker rule set than the optimized
# path (ST-SCHED-007: they placed a fully-unavailable lecturer at monday/09:00
# and relocated a `protection="locked"` class) and made each entry point a
# one-line forward rather than teaching the old rules the missing checks.
# Phase 6 removes the code. Nothing imported them but one unused import in
# ui/dialogs.py; `optimized_batch_schedule`, `optimized_auto_place` and
# `optimized_reschedule_all` are the entry points, and always were.
#
# ST-ARCH-011 / Phase 7: `_unplaced_reason` went with them. It sat here on its
# own, calling the `_get_valid_slots` named above and therefore raising
# `NameError` on its first line, and nothing called it. Its live twin is
# `CandidateGenerator.unplaced_reason` in `core/candidate_generator.py`, which
# returns the same six `negotiation.*` strings from the same six branches --
# so this copy was a duplicate that could not run, not a behaviour anyone lost.


def lighten_color(hex_color, factor=0.45):
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    r = min(255, int(r + (255 - r) * factor))
    g = min(255, int(g + (255 - g) * factor))
    b = min(255, int(b + (255 - b) * factor))
    return f"#{r:02x}{g:02x}{b:02x}"


# ══════════════════════════════════════════════════════════════════════════
#  (moved)  ST-ARCH-010 — the AI-assisted optimization bridge
# ------------------------------------------------------------------
# `optimized_auto_place`, `optimized_reschedule_all`,
# `optimized_batch_schedule`, `score_placement`, `score_placement_explained`,
# `analyze_schedule`, `negotiate_after_optimization` and
# `apply_negotiation_suggestion` now live in `scheduler_app/core/facade.py`.
# They held all 13 of this module's function-level deferred imports, and they
# were the reason 15 of `core`'s modules formed one strongly connected
# component: `logic` supplies the primitives the engine imports at module
# scope, so it could not import the engine back except from inside a function.
# The facade sits above both and imports whatever it needs normally.
#
# Do NOT re-export those eight names from here "for compatibility". Both ways
# of doing it were built and measured: a star re-export turns the cycle into a
# module-level one and `import scheduler_app.core.workflow` raises ImportError;
# a lazy PEP 562 `__getattr__` runs fine and puts the component at 16 modules,
# larger than the 15 the split removed. `tests/test_import_layering.py` fails
# on either.
