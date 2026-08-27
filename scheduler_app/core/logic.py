"""Scheduling logic: conflict detection, slot fitting, color helpers."""

from scheduler_app.constants import YEAR_COLORS
from scheduler_app.models import (
    DEFAULT_OPTIMIZER_SEED,
    room_fits_class, lecturer_available_at, needs_physical_room, display_room,
    get_room_candidates, get_physical_room_candidates,
    effective_day, effective_time, effective_room,
    mark_placed, mark_unplaced,
    filter_class_days, filter_class_times,
    apply_lecturer_availability_filters,
    cls_key,
)
from scheduler_app.translations import tr
from scheduler_app.ui.day_keys import display_day


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

    lane_counts = {}
    for day in days:
        ordered = sorted(
            day_entries[day],
            key=lambda entry: (entry["row"], -entry["span"], entry["order"]),
        )
        active = []
        peak_lane_count = 0
        for entry in ordered:
            active = [item for item in active if item["end_row"] > entry["row"]]
            used_lanes = {item["lane"] for item in active}
            lane = 0
            while lane in used_lanes:
                lane += 1
            entry["lane"] = lane
            active.append(entry)
            peak_lane_count = max(peak_lane_count, lane + 1)
        lane_counts[day] = max(1, peak_lane_count)

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
    for occupancy conflict detection, used by both find_conflicts() and
    find_conflicting_classes().
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


def respects_constraints(candidate, day, slot, room, state=None):
    """Check if (day, slot, room) satisfies the class's own constraints.

    .. deprecated::
        Use ``ConstraintValidator.respects_constraints()`` instead, which
        also checks room capacity and lecturer availability.
    """
    if candidate["allowed_days"] and day not in candidate["allowed_days"]:
        return False
    if candidate.get("excluded_days") and day in candidate["excluded_days"]:
        return False
    if candidate["allowed_times"] and slot not in candidate["allowed_times"]:
        return False
    if candidate.get("excluded_times") and slot in candidate["excluded_times"]:
        return False
    # Classroom constraints only apply to face-to-face classes
    if needs_physical_room(candidate):
        if candidate["required_classrooms"] and room not in candidate["required_classrooms"]:
            return False
        if candidate["excluded_classrooms"] and room in candidate["excluded_classrooms"]:
            return False
        if state is not None and not room_fits_class(state, room, candidate):
            return False
    return True


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


def find_conflicting_classes(state, candidate, day, start_slot, classroom):
    """Return placed/pinned classes that conflict with *candidate*
    if it were placed at (day, start_slot, classroom).

    Checks room, lecturer, target, AND lecturer availability conflicts.
    """
    conflicting = set()
    td = total_duration(candidate)
    if not slots_fit(state, start_slot, td):
        return []

    # Occupancy conflicts via shared detection
    for existing, ns, conflict_type in _detect_occupancy_conflicts(
            state, candidate, day, start_slot, classroom):
        conflicting.add(cls_key(existing))

    # Lecturer availability: if lecturer is unavailable at any needed slot,
    # mark the candidate itself as conflicting (no existing class to blame,
    # but caller should know placement is invalid).
    lecturer = candidate.get("lecturer", "")
    if lecturer:
        needed_slots = get_consecutive_slots(state, start_slot, td)
        for ns in needed_slots:
            if not lecturer_available_at(state, lecturer, day, ns):
                # Lecturer unavailable — add sentinel to signal infeasibility
                conflicting.add(cls_key(candidate))
                break

    # Return actual class dicts (preserving identity)
    result = [c for c in get_placed_classes(state) if cls_key(c) in conflicting]
    # Include candidate itself if lecturer unavailability was detected
    if cls_key(candidate) in conflicting and candidate not in result:
        result.append(candidate)
    return result


def _unplace(cls):
    """Remove placement from a class (does not touch pinned fields)."""
    mark_unplaced(cls)


