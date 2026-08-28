"""The god object must stop growing — ST-ARCH-005, a ceiling that may only fall.

Why a ratchet on complexity, and not on the metric the finding names
--------------------------------------------------------------------
ST-ARCH-005 is filed against ``ui/app.py``'s radon Maintainability Index of
**0.00**. That number cannot be a target. Decomposing radon's own formula for
this file gives ``171 - 5.2*lnV - 0.23*C - 16.2*lnSLOC + comments``, and the
complexity term alone is about **-205**, which already exceeds the 171
constant: holding total complexity where it is, *no* value of SLOC produces an
MI above zero. Phase 7's measurement round built all six plausible extractions
(``SessionStore``, ``SelectionAndPanels``, ``ChromeAndHelp``, ``IOSurface``,
``SolveOrchestration``, ``MutationCommands``/``DragAndDrop``) and the residual
``app.py`` came back at MI 0.00 six times out of six. An MI ratchet would be a
constant, and a line-count ratchet is satisfied by moving ``_tutorial_steps``
(116 lines, complexity 1, pure data) somewhere else.

So this file ratchets the term that actually drives it, and that nothing was
watching. Measured with this module's own counter across the remediation:

    ui/app.py                       audit 65de83a   Phase 6 b6c453b   today
      module total complexity             885             878          915
      SchedulerApp methods                119             145          154
      SchedulerApp total complexity       738             793          830

The module total is roughly flat because Phase 6 deleted the 574-line
``_write_excel``. **The class grew by 55 across six phases of remediation and
by another 37 in Phase 7 alone**, and its method count is up 35 from the audit.
That is the shape of the finding, and every one of those numbers went up with
no test in the suite that could notice.

Why the number here is not radon's
----------------------------------
radon reports 893 for ``app.py`` where this module reports 915. Neither is
wrong; McCabe counting has no single canonical decision-point set. This module
pins **what it computes itself**, with the set spelled out in ``BRANCHING``
below, so the ceiling can never drift because a dependency changed its mind —
and so the ratchet needs no dependency at all. It parses source with ``ast``
and never imports ``scheduler_app``, exactly like ``test_import_layering.py``.

What is deliberately NOT here
-----------------------------
The number worth moving most is **statement coverage of ``ui/app.py``**. That
ratchet needs ``coverage`` declared in ``requirements-dev.txt`` and the CI step
at ``.github/workflows/ci.yml:172`` wrapped in ``coverage run``; neither file is
in this change's scope. A coverage floor that skips when the data file is absent
is not a ratchet, it is a test that always passes, so none is written here.

The measurement is recorded here instead, so whoever owns those two files can
set the floor without re-running the lane. ``coverage run --source=scheduler_app
-m pytest -m "not slow"`` on this tree — 859 passed, 20 deselected, 9 xfailed,
478 s, coverage.py 7.15.4:

    ui/app.py       **54.0%**  (3 015 statements, 1 388 never executed)
    ui/dialogs.py   **27.7%**  (3 184 statements, 2 303 never executed)
    ui/renderer.py    64.7%    ui/bug_report.py 56.6%
    ui/tier_enforcement.py 33.7%    ui/first_run.py 25.4%
    ui/tutorial.py   **0.0%**  (193 statements, none)
    whole package     60.6%  (17 079 statements, 6 725 missed)

``app.py`` was 47.7% at Phase 6; Phase 7's new test modules moved it 6.3 points
without any extraction. Floors of 50.0 / 25.0 would sit at the same headroom
this file's ceilings do.

The rule
--------
Every ceiling below may go **down**. Raising one is a deliberate act and needs a
sentence in the commit message saying why — the same contract as
``MAX_UPWARD_IMPORT_PAIRS`` in ``test_import_layering.py`` and
``MAX_MISSING_LOCALE_KEY_PAIRS`` in ``test_translation_coverage.py``.

Findings guarded here: ST-ARCH-005.
"""
import ast
import os

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PY = os.path.join(REPO, "scheduler_app", "ui", "app.py")
DIALOGS_PY = os.path.join(REPO, "scheduler_app", "ui", "dialogs.py")


# ── measured ceilings ───────────────────────────────────────────────────────
# Measured by this file's own counter on the tree at the commit that
# introduced it. They may go DOWN.

MAX_APP_PY_TOTAL_MCCABE = 915
"""Sum of every function's cyclomatic complexity in `ui/app.py`. 885 -> 915.

Was 885 at the audit (65de83a) and 878 after Phase 6, which is flat only
because Phase 6 deleted `_write_excel` (574 lines, the second Excel engine of
ST-ARCH-003). Phase 7's release work put it at 915.
"""

MAX_DIALOGS_PY_TOTAL_MCCABE = 914
"""The same sum for `ui/dialogs.py`. 882 -> 914.

`dialogs.py` is here because ST-ARCH-005 names the wrong file as the worst
one. Measured: it carries essentially the same total complexity as `app.py`,
holds the codebase's only F-band function (`AddClassDialog.__init__`,
complexity 46 by this counter), and is **27%** covered against `app.py`'s 48%.
It is also where every piece of user data is typed. Left un-ratcheted it is
where the complexity `app.py` is not allowed to gain would quietly go.
"""

