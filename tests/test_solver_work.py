"""How much work the solver does to reach its answer (ST-PERF-001, ST-SCHED-001).

**Why this is a work-count ratchet and not a benchmark.**
ST-PERF-001 asked for "a perf benchmark in CI". Phase 7 measured what that
would actually buy and the answer was: nothing, plus flakiness. Eleven
consecutive ``ubuntu-latest`` runs of the existing engine job spread 1.36-1.49x
on identical code (one historical outlier at 3.38x), and the runner is **1.87x
faster** than the machine any threshold would be calibrated on — so a
locally-derived wall-clock bound is ~1.9x wrong before variance is considered,
and one loose enough to survive the variance cannot detect a real regression.

The same measurement found something better. ``ConstraintValidator.check_placement``
is the engine's inner loop, and its **call count is bit-exact**:

    preset   calls        runs   spread   wall
    tiny      47 693      5/5    1.000    0.25-0.28 s
    small    687 396      3/3    1.000    2.50-2.73 s

Each run is a separate subprocess with ``PYTHONHASHSEED`` unset, i.e. a
different hash seed every time, so the count is not an artefact of dictionary
ordering. Wall clock over the same runs is not stable to better than ~1.1x on an
idle box and ~2.8x under load. **The count is the measurement; the clock is
not.**

**What the ratchet catches that the quality floors cannot.** Same instance, same
seed, one knob changed (measured on this tree):

    configuration                        calls       vs base  placed  q_after
    shipped LNS knobs                     687 396      1.00x      21     4.30
    lns_no_improve_limit=10000            1 795 135    2.61x      21     4.30
    lns_iterations=400 (control)          687 396      1.00x      21     4.30

Row 2 is ST-PERF-004 in its purest form — the solver does two and a half times
the work for a byte-identical timetable — and ``placed``, ``hard_violations``
and ``q_after`` all miss it, so ``test_greedy_bounds.py``'s placement floors are
blind to exactly this. Row 3 is the control: a knob that changes no work moves
the counter not at all, so the count is not a hash of the configuration.

**Ratchet semantics**, identical to ``tests/test_import_layering.py`` and
``tests/test_translation_coverage.py``: the ceilings below are a maximum that
may only go **down**. A change that makes the solver cheaper lowers them in the
same commit that earned it. Raising one is a deliberate act that needs a
sentence in the commit message saying what bought the extra work.

**Why the band has a floor too.** A ceiling alone is satisfied by a solver that
does nothing. The floor, together with the placement and cleanliness assertions
beside it, is what stops this module degenerating into ``f(x) == f(x)``.

This module also carries the ST-SCHED-001 assertion the correctness spine was
missing — ``summary['repaired_conflicts'] == 0`` — because the ``small`` solve it
already runs is the cheapest place in the suite to make it. See
``test_the_engine_withdraws_none_of_its_own_placements``.

**The budget the app actually runs** is also pinned here, in sections 5-7: that
it has one definition rather than a literal in the facade, that a capped solve
stops at the deadline instead of granting it to each phase in turn, and that the
restart count the user is shown counts restarts that happened. None of the three
asserts a second of wall clock — see section 4 for why nothing in this module
does.

Runtime: ~10.5 s for the whole fast half (tiny 0.3 s + small 2.6 s, shared
across every ratchet assertion, plus 4.4 s and 3.1 s for the two budget tests
that need their own capped solves). The one ``slow`` test runs the shipped
80-class configuration and costs ~2 min; it lives in the engine job.
"""
import pytest

from _support.dataset_gen import make_preset
from _support.schedule_oracle import (
    check_schedule,
    format_violations,
    hard_violation_count,
)

# The optimizer seed, pinned as a literal rather than imported. It happens to
# equal the shipped ``DEFAULT_OPTIMIZER_SEED`` today; pinning it here means this
# gate keeps measuring the *same search* even if that default is ever changed,
# which is the only way a work count is comparable across commits.
SEED = 20260101

# ---------------------------------------------------------------------------
# THE RATCHET. Ceilings may only go DOWN; see the module docstring.
#
# Measured on this tree (Windows / CPython 3.12 / .venv-audit), production
# workflow, ``make_preset(name, seed=42)``, one restart, uncapped clock, no
# worker pool. Identical across 5 (`tiny`) and 3 (`small`) independent
# subprocesses with randomised PYTHONHASHSEED.
#
#   tiny    47 693      small   687 396
#
# Ceilings carry ~5 % headroom. That headroom is deliberate and is NOT slack to
# be spent: the first green CI run on ubuntu-latest / CPython 3.11 is the
# calibration for whether the count is platform-identical (it could not be
# measured from Windows), and once it is known the ceilings should be tightened
# toward the observed value.
# ---------------------------------------------------------------------------
MAX_CHECK_PLACEMENT_CALLS_TINY = 50_000     # measured 47 693
MAX_CHECK_PLACEMENT_CALLS_SMALL = 725_000   # measured 687 396