def _find_candidate_slots(state, cls, visited_ids):
    """Find candidate (day, slot, room) tuples for *cls* respecting its constraints.

    Returns a list of (day, slot, room, conflicting_classes) tuples sorted so
    conflict-free slots come first, then slots with fewer displaceable conflicts.
    Slots that conflict with pinned or already-visited classes are excluded.
    """
    days = filter_class_days(cls, state["days"])
    times = filter_class_times(cls, state["slots"])
    rooms = get_room_candidates(state, cls)

    candidates = []
    for day in days:
        for slot in times:
            for room in rooms:
                blockers = find_conflicting_classes(state, cls, day, slot, room)
                # Skip if any blocker is pinned or already being relocated
                if any(b["pinned"] or cls_key(b) in visited_ids for b in blockers):
                    continue
                # Also skip if duration overflows
                if not slots_fit(state, slot, total_duration(cls)):
                    continue
                candidates.append((day, slot, room, blockers))
    # Prefer conflict-free, then fewest conflicts
    candidates.sort(key=lambda c: len(c[3]))
    return candidates


def cascade_relocate(state, new_cls):
    """Place *new_cls* (must be pinned) and cascade-relocate any displaced classes.

    Returns (success: bool, relocations: list[dict]) where each relocation dict
    has keys: cls, old_day, old_time, old_room, new_day, new_time, new_room.
    On failure the state is fully rolled back.
    """
    day = new_cls["pinned_day"]
    time_ = new_cls["pinned_time"]
    room = new_cls["pinned_classroom"]

    # Find who currently conflicts with the new pinned class
    displaced = find_conflicting_classes(state, new_cls, day, time_, room)

    # Pinned classes cannot be displaced
    for d in displaced:
        if d["pinned"]:
            return False, []

    # Save original placements so we can roll back
    snapshots = {}
    for d in displaced:
        snapshots[cls_key(d)] = (d["placed"], d["placed_day"],
                            d["placed_time"], d["placed_classroom"])

    # Unplace all displaced classes so they don't block each other's search
    for d in displaced:
        _unplace(d)

    relocations = []
    queue = list(displaced)
    visited = {cls_key(c) for c in queue}  # prevent infinite loops

    success = True
    while queue:
        cls = queue.pop(0)
        candidates = _find_candidate_slots(state, cls, visited)
        if not candidates:
            success = False
            break

        # Pick best candidate (fewest new displacements, conflict-free first)
        chosen_day, chosen_slot, chosen_room, newly_displaced = candidates[0]

        # Save snapshots and unplace newly displaced classes
        for nd in newly_displaced:
            if cls_key(nd) not in snapshots:
                snapshots[cls_key(nd)] = (nd["placed"], nd["placed_day"],
                                     nd["placed_time"], nd["placed_classroom"])
            _unplace(nd)
            visited.add(cls_key(nd))
            queue.append(nd)

        # Place the current displaced class in its new slot
        old = snapshots[cls_key(cls)]
        mark_placed(cls, chosen_day, chosen_slot, chosen_room)
        relocations.append({
            "cls": cls,
            "old_day": old[1], "old_time": old[2], "old_room": old[3],
            "new_day": chosen_day, "new_time": chosen_slot, "new_room": chosen_room,
        })

    if not success:
        # Roll back all changes
        for cid, (placed, pday, ptime, proom) in snapshots.items():
            for c in state["classes"]:
                if cls_key(c) == cid:
                    c["placed"] = placed
                    c["placed_day"] = pday
                    c["placed_time"] = ptime
                    c["placed_classroom"] = proom
                    break
        return False, []

    return True, relocations


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


def _check_placement_fast(state, cls, day, start_slot, room,
                          room_occ, lect_occ, group_occ):
    """Fast conflict check using pre-built occupancy maps. Returns True if OK."""
    td = total_duration(cls)
    si = slot_index(state, start_slot)
    if si + td > len(state["slots"]):
        return False
    check_room = needs_physical_room(cls) and room is not None
    slots_list = state["slots"][si:si + td]
    for off, s in enumerate(slots_list):
        key = (day, s)
        if check_room and room in room_occ.get(key, set()):
            return False
        if cls["lecturer"] in lect_occ.get(key, set()):
            return False
        active = _active_targets(cls, off)
        for t in active:
            if (t["year"], t["branch"]) in group_occ.get(key, set()):
                return False
    return True


