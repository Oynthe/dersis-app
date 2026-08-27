"""Event-driven Constraint Negotiation layer for timetable scheduling.

Automatically diagnoses infeasibility, explains bottlenecks, and suggests
the smallest or most reasonable constraint relaxations needed to make
scheduling possible or to improve timetable quality.

This module is triggered automatically by the scheduling workflow whenever:
  - A class cannot be placed during add/auto-place operations
  - One or more classes remain unplaced after rescheduling/optimization
  - Severe bottleneck conditions are detected during refresh

Architecture:
  InfeasibilityAnalyzer — examines why specific classes cannot be placed,
      categorizes blocking constraints, and builds structured explanations.
  RelaxationSuggester — generates ranked, minimal constraint relaxation
      suggestions based on conflict graph structure and incremental state.
  NegotiationReportBuilder — assembles user-readable negotiation reports
      combining analysis and suggestions.
  ConstraintNegotiator — top-level orchestrator that integrates with
      ConstraintValidator, CandidateGenerator, ConflictGraphBuilder,
      and ScheduleOptimizer.

The existing hard-constraint engine remains authoritative. This module
never overrides constraints — it only *suggests* relaxations (unless
the user has enabled auto-apply for low-risk suggestions via
Documents/Dersis/settings/negotiation_settings.egu).
"""

from collections import defaultdict

from scheduler_app.logic import (
    slot_index, find_slot_index, slots_fit, total_duration,
    get_placed_classes, classroom_of,
    _active_targets, targets_overlap,
)
from scheduler_app.models import (
    cls_key, find_off_grid_placements,
    room_fits_class, get_room_capacity, get_physical_room_candidates,
)
from scheduler_app.constraint_validator import ConstraintValidator
from scheduler_app.candidate_generator import CandidateGenerator
from scheduler_app.conflict_graph import ConflictGraphBuilder, ConflictAnalyzer
from scheduler_app.translations import tr
from scheduler_app.ui.day_keys import day_label


# ══════════════════════════════════════════════════════════════════════════
#  INFEASIBILITY ANALYZER
# ══════════════════════════════════════════════════════════════════════════

