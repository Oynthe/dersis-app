"""Reschedule wrapper overhead — ST-PERF-007.

Why this module exists
----------------------
``SchedulingWorkflow.reschedule`` runs a second pass —
``negotiate_after_optimization`` — whenever the optimizer leaves *any* class
unplaced. Most realistic timetables leave something unplaced, so the pass is
the common case, not the exception. The register calls it "+10 s of wrapper
overhead at 25 classes". **That number is wrong, and the correction is the
first thing this module records**, because a test calibrated against a
phantom 10 s would be a test that can never fail.

What was actually measured
--------------------------
All figures from ``.venv-audit`` on the audit machine against
``fix/phase-2-performance``, ``small`` preset (25 classes) at production
defaults unless stated otherwise.

1. **The wrapper is not where the time goes.** Timing
   ``optimized_reschedule_all`` *from inside* the ``reschedule()`` call — so
   machine load cancels out of the ratio — splits the call into solver and
   everything-else:

   ===================================  =========  =========  ============
   run                                  wall       solver     non-solver
   ===================================  =========  =========  ============
   ``seed=1234``, 4 unplaced            37.72 s    37.71 s    **0.01 s**
   ``seed=20260826``, 4 unplaced        19.41 s    19.40 s    **6.2 ms**
   ``seed=20260826``, 4 unplaced (v)    35.23 s    35.21 s    **15.9 ms**
   ===================================  =========  =========  ============

   The third row is an independent re-measurement of the second, at the same
   parameters on the same machine at a different time: 0.045 % rather than
   0.032 %, and the solve itself nearly doubled. Both the absolute non-solver
   cost and the solve wander with machine load — which is exactly why no
   assertion in this module is a wall-clock threshold. What does not wander
   is the shape: the wrapper is a fraction of a percent of the call.
   Negotiation / solve is of order **1e-4**.

2. **Where the register's +10.1 s came from.** ``06-performance-audit.md``
   §2.2 compares two runs of ``stress-test/tests/_smoke_reschedule_api.py``:
   ``optimized_reschedule_all(..., multi_start_runs=2,
   multi_start_time_limit=8.0, parallel_workers=-1)`` at 7.6 s against
   ``workflow.reschedule(...)`` at production defaults —
   ``multi_start_runs=5``, ``multi_start_time_limit=120.0``,
   ``parallel_workers=0`` — at 17.7 s. The two ran *different solver
   budgets*; the delta is three extra restarts plus the worker pool, not the
   wrapper. Re-running exactly that comparison (``small``, density 0.3, the
   default seed) reproduces the artifact — 11.29 s capped vs 36.88 s
   production, "+25.6 s of wrapper" — while the in-run split of that very
   same production call puts the wrapper's own share at **7.8 ms, 0.02 % of
   wall**. The audit's own evidence already contained the refutation:
   ``scheduler_benchmark.csv`` row ``infeasible_oversub`` records
   ``wall_seconds`` 33.5868 against ``opt_total_time`` 33.4553 — 0.13 s of
   wrapper with **106** classes unplaced.

3. **The pass is still worth fixing, for two reasons that are real.**

   a. *It analyses every unplaced class exactly twice.*
      ``ConstraintNegotiator.negotiate_after_optimization`` builds
      ``class_reports`` by calling ``analyzer.analyze_class(cls)`` per
      unplaced class, then immediately rebuilds the identical list as
      ``all_analyses`` for the diagnostic summary. Measured
      ``InfeasibilityAnalyzer.analyze_class`` call counts: 8 for 4 unplaced,
      38 for 19, 230 for 115, 568 for 284 — exactly 2n every time. Half of
      the pass is pure waste.

   b. *It is unconditional, and it grows fast.* Cost of the negotiation
      pass alone, on ``_support.dataset_gen`` presets at density 0.3
      (re-measured independently):

      ==============  ===========  ==========  ==============
      preset          classes      unplaced    negotiation
      ==============  ===========  ==========  ==============
      ``normal``      80           5           14 ms
      ``large``       250          21          **727 ms**
      ``very_large``  600          101         **5 794 ms**
      ==============  ===========  ==========  ==============

      Nearly six seconds of blocking, uncancellable UI-thread work at 600
      classes — paid on every reschedule whether or not anybody looks. Note
      the cost tracks the *search space per class*, not the unplaced count:
      a flat 250-class state with 138 unplaced costs only 116 ms, while the
      ``large`` preset with 21 unplaced costs 727 ms. ST-UI-015 /
      ST-SCHED-014 want a richer "why unplaced?" panel built on exactly this
      data, so the eager price only goes up.

The policy this module asserts
------------------------------
**Lazy, memoised, pinned to the reschedule-time state, one analysis per
unplaced class.** Concretely, ``RescheduleResult.negotiation_result``:

* is **not** computed during ``reschedule()``;
* is computed on first attribute read, doing exactly one ``analyze_class``
  and one ``suggest_for_class`` per unplaced class;
* is cached, so the three reads the UI already performs
  (``scheduler_app/ui/app.py`` lines 2842, 2881, 2882) cost one computation;
* returns the *same dict whenever it is read* — in particular a read after
  ``apply_reschedule()`` returns what a read before it would have returned;
* stays ``None``, with zero work done, when nothing is unplaced.

Why lazy and not the alternatives. **Bounded** ("only the first N unplaced")
truncates the very answer the user asked for, and buys nothing on the
instances that are actually slow — 21 unplaced at 250 classes is already
under any N worth setting, yet costs 727 ms. **Opt-in via a parameter**
moves the decision to the caller, and the only caller is the dialog that
always wants it, so the default would either be ``True`` (no saving) or
``False`` (the negotiation tab silently disappears). **Lazy** is the only one
of the three that pays exactly zero when nobody looks and full price when
somebody does, and it is the shape the "why unplaced?" panel needs anyway.

The trap laziness sets, and why the last bullet above is not optional
--------------------------------------------------------------------
``negotiate_after_optimization`` analyses ``self.state``, and the reschedule
proposal is **not** committed until ``apply_reschedule()``. ``ui/app.py``
reads ``result.negotiation_result`` on *both* sides of that call — line 2842
feeds ``BulkResultsDialog`` before, lines 2881-2882 feed the warning log
after. A lazy property naively evaluated against the live state would
therefore hand the dialog and the warning log **different answers for the
same reschedule**. Measured on this module's own fixture, recomputing after
the commit flips every report: ``Ders 19`` goes ``ok`` / 6 valid slots →
``infeasible`` / 0, ``Ders 10`` ``constrained`` / 2 → ``infeasible`` / 0, and
the diagnostic summary changes with them.
``test_negotiation_result_survives_apply_unchanged`` pins that, and asserts
the recompute genuinely differs so the pin cannot pass vacuously.

Deliberately **not** asserted here: *which* occupancy the report should
describe. Today's eager pass answers "why is this class unplaced?" against
the timetable the user is about to replace, not the one being proposed — the
probe above shows those are materially different answers, and the post-apply
one looks more useful. Changing that is a semantic change belonging to
ST-SCHED-014 / ST-UI-015, not to a performance fix, so this module pins
today's answer exactly. When ST-SCHED-014 moves the evaluation point, one
assertion here changes, deliberately and visibly.

What this module does NOT prove — read before calling ST-PERF-007 closed
------------------------------------------------------------------------
**Deferring the pass in ``workflow.py`` alone saves the shipped app exactly
zero.** Every test here stops at the ``SchedulingWorkflow`` boundary, and at
that boundary laziness is worth the full 5.8 s. But the only production
caller reads the value immediately anyway: ``ui/app.py:2842`` passes
``result.negotiation_result`` **by value** into ``BulkResultsDialog(...)``,
and ``dialogs.py:3941-3983`` builds the negotiation tab inline in that
dialog's ``__init__`` — the truthiness test at 3941 and the
``.get("class_reports")`` at 3942 both run before the dialog is ever shown.
So a lazy property whose first read is that constructor argument moves the
work a few lines later in the same blocking stretch and changes nothing the
user can feel.

The workflow-level deferral is a **precondition** for the saving, not the
saving. Collecting it requires the second half: ``ui/app.py:2842`` must hand
the dialog a deferred source rather than a materialised dict, and
``dialogs.py`` must build the negotiation tab when that tab is first shown.
The de-duplication in §3a is the part that pays off unconditionally today —
it halves the pass wherever it runs.

Nothing in this file will go red if only ``workflow.py`` is changed. Ten
green tests and no user-visible improvement is the failure mode to watch
for, so it is written down here rather than left to be discovered.

How the tests avoid being flaky
-------------------------------
No assertion in the fast lane touches the clock. The property under test is
expressed as **call counts** on the two expensive per-class steps of the
pass, ``InfeasibilityAnalyzer.analyze_class`` and
``RelaxationSuggester.suggest_for_class``: "zero during ``reschedule()``",
"exactly one per unplaced class through the first read", "zero on every
later read". Those are exact integers on a shared CI runner under load.

The fast lane also does not run the optimizer. ``optimized_reschedule_all``
is replaced by a stub returning a *real* partial schedule built by a
deterministic first-fit through the production ``ConstraintValidator``
(measured: 0.5 ms for 20 classes). That is the right isolation for a finding
about wrapper overhead — the wrapper and the negotiation pass both run for
real; only the solver, which ST-PERF-007 is not about, is stubbed. The two
``slow`` tests run the real optimizer: one at production defaults on
``small`` to hold the corrected measurement in place, one at a capped budget
for the seeded end-to-end determinism check.

Non-vacuity
-----------
The fixture asserts it leaves classes unplaced (20 classes → 8 placed, 10
unplaced, reports spanning all three statuses ``ok`` / ``constrained`` /
``infeasible``); a fixture that placed everything would make every count
assertion here trivially 0. The apply-stability test asserts the post-apply
recompute differs from the reference. The determinism tests assert the
signature is not degenerate before comparing it with itself. The ST-DATA-011
guard asserts that a suggestion touching a constraint list was actually
produced, and drives the negotiator directly rather than through
``RescheduleResult`` — through it, the fix's snapshot would absorb every leak
and the guard would pass with the ``finally`` deleted (measured).

Each of these was checked by breaking the code on purpose and confirming the
test goes red: the solver stubbed to return nothing, the negotiation pass
replaced by a canned dict, the ST-DATA-011 ``finally`` removed, a *naive*
lazy property with no snapshot, and the full proposed fix (under which all
ten pass).
"""