# Anti-vacuity floors. Far below the measured values — a degeneracy detector,
# not a second ratchet. A solver that stubs out its own search fails here.
MIN_CHECK_PLACEMENT_CALLS_TINY = 20_000
MIN_CHECK_PLACEMENT_CALLS_SMALL = 400_000

# Placement floors for the two instrumented solves. Measured 5/5 and 21/25 on
# every run. `small` keeps one lesson of margin for the LNS acceptance RNG;
# `tiny` is trivially satisfiable and must be complete.
PLACED_TINY = 5
MIN_PLACED_SMALL = 20


class _WorkRun:
    """One instrumented reschedule: the counts, the answer, and the summary."""

    def __init__(self, preset, calls, result, raw):
        self.preset = preset
        self.calls = calls
        self.result = result
        self.summary = result.summary
        self.raw = raw

    @property
    def placed(self):
        return len(self.result.placed)


def _instrumented_reschedule(preset, **overrides):
    """Run the production reschedule with ``check_placement`` counted.

    Every keyword below is load-bearing:

    * ``use_cpsat=False`` — CP-SAT solves in a subprocess, where the counter
      cannot see the work and ``deterministic`` is False by construction.
    * ``parallel_workers=-1`` — **not** a performance choice. ``0`` means "auto",
      which means a multiprocessing pool; calls made inside a worker are
      invisible here, the count silently drops and the ratchet would pass for
      the wrong reason.
    * ``multi_start_runs=1`` + ``multi_start_time_limit=1e9`` — the emergency
      clock cap must not be able to fire, or the count would record where the
      wall clock landed rather than how much work the search does. Asserted, not
      assumed: every caller checks ``deterministic``.
    """
    from scheduler_app.core.constraint_validator import ConstraintValidator
    from scheduler_app.core.workflow import SchedulingWorkflow

    state = make_preset(preset, seed=42)
    original = ConstraintValidator.check_placement
    counter = {"calls": 0}

    def counting(self, cls, day, start_slot, room):
        counter["calls"] += 1
        return original(self, cls, day, start_slot, room)

    kwargs = dict(use_cpsat=False, seed=SEED, multi_start_runs=1,
                  multi_start_time_limit=1e9, parallel_workers=-1)
    kwargs.update(overrides)

    ConstraintValidator.check_placement = counting
    try:
        workflow = SchedulingWorkflow(state, lambda: {})
        result = workflow.reschedule({}, **kwargs)
    finally:
        ConstraintValidator.check_placement = original

    raw = check_schedule(state, placements=result.placed)
    return _WorkRun(preset, counter["calls"], result, raw)


@pytest.fixture(scope="module")
def tiny_work():
    return _instrumented_reschedule("tiny")


@pytest.fixture(scope="module")
def small_work():
    return _instrumented_reschedule("small")


def _assert_the_run_is_judgeable(run):
    """The precondition. A capped run's work count means nothing.

    ``deterministic`` is False exactly when the emergency wall-clock cap fired
    (or CP-SAT ran). Under a cap the search stops wherever the machine's speed
    put it, so the count measures the box, not the code, and this module must
    refuse to judge it rather than pass or fail by luck.
    """
    assert run.summary["deterministic"] is True, (
        f"`{run.preset}`: the solve reported deterministic=False, so its work "
        "count records how fast this machine is rather than how much work the "
        "search does. Nothing in this module may be read from it. "
        f"(runs_completed={run.summary.get('runs_completed')}, "
        f"cpsat_used={run.summary.get('cpsat_used')})")
    assert run.summary["cpsat_used"] is False, (
        f"`{run.preset}`: CP-SAT ran, and its work happens in a subprocess "
        "where the counter cannot see it")


def _assert_within_the_ratchet(run, floor, ceiling):
    assert run.calls <= ceiling, (
        f"WORK RATCHET: `{run.preset}` made {run.calls:,} "
        f"ConstraintValidator.check_placement calls against a ceiling of "
        f"{ceiling:,}. The solver is doing more work for the same timetable — "
        f"it still placed {run.placed} classes with "
        f"{hard_violation_count(run.raw)} hard violations, which is why the "
        "placement floors in tests/test_greedy_bounds.py cannot see this. "
        "Either the change is a regression, or it bought something and the "
        "ceiling moves UP in the same commit with a sentence saying what.")
    assert run.calls >= floor, (
        f"ANTI-VACUITY: `{run.preset}` made only {run.calls:,} check_placement "
        f"calls (floor {floor:,}). Either the solver stopped searching, or the "
        "counter is no longer wrapping the function it thinks it is — in both "
        "cases the ceiling above is being met for the wrong reason. If the "
        "search genuinely got this much cheaper, lower BOTH bounds here in the "
        "commit that earned it.")