class InfeasibilityAnalyzer:
    """Examines why classes cannot be placed and categorizes blocking causes.

    For each unplaced class, determines whether the failure is due to:
      - No valid days remaining after allowed/excluded filters
      - No valid times remaining after allowed/excluded filters
      - No valid rooms remaining after required/excluded filters
      - All remaining slots blocked by lecturer conflicts
      - All remaining slots blocked by room occupancy
      - All remaining slots blocked by branch/group conflicts
      - A combination of multiple constraint types

    Produces structured BlockingReport objects for further analysis.
    """

    def __init__(self, state, validator, generator):
        self.state = state
        self.validator = validator
        self.generator = generator

    def analyze_class(self, cls):
        """Analyze why a class cannot be placed.

        Returns:
            dict with keys:
                class_name: str
                lecturer: str
                is_infeasible: bool
                blocking_categories: list of category dicts
                narrowest_bottleneck: str or None
                total_search_space: int
                valid_slots: int
                blocking_summary: str (human-readable)
        """
        days, times, rooms = self.generator.get_search_space(cls)
        all_days = cls.get("allowed_days") or self.state["days"]
        all_times = cls.get("allowed_times") or self.state["slots"]
        all_rooms = list(self.state["classrooms"])

        categories = []

        # Check day restrictions
        if not days:
            if cls.get("allowed_days") and cls.get("excluded_days"):
                overlap = set(cls["allowed_days"]) & set(cls["excluded_days"])
                if overlap:
                    categories.append({
                        "type": "day_conflict",
                        "severity": "critical",
                        "message": tr("negotiation.days_conflict"),
                        "details": {"allowed": cls["allowed_days"],
                                    "excluded": cls["excluded_days"]},
                    })
                else:
                    categories.append({
                        "type": "no_valid_days",
                        "severity": "critical",
                        "message": tr("negotiation.all_days_excluded"),
                        "details": {"allowed": cls["allowed_days"],
                                    "excluded": cls["excluded_days"]},
                    })
            elif not cls.get("allowed_days") and cls.get("excluded_days"):
                categories.append({
                    "type": "all_days_excluded",
                    "severity": "critical",
                    "message": tr("negotiation.all_days_excluded_simple"),
                    "details": {"excluded": cls["excluded_days"]},
                })
            else:
                categories.append({
                    "type": "no_days_configured",
                    "severity": "critical",
                    "message": tr("negotiation.no_days_configured"),
                    "details": {},
                })

        # Check time restrictions
        if not times:
            td = total_duration(cls)
            raw_times = cls.get("allowed_times") or self.state["slots"]
            if cls.get("excluded_times"):
                raw_times = [t for t in raw_times
                             if t not in cls["excluded_times"]]
            # Check if duration filtering removed everything
            if raw_times:
                categories.append({
                    "type": "duration_overflow",
                    "severity": "critical",
                    "message": tr("negotiation.duration_exceeds").format(d=td),
                    "details": {"duration": td,
                                "available_starts": raw_times},
                })
            else:
                categories.append({
                    "type": "no_valid_times",
                    "severity": "critical",
                    "message": tr("negotiation.no_valid_start_times"),
                    "details": {"allowed": cls.get("allowed_times", []),
                                "excluded": cls.get("excluded_times", [])},
                })

        # Check room restrictions
        if not rooms:
            # Use canonical helpers for room candidate filtering
            pre_cap_rooms = get_physical_room_candidates(
                self.state, cls, apply_capacity=False)
            cap_filtered = get_physical_room_candidates(
                self.state, cls, apply_capacity=True)
            if pre_cap_rooms and not cap_filtered:
                # Rooms exist but none have sufficient capacity
                participants = cls.get("participants", 0)
                max_cap = max(
                    (get_room_capacity(self.state, r) for r in pre_cap_rooms),
                    default=0)
                categories.append({
                    "type": "insufficient_capacity",
                    "severity": "critical",
                    "message": tr("negotiation.no_room_capacity"),
                    "details": {
                        "participants": participants,
                        "max_available_capacity": max_cap,
                        "available_rooms": pre_cap_rooms,
                        "suggestion": tr("negotiation.use_larger_room"),
                    },
                })
            elif cls.get("required_classrooms"):
                excluded = set(cls.get("excluded_classrooms", []))
                required = set(cls["required_classrooms"])
                if required & excluded:
                    categories.append({
                        "type": "room_conflict",
                        "severity": "critical",
                        "message": tr("negotiation.rooms_conflict"),
                        "details": {"required": cls["required_classrooms"],
                                    "excluded": cls.get("excluded_classrooms", [])},
                    })
                else:
                    available = set(self.state["classrooms"])
                    missing = required - available
                    if missing:
                        categories.append({
                            "type": "required_room_missing",
                            "severity": "critical",
                            "message": tr("negotiation.rooms_not_in_schedule").format(
                                rooms=", ".join(sorted(missing))),
                            "details": {"missing": sorted(missing)},
                        })
            else:
                categories.append({
                    "type": "no_rooms_configured",
                    "severity": "critical",
                    "message": tr("negotiation.no_classrooms_configured"),
                    "details": {},
                })

        # If search space exists, analyze occupancy-based blocking
        if days and times and rooms:
            blocking = self._analyze_occupancy_blocking(cls, days, times, rooms)
            categories.extend(blocking)

        # Determine narrowest bottleneck
        total_space = len(days) * len(times) * len(rooms) if (days and times and rooms) else 0
        valid_slots = len(self.generator.generate(cls))

        bottleneck = None
        if categories:
            critical = [c for c in categories if c["severity"] == "critical"]
            if critical:
                bottleneck = critical[0]["type"]
            else:
                # Find the category that blocks the most
                bottleneck = max(categories,
                                 key=lambda c: c.get("blocked_count", 0),
                                 default=categories[0])["type"]

        summary = self._build_summary(cls, categories, valid_slots, total_space)

        return {
            "class_name": cls["name"],
            "lecturer": cls.get("lecturer", ""),
            "targets": cls.get("targets", []),
            "is_infeasible": valid_slots == 0,
            "blocking_categories": categories,
            "narrowest_bottleneck": bottleneck,
            "total_search_space": total_space,
            "valid_slots": valid_slots,
            "blocking_summary": summary,
        }

    def _analyze_occupancy_blocking(self, cls, days, times, rooms):
        """Categorize blocking by occupancy type across valid search space."""
        categories = []
        td = total_duration(cls)

        lecturer_blocked = 0
        room_blocked = 0
        group_blocked = 0
        total_checked = 0

        # Track per-day and per-time blocking patterns
        day_block_counts = defaultdict(int)
        time_block_counts = defaultdict(int)
        room_block_counts = defaultdict(int)
        lecturer_busy_slots = defaultdict(set)

        for day in days:
            for slot in times:
                for room in rooms:
                    total_checked += 1
                    if self.validator.check_placement(cls, day, slot, room):
                        continue

                    si = slot_index(self.state, slot)
                    slots_list = self.state["slots"][si:si + td]

                    is_lect = False
                    is_room = False
                    is_group = False

                    for off, s in enumerate(slots_list):
                        key = (day, s)
                        if cls["lecturer"] in self.validator.lect_occ.get(key, set()):
                            is_lect = True
                            lecturer_busy_slots[day].add(s)
                        if room in self.validator.room_occ.get(key, set()):
                            is_room = True
                        for t in _active_targets(cls, off):
                            if (t["year"], t["branch"]) in self.validator.group_occ.get(key, set()):
                                is_group = True

                    if is_lect:
                        lecturer_blocked += 1
                    if is_room:
                        room_blocked += 1
                        room_block_counts[room] += 1
                    if is_group:
                        group_blocked += 1

                    day_block_counts[day] += 1
                    time_block_counts[slot] += 1

        if total_checked == 0:
            return categories

        blocked_total = total_checked - len(self.generator.generate(cls))

        if lecturer_blocked > 0:
            pct = lecturer_blocked / total_checked
            severity = "critical" if pct > 0.8 else ("high" if pct > 0.5 else "medium")
            categories.append({
                "type": "lecturer_conflict",
                "severity": severity,
                "message": tr("negotiation.lecturer_busy").format(
                    lect=cls["lecturer"], pct=pct),
                "blocked_count": lecturer_blocked,
                "details": {
                    "lecturer": cls["lecturer"],
                    "busy_days": {d: sorted(s) for d, s in lecturer_busy_slots.items()},
                    "block_percentage": pct,
                },
            })

        if room_blocked > 0:
            pct = room_blocked / total_checked
            severity = "critical" if pct > 0.8 else ("high" if pct > 0.5 else "medium")
            worst_rooms = sorted(room_block_counts.items(),
                                 key=lambda x: -x[1])[:3]
            categories.append({
                "type": "room_occupancy",
                "severity": severity,
                "message": tr("negotiation.rooms_occupied").format(pct=pct),
                "blocked_count": room_blocked,
                "details": {
                    "worst_rooms": worst_rooms,
                    "block_percentage": pct,
                },
            })

        if group_blocked > 0:
            pct = group_blocked / total_checked
            severity = "critical" if pct > 0.8 else ("high" if pct > 0.5 else "medium")
            categories.append({
                "type": "group_conflict",
                "severity": severity,
                "message": tr("negotiation.student_conflicts").format(pct=pct),
                "blocked_count": group_blocked,
                "details": {
                    "targets": [f"{t['year']}/{t['branch']}"
                                for t in cls.get("targets", [])],
                    "block_percentage": pct,
                },
            })

        # Identify day-specific bottlenecks
        if day_block_counts:
            fully_blocked_days = [d for d, c in day_block_counts.items()
                                  if c >= len(times) * len(rooms)]
            if fully_blocked_days and len(fully_blocked_days) < len(days):
                categories.append({
                    "type": "day_bottleneck",
                    "severity": "high",
                    "message": tr("negotiation.blocked_days").format(
                        n=len(fully_blocked_days), total=len(days)),
                    "blocked_count": len(fully_blocked_days),
                    "details": {"blocked_days": fully_blocked_days},
                })

        return categories

    def _build_summary(self, cls, categories, valid_slots, total_space):
        """Build human-readable summary of blocking analysis."""
        if not categories:
            if valid_slots > 0:
                return tr("negotiation.valid_placements").format(
                    n=valid_slots)
            return tr("negotiation.no_placements_unknown")

        critical = [c for c in categories if c["severity"] == "critical"]
        if critical:
            reasons = "; ".join(c["message"] for c in critical)
            return tr("negotiation.cannot_place").format(
                name=cls["name"], reasons=reasons)

        high = [c for c in categories if c["severity"] == "high"]
        if high and valid_slots == 0:
            reasons = "; ".join(c["message"] for c in high)
            return tr("negotiation.cannot_place").format(
                name=cls["name"], reasons=reasons)

        if valid_slots == 0:
            reasons = "; ".join(c["message"] for c in categories[:3])
            return tr("negotiation.cannot_place_combo").format(
                name=cls["name"], reasons=reasons)

        return tr("negotiation.highly_constrained").format(
            name=cls["name"], n=valid_slots, total=total_space)

    def analyze_all_unplaced(self, classes=None):
        """Analyze all unplaced classes.

        Returns list of analysis dicts sorted by severity (most
        constrained first).
        """
        if classes is None:
            classes = [c for c in self.state["classes"]
                       if not c["placed"] and not c["pinned"]]
        results = []
        for cls in classes:
            report = self.analyze_class(cls)
            if report["is_infeasible"] or report["valid_slots"] < 5:
                results.append(report)
        results.sort(key=lambda r: (0 if r["is_infeasible"] else 1,
                                    r["valid_slots"]))
        return results