import pytest

from scheduler_app.core import constraint_negotiator as cn_mod
from scheduler_app.core import workflow as wf_mod
from scheduler_app.core.constraint_validator import ConstraintValidator
from scheduler_app.core.models import needs_physical_room
from scheduler_app.core.workflow import SchedulingWorkflow
from scheduler_app.logic import negotiate_after_optimization

from _support.dataset_gen import make_state as build_state

SEED = 20260826

#: ``multi_start_time_limit`` for the two ``slow`` tests, replacing the shipped
#: 120 s. It is NOT a performance assertion — no test compares anything to it.
#: Phase 1 (ST-SCHED-013) made the solver's *work* machine-independent by
#: bounding LNS with an iteration count, but ``multi_start_time_limit``
#: survives as a wall-clock emergency cap, and crossing it sets
#: ``_clock_capped`` — which truncates the restarts, flips
#: ``summary['deterministic']`` to False and changes what the run actually
#: exercised. Measured under pytest on the audit machine, the production test
#: below cost 25 s of the shipped 120 s on an idle box and **88.7 s** with
#: three other pytest processes running — 1.35x of headroom under a load that
#: a shared CI runner reaches routinely, at which point the run silently
#: becomes a different, capped, non-deterministic solve. With the reduced
#: restart count below the same test costs ~8.5 s, so 600 s is ~70x the
#: measured cost and ~7x the worst contended figure: far enough that the cap
#: can never fire in practice, while still bounding a genuinely runaway solver
#: rather than hanging CI forever.
EMERGENCY_CAP = 600.0

