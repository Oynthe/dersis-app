"""Large Neighborhood Search destroy and repair strategies.

Destroy strategies identify weak parts of the timetable to remove.
Repair strategies reinsert removed classes using scored placement.

All strategies preserve pinned and protected classes.
"""

import random

from scheduler_app.logic import slot_index, total_duration, _active_targets
from scheduler_app.models import cls_key, DEFAULT_OPTIMIZER_SEED
from scheduler_app.placement_scorer import DEFAULT_WEIGHTS


# ══════════════════════════════════════════════════════════════════════════
#  DESTROY STRATEGIES
# ══════════════════════════════════════════════════════════════════════════

class DestroyStrategy:
    """Base class for LNS destroy strategies.

    A destroy strategy selects a subset of non-pinned, non-protected
    classes to temporarily remove from the timetable.
    """

    def __init__(self, state, weights=None, rng=None):
        self.state = state
        self.weights = weights or dict(DEFAULT_WEIGHTS)
        # ST-SCHED-013. The stream is injected, never created per instance: the
        # selector builds a fresh strategy every LNS iteration, so a per-instance
        # Random(seed) would replay the identical shuffle forever and destroy
        # exploration while still looking deterministic. The fallback exists
        # only for direct construction outside the selector.
        self.rng = rng if rng is not None else random.Random(DEFAULT_OPTIMIZER_SEED)

    def select(self, solution, flexible, destroy_size, pinned_ids=None,
               protected_ids=None):
        """Select classes to remove.

        Args:
            solution: list of (day, slot, room) or None, parallel to flexible.
            flexible: list of class dicts (non-pinned classes).
            destroy_size: target number of classes to remove.
            pinned_ids: set of class ids that must not be touched.
            protected_ids: set of class ids that must not be touched.

        Returns:
            List of indices into flexible to remove.
        """
        raise NotImplementedError


class LecturerGapDestroy(DestroyStrategy):
    """Remove classes that contribute to lecturer timetable gaps."""

    def select(self, solution, flexible, destroy_size, pinned_ids=None,
               protected_ids=None):
        pinned_ids = pinned_ids or set()
        protected_ids = protected_ids or set()

        # Build lecturer -> day -> [(slot_idx, flex_index)]
        lect_schedule = {}
        for i, cls in enumerate(flexible):
            if solution[i] is None:
                continue
            if cls_key(cls) in pinned_ids or cls_key(cls) in protected_ids:
                continue
            day, slot, room = solution[i]
            si = slot_index(self.state, slot)
            td = total_duration(cls)
            lect = cls["lecturer"]
            if not lect:
                continue
            for off in range(td):
                lect_schedule.setdefault(lect, {}).setdefault(
                    day, []).append((si + off, i))

        # Find classes involved in gaps
        gap_classes = {}  # flex_index -> gap contribution
        for lect, days_map in lect_schedule.items():
            for day, entries in days_map.items():
                if len(entries) <= 1:
                    continue
                entries.sort(key=lambda x: x[0])
                slots_only = [e[0] for e in entries]
                unique_slots = sorted(set(slots_only))
                if len(unique_slots) <= 1:
                    continue
                span = unique_slots[-1] - unique_slots[0] + 1
                gap_count = span - len(unique_slots)
                if gap_count > 0:
                    # Credit gap to all classes on this day for this lecturer
                    seen = set()
                    for _, idx in entries:
                        if idx not in seen:
                            gap_classes[idx] = gap_classes.get(idx, 0) + gap_count
                            seen.add(idx)

        # Sort by gap contribution (highest first) and pick top
        ranked = sorted(gap_classes.keys(),
                        key=lambda i: gap_classes[i], reverse=True)
        return ranked[:destroy_size]


