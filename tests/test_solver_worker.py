"""The solver must not run on the Qt main thread — ST-PERF-001.

Why this module exists
----------------------
``SchedulerApp._do_reschedule`` (``ui/app.py``) calls
``self._workflow.reschedule(...)`` **inline on the GUI thread** and hands it a
progress callback that does ``self.statusBar().showMessage(...)`` followed by
``QApplication.processEvents(...)``. That callback is the freeze: the only
reason the window repaints at all during a solve is that the solver reaches
back into Qt and pumps the event loop for it, roughly three times per second.
Between two pumps the application is dead — it does not repaint, does not
answer input, and Windows paints it "Not Responding". There is no cancel: the
only way out of a 120-second solve is to kill the process, which loses the
schedule.

What was measured before these tests were written
-------------------------------------------------
All numbers are from ``.venv-audit`` on this machine against
``fix/phase-2-performance`` (which is Phase 1's engine: seeded RNG,
iteration-bounded LNS).

1. **The freeze is real and it is long.** The audit measured 25.4 s at 80
   classes single-restart and 121.7 s at the shipped multi-start default
   (``evidence/scheduler_benchmark.csv``). Reproduced here at test scale
   through the production entry point ``SchedulingWorkflow.reschedule`` at
   shipped defaults: ``tiny`` (5 classes) 0.86 s, 16 classes 6.12 s,
   ``small`` (25 classes) **37.0 s**. Every one of those seconds is currently
   spent with the GUI thread inside ``optimize()``.

   VERIFIER re-measurement of the same three, same machine, idle: 0.88 s /
   3.94 s / **18.0 s**. The 25-class figure is half what was first recorded,
   so treat "tens of seconds of frozen window at 25 classes" as the claim and
   not the specific number — solve time on this machine moves by 2x with
   background load, which is precisely why no test here asserts one.

2. **Progress arrives three times per second at best, and never during the
   greedy phase.** ``ScheduleOptimizer`` calls ``progress_callback`` only from
   ``_lns_improve``, at ``iteration % 10 == 0`` plus one final call per run.
   Measured cadence at 16 classes / 5 starts x 200 LNS iterations: 105 calls
   over 34.5 s, first call at **0.28 s**, median gap 0.31 s, max gap 0.60 s.
   The silence before the first call *is* ``_greedy_construct``, which has no
   callback and no checkpoint of any kind: **2.86 s at 25 classes, 8.32 s at
   80**. Any cancellation built purely on the existing callback therefore
   inherits a multi-second — at 250 classes, a multi-*ten*-second — latency
   floor. The plan puts a checkpoint in the greedy backtracker for exactly this
   reason, and ``test_cancelling_during_the_greedy_phase_...`` is what holds it
   there.

3. **The callback protocol is too thin for a real progress UI.** Its five
   arguments are ``(iteration, best_score, current_score, run_number,
   total_runs)`` — there is no denominator for ``iteration``, so no determinate
   progress bar can be drawn, and no phase label, so the 4.5 s of greedy silence
   cannot be explained to the user. Worse, ``_cpsat_optimize`` re-uses the same
   callback as a liveness heartbeat with ``run_number=-1, total_runs=0``, which
   is not a point on any progress scale. ``SolveProgress`` below replaces it.

4. **Moving the solve to a thread is viable — verified, not assumed.** The
   optimizer holds ``state`` but never writes to it: ``optimize()`` contains no
   ``mark_placed`` / ``mark_unplaced`` and works entirely on local ``solution``
   lists; a deep-copy comparison across a full ``SchedulingWorkflow.reschedule``
   came back equal at 5 and at 16 classes. Driven from a plain
   ``threading.Thread`` the optimizer produced *the same answer* as the main
   thread (score 3.18 both, 16/16 placed) in the same time (0.96 s vs 1.13 s),
   including its internal ``ProcessPoolExecutor`` (``parallel_workers=0``,
   which is the shipped default) started from that worker thread. While it ran,
   the calling thread completed 544 loop turns. The GIL is not the obstacle
   here: the GUI thread needs microseconds per repaint, and CPython hands it
   the interpreter every 5 ms.

5. **A process worker would have to re-map its answer onto the live state.**
   Every entry of ``RescheduleResult.placed`` — and every ``changes[i]["cls"]``
   — *is* the dict inside ``state["classes"]`` (measured: 5/5 by identity at 5
   classes; VERIFIER re-measured 16/16 at 16 classes, and
   ``changes[0]["cls"]`` likewise), and
   ``apply_reschedule`` commits by mutating those dicts in place. The result
   pickles happily (7.3 KB at 16 classes) but comes back with **0/16** entries
   still pointing at the live dicts, so a process-based worker would commit its
   placements into throwaway copies unless every one is translated back, the
   way ``_cpsat_optimize`` already translates by index. Shipping the state is
   not the problem (39 KB / under 1 ms at 250 classes) — shipping the *answer*
   back is.

Why the fast lane is 16 classes
-------------------------------
Same instance as ``test_optimizer_determinism.py``, for the same reason: it is
the smallest size at which the answer actually moves between seeds, so an
equality assertion between two solves is not vacuous. Two budgets are used:

===========  =========================  ==============  ==================
name         budget                     16-cls runtime  what it is for
===========  =========================  ==============  ==================
``FAST``     2 starts x 15 LNS iters    1.10 s          result equality,
                                                        progress protocol
``LONG``     5 starts x 200 LNS iters   34.5 s          cancellation
===========  =========================  ==============  ==================

``LONG`` is deliberately long: ``test_cancelling_a_real_solve_...`` cancels on
the first progress event (0.28 s in) and asserts the worker reports cancelled
within 5 s. The gap between "5 s" and "34.5 s if nothing stops it" is what
makes that assertion mean something. On the passing path the test costs ~1.5 s;
on the failing path it fails at the 5 s assertion and the module teardown then
waits out the remaining ~33 s rather than leaking a thread into the next
module.

No test in this module asserts an absolute solve *duration*. Solve time varies
with machine load — Phase 1 measured the same 80-class run at 77 s and at 105 s
— so the two wall-clock numbers here are both *cancellation latencies* with
7x headroom, chosen so a loaded CI box cannot redden them.

Runtime
-------
Whole module, default lane, against a scratch tree with the plan applied:
**6.2-6.5 s** on an idle machine, of which ~3.6 s is the four real-optimizer
tests and ~0.8 s is importing ``ui/app.py``. Two runs out of eleven came in at
12.3 s, all of the extra being the 16-class solve inside
``test_worker_result_equals_synchronous_reschedule`` — the same 2x solve-time
spread seen in the freeze numbers above, and the reason nothing here asserts a
solve duration. Nothing is marked ``slow``: even the outlier is a third of the
budget. Against this tree, with nothing implemented, all 19 fail in
**1.6-3.0 s**.

The thresholds were then checked under load, because that is the only thing
that makes a timing assertion flaky. With the same 12-core machine deliberately
oversubscribed 2:1 (24 spinning processes on 12 cores) the module takes
75-88 s and still passes 19/19 across three runs — no verdict anywhere in the
module changed.

The two wall-clock assertions were then measured directly rather than inferred
from per-test durations. **Cancellation latency, the only thing they bound:**

=========================  ==========  ==========  ===========
                           idle        2:1 load    threshold
=========================  ==========  ==========  ===========
LNS-phase cancel           0.015 s     0.031 s     5 s
                                       0.047 s
                                       0.032 s
greedy-phase cancel        0.016 s     0.016 s     3 s
                                       0.031 s
                                       0.062 s
=========================  ==========  ==========  ===========

That is 50-200x headroom, and load barely moves it, because a per-iteration
checkpoint measures *spacing* and not throughput. (An earlier draft of this
docstring quoted 1.23 s and 0.51 s "at saturation"; those were whole-test call
durations, which include waiting for the first progress event, not the
latencies the assertions actually bound.)

The one place the failing path is slow is
``test_cancelling_during_the_greedy_phase_...``: if cancellation is ignored
entirely it fails after 3 s and the 80-class solve then runs itself out in
another ~5 s. Its budget is one multi-start run and one LNS iteration
specifically so that "cancellation does not work at all" costs ~9 s and not
several minutes of leaked worker thread.

The API these tests are written against
---------------------------------------
See the implementation plan in the hand-off. In short:

``scheduler_app/core/solver_worker.py`` — **no Qt** — provides ``CancelToken``
(one-way, thread-safe), ``SolveCancelled``, the frozen ``SolveProgress`` record,
and ``run_solve(workflow, weights, *, cancel_token, on_progress, **kw)`` which
adapts the optimizer's five-argument callback into ``SolveProgress`` and
returns a ``RescheduleResult``.

``scheduler_app/ui/solver_task.py`` — ``SolverTask(QObject)``, created and owned
on the GUI thread, running ``run_solve`` on a ``QThread``. Signals
``progress(SolveProgress)``, ``finished(RescheduleResult)``,
``failed(BaseException)``, ``cancelled()`` — exactly one of the last three per
run, always delivered on the GUI thread. ``start()``, ``cancel()``,
``wait(msec)``, ``is_running``, ``was_cancelled``, ``result``,
``failure_traceback``.

The cancel token is threaded ``SolverTask`` -> ``run_solve`` ->
``SchedulingWorkflow.reschedule`` -> ``logic.optimized_reschedule_all`` ->
``ScheduleOptimizer``, which polls it in the multi-start loop, in the greedy
backtracker and in the LNS loop. ``reschedule`` and ``optimized_reschedule_all``
also gain a ``**optimizer_kwargs`` passthrough so a caller can choose a search
budget — which is what lets these tests run in seconds instead of minutes, and
what Phase 4's "quick / thorough" reschedule modes will need.

Discrimination
--------------
Every assertion here is about the seam, so most of the module is written
against a *fake* workflow: a real solve cannot be parked mid-flight on demand,
and a test that proves "the caller was free" by racing the optimizer would be
exactly the flaky perf test this suite is not allowed to grow. The four tests
that use the real optimizer are the ones where the fake could hide something:
the answer must not change (``..._equals_synchronous_reschedule``), the
optimizer itself must poll the token (``test_cancelling_a_real_solve_...``),
the silent greedy phase must poll it too
(``test_cancelling_during_the_greedy_phase_...``), and the real progress stream
must be usable by a progress bar (``..._supports_a_real_progress_ui``).

Measured against a scratch tree with the plan implemented, 19/19 pass. And the
module caught a real design trap in the first draft of the worker:
``SolverTask.wait()`` deadlocked, because the worker thread's event loop was
being shut down from a *queued* slot, which the GUI thread cannot deliver while
it is blocked inside ``wait()``. That is the bug that would have made "close
the window mid-solve" hang forever; it is written up in the plan.

VERIFIER — what an independent rebuild found
--------------------------------------------
The seam was rebuilt from the implementation plan alone, in a second scratch
tree, and five mutants were run against it. Verdicts, all measured:

===============================================  ==================================
mutant                                           result
===============================================  ==================================
plan applied faithfully                          19 pass, 6.2-6.5 s idle
greedy checkpoint removed                        exactly 1 fails, the right one
all optimizer checkpoints removed                exactly 2 fail, the right ones
``cancel()`` emits ``cancelled`` at once and     2 fail — but only AFTER this
lets the optimizer run on                        review added the join assertion
adapter's iteration denominator drifts from      exactly 1 fails, the new
``ScheduleOptimizer``'s own default              scale test
``start()`` loses its idempotence guard          exactly 1 fails, the new
                                                 double-start test
===============================================  ==================================

A second, unrelated defect surfaced while checking isolation and is written up
on ``_defuse_foreign_dangling_modals``: ``pytest tests/test_settings_recovery.py
tests/test_solver_worker.py`` aborts the interpreter 6/6 once the fix lands,
because ``ui/app.py``'s deferred settings-problem warning box leaves an
un-owned ``QTimer.singleShot`` pointing at
a destroyed window and this module is the first thing in the suite that pumps
the Qt loop hard enough to fire it. Contained here, 10/10 clean; the one-line
production fix is in the plan. The natural whole-suite order never hit it,
which is exactly why it had not been found.

The fourth mutant is the reason three assertions were added. As first written,
**both real-optimizer cancellation tests passed against a "cancel" that only
stopped the UI listening** — the optimizer kept burning CPU in the background,
which is exactly the failure mode ``test_the_worker_is_given_a_cancel_token``'s
docstring warns about. Worse, the abandoned QThreads then aborted the pytest
process (exit 127 after 7 tests, no summary), because Qt terminates the process
rather than raising when a running QThread is destroyed. Both are fixed here:
``_assert_the_worker_really_stopped`` turns "a signal arrived" into "the worker
is joined", and ``_UNJOINABLE`` keeps a thread that could not be joined alive
so a failure stays a failure instead of taking the interpreter down.
"""
import copy
import inspect
import threading
import time