#: Every list-valued constraint the three ST-DATA-011 estimators mutate while
#: simulating a relaxation. They must all come back untouched.
CONSTRAINT_FIELDS = ("allowed_days", "excluded_days",
                     "allowed_times", "excluded_times",
                     "required_classrooms", "excluded_classrooms")


# ── Helpers ─────────────────────────────────────────────────────────────────

def _first_fit(state):
    """A deterministic, solver-free stand-in for an optimizer result.

    Walks (day, slot, room) in grid order for each non-pinned class and takes
    the first cell the *production* ``ConstraintValidator`` accepts. Returns
    ``(placed, unplaced)`` in exactly the shapes ``optimized_reschedule_all``
    returns them. Does not touch ``state`` — the placements are a proposal,
    which is precisely the condition ``_reschedule_impl`` sees them in.
    """
    validator = ConstraintValidator(state)
    placed, unplaced = [], []
    for cls in state["classes"]:
        if cls["pinned"]:
            continue
        rooms = state["classrooms"] if needs_physical_room(cls) else [None]
        hit = None
        for day in state["days"]:
            for slot in state["slots"]:
                for room in rooms:
                    if validator.check_placement(cls, day, slot, room):
                        hit = (day, slot, room)
                        break
                if hit:
                    break
            if hit:
                break
        if hit is None:
            unplaced.append((cls, "no valid slot"))
        else:
            validator.add_placement(cls, hit[0], hit[1], hit[2])
            placed.append((cls, hit[0], hit[1], hit[2]))
    return placed, unplaced


def _constraint_snapshot(state):
    """Every constraint list on every class, by value — the ST-DATA-011 witness."""
    return [{f: list(c.get(f) or []) for f in CONSTRAINT_FIELDS}
            for c in state["classes"]]


def _report_signature(negotiation_result):
    """A comparable, order-sensitive digest of a negotiation result."""
    if negotiation_result is None:
        return None
    return [
        (r["class_name"], r["status"], r["priority"], r["valid_slots"],
         r["total_search_space"], tuple(r["blocking_reasons"]),
         tuple((s["type"], s["constraint_field"], s["constraint_value"],
                s["impact_label"]) for s in r["suggestions"]))
        for r in negotiation_result["class_reports"]
    ]


