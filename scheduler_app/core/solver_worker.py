"""Running a solve off the GUI thread — the Qt-free half (ST-PERF-001).

``SchedulerApp`` used to call ``SchedulingWorkflow.reschedule`` inline on the Qt
main thread and hand it a progress callback that pumped ``processEvents``. That
callback *was* the freeze: the only reason the window repainted during a solve
was that the solver reached back into Qt roughly three times a second, and
between two pumps the application answered nothing. At a realistic 80-class
department that is 25–120 s of a dead window, with no way out but Task Manager —
which loses the schedule.

This module holds everything that runs *on the worker*, and deliberately imports
no Qt at all. Keeping it Qt-free is not tidiness: it is what stops a convenience
import from quietly reintroducing a cross-thread widget touch, and it keeps
``core/`` from depending on ``ui/`` (ST-ARCH-009). The Qt-side owner lives in
``scheduler_app/ui/solver_task.py``.

Three pieces:

``CancelToken``    a one-way, thread-safe flag the solver polls.
``SolveProgress``  a frozen record of primitives — safe to hand across a thread
                   boundary because it owns nothing and cannot be rewritten.
``run_solve``      adapts the optimizer's five-argument callback into
                   ``SolveProgress`` and runs the reschedule.
"""
import threading
from dataclasses import dataclass


class SolveCancelled(Exception):
    """Raised inside the solver when the user has asked it to stop."""


class CancelToken:
    """A one-way, thread-safe "stop" flag.

    One-way on purpose. A resettable token invites the bug where a stale cancel
    from a previous run silently kills the *next* solve; a fresh token per run
    is cheap and cannot do that.
    """

    __slots__ = ("_event",)

    def __init__(self):
        self._event = threading.Event()

    def cancel(self) -> None:
        """Ask the solve to stop. Idempotent."""
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """No-op while untripped; raises :class:`SolveCancelled` once tripped."""
        if self._event.is_set():
            raise SolveCancelled()


@dataclass(frozen=True)
class SolveProgress:
    """One point on a progress scale, as primitives only.

    The optimizer's own callback signature —
    ``(iteration, best_score, current_score, run_number, total_runs)`` — cannot
    drive a real progress UI: ``iteration`` has no denominator, so no
    determinate bar can be drawn, and there is no phase label, so the several
    seconds of silence during greedy construction cannot be explained to the
    user. This carries both.

    Frozen, and primitives only, so that a GUI slot cannot rewrite a payload the
    worker thread still holds a reference to.
    """

    phase: str
    run: int
    total_runs: int
    iteration: int
    max_iterations: int
    best_score: float
    current_score: float

    @property
    def fraction(self) -> float:
        """Overall completion in ``0.0..1.0``, across all multi-start runs."""
        total = max(1, self.total_runs) * max(1, self.max_iterations)
        done = self.run * max(1, self.max_iterations) + self.iteration
        return min(1.0, max(0.0, done / total))


# Phase labels. ``PHASE_SOLVING`` covers the LNS improvement loop, which is the
# only phase the optimizer reports from; ``PHASE_CPSAT`` is the liveness
# heartbeat ``_cpsat_optimize`` sends with ``run_number=-1``, which is not a
# point on any progress scale and must not be treated as one.
PHASE_SOLVING = "solving"
PHASE_CPSAT = "cpsat"


def make_progress_adapter(on_progress, total_runs, max_iterations):
    """Adapt the optimizer's 5-argument callback into :class:`SolveProgress`.

    Returns None when *on_progress* is None, so the optimizer can skip the call
    entirely rather than paying for a no-op on every tenth LNS iteration.
    """
    if on_progress is None:
        return None

    total_runs = max(1, int(total_runs))
    max_iterations = max(1, int(max_iterations))
    last = {"fraction": -1.0}

    def _adapter(iteration, best_score, current_score, run_number, total):
        if run_number is not None and run_number < 0:
            # CP-SAT heartbeat: a liveness tick, not a position. Report it as
            # its own phase and hold the bar where it was.
            on_progress(SolveProgress(
                phase=PHASE_CPSAT, run=total_runs - 1, total_runs=total_runs,
                iteration=max_iterations, max_iterations=max_iterations,
                best_score=float(best_score or 0.0),
                current_score=float(current_score or 0.0)))
            return
        run = min(max(0, int(run_number or 0)), total_runs - 1)
        it = min(max(0, int(iteration or 0)), max_iterations)
        progress = SolveProgress(
            phase=PHASE_SOLVING, run=run, total_runs=total_runs,
            iteration=it, max_iterations=max_iterations,
            best_score=float(best_score or 0.0),
            current_score=float(current_score or 0.0))
        # The bar only ever moves forward. The optimizer restarts its iteration
        # count on each multi-start run, so an unfiltered stream would jump
        # backwards several times per solve.
        if progress.fraction < last["fraction"]:
            return
        last["fraction"] = progress.fraction
        on_progress(progress)

    return _adapter


def run_solve(workflow, weights, *, cancel_token=None, on_progress=None,
              use_cpsat=False, seed=None, **optimizer_kwargs):
    """Run a full reschedule, reporting progress and honouring cancellation.

    Everything here runs on the worker thread. *workflow* is a
    :class:`~scheduler_app.core.workflow.SchedulingWorkflow` (or anything with
    the same ``reschedule`` shape).

    Raises :class:`SolveCancelled` if the token is tripped; the caller turns
    that into a "cancelled" outcome rather than an error.
    """
    from scheduler_app.core.schedule_optimizer import (
        DEFAULT_LNS_ITERATIONS, DEFAULT_MULTI_START_RUNS,
    )

    # The denominators must be the budget the optimizer will ACTUALLY run, and
    # the production Generate button passes no budget at all — so the defaults
    # are the user-facing path. They are read from the optimizer's own
    # constants rather than copied, or the bar stops short of the end (or
    # saturates early) on every real solve the moment the two drift.
    total_runs = optimizer_kwargs.get("multi_start_runs", DEFAULT_MULTI_START_RUNS)
    max_iterations = optimizer_kwargs.get("lns_iterations", DEFAULT_LNS_ITERATIONS)

    if cancel_token is not None:
        cancel_token.raise_if_cancelled()

    kwargs = dict(optimizer_kwargs)
    if seed is not None:
        kwargs["seed"] = seed
    return workflow.reschedule(
        weights,
        use_cpsat=use_cpsat,
        progress_callback=make_progress_adapter(
            on_progress, total_runs, max_iterations),
        cancel_token=cancel_token,
        **kwargs)
