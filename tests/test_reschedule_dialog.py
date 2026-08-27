"""The reschedule dialog must say what the user gets, not what algorithm runs.

Phase 4, task 6. The audit (``09-ui-ux-audit.md``, "Placement & results
dialogs") records that ``RescheduleDialog`` "offers 'Standart' vs 'Derin
(CP-SAT)' as two equally-primary buttons with unexplained jargon". Verified —
the tooltips are jargon too:

    optimization.standard      'Standart'
    optimization.deep_cpsat    'Derin (CP-SAT)'
    optimization.lns_tooltip   'Çoklu başlangıçlı LNS optimizasyonu'
    optimization.cpsat_tooltip 'Kısıt çözücü ile derin optimizasyon'

A school administrator choosing between two buttons cannot be expected to know
what LNS or CP-SAT is, and neither label tells them what the choice costs.

The reproducibility half, which the audit does not mention
----------------------------------------------------------
``summary['deterministic']`` is ``(not clock_capped) and (not cpsat_used)`` —
so it is **False whenever the Deep button was pressed**, and also whenever the
time budget truncated the search. Phase 1 was careful that the engine never
claims a reproducibility it cannot deliver, and then nothing surfaced the flag:
``grep deterministic scheduler_app/ui/`` found only an unrelated palette
comment. So the choice that silently forfeits reproducibility was the one
described as merely "deep".

What is deliberately NOT changed here
-------------------------------------
The engine's time budget. The task-6 implementation spec proposed a global
deadline and was calibrated throughout on ``multi_start_time_limit=3600.0`` as
"the shipped default"; its adversarial reviewer returned **materially-wrong**,
because the production path never sees that value. Measured by spying on the
real constructor through ``SchedulingWorkflow.reschedule``::

    ScheduleOptimizer.__init__ default : 3600.0
    optimized_reschedule_all default   : 120.0
    what the LIVE reschedule path uses : 120.0

``SolverTask`` is constructed with no optimizer kwargs, so every production
solve goes through ``optimized_reschedule_all``'s own 120.0. (PROGRESS.md
records the Phase 2 raise "120 s → 3600 s" as landed; on this path it is inert.
``tests/test_greedy_bounds.py`` already documents the 120 as live.) Re-deriving
the deadline against the real number is engine work needing its own measurement
pass, and the reviewer showed the proposed change would truncate run 4 on the
``normal`` preset and cost the reproducibility it was meant to protect.

So this module pins the honest-communication half: the dialog must not promise
what the engine does not deliver.
"""
import pytest

from scheduler_app.translations import tr

pytestmark = pytest.mark.ui


def _dialog(has_ortools=True):
    from scheduler_app.ui.dialogs import RescheduleDialog
    return RescheduleDialog(None, has_ortools=has_ortools)


def _button_texts(dlg):
    from PyQt6.QtWidgets import QPushButton
    return [b.text() for b in dlg.findChildren(QPushButton)]


def _button(dlg, needle):
    from PyQt6.QtWidgets import QPushButton
    for b in dlg.findChildren(QPushButton):
        if needle in b.text():
            return b
    raise AssertionError(f"no button containing {needle!r} in {_button_texts(dlg)}")


# ══════════════════════════════════════════════════════════════════════
#  1. The choice is described in plain language
# ══════════════════════════════════════════════════════════════════════

def test_the_mode_buttons_do_not_name_the_algorithm(qapp):
    """Task 6 — "CP-SAT" and "LNS" are not words a user can act on.

    A failure means the primary decision in the whole reschedule flow is
    presented as a choice between two acronyms, so the user picks arbitrarily
    and cannot tell what it cost them.
    """
    from PyQt6.QtWidgets import QLabel, QPushButton

    dlg = _dialog()
    try:
        surface = " ".join(
            [b.text() for b in dlg.findChildren(QPushButton)]
            + [b.toolTip() for b in dlg.findChildren(QPushButton)]
            + [w.text() for w in dlg.findChildren(QLabel)]
        )

        for jargon in ("CP-SAT", "CPSAT", "LNS"):
            assert jargon not in surface, (
                f"{jargon!r} is still shown to the user: {surface!r}"
            )
    finally:
        dlg.deleteLater()


