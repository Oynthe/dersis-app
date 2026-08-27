"""Bounds and recursion depth of the greedy construction phase.

Guards three findings that all live in the same twenty lines of
``ScheduleOptimizer._greedy_construct``:

* **ST-SCHED-012** (Medium) — the phase was written as a nested ``def solve(idx)``
  calling ``solve(idx + 1)``, so its Python stack depth was *one frame per
  flexible class*. Past ~1000 classes the interpreter's recursion limit is hit
  and the whole reschedule dies with ``RecursionError``.
* **ST-PERF-004** (High) — the same search burns its entire 100 000-iteration
  backtracking budget on a 25-class instance.
* **ST-PERF-008** (Medium) — the phase consults no clock.
  ``multi_start_time_limit`` is only read *between* multi-start runs, so it
  cannot interrupt a run that is already inside greedy construction.

Where the baselines come from
-----------------------------
Every "at HEAD" number below was measured against the pristine ``e286a25`` tree,
extracted with ``git archive HEAD`` into a scratch directory and run from there.
That indirection is deliberate: ``scheduler_app/`` was being edited *while* this
module was written, so the working tree is not a stable reference. This module
was executed against both trees; see the report that accompanies it.

Reproducing ST-SCHED-012 (done first, not assumed)
--------------------------------------------------
Through the public ``ScheduleOptimizer.optimize()`` entry point on the
1200-class ``pathological`` preset (1107 of them flexible), with 60 padding
frames on the stack to imitate pytest's own depth::

    pathological n_classes=1200 n_flex=1107 max_iterations=1400
        extra_stack_frames=60 recursionlimit=1000
    RecursionError after 92.6s: maximum recursion depth exceeded
    traceback depth: 999 frames

It is an ordinary, catchable ``RecursionError`` — **not** a Windows C-stack
overflow (no ``0xC0000409``, no silent process death). CPython 3.12 inlines
Python-to-Python calls, so this recursion consumes interpreter frames and
essentially no C stack. That is why these tests run in-process; a subprocess +
exit-code assertion is not needed on this interpreter. (If DERSİS is ever built
against a pre-3.11 CPython, revisit that: there the same recursion does eat C
stack and the crash mode can change to an uncatchable one.)

``sys.setrecursionlimit`` appears nowhere in ``scheduler_app/`` — grepped; the
only hits in the tree are inside third-party suites under ``.venv-audit``. So
the ceiling really is the stock 1000.

The depth was *exactly* linear
------------------------------
Peak Python frame depth at the moment ``CandidateGenerator.generate()`` is
entered, minus the depth at the call site, one run per preset, at HEAD::

    tiny    n_flex=  5  delta=  8
    small   n_flex= 24  delta= 27
    normal  n_flex= 79  delta= 82
    large   n_flex=235  delta=238

``delta == n_flex + 3`` at every scale. Independently, a binary search for the
smallest recursion headroom in which the phase completes returns ``n_flex + 9``
for ``tiny``, ``small`` and ``normal`` alike. Both say the same thing: the
constant part of the stack is single-digit and everything else is the per-class
recursion. Those two measurements are what set ``_RECURSION_HEADROOM`` and
``_DEPTH_GROWTH_TOLERANCE`` below — they are not guesses.

Traps these tests are shaped around
-----------------------------------
**Trap A — "clean because empty".** Imitates ``test_scheduler_invariants``'
Trap 1. "Recursion depth did not grow" and "the solve finished inside the
budget" are both trivially true of an optimizer that proposes nothing. Every
test here that measures a bound also asserts a placement floor, and the depth
probe additionally asserts that its instrument actually fired.

**Trap B — buying the bound with placements.** ST-PERF-004's obvious fix is to
cut ``max_iterations``. Measured through the full shipped workflow at HEAD,
sweeping the budget over 100000 / 20000 / 5000 / 1000 / 200::

    small  at every budget:      raw=21 raw_clean=19 rejected=1  committed=20
    normal at 100000 and 1000:   raw=76 raw_clean=39 rejected=15 committed=61

Identical at every point. At these scales the 100 000-iteration budget buys
literally zero placements — LNS recovers the same answer from a 200-iteration
greedy. That makes the naive cut *safe here*, which is exactly why a regression
guard is needed: nothing in the suite would have noticed if it were not.
``test_bounding_does_not_cost_placements`` pins those numbers.

**Trap C — sampling the clock every N search nodes is not a wall-clock bound.**
It only bounds the solve if N nodes are cheap, and they are not: the early nodes
of greedy construction do full candidate generation plus look-ahead over ten
successors, while the late ones are shallow backtracks. Measured against an
in-flight Phase-3 attempt that tests the deadline every 512 nodes::

    large      budget= 1.0s elapsed=26.6s overshoot=25.6s greedy_nodes=512
    large      budget= 5.0s elapsed=23.8s overshoot=18.8s greedy_nodes=512
    large      budget=15.0s elapsed=33.8s overshoot=18.8s greedy_nodes=512
    very_large budget= 5.0s elapsed=65.3s overshoot=60.3s greedy_nodes=512

The overshoot is the same ~19 s whether the budget is 1 s or 15 s, and it grows
to 60 s when the instance grows — it is a property of the instance, not of the
budget. ``test_greedy_phase_respects_the_wall_clock_budget`` therefore runs on
``very_large`` and not on ``large``: on ``large`` the 512-node window costs
anywhere from 6 s to 19 s depending on machine load (17.2 / 22.7 / 23.6 s
elapsed over three runs, 45.0 s under saturating load), which straddles every
ceiling a correct implementation could also clear. On ``very_large`` the same
window costs 60 s and the two behaviours separate cleanly.

The same trap has a second, quieter face. ``very_large`` at
``multi_start_time_limit=2`` returns in 3.2 s having run **zero** search nodes:
the pre-search setup already used the whole budget, so the very first deadline
check fires and the phase never starts. Fast, bounded, and useless. Any test of
this property has to be run with a budget bigger than the setup cost, or it
passes for that reason instead.

**Trap D — the ST-SCHED-001 fix moves the placement counts.** It lands in this
same phase and makes LNS stop repairing against a near-empty occupancy map, so
it legitimately places *fewer but valid* classes. Trap B's floors therefore
carry explicit headroom below the HEAD measurement, and each floor is paired
with "the committed schedule is oracle-clean" so fewer can never be traded for
more broken.

Runtime (measured, this machine, ``.venv-audit``)
-------------------------------------------------
Re-measured on the fixed tree (the figures that were here before were taken
before the Phase 3 bounds landed and were stale by 2x to 20x):

``pytest tests/test_greedy_bounds.py -m "not slow"``   ~5 s
``pytest tests/test_greedy_bounds.py``                 ~135 s

The slow half is `test_bounding_does_not_cost_placements` (``small`` ~7 s,
``normal`` ~123 s — the latter still clock-capped at 120 s by
``optimized_reschedule_all``'s ``multi_start_time_limit=120.0``) and
``test_pathological_preset_does_not_exhaust_the_stack`` (~90 s; it raised
RecursionError at HEAD). The wall-clock pin is now ~5-6 s against its 5 s
budget, down from 125-291 s at HEAD — it is in the non-slow lane for that
reason. The bands are machine load, not variance in the code. CI runs
``-m "not slow"``.
"""
import sys
import time