def _add_to_occupancy(state, cls, day, start_slot, room,
                      room_occ, lect_occ, group_occ):
    """Add a class placement to occupancy maps."""
    td = total_duration(cls)
    si = slot_index(state, start_slot)
    slots_list = state["slots"][si:si + td]
    track_room = needs_physical_room(cls) and room is not None
    for off, s in enumerate(slots_list):
        key = (day, s)
        if track_room:
            occ_claim(room_occ, key, room)
        occ_claim(lect_occ, key, cls["lecturer"])
        for t in _active_targets(cls, off):
            occ_claim(group_occ, key, (t["year"], t["branch"]))


def _remove_from_occupancy(state, cls, day, start_slot, room,
                           room_occ, lect_occ, group_occ):
    """Remove a class placement from occupancy maps."""
    td = total_duration(cls)
    si = slot_index(state, start_slot)
    slots_list = state["slots"][si:si + td]
    track_room = needs_physical_room(cls) and room is not None
    for off, s in enumerate(slots_list):
        key = (day, s)
        if track_room:
            occ_release(room_occ, key, room)
        occ_release(lect_occ, key, cls["lecturer"])
        for t in _active_targets(cls, off):
            occ_release(group_occ, key, (t["year"], t["branch"]))


def _get_valid_slots(state, cls, room_occ, lect_occ, group_occ):
    """Get all valid (day, slot, room) for cls respecting constraints + occupancy."""
    days = filter_class_days(cls, state["days"])
    times = filter_class_times(cls, state["slots"])
    rooms = get_room_candidates(state, cls)
    options = []
    for day in days:
        for slot in times:
            for room in rooms:
                if _check_placement_fast(state, cls, day, slot, room,
                                         room_occ, lect_occ, group_occ):
                    options.append((day, slot, room))
    return options


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


def _score_placement(state, cls, day, slot, room, room_occ, lect_occ, group_occ):
    """Score a placement: lower is better.

    Priorities (highest to lowest weight):
    1. Lecturer compactness — minimize gaps between a lecturer's classes on same day
    2. Student group compactness — minimize gaps for student groups on same day
    3. Structural preference — earlier days/slots for tidiness
    """
    score = 0.0
    day_idx = state["days"].index(day) if day in state["days"] else 0
    si = slot_index(state, slot)
    td = total_duration(cls)

    # Hard penalty: conflict slots (shouldn't happen if filtered, but safety)
    for off in range(td):
        if si + off < len(state["slots"]):
            key = (day, state["slots"][si + off])
            if cls["lecturer"] in lect_occ.get(key, set()):
                score += 100

    # ── Lecturer compactness (high priority, weight ~5.0 per gap slot) ──
    # For each slot the class occupies, measure gap to nearest lecturer slot
    lect_key = cls["lecturer"]
    if lect_key:
        lect_gap = _compactness_gap(state, day, si, lect_key, lect_occ, None)
        score += lect_gap * 5.0
        # Bonus: prefer days where the lecturer already has classes (encourages grouping)
        lect_on_day = sum(1 for s in state["slots"]
                          if lect_key in lect_occ.get((day, s), set()))
        if lect_on_day > 0:
            score -= 2.0  # Reward clustering on same day

    # ── Student group compactness (medium priority, weight ~2.0 per gap slot) ──
    for t in cls["targets"]:
        grp_key = (t["year"], t["branch"])
        grp_gap = _compactness_gap(state, day, si, grp_key, group_occ, None)
        score += grp_gap * 2.0
        # Bonus for days where group already has classes
        grp_on_day = sum(1 for s in state["slots"]
                         if grp_key in group_occ.get((day, s), set()))
        if grp_on_day > 0:
            score -= 1.0

    # ── Spread across days (light penalty for overloading a single day) ──
    lect_day_load = sum(1 for s in state["slots"]
                        if lect_key in lect_occ.get((day, s), set())) if lect_key else 0
    if lect_day_load > len(state["slots"]) * 0.6:
        score += 1.5  # Discourage packing too many on one day

    # ── Structural preference (low priority) ──
    score += day_idx * 0.05 + si * 0.01

    return score