# ===========================================================================
# 1. THE RATCHET
# ===========================================================================
@pytest.mark.engine
def test_the_tiny_preset_stays_inside_the_work_ratchet(tiny_work):
    """Pins ST-PERF-001 / ST-PERF-004 at 5 classes.

    The cheap end of the curve. Measured 47 693 calls, identical on 5/5
    independent subprocesses. A failure at ``tiny`` and not at ``small`` says
    the extra work is per-class overhead rather than search growth.
    """
    _assert_the_run_is_judgeable(tiny_work)
    _assert_within_the_ratchet(tiny_work,
                               MIN_CHECK_PLACEMENT_CALLS_TINY,
                               MAX_CHECK_PLACEMENT_CALLS_TINY)


@pytest.mark.engine
def test_the_small_preset_stays_inside_the_work_ratchet(small_work):
    """Pins ST-PERF-001 / ST-PERF-004 at 25 classes.

    This is the gate that matters. Measured 687 396 calls; removing the LNS
    stopping condition (``lns_no_improve_limit=10000``) takes it to 1 795 135 —
    2.61x the work for a timetable identical in ``placed``, ``hard_violations``
    and ``q_after``. That regression is invisible to every other test in the
    suite.
    """
    _assert_the_run_is_judgeable(small_work)
    _assert_within_the_ratchet(small_work,
                               MIN_CHECK_PLACEMENT_CALLS_SMALL,
                               MAX_CHECK_PLACEMENT_CALLS_SMALL)


# ===========================================================================
# 2. THE INSTRUMENT ITSELF
# ===========================================================================
@pytest.mark.engine
def test_the_counter_survives_the_run_it_instruments(small_work):
    """Guards this module (no finding ID).

    ``_instrumented_reschedule`` monkeypatches a method on a production class
    and restores it in a ``finally``. A leak would silently slow — and
    mis-count — every later test in the session.
    """
    from scheduler_app.core.constraint_validator import ConstraintValidator

    assert ConstraintValidator.check_placement.__name__ == "check_placement", (
        "the counting wrapper was left installed on ConstraintValidator after "
        "the instrumented run; every subsequent test in this session is now "
        "running through it")
    assert small_work.calls > 0


@pytest.mark.engine
def test_a_count_the_instrument_never_saw_fails_the_floor():
    """Guards this module (no finding ID) — the ``parallel_workers`` hazard.

    The single most likely way for this gate to go quietly useless is for the
    work to move somewhere the counter cannot see it. ``parallel_workers=0``
    means "auto", which means a multiprocessing pool: calls made inside a
    worker never touch this process's counter, the count collapses, and a
    ceiling-only assertion would pass — for exactly the wrong reason.

    The floor is what makes that failure loud. This test proves the floor is
    load-bearing rather than decorative.
    """
    from types import SimpleNamespace

    unseen = SimpleNamespace(preset="small", calls=0, placed=21,
                             raw={"counts": {}})

    with pytest.raises(AssertionError, match="ANTI-VACUITY"):
        _assert_within_the_ratchet(unseen,
                                   MIN_CHECK_PLACEMENT_CALLS_SMALL,
                                   MAX_CHECK_PLACEMENT_CALLS_SMALL)


@pytest.mark.engine
def test_a_validator_that_blesses_everything_is_caught_by_this_module():
    """Guards this module (no finding ID) — the Phase 5 lesson, measured.

    Phase 5 shipped four tests that pinned nothing. The way to avoid a fifth is
    to build the stub and find out which assertion actually stops it. So: a
    ``check_placement`` that says yes to every cell.

    Measured on this tree, and the reason this test is worth its 0.2 s: the
    count **does not** catch it. The stub run still makes 30 085 counted calls
    at ``tiny`` and 485 218 at ``small`` — comfortably clear of the anti-vacuity
    floors and inside the ceilings at both scales. What catches it is the oracle
    assertion standing next to the count: the schedule it produces carries 16
    hard violations at ``tiny`` and 138 at ``small``.

    Read that as the division of labour this module depends on. The work count
    is a *cost* gate and nothing else; it is blind to a wrong answer, exactly as
    the placement floors in ``tests/test_greedy_bounds.py`` are blind to wasted
    work. Neither is a substitute for the other, and stripping either one out
    leaves a hole.
    """
    from scheduler_app.core.constraint_validator import ConstraintValidator

    original = ConstraintValidator.check_placement
    ConstraintValidator.check_placement = (
        lambda self, cls, day, start_slot, room: True)
    try:
        run = _instrumented_reschedule("tiny")
    finally:
        ConstraintValidator.check_placement = original

    # The count alone does not notice — this is the honest, measured limit of
    # it, and the reason nobody may delete the oracle assertions below.
    assert run.calls >= MIN_CHECK_PLACEMENT_CALLS_TINY, (
        f"a broken validator now makes only {run.calls:,} counted calls, below "
        f"the floor of {MIN_CHECK_PLACEMENT_CALLS_TINY:,}. That is a change in "
        "the engine's shape, not a fix — re-measure before adjusting anything.")

    # The oracle beside it is not fooled.
    assert hard_violation_count(run.raw) > 0, (
        "an optimizer whose validator blesses every cell produced a "
        "hard-constraint-clean timetable. Either the oracle has stopped "
        "detecting collisions — in which case every 'is clean' assertion in "
        "this module and in tests/test_scheduler_invariants.py is vacuous — or "
        "the stub above is no longer reaching the engine.")


