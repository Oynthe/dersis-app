"""Parallel candidate evaluation using process-based worker pools.

Distributes lookahead candidate scoring across multiple CPU cores via
concurrent.futures.ProcessPoolExecutor. Each worker receives a serialized
snapshot of the schedule state and occupancy maps, reconstructs an
isolated validator/scorer, evaluates its candidate batch, and returns
scores. No shared mutable state between processes.

Integration:
  ParallelScorerPool is created once per optimization and passed to
  PlacementScorer. When the pool is available and the candidate count
  justifies parallelism, score_candidates_with_lookahead distributes
  the expensive lookahead phase across workers.
"""

import os
from concurrent.futures import ProcessPoolExecutor


# ══════════════════════════════════════════════════════════════════════════
#  SERIALIZATION HELPERS
# ══════════════════════════════════════════════════════════════════════════

def create_occupancy_snapshot(validator):
    """Capture a picklable snapshot of validator occupancy maps.

    Returns a dict of deep-copied occupancy data that can be sent to
    worker processes.
    """
    # ST-SCHED-010: cells are ref-counted `{entity: count}` maps, and the
    # worker calls add_placement/remove_placement on the validator it rebuilds
    # from this snapshot. Flattening to a set here would hand the worker cells
    # it cannot decrement correctly, so copy the counts.
    return {
        "room_occ": {k: dict(v) for k, v in validator.room_occ.items()},
        "lect_occ": {k: dict(v) for k, v in validator.lect_occ.items()},
        "group_occ": {k: dict(v) for k, v in validator.group_occ.items()},
    }


def create_state_snapshot(state):
    """Create a minimal picklable state snapshot for workers.

    Includes scheduling metadata and class data but avoids any
    non-picklable objects.
    """
    return {
        "days": list(state["days"]),
        "slots": list(state["slots"]),
        "classrooms": list(state["classrooms"]),
        "classroom_capacities": dict(state.get("classroom_capacities", {})),
        "lecturer_availability": {
            k: dict(v) for k, v in state.get("lecturer_availability", {}).items()
        },
        "classes": [_serialize_class(c) for c in state["classes"]],
    }


def _serialize_class(cls):
    """Serialize a class dict for cross-process transfer."""
    return {
        "name": cls.get("name", ""),
        "lecturer": cls.get("lecturer", ""),
        "targets": [dict(t) for t in cls.get("targets", [])],
        "duration": cls.get("duration", 1),
        "participants": cls.get("participants", 0),
        "location_type": cls.get("location_type", "face_to_face"),
        "joint_session": cls.get("joint_session", True),
        "allowed_days": list(cls.get("allowed_days") or []),
        "excluded_days": list(cls.get("excluded_days") or []),
        "allowed_times": list(cls.get("allowed_times") or []),
        "excluded_times": list(cls.get("excluded_times") or []),
        "required_classrooms": list(cls.get("required_classrooms") or []),
        "excluded_classrooms": list(cls.get("excluded_classrooms") or []),
        "placed": cls.get("placed", False),
        "placed_day": cls.get("placed_day"),
        "placed_time": cls.get("placed_time"),
        "placed_classroom": cls.get("placed_classroom"),
        "pinned": cls.get("pinned", False),
        "pinned_day": cls.get("pinned_day"),
        "pinned_time": cls.get("pinned_time"),
        "pinned_classroom": cls.get("pinned_classroom"),
        "protection": cls.get("protection", "none"),
    }


# ══════════════════════════════════════════════════════════════════════════
#  WORKER FUNCTION (must be top-level for pickling)
# ══════════════════════════════════════════════════════════════════════════