def _constraint_tightness(state, cls):
    """Estimate how constrained a class is (lower = tighter = schedule first)."""
    days = filter_class_days(cls, state["days"])
    times = filter_class_times(cls, state["slots"])
    # Apply lecturer availability (consistent with constraint_validator)
    days, times = apply_lecturer_availability_filters(
        state, cls.get("lecturer", ""), days, times)
    rooms = get_room_candidates(state, cls)
    td = total_duration(cls)
    valid_time_count = sum(1 for s in times
                           if slot_index(state, s) + td <= len(state["slots"]))
    return len(days) * valid_time_count * len(rooms)


def _solve_backtrack(state, flexible, room_occ, lect_occ, group_occ,
                     max_iterations=50000, preferred=None):
    """Core backtracking solver.

    Args:
        flexible: List of classes to place.
        room_occ/lect_occ/group_occ: Occupancy maps (pinned classes pre-loaded).
        max_iterations: Iteration cap to avoid hanging.
        preferred: Optional dict {id(cls): (day, slot, room)} of preferred
                   placements to try first (used to preserve existing positions).

    Returns:
        List of (day, slot, room) or None per class in flexible.
    """
    preferred = preferred or {}
    n = len(flexible)
    assignments = [None] * n
    best_solution = [None]
    best_count = [0]
    iterations = [0]

    def solve(idx):
        if iterations[0] >= max_iterations:
            return False
        iterations[0] += 1

        if idx == n:
            placed_count = sum(1 for a in assignments if a is not None)
            if placed_count > best_count[0]:
                best_count[0] = placed_count
                best_solution[0] = list(assignments)
            return placed_count == n

        cls = flexible[idx]
        options = _get_valid_slots(state, cls, room_occ, lect_occ, group_occ)

        # Score and sort options
        scored = [(opt, _score_placement(state, cls, opt[0], opt[1], opt[2],
                                          room_occ, lect_occ, group_occ))
                  for opt in options]
        scored.sort(key=lambda x: x[1])

        # If this class has a preferred slot, try it first
        pref = preferred.get(cls_key(cls))
        if pref and pref in options:
            # Move preferred to front with best score
            scored = [(pref, -1)] + [(o, s) for o, s in scored if o != pref]

        for (day, slot, room), _ in scored:
            assignments[idx] = (day, slot, room)
            _add_to_occupancy(state, cls, day, slot, room,
                              room_occ, lect_occ, group_occ)

            if solve(idx + 1):
                return True

            _remove_from_occupancy(state, cls, day, slot, room,
                                    room_occ, lect_occ, group_occ)
            assignments[idx] = None

        # Try skipping this class (leave unplaced) — still try remaining
        if solve(idx + 1):
            return True

        return False

    solve(0)
    return best_solution[0] if best_solution[0] else [None] * n


def _unplaced_reason(state, cls, room_occ, lect_occ, group_occ):
    """Determine why a class couldn't be placed."""
    options = _get_valid_slots(state, cls, room_occ, lect_occ, group_occ)
    if options:
        return tr("negotiation.batch_displacement_required")
    all_days = filter_class_days(cls, state["days"])
    all_times = filter_class_times(cls, state["slots"])
    if needs_physical_room(cls):
        all_rooms = get_physical_room_candidates(state, cls, apply_capacity=False)
        rooms_before_cap = list(all_rooms)
        all_rooms = get_physical_room_candidates(state, cls)
        if not all_rooms:
            if rooms_before_cap:
                return tr("negotiation.no_room_capacity")
            return tr("negotiation.no_matching_classrooms")
    else:
        all_rooms = [None]
    if not all_days:
        return tr("negotiation.no_allowed_days_configured")
    if not all_times:
        return tr("negotiation.no_allowed_times_configured")
    return tr("negotiation.all_slots_occupied")


