"""A test may name a symbol in the shipped code. It may not name a line number.

What this pins
--------------
No file under ``tests/`` may cite a source line by number — no ``app.py`` plus
colon plus digits, in a docstring, a comment or a failure message. Symbol names
only.

Why a whole rule rather than a fix
----------------------------------
Phase 9 filed C9 against one docstring: ``test_validator_unification.py``'s
``_drop_verdict`` said it mirrored ``ui/app.py::_execute_drop`` and gave a line
range beside the name. The range had been accurate to within nine lines when it
was written (779ac7b, Phase 3). ``ui/app.py`` then grew by about 1100 lines, and
by the time anyone read it the range landed in ``_on_solve_finished`` and
``_remove_classes`` — two methods with nothing to do with dragging. The helper's
whole claim is "phase for phase", and the pointer offered to check it against
was **off by 1124 lines**.

Fixing that one docstring would have been the wrong repair, and measuring said
so. Counted across the suite on 2026-08-29 at 42e1943, the tracked test files
carried **158 such citations in 25 files** (149 in the plain ``file.py:NNN``
form, 9 more in the detached form C9 used). Of the fifty whose surrounding prose
also named a function — the ones a machine can grade, because the citation is
right exactly when the cited line falls inside the named function — **forty-one
pointed outside it**, twenty-five of those into ``app.py``. Two more measured
here while removing them: ``storage.py``'s append-conversion branch was cited
about 200 lines short of ``append_encrypted_entry``, and ``ui/renderer.py``'s
0.50 sequential-block factor was cited into ``_paint_joint`` when the call is in
``_paint_sequential``.

The nine that were still correct were all in modules Phase 9 barely touched.
That is the tell: the number is not wrong because an author was careless, it is
wrong because an unrelated commit above it moved it. Almost every one of the
158 already carried the function name next to the number, and the name survives
every refactor that keeps the function. So the number was never the half doing
the work, and correcting all 158 buys a suite that is stale again after the next
``ui/app.py`` refactor. They were deleted instead; where the prose named no
symbol, the owning one was resolved with ``ast`` and written in.

``tests/test_drag_and_drop.py`` had already reached the same conclusion for
itself one pass earlier and written it down — and then kept two references
"because both were re-measured and are exact". Those two are gone now as well.
"Exact today" is the state all 158 were in when they were written; it is a
description of the day, not a property of the citation. That is precisely why
this is a test and not a convention.

Scope, and what is deliberately outside it
------------------------------------------
Matched: a Python module name followed by a colon and digits — ``app.py:2150``,
``core/models.py:557-558``, the same thing wrapped in reST double backticks,
and the detached form that hangs the range off the symbol instead of the
filename, which is the form C9 itself used. Those shapes are unambiguous and
have no legitimate use in this suite.

Not matched: prose like "at line 196" or "lines 101-108". The suite carried
21 of those; 13 were removed by hand with the 158 and 8 were kept deliberately.
Two of the survivors are in ``test_release_pipeline.py``, which cites lines of a
file inside a **named git object** (``git show 980887c^:...``). Those cannot
rot: the commit is immutable, so the line numbers are as permanent as the bytes
they index. A
regex for the prose form would fire on it, and on "95 lines, 29 points", and on
the ``File "...", line 88`` traceback fixtures in
``test_report_redaction.py`` — false alarms teach people to add suppressions,
so the prose form is left to review.

Pure text plus ``ast``. No ``scheduler_app`` import, no Qt, so this runs in the
headless job as well.
"""
import ast
import os
import re

TESTS = os.path.dirname(os.path.abspath(__file__))
SELF = os.path.basename(__file__)

# Two shapes, both measured in this suite.
#
# 1. ``pkg/mod.py:123`` / ``mod.py:123-456``, with reST double backticks allowed
#    between the extension and the colon — ``ui/renderer.py``:2052 was written
#    that way and a regex anchored on ``.py:`` walks straight past it.
# 2. A line reference detached from the filename and hung off the symbol
#    instead: ``ui/app.py::_execute_drop`` (:3910-3984), or ``:283-298``. This
#    is the shape C9 itself was written in. The first draft of this module
#    matched only shape 1 and let C9's own docstring through, which is why the
#    sample list below is built from lines the sweep really deleted rather than
#    from what the pattern was assumed to look like.
CITATION = re.compile(r"[A-Za-z_][A-Za-z0-9_/]*\.py`{0,2}:[0-9]+(?:-[0-9]+)?"
                      r"|[(`]:[0-9]+(?:-[0-9]+)?")

# Empty, and it should stay that way. This held one wave-boundary carve-out:
# ``tests/test_phase9_c3.py`` was written by a parallel agent in the same wave
# as the sweep that created this file, so its single citation could not be
# edited from here. That citation was replaced with
# ``core/models.py::get_physical_room_candidates`` as soon as the wave closed,
# and the carve-out went with it. The mechanism is kept rather than deleted
# because the next sweep-versus-parallel-work collision will want it, and
# because an empty tuple states the intended steady state out loud.
PENDING = ()


