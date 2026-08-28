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

Runtime: ~2.9 s for the whole fast half (tiny 0.3 s + small 2.6 s), sharing two
solves across every assertion. The one ``slow`` test at the bottom runs the
shipped 80-class configuration and costs ~2 min; it lives in the engine job.
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
    strict=True,
    reason="ST-SCHED-013 / ST-PERF-001, open. `optimized_reschedule_all` "
           "(core/logic.py) still defaults to multi_start_time_limit=120.0, "
           "and an 80-class solve does not finish its 5 configured restarts "
           "inside it. Measured: deterministic=False on 7/7 local runs with "
           "runs_completed 3-4 of 5, and the same fixture costs 130-177 s on "
           "ubuntu-latest against the same 120 s cap on 11/11 CI runs. Every "
           "80-class user therefore silently receives 60-80 % of the search, "
           "and which 60-80 % depends on how busy their machine was. PROGRESS "
           "recorded the limit as raised to 3600 s in Phase 2; Phase 4 found "
           "the raise inert because production goes through this signature "
           "default. Nothing asserted it until now.")
def test_the_shipped_configuration_is_reproducible_at_80_classes():
    """Open defect, ST-SCHED-013. Fails today, on purpose.

    No budget overrides: this is exactly what a user gets when they press
    *Reschedule All* on a real department timetable. ``deterministic`` is the
    optimizer's own statement that its answer can be reproduced; False means the
    emergency clock cap truncated the search.

    This XPASSes — and turns the build red, which is the point — the day the
    120 s default is raised, or the day the solve gets cheap enough to finish
    five restarts inside it. Either is the fix; delete the marker then.

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
