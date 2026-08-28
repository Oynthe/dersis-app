"""The engine must stay usable without the UI — ST-ARCH-009, ST-ARCH-010.

DERSİS layers as ``ui -> data_io -> core -> storage``, and the audit found the
arrows pointing back: ``core``, ``data_io``, ``storage`` and ``learning`` all
import ``ui``. That is not a stylistic complaint. It has three concrete costs:

* the scheduling engine cannot be extracted or reused headlessly;
* engine return values carry **translated** strings, so solver output depends
  on the display language (see the CP-SAT note below, which is worse);
* one added Qt import in ``ui/translations.py`` breaks every headless consumer,
  including the Linux CI job that runs the invariant oracle with no Qt at all.

Why this is a ratchet and not an assertion
------------------------------------------
The violations are real but numerous, and fixing them is a package move rather
than an edit. A hard ``== 0`` would have to land in the same commit as the move
or sit red in between. So each contract carries a **measured ceiling** that may
only go down. Same design as ``MAX_MISSING_LOCALE_KEY_PAIRS`` in
``test_translation_coverage.py``: adding a violation turns the suite red and
says so, and lowering a ceiling is how progress is recorded.

Three ways to get this test wrong, all of which were built and measured first
--------------------------------------------------------------------------
1. **A grep for ``ui`` finds a quarter of the problem.** 16 of the 22 upward
   imports go through the *flat shim name* ``scheduler_app.translations``,
   which ``scheduler_app/__init__.py`` maps onto ``scheduler_app.ui.translations``
   at import time. They never mention ``ui``. This module therefore resolves
   every target through ``_SHIM_MAP``, parsed out of the package source with
   ``ast.literal_eval`` so it can never drift from the real one.

2. **"11 module-level import cycles" is not what is there.** Measured on this
   tree, module-level edges alone form **zero** cycles and zero mutually
   importing pairs -- nothing cyclic runs at import time, today or at the audit
   commit. The cycles appear only when ``logic.py``'s function-level deferred
   imports are counted as edges, and then it is not 11 discrete cycles but a
   single **15-module strongly connected component** covering nearly all of
   ``core``. That distinction matters: you cannot fix a 15-node SCC one cycle
   at a time, which is why the register's remedy is a seam (split ``logic``)
   and not a list.

3. **The contract must not go blind when the move lands.** Moving the leaf
   modules under ``scheduler_app/i18n/`` and adding shim aliases for the old
   names would let a shim-aware resolver rewrite ``ui.translations`` to
   ``i18n.translations`` -- and the upward-import count would drop to zero
   without a single import changing. ``test_the_shim_map_cannot_launder_a_violation``
   below pins that the resolver is not doing the laundering.

This module imports **no** ``scheduler_app`` code. It parses source with ``ast``.
"""
import ast
import os
from collections import defaultdict

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(REPO, "scheduler_app")

# The layer each subpackage belongs to, lowest first. `ui` is the top; nothing
# below it may import it.
LOWER_LAYERS = ("core", "data_io", "storage", "learning")
UI_LAYER = "ui"


# ── measured ceilings ───────────────────────────────────────────────────────
# Every number here was measured by this file's own helpers on the tree at the
# commit that introduced it. They may go DOWN. Raising one is a deliberate act
# and needs a sentence in the commit saying why.

MAX_UPWARD_IMPORT_PAIRS = 22
"""Module-level imports from a lower layer into `ui`. 22 at Phase 6.

By importing package: core 13, data_io 7, learning 1, storage 1.
By imported module: translations 16, day_keys 4, badge_formatter 1,
cell_formatter 1. The register says 19; it was 19 at the audit commit and three
were added by the remediation itself.
"""

MAX_DEFERRED_IMPORTS_IN_LOGIC = 13
"""Function-level imports inside `core/logic.py`. 21 -> 13 in Phase 6.

These exist to keep `core` importable at all: `logic` is imported at module
scope by its partners, so its own side of each edge has to be deferred. The
register says 20 and cites lines 1129-1455; both are stale.

Phase 6 took 21 to 13: two deferrals were measured NOT load-bearing and were
promoted (`PROTECTION_LOCKED`, `ExplanationEngine` -- neither target imports
`logic`), and deleting `analyze_conflict_graph` / `analyze_constraint_propagation`
removed six more. The other 19 measured at the start of the phase genuinely do
raise ImportError if promoted; each was tested by promoting it in a copy and
importing `scheduler_app.core.workflow` in a fresh subprocess. Note that
`python -c "import scheduler_app.core.logic"` is NOT the check -- it succeeds
for all of them.
"""

