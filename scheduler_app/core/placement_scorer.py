"""Soft-scoring optimization layer for candidate placement ranking.

Ranks valid candidate placements by timetable quality. All candidates
have already passed hard constraints via ConstraintValidator — this
module only evaluates quality. Lower score = better placement.

Priority order:
  1. Lecturer compactness (highest weight)
  2. Student group compactness (second priority)
  3. Fragmentation reduction
  4. Gap minimization
  5. Low-quality placement avoidance

Includes look-ahead scoring: each candidate is also evaluated based on
its future impact on unscheduled classes. A placement that restricts
future options incurs a difficulty penalty.
"""

from scheduler_app.logic import slot_index, total_duration, _active_targets
from scheduler_app.models import get_effective_room_resource_for_class, effective_day, cls_key


# Default weight profile — can be overridden by PreferenceLearner
DEFAULT_WEIGHTS = {
    "lecturer_gap":         5.0,   # Per gap slot between lecturer classes
    "lecturer_cluster":     2.5,   # Reward for placing on a day lecturer is active
    "student_gap":          2.5,   # Per gap slot between student group classes
    "student_cluster":      1.2,   # Reward for placing on a day group is active
    "day_overload":         1.5,   # Penalty for overloading a single day
    "fragmentation":        1.8,   # Penalty for isolated single-class days
    "early_slot_bonus":     0.3,   # Mild preference for earlier slots (avoids late)
    "day_spread":           0.05,  # Tiny bias toward earlier days for tidiness
    "slot_position":        0.01,  # Tiny bias toward earlier slots for tidiness
    "room_switch_penalty":  0.8,   # Penalty for switching rooms on same day per entity
    "end_of_day_penalty":   0.6,   # Penalty for using last slots of the day
    "midday_bonus":         0.2,   # Mild preference for mid-morning slots
    "lookahead_penalty":    3.0,   # Per-class difficulty increase from this placement
    "neighbor_impact_penalty": 4.0,  # Penalty for constraining conflict-graph neighbors
    "stability_penalty":    2.0,   # Penalty for moving a class from its current position
}