import pytest

from _support.dataset_gen import make_preset

pytestmark = [pytest.mark.ui]


# ── The instance and the two budgets ────────────────────────────────────────
SEED = 20260826
FAST_N_CLASSES = 16

# 2 starts x 15 LNS iterations, no wall-clock pressure, scorer pool off.
# 1.10 s measured. Used wherever the test needs a real solve that *finishes*.
FAST = dict(multi_start_runs=2, lns_iterations=15, lns_no_improve_limit=15,
            lns_time_limit=1e9, multi_start_time_limit=1e9,
            parallel_workers=-1)

# 5 starts x 200 LNS iterations. 34.5 s measured — long on purpose, so that
# "it stopped within 5 s of the cancel" is a statement about cancellation and
# not about the solve having finished by itself.
LONG = dict(multi_start_runs=5, lns_iterations=200, lns_no_improve_limit=200,
            lns_time_limit=1e9, multi_start_time_limit=1e9,
            parallel_workers=-1)

# How long a terminal signal may take to reach the GUI thread. 5 s against a
# 34.5 s solve and against a 0.60 s worst-case gap between the optimizer's own
# progress callbacks: ~7x headroom over the thing being measured and ~8x over
# the checkpoint spacing, which is enough for a loaded shared CI runner. It is
# NOT a solve-time budget — no test here asserts how long solving takes.
CANCEL_BUDGET_S = 5.0

