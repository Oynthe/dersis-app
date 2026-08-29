"""Diagnosability of a solve: dropped classes, global infeasibility, dead terms.

Findings guarded here
---------------------
* **ST-SCHED-001 (the "surface dropped classes" half).** ``apply_reschedule``
  re-validates every placement the optimizer proposed and throws away the ones
  that no longer fit. It reports them — as a bare list of *names* — and
  ``ui/app.py::_on_solve_finished`` (``self._workflow.apply_reschedule(result)``)
  discards the
  return value entirely. From the user's chair a lesson the solver placed simply
  vanishes. Section 1 pins the *core-layer* contract the UI has to consume: no
  drop without a report, and no report without a reason.
* **ST-SCHED-014.** An oversubscribed instance produces N identical copies of
  "all remaining candidate slots are occupied" and nothing that names the global
  bottleneck ("you have 14 class-hours and 8 room-hours of grid"). The
  negotiation pass then labels the very classes the solver failed to place
  ``status="ok"``. Sections 2 and 3.
* **ST-SCHED-015.** ``PlacementScorer._neighbor_impact`` is dead code and its
  ``neighbor_impact_penalty`` weight (4.0) has no effect on any score.
  Section 4 is the invariance harness for deleting it safely.

Why the drop tests are hand-built rather than optimizer-driven
--------------------------------------------------------------
The findings register records "``small`` drops 1 of 21, ``normal`` drops 9-21 of
76". Measured on the tree this file was written against, **both are zero** —
Phase 3's ST-SCHED-001 work has already removed the collisions that caused the
drops. A test that waits for the optimizer to produce a drop is therefore
vacuous *today*, and would have become vacuous the moment that fix landed
anyway. Section 1 builds a ``RescheduleResult`` by hand whose two placements
provably collide, so the commit step is forced to drop exactly one class no
matter how good the solver gets. Section 1's third test then checks the same
accounting closes on a real solve, with an anti-vacuity floor.

The API this file assumes for ST-SCHED-014 (read before implementing)
---------------------------------------------------------------------
Nothing global exists yet, so the shape below is a *proposal* pinned as a strict
xfail. It extends the ``summary`` dict ``ScheduleOptimizer.optimize()`` already
returns — the ``summary = {`` literal inside ``schedule_optimizer.py::optimize``;
match on the literal, never on a line number — instead of inventing a new
return channel::

    summary["infeasibility"] is None          # not provably oversubscribed
    summary["infeasibility"] == {
        "bottlenecks": [                      # non-empty, worst first
            {"type":      one of _BOTTLENECK_TYPES,
             "entity":    str | None,         # the lecturer / group / None for the grid
             "required":  int,                # class-hours that must be scheduled
             "available": int,                # hours the resource actually offers
             "message":   str},               # translated, names entity + numbers
            ...,
        ],
        "message": str,                       # one sentence, the worst bottleneck
    }

``required`` and ``available`` are the point: a diagnosis that does not carry
the two numbers is not a diagnosis, it is another adjective. See the failure
messages below — they spell out the exact arithmetic each scenario expects.

Runtime
-------
Whole module ~6-8 s (three real optimizer runs on 5-14 class instances, at a
reduced search budget — see ``_FAST_BUDGET``). Nothing here is marked ``slow``
on purpose: these are ST-SCHED-014's only pins and CI runs ``-m "not slow"``.
"""
import pytest

from scheduler_app.core.models import cls_key, new_class, new_state
from scheduler_app.core.workflow import RescheduleResult, SchedulingWorkflow

# The optimizer's shipped budget (5 multi-start runs x 200 LNS iterations) costs
# 27-43 s on the deliberately saturated instances below, which is all wall clock
# spent re-confirming a structurally impossible packing. Cutting it to a single
# greedy pass costs 3 s and cannot change any assertion here: every conclusion
# in sections 2 and 3 follows from counting (14 one-hour classes cannot fit in 8
# room-hours however hard you search). Measured with the full budget too — same
# unplaced counts, same negotiation statuses, same absent summary key.
_FAST_BUDGET = dict(multi_start_runs=1, lns_iterations=0, parallel_workers=-1)

# The vocabulary the ST-SCHED-014 pins expect in summary["infeasibility"].
# Kept in one place so the implementer can see every accepted string at once.
_BOTTLENECK_TYPES = frozenset({
    "grid_capacity",    # total class-hours > days x slots x rooms
    "room_hours",       # same thing said room-first; either is accepted
    "lecturer_hours",   # one lecturer's hours > days x slots
    "group_hours",      # one year/branch's hours > days x slots
})


