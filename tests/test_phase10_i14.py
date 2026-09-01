"""Item 14 — a Thorough solve that fell back to greedy tells the user nothing.

``core/schedule_optimizer.py`` records *why* deep mode did not contribute, in
``summary['cpsat_failure']``. **Eight** assignments feed it — not six —
in ``_cpsat_optimize`` at lines 1338, 1341, 1392, 1399, 1403, 1409, 1413 and
1416, plus the ``= None`` reset at line 592; the summary exposes it at line 710.
Nothing anywhere in ``scheduler_app/`` reads the key back. The only other
occurrences in the repo are in ``tests/test_cpsat_subprocess_boundary.py``,
which greps the optimizer's own *source text* and therefore cannot notice that
no consumer exists.

Two of the eight are unreachable, and that matters
--------------------------------------------------
``import_failed`` (1338) and ``ortools_missing`` (1341) fire only when
``HAS_ORTOOLS`` is false. But ``ui/app.py`` line 3731 reads that **same**
module attribute before opening the dialog and passes it to
``RescheduleDialog(has_ortools=...)``, which at ``ui/dialogs.py`` line 4365
renders the Thorough button *only* in the true branch and otherwise shows
``optimization.deep_unavailable`` — "Thorough mode is unavailable in this
installation (OR-Tools is not installed)." Same process, same cached import, so
a user who cannot have CP-SAT is never offered it and is told why up front.
``test_thorough_is_not_offered_when_ortools_is_absent`` pins that down.

The consequence for a fix: the one existing catalogue key that looks like it
already says the right thing describes the only case that cannot happen. The
six reachable sites all fire with OR-Tools installed and the button pressed —
the child process crashed, timed out, or returned nothing — and "OR-Tools is
not installed" would be a lie about every one of them.

Why the sibling signals do not cover the reachable six
------------------------------------------------------
Phase 8 killed a similar claim because three distinct signals already existed,
so each candidate was checked here:

``summary['cpsat_used']``
    read twice — ``ui/app.py`` line 3909 (the result toast) and
    ``core/explanation_engine.py`` line 365 (the results-dialog "engine" line).
    Both are ``if summary.get("cpsat_used"):`` and both *append* a
    ``+ CP-SAT (status)`` clause. On a fallback ``cpsat_used`` is ``False``, so
    both take the empty branch and emit exactly the string a Quick solve emits.
    The flag says "CP-SAT contributed"; it cannot say "you asked and it did
    not".

``summary['cpsat_status']`` / ``['cpsat_status_label']``
    both ``None`` on every fallback — they are filled from ``cpsat_info``,
    which exists only when the subprocess returned a result.

``summary['deterministic']``
    ``(not clock_capped) and (not cpsat_used)``. On a fallback ``cpsat_used`` is
    ``False``, so this is ``True`` and ``_reproducibility_note`` (``ui/app.py``
    line 4395) returns ``""``. The one signal that *does* have a renderer is
    switched off by the very failure it would need to describe.

So the observable output of "Thorough, and the solver failed" is byte-identical
to "Quick". That equality is what these tests assert, rather than any particular
wording: the probe must go green under any fix that reaches the user, and must
not canonize a string.

Reachable failure modes exercised
---------------------------------
Four of the six, all instant. ``monkeypatch`` cannot cross a spawn boundary, so
``multiprocessing`` is swapped *inside* ``schedule_optimizer`` for a namespace
whose ``Process``/``Queue`` reproduce what a broken child leaves behind.
Everything downstream of that — the branch, the flag, the summary, the
explanation, the toast — is the real code path.

==================  ====  =========================================
site                line  simulated by
==================  ====  =========================================
subprocess_exit_-11 1399  child exits on SIGSEGV, the native crash
                          the docstring at 1396 names
no_result           1403  child exits 0 and posts nothing
unavailable         1413  child posts ``None``
INFEASIBLE          1416  child posts a non-``ok`` status
==================  ====  =========================================

``timeout`` (1392) is not exercised: its guard is
``time.time() < self.cpsat_time_limit + 30``, so forcing it needs either a
30 s test or a fake clock, and it joins the identical ``return None`` path one
line later. ``import_failed``/``ortools_missing`` are covered by the two
evidence tests below rather than as user-facing reproductions, because the
dialog gate makes them unreachable.

Nothing here sets ``_cpsat_failure`` or any summary key by hand.
"""
import os
import queue
import re
import types

import pytest

pytestmark = [pytest.mark.ui]

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG = os.path.join(_REPO_ROOT, "scheduler_app")
_OPTIMIZER = os.path.join(_PKG, "core", "schedule_optimizer.py")

# Small enough that a full reschedule is ~0.1 s, big enough that the optimizer
# actually runs its multi-start / LNS path and produces a real summary.
_DATASET = dict(n_days=5, n_slots=6, n_rooms=4, n_lecturers=5,
                n_years=2, branches_per_year=1, n_classes=12, seed=7)
_BUDGET = dict(multi_start_runs=1, lns_iterations=5,
               multi_start_time_limit=5.0, parallel_workers=-1)