# Cancellation issued during the greedy phase, where the optimizer emits no
# progress at all. Measured: 7.8 s latency with no checkpoint there, 3.7 s with
# one polled every 128 backtracking iterations, 0.04 s with one polled every
# iteration. 3 s admits only the last, with ~70x headroom.
GREEDY_CANCEL_BUDGET_S = 3.0

# Ordinary signal-delivery wait for the fake-workflow tests, where the "work"
# is a threading.Event and the only thing being timed is Qt's queued delivery.
SIGNAL_BUDGET_S = 10.0

# After a terminal signal, how long the worker may take to actually be *gone*.
# This is a join, not a solve: by the time the signal was delivered the job had
# already returned, so the only thing left is thread teardown. 5 s is absurdly
# generous for that and is chosen to match CANCEL_BUDGET_S rather than to be
# tight. VERIFIER: this budget exists because "the signal arrived" and "the
# solve stopped" are different claims — see _assert_the_worker_really_stopped.
JOIN_BUDGET_S = 5.0


# ── Imports of the code under test, deferred so failures stay per-test ──────
def _core():
    """``scheduler_app.core.solver_worker`` — the Qt-free half of the seam."""
    import scheduler_app.core.solver_worker as sw
    return sw


def _SolverTask():
    """``scheduler_app.ui.solver_task.SolverTask`` — the Qt-side owner."""
    from scheduler_app.ui.solver_task import SolverTask
    return SolverTask


# ── Qt helpers ──────────────────────────────────────────────────────────────
def _gui_thread():
    from PyQt6.QtCore import QCoreApplication
    return QCoreApplication.instance().thread()


def _pump_until(qapp, predicate, timeout_s, tick_s=0.002):
    """Spin the GUI event loop until *predicate* holds. Returns elapsed seconds.

    Sleeps a little on each turn: a bare ``processEvents`` loop holds the GIL
    hard enough to starve the worker it is waiting for.
    """
    start = time.monotonic()
    while True:
        qapp.processEvents()
        if predicate():
            return time.monotonic() - start
        if time.monotonic() - start >= timeout_s:
            return time.monotonic() - start
        time.sleep(tick_s)


def _assert_the_worker_really_stopped(task, what):
    """The terminal signal is not the claim — the worker being *gone* is.

    VERIFIER: without this, every cancellation test in this module passes
    against the exact half-fix the module's own docstrings warn about — a
    ``cancel()`` that emits ``cancelled`` immediately and lets the optimizer
    burn on in the background. Measured: with that mutant, both real-optimizer
    cancellation tests passed, and the abandoned threads then aborted the
    pytest process outright on the following test.

    ``wait()`` is the discriminator because it is the same call
    ``closeEvent`` has to make: it returns only when the worker is joined.
    """
    joined = task.wait(int(JOIN_BUDGET_S * 1000))
    assert joined is True, (
        f"{what}: a terminal signal was delivered but the worker did not "
        f"stop within {JOIN_BUDGET_S:.0f} s. Cancel that only makes the UI "
        "stop listening leaves the optimizer running at full CPU — the user "
        "presses Stop, presses Generate again, and now has two solves "
        "competing for the machine.")
    assert task.is_running is False


class _Sink:
    """Records every SolverTask signal, and which thread delivered it."""

    def __init__(self, task):
        self.progress = []
        self.result = None
        self.error = None
        self.cancelled_count = 0
        self.terminal_order = []
        self.handler_threads = []
        task.progress.connect(self._on_progress)
        task.finished.connect(self._on_finished)
        task.failed.connect(self._on_failed)
        task.cancelled.connect(self._on_cancelled)

    def _note_thread(self):
        from PyQt6.QtCore import QThread
        self.handler_threads.append(QThread.currentThread())

    def _on_progress(self, p):
        self._note_thread()
        self.progress.append(p)

    def _on_finished(self, result):
        self._note_thread()
        self.result = result
        self.terminal_order.append("finished")

    def _on_failed(self, exc):
        self._note_thread()
        self.error = exc
        self.terminal_order.append("failed")

    def _on_cancelled(self):
        self._note_thread()
        self.cancelled_count += 1
        self.terminal_order.append("cancelled")

    @property
    def terminal(self):
        return self.terminal_order[0] if self.terminal_order else None

    @property
    def done(self):
        return bool(self.terminal_order)


# ── The fake workflow ───────────────────────────────────────────────────────
class _FakeWorkflow:
    """A ``SchedulingWorkflow``-shaped stand-in the test can steer exactly.

    Duck-typed against the one method the worker is allowed to call. Whatever
    the worker passes is recorded, so the tests also pin the *call shape* of
    the seam, not only its outcome.
    """

    def __init__(self, *, gate=None, raises=None, progress_events=(),
                 honour_cancel=True, state=None):
        self.state = state if state is not None else {"classes": []}
        self._gate = gate
        self._raises = raises
        self._progress_events = list(progress_events)
        self._honour_cancel = honour_cancel

        self.entered = threading.Event()
        self.returned = threading.Event()
        self.calls = []
        self.worker_thread = None          # QThread seen inside reschedule()
        self.worker_ident = None           # threading.get_ident() inside it
        self.callback_threads = []         # QThread at each progress emit
        self.result = object()             # identity-comparable stand-in

    def reschedule(self, weights, use_cpsat=False, progress_callback=None,
                   seed=None, cancel_token=None, **kwargs):
        from PyQt6.QtCore import QThread

        self.calls.append(dict(weights=weights, use_cpsat=use_cpsat,
                               progress_callback=progress_callback,
                               seed=seed, cancel_token=cancel_token,
                               kwargs=dict(kwargs)))
        self.worker_thread = QThread.currentThread()
        self.worker_ident = threading.get_ident()
        self.entered.set()
        try:
            if self._gate is not None:
                self._gate.wait(timeout=60.0)

            for args in self._progress_events:
                if progress_callback is not None:
                    self.callback_threads.append(QThread.currentThread())
                    progress_callback(*args)

            if self._honour_cancel and cancel_token is not None:
                cancel_token.raise_if_cancelled()

            if self._raises is not None:
                raise self._raises
            return self.result
        finally:
            self.returned.set()


# ── Fixtures ────────────────────────────────────────────────────────────────
# VERIFIER: a task whose QThread is still running must never be garbage
# collected — Qt aborts the process ("QThread: Destroyed while thread is still
# running") rather than raising, which turns one failing test into an
# interpreter crash that hides every test after it. Measured against a
# deliberate half-fix: the module died at exit 127 after 7 tests with no
# summary. Anything the guard could not join is parked here forever instead.
_UNJOINABLE = []