# ---------------------------------------------------------------------------
# Hand-built instances. No dataset_gen: every scenario here needs an exact,
# hand-checkable ratio between demand and capacity, which a random generator
# cannot promise.
# ---------------------------------------------------------------------------
def _grid(n_days, n_slots, rooms, specs):
    """Build a state with an exactly known capacity.

    *specs* is a list of ``(name, lecturer, year, branch, duration)``.
    Capacity is ``n_days * n_slots`` grid-hours and
    ``n_days * n_slots * len(rooms)`` room-hours; demand is ``sum(duration)``.
    """
    state = new_state()
    state["days"] = ["monday", "tuesday", "wednesday", "thursday",
                     "friday"][:n_days]
    state["slots"] = [f"{9 + i:02d}:00" for i in range(n_slots)]
    state["classrooms"] = list(rooms)
    # 0 == "capacity unknown", which keeps the (soft) room-capacity rule out of
    # the way so each scenario isolates exactly one global bottleneck.
    state["classroom_capacities"] = {r: 0 for r in rooms}
    state["lecturers"] = sorted({s[1] for s in specs})
    years = {}
    for _name, _lect, year, branch, _dur in specs:
        years.setdefault(year, [])
        if branch not in years[year]:
            years[year].append(branch)
    state["years"] = years
    for name, lect, year, branch, dur in specs:
        cls = new_class()
        cls["class_code"] = name
        cls["name"] = name
        cls["lecturer"] = lect
        cls["targets"] = [{"year": year, "branch": branch}]
        cls["duration"] = dur
        cls["participants"] = 0
        state["classes"].append(cls)
    return state


def _distinct_specs(n, duration=1, prefix="G"):
    """*n* classes with pairwise-distinct lecturers AND student groups.

    Distinctness matters: it removes lecturer clashes and group clashes as
    possible explanations, so the only thing that can stop these classes fitting
    is the number of room-hours in the grid.
    """
    specs = []
    i = 0
    for year in range(1, 8):
        for branch in ("A", "B", "C"):
            if i >= n:
                return specs
            specs.append((f"{prefix}{i:02d}", f"L{i:02d}",
                          f"Year-{year}", branch, duration))
            i += 1
    return specs


def _mini_pair_state():
    """Two classes that share nothing but a room — a clean collision fixture."""
    return _grid(2, 3, ["R001", "R002"], [
        ("Alpha", "Lect-A", "Year-1", "A", 1),
        ("Beta", "Lect-B", "Year-1", "B", 1),
    ])


def _capacity(state):
    """(grid_hours, room_hours, demand_hours) for a state built by _grid()."""
    grid_hours = len(state["days"]) * len(state["slots"])
    return (grid_hours,
            grid_hours * len(state["classrooms"]),
            sum(c["duration"] for c in state["classes"]))


def _solve(state):
    """Run reschedule at the reduced budget and return (workflow, result)."""
    workflow = SchedulingWorkflow(state, lambda: {})
    result = workflow.reschedule({}, use_cpsat=False, **_FAST_BUDGET)
    return workflow, result


# ---------------------------------------------------------------------------
# Shared solves. Each is one optimizer run, reused by every test that reads it.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def oversubscribed_grid():
    """14 one-hour classes against 8 room-hours (2 days x 2 slots x 2 rooms).

    Every class has its own lecturer and its own student group, and each of them
    owns 1 hour against 4 grid-hours, so neither lecturers nor groups are
    anywhere near saturated. The *only* reason six of these cannot be scheduled
    is that the timetable does not physically have fourteen room-hours in it.

    The negotiation report is read BEFORE ``apply_reschedule``, which is the
    order ``ui/app.py`` uses: ``BulkResultsDialog`` receives
    ``negotiation_source=lambda: result.negotiation_result`` and is ``exec()``d
    inside ``app.py::_on_solve_finished``, i.e. before that same method's
    ``apply_reschedule`` commit.
    """
    state = _grid(2, 2, ["R001", "R002"], _distinct_specs(14))
    workflow, result = _solve(state)
    negotiation = result.negotiation_result
    rejected = workflow.apply_reschedule(result)
    return {"state": state, "result": result, "rejected": rejected,
            "negotiation": negotiation}


@pytest.fixture(scope="module")
def oversubscribed_lecturer():
    """One lecturer owns 5 x 3 h = 15 h against a 3 x 4 = 12-hour week.

    Room-hours are 24, so the grid as a whole is not the problem and the five
    student groups are distinct. A single named person is the bottleneck, and
    "Solo cannot teach 15 hours in a 12-hour week" is the only honest
    explanation of the failure.
    """
    specs = [(f"S{i:02d}", "Solo", f"Year-{1 + i // 3}",
              ("A", "B", "C")[i % 3], 3) for i in range(5)]
    state = _grid(3, 4, ["R001", "R002"], specs)
    workflow, result = _solve(state)
    negotiation = result.negotiation_result
    workflow.apply_reschedule(result)
    return {"state": state, "result": result, "negotiation": negotiation}


