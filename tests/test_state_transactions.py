"""State-transaction guarantees: nothing half-applied, nobody clobbering anybody.

Two findings live here, and they are the same promise seen from two distances.

**[ST-DATA-011]** — *inside one process*. ``SchedulingWorkflow.schedule_new_classes``
appends the new classes to ``state["classes"]`` **before** it calls the optimizer
and has no ``try/except`` around anything that follows. Every raise between the
append and the return therefore leaves a class in the user's timetable that the
user never got told about — and, on one path, leaves it *marked placed* at a cell
the workflow already decided to abandon. The same shape recurs in the constraint
negotiator's three ``_estimate_*_impact`` helpers, in
``PlacementScorer.score_with_lookahead`` and in
``ConstraintValidator.check_placement_explained``: each mutates something,
computes, then un-mutates on the last line — a line an exception simply skips.

**[ST-DATA-012]** — *between processes*. There is no single-instance guard, so two
DERSİS windows both read ``settings/app_settings.egu``, both edit, and the last
one to save wins. The audit lost a whole class plus a language change that way.

Reading order
-------------
1. ``TestScheduleNewClassesIsTransactional`` — the add is all-or-nothing.
2. ``TestEstimatorsRestoreState`` — "restore on the last line" is not a restore.
3. ``TestSingleInstanceLock`` — the guard that does not exist yet.

How the failure is injected
---------------------------
Every ST-DATA-011 test replaces one collaborator with a stub that raises
``_Boom``. That is deliberate: the point is not *which* call fails (any of them
can — the optimizer is a CP-SAT/LNS pipeline, the feedback logger writes an
encrypted file, the negotiator builds a conflict graph) but that the workflow
survives *a* failure without corrupting state. It also keeps the module
deterministic and fast: **the real optimizer is never run here**, so nothing in
this file depends on ST-SCHED-013 (the optimizer is not reproducible).

Each injected stub carries a call counter *and* records what the state looked
like at the moment it fired, and every test asserts on that record. A counter
alone is not enough: deleting the temporary mutation outright leaves the counter
happy and the "state is unchanged" assertion trivially true. The recorded
snapshot is what proves the mutation actually happened, so these tests cannot go
green by the code simply doing less.

The seam ST-DATA-012 is written against does not exist yet
----------------------------------------------------------
``TestSingleInstanceLock`` imports ``scheduler_app.single_instance``. That module
is the *proposed* fix surface, specified in the task report accompanying this
file; until it lands, every test in that class fails at import with
``ModuleNotFoundError``. That is the intended fail-now state, not a bug in the
test. The contract asserted is deliberately implementation-agnostic — it holds
for ``QLockFile``, for an ``O_CREAT|O_EXCL`` PID file, and for an OS-level
exclusive handle — with exactly one primitive ruled out (see
``test_second_acquisition_is_refused_while_the_first_is_held``).

Runtime: see the measured figures in this task's report. The module never runs
the optimizer, so its cost is dominated by the one subprocess test; no ``slow``
marker is warranted.
"""
import copy
import os
import subprocess
import sys
import time

import pytest

from scheduler_app.core import constraint_negotiator as cn_mod
from scheduler_app.core import constraint_validator as cv_mod
from scheduler_app.core import workflow as wf_mod
from scheduler_app.core.candidate_generator import CandidateGenerator
from scheduler_app.core.constraint_negotiator import ConstraintNegotiator
from scheduler_app.core.constraint_validator import ConstraintValidator
from scheduler_app.core.models import cls_key, mark_placed, new_class
from scheduler_app.core.placement_scorer import PlacementScorer
from scheduler_app.core.workflow import SchedulingWorkflow, snapshot_placements

from _support.dataset_gen import make_state

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Boom(RuntimeError):
    """The injected failure. Distinct type so a real bug cannot be mistaken for it."""


def _weights():
    return {}