@pytest.fixture(scope="module", autouse=True)
def _defuse_foreign_dangling_modals(qapp):
    """Stop another module's orphaned zero-timer from aborting this process.

    VERIFIER, measured and reproducible 6/6: running
    ``tests/test_settings_recovery.py`` and this module in one invocation
    aborts the interpreter outright — Windows 0xc0000374, heap corruption, no
    pytest summary at all — inside ``ui/app.py``'s settings-problem warning::

        QTimer.singleShot(0, lambda: QMessageBox.warning(
            self, tr("status.settings_problem_title"), message))

    That timer is given no context object, so Qt cannot cancel it when the
    ``SchedulerApp`` the lambda captures is destroyed. The settings module
    deliberately provokes the settings-problem path, closes and deletes its
    window, and leaves a zero-timer pointing at a dead C++ widget. Nothing
    detonates it until something pumps the Qt loop — and this module, once the
    fix lands, is the first one in the suite that pumps hard.

    Draining the queue first was tried and does **not** work (3 clean runs,
    then 6 crashes). What does work is denying the lambda its one C++ touch:
    the crash is inside ``QMessageBox.warning`` being handed a deleted parent,
    and this module never shows a message box of its own, so the call is
    stubbed for the module's lifetime and restored afterwards.

    This is containment, not a fix. That ``ui/app.py`` call should read
    ``QTimer.singleShot(0, self, lambda: ...)`` so Qt drops the timer with the
    window; that is a required item in the implementation plan, and when it
    lands this fixture can go.
    """
    from PyQt6.QtWidgets import QMessageBox

    original = QMessageBox.warning
    QMessageBox.warning = staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Ok)
    try:
        for _ in range(20):
            qapp.processEvents()
            time.sleep(0.002)
        yield
    finally:
        QMessageBox.warning = original


@pytest.fixture
def task_guard(qapp):
    """Own every SolverTask a test creates; never leak a worker thread.

    A failing assertion must not leave a live solve running into the next test
    module — Qt objects from a dead test would then be torn down underneath it.
    On teardown every task is cancelled, every gate is opened, and the worker
    is waited out. The wait is generous (40 s) because the whole point of the
    cancellation tests is that they run against a solve that would otherwise
    take 34.5 s.
    """
    tasks = []
    gates = []

    def register(task=None, gate=None):
        if task is not None:
            tasks.append(task)
        if gate is not None:
            gates.append(gate)
        return task

    register.gates = gates
    yield register

    for gate in gates:
        gate.set()
    for task in tasks:
        try:
            task.cancel()
        except Exception:
            pass
    for task in tasks:
        deadline = time.monotonic() + 40.0
        while time.monotonic() < deadline:
            qapp.processEvents()
            try:
                if not task.is_running:
                    break
            except Exception:
                break
            time.sleep(0.005)
        joined = False
        try:
            joined = bool(task.wait(2000))
        except Exception:
            pass
        if not joined:
            # Keep it alive rather than let Qt abort the interpreter.
            _UNJOINABLE.append(task)
    qapp.processEvents()


@pytest.fixture(scope="module")
def state16():
    """A 16-class instance, built once; every test deep-copies before using it."""
    return make_preset("small", seed=42, n_classes=FAST_N_CLASSES)


def _workflow(state):
    from scheduler_app.core.workflow import SchedulingWorkflow
    return SchedulingWorkflow(state, lambda: {})


def _signature(placed):
    """The proposal as a comparable value — same shape as the determinism module."""
    from scheduler_app.core.models import cls_key

    return tuple(
        (cls.get("class_code") or cls.get("name", ""), cls_key(cls),
         day, slot, room or "")
        for cls, day, slot, room in placed)


def _assert_not_degenerate(signature, state, what):
    """Refuse a run that placed almost nothing.

    ``test_worker_result_equals_synchronous_reschedule`` is an equality between
    two solves, and two empty proposals are equal. The floor is half the
    instance; the measured result is 16/16.
    """
    n = len(state["classes"])
    assert len(signature) >= max(1, n // 2), (
        f"{what} placed only {len(signature)} of {n} classes, so the equality "
        "assertion in this test would be vacuous.")


# ===========================================================================
# 1. THE SOLVE DOES NOT BLOCK THE CALLER
# ===========================================================================
def test_start_returns_while_the_solve_is_still_running(qapp, task_guard):
    """ST-PERF-001 — proves the caller is free, structurally, not by timing.

    A failure means the Generate button still runs the optimizer inline: the
    window is frozen for the 25-120 s the solve takes at a real department
    scale, and the user cannot see progress, cancel, or close it.

    The solve is a ``threading.Event`` the test holds shut, so "is the caller
    free?" is answered by facts (``start()`` returned, the worker is parked
    inside ``reschedule``, no terminal signal yet, the GUI event loop is still
    turning) rather than by racing a real optimizer.
    """
    from PyQt6.QtCore import QTimer

    gate = threading.Event()
    task_guard(gate=gate)
    fake = _FakeWorkflow(gate=gate)

    task = task_guard(_SolverTask()(fake, {}, seed=SEED))
    sink = _Sink(task)

    ticks = []
    timer = QTimer()
    timer.setInterval(1)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start()

    task.start()

    # (a) start() handed control back before the work was done.
    assert task.is_running, (
        "SolverTask.start() returned with is_running False — the solve ran "
        "inline on the calling thread, which is the ST-PERF-001 freeze.")
    assert not sink.done, (
        "a terminal signal was already delivered when start() returned, so "
        "the solve had finished before the caller got control back")

    # (b) the worker really entered the solve, and it is parked there.
    assert fake.entered.wait(timeout=SIGNAL_BUDGET_S), (
        "the worker never called workflow.reschedule()")
    assert not fake.returned.is_set()

    # (c) the GUI thread reaches a checkpoint while the solve is in flight.
    _pump_until(qapp, lambda: len(ticks) >= 20, timeout_s=SIGNAL_BUDGET_S)
    assert len(ticks) >= 20, (
        f"the GUI event loop turned only {len(ticks)} times while the solve "
        "was in flight; a responsive window needs it turning continuously")
    assert not fake.returned.is_set(), "the gate opened by itself"
    assert task.is_running
    assert not sink.done

    timer.stop()

    # (d) and it still completes.
    gate.set()
    _pump_until(qapp, lambda: sink.done, timeout_s=SIGNAL_BUDGET_S)
    assert sink.terminal == "finished", (
        f"expected a finished signal after the gate opened, got "
        f"{sink.terminal_order!r}")


def test_the_worker_runs_on_a_thread_that_is_not_the_gui_thread(
        qapp, task_guard):
    """ST-PERF-001 — the solve leaves the GUI thread for real.

    A failure means the work was wrapped in a "worker" that still executes on
    the GUI thread, so the window freezes exactly as before while looking
    refactored.
    """
    fake = _FakeWorkflow()
    task = task_guard(_SolverTask()(fake, {}, seed=SEED))
    sink = _Sink(task)

    task.start()
    _pump_until(qapp, lambda: sink.done, timeout_s=SIGNAL_BUDGET_S)

    assert sink.terminal == "finished", f"got {sink.terminal_order!r}"
    assert fake.worker_thread is not None
    assert fake.worker_thread is not _gui_thread(), (
        "workflow.reschedule() ran on the GUI thread")
    assert fake.worker_ident != threading.get_ident(), (
        "workflow.reschedule() ran on the test's own thread")