# ══════════════════════════════════════════════════════════════════════════
#  RELAXATION SUGGESTER
# ══════════════════════════════════════════════════════════════════════════

class RelaxationSuggester:
    """Generates ranked, minimal constraint relaxation suggestions.

    For each infeasible or highly-constrained class, determines which
    specific constraint relaxation would unlock the most placements
    with the least disruption. Uses the conflict graph and validator
    to estimate the impact of each potential relaxation.

    Suggestion types:
      - allow_day: Allow one additional day
      - allow_time: Allow one additional start time
      - allow_room: Permit one additional classroom
      - remove_excluded_day: Remove a day from excluded list
      - remove_excluded_time: Remove a time from excluded list
      - remove_excluded_room: Remove a room from excluded list
      - move_conflicting: Move a specific conflicting class
    """

    def __init__(self, state, validator, generator):
        self.state = state
        self.validator = validator
        self.generator = generator

    def suggest_for_class(self, cls, analysis=None, max_suggestions=10):
        """Generate relaxation suggestions for a single class.

        Args:
            cls: The class dict to generate suggestions for.
            analysis: Optional pre-computed analysis from InfeasibilityAnalyzer.
            max_suggestions: Maximum number of suggestions to return.

        Returns:
            list of suggestion dicts, sorted by (impact desc, disruption asc).
            Each suggestion has:
                type: str (suggestion category)
                description: str (human-readable)
                impact: int (estimated new valid slots gained)
                disruption: float (0.0-1.0, lower = less disruptive)
                details: dict (specifics of the relaxation)
        """
        suggestions = []

        # Try adding days
        suggestions.extend(self._suggest_add_days(cls))

        # Try adding times
        suggestions.extend(self._suggest_add_times(cls))

        # Try adding rooms
        suggestions.extend(self._suggest_add_rooms(cls))

        # Suggest larger rooms if capacity is the bottleneck
        suggestions.extend(self._suggest_larger_rooms(cls))

        # Try removing excluded constraints
        suggestions.extend(self._suggest_remove_exclusions(cls))

        # Try moving conflicting classes. This also reports how many placed
        # lessons it had to skip as off-grid (ST-DATA-003); stash it so
        # build_class_report can surface the count rather than swallowing it.
        move_suggestions, off_grid = self._suggest_move_conflicts(cls)
        suggestions.extend(move_suggestions)
        self.last_off_grid_blockers = off_grid

        # Sort: primary by impact (desc), secondary by disruption (asc)
        suggestions.sort(key=lambda s: (-s["impact"], s["disruption"]))

        return suggestions[:max_suggestions]

    def _suggest_allow_items(self, cls, dimension):
        """Suggest allowing additional items for a constraint dimension.

        Args:
            cls: The class to generate suggestions for.
            dimension: One of 'days', 'times', or 'rooms'.

        Returns list of suggestion dicts.
        """
        config = {
            "days": {
                "allowed_field": "allowed_days",
                "excluded_field": "excluded_days",
                "all_items": self.state["days"],
                "type": "allow_day",
                "tr_key": "negotiation.allow_day",
                "tr_kwarg": "day",
                "format_fn": day_label,
                "impact_fn": self._estimate_day_impact,
                "disruption": 0.1,
                "constraint_field": "allowed_days",
                "feasibility_fn": None,
            },
            "times": {
                "allowed_field": "allowed_times",
                "excluded_field": "excluded_times",
                "all_items": self.state["slots"],
                "type": "allow_time",
                "tr_key": "negotiation.allow_start_time",
                "tr_kwarg": "time",
                "format_fn": None,
                "impact_fn": self._estimate_time_impact,
                "disruption": 0.1,
                "constraint_field": "allowed_times",
                "feasibility_fn": lambda slot: slots_fit(
                    self.state, slot, total_duration(cls)),
            },
            "rooms": {
                "allowed_field": "required_classrooms",
                "excluded_field": "excluded_classrooms",
                "all_items": list(self.state["classrooms"]),
                "type": "allow_room",
                "tr_key": "negotiation.allow_classroom",
                "tr_kwarg": "room",
                "format_fn": None,
                "impact_fn": self._estimate_room_impact,
                "disruption": 0.15,
                "constraint_field": "required_classrooms",
                "feasibility_fn": lambda room: room_fits_class(
                    self.state, room, cls),
            },
        }[dimension]

        suggestions = []
        allowed = cls.get(config["allowed_field"])
        if not allowed:
            return suggestions

        current = set(allowed)
        excluded = set(cls.get(config["excluded_field"], []) or [])

        for item in config["all_items"]:
            if item in current or item in excluded:
                continue
            if config["feasibility_fn"] and not config["feasibility_fn"](item):
                continue
            impact = config["impact_fn"](cls, item)
            if impact > 0:
                fmt = config["format_fn"]
                label = fmt(item) if fmt else item
                suggestions.append({
                    "type": config["type"],
                    "description": tr(config["tr_key"]).format(
                        **{config["tr_kwarg"]: label, "name": cls["name"]}),
                    "impact": impact,
                    "disruption": config["disruption"],
                    "details": {config["tr_kwarg"]: item,
                                "class_name": cls["name"]},
                    "constraint_field": config["constraint_field"],
                    "constraint_value": item,
                })

        return suggestions

    def _suggest_add_days(self, cls):
        """Suggest allowing additional days."""
        return self._suggest_allow_items(cls, "days")

    def _suggest_add_times(self, cls):
        """Suggest allowing additional start times."""
        return self._suggest_allow_items(cls, "times")

    def _suggest_add_rooms(self, cls):
        """Suggest allowing additional classrooms."""
        return self._suggest_allow_items(cls, "rooms")

    def _suggest_larger_rooms(self, cls):
        """Suggest using a larger room if capacity is the bottleneck."""
        suggestions = []
        participants = cls.get("participants", 0)
        if participants == 0:
            return suggestions

        # Find rooms that have sufficient capacity but aren't currently allowed
        for room in self.state["classrooms"]:
            cap = get_room_capacity(self.state, room)
            if cap == 0 or cap >= participants:
                continue  # Already fits or unlimited
            # This room is too small — skip it
        # Check if no rooms fit
        fitting_rooms = [r for r in self.state["classrooms"]
                         if room_fits_class(self.state, r, cls)]
        if not fitting_rooms:
            suggestions.append({
                "type": "use_larger_room",
                "description": tr("negotiation.use_larger_room_detail").format(
                    n=participants),
                "impact": 0,
                "disruption": 0.8,
                "details": {
                    "participants": participants,
                    "suggestion": tr("negotiation.add_larger_room").format(n=participants),
                },
            })
        elif cls.get("required_classrooms"):
            # Required rooms are set but none fit — suggest allowing a fitting room
            required = set(cls["required_classrooms"])
            for room in fitting_rooms:
                if room not in required:
                    cap = get_room_capacity(self.state, room)
                    cap_label = str(cap) if cap > 0 else tr("labels.unlimited")
                    suggestions.append({
                        "type": "allow_room",
                        "description": tr("negotiation.allow_classroom_capacity").format(
                            room=room, cap=cap_label, name=cls["name"]),
                        "impact": self._estimate_room_impact(cls, room),
                        "disruption": 0.15,
                        "details": {"room": room, "class_name": cls["name"],
                                    "capacity": cap},
                        "constraint_field": "required_classrooms",
                        "constraint_value": room,
                    })
        return suggestions

    def _suggest_remove_exclusions(self, cls):
        """Suggest removing items from excluded lists."""
        suggestions = []
        exclusion_configs = [
            {
                "field": "excluded_days",
                "type": "remove_excluded_day",
                "tr_key": "negotiation.remove_excluded_day",
                "tr_kwarg": "day",
                "format_fn": day_label,
                "impact_fn": self._estimate_day_impact,
                "disruption": 0.1,
                "feasibility_fn": None,
            },
            {
                "field": "excluded_times",
                "type": "remove_excluded_time",
                "tr_key": "negotiation.remove_excluded_time",
                "tr_kwarg": "time",
                "format_fn": None,
                "impact_fn": self._estimate_time_impact,
                "disruption": 0.1,
                "feasibility_fn": lambda slot: slots_fit(
                    self.state, slot, total_duration(cls)),
            },
            {
                "field": "excluded_classrooms",
                "type": "remove_excluded_room",
                "tr_key": "negotiation.remove_excluded_room",
                "tr_kwarg": "room",
                "format_fn": None,
                "impact_fn": self._estimate_room_impact,
                "disruption": 0.15,
                "feasibility_fn": lambda room: room in self.state["classrooms"],
            },
        ]

        for config in exclusion_configs:
            for item in cls.get(config["field"], []) or []:
                if config["feasibility_fn"] and not config["feasibility_fn"](item):
                    continue
                impact = config["impact_fn"](
                    cls, item, is_exclusion_removal=True)
                if impact > 0:
                    fmt = config["format_fn"]
                    label = fmt(item) if fmt else item
                    suggestions.append({
                        "type": config["type"],
                        "description": tr(config["tr_key"]).format(
                            **{config["tr_kwarg"]: label,
                               "name": cls["name"]}),
                        "impact": impact,
                        "disruption": config["disruption"],
                        "details": {config["tr_kwarg"]: item,
                                    "class_name": cls["name"]},
                        "constraint_field": config["field"],
                        "constraint_value": item,
                    })

        return suggestions

    def _suggest_move_conflicts(self, cls):
        """Suggest moving specific conflicting classes to free up slots.

        ST-UI-015. This returned nothing at all, on every instance, for the
        whole life of the feature: blockers were accumulated under
        ``id(existing)`` -- a CPython object address -- and then resolved
        through ``{cls_key(c): c}``, whose keys are uuid strings. The lookup
        could never hit, so ``blocker`` was always None and the loop always
        skipped. Measured before the fix: 0 suggestions of this type against 4
        total on `small` and 4 on `normal`.

        It matters out of proportion to its size. The commonest unplaced reason
        is "all remaining candidate slots are occupied", and the only advice
        that helps a user act on it is "move Ders 6 out of Wednesday 12:00 and
        16 slots open up" -- which is precisely this function's job.

        Returns ``(suggestions, off_grid_blockers)``: the second value is the
        number of placed lessons skipped because they sit on a day or hour the
        grid no longer has. Skipping them is correct --
        ``ConstraintValidator.add_placement`` returns early on the identical
        condition, so such a lesson occupies no cell and blocks nothing, and
        counting it would contradict the validator that decides
        ``check_placement``. Returning the count is what keeps that from being
        a silent drop: those lessons are otherwise mentioned nowhere in the UI.
        """
        suggestions = []
        days, times, rooms = self.generator.get_search_space(cls)
        if not days or not times or not rooms:
            return suggestions, 0

        td = total_duration(cls)
        placed_classes = get_placed_classes(self.state)

        # Find which placed classes are blocking the most candidates
        blocker_counts = defaultdict(int)
        blocker_slots = defaultdict(set)

        for day in days:
            for slot in times:
                for room in rooms:
                    if self.validator.check_placement(cls, day, slot, room):
                        continue
                    # Find blockers
                    si = find_slot_index(self.state, slot)
                    if si is None:
                        continue
                    slots_list = self.state["slots"][si:si + td]
                    for existing in placed_classes:
                        if existing["pinned"] or existing.get("protection") == "locked":
                            continue
                        ex_day = existing.get("placed_day")
                        if ex_day != day:
                            continue
                        ex_room = classroom_of(existing)
                        ex_start = existing.get("placed_time")
                        # A STORED slot: logic.slot_index raises on one the user
                        # has deleted, and its own docstring forbids reading
                        # stored data with it (ST-SCHED-004). Before this,
                        # a single orphaned lesson killed 3 of 4 negotiate_class
                        # calls with ValueError: '20:00' is not in list.
                        ex_si = find_slot_index(self.state, ex_start)
                        if ex_si is None or ex_day not in self.state["days"]:
                            continue
                        ex_td = total_duration(existing)
                        ex_slots = set(self.state["slots"][ex_si:ex_si + ex_td])

                        blocked = False
                        for s in slots_list:
                            if s not in ex_slots:
                                continue
                            # Room conflict
                            if ex_room == room:
                                blocked = True
                                break
                            # Lecturer conflict
                            if existing["lecturer"] == cls["lecturer"]:
                                blocked = True
                                break
                            # Group conflict
                            off = slots_list.index(s) if s in slots_list else 0
                            ex_off = list(ex_slots).index(s) if s in ex_slots else 0
                            if targets_overlap(
                                    _active_targets(cls, off),
                                    _active_targets(existing, ex_off)):
                                blocked = True
                                break

                        if blocked:
                            ex_key = cls_key(existing)
                            blocker_counts[ex_key] += 1
                            blocker_slots[ex_key].add((day, slot, room))

        # Generate suggestions for top blockers.
        # Keyed by cls_key on BOTH sides -- see the docstring.
        id_to_cls = {cls_key(c): c for c in placed_classes}
        sorted_blockers = sorted(blocker_counts.items(), key=lambda x: -x[1])

        for cls_id, count in sorted_blockers[:5]:
            blocker = id_to_cls.get(cls_id)
            if blocker is None or blocker["pinned"] or blocker.get("protection") == "locked":
                continue
            blocker_day = blocker.get("placed_day", "")
            blocker_time = blocker.get("placed_time", "")
            suggestions.append({
                "type": "move_conflicting",
                # day_label, not the raw key: this sentence is shown to a user,
                # and "monday" in a Turkish sentence is the ST-FUNC-006 family
                # of defect (internal keys leaking into localized text).
                "description": tr("negotiation.move_blocker").format(
                    blocker=blocker["name"],
                    day=day_label(blocker_day) if blocker_day else "",
                    time=blocker_time, n=count, name=cls["name"]),
                "impact": count,
                "disruption": 0.5,  # Medium: affects another class
                "details": {
                    "blocker_name": blocker["name"],
                    "blocker_day": blocker_day,
                    "blocker_time": blocker_time,
                    "freed_slots": count,
                    "class_name": cls["name"],
                },
            })

        # Counted from models.find_off_grid_placements -- the codebase's one
        # oracle for "not on the grid", already used by the exporter warning
        # and the PDF appendix -- rather than from what this loop happened to
        # skip. The loop's own day filter (`ex_day != day`) runs BEFORE the
        # guard above, so a lesson orphaned by a deleted DAY never reaches it:
        # deriving the count here would silently report only the deleted-HOUR
        # half. Re-deriving the rule would also make a third copy of it.
        return suggestions, len(find_off_grid_placements(self.state))

    def _estimate_day_impact(self, cls, day, is_exclusion_removal=False):
        """Estimate how many valid slots would open if a day were allowed.

        Temporarily modifies the class constraints to simulate the relaxation,
        then restores them. This ensures the validator correctly evaluates
        placements under the hypothetical relaxation.
        """
        if is_exclusion_removal:
            allowed = cls.get("allowed_days") or self.state["days"]
            if day not in allowed:
                return 0

        # Temporarily add day / remove exclusion
        orig_allowed = cls.get("allowed_days", [])
        orig_excluded = cls.get("excluded_days", [])
        if is_exclusion_removal:
            cls["excluded_days"] = [d for d in orig_excluded if d != day]
        elif orig_allowed:
            cls["allowed_days"] = list(orig_allowed) + [day]

        # ST-DATA-011: the constraints are mutated to simulate a relaxation, so
        # the restore has to happen even when the estimate raises — otherwise the
        # class is left carrying a constraint the user never set.
        try:
            _, times, rooms = self.generator.get_search_space(cls)
            count = 0
            for slot in (times if times else self.state["slots"]):
                td = total_duration(cls)
                if not slots_fit(self.state, slot, td):
                    continue
                for room in (rooms if rooms else self.state["classrooms"]):
                    if self.validator.check_placement(cls, day, slot, room):
                        count += 1
        finally:
            cls["allowed_days"] = orig_allowed
            cls["excluded_days"] = orig_excluded
        return count

    def _estimate_time_impact(self, cls, slot, is_exclusion_removal=False):
        """Estimate how many valid slots would open if a time were allowed.

        Temporarily modifies the class constraints to simulate the relaxation.
        """
        if is_exclusion_removal:
            allowed = cls.get("allowed_times") or self.state["slots"]
            if slot not in allowed:
                return 0

        orig_allowed = cls.get("allowed_times", [])
        orig_excluded = cls.get("excluded_times", [])
        if is_exclusion_removal:
            cls["excluded_times"] = [t for t in orig_excluded if t != slot]
        elif orig_allowed:
            cls["allowed_times"] = list(orig_allowed) + [slot]

        # ST-DATA-011: restore even when the estimate raises, or the class is
        # left carrying a constraint the user never set.
        try:
            days, _, rooms = self.generator.get_search_space(cls)
            count = 0
            for day in (days if days else self.state["days"]):
                for room in (rooms if rooms else self.state["classrooms"]):
                    if self.validator.check_placement(cls, day, slot, room):
                        count += 1
        finally:
            cls["allowed_times"] = orig_allowed
            cls["excluded_times"] = orig_excluded
        return count

    def _estimate_room_impact(self, cls, room, is_exclusion_removal=False):
        """Estimate how many valid slots would open if a room were allowed.

        Temporarily modifies the class constraints to simulate the relaxation.
        """
        orig_required = cls.get("required_classrooms", [])
        orig_excluded = cls.get("excluded_classrooms", [])
        if is_exclusion_removal:
            cls["excluded_classrooms"] = [r for r in orig_excluded if r != room]
        elif orig_required:
            cls["required_classrooms"] = list(orig_required) + [room]

        # ST-DATA-011: restore even when the estimate raises, or the class is
        # left carrying a constraint the user never set.
        try:
            days, times, _ = self.generator.get_search_space(cls)
            count = 0
            for day in (days if days else self.state["days"]):
                for slot in (times if times else self.state["slots"]):
                    td = total_duration(cls)
                    if not slots_fit(self.state, slot, td):
                        continue
                    if self.validator.check_placement(cls, day, slot, room):
                        count += 1
        finally:
            cls["required_classrooms"] = orig_required
            cls["excluded_classrooms"] = orig_excluded
        return count