class _NegotiationProbe:
    """Counts the two expensive per-class steps of the negotiation pass."""

    def __init__(self):
        self.analyses = 0
        self.suggestions = 0

    def reset(self):
        self.analyses = 0
        self.suggestions = 0

    @property
    def work(self):
        return self.analyses + self.suggestions

    def __repr__(self):  # pragma: no cover - assertion messages only
        return (f"<analyze_class={self.analyses} "
                f"suggest_for_class={self.suggestions}>")


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def negotiation_probe(monkeypatch):
    """Instrument the negotiation pass without changing what it computes.

    Counts on ``InfeasibilityAnalyzer.analyze_class`` and
    ``RelaxationSuggester.suggest_for_class`` rather than on
    ``logic.negotiate_after_optimization``, so the assertions survive the fix
    moving *where* the pass is invoked from — which is the whole point of the
    change under test.
    """
    probe = _NegotiationProbe()
    real_analyze = cn_mod.InfeasibilityAnalyzer.analyze_class
    real_suggest = cn_mod.RelaxationSuggester.suggest_for_class

    def counted_analyze(self, cls):
        probe.analyses += 1
        return real_analyze(self, cls)

    def counted_suggest(self, cls, analysis=None, max_suggestions=10):
        probe.suggestions += 1
        return real_suggest(self, cls, analysis, max_suggestions)

    monkeypatch.setattr(cn_mod.InfeasibilityAnalyzer, "analyze_class",
                        counted_analyze)
    monkeypatch.setattr(cn_mod.RelaxationSuggester, "suggest_for_class",
                        counted_suggest)
    return probe


@pytest.fixture
def partial_schedule():
    """A state plus a proposal that leaves ten classes unplaced.

    20 classes on a 3 x 6 grid with 2 rooms: tight enough that the first-fit
    proposal strands half of them, small enough that the whole negotiation
    pass runs in ~2 ms.
    """
    state = build_state(n_classes=20, n_rooms=2, n_slots=6, n_days=3,
                        n_lecturers=5, n_years=2, density=0.4, seed=7)
    placed, unplaced = _first_fit(state)
    assert unplaced, (
        "the fixture placed everything, so every call-count assertion in this "
        "module would pass at zero regardless of the code under test")
    assert placed, "the fixture placed nothing — not a realistic reschedule"
    return state, placed, unplaced


@pytest.fixture
def full_schedule():
    """A state plus a proposal with nothing left unplaced."""
    state = build_state(n_classes=6, n_rooms=4, n_slots=8, n_days=5,
                        n_lecturers=6, n_years=2, density=0.0, seed=3)
    placed, unplaced = _first_fit(state)
    assert not unplaced, (
        "the fixture was supposed to place everything; "
        f"{len(unplaced)} class(es) were stranded")
    return state, placed, unplaced


def _stub_solver(monkeypatch, placed, unplaced, changes=None):
    """Replace only the solver, leaving the wrapper and the negotiator real."""
    summary = {"improvement": {}, "before": {}, "after": {},
               "seed": SEED, "deterministic": True,
               "runs_completed": 1, "total_time": 0.0}
    seen = []

    def fake_solver(state, **kwargs):
        seen.append(kwargs)
        return list(placed), list(unplaced), list(changes or []), dict(summary)

    monkeypatch.setattr(wf_mod, "optimized_reschedule_all", fake_solver)
    return seen


def _workflow(state):
    return SchedulingWorkflow(state, get_weights=lambda: {})


# ===========================================================================
# 1. THE PASS IS NOT UNCONDITIONAL
# ===========================================================================

def test_reschedule_does_no_negotiation_work(partial_schedule,
                                             negotiation_probe, monkeypatch):
    """ST-PERF-007 — ``reschedule()`` must not run the negotiation pass.

    A failure means every user who presses Generate pays for the "why is this
    unplaced?" analysis of every stranded class, on the UI thread, whether or
    not they ever open the panel that shows it — about a second of frozen
    window at department scale, and more once ST-UI-015 enriches that panel.
    """
    state, placed, unplaced = partial_schedule
    _stub_solver(monkeypatch, placed, unplaced)

    result = _workflow(state).reschedule({}, seed=SEED)

    assert negotiation_probe.work == 0, (
        "reschedule() ran the constraint-negotiation pass eagerly: "
        f"{negotiation_probe.analyses} analyze_class + "
        f"{negotiation_probe.suggestions} suggest_for_class calls for "
        f"{len(unplaced)} unplaced classes. The pass must be deferred to the "
        "first read of RescheduleResult.negotiation_result.")
    assert result.unplaced, "stub result lost the unplaced list"