class StudentGapDestroy(DestroyStrategy):
    """Remove classes that contribute to student group idle periods."""

    def select(self, solution, flexible, destroy_size, pinned_ids=None,
               protected_ids=None):
        pinned_ids = pinned_ids or set()
        protected_ids = protected_ids or set()

        group_schedule = {}  # (year, branch) -> day -> [(slot, flex_idx)]
        for i, cls in enumerate(flexible):
            if solution[i] is None:
                continue
            if cls_key(cls) in pinned_ids or cls_key(cls) in protected_ids:
                continue
            day, slot, room = solution[i]
            si = slot_index(self.state, slot)
            td = total_duration(cls)
            for t in cls["targets"]:
                gk = (t["year"], t["branch"])
                for off in range(td):
                    group_schedule.setdefault(gk, {}).setdefault(
                        day, []).append((si + off, i))

        gap_classes = {}
        for gk, days_map in group_schedule.items():
            for day, entries in days_map.items():
                if len(entries) <= 1:
                    continue
                entries.sort(key=lambda x: x[0])
                unique_slots = sorted(set(e[0] for e in entries))
                if len(unique_slots) <= 1:
                    continue
                span = unique_slots[-1] - unique_slots[0] + 1
                gap_count = span - len(unique_slots)
                if gap_count > 0:
                    seen = set()
                    for _, idx in entries:
                        if idx not in seen:
                            gap_classes[idx] = gap_classes.get(idx, 0) + gap_count
                            seen.add(idx)

        ranked = sorted(gap_classes.keys(),
                        key=lambda i: gap_classes[i], reverse=True)
        return ranked[:destroy_size]


class LowScoreDestroy(DestroyStrategy):
    """Remove the lowest-scoring (worst quality) placements."""

    def select(self, solution, flexible, destroy_size, pinned_ids=None,
               protected_ids=None):
        pinned_ids = pinned_ids or set()
        protected_ids = protected_ids or set()

        from scheduler_app.timetable_scorer import TimetableScorer
        scorer = TimetableScorer(self.state, weights=self.weights)

        scored = []
        for i, cls in enumerate(flexible):
            if solution[i] is None:
                continue
            if cls_key(cls) in pinned_ids or cls_key(cls) in protected_ids:
                continue
            day, slot, room = solution[i]
            s = scorer.placement_score(cls, day, slot, room)
            scored.append((i, s))

        # Highest score = worst placement
        scored.sort(key=lambda x: x[1], reverse=True)
        return [i for i, _ in scored[:destroy_size]]


class ConflictClusterDestroy(DestroyStrategy):
    """Remove classes within dense conflict clusters (same lecturer/group
    with overlapping schedules on the same day)."""

    def select(self, solution, flexible, destroy_size, pinned_ids=None,
               protected_ids=None):
        pinned_ids = pinned_ids or set()
        protected_ids = protected_ids or set()

        # Build adjacency: which classes share a lecturer or group on same day
        day_lect = {}  # (day, lecturer) -> [flex_indices]
        day_group = {}  # (day, (year, branch)) -> [flex_indices]

        for i, cls in enumerate(flexible):
            if solution[i] is None:
                continue
            if cls_key(cls) in pinned_ids or cls_key(cls) in protected_ids:
                continue
            day = solution[i][0]
            lect = cls["lecturer"]
            if lect:
                day_lect.setdefault((day, lect), []).append(i)
            for t in cls["targets"]:
                gk = (t["year"], t["branch"])
                day_group.setdefault((day, gk), []).append(i)

        # Count cluster membership
        cluster_score = {}
        for key, indices in day_lect.items():
            if len(indices) >= 3:
                for idx in indices:
                    cluster_score[idx] = cluster_score.get(idx, 0) + len(indices)
        for key, indices in day_group.items():
            if len(indices) >= 3:
                for idx in indices:
                    cluster_score[idx] = cluster_score.get(idx, 0) + len(indices)

        ranked = sorted(cluster_score.keys(),
                        key=lambda i: cluster_score[i], reverse=True)
        return ranked[:destroy_size]


