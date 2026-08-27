"""Reproducibility of the DERSİS optimizer — ST-SCHED-013.

Why this module exists
----------------------
Every other engine assertion in this suite is a sample, not a proof.
``test_scheduler_invariants.py`` had to demote two ``xfail(strict=True)`` pins
to ``strict=False`` because 1 of 13 measured 80-class runs came out clean *by
luck*. Until identical input gives identical output, "the optimizer never
proposes an invalid schedule" cannot be tested, only estimated — and the
Phase 3 engine work has nothing to verify against.

What was measured before these tests were written
-------------------------------------------------
All numbers are from ``.venv-audit`` on the audit machine against the code at
``fix/phase-1-data-correctness``. They are what the assertions are calibrated
against.

1. **The global RNG is the only randomness source, and nothing seeds it.**
   ``schedule_optimizer._perturb_ordering`` (``random.shuffle``, L547),
   ``schedule_optimizer._lns_improve``'s simulated-annealing acceptance
   (``random.random``, L785), ``lns_strategies.AdaptiveStrategySelector.select``
   and ``._weighted_select`` (L562-563, L597), ``RandomDestroy.select`` and
   ``DayWindowDestroy.select`` (L271, L287). A repo-wide grep for
   ``random.<method>`` finds those seven sites and nothing else. Confirmed
   empirically: ``random.getstate()`` differs before and after one
   ``optimize()`` call.

2. **Nothing else leaks into the answer.** Three child processes with
   ``PYTHONHASHSEED`` = 1 / 2 / 999, the same pickled state and the same
   ``random.seed(1234)`` produced byte-identical placements, so no ``set`` or
   ``dict`` iteration order and no ``id()``-based ordering reaches the output.
   The ``ParallelScorerPool`` path is reproducible too — ``parallel_workers=0``
   and ``-1`` each agreed with themselves and with each other — because
   ``score_batch`` collects futures in submission order and its final ``sort``
   is stable on the score alone.

3. **A seed alone is NOT enough: how fast the machine is picks the answer.**
   With ``random.seed(1234)`` applied identically before each run, two
   ``normal`` (80-class) runs at production defaults produced different
   placements and scores **21.80 vs 23.34**, and a second identically seeded
   pair scored **27.18 vs 65.88** — a 2.4x quality spread from identical input
   and an identical seed. The mechanism is visible in the LNS iteration counts
   per multi-start run: ``[95, 58, 66, 70, 66]`` on one run against
   ``[74, 106, 44, 51, 33]`` on the next. Both burned the whole
   ``multi_start_time_limit=120.0``. How much work the optimizer does — and
   therefore how much of the RNG stream it consumes — is a function of how
   fast the machine happened to be. ``test_machine_speed_does_not_change_the
   _answer`` reproduces that mechanism deterministically with a fake clock.

4. **Where the clock does not bind, a seed already delivers determinism.**
   The same experiment at 12 and 25 classes, where the LNS loop exits on
   ``lns_no_improve_limit`` rather than on time, gave 3/3 and 2/2 identical
   runs. That split is why the fast lane below drives the search with explicit
   iteration budgets and 1e9 time limits: it isolates "the seed is wired" from
   "the budget is deterministic", and the fake-clock test covers the second
   half on its own terms.

Why the fast instance is 16 classes
-----------------------------------
The instance has to be one where the answer actually moves, or the equality
assertions pass no matter what the code does. Measured on the *fixed* code
(``small`` preset, ``seed=42``, 8 different optimizer seeds, 2 starts x 25 LNS
iterations):

===========  ==================  ================  =============
n classes    distinct placements  distinct scores   s per run
===========  ==================  ================  =============
12            3 / 8               2 (7 runs tie)    0.64
13            7 / 8               4                 0.69
14            2 / 8               2                 0.85
15            4 / 8               2                 0.90
16            8 / 8               5                 1.20
18            8 / 8               7                 5.37
===========  ==================  ================  =============

The fast lane runs 15 LNS iterations rather than 25, to buy back the time the
larger instance costs; 16 classes still separates all 8 seeds there (5 distinct
scores) at 0.94 s per run.

12 classes — the size this module originally used — is the worst of the small
sizes: seven of eight seeds converge on the same perfect ``-0.0`` score, which
makes ``test_same_seed_gives_identical_score`` almost vacuous and leaves
``test_different_seeds_explore_different_solutions`` only three distinct
answers away from a false green. 16 separates every seed, in both placement and
score, for 0.3 s more per run. Above 16 the greedy backtracking blowup
(ST-PERF-004) triples the cost. All 16 classes are placed on every seed
measured, on both the current and the fixed code.

Non-vacuity
-----------
``_run`` and ``_workflow_reschedule`` refuse a run that placed less than half
the instance. Phase 0 found three tests that passed against an optimizer
stubbed to return nothing (a schedule with no placements has no violations);
verified here by gutting ``optimize()`` in a scratch copy of the tree — without
these guards **9 of the 11 fast tests still passed**.

Runtime (measured on the audit machine, against a scratch tree with the plan
applied)
---------------------------------------------------------------------------
``pytest tests/test_optimizer_determinism.py -m "not slow"``   21.5 s
  (21.78 / 21.30 / 21.68 s over three consecutive runs)
``pytest tests/test_optimizer_determinism.py -m slow``          99 s
  (department scale 61 s, small-scale workflow 38 s)

Before the fix the whole module fails in ~4 s, because every test but one
raises ``TypeError`` on the missing ``seed`` argument.

The API these tests are written against
---------------------------------------
See the implementation plan in the hand-off for the full specification. In
short: ``ScheduleOptimizer(..., seed=DEFAULT_SEED)`` owning a private
``random.Random``; ``seed=None`` means "randomize"; the seed is threaded
through ``logic.optimized_reschedule_all`` and
``SchedulingWorkflow.reschedule``; the search is governed by counted work
rather than by elapsed seconds; and ``summary`` gains ``"seed"`` (the seed
actually used, including one that was drawn) and ``"deterministic"`` (False if
a wall-clock cap fired and cut the search short). ``"deterministic"`` is the
one part of that API the register does not ask for; exactly two tests depend on
it (``test_summary_reports_seed_and_determinism`` and
``test_machine_speed_does_not_change_the_answer``), so dropping it from the
specification costs one test and one precondition check, not the module.

Known gap
---------
CP-SAT is not covered. ``cpsat_scheduler`` sets ``num_workers = 1`` but no
``random_seed``, and is bounded by ``max_time_in_seconds`` — plus
``_cpsat_optimize`` kills the subprocess on a wall-clock deadline, so a slow
machine silently drops the CP-SAT result entirely. All of that is
non-deterministic and all of it is addressed in the plan, but a test for it
would have to run a native solver in a subprocess under a time limit, which is
the wrong shape for a regression suite. Add one once the seeding lands and the
budget is deterministic.
"""
import copy
import hashlib
import random
import time as _real_time