MAX_SCHEDULERAPP_METHODS = 154
"""Methods defined directly on `SchedulerApp`. 119 -> 145 -> 154.

The register records "~135 methods" at the audit; measured it was 119. This is
the cheapest signal that the god object is being fed: a new method on this
class is a new piece of behaviour that no seam and no test is being asked for.
"""

MAX_SCHEDULERAPP_TOTAL_MCCABE = 830
"""Complexity held by `SchedulerApp` itself. 738 -> 793 -> 830.

This is the number the finding is actually about, and the one that moved most:
**+55 across the six remediation phases and +37 in Phase 7**, while the module
total stayed flat because deletions elsewhere in the file masked it. A ceiling
on the module alone would have shown nothing.
"""

BANKED_HEADROOM = 20
"""How far a ceiling may sit above reality before it must be lowered.

A ratchet with no lower bound gives ground back silently: someone simplifies a
method, nobody lowers the constant, and the next change spends the slack. A
ratchet pinned to the exact measurement instead goes red on every commit that
deletes an `if`, which is how a ratchet gets deleted. So: drift up to this many
points is ordinary churn, and more than this is a real reduction that must be
banked in the same commit that earned it. Applied to the two four-digit
complexity ceilings; the method counts use `BANKED_HEADROOM_METHODS`.
"""

BANKED_HEADROOM_METHODS = 3


# ── the counter ─────────────────────────────────────────────────────────────

BRANCHING = (
    ast.If,            # `if` and every `elif`, which is a nested If
    ast.IfExp,         # a ternary
    ast.For, ast.AsyncFor, ast.While,
    ast.ExceptHandler,  # one per `except` clause; `else`/`finally` are not
    ast.Assert,
    ast.comprehension,  # one per `for` in a comprehension
)
"""The decision points this module counts. `with` is deliberately absent: it is
not a branch. `BoolOp` and `Match` are handled separately below because their
contribution depends on how many operands / cases they carry."""

_NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _own_complexity(fn):
    """Cyclomatic complexity of *fn*, excluding functions nested inside it.

    The exclusion matters: `ast.walk` would count a nested helper's branches
    once here and once again when the helper is visited in its own right, so a
    naive walker inflates `app.py` by 5 and moves the number whenever someone
    refactors a closure without changing any logic.
    """
    n = 1
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, _NESTED):
            continue
        if isinstance(node, BRANCHING):
            n += 1
        elif isinstance(node, ast.BoolOp):
            n += len(node.values) - 1
        elif isinstance(node, ast.Match):
            n += len(node.cases)
        stack.extend(ast.iter_child_nodes(node))
    return n


def _parse(path):
    return ast.parse(open(path, encoding="utf-8").read())


def _functions(tree):
    return [n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _module_total(path):
    return sum(_own_complexity(f) for f in _functions(_parse(path)))


def _scheduler_app_methods():
    for node in _parse(APP_PY).body:
        if isinstance(node, ast.ClassDef) and node.name == "SchedulerApp":
            return [n for n in node.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    raise AssertionError(
        "ui/app.py no longer defines SchedulerApp at module scope; every "
        "ceiling in this file that mentions the class is now vacuous")


def _ratchet(label, measured, ceiling, headroom, constant, worst=()):
    """Assert the two halves of a ratchet, and say what to do about each."""
    listing = "".join("\n    %4d  %s (line %d)" % w for w in worst)
    assert measured <= ceiling, (
        "%s rose to %d (ceiling %d).%s\n"
        "This is ST-ARCH-005 growing. Either take the new branching out of "
        "ui/, or raise %s deliberately and say in the commit message why the "
        "god object had to get bigger."
        % (label, measured, ceiling, listing, constant))
    assert measured >= ceiling - headroom, (
        "%s fell to %d, more than %d below the ceiling of %d. Lower %s to %d "
        "in this commit, so the ground gained cannot be spent by the next one."
        % (label, measured, headroom, ceiling, constant, measured))


# ── the contracts ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("path,ceiling,constant", [
    ("scheduler_app/ui/app.py", MAX_APP_PY_TOTAL_MCCABE,
     "MAX_APP_PY_TOTAL_MCCABE"),
    ("scheduler_app/ui/dialogs.py", MAX_DIALOGS_PY_TOTAL_MCCABE,
     "MAX_DIALOGS_PY_TOTAL_MCCABE"),
])
def test_the_ui_god_objects_do_not_get_more_complex(path, ceiling, constant):
    """ST-ARCH-005 — total cyclomatic complexity of the two biggest UI modules.

    A failure upward means somebody added branching to a file that is already
    at radon MI 0.00 and under 50% covered, and no test in the suite would
    otherwise have said so: six phases of remediation moved this number with
    nothing watching.
    """
    full = os.path.join(REPO, path)
    tree = _parse(full)
    fns = _functions(tree)
    worst = sorted(((_own_complexity(f), f.name, f.lineno) for f in fns),
                   reverse=True)[:5]
    _ratchet("total McCabe complexity of %s" % path,
             sum(_own_complexity(f) for f in fns), ceiling,
             BANKED_HEADROOM, constant, worst)