class DayWindowDestroy(DestroyStrategy):
    """Remove all classes from a problematic day or time window."""

    def select(self, solution, flexible, destroy_size, pinned_ids=None,
               protected_ids=None):
        pinned_ids = pinned_ids or set()
        protected_ids = protected_ids or set()

        # Find the day with worst gap ratio
        day_indices = {}  # day -> [flex_indices]
        for i, cls in enumerate(flexible):
            if solution[i] is None:
                continue
            if cls_key(cls) in pinned_ids or cls_key(cls) in protected_ids:
                continue
            day = solution[i][0]
            day_indices.setdefault(day, []).append(i)

        if not day_indices:
            return []

        # Score each day by per-entity gap density (lecturer + student group)
        day_scores = {}
        for day, indices in day_indices.items():
            # Build per-entity slot maps
            entity_slots = {}  # entity_key -> set of slot indices
            for idx in indices:
                cls = flexible[idx]
                si = slot_index(self.state, solution[idx][1])
                td = total_duration(cls)
                lect = cls.get("lecturer")
                if lect:
                    key = ("lect", lect)
                    entity_slots.setdefault(key, set()).update(
                        range(si, si + td))
                for t in cls.get("targets", []):
                    key = ("grp", t["year"], t["branch"])
                    entity_slots.setdefault(key, set()).update(
                        range(si, si + td))
            # Compute gap density per entity
            total_gaps = 0
            num_entities = 0
            for slots_set in entity_slots.values():
                if len(slots_set) <= 1:
                    continue
                span = max(slots_set) - min(slots_set) + 1
                gaps = span - len(slots_set)
                total_gaps += gaps
                num_entities += 1
            day_scores[day] = total_gaps / max(1, num_entities)

        if not day_scores:
            return []

        # Pick the worst day
        worst_day = max(day_scores, key=day_scores.get)
        candidates = day_indices[worst_day]
        self.rng.shuffle(candidates)
        return candidates[:destroy_size]


class RandomDestroy(DestroyStrategy):
    """Remove a random subset for exploration diversity."""

    def select(self, solution, flexible, destroy_size, pinned_ids=None,
               protected_ids=None):
        pinned_ids = pinned_ids or set()
        protected_ids = protected_ids or set()

        eligible = [i for i, cls in enumerate(flexible)
                    if solution[i] is not None
                    and cls_key(cls) not in pinned_ids
                    and cls_key(cls) not in protected_ids]
        self.rng.shuffle(eligible)
        return eligible[:destroy_size]


class ConflictGraphDestroy(DestroyStrategy):
    """Destroy a tightly connected subgraph from the conflict graph.

    Uses the precomputed conflict graph to find structurally dense
    clusters regardless of current-day placement, unlike
    ConflictClusterDestroy which rebuilds ad-hoc adjacency per call.
    """

    def __init__(self, state, weights=None, conflict_graph=None,
                 analyzer=None, rng=None):
        super().__init__(state, weights, rng=rng)
        self.conflict_graph = conflict_graph
        self.analyzer = analyzer

    def select(self, solution, flexible, destroy_size, pinned_ids=None,
               protected_ids=None):
        pinned_ids = pinned_ids or set()
        protected_ids = protected_ids or set()

        if self.conflict_graph is None:
            return []

        graph = self.conflict_graph

        # Build eligible set: placed, not pinned/protected
        eligible = []
        eligible_set = set()
        for i, cls in enumerate(flexible):
            if solution[i] is None:
                continue
            if cls_key(cls) in pinned_ids or cls_key(cls) in protected_ids:
                continue
            idx = graph.get_index(cls)
            if idx is not None:
                eligible.append(i)
                eligible_set.add(i)

        if not eligible:
            return []

        # Pick seed: highest-degree eligible node
        best_seed = None
        best_degree = -1
        for i in eligible:
            cls = flexible[i]
            idx = graph.get_index(cls)
            if idx is not None:
                deg = graph.degree(idx)
                if deg > best_degree:
                    best_degree = deg
                    best_seed = i

        if best_seed is None:
            return eligible[:destroy_size]

        # BFS from seed through graph edges, collecting eligible neighbors
        seed_graph_idx = graph.get_index(flexible[best_seed])
        # Map graph indices back to flexible indices
        graph_to_flex = {}
        for i in eligible:
            gidx = graph.get_index(flexible[i])
            if gidx is not None:
                graph_to_flex[gidx] = i

        from collections import deque
        result = [best_seed]
        visited = {seed_graph_idx}
        queue = deque([seed_graph_idx])

        while queue and len(result) < destroy_size:
            node = queue.popleft()
            for nbr in graph.neighbors(node):
                if nbr in visited:
                    continue
                visited.add(nbr)
                flex_idx = graph_to_flex.get(nbr)
                if flex_idx is not None and flex_idx in eligible_set:
                    result.append(flex_idx)
                    if len(result) >= destroy_size:
                        break
                queue.append(nbr)

        return result


# ══════════════════════════════════════════════════════════════════════════
#  REPAIR STRATEGY
# ══════════════════════════════════════════════════════════════════════════