import pytest

from _support.dataset_gen import make_preset

# ── The fast lane's instance and budgets ────────────────────────────────────
FAST_N_CLASSES = 16

# Explicit iteration budgets and deliberately unreachable time limits: at this
# scale the LNS loop exits on `lns_no_improve_limit`, never on the clock, so
# every test except `test_machine_speed_does_not_change_the_answer` isolates
# "the seed is wired" from "the budget is deterministic". That one test binds
# the clock on purpose, with its own configuration.
FAST = dict(
    multi_start_runs=2,
    lns_iterations=15,
    lns_no_improve_limit=15,
    lns_time_limit=1e9,
    multi_start_time_limit=1e9,
    parallel_workers=-1,   # the pool path has its own test below
)

SEED = 20260826


# ── Helpers ─────────────────────────────────────────────────────────────────
def _signature(placed):
    """The optimizer's proposal as a comparable, printable value.

    Compares the placement *sequence*, not a set: the order of ``placed_list``
    is itself an output of the run (pinned, then locked, then protected, then
    the winning multi-start run's difficulty ordering), so two reproducible
    runs must agree on it too.
    """
    from scheduler_app.core.models import cls_key

    return tuple(
        (cls.get("class_code") or cls.get("name", ""), cls_key(cls),
         day, slot, room or "")
        for cls, day, slot, room in placed)