def test_negotiation_analyses_each_unplaced_class_once(partial_schedule,
                                                       negotiation_probe,
                                                       monkeypatch):
    """ST-PERF-007 — one analysis per unplaced class, not two.

    ``negotiate_after_optimization`` builds its per-class reports and then
    rebuilds the identical analyses for the diagnostic summary. A failure
    means the user waits twice as long as necessary for an answer that is
    identical either way.
    """
    state, placed, unplaced = partial_schedule
    _stub_solver(monkeypatch, placed, unplaced)

    result = _workflow(state).reschedule({}, seed=SEED)
    negotiation = result.negotiation_result

    assert negotiation is not None
    assert negotiation_probe.analyses == len(unplaced), (
        "the negotiation pass analysed the unplaced classes "
        f"{negotiation_probe.analyses / len(unplaced):.1f}x over: "
        f"{negotiation_probe.analyses} analyze_class calls for "
        f"{len(unplaced)} unplaced classes. The per-class reports and the "
        "diagnostic summary must share one analysis per class.")
    assert negotiation_probe.suggestions == len(unplaced), (
        f"{negotiation_probe.suggestions} suggest_for_class calls for "
        f"{len(unplaced)} unplaced classes")


def test_negotiation_result_is_computed_once_and_cached(partial_schedule,
                                                        negotiation_probe,
                                                        monkeypatch):
    """ST-PERF-007 — repeated reads must be free.

    ``ui/app.py`` reads ``result.negotiation_result`` three times per
    reschedule (lines 2842, 2881, 2882). A failure means a lazy fix that
    recomputes on every read has made the common path *slower* than the eager
    version it replaced.
    """
    state, placed, unplaced = partial_schedule
    _stub_solver(monkeypatch, placed, unplaced)

    result = _workflow(state).reschedule({}, seed=SEED)

    negotiation_probe.reset()
    first = result.negotiation_result
    assert negotiation_probe.analyses == len(unplaced), (
        "the first read of negotiation_result did not compute it: "
        f"{negotiation_probe!r} for {len(unplaced)} unplaced classes. The "
        "work belongs to the read, not to reschedule() — and it must not "
        "have been dropped altogether.")

    negotiation_probe.reset()
    second = result.negotiation_result
    third = result.negotiation_result

    assert negotiation_probe.work == 0, (
        "negotiation_result recomputed itself on re-read "
        f"({negotiation_probe!r}); the UI reads it three times per "
        "reschedule, so it must be memoised")
    assert second is first and third is first, (
        "negotiation_result handed out a different object on re-read; "
        "callers hold on to it across the accept/reject branch")


# ===========================================================================
# 2. NOTHING THE USER RELIES ON IS LOST
# ===========================================================================

def test_negotiation_result_still_says_what_it_used_to(partial_schedule,
                                                       monkeypatch):
    """ST-PERF-007 — deferring the pass must not change its answer.

    ``result.negotiation_result`` is what fills the negotiation tab of
    ``BulkResultsDialog`` and the post-reschedule warning log. A failure means
    the performance fix quietly deleted or degraded the only explanation the
    user gets for why a lesson could not be timetabled.
    """
    state, placed, unplaced = partial_schedule
    presolve = negotiate_after_optimization(state, placed, unplaced)
    assert presolve is not None and presolve["class_reports"], (
        "the reference pass produced nothing — fixture is not exercising it")

    _stub_solver(monkeypatch, placed, unplaced)
    workflow = _workflow(state)
    result = workflow.reschedule({}, seed=SEED)
    negotiation = result.negotiation_result

    assert negotiation is not None, (
        "negotiation_result is None even though the reschedule left "
        f"{len(unplaced)} classes unplaced — the pass was removed, not "
        "deferred. BulkResultsDialog drops its negotiation tab entirely and "
        "the user is told nothing about why those lessons are missing.")
    assert isinstance(negotiation, dict), (
        "negotiation_result must stay a plain attribute read yielding the "
        f"report dict, not a {type(negotiation).__name__}. ui/app.py:2842, "
        "ui/app.py:2881-2882 and stress-test/tests/scheduler_benchmark.py:92 "
        "all read it as an attribute; turning it into a method is a breaking "
        "API change, not a deferral.")
    assert set(negotiation) >= {"class_reports", "diagnostic_summary",
                                "unplaced_count"}, (
        f"negotiation_result lost keys its consumers read: {sorted(negotiation)}")
    assert negotiation["unplaced_count"] == len(unplaced)

    # The eager reference is computed on the schedule the proposal DESCRIBES,
    # which is what the state holds once the proposal is committed.
    #
    # Phase 3 changed which state that is (ST-SCHED-014). This assertion used
    # to compare against a pass over the PRE-solve state, and passed because
    # the deferred pass analysed the pre-solve state too — which was the
    # defect: the negotiation tab answered "why can't this class be placed?"
    # against a timetable in which the solve had not happened, called every
    # unplaced class "ok" with slots to spare, and reported all 20 classes as
    # unplaced when the solve left 10. The deferral property this test exists
    # for is untouched; only the baseline moved.
    workflow.apply_reschedule(result)
    eager = negotiate_after_optimization(state, placed, unplaced)

    assert _report_signature(negotiation) == _report_signature(eager), (
        "the deferred negotiation result disagrees with an eager pass over "
        "the same schedule")
    assert _report_signature(eager) != _report_signature(presolve), (
        "committing the proposal did not change what the negotiation pass "
        "says, so this comparison cannot tell the two states apart — pick a "
        "fixture where the occupancy actually matters")