@pytest.mark.engine
def test_the_ratchet_refuses_to_judge_a_clock_capped_run():
    """Guards this module (no finding ID) — the precondition.

    Under the emergency wall-clock cap the search stops wherever this machine's
    speed left it, so the count is a property of the box. A gate that read it
    anyway would be the wall-clock assertion this module exists to avoid, in
    disguise. Forcing the cap must make the module *decline*, not pass.
    """
    run = _instrumented_reschedule("small", multi_start_time_limit=0.05,
                                   multi_start_runs=5)

    assert run.summary["deterministic"] is False, (
        "a 0.05 s budget on the 25-class instance did not trip the clock cap, "
        "so this test is no longer exercising the precondition it names")
    with pytest.raises(AssertionError, match="deterministic=False|records how fast"):
        _assert_the_run_is_judgeable(run)


# ===========================================================================
# 3. THE CORRECTNESS SPINE'S MISSING ASSERTION (ST-SCHED-001)
# ===========================================================================
@pytest.mark.engine
def test_the_engine_withdraws_none_of_its_own_placements(small_work, tiny_work):
    """Pins ST-SCHED-001 — the gap the invariants suite could not see.

    ``summary['repaired_conflicts']`` counts placements the optimizer proposed
    and then had to withdraw because they broke a hard constraint. Production
    says so itself (``core/schedule_optimizer.py``): *"A non-zero count means
    the engine produced something it should not have."* Phase 3 defined it as an
    engine defect rather than a normal outcome, it measures 0 on every preset —
    and before Phase 7 **nothing asserted it**.

    That mattered. Deleting the ST-SCHED-001 occupancy resync loses 10 of 76
    lessons on ``normal`` while ``rejected == 0``, ``raw_violations == 0`` and
    all six parity tests in ``tests/test_scheduler_invariants.py`` stay green:
    ``screen_placements`` converts the desync from *invalid* placements into
    *missing* ones, and nothing measured how many lessons came back. The two
    assertions below — the withdrawal count, and a placement floor — are what
    turn that silent 76 -> 66 into a red build.

    Free: both solves are already running for the ratchet above.
    """
    for run, floor in ((tiny_work, PLACED_TINY), (small_work, MIN_PLACED_SMALL)):
        _assert_the_run_is_judgeable(run)

        assert run.summary["repaired_conflicts"] == 0, (
            f"`{run.preset}`: the optimizer withdrew "
            f"{run.summary['repaired_conflicts']} of its own placements "
            f"({run.summary.get('repaired_classes')}) because they broke a "
            "hard constraint. Those lessons do not appear in `rejected` and do "
            "not appear in the timetable — they simply vanish, and the user is "
            "told nothing.")

        assert run.placed >= floor, (
            f"`{run.preset}`: the optimizer proposed {run.placed} placements "
            f"(floor {floor}; measured 5 for tiny, 21 for small). A drop here "
            "with `repaired_conflicts` still 0 is quality lost somewhere "
            "earlier than the screen.")

        assert hard_violation_count(run.raw) == 0, (
            f"`{run.preset}`: the raw proposal is not clean, so its placement "
            "count says nothing about quality:\n" + format_violations(run.raw))


# ===========================================================================
# 4. THE SHIPPED CONFIGURATION'S REPRODUCIBILITY (ST-SCHED-013)
# ===========================================================================
@pytest.mark.engine
@pytest.mark.slow
@pytest.mark.xfail(
    strict=False,
    reason="ST-SCHED-013 / ST-PERF-001, open on slow hardware only. "
           "`optimized_reschedule_all` (core/facade.py) budgets an 80-class "
           "reschedule at DEFAULT_MULTI_START_TIME_LIMIT=120 s across "
           "DEFAULT_MULTI_START_RUNS=5 restarts, and whether five restarts fit "
           "is a property of the runner, not of the code. Measured on ONE "
           "machine, on one tree, within one hour: run as a single node on an "
           "idle box it XPASSes twice — five restarts, deterministic=True, "
           "96.4 s and 103.6 s, 14-20 % headroom; run as part of this module's "
           "own slow lane it xfails, capped at 120.07 s. Same code, same box. "
           "ubuntu-latest runs this workload 1.87x faster (ci.yml), i.e. ~52 s, "
           "so CI is on the xpassing side. NOT strict for exactly that reason: "
           "strict=True turned every idle run into a build failure, and "
           "deleting the marker would turn every loaded one into a build "
           "failure. Neither states a defect. The half of this "
           "property that does not depend on the runner — that the budget is "
           "one number and that the search actually stops at it — is asserted "
           "unconditionally by "
           "test_the_shipped_budget_has_a_single_definition and "
           "test_the_lns_phase_stops_at_the_solve_wide_deadline below.")