# ══════════════════════════════════════════════════════════════════════════
#  LEGACY SOLVER ENTRY POINTS  (ST-SCHED-007, ST-ARCH-004, ST-ARCH-011)
# ══════════════════════════════════════════════════════════════════════════
# These three names are the app's original solver. They have no live caller —
# `batch_schedule` is imported by ui/dialogs.py:28 and never invoked — but they
# are exported and importable, so they are a loaded gun: re-wire any of them and
# you get a solver that silently enforces a different, weaker rule set.
#
# The audit measured exactly that (ST-SCHED-007). Driving a fully-unavailable
# lecturer through all three placed the class at monday/09:00, because their
# shared candidate generator `_get_valid_slots` never applied the lecturer
# availability filters at all; and `reschedule_all` treated every non-pinned
# placed class as movable, so it relocated a `protection="locked"` class from
# friday/11:00 to monday/09:00. The optimized path got both right.
#
# Rather than teach the old rules the missing checks — which would have created
# a fifth copy of the constraint logic, the very thing ST-ARCH-004 is about —
# each entry point now forwards to its optimized counterpart, which validates
# through ConstraintValidator like everything else. Return signatures are
# unchanged; `optimized_auto_place` and `optimized_batch_schedule` already
# documented themselves as drop-in replacements.
#
# Deleting them outright, along with the now-unreferenced `_solve_backtrack` /
# `_get_valid_slots` / `_check_placement_fast` helpers, is ST-ARCH-011's job in
# Phase 6. This phase closes the behavioural hole; that one removes the code.


def batch_schedule(state, new_classes):
    """Deprecated. Forwards to :func:`optimized_batch_schedule`.

    .. deprecated::
        Kept only so existing imports resolve. Call
        ``optimized_batch_schedule`` directly.

    Returns (placed_list, unplaced_list, rescheduled) — unchanged.
    """
    return optimized_batch_schedule(state, new_classes)


def auto_place_class(state, new_cls):
    """Deprecated. Forwards to :func:`optimized_auto_place`.

    .. deprecated::
        Kept only so existing imports resolve. Call ``optimized_auto_place``
        directly.

    Returns (success, placements, rescheduled) — unchanged.
    """
    return optimized_auto_place(state, new_cls)


def reschedule_all(state):
    """Deprecated. Forwards to :func:`optimized_reschedule_all`.

    .. deprecated::
        Kept only so existing imports resolve. Call
        ``optimized_reschedule_all`` directly — it also returns a summary,
        which this signature has no room for.

    Returns (placed_list, unplaced_list, changes) — unchanged.
    """
    placed, unplaced, changes, _summary = optimized_reschedule_all(state)
    return placed, unplaced, changes


def lighten_color(hex_color, factor=0.45):
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    r = min(255, int(r + (255 - r) * factor))
    g = min(255, int(g + (255 - g) * factor))
    b = min(255, int(b + (255 - b) * factor))
    return f"#{r:02x}{g:02x}{b:02x}"


# ══════════════════════════════════════════════════════════════════════════
#  AI-ASSISTED OPTIMIZATION BRIDGE
# ══════════════════════════════════════════════════════════════════════════
# These functions delegate to the new optimization classes while
# maintaining the same API as the original functions above.

def optimized_auto_place(state, new_cls, weights=None):
    """AI-assisted auto-placement using scored candidate ranking.

    Generates all valid candidates, scores them, and picks the best.
    Falls back to reschedule if no direct placement exists.

    Same return signature as auto_place_class.
    """
    from scheduler_app.schedule_optimizer import ScheduleOptimizer
    # No seed needed: place_with_reschedule reaches only quick_place and
    # _greedy_construct, and neither draws from the RNG. The randomized paths
    # (_perturb_ordering, _lns_improve) are called only from optimize().
    optimizer = ScheduleOptimizer(state, weights=weights)
    return optimizer.place_with_reschedule(new_cls)