def _score_lookahead_batch(payload):
    """Evaluate a batch of candidate placements in a worker process.

    Reconstructs an isolated validator and scorer from the serialized
    snapshot, then evaluates each candidate with lookahead scoring.

    Args:
        payload: tuple of (state_snap, occ_snap, weights, cls_data,
                 candidate_batch, remaining_data)

    Returns:
        List of (day, slot, room, score) tuples.
    """
    (state_snap, occ_snap, weights, cls_data,
     candidate_batch, remaining_data) = payload

    from scheduler_app.constraint_validator import ConstraintValidator
    from scheduler_app.candidate_generator import CandidateGenerator
    from scheduler_app.placement_scorer import PlacementScorer

    # Reconstruct validator with pre-built occupancy (no exclude_ids
    # needed since occupancy already reflects the correct state)
    validator = ConstraintValidator.__new__(ConstraintValidator)
    validator.state = state_snap
    validator.exclude_ids = set()
    validator.room_occ = occ_snap["room_occ"]
    validator.lect_occ = occ_snap["lect_occ"]
    validator.group_occ = occ_snap["group_occ"]

    generator = CandidateGenerator(state_snap, validator=validator)

    # Create scorer without conflict_graph or propagator (those use
    # id()-based lookups which don't work across process boundaries)
    scorer = PlacementScorer(state_snap, validator, weights=weights)

    results = []
    for day, slot, room in candidate_batch:
        if remaining_data:
            score = scorer.score_with_lookahead(
                cls_data, day, slot, room, remaining_data, generator)
        else:
            score = scorer.score(cls_data, day, slot, room)
        results.append((day, slot, room, score))

    return results


# ══════════════════════════════════════════════════════════════════════════
#  PARALLEL SCORER POOL
# ══════════════════════════════════════════════════════════════════════════

class ParallelScorerPool:
    """Manages a ProcessPoolExecutor for parallel candidate evaluation.

    Creates worker processes on demand and distributes candidate batches
    for lookahead scoring. Each worker operates on an isolated copy of
    the schedule state — no shared mutable state.

    Usage:
        pool = ParallelScorerPool(max_workers=4)
        results = pool.score_batch(state, validator, weights, cls,
                                   candidates, remaining)
        pool.shutdown()
    """

    def __init__(self, max_workers=None, min_candidates=8):
        """
        Args:
            max_workers: Number of worker processes. Defaults to
                         min(cpu_count, 4). Set to 0 to disable.
            min_candidates: Minimum candidate count to justify parallel
                            evaluation. Below this, sequential is faster
                            due to IPC overhead.
        """
        cpu = os.cpu_count() or 1
        self.max_workers = max_workers if max_workers is not None else min(cpu, 4)
        self.min_candidates = min_candidates
        self._pool = None

    def _ensure_pool(self):
        """Lazily create the process pool on first use."""
        if self._pool is None and self.max_workers > 1:
            self._pool = ProcessPoolExecutor(max_workers=self.max_workers)

    def should_parallelize(self, candidate_count):
        """Check if parallelism is worthwhile for this candidate count."""
        return (self.max_workers > 1
                and candidate_count >= self.min_candidates)

    def score_batch(self, state, validator, weights, cls, candidates,
                    remaining_classes):
        """Score candidates in parallel using worker processes.

        Snapshots the current state and occupancy, divides candidates
        into batches, distributes to workers, and collects results.

        Args:
            state: Schedule state dict.
            validator: Current ConstraintValidator (for occupancy snapshot).
            weights: Scoring weight dict.
            cls: The class being placed.
            candidates: List of (day, slot, room) tuples to evaluate.
            remaining_classes: Classes for lookahead scoring.

        Returns:
            List of (day, slot, room, score) sorted best-first.
        """
        if not self.should_parallelize(len(candidates)):
            return None  # Signal caller to use sequential path

        self._ensure_pool()

        # Create serializable snapshots
        state_snap = create_state_snapshot(state)
        occ_snap = create_occupancy_snapshot(validator)
        cls_data = _serialize_class(cls)
        remaining_data = [_serialize_class(rc) for rc in remaining_classes]

        # Divide candidates into batches
        n_workers = min(self.max_workers, len(candidates))
        batches = _split_into_batches(candidates, n_workers)

        # Submit all batches
        payloads = [
            (state_snap, occ_snap, weights, cls_data, batch, remaining_data)
            for batch in batches
        ]

        futures = [self._pool.submit(_score_lookahead_batch, p)
                   for p in payloads]

        # Collect results
        all_results = []
        for future in futures:
            batch_results = future.result()
            all_results.extend(batch_results)

        # Sort best-first
        all_results.sort(key=lambda x: x[3])
        return all_results

    def shutdown(self):
        """Shut down the worker pool and wait for workers to finish."""
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None

    def __del__(self):
        self.shutdown()


def _split_into_batches(items, n_batches):
    """Split a list into n roughly equal batches."""
    n = len(items)
    batch_size = max(1, (n + n_batches - 1) // n_batches)
    return [items[i:i + batch_size] for i in range(0, n, batch_size)]