def _state_delta(before, after):
    """Human-sized description of how two state dicts differ.

    ``assert state == state_before`` on a 4-class state prints an unreadable
    wall of dicts; this pinpoints the field so the failure names the leak.
    """
    parts = []
    for key in sorted(set(before) | set(after)):
        if key == "classes":
            continue
        if before.get(key) != after.get(key):
            parts.append(f"state[{key!r}] changed")
    old_by_uid = {c.get("class_uid"): c for c in before.get("classes", [])}
    new_by_uid = {c.get("class_uid"): c for c in after.get("classes", [])}
    for uid in old_by_uid.keys() - new_by_uid.keys():
        parts.append(f"class {old_by_uid[uid].get('name')!r} disappeared")
    for uid in new_by_uid.keys() - old_by_uid.keys():
        parts.append(f"class {new_by_uid[uid].get('name')!r} appeared")
    for uid in old_by_uid.keys() & new_by_uid.keys():
        old, new = old_by_uid[uid], new_by_uid[uid]
        for field in sorted(set(old) | set(new)):
            if old.get(field) != new.get(field):
                parts.append(
                    f"class {old.get('name')!r}: {field} "
                    f"{old.get(field)!r} -> {new.get(field)!r}")
    return "; ".join(parts) or "no difference"


def _occupancy(validator):
    """Semantic fingerprint of a validator's occupancy maps.

    Empty sets are dropped: ``add_placement`` creates a cell entry with
    ``setdefault`` and ``remove_placement`` only ``discard``s from it, so a
    correctly reverted placement leaves ``{cell: set()}`` behind. Every reader
    goes through ``.get(key, set())``, so an empty set and an absent key mean
    exactly the same thing — comparing the raw dicts would flag a clean revert
    as a leak.
    """
    return tuple(
        {cell: set(names) for cell, names in occ.items() if names}
        for occ in (validator.room_occ, validator.lect_occ, validator.group_occ)
    )


# ── ST-DATA-011 · fixtures ───────────────────────────────────────────────────

def _placed_state(n_classes=5, seed=3):
    """A small, fully placed timetable. No optimizer involved — placements are
    written directly, so the starting point is identical on every run."""
    state = make_state(n_days=5, n_slots=6, n_rooms=3, n_lecturers=4,
                       n_years=2, n_classes=n_classes, density=0.0, seed=seed)
    for i, cls in enumerate(state["classes"]):
        cls["duration"] = 1
        mark_placed(cls, state["days"][i % 5], state["slots"][0],
                    state["classrooms"][0])
    return state


def _pending_class(state, name="Yeni Ders", lecturer_index=0, branch="A"):
    """A normalized, unplaced class ready to be handed to schedule_new_classes."""
    cls = new_class()
    cls["name"] = name
    cls["class_code"] = name
    cls["lecturer"] = state["lecturers"][lecturer_index]
    cls["targets"] = [{"year": "Year-1", "branch": branch}]
    cls["duration"] = 1
    cls["location_type"] = "physical"
    cls["participants"] = 10
    return cls


@pytest.fixture
def placed_state():
    return _placed_state()