import pytest

from _support.dataset_gen import make_preset
from _support.schedule_oracle import (
    HARD_CATEGORIES,
    check_schedule,
    format_violations,
    hard_violation_count,
)

# The interpreter's limit as it stood when this module was imported, captured
# before any test can touch it. `test_recursion_limit_is_restored_after_lowering`
# compares against this rather than a hard-coded 1000, so the check keeps working
# if CI ever raises the default.
_IMPORT_TIME_RECURSION_LIMIT = sys.getrecursionlimit()

# Extra recursion headroom granted to `test_greedy_survives_a_lowered_recursion
# _limit`. From the binary search quoted in the module docstring: at HEAD the
# phase needs `n_flex + 9` frames, i.e. 33 on `small`; a depth that does not grow
# with the instance needs ~10. 20 sits between them with ~2x margin either way.
_RECURSION_HEADROOM = 20

# How much peak stack depth may grow between two instances whose flexible-class
# counts differ by 55. At HEAD it grew by exactly 55.
_DEPTH_GROWTH_TOLERANCE = 8

# Wall-clock pin (ST-PERF-008), on the 600-class `very_large` preset. Measured
# elapsed for `multi_start_runs=1, lns_iterations=0, multi_start_time_limit=5`:
#
#   HEAD, no clock check anywhere               125.1 s   deterministic=True
#   deadline sampled every 512 search nodes      65.3 s   deterministic=False
#   ...the same, under saturating CPU load      168.1 s   deterministic=False
#   a bound that is actually a bound            ~7    s   (5 s budget + 2.0 s of
#                                                          measured pre-search
#                                                          setup, the same solve
#                                                          with max_iterations=1)
#
# A 30 s ceiling therefore sits ~4x above a correct implementation at idle and
# 2.2x below the nearest incorrect one, and the incorrect side only moves up
# under load. The grace has to be this wide because the part of the solve that
# a clock bound does NOT cover — setup and the post-search scoring — is what
# stretches on a loaded machine: measured on `normal`, where the existing bound
# does hold, elapsed went from 5.1 s idle to 15.9 s under twelve competing
# busy-loops while the search itself still stopped on time.
#
# `large` was tried first and rejected: there the 512-node window costs anywhere
# from 6 s to 19 s depending on load, which straddles every ceiling a correct
# implementation could also clear.
#
# The budget cannot go much below 5 s: pre-search setup on this preset costs
# 2.0 s idle and ~4 s loaded, and once the budget is under that the deadline
# fires at search node 0 — the run then looks fast for the wrong reason and the
# granularity defect never gets a chance to show. Verified at
# `multi_start_time_limit=2`: 3.2 s elapsed, 0 search nodes.
_CLOCK_BUDGET = 5.0
_CLOCK_GRACE = 25.0