@pytest.fixture(scope="module")
def feasible_control():
    """9 one-hour classes against 36 room-hours — comfortably schedulable.

    The negative control for section 2. Without it, "an oversubscribed instance
    names its bottleneck" is satisfied by an implementation that names a
    bottleneck unconditionally.
    """
    state = _grid(3, 4, ["R001", "R002", "R003"], _distinct_specs(9, prefix="F"))
    workflow, result = _solve(state)
    workflow.apply_reschedule(result)
    return {"state": state, "result": result}


# ===========================================================================
# 1. NOTHING IS DROPPED WITHOUT BEING REPORTED  (ST-SCHED-001)
# ===========================================================================
def _forced_collision():
    """A RescheduleResult whose two placements cannot both be committed.

    Alpha and Beta share nothing except the cell (monday, 09:00, R001):
    different lecturers, different student groups, unlimited room capacity. So
    the commit step's re-validation has exactly one reason to refuse the second
    one, and exactly one class must end up dropped. Nothing here depends on the
    optimizer, which is the point — see the module docstring.
    """
    state = _mini_pair_state()
    alpha, beta = state["classes"]
    workflow = SchedulingWorkflow(state, lambda: {})
    result = RescheduleResult(
        placed=[(alpha, "monday", "09:00", "R001"),
                (beta, "monday", "09:00", "R001")],
        unplaced=[], changes=[], summary=None)
    return state, workflow, result, alpha, beta


def _committed_at(cls, day, slot, room):
    return (cls["placed"], cls["placed_day"], cls["placed_time"],
            cls["placed_classroom"]) == (True, day, slot, room)


def test_apply_reschedule_reports_every_class_it_drops():
    """Guards ST-SCHED-001's drop-reporting contract (currently PASSES).

    A failure means the commit step threw a lesson out of the timetable and told
    nobody — the user asked for a schedule, got fewer lessons than the solver
    actually produced, and has no way to find out which ones.
    """
    state, workflow, result, alpha, beta = _forced_collision()

    rejected = workflow.apply_reschedule(result)

    # Anti-vacuity: the scenario must really have forced a drop. If a future
    # commit step learns to relocate the loser instead of dropping it, this is
    # the assertion that says "rewrite this fixture", rather than the test
    # quietly passing on a contract it no longer exercises.
    committed = [c for c in (alpha, beta)
                 if _committed_at(c, "monday", "09:00", "R001")]
    dropped = [c for c in (alpha, beta) if c not in committed]
    assert len(committed) == 1 and len(dropped) == 1, (
        "the fixture no longer forces a drop, so this test proves nothing: "
        f"committed={[c['name'] for c in committed]} "
        f"dropped={[c['name'] for c in dropped]}")

    assert len(rejected) == len(dropped), (
        f"apply_reschedule dropped {len(dropped)} of {len(result.placed)} "
        f"proposed placements but reported {len(rejected)}: "
        f"dropped={[c['name'] for c in dropped]} rejected={rejected!r}")

    reported_names = {_report_name(entry) for entry in rejected}
    assert reported_names == {c["name"] for c in dropped}, (
        "the report does not name the class that was actually dropped: "
        f"reported={reported_names} dropped={{{dropped[0]['name']!r}}}")

    # And the drop really is data loss, not a silent relocation somewhere else.
    assert not dropped[0]["placed"], (
        f"{dropped[0]['name']!r} was reported as rejected but is still placed "
        f"at {(dropped[0]['placed_day'], dropped[0]['placed_time'], dropped[0]['placed_classroom'])}")


def test_drop_report_says_why_not_only_who():
    """Pins ST-SCHED-001's drop-reporting contract — the actionable half.

    "Ders 12 disappeared" is not a message a user can do anything with. "Ders 12
    was removed because R001 was already taken on Monday at 09:00" is. A failure
    means the app can tell someone that a lesson vanished but not what to change
    to get it back.

    Expected shape (see module docstring): each entry of the returned list is a
    mapping — or an object with the same attribute names — carrying at least
    ``name`` and a non-empty ``reason``. ``class_uid`` is strongly recommended
    on top, because class names are not unique in a real dataset.
    """
    state, workflow, result, alpha, beta = _forced_collision()

    rejected = workflow.apply_reschedule(result)

    assert rejected, "fixture regression: nothing was dropped at all"
    bad = [entry for entry in rejected if not _report_reason(entry)]
    assert not bad, (
        "apply_reschedule reported dropped classes without a machine-readable "
        f"reason: {bad!r}. Expected each entry to expose 'name' and a non-empty "
        "'reason' (a mapping or an object with those names); got bare "
        f"{type(rejected[0]).__name__} values instead.")