def _sources():
    """``(filename, text)`` for every test module except this one.

    This module has to spell the pattern out — in the regex, in ``PENDING`` and
    in the examples above — so grading itself would be a permanent false alarm.
    """
    for name in sorted(os.listdir(TESTS)):
        if not name.endswith(".py") or name == SELF:
            continue
        with open(os.path.join(TESTS, name), encoding="utf-8") as fh:
            yield name, fh.read()


def _citations(name, text):
    """Every offending ``file.py:NNN`` in one module, as ``(line, text)``."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        for match in CITATION.finditer(line):
            if (name, match.group(0)) in PENDING:
                continue
            found.append((number, match.group(0)))
    return found


# ── the rule ────────────────────────────────────────────────────────────────

def test_no_test_file_cites_a_source_line_by_number():
    """Symbol names only. A line number is stale the next time anyone commits.

    A failure does not mean the number is wrong yet. It means somebody wrote a
    pointer that goes wrong on its own, silently, in a commit that never touches
    this file — which is how all 149 of its predecessors got that way. Replace
    it with the symbol the same sentence already names:
    ``ui/app.py::_execute_drop``, ``models.py::new_class``,
    ``widgets.py::WarningLogPanel.log``. If the citation names no symbol, find
    the one that owns the line and name that; ``ast`` will tell you in a dozen
    lines, and unlike the number it will still be true next month.
    """
    offenders = [(name, number, cited)
                 for name, text in _sources()
                 for number, cited in _citations(name, text)]

    listing = "\n".join("  %-38s L%-5d %s" % row for row in offenders)
    assert not offenders, (
        "%d line-number citation(s) in tests/. The line number is the only half "
        "of a reference that decays, and it decays without anyone editing the "
        "file that holds it: measured 2026-08-29, 41 of the 50 gradable "
        "citations in this suite pointed outside the function their own "
        "sentence named. Name the symbol instead.\n%s"
        % (len(offenders), listing))


# ── the guard is not a no-op ────────────────────────────────────────────────

def test_the_detector_fires_on_every_shape_that_was_actually_removed():
    """Each sample is a real line deleted by the sweep, verbatim.

    Without this, a regex that had quietly stopped matching would leave the
    module green forever — the failure mode a suite-wide "must not exist" rule
    is most exposed to, because its healthy state and its broken state look
    identical from the outside.
    """
    samples = [
        "Mirrors ``ui/app.py::_execute_drop`` (:3910-3984) phase for phase",
        "    core/logic.py:729   find_conflicting_classes(state, cls)",
        "out by `r in cls['required_classrooms']` (core/models.py:557-558) ",
        "``protection == 'same_day'`` branch (``core/workflow.py``:696-699)",
        "# background: 0.45 joint cell (renderer.py:95), 0.50 sub-block",
        "#   ui/first_run.py:80                      the starter file",
        "one entry — and ``remove_placement`` (``:283-298``) uses ``discard``",
        "(``ui/app.py:4963``/``:5015`` tab-index to export mode)",
    ]
    missed = [s for s in samples if not CITATION.search(s)]
    assert not missed, (
        "the detector no longer recognises %d of the %d shapes this sweep "
        "actually removed, so the rule is unenforced: %r"
        % (len(missed), len(samples), missed))


def test_the_detector_leaves_ordinary_prose_alone():
    """The false alarms that would make people reach for a suppression.

    A version number, a size, a traceback fixture, a git-pinned range and a
    ``module::symbol`` reference must all pass. The last one matters most: it is
    the replacement this module asks for, so matching it would make the rule
    unsatisfiable.
    """
    innocent = [
        "``ui/app.py::_execute_drop`` commits the drop",
        "`OpenSlotsDialog` (95 lines, 29 points), which no code constructed",
        r'  File "...\\scheduler_app\\single_instance.py", line 88',
        "`git show 980887c^:.github/workflows/build-release.yml` lines 101-108",
        "CPython 3.12 inlines the call; see ``0xC0000409``",
        "the ``summary = {`` literal inside ``schedule_optimizer.py::optimize``",
    ]
    fired = [(s, CITATION.search(s).group(0)) for s in innocent
             if CITATION.search(s)]
    assert not fired, (
        "the detector fires on prose that cites no line number. A rule with "
        "false alarms gets suppressed rather than obeyed: %r" % (fired,))


def test_every_test_module_still_parses():
    """The sweep edited 27 modules' docstrings; none may have lost a quote.

    Cheap, and it catches the one way a docs-only change can break a suite: an
    unbalanced triple quote turns the rest of a module into a string and pytest
    collects a file with no tests in it, silently.
    """
    broken = []
    for name, text in _sources():
        try:
            ast.parse(text)
        except SyntaxError as exc:                        # pragma: no cover
            broken.append("%s: %s" % (name, exc))
    assert not broken, "test modules no longer parse: %s" % broken