# ===========================================================================
# 2. CANCELLATION
# ===========================================================================
def test_cancel_is_reported_and_commits_nothing(qapp, task_guard):
    """ST-PERF-001 — a cancelled solve ends in `cancelled`, with no result.

    A failure means a user who presses Stop either gets no acknowledgement at
    all, or gets the half-finished schedule applied to their timetable anyway.
    """
    gate = threading.Event()
    task_guard(gate=gate)
    fake = _FakeWorkflow(gate=gate)

    task = task_guard(_SolverTask()(fake, {}, seed=SEED))
    sink = _Sink(task)
    task.start()
    assert fake.entered.wait(timeout=SIGNAL_BUDGET_S)

    task.cancel()
    gate.set()          # the solve is now free to run to its cancel checkpoint

    _pump_until(qapp, lambda: sink.done, timeout_s=SIGNAL_BUDGET_S)

    assert sink.terminal_order == ["cancelled"], (
        f"expected exactly one 'cancelled' signal, got {sink.terminal_order!r}")
    assert sink.result is None, "a cancelled solve delivered a result anyway"
    assert task.result is None
    assert task.was_cancelled is True
    assert task.is_running is False
    _assert_the_worker_really_stopped(task, "cancelled fake solve")


def test_cancel_before_start_never_runs_the_solve(qapp, task_guard):
    """ST-PERF-001 — cancelling a queued solve must not start one.

    A failure means pressing Stop immediately after Generate still burns the
    full solve, so the button lies about what it does.
    """
    fake = _FakeWorkflow()
    task = task_guard(_SolverTask()(fake, {}, seed=SEED))
    sink = _Sink(task)

    task.cancel()
    task.start()

    _pump_until(qapp, lambda: sink.done, timeout_s=SIGNAL_BUDGET_S)

    assert sink.terminal_order == ["cancelled"], (
        f"expected 'cancelled', got {sink.terminal_order!r}")
    assert fake.calls == [], (
        "the solve was started even though it had already been cancelled")


def test_starting_the_same_task_twice_runs_one_solve(qapp, task_guard):
    """ST-PERF-001 — a second Generate must not start a second solve.

    A failure means the user who clicks Generate twice (which they will, once
    the button stops freezing the window and starts looking idle) gets two
    optimizers on one ``state`` dict and two proposals racing into one
    ``apply_reschedule`` — the timetable ends up a mixture of two answers.

    VERIFIER: added because nothing else in this module touches re-entrancy,
    and the whole point of the change is that the window stays clickable
    during the solve. This pins the seam half only — ``start()`` is
    idempotent. The *app* half (disabling Generate / undo / import while
    ``is_running``) is in the plan and is not pinned by any test here; a
    QThread-based ``SolverTask`` gets the seam half almost for free, so treat
    a green result as necessary and not sufficient.
    """
    gate = threading.Event()
    task_guard(gate=gate)
    fake = _FakeWorkflow(gate=gate)

    task = task_guard(_SolverTask()(fake, {}, seed=SEED))
    sink = _Sink(task)

    task.start()
    assert fake.entered.wait(timeout=SIGNAL_BUDGET_S)
    task.start()                       # the second click
    _pump_until(qapp, lambda: len(fake.calls) > 1, timeout_s=1.0)

    assert len(fake.calls) == 1, (
        f"workflow.reschedule() was entered {len(fake.calls)} times from one "
        "SolverTask; two solves are now writing one proposal")

    gate.set()
    _pump_until(qapp, lambda: sink.done, timeout_s=SIGNAL_BUDGET_S)
    assert sink.terminal_order == ["finished"], (
        f"expected exactly one terminal signal, got {sink.terminal_order!r}")


def test_the_worker_is_given_a_cancel_token_it_can_poll(qapp, task_guard):
    """ST-PERF-001 — cancellation reaches the solver, not just the wrapper.

    A failure means ``cancel()`` only stops the UI from *listening*: the
    optimizer keeps burning CPU in the background for the rest of the 120 s,
    so a user who cancels and retries now has two solves running at once.
    """
    core = _core()
    gate = threading.Event()
    task_guard(gate=gate)
    fake = _FakeWorkflow(gate=gate)

    task = task_guard(_SolverTask()(fake, {}, seed=SEED))
    _Sink(task)
    task.start()
    assert fake.entered.wait(timeout=SIGNAL_BUDGET_S)

    assert len(fake.calls) == 1
    token = fake.calls[0]["cancel_token"]
    assert token is not None, (
        "SolverTask did not pass a cancel_token into workflow.reschedule(); "
        "the solver has no way to notice a cancellation")
    assert isinstance(token, core.CancelToken)
    assert token.is_cancelled() is False

    task.cancel()
    assert token.is_cancelled() is True, (
        "SolverTask.cancel() did not trip the token the solver is polling")

    gate.set()


def test_cancel_token_is_one_way_and_visible_across_threads():
    """ST-PERF-001 — the token's own contract (no Qt involved).

    A failure means the flag the whole cancellation design rests on is either
    resettable (so a stale cancel silently kills the *next* solve) or not
    visible to the thread that has to act on it (so cancel does nothing).
    """
    core = _core()
    token = core.CancelToken()

    assert token.is_cancelled() is False
    token.raise_if_cancelled()          # must be a no-op while untripped

    seen = []
    ready = threading.Event()

    def watcher():
        ready.set()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if token.is_cancelled():
                seen.append(True)
                return
            time.sleep(0.001)
        seen.append(False)

    th = threading.Thread(target=watcher, name="token-watcher")
    th.start()
    assert ready.wait(timeout=5.0)
    token.cancel()
    th.join(timeout=10.0)

    assert seen == [True], (
        "a cancel on the GUI thread was not observed by the worker thread")
    assert token.is_cancelled() is True
    token.cancel()                       # idempotent
    assert token.is_cancelled() is True
    with pytest.raises(core.SolveCancelled):
        token.raise_if_cancelled()


@pytest.mark.engine
def test_cancelling_a_real_solve_stops_it_and_leaves_state_untouched(
        qapp, task_guard, state16):
    """ST-PERF-001 — the *optimizer* honours the token, and rolls nothing in.

    A failure means either that a real solve cannot be interrupted at all (the
    user's only escape from a 120 s run is Task Manager, losing the schedule),
    or that an interrupted solve left the timetable in a state that is neither
    the old one nor a complete new one.

    Configured at 5 starts x 200 LNS iterations, measured at 34.5 s if nothing
    stops it. The cancel is issued on the first progress event (measured at
    0.28 s) and the worker must report cancelled within 5 s — comfortably more
    than the 0.60 s worst-case gap between the optimizer's own checkpoints and
    comfortably less than the 34.5 s it would otherwise take.
    """
    state = copy.deepcopy(state16)
    before = copy.deepcopy(state)

    task = task_guard(_SolverTask()(
        _workflow(state), {}, seed=SEED, **LONG))
    sink = _Sink(task)

    task.start()
    _pump_until(qapp, lambda: sink.progress or sink.done,
                timeout_s=30.0)
    assert not sink.done, (
        "the solve reached a terminal state before it could be cancelled, so "
        "this test proved nothing; the LONG budget is supposed to make that "
        "impossible")
    assert sink.progress, "no progress arrived within 30 s of starting a solve"

    t0 = time.monotonic()
    task.cancel()
    _pump_until(qapp, lambda: sink.done, timeout_s=CANCEL_BUDGET_S)
    latency = time.monotonic() - t0

    assert sink.terminal == "cancelled", (
        f"after {latency:.2f} s the solve had not reported cancelled "
        f"(signals so far: {sink.terminal_order!r}). A run that ignores the "
        "token is a run the user cannot stop.")
    assert latency < CANCEL_BUDGET_S
    _assert_the_worker_really_stopped(task, "cancelled 16-class solve")
    assert sink.result is None and task.result is None, (
        "a cancelled solve still delivered a proposal")
    assert state == before, (
        "a cancelled solve changed the timetable; cancel must leave the "
        "state byte-for-byte as it was")