def test_negotiation_result_survives_apply_unchanged(partial_schedule,
                                                     monkeypatch):
    """ST-PERF-007 — the answer must not depend on *when* it is read.

    ``ui/app.py`` reads ``negotiation_result`` before ``apply_reschedule()``
    (line 2842, feeding the dialog) and again after it (lines 2881-2882,
    feeding the warning log). A failure means the dialog and the warning log
    describe the same reschedule differently — the user is told a class has
    six free slots, then told it has none.
    """
    state, placed, unplaced = partial_schedule

    _stub_solver(monkeypatch, placed, unplaced)
    workflow = _workflow(state)
    result = workflow.reschedule({}, seed=SEED)

    before = result.negotiation_result
    assert before is not None and before["class_reports"], (
        "no negotiation report to compare — fixture is not exercising it")

    rejected = workflow.apply_reschedule(result)
    assert not rejected, f"the fixture proposal was rejected on apply: {rejected}"

    # Now move the live timetable out from under the report. If the value were
    # computed at read time against `self.state` rather than pinned to the
    # snapshot taken during reschedule(), this is what would make the dialog
    # and the warning log disagree.
    #
    # (Before Phase 3 the non-vacuity step here was apply_reschedule itself,
    # because the snapshot described the PRE-solve state and committing moved
    # the live state away from it. That gap was the ST-SCHED-014 defect and is
    # gone: the snapshot now describes the proposal, which is what apply
    # commits. Perturbing the state explicitly restores the test's bite and
    # tests the pinning property directly rather than as a side effect.)
    from scheduler_app.core.models import mark_unplaced
    for cls in state["classes"]:
        if cls.get("placed") and not cls.get("pinned"):
            mark_unplaced(cls)
            break
    else:
        pytest.fail("nothing was committed, so the perturbation is a no-op")

    perturbed = negotiate_after_optimization(state, placed, unplaced)
    assert _report_signature(perturbed) != _report_signature(before), (
        "perturbing the live timetable did not change what a fresh "
        "negotiation pass says, so this test cannot detect a value computed "
        "too late — pick a fixture where the occupancy actually matters")

    assert result.negotiation_result == before, (
        "negotiation_result changed when the live state changed, so it is "
        "being computed at read time rather than pinned to the state as of "
        "reschedule(). ui/app.py reads it on both sides of "
        "apply_reschedule(); the dialog and the warning log must agree.")


def test_negotiation_pass_leaves_constraints_untouched(partial_schedule):
    """ST-DATA-011 — the relaxation estimators must restore what they borrow.

    ``_estimate_day_impact`` / ``_estimate_time_impact`` /
    ``_estimate_room_impact`` mutate a class's constraint lists to simulate a
    relaxation and restore them in a ``finally``. A failure means a class
    silently keeps a day, hour or room the user never allowed — and every
    later schedule is built on that lie.

    **Why this drives the pass directly instead of going through
    ``reschedule()``.** The ST-PERF-007 fix hands the negotiator a *snapshot*
    of the state, so estimators reached through ``RescheduleResult`` mutate a
    throwaway copy and this guard would pass no matter what the ``finally``
    does. Verified: with the restore removed, the through-``reschedule()``
    form of this test still passes under the proposed fix, while the form
    below fails both before and after it. ST-DATA-011 is a property of the
    negotiator, so it is asserted against the negotiator — on the live state,
    which is also how ``ui/app.py``'s "why unplaced?" negotiators (lines 3831,
    3845) invoke it.
    """
    state, placed, unplaced = partial_schedule

    before = _constraint_snapshot(state)
    negotiation = negotiate_after_optimization(state, placed, unplaced)
    after = _constraint_snapshot(state)

    # Non-vacuity: the estimators only run while costing a relaxation
    # suggestion, so the pass must actually have produced some.
    assert negotiation and negotiation["class_reports"], (
        "the negotiation pass produced no reports, so no estimator ran and "
        "this test cannot see a leaked mutation")
    kinds = {s.get("constraint_field")
             for r in negotiation["class_reports"]
             for s in r.get("suggestions", [])}
    assert kinds & set(CONSTRAINT_FIELDS), (
        "no suggestion touched a constraint list "
        f"({sorted(k for k in kinds if k)}), so none of the three estimators "
        "under test was exercised — pick a fixture that produces relaxation "
        "suggestions")

    assert after == before, (
        "the negotiation pass left constraint lists mutated; classes changed: "
        + ", ".join(state["classes"][i]["name"]
                    for i, (b, a) in enumerate(zip(before, after)) if b != a))