def optimized_reschedule_all(state, weights=None, protected_ids=None,
                             progress_callback=None,
                             multi_start_runs=5,
                             multi_start_time_limit=120.0,
                             use_cpsat=False, cpsat_time_limit=15.0,
                             parallel_workers=0,
                             seed=DEFAULT_OPTIMIZER_SEED,
                             cancel_token=None, **optimizer_kwargs):
    """Hybrid timetable-wide reschedule with multi-start LNS + optional CP-SAT.

    Preserves pinned and protected placements. Uses greedy construction
    with look-ahead scoring followed by iterative LNS destroy-repair
    for better overall quality. Runs multiple independent optimization
    passes from perturbed starting states and keeps the best.
    Optionally refines with Google OR-Tools CP-SAT constraint solver.

    Args:
        parallel_workers: Number of worker processes for parallel
                          candidate evaluation. 0 = auto, negative = disabled.
        seed: RNG seed; the default makes the result reproducible
              (ST-SCHED-013). None draws a fresh seed. The seed used is
              reported back as summary['seed'].

    Returns:
        (placed_list, unplaced_list, changes, summary) where summary
        contains before/after quality breakdown and improvement stats.
    """
    from scheduler_app.schedule_optimizer import ScheduleOptimizer
    optimizer = ScheduleOptimizer(
        state, weights=weights, protected_ids=protected_ids,
        progress_callback=progress_callback,
        multi_start_runs=multi_start_runs,
        multi_start_time_limit=multi_start_time_limit,
        use_cpsat=use_cpsat, cpsat_time_limit=cpsat_time_limit,
        parallel_workers=parallel_workers, seed=seed,
        cancel_token=cancel_token, **optimizer_kwargs)
    return optimizer.optimize()


def optimized_batch_schedule(state, new_classes, weights=None):
    """AI-assisted batch scheduling with candidate scoring.

    Phase 1: Place new classes using scored candidates.
    Phase 2: Full reschedule if any fail.

    Same return signature as batch_schedule.
    """
    from scheduler_app.constraint_validator import ConstraintValidator
    from scheduler_app.candidate_generator import CandidateGenerator
    from scheduler_app.placement_scorer import PlacementScorer

    from scheduler_app.models import PROTECTION_LOCKED

    new_ids = {cls_key(c) for c in new_classes}
    # ST-SCHED-007: a `protection="locked"` class is not flexible. Phase 2
    # re-solves everything in `existing_flexible` from scratch, so including
    # locked classes here let adding one new lesson relocate a lesson the user
    # had explicitly frozen — observed moving monday/09:00 -> tuesday/10:00.
    # Excluded here, they stay in build_occupancy() and act as fixed points
    # the reschedule has to work around, which is what "locked" means.
    existing_flexible = [c for c in state["classes"]
                         if c["placed"] and not c["pinned"]
                         and c.get("protection") != PROTECTION_LOCKED
                         and cls_key(c) not in new_ids]

    new_pinned = [c for c in new_classes if c["pinned"]]
    new_flexible = [c for c in new_classes if not c["pinned"]]

    # ── Phase 1: Place new classes around existing ──
    validator = ConstraintValidator(state, exclude_ids=new_ids)
    generator = CandidateGenerator(state, validator=validator)
    scorer = PlacementScorer(state, validator, weights=weights)

    placed_result = []
    unplaced_result = []

    # Place new pinned classes
    pinned_ok = True
    for cls in new_pinned:
        day, slot, room = cls["pinned_day"], cls["pinned_time"], cls["pinned_classroom"]
        if validator.check_placement(cls, day, slot, room):
            validator.add_placement(cls, day, slot, room)
            placed_result.append((cls, day, slot, room))
        else:
            pinned_ok = False
            conflicts = validator.find_conflicts(cls, day, slot, room)
            reason = "; ".join(conflicts) if conflicts else tr("conflicts.batch_conflict")
            unplaced_result.append((cls, reason))

    # Sort by difficulty (hardest first)
    new_flexible_sorted = validator.sort_by_difficulty(new_flexible)

    # Try placing each using scored candidates with look-ahead
    all_placed = True
    for pos, cls in enumerate(new_flexible_sorted):
        candidates = generator.generate(cls)
        if candidates:
            remaining = new_flexible_sorted[pos + 1:]
            if remaining and len(candidates) > 1:
                scored = scorer.score_candidates_with_lookahead(
                    cls, candidates, remaining, generator)
            else:
                scored = scorer.score_candidates(cls, candidates)
            best = scored[0]
            day, slot, room = best[0], best[1], best[2]
            validator.add_placement(cls, day, slot, room)
            placed_result.append((cls, day, slot, room))
        else:
            all_placed = False
            break

    if all_placed and pinned_ok:
        # Every already-placed class keeps its position, locked ones included —
        # they are absent from `existing_flexible` by design now, and leaving
        # them out of the result would report them as no longer placed.
        for cls in state["classes"]:
            if (cls["placed"] and not cls["pinned"]
                    and cls_key(cls) not in new_ids):
                placed_result.append((cls, cls["placed_day"],
                                      cls["placed_time"],
                                      cls["placed_classroom"]))
        return placed_result, unplaced_result, False

    # ── Phase 2: Full reschedule ──
    from scheduler_app.schedule_optimizer import ScheduleOptimizer
    # Remove phase 1 partial results
    placed_result = []
    unplaced_result = []

    all_exclude = new_ids | {cls_key(c) for c in existing_flexible}
    validator2 = ConstraintValidator(state, exclude_ids=all_exclude)

    # Place pinned
    for cls in new_pinned:
        day, slot, room = cls["pinned_day"], cls["pinned_time"], cls["pinned_classroom"]
        if validator2.check_placement(cls, day, slot, room):
            validator2.add_placement(cls, day, slot, room)
            placed_result.append((cls, day, slot, room))
        else:
            conflicts = validator2.find_conflicts(cls, day, slot, room)
            reason = "; ".join(conflicts) if conflicts else tr("conflicts.pinned_conflict")
            unplaced_result.append((cls, reason))

    combined = existing_flexible + new_flexible
    combined = validator2.sort_by_difficulty(combined)

    generator2 = CandidateGenerator(state, validator=validator2)
    scorer2 = PlacementScorer(state, validator2, weights=weights)

    # Greedy with backtracking via optimizer.
    # ST-PERF-008: bounded like every other greedy phase. This one is reached
    # from the "add classes" and "place batch" buttons, so an unbounded search
    # here is a frozen window with no progress dialog behind it.
    import time as _time
    optimizer = ScheduleOptimizer(state, weights=weights)
    solution, _greedy_stats = optimizer._greedy_construct(
        combined, validator2, generator2, scorer2,
        deadline=_time.time() + optimizer.multi_start_time_limit)

    for i, cls in enumerate(combined):
        if solution[i] is not None:
            day, slot, room = solution[i]
            placed_result.append((cls, day, slot, room))
        else:
            reason = generator2.unplaced_reason(cls)
            unplaced_result.append((cls, reason))

    return placed_result, unplaced_result, True