# ══════════════════════════════════════════════════════════════════════════
#  NEGOTIATION REPORT BUILDER
# ══════════════════════════════════════════════════════════════════════════

class NegotiationReportBuilder:
    """Assembles user-readable negotiation reports.

    Combines infeasibility analysis and relaxation suggestions into
    structured reports for UI display.
    """

    def build_class_report(self, analysis, suggestions,
                           off_grid_blockers=0):
        """Build a complete negotiation report for one class.

        Args:
            analysis: dict from InfeasibilityAnalyzer.analyze_class()
            suggestions: list from RelaxationSuggester.suggest_for_class()

        Returns:
            dict with:
                class_name: str
                status: "infeasible" | "constrained" | "ok"
                summary: str (human-readable overview)
                blocking_reasons: list of str
                suggestions: list of formatted suggestion dicts
                priority: int (lower = more urgent)
                off_grid_blockers: int -- placed lessons skipped because
                    they sit on a day or hour the grid no longer has
                    (ST-DATA-003). Reported rather than swallowed: they are
                    mentioned nowhere else in the UI.
        """
        if analysis["is_infeasible"]:
            status = "infeasible"
            priority = 0
        elif analysis["valid_slots"] < 5:
            status = "constrained"
            priority = 1
        else:
            status = "ok"
            priority = 2

        blocking_reasons = []
        for cat in analysis["blocking_categories"]:
            blocking_reasons.append(cat["message"])

        formatted_suggestions = []
        for s in suggestions:
            formatted_suggestions.append({
                "description": s["description"],
                "impact_label": tr("negotiation.new_slots_available").format(n=s["impact"]),
                "disruption_label": self._disruption_label(s["disruption"]),
                "type": s["type"],
                "details": s["details"],
                "constraint_field": s.get("constraint_field"),
                "constraint_value": s.get("constraint_value"),
            })

        return {
            "class_name": analysis["class_name"],
            "lecturer": analysis["lecturer"],
            "status": status,
            "summary": analysis["blocking_summary"],
            "blocking_reasons": blocking_reasons,
            "suggestions": formatted_suggestions,
            "valid_slots": analysis["valid_slots"],
            "total_search_space": analysis["total_search_space"],
            "priority": priority,
            "off_grid_blockers": off_grid_blockers,
        }

    def build_diagnostic_summary(self, state, analyses, conflict_graph=None,
                                 analyzer=None):
        """Build a global diagnostic summary for the entire timetable.

        Evaluates main reasons for poor schedule quality or infeasibility
        including lecturer over-concentration, room scarcity, branch
        bottlenecks, and over-restrictive filters.

        Returns:
            dict with:
                total_classes: int
                unplaced_count: int
                constrained_count: int
                diagnostics: list of diagnostic dicts
                overall_assessment: str
        """
        all_classes = state.get("classes", [])
        total = len(all_classes)
        unplaced = [c for c in all_classes if not c["placed"] and not c["pinned"]]
        unplaced_count = len(unplaced)
        constrained = [a for a in analyses if a["valid_slots"] < 5
                       and not a["is_infeasible"]]

        diagnostics = []

        # 1. Lecturer over-concentration
        lect_classes = defaultdict(list)
        for cls in all_classes:
            if cls.get("lecturer"):
                lect_classes[cls["lecturer"]].append(cls)

        total_slots = len(state.get("days", [])) * len(state.get("slots", []))
        for lect, classes in lect_classes.items():
            total_dur = sum(total_duration(c) for c in classes)
            if total_slots > 0 and total_dur / total_slots > 0.5:
                diagnostics.append({
                    "type": "lecturer_overload",
                    "severity": "high",
                    "message": tr("negotiation.lecturer_utilization").format(
                        name=lect, dur=total_dur, total=total_slots,
                        pct=total_dur / total_slots),
                    "entity": lect,
                })

        # 2. Room scarcity analysis
        rooms = state.get("classrooms", [])
        if rooms:
            placed = get_placed_classes(state)
            room_usage = defaultdict(int)
            for cls in placed:
                room = classroom_of(cls)
                td = total_duration(cls)
                room_usage[room] += td

            overused_rooms = []
            for room in rooms:
                usage = room_usage.get(room, 0)
                if total_slots > 0 and usage / total_slots > 0.7:
                    overused_rooms.append((room, usage / total_slots))

            if overused_rooms:
                room_list = ", ".join(f"{r} ({p:.0%})"
                                     for r, p in overused_rooms)
                diagnostics.append({
                    "type": "room_scarcity",
                    "severity": "high" if len(overused_rooms) > len(rooms) * 0.5
                    else "medium",
                    "message": tr("negotiation.high_room_utilization").format(
                        rooms=room_list),
                    "entity": "rooms",
                })

        # 3. Branch bottleneck detection
        branch_classes = defaultdict(list)
        for cls in all_classes:
            for t in cls.get("targets", []):
                branch_classes[(t["year"], t["branch"])].append(cls)

        for (year, branch), classes in branch_classes.items():
            total_dur = sum(total_duration(c) for c in classes)
            if total_slots > 0 and total_dur / total_slots > 0.6:
                diagnostics.append({
                    "type": "branch_bottleneck",
                    "severity": "medium",
                    "message": tr("negotiation.student_group_capacity").format(
                        year=year, branch=branch, dur=total_dur,
                        total=total_slots, pct=total_dur / total_slots),
                    "entity": f"{year}/{branch}",
                })

        # 4. Over-restrictive filter detection
        restrictive_count = 0
        for cls in all_classes:
            constraint_count = 0
            if cls.get("allowed_days"):
                constraint_count += 1
            if cls.get("excluded_days"):
                constraint_count += 1
            if cls.get("allowed_times"):
                constraint_count += 1
            if cls.get("excluded_times"):
                constraint_count += 1
            if cls.get("required_classrooms"):
                constraint_count += 1
            if cls.get("excluded_classrooms"):
                constraint_count += 1
            if constraint_count >= 3:
                restrictive_count += 1

        if total > 0 and restrictive_count / total > 0.3:
            diagnostics.append({
                "type": "over_restrictive",
                "severity": "medium",
                "message": tr("negotiation.many_constraint_filters").format(
                    n=restrictive_count, total=total),
                "entity": "constraints",
            })

        # 5. Conflict graph density (if available)
        if conflict_graph is not None:
            total_edges = conflict_graph.total_edges()
            n_nodes = len(conflict_graph)
            if n_nodes > 1:
                max_edges = n_nodes * (n_nodes - 1) / 2
                density = total_edges / max_edges if max_edges > 0 else 0
                if density > 0.3:
                    diagnostics.append({
                        "type": "high_conflict_density",
                        "severity": "high",
                        "message": tr("negotiation.conflict_graph_density").format(d=density),
                        "entity": "graph",
                    })

        # Overall assessment
        if unplaced_count > total * 0.3:
            assessment = tr("negotiation.severe_issues").format(
                n=unplaced_count, total=total)
        elif unplaced_count > 0:
            assessment = tr("negotiation.classes_cannot_place").format(n=unplaced_count)
        elif len(constrained) > 0:
            assessment = tr("negotiation.all_placed_constrained").format(
                n=len(constrained))
        else:
            assessment = tr("negotiation.schedule_feasible")

        diagnostics.sort(key=lambda d: {"critical": 0, "high": 1,
                                        "medium": 2, "low": 3
                                        }.get(d["severity"], 4))

        return {
            "total_classes": total,
            "unplaced_count": unplaced_count,
            "constrained_count": len(constrained),
            "diagnostics": diagnostics,
            "overall_assessment": assessment,
        }

    def _disruption_label(self, disruption):
        """Convert disruption score to human-readable label."""
        if disruption <= 0.15:
            return tr("negotiation.minimal_change")
        elif disruption <= 0.35:
            return tr("negotiation.small_change")
        elif disruption <= 0.6:
            return tr("negotiation.moderate_change")
        else:
            return tr("negotiation.significant_change")

    def format_report_text(self, report):
        """Format a class report as plain text for display."""
        lines = []
        lines.append(f"{'=' * 50}")
        lines.append(f"{report['class_name']} ({report['lecturer']})")
        lines.append(f"{'=' * 50}")
        lines.append(f"{tr('labels.status')}: {report['status'].upper()}")
        lines.append(f"{report['summary']}")
        lines.append("")

        if report["blocking_reasons"]:
            lines.append(tr("negotiation.blocking_reasons"))
            for reason in report["blocking_reasons"]:
                lines.append(f"  - {reason}")
            lines.append("")

        if report["suggestions"]:
            lines.append(tr("negotiation.suggested_relaxations"))
            for i, s in enumerate(report["suggestions"], 1):
                lines.append(f"  {i}. {s['description']}")
                lines.append(f"     {s['impact_label']} "
                             f"({s['disruption_label']})")
            lines.append("")

        return "\n".join(lines)

    def format_diagnostic_text(self, diagnostic_summary):
        """Format diagnostic summary as plain text."""
        lines = []
        lines.append(tr("negotiation.diagnostic_summary"))
        lines.append("=" * 50)
        lines.append(diagnostic_summary["overall_assessment"])
        lines.append(f"{tr('analytics.total_classes')}: "
                     f"{diagnostic_summary['total_classes']}")
        lines.append(f"{tr('labels.unplaced')}: "
                     f"{diagnostic_summary['unplaced_count']}")
        lines.append(f"{tr('negotiation.highly_constrained_label')}: "
                     f"{diagnostic_summary['constrained_count']}")
        lines.append("")

        if diagnostic_summary["diagnostics"]:
            lines.append(tr("labels.diagnostics_colon"))
            severity_icons = {"critical": "!!",
                              "high": "!", "medium": "*", "low": "-"}
            for d in diagnostic_summary["diagnostics"]:
                icon = severity_icons.get(d["severity"], "-")
                lines.append(f"  [{icon}] {d['message']}")

        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