@pytest.mark.engine
def test_cancelling_during_the_greedy_phase_stops_within_seconds(
        qapp, task_guard):
    """ST-PERF-001 — the silent first phase of the solve must be cancellable too.

    A failure means Stop appears to do nothing for the first several seconds of
    every solve — at 250 classes, for the first half-minute — because the only
    place the optimizer can notice a cancellation is the LNS loop, and the LNS
    loop has not started yet.

    ``_greedy_construct`` emits no progress callback at all, so the length of
    its silence is the cancellation latency floor for any design that reuses
    the existing callback as its checkpoint. Measured time from ``optimize()``
    to the first LNS callback: 2.86 s at 25 classes, **8.32 s at 80**.

    The cancel is issued 0.5 s in, which is inside the greedy phase by a factor
    of ~16 on this machine and by more on any slower one. Measured latencies
    from that cancel: **7.8 s** with no greedy checkpoint, **3.7 s** with one
    polled every 128 backtracking iterations (each iteration does a full
    look-ahead scoring pass, so 128 of them is seconds, not microseconds), and
    **0.04 s** with one polled every iteration.

    Discrimination, measured by building all three: no checkpoint fails; every
    iteration passes with ~70x headroom. A 128-iteration stride lands on the
    threshold — it failed the standalone probe at 3.7 s and passed inside the
    suite — which is not a flakiness problem (the *passing* configuration has
    two orders of magnitude of headroom, which is what a loaded CI box needs)
    but is the reason the plan asks for a per-iteration poll rather than a
    strided one.

    The budget is one start and one LNS iteration on purpose: if cancellation
    is ignored entirely the solve still ends by itself in ~8.6 s instead of
    running for minutes and leaking a thread out of a failing test.
    """
    state = make_preset("normal", seed=42)
    budget = dict(multi_start_runs=1, lns_iterations=1, lns_no_improve_limit=1,
                  lns_time_limit=1e9, multi_start_time_limit=1e9,
                  parallel_workers=-1)

    task = task_guard(_SolverTask()(
        _workflow(state), {}, seed=SEED, **budget))
    sink = _Sink(task)

    task.start()
    _pump_until(qapp, lambda: sink.done, timeout_s=0.5)
    assert not sink.done, (
        "an 80-class solve finished in under 0.5 s, which means it is not "
        "doing the work this test is about")
    assert sink.progress == [], (
        "progress already arrived 0.5 s into an 80-class solve, so the greedy "
        "phase was already over and this test is measuring the LNS checkpoint "
        "instead of the greedy one — the measured greedy phase is 8.3 s, so "
        "this can only mean the instance or the budget changed")

    t0 = time.monotonic()
    task.cancel()
    _pump_until(qapp, lambda: sink.done, timeout_s=GREEDY_CANCEL_BUDGET_S)
    latency = time.monotonic() - t0

    assert sink.terminal == "cancelled", (
        f"{latency:.2f} s after the cancel the solve had still not stopped "
        f"(signals: {sink.terminal_order!r}). The greedy construction phase "
        "needs its own cancellation checkpoint — the LNS callback is up to "
        "8.3 s away at 80 classes and further at 250.")
    assert latency < GREEDY_CANCEL_BUDGET_S
    _assert_the_worker_really_stopped(task, "cancelled 80-class greedy solve")


# ===========================================================================
# 3. PROGRESS
# ===========================================================================
def test_progress_is_delivered_to_the_caller(qapp, task_guard):
    """ST-PERF-001 — the caller hears about progress at least once.

    A failure means the window shows a frozen "Optimising..." with no sign of
    life for the whole solve, which is what users read as a hang.
    """
    core = _core()
    fake = _FakeWorkflow(progress_events=[
        (0, 9.0, 9.0, 0, 2),
        (10, 7.5, 8.0, 0, 2),
        (0, 7.5, 8.0, 1, 2),
    ])
    task = task_guard(_SolverTask()(fake, {}, seed=SEED))
    sink = _Sink(task)

    task.start()
    _pump_until(qapp, lambda: sink.done, timeout_s=SIGNAL_BUDGET_S)

    assert sink.terminal == "finished", f"got {sink.terminal_order!r}"
    assert len(sink.progress) >= 1, (
        "the solver reported progress three times and the caller heard none "
        "of it")
    for p in sink.progress:
        assert isinstance(p, core.SolveProgress), (
            f"progress payload is {type(p).__name__}, not SolveProgress")


@pytest.mark.engine
def test_progress_from_a_real_solve_supports_a_real_progress_ui(
        qapp, task_guard, state16):
    """ST-PERF-001 — the progress stream can actually drive a progress bar.

    A failure means the UI can only render a spinner: it cannot say which
    multi-start run is going, how far through it is, or whether the quality is
    improving — which is the whole difference between "working" and "hung"
    during a two-minute solve.

    ``best_score`` is deliberately NOT asserted monotone. ``_lns_improve``
    treats "placed more classes" as better than "scored lower", so
    ``best_quality`` legitimately rises when a worse-scoring solution places an
    extra class. ``run`` and ``iteration`` and ``fraction`` are the numbers
    that really are monotone, and they are the ones a progress bar needs.
    """
    state = copy.deepcopy(state16)
    task = task_guard(_SolverTask()(_workflow(state), {}, seed=SEED, **FAST))
    sink = _Sink(task)

    task.start()
    _pump_until(qapp, lambda: sink.done, timeout_s=120.0)
    assert sink.terminal == "finished", (
        f"expected the FAST solve to finish, got {sink.terminal_order!r}")

    events = sink.progress
    assert len(events) >= 1, "a real solve produced no progress at all"

    for p in events:
        assert p.total_runs >= 1
        assert p.total_runs == FAST["multi_start_runs"], (
            f"total_runs is {p.total_runs}, but the caller asked for "
            f"{FAST['multi_start_runs']} multi-start runs")
        assert 0 <= p.run < p.total_runs, (
            f"run={p.run} is not a point on a 0..{p.total_runs - 1} scale; a "
            "progress bar cannot render it")
        assert p.max_iterations > 0, (
            "iteration has no denominator, so no determinate progress bar can "
            "be drawn — this is the gap in today's 5-argument callback")
        assert 0 <= p.iteration <= p.max_iterations
        assert isinstance(p.phase, str) and p.phase, (
            "no phase label, so the multi-second silent greedy stretch cannot "
            "be explained to the user")
        assert 0.0 <= p.fraction <= 1.0
        assert p.best_score == p.best_score        # not NaN
        assert p.current_score == p.current_score

    runs = [p.run for p in events]
    assert runs == sorted(runs), (
        f"the multi-start run index went backwards: {runs}")

    for r in set(runs):
        iters = [p.iteration for p in events if p.run == r]
        assert iters == sorted(iters), (
            f"iteration went backwards inside run {r}: {iters}")

    fractions = [p.fraction for p in events]
    assert fractions == sorted(fractions), (
        f"overall completion went backwards: {fractions}")