# ===========================================================================
# 3. A FULLY PLACED RESCHEDULE DOES NO NEGOTIATION WORK AT ALL
# ===========================================================================

def test_fully_placed_reschedule_never_negotiates(full_schedule,
                                                  negotiation_probe,
                                                  monkeypatch):
    """ST-PERF-007 — nothing unplaced means nothing to explain.

    A failure means the happy path — the one users hit most — pays for an
    analysis whose only possible output is "everything is fine".
    """
    state, placed, unplaced = full_schedule
    _stub_solver(monkeypatch, placed, unplaced)

    result = _workflow(state).reschedule({}, seed=SEED)

    assert result.negotiation_result is None, (
        "negotiation_result must stay None when nothing is unplaced; "
        "BulkResultsDialog keys its whole negotiation tab off that")
    assert negotiation_probe.work == 0, (
        "a reschedule that placed every class still ran the negotiation "
        f"machinery: {negotiation_probe!r}")

    # Reading it must not trigger the pass either — laziness must not turn
    # "there is nothing to say" into "compute it to find that out".
    _ = result.negotiation_result
    assert negotiation_probe.work == 0, (
        f"reading negotiation_result on a fully placed reschedule did work: "
        f"{negotiation_probe!r}")


# ===========================================================================
# 4. DETERMINISM
# ===========================================================================

def test_negotiation_result_is_deterministic(monkeypatch):
    """ST-SCHED-013 / ST-PERF-007 — same input, same explanation.

    Phase 1 made ``reschedule(seed=...)`` reproducible. A failure means the
    negotiation change reintroduced set- or dict-iteration order into a
    user-visible list, so the same timetable explains itself differently on
    two runs and no bug report about it can ever be reproduced.

    Two independently built copies of the same state, so nothing is shared by
    identity — only by value.
    """
    signatures = []
    for _ in range(2):
        state = build_state(n_classes=20, n_rooms=2, n_slots=6, n_days=3,
                            n_lecturers=5, n_years=2, density=0.4, seed=7)
        placed, unplaced = _first_fit(state)
        assert unplaced
        _stub_solver(monkeypatch, placed, unplaced)
        result = _workflow(state).reschedule({}, seed=SEED)
        signatures.append(_report_signature(result.negotiation_result))

    assert len({len(s) for s in signatures}) == 1
    assert signatures[0], "no reports to compare — the check would be vacuous"
    assert signatures[0] == signatures[1], (
        "two identical reschedules explained themselves differently")


@pytest.mark.engine
@pytest.mark.slow
def test_seeded_reschedule_and_its_explanation_are_reproducible():
    """ST-SCHED-013 / ST-PERF-007 — determinism holds through the real solver.

    The only test here that runs the optimizer end to end on both sides of the
    comparison. A failure means the deferred negotiation result varies run to
    run even when the timetable does not.

    Budget is capped to ``multi_start_runs=1`` because what is under test is
    the negotiation half; the shipped configuration's own reproducibility is
    ``test_optimizer_determinism.py``'s job.

    ``multi_start_time_limit`` is raised to ``EMERGENCY_CAP`` — see its
    definition for why the shipped 120 s value must not be left in place here.
    """
    import functools

    real_solver = wf_mod.optimized_reschedule_all
    capped = functools.partial(real_solver, multi_start_runs=1,
                               multi_start_time_limit=EMERGENCY_CAP,
                               parallel_workers=-1)

    runs = []
    for _ in range(2):
        state = build_state(n_classes=20, n_rooms=2, n_slots=6, n_days=3,
                            n_lecturers=5, n_years=2, density=0.4, seed=7)
        workflow = _workflow(state)
        wf_mod.optimized_reschedule_all = capped
        try:
            result = workflow.reschedule({}, use_cpsat=False, seed=SEED)
        finally:
            wf_mod.optimized_reschedule_all = real_solver
        runs.append((
            sorted((c["name"], d, s, r) for c, d, s, r in result.placed),
            sorted(c["name"] for c, _ in result.unplaced),
            result.summary.get("seed"),
            _report_signature(result.negotiation_result),
        ))

    placed_a, unplaced_a, seed_a, sig_a = runs[0]
    placed_b, unplaced_b, seed_b, sig_b = runs[1]

    assert placed_a and unplaced_a, (
        "the instance placed everything or nothing; with no unplaced classes "
        "the negotiation comparison below is vacuous")
    assert seed_a == seed_b == SEED, (
        f"reschedule() did not report the seed it was given: {seed_a}, {seed_b}")
    assert (placed_a, unplaced_a) == (placed_b, unplaced_b), (
        "the seeded reschedule itself is no longer reproducible")
    assert sig_a, "no negotiation reports produced"
    assert sig_a == sig_b, (
        "the same seeded reschedule produced two different explanations for "
        "the same unplaced classes")