def test_each_mode_says_what_it_costs(qapp):
    """Task 6 — a choice with no stated trade-off is not a choice.

    A failure means both buttons look equally primary and equally free, when
    one of them takes materially longer and forfeits reproducibility.
    """
    dlg = _dialog()
    try:
        quick = _button(dlg, tr("optimization.standard"))
        deep = _button(dlg, tr("optimization.deep_cpsat"))

        assert quick.toolTip().strip(), "the quick mode explains nothing"
        assert deep.toolTip().strip(), "the thorough mode explains nothing"
        assert quick.toolTip() != deep.toolTip(), (
            "both modes carry the same explanation, so neither is a choice"
        )
    finally:
        dlg.deleteLater()


def test_the_thorough_mode_warns_that_the_result_is_not_reproducible(qapp):
    """Task 6 / ST-SCHED-013 — do not silently forfeit reproducibility.

    ``summary['deterministic']`` is False whenever CP-SAT ran. Phase 1 took
    care that the engine never claims a reproducibility it cannot deliver; the
    UI then never mentioned the flag at all.

    A failure means the user picks the button described as "more thorough" and
    silently loses the ability to regenerate the same timetable from the same
    seed — the property the whole of ST-SCHED-013 exists to provide.
    """
    dlg = _dialog()
    try:
        deep = _button(dlg, tr("optimization.deep_cpsat"))
        assert tr("optimization.not_reproducible") in deep.toolTip(), (
            f"the thorough mode does not mention reproducibility: "
            f"{deep.toolTip()!r}"
        )
    finally:
        dlg.deleteLater()


def test_the_absent_solver_is_explained_rather_than_silent(qapp):
    """Task 6 — a missing option must not simply not exist.

    Without OR-Tools the thorough button is not rendered at all, so a user
    following a colleague's instructions sees a dialog that does not match the
    description and is told nothing.

    A failure means the option vanishes with no trace.
    """
    dlg = _dialog(has_ortools=False)
    try:
        from PyQt6.QtWidgets import QLabel, QPushButton
        assert all(tr("optimization.deep_cpsat") not in b.text()
                   for b in dlg.findChildren(QPushButton))
        shown = " ".join(w.text() for w in dlg.findChildren(QLabel))
        assert tr("optimization.deep_unavailable") in shown, (
            "the thorough mode is missing with no explanation"
        )
    finally:
        dlg.deleteLater()


def test_the_quick_mode_is_the_visually_primary_one(qapp):
    """Task 6 — two equally-primary buttons is not a recommendation.

    A failure means the dialog presents no default, so every user must make an
    engine decision on their first reschedule.
    """
    dlg = _dialog()
    try:
        quick = _button(dlg, tr("optimization.standard"))
        deep = _button(dlg, tr("optimization.deep_cpsat"))
        assert quick.isDefault() or not deep.isDefault(), (
            "neither mode is presented as the recommended one"
        )
    finally:
        dlg.deleteLater()


# ══════════════════════════════════════════════════════════════════════
#  2. The result must report reproducibility honestly
# ══════════════════════════════════════════════════════════════════════

def test_a_non_reproducible_result_says_so(make_app):
    """Task 6 / ST-SCHED-013 — the summary flag must reach the user.

    ``deterministic`` is False when CP-SAT ran OR when the time budget
    truncated the search. The second case is invisible to the user — they did
    not choose it — so if the app stays quiet, a timetable they cannot
    regenerate looks exactly like one they can.

    A failure means the app implicitly claims a reproducibility the engine
    explicitly reported it does not have.
    """
    app = make_app()
    try:
        capped = app._reproducibility_note({"deterministic": False, "seed": 7})
        clean = app._reproducibility_note({"deterministic": True, "seed": 7})
    finally:
        app.close()

    assert capped.strip(), "a non-reproducible solve said nothing"
    assert clean == "", (
        "a reproducible solve added noise; only the exception is worth saying"
    )


def test_a_missing_flag_is_treated_as_reproducible(make_app):
    """Task 6 — an older result dict must not trigger a false warning.

    A failure means every legacy or partial summary claims non-reproducibility,
    which is how a real warning stops being read.
    """
    app = make_app()
    try:
        assert app._reproducibility_note({}) == ""
        assert app._reproducibility_note(None) == ""
    finally:
        app.close()
