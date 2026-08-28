"""The domain model has a written-down shape, and it is the real one.

ST-ARCH-013 (Medium) · ``core/models.py``
    Type-hint coverage was 10.8% overall and ~7% in ``core``, on a codebase
    whose entire domain model is stringly-keyed dicts: an 8-key state dict and
    a 24-field class dict flowing through every solver, where a typo'd key
    fails only at runtime.

What the register proposes, and what it actually buys
-----------------------------------------------------
The remedy is "define TypedDicts for StateDict and ClassDict; run mypy on core".
Both landed. But the finding claims they address a specific failure class -- a
``lecturer_available_at`` KeyError on a malformed availability dict, and an
unimported ``tr()`` NameError -- and **measured, they address neither**:

* a TypedDict does not catch a missing key at *either* totality; mypy has no
  such check, so the KeyError is untouched;
* it is blind to ``.get()``, which is over half of all class-dict reads;
* the cited KeyError is in a **third** dict shape (``new_lecturer_availability``)
  that the proposed remedy never mentions;
* ``[name-defined]`` -- the error code that catches an unimported ``tr()`` --
  needs ``check_untyped_defs``, which costs 168 errors on this tree. Run with
  it, the current count of ``[name-defined]`` errors is **zero**: Phase 3 fixed
  the instance the audit found and there are no others.

So the honest value is narrower than the finding claims, and it is this: the
24-field shape previously existed only as a dict literal, so nothing could
detect drift between what ``new_class()`` writes and what any reader expects.
The tests below make the declaration and the constructor check each other.

Why the obvious stronger test is not here
------------------------------------------
"Every key read anywhere must be in the TypedDict" was built and rejected: a
name-based AST scan cannot tell which *dict* a variable holds. Scanning for
``c[...]`` and ``entry[...]`` across the package returns ``b_idx``, ``row``,
``col``, ``span`` and ``start_si`` -- layout and spreadsheet records, not
classes -- so the test would either be noisy or need a hand-maintained
exclusion list that decays. Distinguishing them needs real type inference,
which is ``check_untyped_defs``, which is a project rather than a test.
"""
import ast
import os

import pytest

from scheduler_app.core.models import (
    ClassDict, StateDict, TargetDict,
    new_class, new_state, new_lecturer_availability,
)

MODELS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scheduler_app", "core", "models.py")


def _annotations(cls):
    """TypedDict fields, including inherited ones."""
    return set(cls.__annotations__)


def test_the_classdict_matches_new_class():
    """ST-ARCH-013 — the declared shape must be the constructed one.

    A failure means someone added or removed a field in ``new_class()`` without
    touching ``ClassDict``, so the only written-down description of a class is
    now wrong. That is exactly the drift the finding is about, and before this
    test nothing could see it.
    """
    built = set(new_class())
    declared = _annotations(ClassDict)
    assert built == declared, (
        "ClassDict and new_class() disagree.\n"
        "  in new_class but not declared: %s\n"
        "  declared but not constructed : %s"
        % (sorted(built - declared), sorted(declared - built)))


def test_the_statedict_matches_new_state():
    """ST-ARCH-013 — same contract for the application state."""
    built = set(new_state())
    declared = _annotations(StateDict)
    assert built == declared, (
        "StateDict and new_state() disagree.\n"
        "  in new_state but not declared: %s\n"
        "  declared but not constructed : %s"
        % (sorted(built - declared), sorted(declared - built)))


def test_a_target_is_a_year_and_a_branch():
    """ST-ARCH-013 — the nested shape the solvers iterate constantly."""
    assert _annotations(TargetDict) == {"year", "branch"}


def test_the_declarations_are_partial_on_purpose():
    """ST-ARCH-013 — anti-vacuity, and a claim the code has to honour.

    ``total=True`` would assert that every key is always present. It is not:
    ``_auto_load`` back-fills ``lecturers`` and ``classroom_capacities`` for
    files predating those features, and ``cls_key`` writes ``class_uid`` on
    *read* for legacy data. Declaring totality we do not have would be a
    stronger-looking claim that is simply false, so pin the weaker true one.
    """
    for shape in (ClassDict, StateDict):
        assert shape.__total__ is False, (
            "%s claims every key is always present; the loader back-fills "
            "missing ones, so that claim is false" % shape.__name__)
    # TargetDict is genuinely total -- a target with no branch is meaningless.
    assert TargetDict.__total__ is True


def test_the_availability_record_is_the_shape_the_finding_actually_names():
    """ST-ARCH-013 — the third dict, which the proposed remedy omits.

    The audit's motivating crash is ``lecturer_available_at`` raising KeyError
    on a malformed availability dict. That record is neither a ClassDict nor a
    StateDict, so neither declaration protects it. What protects it is
    ``get_lecturer_availability`` returning the default record for an unknown
    lecturer -- pin that, since it is the actual guard.
    """
    from scheduler_app.core.models import (
        get_lecturer_availability, lecturer_available_at,
    )
    record = new_lecturer_availability()
    assert set(record) == {
        "allowed_days", "allowed_hours", "excluded_days", "excluded_hours"}

    state = new_state()
    state["days"] = ["monday"]
    state["slots"] = ["09:00"]
    # An unknown lecturer must degrade to "fully available", not raise.
    assert get_lecturer_availability(state, "Nobody") == record
    assert lecturer_available_at(state, "Nobody", "monday", "09:00") is True

    # And a record missing every key must still not raise.
    state["lecturer_availability"] = {"Partial": {}}
    assert lecturer_available_at(state, "Partial", "monday", "09:00") is True


def test_models_declares_the_shapes_before_it_builds_them():
    """ST-ARCH-013 — anti-vacuity: the declarations must be reachable.

    A TypedDict defined after its constructor, or inside a ``TYPE_CHECKING``
    block, would satisfy every assertion above while being invisible to mypy
    and to any reader following the file top to bottom.
    """
    tree = ast.parse(open(MODELS, encoding="utf-8").read())
    order = {}
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            order[node.name] = node.lineno
    for shape, ctor in (("ClassDict", "new_class"), ("StateDict", "new_state")):
        assert shape in order, (
            "%s is not a top-level definition in models.py -- it may be inside "
            "a TYPE_CHECKING block, where mypy sees it and readers do not"
            % shape)
        assert order[shape] < order[ctor], (
            "%s is declared after %s; the shape should be readable before the "
            "literal that implements it" % (shape, ctor))