class TestScheduleNewClassesIsTransactional:
    """ST-DATA-011 — adding classes must be all-or-nothing."""

    def test_optimizer_raise_leaves_state_untouched(self, placed_state,
                                                    monkeypatch):
        """ST-DATA-011: if the optimizer blows up while placing an added class,
        the user must not silently end up with a phantom class in the timetable
        they were never told about and never see in the add dialog again.
        """
        state = placed_state
        workflow = SchedulingWorkflow(state, _weights)
        pending = _pending_class(state)

        state_before = copy.deepcopy(state)
        class_before = copy.deepcopy(pending)
        calls = {"n": 0}

        def exploding_optimizer(*args, **kwargs):
            calls["n"] += 1
            raise _Boom("optimizer failed mid-batch")

        monkeypatch.setattr(wf_mod, "optimized_batch_schedule",
                            exploding_optimizer)

        with pytest.raises(_Boom):
            workflow.schedule_new_classes([pending])

        assert calls["n"] == 1, "the optimizer stub was never reached"
        assert pending not in state["classes"], (
            "half-added class leaked into state after the optimizer raised")
        assert state == state_before, (
            "state was not restored after the failure: "
            + _state_delta(state_before, state))
        assert pending == class_before, (
            "the class dict handed in was left mutated by the failed add")

    def test_negotiator_raise_leaves_state_untouched(self, placed_state,
                                                     monkeypatch):
        """ST-DATA-011: when a single added class cannot be placed, DERSİS runs
        constraint negotiation to explain why; if that explanation step fails the
        user must still not be left with an invisible extra class in the file.
        """
        state = placed_state
        workflow = SchedulingWorkflow(state, _weights)
        pending = _pending_class(state)

        state_before = copy.deepcopy(state)
        class_before = copy.deepcopy(pending)
        calls = {"n": 0}

        monkeypatch.setattr(
            wf_mod, "optimized_batch_schedule",
            lambda *a, **k: ([], [(pending, "no valid slot")], False))

        def exploding_negotiation(self, cls):
            calls["n"] += 1
            raise _Boom("negotiation failed")

        monkeypatch.setattr(cn_mod.ConstraintNegotiator, "negotiate_class",
                            exploding_negotiation)

        with pytest.raises(_Boom):
            workflow.schedule_new_classes([pending])

        assert calls["n"] == 1, "the negotiation stub was never reached"
        assert pending not in state["classes"], (
            "half-added class leaked into state after negotiation raised")
        assert state == state_before, (
            "state was not restored after the failure: "
            + _state_delta(state_before, state))
        assert pending == class_before

    def test_feedback_logger_raise_leaves_no_orphan_placement(self,
                                                              placed_state,
                                                              monkeypatch):
        """ST-DATA-011: on the happy path the workflow marks the new class placed
        and *then* writes a feedback log entry; if that write fails the user must
        not be left with a class pinned to a cell nobody ever confirmed.
        """
        state = placed_state
        workflow_calls = {"n": 0}

        class ExplodingFeedbackLogger:
            def log_batch_result(self, *args, **kwargs):
                workflow_calls["n"] += 1
                raise _Boom("feedback log write failed")

            def log_accepted_placement(self, *args, **kwargs):
                workflow_calls["n"] += 1
                raise _Boom("feedback log write failed")

        workflow = SchedulingWorkflow(state, _weights,
                                      feedback_logger=ExplodingFeedbackLogger())
        pending = _pending_class(state)

        state_before = copy.deepcopy(state)
        class_before = copy.deepcopy(pending)

        # Fast path: the optimizer reports the new class placed cleanly, so the
        # workflow commits the placement before it touches the logger.
        monkeypatch.setattr(
            wf_mod, "optimized_batch_schedule",
            lambda *a, **k: ([(pending, state["days"][2], state["slots"][3],
                               state["classrooms"][1])], [], False))

        with pytest.raises(_Boom):
            workflow.schedule_new_classes([pending])

        assert workflow_calls["n"] == 1, "the logger stub was never reached"
        assert pending not in state["classes"], (
            "the class stayed in state after the commit step failed")
        assert pending["placed"] is False, (
            "orphaned placement: the class is still marked placed after the "
            "add was abandoned")
        assert state == state_before, (
            "state was not restored after the failure: "
            + _state_delta(state_before, state))
        assert pending == class_before

    def test_unplaceable_single_class_is_not_left_in_state(self, placed_state,
                                                           monkeypatch):
        """ST-DATA-011 (returned-failure path): when the optimizer *reports*
        that a single added class has no valid slot, the class must be taken back
        out of state rather than lingering as an unplaced ghost.

        This pins TODAY's workflow contract so the new try/except cannot change
        the path that already works. Be aware it is one side of an unresolved
        contradiction: ``ui/app.py:2452`` comments "class stays in state
        (unplaced)", returns True and pushes an undo entry, while
        ``workflow.py:268-270`` removes the class. If the maintainer resolves
        that in favour of keeping the class, this test is the one to rewrite —
        see the implementation plan.
        """
        state = placed_state
        workflow = SchedulingWorkflow(state, _weights)
        pending = _pending_class(state)
        state_before = copy.deepcopy(state)

        monkeypatch.setattr(
            wf_mod, "optimized_batch_schedule",
            lambda *a, **k: ([], [(pending, "no valid slot")], False))

        result = workflow.schedule_new_classes([pending])

        assert result.single_failed is True
        assert pending not in state["classes"]
        assert state == state_before, _state_delta(state_before, state)

    def test_multi_class_add_leaves_the_classes_for_the_caller_to_decide(
            self, placed_state, monkeypatch):
        """ST-DATA-011: for a *multi*-class add the user gets a confirm dialog,
        so the classes must still be in the timetable (unplaced) when the dialog
        opens — otherwise the dialog describes classes that no longer exist.

        Regression guard: nobody may "fix" ST-DATA-011 by making the
        returned-failure multi path self-cleaning, because ``ui/app.py:2465``
        commits it with ``apply_schedule_result`` on the accept branch.
        """
        state = placed_state
        workflow = SchedulingWorkflow(state, _weights)
        first = _pending_class(state, name="Toplu 1", lecturer_index=0,
                               branch="A")
        second = _pending_class(state, name="Toplu 2", lecturer_index=1,
                                branch="B")
        existing_before = copy.deepcopy(state["classes"])

        monkeypatch.setattr(
            wf_mod, "optimized_batch_schedule",
            lambda *a, **k: ([], [(first, "x"), (second, "x")], False))

        result = workflow.schedule_new_classes([first, second])

        assert result.single_success is False and result.single_failed is False
        assert first in state["classes"] and second in state["classes"], (
            "the pending classes were removed before the user could answer")
        assert first["placed"] is False and second["placed"] is False, (
            "an unplaceable class was marked placed")
        assert state["classes"][:len(existing_before)] == existing_before, (
            "the existing timetable was disturbed by a failed bulk add")

    def test_rollback_after_apply_restores_the_timetable_byte_for_byte(
            self, placed_state, monkeypatch):
        """ST-DATA-011: a bulk add that reorganises the timetable and is then
        undone must put every existing lesson back on its original day, hour and
        room — a rollback that only deletes the new classes silently keeps the
        reorganisation the user just rejected.

        This is also the mechanism section A of the plan leans on: the internal
        rollback restores placements *before* removing the new classes, so
        ``restore_placements`` has to genuinely work.
        """
        state = placed_state
        workflow = SchedulingWorkflow(state, _weights)
        first = _pending_class(state, name="Toplu 1", lecturer_index=0,
                               branch="A")
        second = _pending_class(state, name="Toplu 2", lecturer_index=1,
                                branch="B")

        existing = list(state["classes"])
        snapshots = snapshot_placements(state)
        state_before = copy.deepcopy(state)

        # A full reorganisation: every existing lesson moves to another hour and
        # room, and the two new classes land somewhere else again.
        relocated = [(c, state["days"][i % 5], state["slots"][2],
                      state["classrooms"][1]) for i, c in enumerate(existing)]
        placements = relocated + [
            (first, state["days"][0], state["slots"][4], state["classrooms"][2]),
            (second, state["days"][1], state["slots"][4], state["classrooms"][2]),
        ]
        monkeypatch.setattr(
            wf_mod, "optimized_batch_schedule",
            lambda *a, **k: (placements, [], True))

        result = workflow.schedule_new_classes([first, second])
        assert result.rescheduled is True
        workflow.apply_schedule_result(result)

        # Pre-conditions, so the rollback below is not trivially correct: the
        # new classes are in state AND the existing ones really did move.
        assert first in state["classes"] and second in state["classes"]
        assert any(c["placed_time"] != snapshots[cls_key(c)][1]
                   for c in existing), "no existing class was relocated"

        workflow.rollback_schedule([first, second], snapshots)

        assert state == state_before, (
            "cancelling a bulk add did not restore the previous timetable: "
            + _state_delta(state_before, state))