#  CONSTRAINT NEGOTIATOR (top-level orchestrator)
# ══════════════════════════════════════════════════════════════════════════

class ConstraintNegotiator:
    """Top-level constraint negotiation orchestrator.

    Integrates InfeasibilityAnalyzer, RelaxationSuggester, and
    NegotiationReportBuilder with the existing scheduling engine
    components (ConstraintValidator, CandidateGenerator,
    ConflictGraphBuilder).

    Usage:
        negotiator = ConstraintNegotiator(state)
        report = negotiator.negotiate_class(cls)
        summary = negotiator.full_diagnostic()
    """

    def __init__(self, state, exclude_ids=None):
        """
        Args:
            state: Schedule state dict.
            exclude_ids: Optional set of class ids to exclude from
                        occupancy (same semantics as ConstraintValidator).
        """
        self.state = state
        self.validator = ConstraintValidator(state, exclude_ids=exclude_ids)
        self.generator = CandidateGenerator(state, validator=self.validator)
        self.analyzer = InfeasibilityAnalyzer(state, self.validator,
                                              self.generator)
        self.suggester = RelaxationSuggester(state, self.validator,
                                            self.generator)
        self.report_builder = NegotiationReportBuilder()

        # Lazily built conflict graph
        self._conflict_graph = None
        self._conflict_analyzer = None

    def _ensure_conflict_graph(self):
        """Build conflict graph lazily."""
        if self._conflict_graph is None:
            all_classes = [c for c in self.state["classes"]
                           if not c["pinned"]]
            builder = ConflictGraphBuilder(self.state, all_classes)
            self._conflict_graph = builder.build()
            self._conflict_analyzer = ConflictAnalyzer(
                self._conflict_graph, self.validator)

    def negotiate_class(self, cls):
        """Run full negotiation for a single class.

        Returns:
            dict: Complete negotiation report with analysis and suggestions.
        """
        analysis = self.analyzer.analyze_class(cls)
        suggestions = self.suggester.suggest_for_class(cls, analysis)
        return self.report_builder.build_class_report(
            analysis, suggestions,
            off_grid_blockers=getattr(
                self.suggester, "last_off_grid_blockers", 0))

    def negotiate_unplaced(self):
        """Run negotiation for all unplaced classes.

        Returns:
            list of negotiation reports, sorted by priority.
        """
        unplaced = [c for c in self.state["classes"]
                    if not c["placed"] and not c["pinned"]]
        reports = []
        for cls in unplaced:
            report = self.negotiate_class(cls)
            reports.append(report)
        reports.sort(key=lambda r: r["priority"])
        return reports

    def full_diagnostic(self):
        """Run a full diagnostic on the entire timetable.

        Returns:
            dict with:
                class_reports: list of per-class negotiation reports
                diagnostic_summary: global diagnostic summary
        """
        self._ensure_conflict_graph()

        # Analyze all unplaced and highly-constrained classes
        all_analyses = self.analyzer.analyze_all_unplaced()

        # Also check placed classes that are highly constrained
        for cls in self.state["classes"]:
            if cls["placed"] or cls["pinned"]:
                analysis = self.analyzer.analyze_class(cls)
                if analysis["valid_slots"] < 5:
                    all_analyses.append(analysis)

        # Build per-class reports
        class_reports = []
        for analysis in all_analyses:
            cls = self._find_class_by_name(analysis["class_name"])
            if cls is None:
                continue
            suggestions = self.suggester.suggest_for_class(cls, analysis)
            report = self.report_builder.build_class_report(
                analysis, suggestions)
            class_reports.append(report)

        class_reports.sort(key=lambda r: r["priority"])

        # Build global diagnostic
        diagnostic_summary = self.report_builder.build_diagnostic_summary(
            self.state, all_analyses,
            conflict_graph=self._conflict_graph,
            analyzer=self._conflict_analyzer)

        return {
            "class_reports": class_reports,
            "diagnostic_summary": diagnostic_summary,
        }

    def negotiate_after_optimization(self, placed_list, unplaced_list):
        """Run negotiation specifically after an optimization pass.

        Called when ScheduleOptimizer.optimize() leaves unplaced classes
        or produces a low-quality result.

        Args:
            placed_list: [(cls, day, slot, room), ...] from optimizer
            unplaced_list: [(cls, reason), ...] from optimizer

        Returns:
            dict with negotiation results for unplaced classes and
            diagnostic summary.
        """
        if not unplaced_list:
            return None

        # ST-PERF-007: every unplaced class used to be analysed TWICE — once
        # here and again below to build the identical `all_analyses` list.
        # Built before the sort on purpose: the diagnostic summary expects them
        # in unplaced_list order, not in report-priority order.
        analyses = [self.analyzer.analyze_class(cls) for cls, _ in unplaced_list]
        class_reports = [
            self.report_builder.build_class_report(
                analysis, self.suggester.suggest_for_class(cls, analysis))
            for (cls, _reason), analysis in zip(unplaced_list, analyses)
        ]

        class_reports.sort(key=lambda r: r["priority"])

        self._ensure_conflict_graph()
        all_analyses = analyses
        diagnostic_summary = self.report_builder.build_diagnostic_summary(
            self.state, all_analyses,
            conflict_graph=self._conflict_graph,
            analyzer=self._conflict_analyzer)

        return {
            "class_reports": class_reports,
            "diagnostic_summary": diagnostic_summary,
            "unplaced_count": len(unplaced_list),
        }

    def apply_suggestion(self, cls, suggestion):
        """Apply a single relaxation suggestion to a class.

        Modifies the class dict in-place. Does NOT trigger rescheduling.

        Args:
            cls: The class dict to modify.
            suggestion: A suggestion dict from a negotiation report.

        Returns:
            True if the suggestion was applied, False otherwise.
        """
        field = suggestion.get("constraint_field")
        value = suggestion.get("constraint_value")
        stype = suggestion.get("type", "")

        if not field or value is None:
            return False

        if stype in ("allow_day", "allow_time", "allow_room"):
            # Add value to the constraint list
            current = cls.get(field, [])
            if value not in current:
                current.append(value)
                cls[field] = current
                return True

        elif stype in ("remove_excluded_day", "remove_excluded_time",
                        "remove_excluded_room"):
            # Remove value from the exclusion list
            current = cls.get(field, [])
            if value in current:
                current.remove(value)
                cls[field] = current
                return True

        return False

    def _find_class_by_name(self, name):
        """Find a class by name in the state."""
        for cls in self.state["classes"]:
            if cls["name"] == name:
                return cls
        return None