def test_progress_scale_matches_the_optimizers_own_default_budget(
        qapp, task_guard):
    """ST-PERF-001 — the bar must be drawn against the budget actually run.

    A failure means the progress bar is scaled to a budget the optimizer is
    not using. Every solve a user starts from the Generate button runs at the
    *shipped* defaults, with no budget passed in, so this is the only
    configuration that matters to them: if the denominator is stale the bar
    either stalls short of the end or saturates early, both of which read as
    "it hung".

    VERIFIER: added because ``SolveProgress.max_iterations`` is supplied by
    the adapter from its own literal defaults, not reported by the optimizer.
    Two independent copies of the same constant is exactly the thing that
    drifts, and no other test in this module exercises the no-kwargs path.
    The optimizer's own signature is the source of truth here, so this test
    keeps working when the shipped budget is re-tuned — it only fails when
    the two copies disagree.
    """
    from scheduler_app.core.schedule_optimizer import ScheduleOptimizer

    params = inspect.signature(ScheduleOptimizer.__init__).parameters
    n_runs = params["multi_start_runs"].default
    n_iters = params["lns_iterations"].default
    assert isinstance(n_runs, int) and isinstance(n_iters, int)

    # The last callback the optimizer emits when it runs its own defaults.
    last = (n_iters - 1, 1.0, 1.0, n_runs - 1, n_runs)
    fake = _FakeWorkflow(progress_events=[last])

    # Deliberately NO budget kwargs: this is the production call shape.
    task = task_guard(_SolverTask()(fake, {}, seed=SEED))
    sink = _Sink(task)
    task.start()
    _pump_until(qapp, lambda: sink.done, timeout_s=SIGNAL_BUDGET_S)
    assert sink.terminal == "finished", f"got {sink.terminal_order!r}"
    assert sink.progress, "no progress reached the caller"

    p = sink.progress[-1]
    assert p.total_runs == n_runs
    assert p.iteration <= p.max_iterations, (
        f"the optimizer reached iteration {p.iteration} of its default "
        f"{n_iters}, but the payload's denominator is {p.max_iterations}; the "
        "progress bar would run off the end of its own scale")
    assert p.fraction > 0.9, (
        f"at the optimizer's last default-budget callback the bar is only "
        f"{p.fraction:.1%} full. The denominator ({p.max_iterations} x "
        f"{p.total_runs} runs) does not match the budget the optimizer "
        f"actually runs ({n_iters} x {n_runs}).")


# ===========================================================================
# 4. THE RESULT
# ===========================================================================
@pytest.mark.engine
def test_worker_result_equals_synchronous_reschedule(qapp, task_guard, state16):
    """ST-PERF-001 — moving off-thread must not change the answer.

    A failure means the refactor quietly produces a different timetable than
    the one users get today: a lossy state snapshot, a dropped weight, a
    reseeded RNG. Phase 1 (ST-SCHED-013) made this comparison possible by
    making the same seed give the same schedule; this is what that was for.

    Both solves run on their own deep copy of the same instance, at the same
    seed and the same budget.
    """
    sync_state = copy.deepcopy(state16)
    sync_result = _workflow(sync_state).reschedule(
        {}, use_cpsat=False, seed=SEED, **FAST)
    sync_sig = _signature(sync_result.placed)
    _assert_not_degenerate(sync_sig, sync_state,
                           "synchronous SchedulingWorkflow.reschedule()")

    worker_state = copy.deepcopy(state16)
    task = task_guard(_SolverTask()(
        _workflow(worker_state), {}, seed=SEED, **FAST))
    sink = _Sink(task)
    task.start()
    _pump_until(qapp, lambda: sink.done, timeout_s=120.0)

    assert sink.terminal == "finished", (
        f"the off-thread solve did not deliver a result: {sink.terminal_order!r}")
    result = sink.result
    assert result is not None
    assert result is task.result, (
        "SolverTask.result and the finished signal disagree about the result")

    worker_sig = _signature(result.placed)
    assert worker_sig == sync_sig, (
        "the worker produced a different timetable than the synchronous "
        f"reschedule for the same seed: {len(worker_sig)} placements vs "
        f"{len(sync_sig)}")
    assert result.summary["seed"] == sync_result.summary["seed"] == SEED
    assert result.summary["after"]["total"] == sync_result.summary["after"]["total"], (
        "same seed, same input, different schedule quality off-thread")
    assert len(result.changes) == len(sync_result.changes)
    assert len(result.unplaced) == len(sync_result.unplaced)


# ===========================================================================
# 5. ERRORS
# ===========================================================================
def test_an_exception_in_the_solver_reaches_the_caller(qapp, task_guard):
    """ST-PERF-001 — a crashing solve reports, it does not hang.

    A failure means an optimizer exception on the worker thread leaves the UI
    sitting on "Optimising..." forever with a disabled window and no error —
    strictly worse than today, where the crash at least reaches the crash
    dialog. Today's ``reschedule()`` wraps the call in try/except and shows
    ``CrashReportDialog``; that path has to keep working across the thread.
    """
    boom = RuntimeError("optimizer exploded")
    fake = _FakeWorkflow(raises=boom)
    task = task_guard(_SolverTask()(fake, {}, seed=SEED))
    sink = _Sink(task)

    task.start()
    _pump_until(qapp, lambda: sink.done, timeout_s=SIGNAL_BUDGET_S)

    assert sink.terminal_order == ["failed"], (
        f"expected exactly one 'failed' signal, got {sink.terminal_order!r}")
    assert sink.error is boom, (
        f"the caller was handed {sink.error!r} instead of the exception the "
        "solver raised")
    assert sink.result is None and task.result is None
    assert task.is_running is False

    tb = task.failure_traceback
    assert isinstance(tb, str) and "optimizer exploded" in tb, (
        "no traceback text was captured on the worker thread, so "
        "CrashReportDialog has nothing to report; format_exc() has to be "
        "called inside the worker's except block, not on the GUI thread")