def score_placement(state, cls, day, slot, room, weights=None):
    """Score a single placement using the PlacementScorer.

    Convenience function for UI display and feedback logging.
    """
    from scheduler_app.constraint_validator import ConstraintValidator
    from scheduler_app.placement_scorer import PlacementScorer

    exclude = {cls_key(cls)}
    validator = ConstraintValidator(state, exclude_ids=exclude)
    scorer = PlacementScorer(state, validator, weights=weights)
    return scorer.score(cls, day, slot, room)


def score_placement_explained(state, cls, day, slot, room, weights=None):
    """Score a placement and return (score, breakdown, explanation).

    Returns:
        (float, dict, dict) — numerical score, component breakdown,
        and human-readable explanation from ExplanationEngine.
    """
    from scheduler_app.constraint_validator import ConstraintValidator
    from scheduler_app.placement_scorer import PlacementScorer
    from scheduler_app.explanation_engine import ExplanationEngine

    exclude = {cls_key(cls)}
    validator = ConstraintValidator(state, exclude_ids=exclude)
    scorer = PlacementScorer(state, validator, weights=weights)
    score, breakdown = scorer.score_explained(cls, day, slot, room)
    engine = ExplanationEngine()
    explanation = engine.explain_placement(cls, day, slot, room, breakdown)
    return score, breakdown, explanation


def check_placement_explained(state, cls, day, slot, room):
    """Check placement validity with descriptive reasons.

    Returns:
        (bool, list[str]) — validity and list of reason strings.
    """
    from scheduler_app.constraint_validator import ConstraintValidator

    exclude = {cls_key(cls)}
    validator = ConstraintValidator(state, exclude_ids=exclude)
    return validator.check_placement_explained(cls, day, slot, room)