# ── ST-DATA-011 · estimators ─────────────────────────────────────────────────

def _negotiation_state():
    """A state whose first class is constrained on every axis, so all six
    ``_estimate_*_impact`` branches (allow-item and remove-exclusion, for days,
    times and rooms) actually reach their temporary mutation."""
    state = make_state(n_days=5, n_slots=6, n_rooms=3, n_lecturers=4,
                       n_years=2, n_classes=4, density=0.0, seed=11,
                       slot_start_hour=9)
    for cls in state["classes"]:
        cls["duration"] = 1
        cls["location_type"] = "physical"
    target = state["classes"][0]
    days, slots, rooms = state["days"], state["slots"], state["classrooms"]
    target["allowed_days"] = [days[0], days[4]]
    target["excluded_days"] = [days[4]]
    target["allowed_times"] = [slots[0], slots[5]]
    target["excluded_times"] = [slots[5]]
    target["required_classrooms"] = [rooms[0]]
    target["excluded_classrooms"] = [rooms[2]]
    return state, target


# (estimator method name, argument factory) — the argument factory receives the
# state and returns the positional args after ``cls``.
ESTIMATOR_CASES = [
    ("_estimate_day_impact", lambda s: (s["days"][1], False), "allow-day"),
    ("_estimate_day_impact", lambda s: (s["days"][4], True), "unexclude-day"),
    ("_estimate_time_impact", lambda s: (s["slots"][1], False), "allow-time"),
    ("_estimate_time_impact", lambda s: (s["slots"][5], True), "unexclude-time"),
    ("_estimate_room_impact", lambda s: (s["classrooms"][1], False),
     "allow-room"),
    ("_estimate_room_impact", lambda s: (s["classrooms"][2], True),
     "unexclude-room"),
]


