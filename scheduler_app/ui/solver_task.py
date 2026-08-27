"""Owning a background solve from the GUI thread (ST-PERF-001).

The Qt half of the seam whose Qt-free half is
``scheduler_app/core/solver_worker.py``. ``SolverTask`` is created and owned on
the GUI thread, runs ``run_solve`` on a ``QThread``, and delivers exactly one
terminal signal per run — ``finished``, ``failed`` or ``cancelled`` — always on
the GUI thread.

The deadlock this design exists to avoid
----------------------------------------
An earlier shape shut the worker's event loop down from a *queued* slot. That
cannot be delivered while the GUI thread is blocked inside ``wait()``, so
``closeEvent`` hung forever — the "closing the window mid-solve freezes the app"
bug, in the code meant to fix freezing. The job therefore calls
``thread.quit()`` itself, in a ``finally``, on the worker thread, so the loop
always ends whatever the outcome.
"""
import traceback

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from scheduler_app.core.solver_worker import CancelToken, SolveCancelled, run_solve


class _SolveJob(QObject):
    """The unit of work that lives on the worker thread."""

    progress = pyqtSignal(object)
    finished = pyqtSignal(object)
    failed = pyqtSignal(object, str)
    cancelled = pyqtSignal()

    def __init__(self, workflow, weights, cancel_token, options):
        super().__init__()
        self._workflow = workflow
        self._weights = weights
        self._token = cancel_token
        self._options = dict(options)

    def run(self):
        thread = self.thread()
        try:
            result = run_solve(
                self._workflow, self._weights,
                cancel_token=self._token,
                on_progress=self.progress.emit,
                **self._options)
        except SolveCancelled:
            self.cancelled.emit()
        except BaseException as exc:  # noqa: BLE001 — reported, never swallowed
            self.failed.emit(exc, traceback.format_exc())
        else:
            # A cancel that lands between the last checkpoint and here still
            # counts as a cancel: committing a result the user asked us to
            # abandon is worse than discarding one they would have accepted.
            if self._token is not None and self._token.is_cancelled():
                self.cancelled.emit()
            else:
                self.finished.emit(result)
        finally:
            if thread is not None:
                thread.quit()


class SolverTask(QObject):
    """A cancellable solve running on its own thread.

    Create on the GUI thread, connect the signals, call :meth:`start`. Exactly
    one of ``finished`` / ``failed`` / ``cancelled`` arrives per run.
    """

    progress = pyqtSignal(object)
    finished = pyqtSignal(object)
    failed = pyqtSignal(object)
    cancelled = pyqtSignal()

    def __init__(self, workflow, weights, *, seed=None, use_cpsat=False,
                 parent=None, **optimizer_kwargs):
        super().__init__(parent)
        self._workflow = workflow
        self._weights = weights
        self._options = dict(optimizer_kwargs)
        self._options["seed"] = seed
        self._options["use_cpsat"] = use_cpsat

        self._token = CancelToken()
        self._thread = None
        self._job = None
        self._started = False
        self._was_cancelled = False
        self.result = None
        self.failure_traceback = None

    # ── State ───────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        # Asked of the thread itself, never of a flag updated by a queued slot.
        # `wait()` returns as soon as the worker is joined, but the
        # `QThread.finished` relay is delivered on the GUI thread and may not
        # have run yet — so a flag would still read True to the very caller
        # that just successfully joined it, which is exactly what closeEvent
        # asks.
        if self._thread is None:
            return False
        return bool(self._thread.isRunning())

    @property
    def was_cancelled(self) -> bool:
        return self._was_cancelled

    # ── Control ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Begin solving. Idempotent — a second call does nothing.

        Idempotence matters here more than it looks: the whole point of this
        class is that the window stays clickable during the solve, so a user
        WILL press Generate twice. Two solves sharing one state dict and one
        ``apply_reschedule`` is how this change would corrupt a timetable.
        """
        if self._started:
            return
        self._started = True

        if self._token.is_cancelled():
            # Cancelled before it ever ran: report it and never touch the
            # workflow at all.
            self._was_cancelled = True
            self.cancelled.emit()
            return

        self._thread = QThread()
        self._job = _SolveJob(self._workflow, self._weights, self._token,
                              self._options)
        self._job.moveToThread(self._thread)

        self._thread.started.connect(self._job.run)
        # Relays: the job emits on the worker thread, these slots run on the
        # GUI thread because `self` lives there.
        self._job.progress.connect(self._on_progress)
        self._job.finished.connect(self._on_finished)
        self._job.failed.connect(self._on_failed)
        self._job.cancelled.connect(self._on_cancelled)
        self._thread.finished.connect(self._on_thread_finished)

        self._thread.start()

    def cancel(self) -> None:
        """Ask the solve to stop. Idempotent; safe before or after ``start``."""
        self._token.cancel()

    def wait(self, msec: int = 30000) -> bool:
        """Block until the worker thread is gone. True if it is.

        This is the call ``closeEvent`` makes: returning True means the worker
        is genuinely joined, not merely that a signal was delivered.
        """
        if self._thread is None:
            return True
        if not self._thread.isRunning():
            return True
        return bool(self._thread.wait(msec))

    # ── Relays (GUI thread) ─────────────────────────────────────────────────

    def _on_progress(self, payload):
        self.progress.emit(payload)

    def _on_finished(self, result):
        self.result = result
        self.finished.emit(result)

    def _on_failed(self, exc, tb_text):
        self.failure_traceback = tb_text
        self.failed.emit(exc)

    def _on_cancelled(self):
        self._was_cancelled = True
        self.cancelled.emit()

    def _on_thread_finished(self):
        if self._job is not None:
            self._job.deleteLater()
            self._job = None