class PlacementScorer:
    """Score candidate placements for soft-objective optimization.

    Uses occupancy maps from the ConstraintValidator to evaluate
    timetable quality metrics. Accepts weight overrides from the
    PreferenceLearner for adaptive scoring.
    """

    def __init__(self, state, validator, weights=None, conflict_graph=None,
                 propagator=None, parallel_pool=None,
                 previous_placements=None):
        self.state = state
        self.validator = validator
        self.conflict_graph = conflict_graph
        self.propagator = propagator
        self.parallel_pool = parallel_pool
        self.previous_placements = previous_placements or {}
        self.weights = dict(DEFAULT_WEIGHTS)
        if weights:
            self.weights.update(weights)

    def score(self, cls, day, slot, room):
        """Score a single placement. Lower is better.

        Assumes the placement already passes hard constraints.
        """
        return self._score_internal(cls, day, slot, room)[0]

    def score_explained(self, cls, day, slot, room):
        """Score a placement and return (score, breakdown_dict).

        The breakdown dict maps component names to their numerical
        contribution. Negative values are bonuses, positive are penalties.
        """
        return self._score_internal(cls, day, slot, room)

    def _score_internal(self, cls, day, slot, room):
        """Internal scoring with full breakdown. Returns (score, breakdown)."""
        w = self.weights
        score = 0.0
        breakdown = {}
        si = slot_index(self.state, slot)
        day_idx = (self.state["days"].index(day)
                   if day in self.state["days"] else 0)
        total_slots = len(self.state["slots"])
        effective_room = get_effective_room_resource_for_class(
            cls, room_override=room)

        # ── 1. Lecturer compactness (highest priority) ──
        lect_key = cls["lecturer"]
        if lect_key:
            lect_gap = self._compactness_gap(
                day, si, lect_key, self.validator.lect_occ)
            gap_penalty = lect_gap * w["lecturer_gap"]
            score += gap_penalty
            if gap_penalty != 0:
                breakdown["lecturer_gap"] = gap_penalty

            lect_on_day = self._count_on_day(
                day, lect_key, self.validator.lect_occ)
            if lect_on_day > 0:
                score -= w["lecturer_cluster"]
                breakdown["lecturer_cluster"] = -w["lecturer_cluster"]

            # Room switching: check if lecturer uses a different room on
            # this day
            if self._lecturer_switches_room(day, lect_key, effective_room):
                score += w["room_switch_penalty"]
                breakdown["room_switch"] = w["room_switch_penalty"]

        # ── 2. Student group compactness (second priority) ──
        student_gap_total = 0.0
        student_cluster_total = 0.0
        for t in cls["targets"]:
            grp_key = (t["year"], t["branch"])
            grp_gap = self._compactness_gap(
                day, si, grp_key, self.validator.group_occ)
            student_gap_total += grp_gap * w["student_gap"]

            grp_on_day = self._count_on_day(
                day, grp_key, self.validator.group_occ)
            if grp_on_day > 0:
                student_cluster_total -= w["student_cluster"]

        score += student_gap_total + student_cluster_total
        if student_gap_total != 0:
            breakdown["student_gap"] = student_gap_total
        if student_cluster_total != 0:
            breakdown["student_cluster"] = student_cluster_total

        # ── 3. Fragmentation penalty ──
        # Penalize creating an isolated class on a day with no others
        if lect_key:
            lect_on_day = self._count_on_day(
                day, lect_key, self.validator.lect_occ)
            if lect_on_day == 0:
                # This would be the only class for this lecturer on this day
                # Check how many other days the lecturer already has classes
                other_days_with_classes = sum(
                    1 for d in self.state["days"]
                    if d != day and self._count_on_day(
                        d, lect_key, self.validator.lect_occ) > 0)
                if other_days_with_classes > 0:
                    score += w["fragmentation"]
                    breakdown["fragmentation"] = w["fragmentation"]

        # ── 4. Day overload penalty ──
        if lect_key:
            lect_day_load = self._count_on_day(
                day, lect_key, self.validator.lect_occ)
            if lect_day_load > total_slots * 0.6:
                score += w["day_overload"]
                breakdown["day_overload"] = w["day_overload"]

        # ── 5. Time-of-day quality ──
        # Penalize last slots of the day
        td = total_duration(cls)
        time_quality = 0.0
        if si + td >= total_slots:
            time_quality += w["end_of_day_penalty"]

        # Mild preference for mid-morning (slots 1-3 of the day)
        if 1 <= si <= 3:
            time_quality -= w["midday_bonus"]

        # Penalize very early (first slot) mildly
        if si == 0 and total_slots > 4:
            time_quality += w["early_slot_bonus"] * 0.3

        score += time_quality
        if time_quality != 0:
            breakdown["time_quality"] = time_quality

        # ── 6. Structural tidiness (lowest priority) ──
        tidiness = day_idx * w["day_spread"] + si * w["slot_position"]
        score += tidiness
        if tidiness != 0:
            breakdown["tidiness"] = tidiness

        # ── 7. Schedule stability ──
        prev = self.previous_placements.get(cls_key(cls))
        if prev is not None:
            prev_day, prev_slot, prev_room = prev
            if (day, slot, room) != (prev_day, prev_slot, prev_room):
                penalty = w["stability_penalty"]
                # Same day but different slot/room is less disruptive
                if day == prev_day:
                    penalty *= 0.5
                score += penalty
                breakdown["stability"] = penalty

        return score, breakdown

    def score_with_lookahead(self, cls, day, slot, room, remaining_classes,
                             generator):
        """Score a placement including its impact on future scheduling.

        Temporarily places the class, counts how many valid slots remain
        for each unscheduled class, and penalizes if options shrink.

        When a ConstraintPropagator is available, uses cached valid counts
        with incremental invalidation instead of recomputing from scratch.

        Args:
            cls: The class being placed.
            day, slot, room: Candidate placement.
            remaining_classes: Classes still to be scheduled after this one.
            generator: CandidateGenerator for counting future options.

        Returns:
            Combined score (immediate quality + future difficulty penalty).
        """
        immediate = self.score(cls, day, slot, room)

        if not remaining_classes:
            return immediate

        w = self.weights
        prop = self.propagator

        # Snapshot: count current options for remaining classes
        before_counts = {}
        if prop is not None:
            for rc in remaining_classes:
                before_counts[cls_key(rc)] = prop.get_valid_count_fast(rc)
            # Temporarily place via propagator (incremental invalidation)
            prop.simulate_placement(cls, day, slot, room)
        else:
            for rc in remaining_classes:
                before_counts[cls_key(rc)] = self._count_valid_fast(rc, generator)
            self.validator.add_placement(cls, day, slot, room)

        # Count options after placement
        penalty = 0.0
        for rc in remaining_classes:
            if prop is not None:
                after = prop.get_valid_count_fast(rc)
            else:
                after = self._count_valid_fast(rc, generator)
            before = before_counts[cls_key(rc)]
            if before == 0:
                continue
            if after == 0:
                # This placement would make a future class unplaceable
                penalty += w["lookahead_penalty"] * 10.0
            else:
                # Fractional reduction in options
                reduction = 1.0 - (after / before)
                if reduction > 0:
                    penalty += reduction * w["lookahead_penalty"]

        # Focused neighbor impact: check graph neighbors that are
        # still unscheduled (already temporarily placed above)
        if self.conflict_graph is not None:
            remaining_ids = {cls_key(rc) for rc in remaining_classes}
            penalty += self._neighbor_impact(
                cls, generator, remaining_ids, before_counts)

        # Revert temporary placement
        if prop is not None:
            prop.revert_simulation()
        else:
            self.validator.remove_placement(cls, day, slot, room)

        return immediate + penalty

    def _neighbor_impact(self, cls, generator, remaining_ids, before_counts):
        """Calculate impact of current temporary placement on graph neighbors.

        Called while the class is temporarily placed in the validator.
        Only examines graph neighbors that are still in remaining_ids,
        avoiding redundant work for classes already checked by the
        generic lookahead.

        Returns:
            float: penalty score (>= 0).
        """
        graph = self.conflict_graph
        idx = graph.get_index(cls)
        if idx is None:
            return 0.0

        w = self.weights
        penalty = 0.0

        for nbr_idx in graph.neighbors(idx):
            nbr_cls = graph.nodes[nbr_idx]
            nbr_id = cls_key(nbr_cls)
            if nbr_id not in remaining_ids:
                continue
            # Skip if already counted in the generic lookahead
            if nbr_id in before_counts:
                continue

            if self.propagator is not None:
                before = self.propagator.get_valid_count_fast(nbr_cls)
            else:
                before = self._count_valid_fast(nbr_cls, generator)
            # We need a baseline — but the class is already temporarily
            # placed, so 'before' here is actually 'after'. We approximate
            # by using the neighbor's degree as a severity multiplier.
            edge_weight = sum(
                ew for nn, _, ew in graph.adjacency.get(nbr_idx, [])
                if nn == idx)
            if before == 0:
                penalty += w["neighbor_impact_penalty"] * 5.0 * edge_weight
            elif before <= 3:
                penalty += w["neighbor_impact_penalty"] * 2.0 * edge_weight

        return penalty

    def score_candidates(self, cls, candidates):
        """Score and sort a list of (day, slot, room) candidates.

        Returns [(day, slot, room, score), ...] sorted best-first.
        """
        scored = []
        for day, slot, room in candidates:
            s = self.score(cls, day, slot, room)
            scored.append((day, slot, room, s))
        scored.sort(key=lambda x: x[3])
        return scored

    def score_candidates_with_lookahead(self, cls, candidates,
                                        remaining_classes, generator,
                                        max_lookahead_candidates=15):
        """Score candidates with look-ahead analysis.

        For performance, only evaluates look-ahead on top candidates
        from immediate scoring. When a parallel pool is available and
        the candidate count justifies it, distributes the expensive
        lookahead phase across worker processes.

        Returns [(day, slot, room, score), ...] sorted best-first.
        """
        if not remaining_classes or not candidates:
            return self.score_candidates(cls, candidates)

        # First pass: score all candidates by immediate quality
        immediate = self.score_candidates(cls, candidates)

        # Second pass: evaluate look-ahead only on top candidates
        top_n = min(max_lookahead_candidates, len(immediate))
        top_candidates = immediate[:top_n]
        top_tuples = [(d, s, r) for d, s, r, _ in top_candidates]

        # Try parallel evaluation for the lookahead phase
        scored = None
        if (self.parallel_pool is not None
                and self.parallel_pool.should_parallelize(top_n)):
            scored = self.parallel_pool.score_batch(
                self.state, self.validator, self.weights,
                cls, top_tuples, remaining_classes)

        if scored is None:
            # Sequential fallback
            scored = []
            for day, slot, room, _ in top_candidates:
                s = self.score_with_lookahead(
                    cls, day, slot, room, remaining_classes, generator)
                scored.append((day, slot, room, s))

        # Keep the rest with a pessimistic score offset
        if top_n < len(immediate):
            worst_top = max(s for _, _, _, s in scored) if scored else 0
            for day, slot, room, imm in immediate[top_n:]:
                scored.append((day, slot, room, imm + worst_top))

        scored.sort(key=lambda x: x[3])
        return scored

    def timetable_quality(self, placements):
        """Evaluate the overall quality of a complete timetable.

        Args:
            placements: list of (cls, day, slot, room) tuples

        Returns:
            A quality score (lower is better) aggregating all soft metrics.
        """
        total = 0.0
        # Build temporary occupancy for evaluation
        lect_days = {}  # lecturer -> {day -> [slot_indices]}
        group_days = {}  # (year, branch) -> {day -> [slot_indices]}
        lect_rooms = {}  # (lecturer, day) -> set of rooms

        for cls, day, slot, room in placements:
            si = slot_index(self.state, slot)
            td = total_duration(cls)
            lect = cls["lecturer"]
            effective_room = get_effective_room_resource_for_class(
                cls, room_override=room)
            if lect:
                lect_days.setdefault(lect, {}).setdefault(day, [])
                for off in range(td):
                    lect_days[lect][day].append(si + off)
                if effective_room:
                    lect_rooms.setdefault((lect, day), set()).add(effective_room)
            for t in cls["targets"]:
                gk = (t["year"], t["branch"])
                group_days.setdefault(gk, {}).setdefault(day, [])
                for off in range(td):
                    group_days[gk][day].append(si + off)

        w = self.weights

        # Lecturer compactness: sum of gaps per lecturer per day
        for lect, days_map in lect_days.items():
            for day, indices in days_map.items():
                if len(indices) <= 1:
                    continue
                indices.sort()
                gaps = sum(indices[i+1] - indices[i] - 1
                           for i in range(len(indices) - 1)
                           if indices[i+1] - indices[i] > 1)
                total += gaps * w["lecturer_gap"]
            # Fragmentation: count active days
            if len(days_map) > 1:
                single_class_days = sum(
                    1 for d, idx in days_map.items() if len(idx) <= 1)
                total += single_class_days * w["fragmentation"] * 0.5

        # Student group compactness
        for gk, days_map in group_days.items():
            for day, indices in days_map.items():
                if len(indices) <= 1:
                    continue
                indices.sort()
                gaps = sum(indices[i+1] - indices[i] - 1
                           for i in range(len(indices) - 1)
                           if indices[i+1] - indices[i] > 1)
                total += gaps * w["student_gap"]

        # Room switching penalty
        for (lect, day), rooms in lect_rooms.items():
            if len(rooms) > 1:
                total += (len(rooms) - 1) * w["room_switch_penalty"]

        return total

    def _count_valid_fast(self, cls, generator):
        """Quickly count valid placements for a class."""
        days, times, rooms = generator.get_search_space(cls)
        count = 0
        for day in days:
            for slot in times:
                for room in rooms:
                    if self.validator.check_placement(cls, day, slot, room):
                        count += 1
                        if count > 20:
                            return count  # Early exit: plenty of options
        return count

    def _compactness_gap(self, day, slot_idx, entity_key, occ_map):
        """Calculate gap between this placement and nearest existing class."""
        occupied_indices = []
        for si, s in enumerate(self.state["slots"]):
            key = (day, s)
            if entity_key in occ_map.get(key, set()):
                occupied_indices.append(si)
        if not occupied_indices:
            return 0
        min_dist = min(abs(slot_idx - oi) for oi in occupied_indices)
        return max(0, min_dist - 1)

    def _count_on_day(self, day, entity_key, occ_map):
        """Count how many slots an entity occupies on a given day."""
        count = 0
        for s in self.state["slots"]:
            if entity_key in occ_map.get((day, s), set()):
                count += 1
        return count

    def _lecturer_switches_room(self, day, lecturer, new_room):
        """Check if placing in new_room would create a room switch for
        the lecturer on this day."""
        for cls in self.state["classes"]:
            if cls["lecturer"] != lecturer:
                continue
            if not (cls["placed"] or cls["pinned"]):
                continue
            cls_day = effective_day(cls)
            if cls_day != day:
                continue
            cls_room = get_effective_room_resource_for_class(cls)
            if cls_room and cls_room != new_room:
                return True
        return False