class RepairStrategy:
    """Reinsert removed classes using look-ahead scored placement.

    Uses difficulty-aware ordering, candidate generation, and
    look-ahead scoring to find the best placement for each class.
    """

    def __init__(self, state, validator, generator, scorer, weights=None,
                 same_day_map=None, improve_only_scores=None,
                 propagator=None):
        self.state = state
        self.validator = validator
        self.generator = generator
        self.scorer = scorer
        self.same_day_map = same_day_map or {}
        self.improve_only_scores = improve_only_scores or {}
        self.propagator = propagator

    def repair(self, removed_indices, solution, flexible):
        """Reinsert removed classes into the timetable.

        Args:
            removed_indices: Indices into flexible that were removed.
            solution: Current solution array (entries at removed_indices
                      should already be None and occupancy cleared).
            flexible: List of all flexible class dicts.

        Returns:
            Number of classes successfully reinserted.
        """
        # Sort removed by difficulty (hardest first)
        removed_with_diff = []
        for idx in removed_indices:
            cls = flexible[idx]
            diff = self.validator.scheduling_difficulty(cls)
            removed_with_diff.append((idx, diff))
        removed_with_diff.sort(key=lambda x: x[1])

        # Build list of remaining unscheduled for look-ahead
        repaired = 0

        for pos, (idx, _) in enumerate(removed_with_diff):
            cls = flexible[idx]

            # Remaining classes for look-ahead (those not yet reinserted)
            remaining = [flexible[ri]
                         for ri, _ in removed_with_diff[pos + 1:]
                         if solution[ri] is None]

            candidates = self.generator.generate(cls)
            if not candidates:
                continue

            # Enforce same-day constraint if applicable
            original_day = self.same_day_map.get(cls_key(cls))
            if original_day is not None:
                candidates = [(d, s, r) for d, s, r in candidates
                              if d == original_day]
                if not candidates:
                    continue

            # Score with look-ahead if there are remaining classes
            if remaining and len(candidates) > 1:
                scored = self.scorer.score_candidates_with_lookahead(
                    cls, candidates, remaining, self.generator)
            else:
                scored = self.scorer.score_candidates(cls, candidates)

            # Enforce improve_only: skip candidates worse than baseline
            baseline = self.improve_only_scores.get(cls_key(cls))
            if baseline is not None:
                scored = [(d, s, r, sc) for d, s, r, sc in scored
                          if sc <= baseline]
                if not scored:
                    continue

            # Pick the best
            best_day, best_slot, best_room, _ = scored[0]
            solution[idx] = (best_day, best_slot, best_room)
            if self.propagator is not None:
                self.propagator.add_placement(cls, best_day, best_slot, best_room)
            else:
                self.validator.add_placement(cls, best_day, best_slot, best_room)
            repaired += 1

        return repaired


# ══════════════════════════════════════════════════════════════════════════
#  STRATEGY ROTATION & ADAPTIVE SELECTION
# ══════════════════════════════════════════════════════════════════════════

ALL_DESTROY_STRATEGIES = [
    LecturerGapDestroy,
    StudentGapDestroy,
    LowScoreDestroy,
    ConflictClusterDestroy,
    DayWindowDestroy,
    RandomDestroy,
    ConflictGraphDestroy,
]


def get_destroy_strategy(state, iteration, weights=None, rng=None):
    """Select a destroy strategy based on iteration number.

    Rotates through targeted strategies and intersperses random
    exploration for diversity.
    """
    # Every 5th iteration use random for exploration
    if iteration % 5 == 4:
        return RandomDestroy(state, weights=weights, rng=rng)

    # Rotate through targeted strategies
    targeted = [
        LecturerGapDestroy,
        StudentGapDestroy,
        LowScoreDestroy,
        ConflictClusterDestroy,
        DayWindowDestroy,
    ]
    idx = iteration % len(targeted)
    return targeted[idx](state, weights=weights, rng=rng)