# Anti-vacuity floor (Trap A), same spirit and same value as
# `test_scheduler_invariants._MIN_PLACED_FRACTION`.
_MIN_PLACED_FRACTION = 0.5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _frame_depth():
    """Number of Python frames currently on the stack, this one included."""
    depth = 0
    frame = sys._getframe()
    while frame is not None:
        depth += 1
        frame = frame.f_back
    return depth


def _n_flexible(state):
    """Classes the optimizer will hand to the greedy phase."""
    return sum(1 for c in state["classes"] if not c["pinned"])


def _greedy_only_optimizer(state, **overrides):
    """A ScheduleOptimizer configured so that only greedy construction runs.

    ``lns_iterations=0`` empties ``_lns_improve``'s ``for`` loop and
    ``multi_start_runs=1`` means the between-runs clock check (``run > 0 and
    elapsed_total >= self.multi_start_time_limit``) is never reached — so
    nothing but the greedy phase can account for the stack depth or the wall
    clock these tests measure. ``parallel_workers=-1`` disables the
    ``ParallelScorerPool``; keeping the solve single-process is what stops the
    wall-clock pin from depending on the CI machine's core count.
    """
    from scheduler_app.schedule_optimizer import ScheduleOptimizer

    params = dict(multi_start_runs=1, lns_iterations=0, parallel_workers=-1)
    params.update(overrides)
    return ScheduleOptimizer(state, **params)


def _assert_not_degenerate(state, placed, label):
    """Trap A: refuse to let a bound assertion pass on an empty schedule."""
    floor = max(1, int(len(state["classes"]) * _MIN_PLACED_FRACTION))
    assert len(placed) >= floor, (
        f"DEGENERATE RUN on `{label}`: the optimizer proposed only "
        f"{len(placed)} of {len(state['classes'])} classes (floor {floor}). "
        "Every bound assertion in this test is vacuous on a run this empty.")