def test_the_scheduler_app_class_does_not_get_more_complex():
    """ST-ARCH-005 — the class itself, which is where the growth actually was.

    Measured across the remediation: 738 -> 793 -> 830 complexity, while
    `app.py`'s module total went 885 -> 878 -> 915 because Phase 6's deletion
    of `_write_excel` masked the class's rise. Ratcheting the module alone
    would have shown nothing, which is why both are here.

    A failure upward is not automatically wrong — some behaviour has to live
    somewhere. It is a prompt to ask whether it belongs on the window at all,
    and if it does, to raise the constant on purpose.
    """
    methods = _scheduler_app_methods()
    worst = sorted(((_own_complexity(m), m.name, m.lineno) for m in methods),
                   reverse=True)[:5]
    _ratchet("SchedulerApp's total McCabe complexity",
             sum(_own_complexity(m) for m in methods),
             MAX_SCHEDULERAPP_TOTAL_MCCABE, BANKED_HEADROOM,
             "MAX_SCHEDULERAPP_TOTAL_MCCABE", worst)


def test_the_scheduler_app_class_does_not_gain_methods():
    """ST-ARCH-005 — the method count, 119 -> 145 -> 154.

    Separate from the complexity ceiling above on purpose. The two do not
    subsume each other in the direction that matters: a commit that adds three
    methods while simplifying an existing one can leave total complexity flat,
    and the god object still grew by three pieces of behaviour that no seam and
    no test was asked for. Measured, adding a method moves this by 1 and total
    complexity by only 1, so the complexity ceiling alone is a weak signal for
    exactly the change this finding is about.
    """
    methods = _scheduler_app_methods()
    _ratchet("the number of methods on SchedulerApp", len(methods),
             MAX_SCHEDULERAPP_METHODS, BANKED_HEADROOM_METHODS,
             "MAX_SCHEDULERAPP_METHODS")


# ── anti-vacuity ────────────────────────────────────────────────────────────

def test_the_counter_agrees_with_a_hand_computed_function():
    """Anti-vacuity: a metric test whose metric is wrong is a constant.

    Every ceiling above is only as good as `_own_complexity`. If it returned 1
    for everything, all four contracts would pass while measuring nothing —
    the failure mode Phase 5 shipped four times over.

    The sample below has, by hand: `if` +1, `elif` +1, `for` +1, `while` +1,
    two `except` clauses +2, one comprehension +1, `a and b` +1, `... or ...`
    +1, on a base of 1 = **10**.
    """
    sample = (
        "def f(a, b):\n"
        "    if a:\n"
        "        pass\n"
        "    elif b:\n"
        "        pass\n"
        "    for x in range(3):\n"
        "        while x:\n"
        "            break\n"
        "    try:\n"
        "        pass\n"
        "    except ValueError:\n"
        "        pass\n"
        "    except KeyError:\n"
        "        pass\n"
        "    y = [z for z in a if z]\n"
        "    return (a and b) or not a\n")
    assert _own_complexity(ast.parse(sample).body[0]) == 10

    straight_line = "def g():\n    x = 1\n    with open('f') as h:\n        return h\n"
    assert _own_complexity(ast.parse(straight_line).body[0]) == 1, (
        "a function with no branch must score 1; `with` is not a branch")

    nested = ("def outer(a):\n"
              "    def inner(b):\n"
              "        if b:\n"
              "            return 1\n"
              "        return 2\n"
              "    return inner\n")
    assert _own_complexity(ast.parse(nested).body[0]) == 1, (
        "the nested function's branch was counted against its parent, so "
        "every closure in app.py is being counted twice")


def test_the_counter_is_looking_at_the_real_files():
    """Anti-vacuity: prove the ceilings are measured against real source.

    A typo'd path, a parse that silently returned an empty module, or a
    `SchedulerApp` that stopped being found would all make the numbers above
    trivially satisfiable. Bound loosely on purpose: these are sanity checks,
    not a second ratchet.
    """
    for path in (APP_PY, DIALOGS_PY):
        assert os.path.exists(path), path
        assert len(_functions(_parse(path))) > 100, (
            "%s parsed to fewer than 100 functions" % path)

    methods = _scheduler_app_methods()
    assert len(methods) > 100, (
        "SchedulerApp resolved to %d methods" % len(methods))
    scores = {m.name: _own_complexity(m) for m in methods}
    worst = max(scores.items(), key=lambda kv: kv[1])
    assert worst[1] >= 20, (
        "the most complex method on SchedulerApp measured %d (%s). A counter "
        "stuck near 1 would satisfy every ceiling above while measuring "
        "nothing. No method name is hardcoded here on purpose — a legitimate "
        "deletion must not fail this — but a file at radon MI 0.00 has "
        "something in it above 20." % (worst[1], worst[0]))
    assert sum(scores.values()) < _module_total(APP_PY), (
        "SchedulerApp's complexity is not less than its own module's total, "
        "so one of the two traversals is visiting the wrong nodes")