class TestEstimatorsRestoreState:
    """ST-DATA-011 — "temporarily mutate, compute, un-mutate" needs try/finally."""

    @pytest.mark.parametrize(
        "method_name,args_factory,case_id",
        ESTIMATOR_CASES,
        ids=[c[2] for c in ESTIMATOR_CASES])
    def test_estimator_restores_class_constraints_on_error(
            self, method_name, args_factory, case_id):
        """ST-DATA-011: the "what if you allowed Tuesday?" hints relax the class's
        own constraints to measure the effect; if the measurement fails the
        relaxation must not stick, or the user's class silently acquires a day,
        time or room they never approved.
        """
        state, target = _negotiation_state()
        negotiator = ConstraintNegotiator(state)
        suggester = negotiator.suggester
        estimator = getattr(suggester, method_name)
        args = args_factory(state)

        # Control: on the success path the estimator is already clean.
        control = copy.deepcopy(state)
        estimator(target, *args)
        assert state == control, (
            f"{method_name} ({case_id}) leaks even without an exception: "
            f"{_state_delta(control, state)}")

        state_before = copy.deepcopy(state)
        target_before = copy.deepcopy(target)
        calls = {"n": 0}
        seen = {}

        def exploding_check(cls, *a, **k):
            # Record what the class looked like *while* the estimator believed
            # it had relaxed it. This is the non-vacuity guard: without it, an
            # implementation that simply stopped relaxing the class would make
            # the "state is unchanged" assertion below trivially true. Verified
            # by mutation: deleting the three relaxation blocks in
            # constraint_negotiator.py makes all six cases pass without it.
            calls["n"] += 1
            seen.setdefault("cls", copy.deepcopy(cls))
            raise _Boom("validator failed")

        suggester.validator.check_placement = exploding_check

        with pytest.raises(_Boom):
            estimator(target, *args)

        assert calls["n"] >= 1, (
            f"{method_name} ({case_id}) never reached the validator, so this "
            "case does not exercise the temporary mutation")
        assert seen["cls"] != target_before, (
            f"{method_name} ({case_id}) never relaxed the class before "
            "measuring, so this test cannot prove the relaxation is undone")
        assert state == state_before, (
            f"{method_name} ({case_id}) left the class constraints relaxed "
            f"after the estimate failed: {_state_delta(state_before, state)}")

    def test_score_with_lookahead_reverts_temporary_placement_on_error(self):
        """ST-DATA-011: look-ahead scoring parks a class in the occupancy map to
        measure the damage it would do; if the measurement fails the parked class
        stays there and every later placement sees a room and a lecturer that are
        permanently, falsely, busy.
        """
        state = make_state(n_days=3, n_slots=4, n_rooms=2, n_lecturers=3,
                           n_years=1, n_classes=3, density=0.0, seed=5)
        for cls in state["classes"]:
            cls["duration"] = 1
        validator = ConstraintValidator(state)
        generator = CandidateGenerator(state, validator=validator)
        scorer = PlacementScorer(state, validator,
                                 conflict_graph=None, propagator=None)

        candidate = state["classes"][0]
        remaining = [state["classes"][1]]
        occupancy_before = _occupancy(validator)

        spy = {"placed": False, "counts": 0}
        real_add = validator.add_placement

        def spying_add(*a, **k):
            spy["placed"] = True
            return real_add(*a, **k)

        validator.add_placement = spying_add

        def counting_then_exploding(cls, gen):
            # First pass (one call per remaining class) builds the baseline;
            # the temporary placement happens between the passes, so raising on
            # the very next call lands squarely inside the mutated window.
            spy["counts"] += 1
            if spy["counts"] > len(remaining):
                raise _Boom("lookahead counting failed")
            return 5

        scorer._count_valid_fast = counting_then_exploding

        with pytest.raises(_Boom):
            scorer.score_with_lookahead(
                candidate, state["days"][0], state["slots"][0],
                state["classrooms"][0], remaining, generator)

        assert spy["placed"] is True, (
            "the temporary placement was never made, so this test would pass "
            "vacuously")
        assert _occupancy(validator) == occupancy_before, (
            "look-ahead left a phantom placement in the occupancy maps")

    def test_score_with_lookahead_reverts_the_propagator_simulation_on_error(
            self):
        """ST-DATA-011: the real optimizer scores look-ahead through the
        constraint propagator, whose simulated placement sits on a stack; if the
        scoring fails the simulation is never popped, so every later candidate
        in that run is judged against a lesson that was never really placed.

        The sibling test above covers the propagator-less branch of the same
        function. This one covers the branch that actually ships — the optimizer
        always builds a propagator — and it is the reason the ``try`` in the plan
        must open *after* both arms of the if/else, not inside one of them.
        """
        state = make_state(n_days=3, n_slots=4, n_rooms=2, n_lecturers=3,
                           n_years=1, n_classes=3, density=0.0, seed=5)
        for cls in state["classes"]:
            cls["duration"] = 1
        validator = ConstraintValidator(state)
        generator = CandidateGenerator(state, validator=validator)

        spy = {"simulated": 0, "reverted": 0, "counts": 0}
        remaining = [state["classes"][1]]

        class StubPropagator:
            """Minimal stand-in for ConstraintPropagator's lookahead surface."""

            def get_valid_count_fast(self, cls, cap=20):
                spy["counts"] += 1
                # One call per remaining class builds the baseline; the
                # simulation is pushed between the passes, so raising on the
                # next call lands inside the simulated window.
                if spy["counts"] > len(remaining):
                    raise _Boom("propagator counting failed")
                return 5

            def simulate_placement(self, cls, day, slot, room):
                spy["simulated"] += 1

            def revert_simulation(self):
                spy["reverted"] += 1

        scorer = PlacementScorer(state, validator, conflict_graph=None,
                                 propagator=StubPropagator())

        with pytest.raises(_Boom):
            scorer.score_with_lookahead(
                state["classes"][0], state["days"][0], state["slots"][0],
                state["classrooms"][0], remaining, generator)

        assert spy["simulated"] == 1, (
            "no simulation was pushed, so this test would pass vacuously")
        assert spy["reverted"] == 1, (
            "the propagator simulation was never reverted after the failure — "
            "the phantom placement stays on the simulation stack")

    def test_check_placement_explained_restores_occupancy_on_error(self,
                                                                   monkeypatch):
        """ST-DATA-011: validating an already-placed class lifts it out of the
        occupancy map to avoid self-conflicts; if the check fails midway the
        class's own slot is left looking free and a second class can be dropped
        straight on top of it.
        """
        state = make_state(n_days=3, n_slots=4, n_rooms=2, n_lecturers=3,
                           n_years=1, n_classes=3, density=0.0, seed=5)
        for cls in state["classes"]:
            cls["duration"] = 1
            cls["location_type"] = "physical"
            cls["participants"] = 10
        state["classroom_capacities"] = {r: 200 for r in state["classrooms"]}

        # Two classes at the same cell in *different* rooms with *different*
        # lecturers and *different* branches. Nothing they own overlaps, so
        # lifting `subject` out of the occupancy maps cannot also erase
        # `neighbour` — the occupancy maps are sets, and a shared value would
        # make the leak invisible.
        subject, neighbour = state["classes"][0], state["classes"][1]
        subject["lecturer"] = state["lecturers"][0]
        neighbour["lecturer"] = state["lecturers"][1]
        subject["targets"] = [{"year": "Year-1", "branch": "A"}]
        neighbour["targets"] = [{"year": "Year-1", "branch": "B"}]
        day, slot = state["days"][0], state["slots"][0]
        mark_placed(subject, day, slot, state["classrooms"][0])
        mark_placed(neighbour, day, slot, state["classrooms"][1])
        validator = ConstraintValidator(state)

        occupancy_before = _occupancy(validator)
        assert occupancy_before[0].get((day, slot)) == set(state["classrooms"]), (
            "fixture is degenerate: both rooms must be occupied at this cell")

        calls = {"n": 0}
        seen = {}

        def exploding_tr(*a, **k):
            # Record the occupancy *while* the class is supposed to be lifted
            # out of it. Without this the test would also pass against an
            # implementation that never lifted the placement at all, which is
            # a different (and wrong) way to have no leak.
            calls["n"] += 1
            seen.setdefault("occ", _occupancy(validator))
            raise _Boom("message lookup failed")

        # ``tr`` is only reached once a conflict has actually been detected —
        # i.e. after the temporary removal — whereas the occupancy helpers
        # themselves are on the *restore* path and must stay intact, or no
        # possible fix could make this test pass.
        monkeypatch.setattr(cv_mod, "tr", exploding_tr)

        with pytest.raises(_Boom):
            # Asking "could `subject` move into the neighbour's room?" — a real
            # question the drag-and-drop validator asks on every hover.
            validator.check_placement_explained(
                subject, day, slot, state["classrooms"][1])

        assert calls["n"] >= 1, "the injected failure was never reached"
        assert seen["occ"][0].get((day, slot)) == {state["classrooms"][1]}, (
            "the class's own room was never lifted out of the occupancy map "
            "during the check, so this test cannot prove it is put back")
        assert _occupancy(validator) == occupancy_before, (
            "the class's own placement was left removed from the occupancy maps")