def _measure_peak_depth(preset):
    """Run greedy construction on *preset*; return (depth_growth, n_flex).

    ``depth_growth`` is the deepest Python stack seen during the phase, measured
    at ``CandidateGenerator.generate()`` and expressed relative to the depth of
    this function — so it does not depend on how deep pytest itself happens to
    be when the test runs.

    ``generate()`` is the probe point because the construction loop calls it
    exactly once per class it visits, from whatever the search's own stack
    depth is. The absolute value also picks up the few frames between the search
    and ``generate``; that constant cancels when two scales are compared, which
    is why the assertion below is about *growth*, never an absolute number.
    """
    from scheduler_app.candidate_generator import CandidateGenerator

    state = make_preset(preset, seed=42)
    n_flex = _n_flexible(state)
    # Enough budget for one full descent (n_flex + 1 nodes, which is where the
    # deepest stack is reached) plus a little backtracking.
    optimizer = _greedy_only_optimizer(state, max_iterations=n_flex + 25)

    base = _frame_depth()
    peak = [0]
    calls = [0]
    original_generate = CandidateGenerator.generate

    def spy(self, cls):
        calls[0] += 1
        depth = _frame_depth()
        if depth > peak[0]:
            peak[0] = depth
        return original_generate(self, cls)

    CandidateGenerator.generate = spy
    try:
        placed, _unplaced, _changes, _summary = optimizer.optimize()
    finally:
        CandidateGenerator.generate = original_generate

    _assert_not_degenerate(state, placed, preset)
    assert calls[0] >= n_flex, (
        f"the depth probe fired only {calls[0]} times for {n_flex} flexible "
        f"classes on `{preset}` — it is no longer observing the construction "
        "phase, so the depth it reports means nothing")
    return peak[0] - base, n_flex


@pytest.fixture
def restored_recursion_limit():
    """Put ``sys.setrecursionlimit`` back however the test exits.

    A test that lowers the limit and then fails would otherwise leave every
    later test — and pytest's own reporting, which recurses through the
    traceback — running against the lowered ceiling. The teardown asserts the
    restore actually took effect, so a helper that "restores" to the wrong value
    surfaces as an error here instead of as mystery failures downstream.
    """
    original = sys.getrecursionlimit()
    yield original
    sys.setrecursionlimit(original)
    assert sys.getrecursionlimit() == original, (
        "the recursion limit was not restored after this test")


# ===========================================================================
# 0. INSTRUMENT SELF-TESTS — prove the probes are not no-ops
# ===========================================================================
def test_frame_depth_probe_counts_one_frame_per_call():
    """Guards the depth instrument itself (no finding ID).

    If this fails, ``_measure_peak_depth`` has stopped measuring anything and
    the ST-SCHED-012 guard below would report "depth did not grow" no matter how
    deeply the scheduler recursed — i.e. a user's 1200-class file could go back
    to crashing with nobody noticing.
    """
    def recurse(n, out):
        out.append(_frame_depth())
        if n:
            recurse(n - 1, out)

    seen = []
    recurse(20, seen)

    assert len(seen) == 21
    deltas = {seen[i + 1] - seen[i] for i in range(len(seen) - 1)}
    assert deltas == {1}, (
        f"frame depth did not increase by exactly 1 per call: {sorted(deltas)}")


def test_lowering_the_recursion_limit_really_bites(restored_recursion_limit):
    """Guards the lowered-limit instrument itself (no finding ID).

    Proves the technique used by the ST-SCHED-012 CI guard below can fail at
    all. Without this, a ``sys.setrecursionlimit`` that silently did nothing
    would make that guard pass vacuously.
    """
    def recurse(n):
        return 0 if n == 0 else recurse(n - 1)

    sys.setrecursionlimit(_frame_depth() + 15)
    with pytest.raises(RecursionError):
        recurse(500)
    sys.setrecursionlimit(restored_recursion_limit)
    assert recurse(500) == 0, "the restored limit is still too low to recurse"


# ===========================================================================
# 1. RECURSION DEPTH  (ST-SCHED-012)
# ===========================================================================
@pytest.mark.engine
def test_greedy_stack_depth_does_not_grow_with_class_count():
    """Regression guard for ST-SCHED-012 (Medium) — the CI-sized carrier.

    A failure means the scheduling engine's stack usage scales with the size of
    the school again: every extra class the user types in brings the app one
    frame closer to dying with ``RecursionError`` mid-reschedule. This is the
    non-slow half of the guard; the 1200-class reproduction below cannot run in
    CI, so this is the signal CI actually sees.

    Fail-before evidence: against pristine ``e286a25`` this reports growth of
    exactly 55 frames between `small` (24 flexible) and `normal` (79) — one
    frame per class, to the frame — against a tolerance of 8.
    """
    small_growth, small_n = _measure_peak_depth("small")
    normal_growth, normal_n = _measure_peak_depth("normal")

    extra_classes = normal_n - small_n
    assert extra_classes >= 50, (
        "the two presets no longer differ enough in size for this comparison "
        f"to mean anything ({small_n} vs {normal_n} flexible classes)")

    growth = normal_growth - small_growth
    assert growth <= _DEPTH_GROWTH_TOLERANCE, (
        f"greedy construction got {growth} Python frames deeper when the "
        f"instance grew by {extra_classes} flexible classes "
        f"(small: +{small_growth} frames at {small_n} classes; "
        f"normal: +{normal_growth} at {normal_n}). Stack depth must be bounded "
        "by a constant, not by the timetable's size.")