def test_the_shipped_configuration_is_reproducible_at_80_classes():
    """ST-SCHED-013, open on slow hardware. Reports xfail-or-xpass; gates nothing.

    No budget overrides: this is exactly what a user gets when they press
    *Reschedule All* on a real department timetable. ``deterministic`` is the
    optimizer's own statement that its answer can be reproduced; False means the
    emergency clock cap truncated the search.

    Why no strict marker in either direction. The question this asks — does a
    real 80-class solve fit in its 120 s budget? — has no runner-independent
    answer. Measured on one machine within one hour: run alone it finishes with
    14-20 % to spare (96.4 s, 103.6 s); run inside this module's own slow lane
    it hits the cap at 120.07 s. CI is 1.87x faster again. ``strict=True`` turns
    every fast, correct run into a build failure; no marker turns every slow,
    correct run into one. Neither states a defect, and a gate whose colour
    depends on what else the machine was doing is the kind CI learns to ignore.
    The marker is kept, and kept non-strict, so the report still records which
    side of the line the runner fell on.

    What replaced it. The runner-independent half of ST-SCHED-013 — that the
    budget is one number, and that the search actually stops at it instead of
    spending it once per phase — is asserted unconditionally by
    ``test_the_shipped_budget_has_a_single_definition`` and
    ``test_the_lns_phase_stops_at_the_solve_wide_deadline`` below, neither of
    which asserts a second of wall clock.

    Deliberately NOT asserted as a wall-clock bound. The flag is a property of
    the code's own budget arithmetic; the seconds are a property of the runner.
    """
    from scheduler_app.core.workflow import SchedulingWorkflow

    state = make_preset("normal", seed=42)
    workflow = SchedulingWorkflow(state, lambda: {})
    result = workflow.reschedule({})

    # Trap: "reproducible because it did nothing". Assert a real solve first,
    # so a stubbed optimizer returning an empty schedule cannot XPASS this and
    # be mistaken for the fix.
    assert len(result.placed) >= 72, (
        f"only {len(result.placed)} of {len(state['classes'])} classes were "
        "placed; this run is too degenerate to say anything about "
        "reproducibility")

    summary = result.summary
    assert summary["deterministic"] is True, (
        "the shipped 80-class reschedule reports deterministic=False "
        f"(runs_completed={summary.get('runs_completed')} of 5). The user got "
        "a partial search whose result depends on their machine's speed.")


# ===========================================================================
# 5. THE SHIPPED BUDGET IS ONE DEFINITION (ST-ARCH-010)
# ===========================================================================
def _facade_reschedule_defaults():
    """The AST of ``optimized_reschedule_all``'s two budget defaults.

    Read from source, not from ``inspect.signature``. A signature reports the
    *value* 5, which is 5 whether it came from ``DEFAULT_MULTI_START_RUNS`` or
    from a literal typed next to it — and a literal that happens to agree is
    precisely the defect this guards. Only the source says which.
    """
    import ast
    import pathlib

    import scheduler_app.core.facade as facade

    tree = ast.parse(pathlib.Path(facade.__file__).read_text(encoding="utf-8"))
    for node in tree.body:
        if (isinstance(node, ast.FunctionDef)
                and node.name == "optimized_reschedule_all"):
            args = node.args
            defaults = dict(zip([a.arg for a in args.args][-len(args.defaults):],
                                args.defaults))
            return node, defaults
    raise AssertionError(
        "optimized_reschedule_all is no longer a module-level def in "
        "core/facade.py; this guard cannot read its defaults")