# ===========================================================================
# 6. THREAD AFFINITY — no Qt object touched from the worker thread
# ===========================================================================
def test_every_caller_facing_callback_runs_on_the_gui_thread(qapp, task_guard):
    """ST-PERF-001 — the classic crash in this refactor.

    A failure means the worker thread calls back into the UI directly, exactly
    as ``_do_reschedule``'s current ``_progress`` does with
    ``statusBar().showMessage()`` and ``processEvents()``. Touching a widget
    from a non-GUI thread is undefined behaviour in Qt: it corrupts the paint
    state or takes the whole application down, intermittently, on the user's
    machine and never on the developer's.

    Both halves are asserted: the solver-side callback really does run on the
    worker thread (so the work moved), and every SolverTask signal is delivered
    on the GUI thread (so nothing a slot touches is touched from the worker).
    """
    fake = _FakeWorkflow(progress_events=[
        (0, 5.0, 5.0, 0, 1),
        (10, 4.0, 4.5, 0, 1),
    ])
    task = task_guard(_SolverTask()(fake, {}, seed=SEED))
    sink = _Sink(task)

    task.start()
    _pump_until(qapp, lambda: sink.done, timeout_s=SIGNAL_BUDGET_S)
    assert sink.terminal == "finished", f"got {sink.terminal_order!r}"

    gui = _gui_thread()

    assert fake.callback_threads, "the fake never invoked the progress callback"
    for th in fake.callback_threads:
        assert th is not gui, (
            "the solver-side progress callback ran on the GUI thread, so the "
            "solve did not move off it")

    assert sink.handler_threads, "no SolverTask signal was delivered at all"
    for th in sink.handler_threads:
        assert th is gui, (
            "a SolverTask signal was delivered on the worker thread; any slot "
            "that touches a widget would be touching Qt off the GUI thread")


def test_progress_payloads_carry_no_qt_objects(qapp, task_guard):
    """ST-PERF-001 — the worker must not hand widgets across the boundary.

    A failure means the seam smuggles a QObject (a status bar, a dialog, the
    window) into the worker thread inside the progress payload, which
    reintroduces the cross-thread touch the signal machinery was there to
    prevent.
    """
    from dataclasses import fields, is_dataclass
    from PyQt6.QtCore import QObject

    fake = _FakeWorkflow(progress_events=[(0, 5.0, 5.0, 0, 1)])
    task = task_guard(_SolverTask()(fake, {}, seed=SEED))
    sink = _Sink(task)

    task.start()
    _pump_until(qapp, lambda: sink.done, timeout_s=SIGNAL_BUDGET_S)
    assert sink.progress, "no progress to inspect"

    for p in sink.progress:
        assert not isinstance(p, QObject)
        assert is_dataclass(p), (
            "SolveProgress should be a plain frozen dataclass — a value that "
            "can cross a thread boundary without owning anything")
        assert p.__dataclass_params__.frozen, (
            "SolveProgress is mutable, so a GUI slot can rewrite a payload "
            "the worker thread still holds a reference to — the same "
            "cross-thread aliasing the signal machinery exists to prevent")
        for f in fields(p):
            value = getattr(p, f.name)
            assert not isinstance(value, QObject), (
                f"SolveProgress.{f.name} carries a QObject across threads")
            assert isinstance(value, (str, int, float, bool, type(None))), (
                f"SolveProgress.{f.name} is {type(value).__name__}; the "
                "payload must be primitives only")


def test_the_solver_worker_module_does_not_import_qt():
    """ST-PERF-001 / ST-ARCH-009 — the code that runs on the worker is Qt-free.

    A failure means the module executing on the background thread can reach Qt
    at all, which is how the cross-thread touch creeps back in one convenience
    import at a time. It also keeps ``core/`` free of a ``ui/`` dependency,
    which is the direction the architecture findings want.
    """
    import scheduler_app.core.solver_worker as sw

    with open(sw.__file__, encoding="utf-8") as fh:
        source = fh.read()
    for needle in ("PyQt6", "QtCore", "QtWidgets", "QObject", "pyqtSignal"):
        assert needle not in source, (
            f"scheduler_app/core/solver_worker.py mentions {needle!r}; the "
            "Qt-free half of the seam must stay Qt-free")


# ===========================================================================
# 7. TEARDOWN — closing the window mid-solve
# ===========================================================================
def test_owner_can_cancel_and_wait_before_tearing_down(qapp, task_guard):
    """ST-PERF-001 — closeEvent needs a way to stop the solve and join it.

    A failure means closing the window during a solve either blocks forever or
    destroys the widgets while the worker thread is still alive and still
    holding references to them — a crash on exit, which users report as "it
    corrupted my file" because the autosave never completed.

    This is the contract ``SchedulerApp.closeEvent`` is built on: ``cancel()``
    then ``wait(msec)`` returns True, and afterwards nothing is running.
    """
    gate = threading.Event()
    task_guard(gate=gate)
    fake = _FakeWorkflow(gate=gate)

    task = task_guard(_SolverTask()(fake, {}, seed=SEED))
    sink = _Sink(task)
    task.start()
    assert fake.entered.wait(timeout=SIGNAL_BUDGET_S)
    assert task.is_running

    task.cancel()
    gate.set()

    assert task.wait(int(CANCEL_BUDGET_S * 1000)) is True, (
        "SolverTask.wait() timed out after cancel(); an owner cannot safely "
        "tear itself down")
    assert task.is_running is False

    qapp.processEvents()
    assert sink.terminal == "cancelled", f"got {sink.terminal_order!r}"


def test_the_reschedule_ui_no_longer_pumps_the_event_loop_itself():
    """ST-PERF-001 — the freeze itself, pinned at its source.

    ``_do_reschedule`` installs a progress callback that calls
    ``statusBar().showMessage()`` and ``QApplication.processEvents()`` from
    inside the solver. That callback *is* the freeze, and it is also the
    cross-thread crash waiting to happen once the solve moves. A failure means
    the worker was added but the old inline path is still what the Generate
    button runs.

    This is a source-level pin rather than a behavioural one on purpose: the
    behavioural version would have to drive the real ``SchedulerApp`` through a
    complete solve, which is a 30-second UI test with a live thread in it —
    precisely the kind that gets deleted the first time CI is slow.
    """
    from scheduler_app.ui.app import SchedulerApp

    methods = {
        name: obj for name, obj in inspect.getmembers(
            SchedulerApp, predicate=inspect.isfunction)
        if "reschedule" in name
    }
    assert methods, (
        "no SchedulerApp method with 'reschedule' in its name — this pin needs "
        "rewiring to wherever the Generate button now lands")

    offenders = []
    inline = []
    for name, fn in methods.items():
        try:
            src = inspect.getsource(fn)
        except OSError:                                  # pragma: no cover
            continue
        if "processEvents" in src:
            offenders.append(name)
        if "_workflow.reschedule(" in src:
            inline.append(name)
    # VERIFIER: deleting the processEvents pump alone would satisfy the
    # original assertion while leaving the solve exactly where it is — a
    # window that no longer repaints *at all* for 25-120 s, which is worse
    # than today. The synchronous call itself has to be gone.
    assert not inline, (
        f"{', '.join(sorted(inline))} still calls the synchronous "
        "self._workflow.reschedule(...) inline. Removing the processEvents "
        "pump without moving the solve makes the freeze total instead of "
        "intermittent.")
    assert not offenders, (
        f"{', '.join(sorted(offenders))} still pump the Qt event loop from "
        "inside the solve. That is the ST-PERF-001 freeze: between two pumps "
        "the window is unresponsive, and once the solve moves to a worker "
        "thread the same call becomes a cross-thread Qt touch.")

    with open(inspect.getfile(SchedulerApp), encoding="utf-8") as fh:
        app_source = fh.read()
    assert "SolverTask" in app_source, (
        "ui/app.py never mentions SolverTask, so the Generate button is not "
        "using the worker")