@pytest.mark.engine
def test_greedy_survives_a_lowered_recursion_limit(restored_recursion_limit):
    """Regression guard for ST-SCHED-012 (Medium), from the other side.

    Squeezes the interpreter down to a fixed amount of headroom and asks the
    scheduler to solve a 25-class timetable inside it. A failure means the
    engine's appetite for stack is proportional to the instance again, which is
    what turns a large school's reschedule into a crash rather than a slow run.

    Fail-before evidence: against pristine ``e286a25`` this raises
    ``RecursionError`` — measured there, the phase needs ``n_flex + 9`` = 33
    frames on `small` and is given 20.

    The limit is restored by ``restored_recursion_limit`` on every exit path,
    including the ``RecursionError`` one; the test after this one proves it.
    """
    state = make_preset("small", seed=42)
    n_flex = _n_flexible(state)
    optimizer = _greedy_only_optimizer(state, max_iterations=n_flex + 25)

    sys.setrecursionlimit(_frame_depth() + _RECURSION_HEADROOM)
    try:
        placed, _unplaced, _changes, _summary = optimizer.optimize()
    except RecursionError:
        sys.setrecursionlimit(restored_recursion_limit)
        pytest.fail(
            f"greedy construction ran out of stack on a "
            f"{len(state['classes'])}-class instance given "
            f"{_RECURSION_HEADROOM} frames of headroom ({n_flex} flexible "
            "classes). Its depth is O(n), not O(1).")
    finally:
        sys.setrecursionlimit(restored_recursion_limit)

    # Trap A: finishing inside the headroom proves nothing if it finished by
    # placing nothing.
    _assert_not_degenerate(state, placed, "small")


def test_recursion_limit_is_restored_after_lowering():
    """Guards the fixture teardown (no finding ID).

    Runs after the two tests above, both of which lower
    ``sys.setrecursionlimit``. If this fails, one of them leaked a lowered
    ceiling into the rest of the session and any later test could die of
    ``RecursionError`` for a reason unrelated to the code it is testing.
    """
    assert sys.getrecursionlimit() == _IMPORT_TIME_RECURSION_LIMIT, (
        f"recursion limit leaked: {sys.getrecursionlimit()} != "
        f"{_IMPORT_TIME_RECURSION_LIMIT}")


@pytest.mark.engine
@pytest.mark.slow
def test_pathological_preset_does_not_exhaust_the_stack():
    """Regression guard for ST-SCHED-012 (Medium) at the scale that bites.

    A failure means a school with ~1200 class-hours cannot reschedule at all:
    the optimizer dies part-way through construction and the user gets an
    unhandled crash instead of a timetable.

    Fail-before evidence: against pristine ``e286a25`` this raises
    ``RecursionError`` after ~93 s, at roughly the 940th of 1107 flexible
    classes, with a 999-frame traceback.

    ``max_iterations`` is pinned to 1400 (one full descent over the 1107
    flexible classes plus room to backtrack) purely to keep the *fixed*
    version's runtime finite — the phase's clock bound is ST-PERF-008's problem
    and is pinned separately below. It does not affect the failure: the stack
    runs out long before the iteration budget is touched.
    """
    state = make_preset("pathological", seed=42)
    n_flex = _n_flexible(state)
    assert n_flex > _IMPORT_TIME_RECURSION_LIMIT, (
        f"`pathological` now has only {n_flex} flexible classes, which no "
        f"longer exceeds the recursion limit ({_IMPORT_TIME_RECURSION_LIMIT}) "
        "— this test can no longer reproduce ST-SCHED-012")

    optimizer = _greedy_only_optimizer(state, max_iterations=1400)
    started = time.perf_counter()
    try:
        placed, _unplaced, _changes, _summary = optimizer.optimize()
    except RecursionError:
        # Deliberately not re-raised: pytest would render a ~1000-frame
        # traceback that says nothing this message does not.
        pytest.fail(
            f"RecursionError after {time.perf_counter() - started:.1f}s while "
            f"scheduling {n_flex} flexible classes — greedy construction "
            "recursed past the interpreter's limit.")

    _assert_not_degenerate(state, placed, "pathological")