# ── ST-DATA-012 · single-instance lock ───────────────────────────────────────

# How long a second instance may take to notice that the holder of the lock is
# dead. A user whose app crashed must be able to restart it immediately, so
# staleness has to be decided by asking whether the recorded pid is still alive,
# not by how old the lock file is. Measured against QLockFile (which checks pid
# liveness first, before its 30 s mtime rule) the reclaim takes ~0.01 s, so this
# budget is generous for every primitive discussed in the implementation plan.
STALE_RECLAIM_TIMEOUT = 10.0
CHILD_READY_TIMEOUT = 20.0

_CHILD_SOURCE = '''\
"""Holds a DERSIS single-instance lock until killed. Test helper, not shipped."""
import os
import sys
import time

sys.path.insert(0, sys.argv[1])
lock_path, ready_path = sys.argv[2], sys.argv[3]

from scheduler_app.single_instance import SingleInstanceLock

lock = SingleInstanceLock(lock_path)
acquired = lock.acquire()
with open(ready_path, "w", encoding="utf-8") as handle:
    handle.write("%d %d" % (os.getpid(), 1 if acquired else 0))
    handle.flush()
    os.fsync(handle.fileno())
time.sleep(300)
'''


def _spawn_lock_holder(tmp_path):
    """Start a child process that acquires the lock and then blocks.

    Returns ``(process, lock_path, child_pid)``. The child is killed — not asked
    to exit — by the caller, which is what makes the stale-lock case honest.
    """
    child_home = tmp_path / "child_home"
    (child_home / "Documents").mkdir(parents=True, exist_ok=True)
    child_py = tmp_path / "hold_lock.py"
    child_py.write_text(_CHILD_SOURCE, encoding="utf-8")
    lock_path = tmp_path / "dersis.lock"
    ready_path = tmp_path / "child_ready.txt"

    env = dict(os.environ)
    drive, tail = os.path.splitdrive(str(child_home))
    env.update({
        "HOME": str(child_home),
        "USERPROFILE": str(child_home),
        "HOMEDRIVE": drive,
        "HOMEPATH": tail,
        "QT_QPA_PLATFORM": "offscreen",
        "PYTHONPATH": REPO_ROOT,
    })

    proc = subprocess.Popen(
        [sys.executable, str(child_py), REPO_ROOT, str(lock_path),
         str(ready_path)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    deadline = time.monotonic() + CHILD_READY_TIMEOUT
    while time.monotonic() < deadline:
        if ready_path.exists():
            break
        if proc.poll() is not None:
            _, err = proc.communicate()
            pytest.fail("lock-holder child exited before acquiring the lock:\n"
                        + err)
        time.sleep(0.05)
    else:
        proc.kill()
        proc.wait(timeout=10)
        pytest.fail("lock-holder child never signalled readiness")

    pid_text, acquired_text = ready_path.read_text(encoding="utf-8").split()
    assert acquired_text == "1", (
        "the first instance failed to acquire a lock nobody else held")
    return proc, str(lock_path), int(pid_text)


class TestSingleInstanceLock:
    """ST-DATA-012 — one DERSİS at a time, and never a permanent lock-out.

    Every test here targets ``scheduler_app.single_instance``, the module the
    fix must add. See this task's implementation plan for the exact contract.
    """

    def test_second_acquisition_is_refused_while_the_first_is_held(self,
                                                                   tmp_path):
        """ST-DATA-012: without this, opening DERSİS twice gives two windows that
        both save settings.egu, and whichever the user closes last silently
        erases the other's work.

        Note this is asserted *in-process*, on two distinct lock objects over one
        path. That rules out POSIX record locks (``fcntl.lockf``), which are
        owned per-process and would hand the second object a lock it must not
        get; ``QLockFile``, ``flock``, ``LockFileEx`` and ``O_CREAT|O_EXCL`` all
        satisfy it.
        """
        from scheduler_app.single_instance import (
            SingleInstanceLock, acquire_single_instance_lock)

        lock_path = str(tmp_path / "dersis.lock")
        first = SingleInstanceLock(lock_path)
        try:
            assert first.acquire() is True
            assert first.is_held() is True

            second = SingleInstanceLock(lock_path)
            assert second.acquire() is False, (
                "a second instance was allowed to run alongside the first")
            assert second.is_held() is False
            assert acquire_single_instance_lock(lock_path) is None
            assert first.is_held() is True, (
                "the refused acquisition disturbed the live lock")
        finally:
            first.release()

    def test_lock_is_released_on_normal_exit(self, tmp_path):
        """ST-DATA-012: closing DERSİS and reopening it must just work — a guard
        that never lets go is worse than no guard at all.
        """
        from scheduler_app.single_instance import SingleInstanceLock

        lock_path = str(tmp_path / "dersis.lock")

        first = SingleInstanceLock(lock_path)
        assert first.acquire() is True
        first.release()
        assert first.is_held() is False

        second = SingleInstanceLock(lock_path)
        try:
            assert second.acquire() is True, (
                "the lock was not released when the first instance exited")
        finally:
            second.release()

        # release() must be idempotent: the shutdown path can plausibly run
        # twice (closeEvent plus atexit).
        second.release()

        with SingleInstanceLock(lock_path) as third:
            assert third.is_held() is True
        assert SingleInstanceLock(lock_path).acquire() is True, (
            "leaving the context manager did not release the lock")

    def test_context_manager_releases_when_the_body_raises(self, tmp_path):
        """ST-DATA-012: if DERSİS dies with an unhandled exception the guard must
        still come off, or the user's next launch is refused forever.
        """
        from scheduler_app.single_instance import SingleInstanceLock

        lock_path = str(tmp_path / "dersis.lock")

        with pytest.raises(_Boom):
            with SingleInstanceLock(lock_path) as held:
                assert held.is_held() is True
                raise _Boom("crash inside the app")

        recovered = SingleInstanceLock(lock_path)
        try:
            assert recovered.acquire() is True, (
                "an exception inside the app left the lock held")
        finally:
            recovered.release()

    def test_stale_lock_from_a_killed_process_is_reclaimed(self, tmp_path):
        """ST-DATA-012: this is the case that actually bites — DERSİS is killed by
        a crash, a forced reboot or Task Manager, and the leftover lock must not
        lock the user out of their own timetable on the next launch.
        """
        from scheduler_app.single_instance import SingleInstanceLock

        proc, lock_path, child_pid = _spawn_lock_holder(tmp_path)
        try:
            blocked = SingleInstanceLock(lock_path)
            assert blocked.acquire() is False, (
                "a live instance in another process did not block this one")
            assert blocked.owner_pid() == child_pid, (
                "the lock does not identify its holder, so a stale lock cannot "
                "be told apart from a live one")
        finally:
            proc.kill()
            proc.wait(timeout=30)

        deadline = time.monotonic() + STALE_RECLAIM_TIMEOUT
        reclaimed = None
        while time.monotonic() < deadline:
            candidate = SingleInstanceLock(lock_path)
            if candidate.acquire():
                reclaimed = candidate
                break
            time.sleep(0.1)

        assert reclaimed is not None, (
            "the lock left behind by a killed instance was never reclaimed — "
            f"the user stays locked out for more than {STALE_RECLAIM_TIMEOUT}s")
        try:
            assert reclaimed.owner_pid() == os.getpid()
        finally:
            reclaimed.release()

    def test_default_lock_path_lives_under_the_dersis_root(self, dersis_home):
        """ST-DATA-012: the guard has to protect the directory the instances
        actually fight over, so it must live in the same Dersis root that holds
        settings/app_settings.egu.
        """
        from scheduler_app.storage import storage
        from scheduler_app.single_instance import default_lock_path

        path = default_lock_path()

        assert os.path.isabs(path)
        assert os.path.commonpath([path, storage.root_dir()]) == \
            os.path.normpath(storage.root_dir()), (
            "the lock file is not inside the Dersis root it is meant to guard")
        assert os.path.isdir(os.path.dirname(path)), (
            "default_lock_path() points into a directory that does not exist")