def _report_name(entry):
    """Class name out of a drop-report entry, whatever container it uses."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("name")
    return getattr(entry, "name", None)


def _report_reason(entry):
    """Non-empty reason out of a drop-report entry, or None."""
    if isinstance(entry, str):
        return None
    if isinstance(entry, dict):
        reason = entry.get("reason")
    else:
        reason = getattr(entry, "reason", None)
    return reason or None


@pytest.mark.engine
def test_drop_accounting_closes_on_a_real_solve(oversubscribed_grid):
    """Guards ST-SCHED-001's drop-reporting contract on optimizer output.

    The hand-built pin above proves the contract holds when a drop is forced;
    this one proves the same books balance on a real solve, so a future
    optimizer change that starts producing drops cannot slip any of them past
    the report. A failure means proposed-minus-committed and the reported list
    disagree: some lesson left the timetable off the books.
    """
    result = oversubscribed_grid["result"]
    rejected = oversubscribed_grid["rejected"]
    state = oversubscribed_grid["state"]
    _grid_hours, room_hours, _demand = _capacity(state)

    # Anti-vacuity, "clean because empty" (Trap 1 of test_scheduler_invariants):
    # the identity 0 == 0 is free on a solve that proposed nothing. This grid
    # holds 8 room-hours and the solver is expected to fill them.
    assert len(result.placed) == room_hours, (
        f"the solver proposed {len(result.placed)} placements for a grid with "
        f"{room_hours} room-hours; the accounting identity below is not "
        "meaningful on a degenerate run")

    dropped = []
    for cls, day, slot, room in result.placed:
        if cls["pinned"]:
            # A rejected pin is reported but NOT unplaced (apply_reschedule
            # deliberately leaves the user's instruction alone), so it is not a
            # drop and must not be counted as one. There are no pins in this
            # fixture; the branch exists so the identity stays correct if one is
            # ever added.
            continue
        if not _committed_at(cls, day, slot, room):
            dropped.append(cls["name"])

    assert len(dropped) == len(rejected), (
        f"{len(result.placed)} placements proposed, "
        f"{len(result.placed) - len(dropped)} committed, but "
        f"{len(rejected)} reported as rejected — "
        f"{len(dropped) - len(rejected)} lesson(s) left the timetable "
        f"unreported. dropped={dropped!r} rejected={rejected!r}")
    assert set(dropped) <= {_report_name(e) for e in rejected}, (
        f"dropped classes missing from the report: "
        f"{set(dropped) - {_report_name(e) for e in rejected}}")


# ===========================================================================
# 2. A GLOBALLY INFEASIBLE INSTANCE NAMES THE GLOBAL CONSTRAINT (ST-SCHED-014)
# ===========================================================================
def _assert_bottleneck(summary, *, types, entity, required, available,
                       arithmetic):
    """Assert summary['infeasibility'] names one specific global bottleneck.

    *arithmetic* is the human sentence explaining where *required* and
    *available* come from; it goes into the failure message so the implementer
    reads the spec at the moment the test fails, not in a document.
    """
    spec = (
        "Expected summary['infeasibility'] == {'bottlenecks': [{'type', "
        "'entity', 'required', 'available', 'message'}, ...], 'message': str}, "
        "with the list non-empty and worst-first. " + arithmetic)

    assert "infeasibility" in summary, (
        "ScheduleOptimizer.optimize() built its summary without any global "
        f"infeasibility diagnosis (keys: {sorted(summary)}). {spec}")
    diagnosis = summary["infeasibility"]
    assert isinstance(diagnosis, dict), (
        f"summary['infeasibility'] is {diagnosis!r} on an instance that is "
        f"provably oversubscribed. {spec}")

    bottlenecks = diagnosis.get("bottlenecks")
    assert bottlenecks, (
        f"summary['infeasibility'] carries no bottlenecks: {diagnosis!r}. "
        + spec)
    assert isinstance(diagnosis.get("message"), str) and diagnosis["message"], (
        "summary['infeasibility']['message'] must be one human sentence "
        f"naming the worst bottleneck; got {diagnosis.get('message')!r}")

    matching = [b for b in bottlenecks
                if b.get("type") in types and b.get("entity") == entity]
    assert matching, (
        f"no bottleneck of type {sorted(types)} for entity {entity!r} — the "
        "diagnosis does not name the constraint that actually makes this "
        f"instance impossible. Got: {bottlenecks!r}. {spec}")

    for b in bottlenecks:
        assert b.get("type") in _BOTTLENECK_TYPES, (
            f"unknown bottleneck type {b.get('type')!r}; expected one of "
            f"{sorted(_BOTTLENECK_TYPES)}")

    worst = matching[0]
    assert isinstance(worst.get("required"), int), (
        f"'required' must be an int number of class-hours; got "
        f"{worst.get('required')!r}. {arithmetic}")
    assert isinstance(worst.get("available"), int), (
        f"'available' must be an int number of hours the resource offers; got "
        f"{worst.get('available')!r}. {arithmetic}")
    assert worst["required"] > worst["available"], (
        "a bottleneck that does not demand more than it has is not a "
        f"bottleneck: {worst!r}")
    assert (worst["required"], worst["available"]) == (required, available), (
        f"the diagnosis reports {worst['required']}h needed against "
        f"{worst['available']}h available, but {arithmetic} "
        f"So this must read required={required}, available={available}. "
        "A diagnosis with the wrong numbers in it is worse than none.")
    assert isinstance(worst.get("message"), str) and worst["message"], (
        f"each bottleneck needs its own human sentence; got {worst!r}")


@pytest.mark.engine
def test_grid_oversubscription_is_named_in_the_summary(oversubscribed_grid):
    """Pins ST-SCHED-014 (Low) — the whole-timetable bottleneck.

    A school with more teaching hours than its timetable has room-hours cannot
    fix that by moving lessons around, and it is the one thing the app can
    prove without solving anything. Today it reports six separate "all slots
    are occupied" messages, which reads as "the solver gave up" rather than
    "buy another room or drop a course". A failure here means the user is
    still being told the symptom and never the cause.
    """
    state = oversubscribed_grid["state"]
    result = oversubscribed_grid["result"]
    grid_hours, room_hours, demand = _capacity(state)

    # Guard the fixture: assert the instance is genuinely impossible before
    # asserting anything about how that impossibility is explained.
    assert (grid_hours, room_hours, demand) == (4, 8, 14)
    assert result.unplaced, (
        "the solver placed everything, so there is no infeasibility to "
        "diagnose — this fixture has stopped being oversubscribed")

    _assert_bottleneck(
        result.summary,
        types={"grid_capacity", "room_hours"},
        entity=None,
        required=demand,
        available=room_hours,
        arithmetic=(
            f"this instance needs {demand} class-hours and the grid offers "
            f"days({len(state['days'])}) x slots({len(state['slots'])}) x "
            f"rooms({len(state['classrooms'])}) = {room_hours} room-hours."))


@pytest.mark.engine
def test_lecturer_oversubscription_names_the_lecturer(oversubscribed_lecturer):
    """Pins ST-SCHED-014 (Low) — the per-resource global bottleneck.

    One person cannot teach fifteen hours in a twelve-hour week. A failure means
    the timetabler is left to work that out from a list of unplaced courses,
    when the app already knows the name and the numbers.

    NOTE for the implementer: ``build_diagnostic_summary``
    (``constraint_negotiator.py``) already produces this fact at a 50 %-utilisation
    threshold. The work is surfacing it on ``summary`` with hard numbers, not
    inventing the detector.
    """
    state = oversubscribed_lecturer["state"]
    result = oversubscribed_lecturer["result"]
    grid_hours, room_hours, demand = _capacity(state)

    assert (grid_hours, room_hours, demand) == (12, 24, 15)
    assert result.unplaced, (
        "the solver placed all 15 hours in a 12-hour week, which is "
        "impossible — this fixture is broken")

    _assert_bottleneck(
        result.summary,
        types={"lecturer_hours"},
        entity="Solo",
        required=demand,
        available=grid_hours,
        arithmetic=(
            f"lecturer 'Solo' owns all {demand} class-hours in this instance "
            f"and a week is days({len(state['days'])}) x "
            f"slots({len(state['slots'])}) = {grid_hours} hours long."))


@pytest.mark.engine
def test_feasible_instance_reports_no_global_bottleneck(feasible_control):
    """Pins ST-SCHED-014 (Low) — the anti-vacuity control for the two pins above.

    Without this, "an impossible timetable names its bottleneck" is satisfied by
    code that names a bottleneck on every timetable. A failure means the app
    cries "you are oversubscribed" at a school that fits comfortably — which
    would train users to ignore the warning, i.e. cost more than saying nothing.
    """
    result = feasible_control["result"]
    grid_hours, room_hours, demand = _capacity(feasible_control["state"])

    assert (grid_hours, room_hours, demand) == (12, 36, 9)
    assert not result.unplaced, (
        "the control instance failed to schedule, so it is not a control")

    assert "infeasibility" in result.summary, (
        "summary must always carry the key, so callers can read it without "
        f"guessing; keys were {sorted(result.summary)}")
    diagnosis = result.summary["infeasibility"]
    assert not diagnosis or not diagnosis.get("bottlenecks"), (
        "a comfortably feasible instance (9 class-hours into 36 room-hours) "
        f"was reported as globally oversubscribed: {diagnosis!r}")


# ===========================================================================
# 3. THE NEGOTIATOR DOES NOT CALL AN UNPLACEABLE CLASS "ok"  (ST-SCHED-014)
# ===========================================================================
@pytest.mark.engine
def test_negotiation_never_labels_an_unplaced_class_ok(oversubscribed_grid):
    """Pins ST-SCHED-014 (Low) — the negotiation panel contradicts the solve.

    The user opens the results dialog, sees six courses in the "could not be
    placed" list, opens the negotiation tab and is told each of them is "ok"
    with eight valid options and no suggested fix. A failure means the one
    screen that exists to explain a failed solve is arguing with it.

    Measured on this tree (Turkish, as the suite pins it): all six unplaced
    classes come back ``status='ok'``, ``valid_slots=8``, ``suggestions=0``,
    summary "Dersin 8 gecerli yerlestirme secenegi var" — while the same result
    object reports their solver reason as "Kalan tum aday kontenjanlari dolu."
    """
    result = oversubscribed_grid["result"]
    negotiation = oversubscribed_grid["negotiation"]

    assert result.unplaced, "fixture regression: nothing was left unplaced"
    assert negotiation, (
        "the solve left classes unplaced but produced no negotiation report "
        "at all")

    reports = {r["class_name"]: r for r in negotiation["class_reports"]}
    # Anti-vacuity: an empty or partial report list would make the loop below
    # pass without examining anything.
    assert len(reports) == len(result.unplaced), (
        f"{len(result.unplaced)} classes were unplaced but the negotiation "
        f"produced {len(reports)} report(s)")

    mislabelled = []
    for cls, reason in result.unplaced:
        report = reports.get(cls["name"])
        assert report is not None, (
            f"{cls['name']!r} was unplaced but has no negotiation report")
        if report["status"] == "ok":
            mislabelled.append(
                (cls["name"], reason, report["valid_slots"],
                 len(report["suggestions"]), report["summary"]))

    assert not mislabelled, (
        f"{len(mislabelled)}/{len(result.unplaced)} classes the solver could "
        "not place are reported as status='ok' by the negotiation panel:\n"
        + "\n".join(
            f"  {name!r}: solver said {reason!r}; negotiation says ok with "
            f"{slots} valid slots and {n_sug} suggestion(s) — {summary!r}"
            for name, reason, slots, n_sug, summary in mislabelled))


@pytest.mark.engine
def test_negotiation_counts_the_solve_it_is_explaining(oversubscribed_grid):
    """Pins ST-SCHED-014 (Low) — the headline number is the wrong number.

    A failure means the results dialog's summary line tells a user that all
    fourteen of their courses failed when six did, immediately after the same
    dialog listed eight of them as successfully placed. Two contradictory
    counts in one payload: ``negotiation['unplaced_count']`` is right and
    ``negotiation['diagnostic_summary']['unplaced_count']`` is not.

    Measured on this tree: diagnostic_summary reports 14 of 14, the solve left
    6, and the sibling key in the very same dict says 6.
    """
    result = oversubscribed_grid["result"]
    negotiation = oversubscribed_grid["negotiation"]
    assert negotiation, "fixture regression: no negotiation report"

    actual = len(result.unplaced)
    assert actual, "fixture regression: nothing was left unplaced"

    # The sibling key is already correct, which is what makes the other one
    # provably a bug rather than a different definition.
    assert negotiation["unplaced_count"] == actual

    reported = negotiation["diagnostic_summary"]["unplaced_count"]
    assert reported == actual, (
        f"the negotiation diagnostic says {reported} of "
        f"{negotiation['diagnostic_summary']['total_classes']} classes could "
        f"not be placed; the solve it is explaining left {actual}. "
        f"overall_assessment reads: "
        f"{negotiation['diagnostic_summary']['overall_assessment']!r}")


# ===========================================================================
# 4. THE neighbor_impact TERM IS DEAD — INVARIANCE HARNESS  (ST-SCHED-015)
# ===========================================================================
# A fixed, tiny scoring scenario. Deliberately hand-built rather than taken from
# dataset_gen: the golden numbers below have to survive any future change to the
# generator, and `class_uid` is a fresh uuid4 per class so nothing here may
# depend on identifier values (verified: identical digest over three separate
# processes).
_SCORING_SPEC = [
    ("K1", "L1", "Year-1", "A", 1), ("K2", "L1", "Year-1", "B", 1),
    ("K3", "L2", "Year-1", "A", 2), ("K4", "L2", "Year-2", "A", 1),
    ("K5", "L3", "Year-2", "B", 1), ("K6", "L3", "Year-1", "B", 2),
    ("K7", "L1", "Year-2", "A", 1), ("K8", "L2", "Year-2", "B", 1),
]

# Top-3 lookahead scores per class, recorded against the tree this file was
# written on. This is the "prove nothing moved" tripwire for deleting the dead
# term: record, delete, re-run. If it fails *while* the only change is removing
# neighbor_impact_penalty, the deletion changed behaviour and is not the trivial
# edit it looks like. If it fails alongside a deliberate scoring change, the
# numbers below are simply stale — re-record them, and say so in the commit.
_SCORING_GOLDEN = [
    ("K1", "monday", "09:00", "R001", 0.5),
    ("K1", "monday", "09:00", "R002", 0.5),
    ("K1", "tuesday", "09:00", "R001", 0.55),
    ("K2", "monday", "09:00", "R001", 0.5),
    ("K2", "monday", "09:00", "R002", 0.5),
    ("K2", "tuesday", "09:00", "R001", 0.55),
    ("K3", "monday", "10:00", "R001", 0.738571429),
    ("K3", "monday", "10:00", "R002", 0.738571429),
    ("K3", "monday", "09:00", "R001", 0.761904762),
    ("K4", "monday", "09:00", "R001", 0.5),
    ("K4", "monday", "09:00", "R002", 0.5),
    ("K4", "tuesday", "09:00", "R001", 0.55),
    ("K5", "monday", "09:00", "R001", 0.5),
    ("K5", "monday", "09:00", "R002", 0.5),
    ("K5", "tuesday", "09:00", "R001", 0.55),
    ("K6", "monday", "10:00", "R001", 0.595714286),
    ("K6", "monday", "10:00", "R002", 0.595714286),
    ("K6", "monday", "09:00", "R001", 0.619047619),
    ("K7", "monday", "09:00", "R001", 0.333333333),
    ("K7", "monday", "09:00", "R002", 0.333333333),
    ("K7", "tuesday", "09:00", "R001", 0.383333333),
    ("K8", "monday", "09:00", "R001", 0.5),
    ("K8", "monday", "09:00", "R002", 0.5),
    ("K8", "tuesday", "09:00", "R001", 0.55),
]


def _scoring_rig(weight_override=None):
    """Build the exact scorer configuration schedule_optimizer.py::optimize uses.

    Namely: conflict graph AND propagator attached, no parallel pool. That is
    the only configuration in which ``_neighbor_impact`` can be reached at all
    — the parallel worker rebuilds the scorer without a graph
    (parallel_scorer.py), so the term is unreachable there by construction.
    """
    from scheduler_app.core.candidate_generator import CandidateGenerator
    from scheduler_app.core.conflict_graph import ConflictGraphBuilder
    from scheduler_app.core.constraint_propagator import (
        ConstraintPropagator, ConstraintState)
    from scheduler_app.core.constraint_validator import ConstraintValidator
    from scheduler_app.core.placement_scorer import PlacementScorer

    state = _grid(3, 4, ["R001", "R002"], _SCORING_SPEC)
    flexible = list(state["classes"])
    validator = ConstraintValidator(
        state, exclude_ids={cls_key(c) for c in flexible})
    generator = CandidateGenerator(state, validator=validator)
    graph = ConflictGraphBuilder(state, flexible).build()
    propagator = ConstraintPropagator(
        ConstraintState(state, validator, generator, flexible))
    scorer = PlacementScorer(state, validator, weights=weight_override,
                             conflict_graph=graph, propagator=propagator)
    return state, flexible, generator, graph, scorer


def _score_rows(weight_override=None):
    """Top-3 lookahead-scored candidates for every class, plus liveness facts."""
    state, flexible, generator, graph, scorer = _scoring_rig(weight_override)
    rows = []
    lookahead_active = 0
    for cls in flexible:
        candidates = generator.generate(cls)
        assert candidates, f"{cls['name']} has no candidates at all"
        remaining = [c for c in flexible if cls_key(c) != cls_key(cls)]
        scored = scorer.score_candidates_with_lookahead(
            cls, list(candidates), remaining, generator)
        for day, slot, room, score in scored[:3]:
            rows.append((cls["name"], day, slot, room, round(float(score), 9)))
        day, slot, room, score = scored[0]
        if abs(scorer.score(cls, day, slot, room) - score) > 1e-9:
            lookahead_active += 1
    return rows, lookahead_active, graph.total_edges()


@pytest.mark.engine
def test_neighbor_impact_term_stays_deleted():
    """Guards ST-SCHED-015 (Low) — the term is gone and must not creep back.

    This replaces a pre-deletion proof. The original body monkeypatched
    ``PlacementScorer._neighbor_impact`` and walked its loop alongside the real
    one to show the penalty body was unreachable: ``score_with_lookahead``
    fills ``before_counts`` with exactly ``{cls_key(rc) for rc in
    remaining_classes}`` and passes that same set in as ``remaining_ids``, so
    every neighbour hit either the ``not in remaining_ids`` continue or the
    ``in before_counts`` continue and the 4.0-weighted penalty was dead code.

    That was confirmed by measurement before removing it — 3307 calls across
    the ``small`` and ``normal`` presets, every one returning 0.0, and
    identical placements on all six presets afterwards — so the method,
    its call site, its ``DEFAULT_WEIGHTS`` entry and its ``_GOAL_WEIGHT_MAP``
    entry were deleted in Phase 3. The monkeypatch cannot run against code that
    no longer exists, so what is worth pinning now is that it stays deleted.

    A failure means someone reintroduced a scoring term that was measured to do
    nothing, and every timetable this app produces would shift under it.
    ``test_neighbor_impact_weight_lives_in_two_places_or_neither`` is the other
    half: it proves the weight cannot come back in one place only.
    """
    import inspect

    from scheduler_app.core import placement_scorer as ps

    assert not hasattr(ps.PlacementScorer, "_neighbor_impact"), (
        "PlacementScorer._neighbor_impact is back. It was removed in Phase 3 "
        "after measuring 0.0 on 3307 calls; if it has been repaired rather "
        "than resurrected, re-audit the scoring digest first — every "
        "placement decision in the app depends on it.")
    assert "neighbor_impact_penalty" not in ps.DEFAULT_WEIGHTS, (
        "neighbor_impact_penalty is back in DEFAULT_WEIGHTS")
    source = inspect.getsource(ps)
    assert "neighbor_impact" not in source, (
        "placement_scorer still mentions neighbor_impact somewhere; the "
        "deletion was partial")

@pytest.mark.engine
def test_scoring_digest_is_unchanged():
    """Pins ST-SCHED-015 (Low) — the before/after tripwire for the deletion.

    Run it, delete the term, run it again. Identical means the deletion was
    genuinely inert. A failure means the "trivial removal" moved real
    placements, and every timetable the app generates after the change would
    differ from the ones users have already approved.

    If this ever fails alongside a deliberate scoring change, ``_SCORING_GOLDEN``
    is stale rather than wrong — re-record it and say so in the commit message.
    Do not re-record it to make a neighbor_impact deletion pass.

    This golden was recorded before the Phase 3 deletion and is unchanged after
    it, which is the evidence that removing the term moved no placement.

    It also absorbed the job of a sibling test that swung
    ``neighbor_impact_penalty`` across nine orders of magnitude and asserted the
    scores did not move. That test could only ever run while the weight existed:
    once the key left ``DEFAULT_WEIGHTS``, ``PlacementScorer.__init__`` merged
    the override in as an orphan nothing reads, so it went on passing while
    testing nothing at all. A tripwire that cannot fail is worse than none, so
    it was deleted rather than left green.
    """
    rows, lookahead_active, edges = _score_rows()

    assert (edges, lookahead_active) == (11, 8), (
        "the scoring rig itself changed shape (graph edges / lookahead-active "
        f"classes = {edges}/{lookahead_active}, expected 11/8), so the golden "
        "below is comparing different work")
    assert len(rows) == len(_SCORING_GOLDEN)
    for got, want in zip(rows, _SCORING_GOLDEN):
        assert got[:4] == want[:4], (
            f"candidate ranking moved: got {got[:4]}, expected {want[:4]}")
        assert got[4] == pytest.approx(want[4], abs=1e-9), (
            f"score for {got[:4]} moved from {want[4]} to {got[4]}")


def test_neighbor_impact_weight_lives_in_two_places_or_neither():
    """Pins ST-SCHED-015 (Low) — the deletion is not a one-line deletion.

    ``optimization_goals._GOAL_WEIGHT_MAP`` maps the
    user-facing "minimal disruption" slider onto ``neighbor_impact_penalty``,
    and ``optimization_goals.goals_to_weights`` accumulates into
    ``{k: 0.0 for k in DEFAULT_WEIGHTS}`` with **no membership guard**.
    Delete the key from ``DEFAULT_WEIGHTS`` alone and every reschedule
    that carries custom goals dies with ``KeyError:
    'neighbor_impact_penalty'`` — verified by removing the key and calling
    ``goals_to_weights(DEFAULT_GOALS)``.

    A failure means the two halves have drifted apart, and a user who opens the
    reschedule dialog's optimization-goals panel gets a crash instead of a
    timetable.
    """
    from scheduler_app.core.optimization_goals import (
        DEFAULT_GOALS, GOAL_KEYS, PRESETS, _GOAL_WEIGHT_MAP, goals_to_weights)
    from scheduler_app.core.placement_scorer import DEFAULT_WEIGHTS

    mapped = {key for mapping in _GOAL_WEIGHT_MAP.values() for key in mapping}
    orphans = mapped - set(DEFAULT_WEIGHTS)
    assert not orphans, (
        f"_GOAL_WEIGHT_MAP still steers {sorted(orphans)}, which "
        "DEFAULT_WEIGHTS no longer defines. goals_to_weights() does "
        "`accum[weight_key] += ...` against a dict keyed by DEFAULT_WEIGHTS, "
        "so this is a KeyError on every reschedule with custom goals. Delete "
        "the entry in optimization_goals.py too.")

    # And prove it, rather than only reasoning about it: every shipped goal
    # profile must survive the conversion and produce every default weight.
    for label, goals in [("DEFAULT_GOALS", DEFAULT_GOALS), *PRESETS.items()]:
        weights = goals_to_weights(dict(goals))
        missing = set(DEFAULT_WEIGHTS) - set(weights)
        assert not missing, (
            f"goals_to_weights({label}) dropped {sorted(missing)}; "
            "PlacementScorer indexes its weights with [] and would raise")
    assert set(GOAL_KEYS) == set(DEFAULT_GOALS)