# ===========================================================================
# 2. WALL CLOCK  (ST-PERF-008)
# ===========================================================================
@pytest.mark.engine
@pytest.mark.slow
def test_greedy_phase_respects_the_wall_clock_budget():
    """Pins ST-PERF-008 (Medium).

    ``multi_start_time_limit`` is documented as "Total time limit across all
    runs". A failure means it is not: the user asked for a bounded reschedule,
    the progress dialog counts toward a number that means nothing, and the app
    sits unresponsive for as long as the search happens to take — three times
    longer on a big school than on a small one, which is precisely the case
    where the user needed the bound.

    Two assertions, because the timing one alone is escapable:
      1. The solve returns within the budget plus a generous grace. This is the
         weakest honest form of the property, so it holds under any reasonable
         shape of fix (bound the phase, bound each run, or bound the search
         globally) and commits the implementer to no API that does not exist.
      2. ``summary['deterministic'] is False``. This is the anti-vacuity half
         (Trap A): it says the *budget* is what stopped the search, not that the
         search happened to have nothing left to do — a timing assertion on a
         solve that finished early proves nothing. It is also the codebase's own
         established contract for a truncated run (ST-SCHED-013): the LNS
         emergency cap already reports itself this way, and a clock bound in
         greedy construction has to as well or a capped result would silently
         claim to be reproducible. At HEAD this half fails too, because with a
         single multi-start run ``_clock_capped`` can never be set.

    Setup notes, all load-bearing:
      * ``multi_start_runs=1`` — with a single run the between-runs check
        (``if run > 0 and elapsed_total >= self.multi_start_time_limit``) can
        never execute, so at HEAD nothing anywhere can stop this solve.
        Measured there: the run overshot its budget 7x and still reported
        ``summary['deterministic'] is True``, because ``_clock_capped`` is only
        set by that unreachable branch.
      * ``lns_iterations=0`` — LNS has its own emergency clock check, so leaving
        it on would let this pass on a fix that bounded LNS and left greedy
        construction alone.
      * ``max_iterations`` stays at the shipped 100 000. Passing a smaller one
        would let the *iteration* bound stop the search and the test would go
        green with no clock bound existing at all.
      * ``parallel_workers=-1`` — a wall-clock assertion must not depend on the
        CI machine's core count.

    Note what is deliberately NOT asserted: a placement floor. On this preset
    with LNS disabled and a 5 s budget, a correctly bounded search legitimately
    places almost nothing (24 of 600 today) — that is the bound working, not the
    engine failing, and the shipped configuration fills the rest in during LNS.
    The completeness check below is the substitute: it proves every class was
    accounted for, which a stubbed-out optimizer would not manage.

    Flake analysis, verified under 12 busy-loop processes on a 12-core box: the
    failing direction is one-sided — contention can only make elapsed times
    larger, never smaller — and measured, this run went from 65.3 s idle to
    168.1 s loaded, further from the ceiling rather than nearer. The passing
    direction is load-tolerant for a different reason: once the bound is real,
    elapsed time is dominated by the budget itself, and a budget does not
    stretch under load. Only the untimed remainder does, and the closest
    available measurement of that — `normal`, where the existing bound already
    holds — grew from 5.1 s to 15.9 s under the same load, still well inside
    this ceiling.
    """
    state = make_preset("very_large", seed=42)
    optimizer = _greedy_only_optimizer(
        state, multi_start_time_limit=_CLOCK_BUDGET)

    started = time.perf_counter()
    _placed, _unplaced, _changes, summary = optimizer.optimize()
    elapsed = time.perf_counter() - started

    ceiling = _CLOCK_BUDGET + _CLOCK_GRACE
    assert elapsed <= ceiling, (
        f"a solve given multi_start_time_limit={_CLOCK_BUDGET}s ran for "
        f"{elapsed:.1f}s ({elapsed / _CLOCK_BUDGET:.1f}x its budget; ceiling "
        f"{ceiling:.0f}s, of which {_CLOCK_GRACE:.0f}s is grace) on "
        f"{_n_flexible(state)} flexible classes, stopping after "
        f"{summary['greedy_stats']['iterations_used']} search nodes. Either "
        "the greedy construction phase never looks at the clock, or it looks "
        "so rarely that the interval between looks is itself unbounded.")

    # Trap A, first half: the optimizer really did process this instance.
    #
    # `accounted` alone does NOT establish that — placed + unplaced sums to the
    # class count no matter what the search did, so a _greedy_construct that
    # returned [None] * n and set _clock_capped would satisfy every other
    # assertion here (measured: 1.35 s, PASSED). The two below measure the
    # search itself:
    #   * it visited a non-trivial number of nodes before the budget bit, and
    #   * it placed something the greedy phase actually found, not just the
    #     pinned classes it inherited for free. `very_large` ships 24 pinned
    #     classes, so `placed == 24` is exactly what an absent greedy phase
    #     produces.
    accounted = summary["classes_placed"] + summary["classes_unplaced"]
    assert accounted == len(state["classes"]), (
        f"the run accounted for {accounted} of {len(state['classes'])} classes "
        "— it did not actually solve this instance, so its elapsed time says "
        "nothing")

    nodes = summary["greedy_stats"]["iterations_used"]
    assert nodes >= 50, (
        f"the greedy phase visited only {nodes} search nodes before stopping. "
        "A bound that fires before the search does any work makes the timing "
        "assertion above vacuous — it would pass against a phase that returns "
        "nothing at all.")

    n_pinned = sum(1 for c in state["classes"] if c.get("pinned"))
    assert len(_placed) > n_pinned, (
        f"the solve returned {len(_placed)} placements against {n_pinned} "
        "pinned classes, i.e. the greedy phase contributed nothing. The "
        "budget is supposed to truncate the search, not replace it.")

    # Trap A, second half: the budget is what stopped it. Without this, a run
    # that finished early would satisfy the ceiling above for free.
    assert summary["deterministic"] is False, (
        f"the solve finished in {elapsed:.1f}s and still reports "
        "deterministic=True, meaning no clock cap fired. Either the search was "
        "never truncated — in which case the timing assertion above proved "
        "nothing — or it was truncated without saying so, which lets a "
        "machine-speed-dependent timetable claim to be reproducible "
        "(ST-SCHED-013).")