MAX_CORE_SCC_SIZE = 15
"""Largest strongly connected component once deferred edges are counted.

15 modules -- nearly all of `core`. The seam that decomposes it is splitting
`logic.py` into primitives plus a facade holding the `optimized_*` bridges.
"""

MAX_MUTUAL_IMPORT_PAIRS = 7
"""Module pairs that import each other (deferred edges counted). 9 -> 7 in Phase 6.

`core.logic` is in six of the seven. `schedule_optimizer <-> solver_worker` is
the one the register never named. Deleting the two dead `analyze_*` bridges
removed `conflict_graph <-> logic` and `constraint_propagator <-> logic`.
"""


# ── the import graph ────────────────────────────────────────────────────────

def _shim_map():
    """`_SHIM_MAP` read out of the package source, never imported."""
    src = open(os.path.join(PKG, "__init__.py"), encoding="utf-8").read()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign):
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == "_SHIM_MAP":
                return ast.literal_eval(node.value)
    raise AssertionError(
        "scheduler_app/__init__.py no longer defines _SHIM_MAP as a literal; "
        "this module's shim awareness depends on being able to read it")


SHIM = _shim_map()


def _module_name(path):
    rel = os.path.relpath(path, REPO).replace(os.sep, "/")[:-3]
    if rel.endswith("/__init__"):
        rel = rel[: -len("/__init__")]
    return rel.replace("/", ".")


def _layer(module):
    parts = module.split(".")
    return parts[1] if len(parts) > 1 else "_root"


def _python_files():
    out = []
    for dirpath, dirnames, filenames in os.walk(PKG):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        out.extend(os.path.join(dirpath, f)
                   for f in sorted(filenames) if f.endswith(".py"))
    return out


def _edges():
    """(importer, imported, deferred, lineno, went_through_shim)."""
    found = []
    for path in _python_files():
        me = _module_name(path)
        tree = ast.parse(open(path, encoding="utf-8").read())
        parent = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parent[child] = node

        def deferred(node):
            p = parent.get(node)
            while p is not None:
                if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return True
                p = parent.get(p)
            return False

        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names
                           if a.name.startswith("scheduler_app")]
            elif isinstance(node, ast.ImportFrom):
                if node.level or not node.module:
                    continue
                if node.module.startswith("scheduler_app"):
                    targets.append(node.module)
                    for alias in node.names:
                        sub = node.module + "." + alias.name
                        if os.path.exists(
                                os.path.join(REPO,
                                             sub.replace(".", os.sep) + ".py")):
                            targets.append(sub)
            for raw in targets:
                real = SHIM.get(raw, raw)
                found.append((me, real, deferred(node), node.lineno,
                              raw != real))
    return found


def _upward_pairs(edges):
    return [e for e in edges
            if not e[2]
            and _layer(e[1]) == UI_LAYER
            and _layer(e[0]) in LOWER_LAYERS]


def _sccs(graph, nodes):
    """Tarjan, iterative."""
    index, low, on, stack, out, counter = {}, {}, {}, [], [], [0]
    for root in sorted(nodes):
        if root in index:
            continue
        work = [(root, iter(sorted(graph.get(root, ()))))]
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on[root] = True
        while work:
            v, it = work[-1]
            pushed = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter[0]
                    counter[0] += 1
                    stack.append(w)
                    on[w] = True
                    work.append((w, iter(sorted(graph.get(w, ())))))
                    pushed = True
                    break
                if on.get(w):
                    low[v] = min(low[v], index[w])
            if pushed:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on[w] = False
                    comp.append(w)
                    if w == v:
                        break
                out.append(sorted(comp))
    return out


# ── the contracts ───────────────────────────────────────────────────────────

def test_the_engine_does_not_import_the_interface():
    """ST-ARCH-009 — a ratchet on lower-layer imports of `ui`.

    A failure means either a new upward import was added (fix it, or move the
    target into a leaf package), or violations were removed and the ceiling
    should come down to match.
    """
    pairs = _upward_pairs(_edges())
    listing = sorted("%s:%d -> %s" % (a, ln, b) for a, b, _d, ln, _s in pairs)
    assert len(pairs) <= MAX_UPWARD_IMPORT_PAIRS, (
        "upward imports rose to %d (ceiling %d):\n  %s"
        % (len(pairs), MAX_UPWARD_IMPORT_PAIRS, "\n  ".join(listing)))
    assert len(pairs) == MAX_UPWARD_IMPORT_PAIRS, (
        "upward imports fell to %d; lower MAX_UPWARD_IMPORT_PAIRS to match "
        "so the ground gained cannot be given back" % len(pairs))