def analyze_schedule(state, placements=None):
    """Analyze full timetable quality and return structured analytics.

    If placements is None, builds list from currently placed classes.

    Returns:
        dict with global_score, grade, per-entity metrics, and insights.
    """
    from scheduler_app.schedule_analytics import ScheduleAnalytics

    if placements is None:
        placements = []
        for cls in get_placed_classes(state):
            day = effective_day(cls)
            slot = effective_time(cls)
            room = effective_room(cls)
            placements.append((cls, day, slot, room))

    analytics = ScheduleAnalytics(state)
    return analytics.analyze(placements)


def analyze_conflict_graph(state):
    """Build and analyze the conflict graph for flexible classes.

    Returns:
        dict with graph metrics: total_nodes, total_edges,
        components_count, per-class degrees, and density stats.
    """
    from scheduler_app.conflict_graph import ConflictGraphBuilder, ConflictAnalyzer
    from scheduler_app.constraint_validator import ConstraintValidator

    flexible = [c for c in state.get("classes", [])
                if not c.get("pinned", False)]
    if not flexible:
        return {"total_nodes": 0, "total_edges": 0, "components_count": 0,
                "degrees": {}, "avg_degree": 0.0}

    builder = ConflictGraphBuilder(state, flexible)
    graph = builder.build()

    validator = ConstraintValidator(state,
                                    exclude_ids={cls_key(c) for c in flexible})
    analyzer = ConflictAnalyzer(graph, validator)
    components = analyzer.connected_components()

    degrees = {}
    total_degree = 0
    for i, cls in enumerate(flexible):
        deg = graph.degree(i)
        degrees[cls["name"]] = deg
        total_degree += deg

    avg_degree = total_degree / max(len(flexible), 1)

    return {
        "total_nodes": len(flexible),
        "total_edges": graph.total_edges(),
        "components_count": len(components),
        "degrees": degrees,
        "avg_degree": avg_degree,
    }


def analyze_constraint_propagation(state):
    """Build constraint propagation state and return per-class valid counts.

    Returns:
        dict with per-class valid placement counts and summary stats.
    """
    from scheduler_app.constraint_validator import ConstraintValidator
    from scheduler_app.candidate_generator import CandidateGenerator
    from scheduler_app.constraint_propagator import (
        ConstraintState, ConstraintPropagator)

    flexible = [c for c in state.get("classes", [])
                if not c.get("pinned", False)]
    if not flexible:
        return {"total_classes": 0, "valid_counts": {},
                "min_count": 0, "max_count": 0, "avg_count": 0.0}

    exclude_ids = {cls_key(c) for c in flexible}
    validator = ConstraintValidator(state, exclude_ids=exclude_ids)
    generator = CandidateGenerator(state, validator=validator)
    cs = ConstraintState(state, validator, generator, flexible)
    propagator = ConstraintPropagator(cs)

    valid_counts = {}
    total = 0
    min_count = float("inf")
    max_count = 0
    for cls in flexible:
        count = propagator.get_valid_count(cls)
        valid_counts[cls["name"]] = count
        total += count
        min_count = min(min_count, count)
        max_count = max(max_count, count)

    avg = total / max(len(flexible), 1)

    return {
        "total_classes": len(flexible),
        "valid_counts": valid_counts,
        "min_count": min_count if min_count != float("inf") else 0,
        "max_count": max_count,
        "avg_count": avg,
    }


def negotiate_after_optimization(state, placed_list, unplaced_list):
    """Run constraint negotiation after an optimization pass.

    Called when ScheduleOptimizer leaves unplaced classes.

    Returns:
        dict with negotiation results or None if all placed.
    """
    from scheduler_app.constraint_negotiator import ConstraintNegotiator
    negotiator = ConstraintNegotiator(state)
    return negotiator.negotiate_after_optimization(placed_list, unplaced_list)


def apply_negotiation_suggestion(cls, suggestion):
    """Apply a single constraint relaxation suggestion to a class.

    Modifies the class dict in-place.

    Returns:
        True if the suggestion was applied.
    """
    from scheduler_app.constraint_negotiator import ConstraintNegotiator
    return ConstraintNegotiator.apply_suggestion(None, cls, suggestion)