_NUM = re.compile(r"[\d.,]+")
_UNSET = object()

# Every reachable site, and the exact `cpsat_failure` value production writes.
_REACHABLE = [
    ("subprocess_crash", "subprocess_exit_-11"),
    ("no_result", "no_result"),
    ("unavailable", "unavailable"),
    ("bad_status", "INFEASIBLE"),
]


def _normalized(messages):
    """Message texts with every number blanked.

    The two solves being compared differ in wall-clock time by a few tens of
    milliseconds, which reaches the toast through ``{elapsed:.1f}s``. Blanking
    digits keeps every *word* the user reads while removing the one difference
    that is not about the engine.
    """
    return sorted(_NUM.sub("#", str(m)) for m in messages)


def _broken_multiprocessing(exitcode, payload=_UNSET):
    """A stand-in for the ``multiprocessing`` module `_cpsat_optimize` uses.

    The optimizer touches exactly two names on it, ``Queue`` and ``Process``,
    and then interrogates the finished child through ``is_alive`` /
    ``exitcode`` / the queue. Reproducing that surface is enough to drive the
    real failure branches without a real subprocess.
    """

    class _Process:
        def __init__(self, *args, **kwargs):
            self.exitcode = exitcode

        def start(self):
            pass

        def is_alive(self):
            return False  # already dead by the time the poll loop is reached

        def join(self, timeout=None):
            pass

        def kill(self):
            pass

    def _Queue():
        q = queue.Queue()
        if payload is not _UNSET:
            q.put(payload)
        return q

    return types.SimpleNamespace(Process=_Process, Queue=_Queue)


def _force_cpsat_failure(monkeypatch, mode):
    """Break CP-SAT the way production breaks, without touching the flag."""
    import scheduler_app.core.schedule_optimizer as so

    if mode == "ortools_missing":
        import scheduler_app.core.cpsat_scheduler as cps
        # `_cpsat_optimize` does `from scheduler_app.cpsat_scheduler import
        # HAS_ORTOOLS` at call time, and `scheduler_app/__init__.py` aliases
        # that name onto this very module object, so the attribute is what the
        # production import reads.
        monkeypatch.setattr(cps, "HAS_ORTOOLS", False)
        return
    shim = {
        "subprocess_crash": lambda: _broken_multiprocessing(-11),
        "no_result": lambda: _broken_multiprocessing(0),
        "unavailable": lambda: _broken_multiprocessing(0, None),
        "bad_status": lambda: _broken_multiprocessing(0, {"status": "INFEASIBLE"}),
    }.get(mode)
    assert shim is not None, "unknown mode %r" % (mode,)
    monkeypatch.setattr(so, "multiprocessing", shim())


def _solve(make_state, use_cpsat):
    from scheduler_app.core.workflow import SchedulingWorkflow

    state = make_state(**_DATASET)
    workflow = SchedulingWorkflow(state, lambda: {})
    return state, workflow.reschedule({}, use_cpsat=use_cpsat, **_BUDGET)


# ── 1. Evidence: the engine half works, and two sites are unreachable ───────

@pytest.mark.parametrize("mode, expected", _REACHABLE + [
    ("ortools_missing", "ortools_missing")])
def test_summary_records_why_thorough_mode_did_not_run(
        make_state, monkeypatch, mode, expected):
    """Evidence, not the defect: the engine half of this works today.

    Passes on the current tree on purpose. It pins down that the information a
    fix needs is already present and correct at every site, so the remaining
    tests are about the missing consumer and nothing else.
    """
    _force_cpsat_failure(monkeypatch, mode)
    _state, result = _solve(make_state, use_cpsat=True)

    assert result.summary.get("cpsat_used") is False
    assert result.summary.get("cpsat_failure") == expected, (
        "the optimizer no longer records this fallback reason; the rest of "
        "this module would be measuring the wrong thing. Got %r"
        % (result.summary.get("cpsat_failure"),))
    # The one sibling signal with a renderer is switched off by this failure.
    assert result.summary.get("deterministic") is True
    assert result.summary.get("cpsat_status") is None
    assert result.summary.get("cpsat_status_label") is None


def test_thorough_is_not_offered_when_ortools_is_absent(make_app):
    """Evidence: the ``ortools_missing`` / ``import_failed`` sites cannot fire.

    Passes today. It is here so a fix does not spend a translation key on
    "OR-Tools is not installed" for a solve that the dialog already refuses to
    start — and so that, if this gate is ever removed, the accounting above
    fails loudly instead of silently going stale.
    """
    from PyQt6.QtWidgets import QLabel, QPushButton
    from scheduler_app.translations import tr
    from scheduler_app.ui.dialogs import RescheduleDialog

    window = make_app()
    dlg = RescheduleDialog(window, has_ortools=False)
    try:
        buttons = [b.text() for b in dlg.findChildren(QPushButton)]
        labels = [l.text() for l in dlg.findChildren(QLabel)]
        assert tr("optimization.deep_cpsat") not in buttons, (
            "the Thorough button is offered without OR-Tools; the two import "
            "sites become reachable and this module's accounting is wrong")
        assert tr("optimization.deep_unavailable") in labels
    finally:
        dlg.deleteLater()