def test_no_import_cycle_runs_at_import_time():
    """ST-ARCH-010 — module-level edges must stay acyclic.

    This is a HARD contract, not a ratchet: it holds today and has always held.
    A failure means someone promoted a deferred import in `core` to module
    scope, and `import scheduler_app.core.workflow` now raises ImportError --
    which the CI smoke test would also catch, but only for the modules it
    happens to list.
    """
    graph = defaultdict(set)
    nodes = set()
    for a, b, deferred, _ln, _shim in _edges():
        nodes.update((a, b))
        if not deferred and a != b:
            graph[a].add(b)
    cyclic = [c for c in _sccs(graph, nodes) if len(c) > 1]
    assert not cyclic, (
        "these modules import each other at module scope, so importing any of "
        "them raises ImportError:\n  %s"
        % "\n  ".join(", ".join(c) for c in cyclic))


def test_the_core_knot_does_not_grow():
    """ST-ARCH-010 — the real shape of the finding, as a ratchet.

    The register calls this "11 module-level cycles". Measured, it is one
    strongly connected component of 15 modules, visible only once `logic.py`'s
    deferred imports are counted as the dependencies they are. A failure means
    a module was pulled into the knot; the fix is the `logic` split, not a
    bigger number here.
    """
    graph = defaultdict(set)
    nodes = set()
    for a, b, _deferred, _ln, _shim in _edges():
        nodes.update((a, b))
        if a != b:
            graph[a].add(b)
    components = [c for c in _sccs(graph, nodes) if len(c) > 1]
    biggest = max((len(c) for c in components), default=0)
    assert biggest <= MAX_CORE_SCC_SIZE, (
        "the dependency knot grew to %d modules (ceiling %d): %s"
        % (biggest, MAX_CORE_SCC_SIZE,
           ", ".join(max(components, key=len))))

    mutual = {tuple(sorted((a, b)))
              for a in graph for b in graph[a] if a in graph.get(b, ())}
    assert len(mutual) <= MAX_MUTUAL_IMPORT_PAIRS, (
        "mutually importing pairs rose to %d (ceiling %d):\n  %s"
        % (len(mutual), MAX_MUTUAL_IMPORT_PAIRS,
           "\n  ".join("%s <-> %s" % p for p in sorted(mutual))))


def test_logic_does_not_defer_more_imports():
    """ST-ARCH-010 — `logic.py`'s deferred imports are the knot's mechanism.

    Each one is a dependency the module has but cannot declare. A failure means
    a new one was added, which deepens the coupling the finding is about.
    """
    deferred = [e for e in _edges()
                if e[0] == "scheduler_app.core.logic" and e[2]]
    assert len(deferred) <= MAX_DEFERRED_IMPORTS_IN_LOGIC, (
        "core/logic.py now defers %d imports (ceiling %d):\n  %s"
        % (len(deferred), MAX_DEFERRED_IMPORTS_IN_LOGIC,
           "\n  ".join(sorted("logic.py:%d -> %s" % (e[3], e[1])
                              for e in deferred))))


def test_the_shim_map_cannot_launder_a_violation():
    """Anti-vacuity: the resolver must not be able to hide the finding.

    Every contract above resolves imports through `_SHIM_MAP`, because 16 of
    the 22 upward imports are written as the flat `scheduler_app.translations`
    and would otherwise be invisible. That same resolution is a loaded gun: add
    an alias mapping a `ui` name onto a non-`ui` one and every violation
    through it silently disappears from the count, with no import changed.

    So: no shim entry may point OUT of `ui` when its key names a `ui` module.
    Moving those modules to a leaf package is the intended fix, and it must be
    done by editing the imports, not by adding a redirect.
    """
    laundering = {
        alias: real for alias, real in SHIM.items()
        if ".ui." in ("." + alias.split("scheduler_app.", 1)[-1] + ".")
        or (_layer(alias) == UI_LAYER and _layer(real) != UI_LAYER)
    }
    assert not laundering, (
        "these shim aliases would make an upward import resolve to a "
        "non-ui module, hiding it from every contract in this file:\n  %s"
        % "\n  ".join("%s -> %s" % kv for kv in sorted(laundering.items())))


def test_the_graph_builder_actually_sees_the_shim_imports():
    """Anti-vacuity: prove the resolver is doing the work claimed for it.

    If `_SHIM_MAP` parsing silently returned `{}`, every contract above would
    still pass -- with three quarters of the violations invisible. Pin that the
    flat names really are being resolved.
    """
    assert SHIM, "the shim map parsed as empty"
    assert "scheduler_app.translations" in SHIM, sorted(SHIM)[:5]
    through_shim = [e for e in _upward_pairs(_edges()) if e[4]]
    assert len(through_shim) >= 10, (
        "only %d upward imports resolved through the shim; the flat-name "
        "resolution has stopped working and the count is now an undercount"
        % len(through_shim))