# ===========================================================================
# 3. BOUNDING MUST NOT COST PLACEMENTS  (ST-PERF-004)
# ===========================================================================
@pytest.fixture(scope="module")
def shipped_run():
    """Cache one full, shipped-configuration workflow run per preset.

    Deliberately the *production* path — ``SchedulingWorkflow.reschedule`` with
    every optimizer default in place — because these are numbers a bounding
    change would move, and such a change is only interesting if it moves them in
    the configuration the user actually runs.
    """
    cache = {}

    def run(preset):
        if preset not in cache:
            from scheduler_app.core.workflow import SchedulingWorkflow

            state = make_preset(preset, seed=42)
            workflow = SchedulingWorkflow(state, lambda: {})
            result = workflow.reschedule({}, use_cpsat=False)
            raw = check_schedule(state, placements=result.placed)
            dirty = {v["uid"] for v in raw["violations"]
                     if v["category"] in HARD_CATEGORIES}
            rejected = workflow.apply_reschedule(result)
            cache[preset] = {
                "state": state,
                "raw_placed": len(result.placed),
                "raw_clean": len(result.placed) - len(dirty),
                "rejected": len(rejected),
                "committed": sum(1 for c in state["classes"]
                                 if c.get("placed") or c.get("pinned")),
                "applied": check_schedule(state),
            }
        return cache[preset]

    return run