@pytest.mark.engine
def test_the_shipped_budget_has_a_single_definition():
    """ST-ARCH-010 — the budget the app runs must not be a literal in the facade.

    ``core/constants.py`` exists so that the optimizer and the progress bar read
    one copy of the search budget. But the app does not construct
    ``ScheduleOptimizer`` directly: every solve a user starts goes
    ``ui/app.py`` -> ``SolverTask`` -> ``core/solver_worker.run_solve`` ->
    ``workflow.reschedule`` -> ``facade.optimized_reschedule_all``, with **no
    budget keywords anywhere on that path**. So the numbers that actually run
    are this function's signature defaults, and while they were literals there
    were three copies, not one.

    Measured consequence of the drift that made this test necessary: with
    ``DEFAULT_MULTI_START_RUNS`` moved to 8 and the facade literal left at 5,
    ``solver_worker`` scales the bar to 8 runs while the optimizer runs 5, so a
    solve that completed left the progress bar at 62.5 % — and
    ``pytest tests/test_solver_worker.py`` stayed green through all of it,
    because its own guard reads ``ScheduleOptimizer.__init__``'s signature,
    which follows the constant, and never reaches the facade.

    Asserted from the AST rather than from the values, because equal values are
    exactly what a shadowing literal looks like from the outside.
    """
    import ast

    from scheduler_app.core import constants

    _node, defaults = _facade_reschedule_defaults()
    expected = {
        "multi_start_runs": "DEFAULT_MULTI_START_RUNS",
        "multi_start_time_limit": "DEFAULT_MULTI_START_TIME_LIMIT",
    }
    for param, const_name in expected.items():
        assert param in defaults, (
            f"`{param}` has no default in optimized_reschedule_all, so the "
            "production call shape no longer decides the budget; this guard "
            "cannot see what the app runs")
        default = defaults[param]
        assert isinstance(default, ast.Name), (
            f"optimized_reschedule_all's `{param}` default is a literal "
            f"({ast.dump(default)}) in core/facade.py. That is the copy the "
            "app runs and it shadows "
            f"core.constants.{const_name}; the progress bar in "
            "core/solver_worker.py is scaled off the constant, so the two "
            "drift silently and the bar lies. Use the constant.")
        assert default.id == const_name, (
            f"optimized_reschedule_all's `{param}` default is `{default.id}`, "
            f"not `{const_name}` — the budget has a second name again")
        assert hasattr(constants, const_name), (
            f"core/constants.py does not define {const_name}")


@pytest.mark.engine
def test_the_budget_constants_are_what_the_optimizer_is_built_with():
    """Anti-vacuity half of the test above.

    The AST check forbids a second *definition*; this one proves the single
    definition is the number that reaches ``ScheduleOptimizer``. A facade that
    imported the constant and then passed something else would satisfy the
    other test.

    The optimizer is replaced by a recorder that aborts before any search runs,
    so this costs no solve.
    """
    from scheduler_app.core import constants, facade
    from scheduler_app.core.solver_worker import run_solve

    class _Stop(Exception):
        pass

    seen = {}

    def _recorder(state, **kwargs):
        seen.update(kwargs)
        raise _Stop

    class _FakeWorkflow:
        """Only what ``run_solve`` touches: it forwards to the facade."""

        def reschedule(self, weights, **kwargs):
            return facade.optimized_reschedule_all(
                {"classes": []}, weights=weights, **kwargs)

    original = facade.ScheduleOptimizer
    facade.ScheduleOptimizer = _recorder
    try:
        # The production call shape: solver_worker passes no budget keywords.
        with pytest.raises(_Stop):
            run_solve(_FakeWorkflow(), {}, on_progress=None, seed=None,
                      use_cpsat=False)
    finally:
        facade.ScheduleOptimizer = original

    assert seen["multi_start_runs"] == constants.DEFAULT_MULTI_START_RUNS, (
        f"the app builds the optimizer with multi_start_runs="
        f"{seen['multi_start_runs']}, but core/constants.py — which "
        "core/solver_worker.py uses as the progress bar's denominator — says "
        f"{constants.DEFAULT_MULTI_START_RUNS}")
    assert (seen["multi_start_time_limit"]
            == constants.DEFAULT_MULTI_START_TIME_LIMIT), (
        f"the app builds the optimizer with multi_start_time_limit="
        f"{seen['multi_start_time_limit']}, but core/constants.py says "
        f"{constants.DEFAULT_MULTI_START_TIME_LIMIT}. ui/dialogs.py quotes the "
        "constant to the user as the production budget.")


# ===========================================================================
# 6. THE BUDGET BOUNDS THE WHOLE SOLVE, NOT EACH PHASE (ST-PERF-008)
# ===========================================================================
def _capture_lns_calls(preset, budget):
    """Run the production reschedule, recording every ``_lns_improve`` call.

    Returns ``(solve_start, captured, summary)`` where each entry of
    ``captured`` is ``(optimizer, args, kwargs, entered_at)``.
    """
    import time

    from scheduler_app.core import facade
    from scheduler_app.core.schedule_optimizer import ScheduleOptimizer

    captured = []
    original = ScheduleOptimizer._lns_improve

    def spy(self, *args, **kwargs):
        captured.append((self, args, dict(kwargs), time.time()))
        return original(self, *args, **kwargs)

    state = make_preset(preset, seed=42)
    ScheduleOptimizer._lns_improve = spy
    try:
        solve_start = time.time()
        *_rest, summary = facade.optimized_reschedule_all(
            state, weights={}, multi_start_time_limit=budget)
    finally:
        ScheduleOptimizer._lns_improve = original
    return solve_start, captured, summary, original