def _diff(sig_a, sig_b, label_a="run 1", label_b="run 2"):
    """A short, readable description of where two proposals diverge."""
    if len(sig_a) != len(sig_b):
        return (f"{label_a} placed {len(sig_a)} classes, "
                f"{label_b} placed {len(sig_b)}")
    lines = []
    for i, (a, b) in enumerate(zip(sig_a, sig_b)):
        if a == b:
            continue
        lines.append(f"  [{i}] {label_a}={a}  {label_b}={b}")
        if len(lines) >= 8:
            lines.append("  ... (truncated)")
            break
    return "\n".join(lines) or "(sequences are equal)"


def _first_disagreeing_pair(signatures):
    """Return two signatures from *signatures* that differ, or ``None``."""
    for i, a in enumerate(signatures):
        for b in signatures[i + 1:]:
            if a != b:
                return a, b
    return None


def _rng_digest():
    """A compact stand-in for ``random.getstate()``.

    The Mersenne Twister state is a 625-element tuple; comparing it directly
    makes pytest dump the whole thing into the failure report.
    """
    return hashlib.sha256(repr(random.getstate()).encode()).hexdigest()[:16]


def _assert_not_degenerate(signature, state, what):
    """Refuse a run that placed almost nothing.

    Every reproducibility assertion in this module is an *equality* between two
    runs, and two empty proposals are equal. Phase 0 shipped three tests that
    passed against an optimizer stubbed out to return nothing; verified for
    this module by the same experiment (9 of 11 fast tests survived a gutted
    ``optimize()``). The floor is half the instance, which is far below every
    measured result — 16/16 at the fast scale, 21/25 through the workflow at
    ``small`` — so it can only fire on a genuinely degenerate run.
    """
    n_classes = len(state["classes"])
    floor = max(1, n_classes // 2)
    assert len(signature) >= floor, (
        f"{what} placed only {len(signature)} of {n_classes} classes, so the "
        "reproducibility comparison in this test would be vacuous — two runs "
        "that place nothing always agree. Fix the optimizer (or this "
        "instance) before reading anything into the determinism assertions.")


def _run(state, **kwargs):
    """One production ``ScheduleOptimizer.optimize()`` on a private copy.

    Returns ``(signature, total_score, summary)``. ``state`` is deep-copied so
    that repeated calls really do see identical input, including the
    ``class_uid`` values that ``cls_key`` keys every internal map on — calling
    ``make_preset`` again would mint fresh UUIDs and stop being "the same
    input", while a real save/reload preserves them.
    """
    from scheduler_app.core.schedule_optimizer import ScheduleOptimizer

    optimizer = ScheduleOptimizer(copy.deepcopy(state), **kwargs)
    placed, _unplaced, _changes, summary = optimizer.optimize()
    signature = _signature(placed)
    _assert_not_degenerate(signature, state, "ScheduleOptimizer.optimize()")
    return signature, summary["after"]["total"], summary


def _workflow_reschedule(state, **kwargs):
    """Run the real UI-facing reschedule entry point on a private copy."""
    from scheduler_app.core.workflow import SchedulingWorkflow

    work_state = copy.deepcopy(state)
    workflow = SchedulingWorkflow(work_state, lambda: {})
    result = workflow.reschedule({}, use_cpsat=False, **kwargs)
    signature = _signature(result.placed)
    _assert_not_degenerate(signature, state, "SchedulingWorkflow.reschedule()")
    return signature, result.summary


class _FakeClock:
    """A deterministic stand-in for the ``time`` module.

    ``time.time()`` advances by a fixed *step* on every call and by nothing
    else, so "how fast this machine is" becomes a test parameter instead of a
    property of the CI runner. Everything other than ``time`` is delegated to
    the real module.
    """

    def __init__(self, step):
        self._now = 1000.0
        self._step = step
        self.calls = 0

    def time(self):
        self.calls += 1
        self._now += self._step
        return self._now

    def __getattr__(self, name):          # pragma: no cover - delegation
        return getattr(_real_time, name)


def _lns_work(summary):
    """How many LNS destroy-repair iterations the run actually executed.

    ``summary["lns_strategy_stats"]`` already exists today (it is
    ``AdaptiveStrategySelector.get_stats()``), so this needs no new API. One
    strategy is selected per iteration, so the ``uses`` column sums to the
    iteration count of the last multi-start run.
    """
    return sum(s["uses"] for s in summary.get("lns_strategy_stats", []))


# ── Fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module", autouse=True)
def _isolate_global_rng_module():
    """Restore the process-global RNG around the whole module.

    The function-scoped guard below is not sufficient on its own: pytest builds
    module-scoped fixtures *before* function-scoped autouse fixtures (verified),
    so the two ``optimize()`` calls inside ``seeded_pair`` run outside the
    per-test window. Today those calls consume the global stream; this wrapper
    is what keeps that from reaching ``test_scheduler_invariants.py``, which
    pytest runs after this module and which also drives the optimizer.
    """
    saved = random.getstate()
    try:
        yield
    finally:
        random.setstate(saved)


@pytest.fixture(autouse=True)
def _isolate_global_rng():
    """Leave the process-global RNG exactly as each test found it.

    Several tests here seed or drain ``random`` on purpose; without this, one
    test's leftovers would silently steer the next one.
    """
    saved = random.getstate()
    try:
        yield
    finally:
        random.setstate(saved)


@pytest.fixture(scope="module")
def state16():
    """A 16-class instance, built once and shared read-only by every test."""
    return make_preset("small", seed=42, n_classes=FAST_N_CLASSES)


@pytest.fixture(scope="module")
def seeded_pair(state16):
    """Two ``optimize()`` runs over the same input with the same explicit seed.

    Module-scoped because three assertions below read it and each run costs
    real optimizer time.
    """
    return [_run(state16, seed=SEED, **FAST) for _ in range(2)]


# ===========================================================================
# 1. THE GLOBAL RNG MUST NOT BE INVOLVED AT ALL
# ===========================================================================
@pytest.mark.engine
def test_optimizer_does_not_consume_the_global_rng(state16):
    """Pins ST-SCHED-013 — the root cause, stated directly.

    A failure means generating a timetable silently advances the process-wide
    ``random`` stream, so the result depends on whatever else in the app drew a
    random number first: pressing Generate twice in one session cannot give the
    same timetable, and no seed a user or a support engineer supplies can make
    it.

    This is the one assertion here that needs no new API, so it is the cleanest
    fail-now signal for the defect.
    """
    random.seed(4242)
    before = _rng_digest()

    _run(state16, **FAST)          # also asserts the run was not degenerate

    assert _rng_digest() == before, (
        "optimize() consumed the process-global random stream. Every RNG draw "
        "in the optimizer, in the LNS strategy selector and in the destroy "
        "strategies must come from a Random instance owned by the optimizer.")


@pytest.mark.engine
def test_unrelated_global_rng_use_does_not_perturb_the_run(state16):
    """Pins ST-SCHED-013 — reproducibility has to survive a noisy process.

    A failure means a seeded reschedule still depends on what else happened in
    the app first, so the user's "same seed, same timetable" guarantee breaks
    the moment any other feature draws a random number.
    """
    first = _run(state16, seed=SEED, **FAST)

    # Anything at all could have consumed the global stream between two user
    # actions; 97 draws is an arbitrary but deterministic stand-in for that.
    for _ in range(97):
        random.random()

    second = _run(state16, seed=SEED, **FAST)

    assert first[0] == second[0], (
        "an identically seeded run changed after unrelated global RNG use:\n"
        + _diff(first[0], second[0]))
    assert first[1] == second[1], (
        f"score changed too: {first[1]} != {second[1]}")


# ===========================================================================
# 2. SAME INPUT + SAME SEED → SAME OUTPUT
# ===========================================================================
@pytest.mark.engine
def test_same_seed_gives_identical_placements(seeded_pair):
    """Pins ST-SCHED-013 — the completion criterion for the finding.

    A failure means a user who regenerates the timetable without changing
    anything gets a different one, and a support engineer handed the same file
    and the same seed cannot reproduce what the user is looking at.
    """
    (sig_a, _score_a, _sum_a), (sig_b, _score_b, _sum_b) = seeded_pair

    assert sig_a == sig_b, (
        f"two runs over identical input with seed={SEED} produced different "
        "placements:\n" + _diff(sig_a, sig_b))


@pytest.mark.engine
def test_same_seed_gives_identical_score(seeded_pair):
    """Pins ST-SCHED-013 — the quality half of the finding.

    The audit measured a ~41 % best-vs-worst quality spread over 5 identical
    runs, and this module re-measured 27.18 vs 65.88 at 80 classes. A failure
    means the user's timetable quality is a lottery: the same input can produce
    a markedly worse schedule with no way to ask for the good one back.

    The 16-class instance was chosen partly so that this assertion means
    something: eight seeds produce five distinct scores there, against two (and
    seven ties on a perfect ``-0.0``) at the 12 classes this module first used.
    """
    (_sig_a, score_a, _sum_a), (_sig_b, score_b, _sum_b) = seeded_pair

    assert score_a == score_b, (
        f"identical input with seed={SEED} scored {score_a} then {score_b} "
        f"(delta {abs(score_a - score_b)})")


@pytest.mark.engine
def test_default_construction_is_reproducible(state16):
    """Pins ST-SCHED-013 — "default to a fixed seed", from the register.

    A user never types a seed. If reproducibility only arrives when a caller
    passes one, the app stays a lottery for everybody who actually uses it — so
    the no-argument construction has to be the deterministic one, and the
    summary has to say which seed that was.

    The ``summary['seed']`` assertion is the deterministic fail-now signal
    (today the key is absent). The placement check that follows is supporting
    evidence: two *unseeded* runs of this instance agree by chance rarely —
    eight seeds gave eight distinct placements — but it is a probabilistic
    argument, not a proof, which is why it is not the primary signal.
    """
    runs = [_run(state16, **FAST) for _ in range(2)]

    seeds = [summary.get("seed") for _sig, _score, summary in runs]
    assert all(isinstance(s, int) for s in seeds), (
        "summary['seed'] must report the seed the run actually used, as an "
        f"int, so a support engineer can replay it — got {seeds!r}")
    assert len(set(seeds)) == 1, (
        f"default construction used {len(set(seeds))} different seeds "
        f"({seeds!r}); with no seed argument the optimizer must fall back to a "
        "single fixed default")

    signatures = [sig for sig, _score, _summary in runs]
    disagreement = _first_disagreeing_pair(signatures)
    assert disagreement is None, (
        "default-constructed runs over identical input disagreed:\n"
        + _diff(*disagreement))


# ===========================================================================
# 3. THE SEED IS WIRED, NOT IGNORED
# ===========================================================================
# Well-separated values; the point is only that they are different seeds, not
# that any particular one is special. Five is enough: all eight seeds measured
# on this instance gave eight distinct placements, so the chance of a false red
# here is far below the chance of the suite being run at all.
SWEEP_SEEDS = (1, 7, 101, 4242, 65535)


@pytest.mark.engine
def test_different_seeds_explore_different_solutions(state16):
    """Pins ST-SCHED-013 — proves the seed is wired rather than accepted and
    dropped.

    Without this, an implementation that takes a ``seed`` argument, ignores it,
    and returns one canned answer would satisfy every other test in this module
    while quietly disabling the multi-start diversity the optimizer depends on
    for quality — trading a lottery for a permanently mediocre timetable. It is
    also the test that refuses an optimizer stubbed out to place nothing.

    Statistical note: measured on the fixed code, these 8 seeds produced 8
    distinct placements and 5 distinct scores at 16 classes. (At the 12 classes
    this module first used it was 3 distinct placements out of 8 — close enough
    to a false green to be worth the extra 0.3 s per run.) If this ever goes
    red on its own, re-measure the spread before touching the assertion.
    """
    signatures = [_run(state16, seed=s, **FAST)[0] for s in SWEEP_SEEDS]

    assert len(set(signatures)) >= 2, (
        f"all {len(SWEEP_SEEDS)} seeds produced the identical proposal — the "
        "seed is not reaching the LNS strategy selector, the destroy "
        "strategies, or the multi-start ordering perturbation")


@pytest.mark.engine
def test_seed_none_randomizes_and_reports_the_drawn_seed(state16):
    """Pins ST-SCHED-013 — "expose randomize explicitly", from the register.

    Deterministic-by-default is only usable if a user who wants a *different*
    attempt can ask for one, and that attempt is worthless for support unless
    the app can afterwards say which seed produced it. A failure means either
    that "try again" gives the same timetable forever, or that a user cannot
    hand back the number that reproduces the schedule they liked.

    Checks the drawn seed rather than the placements on purpose: comparing
    outputs would inherit this instance's small chance of two random draws
    coinciding, and this assertion has no such failure mode.
    """
    _sig_a, _score_a, summary_a = _run(state16, seed=None, **FAST)
    _sig_b, _score_b, summary_b = _run(state16, seed=None, **FAST)

    assert isinstance(summary_a.get("seed"), int), (
        "a randomized run must still report the seed it drew, so the result "
        f"can be replayed — got {summary_a.get('seed')!r}")
    assert summary_a["seed"] != summary_b["seed"], (
        f"seed=None drew the same seed twice ({summary_a['seed']}); "
        "'randomize' is not actually randomizing")


# ===========================================================================
# 4. THE BUDGET HAS TO BE DETERMINISTIC TOO
# ===========================================================================
# One simulated-slow and one simulated-fast machine. The fake clock advances
# only when the optimizer asks the time, so these are exact, not timings.
# Measured against a scratch tree carrying only the *seed* half of the fix:
# 25 LNS iterations on the fast clock against 14 / 19 / 9 on the slow one, with
# a different placement and a different score every time.
CLOCK_STEPS = [
    pytest.param(0.05, 2.0, id="fast-vs-slow"),
    pytest.param(0.05, 1.5, id="fast-vs-slowish"),
]


@pytest.mark.engine
@pytest.mark.parametrize("fast_step, slow_step", CLOCK_STEPS)
def test_machine_speed_does_not_change_the_answer(
        state16, monkeypatch, fast_step, slow_step):
    """Pins ST-SCHED-013 — the half a seed cannot fix.

    Measured at 80 classes: two runs with the *same* seed, both hitting
    ``multi_start_time_limit=120.0``, executed ``[95, 58, 66, 70, 66]`` and
    ``[74, 106, 44, 51, 33]`` LNS iterations and scored 21.80 and 23.34. How
    much work the optimizer does is a function of how fast the machine was, so
    a laptop under load and an idle one produce different timetables from the
    same file, and neither is reproducible on the other.

    A regression suite cannot make the CI runner slower on demand, so this
    replaces the machine with a counter: ``time.time()`` inside
    ``schedule_optimizer`` advances a fixed amount per call, and the same run
    is executed twice with two different amounts. Everything else — instance,
    seed, iteration budget, time *limits* — is identical, so any difference in
    the answer is by construction attributable to elapsed seconds.

    The work-count assertion fires first and is the load-bearing one: an
    implementation may legitimately reach the same placement after fewer
    iterations, but it must never *do* a different amount of work because the
    machine was slower. It reads ``summary['lns_strategy_stats']``, which
    exists today.

    This test replaces an earlier one that held the clock fixed and varied the
    *value* of ``lns_time_limit`` / ``multi_start_time_limit``. That variant
    was verified to pass against a tree carrying only the seed half of the fix,
    i.e. it could not see this defect at all.
    """
    from scheduler_app.core import schedule_optimizer as so

    # lns_time_limit binds under the slow clock (15 iterations x 2.0 s > 30 s)
    # while multi_start_time_limit stays unreachable, so the plan's emergency
    # cap cannot fire and `deterministic` must stay True on both sides.
    budget = dict(multi_start_runs=2, lns_iterations=25,
                  lns_no_improve_limit=25, lns_time_limit=30.0,
                  multi_start_time_limit=1e9, parallel_workers=-1)

    results = []
    for step in (fast_step, slow_step):
        clock = _FakeClock(step)
        monkeypatch.setattr(so, "time", clock)
        try:
            results.append(_run(state16, seed=SEED, **budget))
        finally:
            monkeypatch.undo()

    (sig_a, score_a, sum_a), (sig_b, score_b, sum_b) = results

    assert sum_a.get("deterministic") is True, (
        "the fast-clock run reported itself non-deterministic; a wall-clock "
        "cap fired even though multi_start_time_limit was 1e9 — the test's "
        f"premise is broken, not just the code. Got {sum_a.get('deterministic')!r}")
    assert sum_b.get("deterministic") is True, (
        "the slow-clock run reported itself non-deterministic — the emergency "
        "cap fired when it should have been unreachable")
    assert _lns_work(sum_a) == _lns_work(sum_b), (
        f"the optimizer executed {_lns_work(sum_a)} LNS iterations on a "
        f"simulated fast machine and {_lns_work(sum_b)} on a slow one. How "
        "much work the search does must be a function of the configured "
        "budget, never of elapsed seconds.")
    assert sig_a == sig_b, (
        "a simulated slow machine produced a different timetable from "
        "identical input and an identical seed:\n"
        + _diff(sig_a, sig_b, "fast clock", "slow clock"))
    assert score_a == score_b, (
        f"quality depends on machine speed: {score_a} then {score_b}")


@pytest.mark.engine
def test_summary_reports_seed_and_determinism(seeded_pair):
    """Pins ST-SCHED-013 — the support-facing contract.

    The finding's user impact is "no reproducibility for support or
    comparison". That is only fixed if the app can tell the user two things
    after a run: which seed produced this timetable, and whether the run was
    reproducible at all or was cut short by a time cap. A failure means a
    support engineer is still guessing.

    ``summary['deterministic']`` is a specification this module proposes, not
    something the register asks for; this test and the fake-clock one above are
    the only two that depend on it.
    """
    for i, (_sig, _score, summary) in enumerate(seeded_pair):
        assert summary.get("seed") == SEED, (
            f"run {i}: summary['seed'] must echo the effective seed; got "
            f"{summary.get('seed')!r}")
        assert summary.get("deterministic") is True, (
            f"run {i}: summary['deterministic'] must be True for a run whose "
            "budgets were never exhausted by the clock; got "
            f"{summary.get('deterministic')!r}")


# ===========================================================================
# 5. THE PARALLEL SCORING POOL
# ===========================================================================
@pytest.mark.engine
def test_parallel_scoring_pool_is_reproducible(state16):
    """Pins ST-SCHED-013 on the path users actually run.

    ``optimized_reschedule_all`` defaults to ``parallel_workers=0`` (auto), so
    every reschedule from the UI evaluates candidates in worker processes. If
    the answer depended on which worker replied first, seeding the main process
    would fix nothing. A failure means reproducibility holds only when the pool
    is switched off — that is, never, for a real user.
    """
    parallel = dict(FAST, parallel_workers=0)

    sig_a, score_a, _sum_a = _run(state16, seed=SEED, **parallel)
    sig_b, score_b, _sum_b = _run(state16, seed=SEED, **parallel)

    assert sig_a == sig_b, (
        "two identically seeded runs with the process pool enabled "
        "disagreed:\n" + _diff(sig_a, sig_b))
    assert score_a == score_b, f"score moved: {score_a} != {score_b}"


# ===========================================================================
# 6. THE PRODUCTION PATH (SchedulingWorkflow → logic → ScheduleOptimizer)
# ===========================================================================
@pytest.mark.engine
def test_workflow_reschedule_accepts_and_reports_a_seed():
    """Pins ST-SCHED-013 — the seed has to be reachable from the UI's API.

    ``SchedulingWorkflow.reschedule`` is what the Generate button calls. A
    failure means the seed exists somewhere inside the optimizer but nothing
    the application actually invokes can set it, so the user-visible behaviour
    is unchanged.

    Uses the 5-class ``tiny`` preset to stay inside the fast lane. ``tiny`` is
    already stable run-to-run (3/3 identical unseeded), so the equality check
    here is weak on its own — the load-bearing assertion is that the parameter
    is plumbed end to end and echoed in the summary. Reproducibility of this
    path at a scale where it is *not* already stable is the next test's job.
    """
    state = make_preset("tiny", seed=42)

    sig_a, summary_a = _workflow_reschedule(state, seed=SEED)
    sig_b, summary_b = _workflow_reschedule(state, seed=SEED)

    assert summary_a.get("seed") == SEED, (
        "SchedulingWorkflow.reschedule must thread the seed through "
        "logic.optimized_reschedule_all into ScheduleOptimizer and report it; "
        f"got {summary_a.get('seed')!r}")
    assert summary_b.get("seed") == SEED
    assert sig_a == sig_b, (
        "the production reschedule path is not reproducible even on `tiny`:\n"
        + _diff(sig_a, sig_b))


@pytest.mark.engine
@pytest.mark.slow
def test_workflow_reschedule_is_reproducible_at_small_scale():
    """Pins ST-SCHED-013 at the shipped defaults.

    The only test here that runs the optimizer exactly as the app does:
    ``multi_start_runs=5``, ``multi_start_time_limit=120.0``,
    ``lns_time_limit=30.0``, ``parallel_workers=0`` (auto), 25 classes. A
    failure means that whatever the unit-level tests above prove, the
    configuration users actually get is still a lottery.

    25 classes keeps this inside a tolerable slow-lane runtime (measured 22 s
    per reschedule, 44 s for the test). The same experiment at 80 classes was
    run by hand against a tree with the fix applied and also came out
    reproducible — 20.94 twice, 76/80 placed — but it took 89 s and 96 s
    against a 120 s ``multi_start_time_limit``. That is only ~25 % of headroom,
    so a machine a third slower would trip the emergency cap and lose
    reproducibility; it is deliberately not asserted here, because the test
    would then be measuring the CI runner rather than the code. See the
    hand-off notes on ``multi_start_time_limit``.
    """
    state = make_preset("small", seed=42)

    sig_a, _summary_a = _workflow_reschedule(state, seed=SEED)
    sig_b, _summary_b = _workflow_reschedule(state, seed=SEED)

    assert sig_a == sig_b, (
        "the shipped reschedule configuration produced two different "
        "timetables from identical input and an identical seed:\n"
        + _diff(sig_a, sig_b))


@pytest.mark.engine
@pytest.mark.slow
def test_department_scale_run_is_reproducible():
    """Pins ST-SCHED-013 where it actually bites — 80 classes.

    This is the measurement that started the module: with the global RNG seeded
    identically, two 80-class runs at production defaults scored 27.18 and
    65.88 and both burned the full 120 s budget. From the user's side, the same
    department timetable regenerated twice differs by more than a factor of two
    in quality, with no way to ask for the better one back.

    Budgets are pinned explicitly (2 starts x 50 LNS iterations, no wall-clock
    pressure) rather than left at the defaults, so that this test measures
    reproducibility and not the machine's speed — under
    ``multi_start_time_limit=120.0`` the result is *defined* by how much work
    the clock allowed, which is the very thing under test.

    2 x 50 is chosen for test runtime (~31 s per run), NOT as a production
    recommendation: measured on this instance it scores 38.6 against ~20 for
    the shipped configuration. The deterministic budget that actually matches
    today's work is 5 starts x 70 iterations — it scored 18.22 twice (better
    than the 19.84/20.64 the clock-bound default produced) in 148 s and 163 s.
    """
    state = make_preset("normal", seed=42)
    budget = dict(multi_start_runs=2, lns_iterations=50,
                  lns_no_improve_limit=50, lns_time_limit=1e9,
                  multi_start_time_limit=1e9, parallel_workers=0)

    sig_a, score_a, _summary_a = _run(state, seed=SEED, **budget)
    sig_b, score_b, _summary_b = _run(state, seed=SEED, **budget)

    assert score_a == score_b, (
        f"80-class quality is still a lottery: {score_a} then {score_b}")
    assert sig_a == sig_b, (
        "two identically seeded 80-class runs produced different "
        "timetables:\n" + _diff(sig_a, sig_b))