# (preset, committed floor, conflict-free-proposal floor)
#
# HEAD measurements, from `SchedulingWorkflow.reschedule({}, use_cpsat=False)`
# on `make_preset(preset, seed=42)`, audited with the independent oracle:
#
#   small   raw_placed=21  raw_clean=19  rejected=1   committed=20  (of 25)
#   normal  raw_placed=76  raw_clean=39  rejected=15  committed=61  (of 80)
#
# `small` reproduced identically in 6 independent runs and `normal` in 3
# (`normal` reports deterministic=False — it is clock-capped at 120 s — and
# still landed on the same numbers every time).
#
# Those were the pre-fix numbers, and the floors below them were set expecting
# the ST-SCHED-001 fix to move `raw_placed` DOWN (LNS was thought to be
# proposing extra placements it could only make against a stale, near-empty
# occupancy map). It did not: the fix moved `raw_clean` and `committed` UP to
# meet `raw_placed`, which did not move at all.
#
#   small   raw_placed=21  raw_clean=21  rejected=0  committed=21  (of 25)
#   normal  raw_placed=76  raw_clean=76  rejected=0  committed=76  (of 80)
#
# So the floors are re-based on the fixed tree, keeping a small margin for the
# LNS acceptance RNG. The old `normal` clean_floor of 39 tolerated a 49 %
# regression in proposal cleanliness — against a phase whose whole point is
# that the proposal is clean, it was a guard that could not fail.
#
# Re-record these only alongside a deliberate, explained quality change. A
# `raw_clean` below `raw_placed` means the optimizer is proposing invalid
# placements again, which is ST-SCHED-001 reopening.
_PLACEMENT_FLOORS = [
    ("small", 20, 20),
    ("normal", 72, 72),
]


@pytest.mark.engine
@pytest.mark.slow
@pytest.mark.parametrize("preset,committed_floor,clean_floor",
                         _PLACEMENT_FLOORS)
def test_bounding_does_not_cost_placements(shipped_run, preset,
                                           committed_floor, clean_floor):
    """Regression guard for ST-PERF-004's completion criterion.

    ST-PERF-004 is "the greedy search wastes its whole 100 000-iteration
    budget", and the cheapest way to close it is to make the budget smaller. A
    failure here means someone did that and the user silently got a thinner
    timetable — fewer lessons scheduled, no message, faster.

    Currently PASSES; it is the "after" half of the bounding work, not a pin.
    Sweeping ``max_iterations`` over 100000/20000/5000/1000/200 at HEAD moved
    none of these numbers at either scale, so today's budget is provably pure
    waste — which also means nothing in the suite would have caught a bounding
    change that *did* cost placements. That is this test's job.

    The clean-schedule assertion is what stops the floors being met by a
    "solution" that double-books its way to a higher count.
    """
    run = shipped_run(preset)

    assert run["committed"] >= committed_floor, (
        f"`{preset}`: only {run['committed']} of "
        f"{len(run['state']['classes'])} classes survived into the committed "
        f"timetable (floor {committed_floor}; HEAD measured 20 for small, 61 "
        "for normal). A bound that buys speed with placements is not a fix.")

    assert run["raw_clean"] >= clean_floor, (
        f"`{preset}`: the optimizer proposed {run['raw_placed']} placements, of "
        f"which only {run['raw_clean']} are free of hard-constraint violations "
        f"(floor {clean_floor}).")

    applied = run["applied"]
    assert hard_violation_count(applied) == 0, (
        f"`{preset}`: the committed timetable is not clean, so its placement "
        "count says nothing about solution quality:\n"
        + format_violations(applied))


@pytest.mark.engine
def test_greedy_budget_is_not_exhausted_by_a_25_class_instance():
    """Pins ST-PERF-004 (High).

    ``greedy_stats['budget_exhausted']`` is the optimizer's own admission that
    it stopped searching because it ran out of iterations rather than because it
    was finished. A failure means the smallest realistic timetable in the suite
    — 25 classes on a 5x8 grid with 4 rooms — still saturates a budget sized for
    something enormously larger: wasted wall clock for the user, and the reason
    the phase has no meaningful stopping condition of its own.

    Measured at HEAD: ``iterations_used=100000, budget_exhausted=True`` on every
    run, at every scale from `small` upwards.
    """
    state = make_preset("small", seed=42)
    optimizer = _greedy_only_optimizer(state)  # shipped max_iterations
    placed, _unplaced, _changes, summary = optimizer.optimize()

    _assert_not_degenerate(state, placed, "small")

    stats = summary["greedy_stats"]
    assert stats["budget_exhausted"] is False, (
        f"greedy construction used all {stats['iterations_used']} of its "
        f"{stats['max_iterations']} iterations on a "
        f"{len(state['classes'])}-class instance with {_n_flexible(state)} "
        "flexible classes. The search has no stopping condition other than the "
        "budget.")
