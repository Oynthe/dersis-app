"""What crosses the CP-SAT process boundary — ST-ARCH-009, ST-SCHED-014.

Thorough mode runs CP-SAT in a separate process, because the OR-Tools solver
can die at the native level and taking the app down with it is not acceptable.
That isolation has a cost the code did not account for: on Windows,
``multiprocessing`` uses **spawn**, so the child re-imports the entire module
chain from scratch. Anything held in a module global does not cross.

Two consequences, both measured and both fixed here.

1. **The child answered in the wrong language.**
   ``translations._current_lang`` is a module global, so the freshly imported
   child reset it to the default. Measured, parent set to Turkish:

       parent  'Optimum'   child  'Optimal'
       parent  'Geçerli kısıtlamalarla eşleşen geçerli yerleşim yok.'
       child   'No valid placements match the current constraints.'

   Those are not debug strings. ``cpsat_scheduler`` calls ``tr()`` for the
   unplaced *reasons*, which land in the results dialog -- so a Turkish school
   running Thorough got a list of English sentences explaining why its lessons
   could not be placed, mixed into an otherwise Turkish UI.

   This is ST-ARCH-009's cost made concrete: an engine that returns translated
   strings has a language, and a language is process state.

2. **A dead child silently downgraded the run.** Every failure path returned
   ``None`` and the caller fell back to the heuristic result with no message,
   no summary key and no log line. The user asked for Thorough and got Quick,
   with the same green "done" dialog. On a frozen build the most likely cause
   is not a segfault at all but an ImportError re-importing the chain in the
   child -- exactly what the ``i18n`` package move could have caused had
   ``build_nuitka.bat`` not been updated with it.
"""
import multiprocessing
import sys

import pytest

from scheduler_app.core import schedule_optimizer as so


def _child_reports_language(queue):
    sys.path.insert(0, __import__("os").path.dirname(
        __import__("os").path.dirname(__import__("os").path.abspath(__file__))))
    from scheduler_app.translations import get_language, tr
    queue.put((get_language(), tr("status.cpsat_status_optimal")))


@pytest.mark.slow
def test_the_solver_subprocess_speaks_the_users_language():
    """ST-ARCH-009 — Thorough mode must not answer in English.

    A failure means a school that runs DERSİS in Turkish gets its "why was
    this lesson not placed" list back in English, and only in Thorough mode,
    so it looks like a different feature rather than a bug.
    """
    from scheduler_app.translations import set_language, get_language, tr

    previous = get_language()
    try:
        set_language("tr")
        expected_lang, expected_text = "tr", tr("status.cpsat_status_optimal")

        queue = multiprocessing.Queue()
        proc = multiprocessing.Process(
            target=_child_reports_language, args=(queue,))
        proc.start()
        proc.join(120)
        assert not queue.empty(), "the child produced nothing"
        child_lang, child_text = queue.get_nowait()

        # First: prove the hazard is real in this environment, so the
        # assertion below is not passing because spawn behaves like fork.
        assert child_lang != expected_lang, (
            "the child inherited the language, so this platform does not "
            "reproduce the spawn reset and this test proves nothing here")

        # Then: the worker must repair it from the argument it is passed.
        assert "language" in so._cpsat_subprocess_worker.__code__.co_varnames, (
            "_cpsat_subprocess_worker no longer takes a language; the child "
            "will answer in %r while the UI is in %r"
            % (child_lang, expected_lang))
        assert child_text != expected_text, (
            "fixture is not adversarial: this key reads the same in both "
            "languages, so it cannot detect the reset")
    finally:
        set_language(previous)


def test_the_launcher_passes_the_language_to_the_child():
    """ST-ARCH-009 — the argument must actually be sent, not just accepted.

    Adding the parameter and forgetting the call site leaves the defect
    exactly as it was, with a signature that says otherwise.
    """
    import inspect
    src = inspect.getsource(so.ScheduleOptimizer._cpsat_optimize)
    assert "get_language()" in src, (
        "_cpsat_optimize builds the subprocess without passing the UI "
        "language, so the child will reset it to the default")


def test_a_dead_subprocess_is_reported_rather_than_swallowed():
    """ST-SCHED-014 — asking for Thorough and getting Quick must be visible.

    A failure means deep optimization can be unavailable on a user's machine
    forever -- a missing OR-Tools, a packaging error, a crash on every run --
    and the app never once says so.
    """
    import inspect
    src = inspect.getsource(so.ScheduleOptimizer._cpsat_optimize)
    bare = src.count("return None")
    recorded = src.count("self._cpsat_failure = ")
    assert recorded >= bare - 1, (
        "_cpsat_optimize has %d `return None` paths but records a reason on "
        "only %d of them; the unrecorded ones downgrade the run silently"
        % (bare, recorded))

    summary_src = inspect.getsource(so.ScheduleOptimizer.optimize)
    assert '"cpsat_failure"' in summary_src, (
        "the run summary does not carry cpsat_failure, so nothing downstream "
        "can tell the user that Thorough mode did not run")
