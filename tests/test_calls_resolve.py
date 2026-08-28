"""Every function the shipped code calls must exist — ST-ARCH-011.

Phase 6 deleted the app's original solver family from ``core/logic.py``
(``batch_schedule``, ``auto_place_class``, ``_solve_backtrack``,
``_get_valid_slots``, ``find_conflicting_classes`` and the rest) because it
enforced a weaker rule set than the optimized path. The deletion removed the
definitions and left **three calls to two of them standing**::

    core/logic.py:729   find_conflicting_classes(state, cls, day, slot, room)
    core/logic.py:754   find_conflicting_classes(state, new_cls, day, time_, room)
    core/logic.py:1036  _get_valid_slots(state, cls, room_occ, lect_occ, group_occ)

Nothing noticed for a phase, because Python resolves a global at *call* time
and the three functions holding these calls — ``_find_candidate_slots``,
``cascade_relocate`` and ``_unplaced_reason`` — had no callers either. The
whole cluster was unreachable, so no test and no user ever ran a line of it.

That inverts the usual reading of dead code. Phase 6's lesson was that the
absence of callers is sometimes the *bug* — ``qt_tooltip`` and
``_flush_before_state_swap`` were both fixes someone forgot to wire, and
``tests/test_written_but_unwired.py`` pins them. This cluster is the opposite:
wiring any of it raises ``NameError`` on the first call, so it could never have
been the fix it looked like. Phase 7 deletes it.

Why an AST pass rather than a linter or an import
------------------------------------------------
Importing a module proves nothing here: the three calls sit inside function
bodies that import cleanly and would only fail when run. And a grep for
``def find_conflicting_classes`` answering "no matches" is the question nobody
thought to ask. This walks every shipped module instead and asks the one
question that has a mechanical answer: *is this called name bound anywhere at
all?*

The binding set is deliberately **over-approximate** — every name bound
anywhere in the module counts as bound everywhere in it, so a local in one
function silences the same name in another. That is the right trade. It cannot
flag a working call (no false alarm to suppress, so nobody learns to add an
ignore), and a name bound *nowhere* in the module and absent from builtins is a
guaranteed ``NameError`` the moment the line executes. All three of the above
are exactly that shape; measured on the rest of ``scheduler_app/``, the count
was already zero, so the contract is ``== 0`` for the whole package and not
just for ``core/``.
"""
import ast
import builtins
import os

import pytest

import scheduler_app

PKG_ROOT = os.path.dirname(os.path.abspath(scheduler_app.__file__))

BUILTINS = frozenset(dir(builtins))


def _shipped_modules():
    """Every ``.py`` file that ships inside ``scheduler_app/``."""
    for dirpath, dirs, files in os.walk(PKG_ROOT):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for filename in sorted(files):
            if filename.endswith(".py"):
                path = os.path.join(dirpath, filename)
                yield os.path.relpath(path, PKG_ROOT).replace("\\", "/"), path


def _bound_anywhere(tree):
    """Every name the module binds, by any statement, at any depth.

    Over-approximate on purpose (see the module docstring): a name bound in one
    scope counts for the whole file. Covers the binding forms this codebase
    actually uses — ``def``/``class``, imports, assignment and augmented
    assignment, ``for`` and ``with`` targets, ``except ... as``, comprehension
    variables, the walrus, lambda and function parameters, and ``global`` /
    ``nonlocal`` declarations.
    """
    names = set()

    def _params(args):
        for arg in (list(args.posonlyargs) + list(args.args)
                    + list(args.kwonlyargs)):
            names.add(arg.arg)
        for arg in (args.vararg, args.kwarg):
            if arg is not None:
                names.add(arg.arg)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
            _params(node.args)
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Lambda):
            _params(node.args)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(
                node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
    return names


def _unbound_calls(rel, path):
    """``(rel, lineno, name)`` for every plain call to a name bound nowhere."""
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    known = _bound_anywhere(tree) | BUILTINS
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id not in known:
                found.append((rel, node.func.lineno, node.func.id))
    return found


def test_the_shipped_code_calls_nothing_that_does_not_exist():
    """ST-ARCH-011 — a call to a deleted function is a crash, not dead weight.

    A failure names the file, the line and the function. It means the line
    raises ``NameError`` the first time it runs, so the code around it is
    either unreachable today (and should be deleted) or reachable and broken
    (and the function needs restoring) — and the message cannot tell you which,
    only that it cannot be both fine.
    """
    offenders = []
    for rel, path in _shipped_modules():
        offenders.extend(_unbound_calls(rel, path))

    assert not offenders, (
        "%d call(s) name a function that is defined, imported and built in "
        "nowhere — each raises NameError when reached:\n  %s"
        % (len(offenders),
           "\n  ".join("%s:%d  %s(...)" % o for o in sorted(offenders))))


def test_the_guard_can_see_a_call_to_a_missing_function():
    """Anti-vacuity: the walk above must actually resolve calls.

    Without this, a bug in ``_bound_anywhere`` that marked every name bound
    would make the contract pass forever while pinning nothing — which is the
    state the codebase was already in for a phase, differently spelled.
    """
    module = ast.parse(
        "import os\n"
        "def caller(state):\n"
        "    known = os.getcwd()\n"
        "    return find_conflicting_classes(state) + len(known)\n")
    known = _bound_anywhere(module) | BUILTINS

    assert "caller" in known and "state" in known and "os" in known
    assert "len" in BUILTINS
    missing = [n.func.id for n in ast.walk(module)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id not in known]
    assert missing == ["find_conflicting_classes"], missing


@pytest.mark.parametrize("source", [
    "def f(a, *rest, **kw):\n    return g(a, rest, kw)\ndef g(*a, **k):\n    return a\n",
    "import os.path as p\ndef f():\n    return p.join('a')\n",
    "def f(xs):\n    return [h(x) for x in xs]\ndef h(x):\n    return x\n",
    "def f(xs):\n    return sum(n for n in xs)\n",
    "try:\n    import json\nexcept ImportError:\n    json = None\n"
    "def f(s):\n    return json.loads(s)\n",
    "def f():\n    with open('x') as fh:\n        return fh.read()\n",
    "def f(cb=lambda v: v):\n    return cb(1)\n",
    "_H = None\ndef f():\n    global _H\n    _H = dict()\n    return _H\n",
])
def test_the_guard_does_not_cry_wolf(source):
    """Every binding form the package uses must count as a binding.

    A guard that flags working code gets an ignore-list, and an ignore-list is
    where the next missing function hides. These are the forms
    ``scheduler_app`` actually contains; each must come out clean.
    """
    tree = ast.parse(source)
    known = _bound_anywhere(tree) | BUILTINS
    unbound = sorted({n.func.id for n in ast.walk(tree)
                      if isinstance(n, ast.Call)
                      and isinstance(n.func, ast.Name)
                      and n.func.id not in known})
    assert unbound == [], unbound