class AdaptiveStrategySelector:
    """Adaptive destroy strategy selector that learns from outcomes.

    Tracks which strategies produce improvements and adjusts selection
    probabilities using a softmax-like weighting with exponential
    moving average of success rates.

    Each strategy starts with equal probability. After each iteration,
    the strategy used is credited with success (quality improved) or
    failure. Probabilities are updated using a decay factor so recent
    performance matters more than historical.
    """

    def __init__(self, state, weights=None, min_prob=0.05, decay=0.95,
                 exploration_rate=0.1, conflict_graph=None, analyzer=None,
                 rng=None):
        """
        Args:
            state: Schedule state dict.
            weights: Optional weight overrides for strategies.
            min_prob: Minimum selection probability per strategy.
            decay: Exponential decay for historical scores (0-1).
            exploration_rate: Fraction of iterations using random strategy.
            conflict_graph: Optional ConflictGraph for graph-aware strategies.
            analyzer: Optional ConflictAnalyzer for graph-aware strategies.
            rng: The optimizer's random stream. Shared with every strategy this
                 selector builds, so the whole LNS phase is reproducible from a
                 single seed (ST-SCHED-013).
        """
        self._rng = rng if rng is not None else random.Random(DEFAULT_OPTIMIZER_SEED)
        self.state = state
        self.weights = weights
        self.min_prob = min_prob
        self.decay = decay
        self.exploration_rate = exploration_rate
        self.conflict_graph = conflict_graph
        self.analyzer = analyzer

        self._strategies = [
            LecturerGapDestroy,
            StudentGapDestroy,
            LowScoreDestroy,
            ConflictClusterDestroy,
            DayWindowDestroy,
            RandomDestroy,
            ConflictGraphDestroy,
        ]
        n = len(self._strategies)
        # Running success scores per strategy (higher = better)
        self._scores = [1.0] * n
        # Count of times each strategy was used
        self._uses = [0] * n
        # Count of successes
        self._successes = [0] * n

    def select(self, iteration):
        """Select a destroy strategy for this iteration.

        Returns:
            (strategy_instance, strategy_index)
        """
        # Exploration: random strategy with some probability
        if self._rng.random() < self.exploration_rate:
            idx = self._rng.randrange(len(self._strategies))
        else:
            idx = self._weighted_select()

        strategy_cls = self._strategies[idx]
        if strategy_cls is ConflictGraphDestroy:
            return strategy_cls(self.state, weights=self.weights,
                                conflict_graph=self.conflict_graph,
                                analyzer=self.analyzer, rng=self._rng), idx
        return strategy_cls(self.state, weights=self.weights,
                            rng=self._rng), idx

    def update(self, strategy_idx, improved):
        """Update strategy performance after an iteration.

        Args:
            strategy_idx: Index of the strategy that was used.
            improved: True if the iteration improved the solution.
        """
        self._uses[strategy_idx] += 1
        if improved:
            self._successes[strategy_idx] += 1

        # Decay all scores and boost the successful one
        for i in range(len(self._scores)):
            self._scores[i] *= self.decay
        if improved:
            self._scores[strategy_idx] += 1.0
        else:
            # Small penalty for failure (but not too harsh)
            self._scores[strategy_idx] += 0.1

    def _weighted_select(self):
        """Select a strategy index using probability-weighted sampling."""
        probs = self._get_probabilities()
        r = self._rng.random()
        cumulative = 0.0
        for i, p in enumerate(probs):
            cumulative += p
            if r <= cumulative:
                return i
        return len(probs) - 1

    def _get_probabilities(self):
        """Compute selection probabilities from scores."""
        n = len(self._scores)
        total = sum(self._scores)
        if total <= 0:
            return [1.0 / n] * n

        # Normalize scores to probabilities
        probs = [s / total for s in self._scores]

        # Enforce minimum probability
        deficit = 0.0
        for i in range(n):
            if probs[i] < self.min_prob:
                deficit += self.min_prob - probs[i]
                probs[i] = self.min_prob

        # Redistribute deficit from strategies above minimum
        if deficit > 0:
            above_min = [i for i in range(n)
                         if probs[i] > self.min_prob]
            if above_min:
                per_strategy = deficit / len(above_min)
                for i in above_min:
                    probs[i] = max(self.min_prob, probs[i] - per_strategy)

        # Re-normalize
        total_p = sum(probs)
        if total_p > 0:
            probs = [p / total_p for p in probs]

        return probs

    def get_stats(self):
        """Return strategy performance statistics.

        Returns:
            list of dicts with name, uses, successes, success_rate, probability.
        """
        probs = self._get_probabilities()
        stats = []
        for i, cls in enumerate(self._strategies):
            rate = (self._successes[i] / self._uses[i]
                    if self._uses[i] > 0 else 0.0)
            stats.append({
                "name": cls.__name__,
                "uses": self._uses[i],
                "successes": self._successes[i],
                "success_rate": rate,
                "probability": probs[i],
            })
        return stats