@pytest.mark.engine
def test_the_lns_phase_stops_at_the_solve_wide_deadline():
    """ST-PERF-008's remaining half — a capped solve must not overrun its budget.

    ``multi_start_time_limit`` is documented as "Total time limit across all
    runs", and PROGRESS recorded the shape of this bug once already in Phase 3:
    a wall-clock bound sampled every N nodes is not a wall-clock bound. The
    greedy phase was fixed then and receives an absolute ``deadline``. The LNS
    phase was not: its emergency check compared ``time.time() - start_time``,
    a stopwatch started at the top of *that phase*, against
    ``multi_start_time_limit``, the budget for the *whole solve* — so every
    phase was entitled to spend the entire budget again.

    Measured through ``optimized_reschedule_all``, the exact signature
    ``workflow.reschedule`` calls, before the fix:

        normal / 8 s budget    LNS entered at 1.58 s, ran to 9.71 s (1.22x)
        normal / 21 s budget   LNS entered at 21.01 s, i.e. after the deadline
        small / 5 s budget     ran to 6.12 s (1.22x)

    and the reporting verifier measured 34.97 s against a 21 s budget (1.66x)
    on a loaded box. Worst case is ~2x: reach the deadline, then start one more
    phase and give it the whole budget over again. After the fix the same four
    configurations overran by 0.00-0.40 s.

    **Why this asserts no number of seconds.** A wall-clock ceiling here would
    be a gate whose threshold is a property of the runner: ubuntu-latest is
    1.87x faster than the machine any threshold would be calibrated on and
    runner variance on identical code is 1.36-1.49x. The two things that are
    *not* runner-dependent are asserted instead — that the phase is handed the
    solve-wide deadline, and that it stops when that deadline has passed.
    """
    import time

    budget = 4.0
    solve_start, captured, _summary, real_lns = _capture_lns_calls(
        "small", budget)

    assert captured, (
        "the LNS phase never ran on this instance, so it says nothing about "
        "whether LNS respects the budget")

    # ── 1. The phase is handed the deadline, and it is the SOLVE's ──
    for i, (_opt, _args, kwargs, _entered) in enumerate(captured):
        assert kwargs.get("deadline") is not None, (
            f"LNS phase {i} was started with no `deadline` "
            f"(kwargs: {sorted(kwargs)}). It can therefore only compare its "
            "own stopwatch against the whole solve's budget, which is how a "
            "phase entered one second before the deadline goes on to spend "
            "the entire budget again.")

    deadlines = {kwargs["deadline"] for _o, _a, kwargs, _e in captured}
    assert len(deadlines) == 1, (
        f"the {len(captured)} LNS phases were given {len(deadlines)} different "
        "deadlines. `multi_start_time_limit` is documented as the total across "
        "all runs; a per-phase deadline is the defect wearing the fix's name.")

    deadline = deadlines.pop()
    first_entry = captured[0][3]
    assert solve_start + budget <= deadline <= first_entry + budget, (
        f"the deadline is {deadline - solve_start:.2f}s after the solve "
        f"started, against a {budget}s budget. It must be the solve's own "
        "start plus the budget — measured between the call into the facade "
        f"({solve_start - solve_start:.2f}s) and the first LNS phase "
        f"({first_entry - solve_start:.2f}s).")

    # ── 2. The phase stops when that deadline has passed ──
    #
    # Replayed with the arguments the first real phase was given, so this
    # exercises the shipped call shape rather than a hand-built one. An expired
    # deadline is checked at the top of the loop, before any occupancy is
    # touched, so replaying costs nothing and mutates nothing.
    opt, args, kwargs, _entered = captured[0]
    opt._clock_capped = False
    started = time.perf_counter()
    _solution, stats = real_lns(
        opt, *args, **{**kwargs, "deadline": time.time() - 1.0})
    replayed = time.perf_counter() - started

    assert stats["strategy_stats"], (
        "the replayed phase returned before reaching its loop at all (fewer "
        "than 3 placements to work with), so this replay proves nothing about "
        "the deadline")
    uses = sum(s["uses"] for s in stats["strategy_stats"])
    assert uses == 0, (
        f"handed a deadline that passed a second ago, the LNS phase still ran "
        f"{uses} destroy-repair iterations in {replayed:.2f}s. The budget does "
        "not bound it.")
    assert opt._clock_capped is True, (
        "the LNS phase stopped at the deadline without setting `_clock_capped`, "
        "so summary['deterministic'] stays True and a truncated, "
        "machine-speed-dependent timetable claims to be reproducible "
        "(ST-SCHED-013).")

    # ── 3. Anti-vacuity: it is the deadline that stopped it, not the replay ──
    opt._clock_capped = False
    opt.lns_iterations = 3
    _solution2, stats2 = real_lns(
        opt, *args, **{**kwargs, "deadline": time.time() + 3600.0})
    uses2 = sum(s["uses"] for s in stats2["strategy_stats"])
    assert uses2 > 0, (
        "the same replayed call does no work with an hour of budget either, so "
        "the zero above was not the deadline stopping the search — this test "
        "would pass against an LNS phase that had been stubbed out")