# ===========================================================================
# 5. THE CORRECTED ST-PERF-007 MEASUREMENT, HELD IN PLACE
# ===========================================================================

@pytest.mark.engine
@pytest.mark.slow
def test_production_reschedule_pays_nothing_for_the_wrapper(negotiation_probe):
    """ST-PERF-007 — on ``small``, on real optimizer output.

    Two assertions, one sharp and one loose.

    *Sharp*: the negotiation pass is not entered during ``reschedule()``. This
    is the finding's fix, verified against unplaced classes the real optimizer
    actually produced rather than ones a stub declared.

    *Loose*: everything the wrapper does besides solving stays under 5 % of
    the call. The solver is timed **from inside the same call**, so machine
    load divides out of the ratio and a busy CI runner cannot fail this on
    speed alone. Measured at the shipped ``multi_start_runs=5``: **0.045 %** —
    15.9 ms of 35.23 s, of which the negotiation pass is the bulk. 5 % leaves
    ~110x headroom; it is not a tight bound, it is a tripwire for the wrapper
    growing into the cost the register already claimed it was — which is what
    ST-UI-015's richer "why unplaced?" data would do if it were computed here.

    **Budget.** ``multi_start_runs`` is cut from the shipped 5 to 2, and
    ``multi_start_time_limit`` raised to ``EMERGENCY_CAP``. Neither weakens
    anything. The wrapper's cost is a fixed per-reschedule amount while the
    solve scales with restarts, so *fewer* restarts make the share assertion
    **stricter**, not looser — measured 0.13 % at 2 restarts against 0.045 %
    at 5, i.e. the bound is ~3x tighter here than at the shipped defaults. In
    exchange the test drops from 25 s idle / 88.7 s contended to a steady
    8.2-8.8 s across five runs and, more importantly, stops running 1.35x
    away from a wall-clock cap that would change what it exercises. The count
    assertions do not depend on the budget at all.
    """
    import time

    state = build_state(n_classes=25, n_rooms=4, n_lecturers=6, n_years=2,
                        density=0.2, seed=42)
    real_solver = wf_mod.optimized_reschedule_all
    solver_seconds = []

    def timed_solver(*args, **kwargs):
        kwargs.update(multi_start_runs=2,
                      multi_start_time_limit=EMERGENCY_CAP)
        t0 = time.perf_counter()
        out = real_solver(*args, **kwargs)
        solver_seconds.append(time.perf_counter() - t0)
        return out

    wf_mod.optimized_reschedule_all = timed_solver
    try:
        t0 = time.perf_counter()
        result = _workflow(state).reschedule({}, use_cpsat=False, seed=SEED)
        wall = time.perf_counter() - t0
    finally:
        wf_mod.optimized_reschedule_all = real_solver

    # Precondition, not a timing bound: if the emergency cap ever fired, the
    # restarts were truncated and this run is not the one the numbers above
    # describe. EMERGENCY_CAP is ~46x the measured solve, so this cannot fire
    # on a merely slow machine — it fires when the solver has genuinely
    # regressed, and then it should say so rather than quietly measure
    # something else.
    assert result.summary.get("deterministic") is True, (
        "the solve was clock-capped (or used CP-SAT), so it did not run the "
        "work this measurement assumes: "
        f"summary={ {k: result.summary.get(k) for k in ('seed', 'deterministic', 'runs_completed')} }")

    assert result.unplaced, (
        "the real optimizer placed all 25 classes, so the negotiation branch "
        "was never reachable and this test proves nothing")
    assert negotiation_probe.work == 0, (
        "the production reschedule ran the negotiation pass eagerly: "
        f"{negotiation_probe!r} for {len(result.unplaced)} unplaced classes")

    solve = solver_seconds[0]
    overhead_share = (wall - solve) / wall
    assert overhead_share < 0.05, (
        f"non-solver work is {overhead_share:.1%} of the reschedule "
        f"({wall - solve:.2f} s of {wall:.2f} s). ST-PERF-007's premise was "
        "that this share is large; it was measured at 0.02 %. If it has "
        "genuinely grown, the wrapper — not the solver — is now worth "
        "optimising.")

    # The data is still there, and asking for it is what costs.
    negotiation = result.negotiation_result
    assert negotiation is not None
    assert len(negotiation["class_reports"]) == len(result.unplaced)
    assert negotiation_probe.analyses == len(result.unplaced), (
        f"{negotiation_probe!r} for {len(result.unplaced)} unplaced classes")