# ── 2. The defect: nothing carries the reason to the user ───────────────────

def test_cpsat_failure_is_read_somewhere_outside_the_optimizer():
    """Some module other than the writer must consume the key.

    A source scan rather than a behaviour check because "nobody reads this" is
    itself a source-level property, and because it names, for a fix, every file
    that would satisfy it. The behavioural consequence is asserted below.
    """
    readers = []
    for dirpath, _dirnames, filenames in os.walk(_PKG):
        if "__pycache__" in dirpath:
            continue
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            if os.path.abspath(path) == os.path.abspath(_OPTIMIZER):
                continue  # the writer
            with open(path, encoding="utf-8") as handle:
                if "cpsat_failure" in handle.read():
                    readers.append(os.path.relpath(path, _REPO_ROOT))

    assert readers, (
        "summary['cpsat_failure'] is written at 8 sites in "
        "core/schedule_optimizer.py and appears in no other module under "
        "scheduler_app/. The user asked for the thorough engine, got the "
        "greedy fallback, and nothing carries the reason to them.")


@pytest.mark.parametrize("mode, reason", _REACHABLE)
def test_engine_line_distinguishes_a_fallback_from_a_quick_solve(
        make_state, monkeypatch, mode, reason):
    """The results dialog's "engine" sentence must not lie by omission.

    ``ExplanationEngine._engine_description`` is what ``BulkResultsDialog``
    renders beside ``labels.engine`` (``ui/dialogs.py`` line 4074). Compared
    against a genuine Quick solve on the same dataset and seed, so the only
    thing that can differ is the engine reporting.
    """
    _state, quick = _solve(make_state, use_cpsat=False)

    _force_cpsat_failure(monkeypatch, mode)
    _state2, fell_back = _solve(make_state, use_cpsat=True)

    assert fell_back.summary.get("cpsat_failure") == reason
    quick_line = _NUM.sub("#", (quick.explanation or {}).get("engine", ""))
    fallback_line = _NUM.sub("#", (fell_back.explanation or {}).get("engine", ""))

    assert fallback_line != quick_line, (
        "a Thorough solve whose solver failed (cpsat_failure=%r) describes its "
        "engine with exactly the string a Quick solve uses: %r. The user chose "
        "the slow, thorough engine, silently got the fast one, and the only "
        "sentence on screen that names an engine agrees with the wrong answer."
        % (reason, fallback_line))


@pytest.mark.parametrize("mode, reason", _REACHABLE)
def test_user_is_told_when_thorough_mode_fell_back(
        make_app, make_state, monkeypatch, mode, reason):
    """Every message a user receives, for a fallback vs. a Quick solve.

    Drives the production renderer ``SchedulerApp._on_solve_finished`` over a
    ``RescheduleResult`` the production optimizer actually produced. The only
    things stubbed are the modal (``BulkResultsDialog.exec`` cannot run
    unattended) and the recorder on ``_show_toast``; no summary key, and no
    message, is planted.
    """
    from PyQt6.QtWidgets import QDialog
    import scheduler_app.ui.app as app_mod

    class _AcceptingDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, *args, **kwargs):
            self.result = True

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(app_mod, "BulkResultsDialog", _AcceptingDialog)

    def _run(use_cpsat, break_cpsat):
        window = make_app()
        seen = []
        # Record at `_show_toast`, which is also the funnel into `warning_log`
        # (`ui/app.py`: `Toast(...)` then `self.warning_log.log(...)`), so this
        # captures the toast and the warning-log line in one place. The real
        # method is never called: `Toast` is a live widget and the log is a
        # sticky panel, neither of which this test needs.
        monkeypatch.setattr(type(window), "_show_toast",
                            lambda self, message, kind="info":
                            seen.append((message, kind)))

        state = make_state(**_DATASET)
        window.state_data = state
        window._workflow.state = state

        with monkeypatch.context() as inner:
            if break_cpsat:
                _force_cpsat_failure(inner, mode)
            result = window._workflow.reschedule(
                {}, use_cpsat=use_cpsat, **_BUDGET)

        window._solve_snapshots = None
        window._on_solve_finished(result)
        return result, [text for text, _kind in seen]

    quick_result, quick_msgs = _run(use_cpsat=False, break_cpsat=False)
    deep_result, deep_msgs = _run(use_cpsat=True, break_cpsat=True)

    assert deep_result.summary.get("cpsat_failure") == reason, (
        "the intended failure site was not the one reached; expected %r got %r"
        % (reason, deep_result.summary.get("cpsat_failure")))
    assert deep_result.summary.get("cpsat_used") is False
    assert quick_result.summary.get("cpsat_failure") is None

    assert _normalized(deep_msgs) != _normalized(quick_msgs), (
        "after a Thorough solve that fell back to greedy (cpsat_failure=%r), "
        "the user receives exactly the messages a Quick solve produces:\n"
        "  %s\n"
        "There is no toast, no warning-log line and no dialog text by which a "
        "user could tell that the engine they chose never ran."
        % (reason, "\n  ".join(deep_msgs) or "(nothing)"))