# ===========================================================================
# 7. THE RESTART COUNT THE USER IS SHOWN (ST-SCHED-013)
# ===========================================================================
def _count_restarts(preset, **overrides):
    """Run the production reschedule, counting real greedy-construction phases.

    One ``_greedy_construct`` call is one restart: it is the first thing a
    multi-start iteration does, and the emergency clock cap breaks *before* it.
    Returns ``(greedy_phases, summary)``.
    """
    from scheduler_app.core import facade
    from scheduler_app.core.schedule_optimizer import ScheduleOptimizer

    calls = []
    original = ScheduleOptimizer._greedy_construct

    def spy(self, *args, **kwargs):
        calls.append(1)
        return original(self, *args, **kwargs)

    state = make_preset(preset, seed=42)
    ScheduleOptimizer._greedy_construct = spy
    try:
        *_rest, summary = facade.optimized_reschedule_all(
            state, weights={}, **overrides)
    finally:
        ScheduleOptimizer._greedy_construct = original
    return len(calls), summary


@pytest.mark.engine
def test_runs_completed_counts_restarts_that_actually_ran():
    """The restart count in the toast must be a count of restarts that happened.

    ``summary['runs_completed']`` is not a diagnostic. ``ui/app.py`` puts it in
    the post-reschedule toast and ``core/explanation_engine.py`` puts it in the
    explanation dialog, both as a plain number of search restarts, and it is the
    user's only evidence for how much search their timetable got.

    It was ``min(n_runs, run + 1)``. The emergency clock cap breaks at the *top*
    of iteration ``run``, before that iteration does any work, so exactly
    ``run`` restarts completed and the reported figure was one too many —
    measured, on every capped configuration: small/1.0 s reported 2 for 1,
    small/2.0-3.0 s reported 3 for 2, small/5.0 s reported 4 for 3, normal/4.0,
    8.0 and 21.0 s reported 2 for 1. Uncapped at the shipped 120 s budget it was
    correct (5 for 5), which is why nothing caught it: the error appears only on
    the machines that got the least search, and it overstates what they got.

    One consequence worth recording, because it is cited as evidence elsewhere
    in this tree: the "runs_completed 3-4 of 5" figures in the xfail reason
    above were produced by this expression, so whatever their author measured
    was 2-3 restarts, not 3-4.

    Both directions are asserted. A capped solve must report what it ran, and an
    uncapped one must still report all of them — an off-by-one is as easy to
    introduce in the other direction, and the uncapped case is the one every
    user on adequate hardware sees.
    """
    # ── Capped: a budget far below what five restarts of `small` cost ──
    # (measured ~1.0-1.4 s per restart on this machine, so 2 s cannot fit five;
    # ubuntu-latest is 1.87x faster and still cannot.)
    phases, summary = _count_restarts("small", multi_start_time_limit=2.0)

    assert summary["deterministic"] is False, (
        f"the {phases}-restart solve given a 2 s budget reports "
        "deterministic=True, i.e. the clock cap never fired — this instance "
        "cannot say anything about how a capped run counts its restarts. "
        "Lower the budget rather than deleting the assertion.")
    assert summary["runs_completed"] == phases, (
        f"the solve ran {phases} greedy-construction phases, i.e. {phases} "
        f"restarts, and reports runs_completed="
        f"{summary['runs_completed']}. The user is told they got "
        f"{summary['runs_completed']} restarts of search when they got "
        f"{phases}.")

    # ── Uncapped: the same count must survive when nothing is truncated ──
    phases, summary = _count_restarts(
        "tiny", multi_start_time_limit=1e9, parallel_workers=-1)

    assert summary["deterministic"] is True, (
        "the uncapped `tiny` solve reports deterministic=False, so it was "
        "truncated after all and cannot check the uncapped count")
    assert summary["runs_completed"] == phases, (
        f"an uncapped solve ran {phases} restarts and reports "
        f"runs_completed={summary['runs_completed']}")
    assert phases > 1, (
        f"only {phases} restart ran, so this half cannot tell a count of "
        "restarts apart from a hardcoded 1")
