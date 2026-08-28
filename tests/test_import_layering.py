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
   which ``scheduler_app/__init__.py`` maps onto ``scheduler_app.i18n.translations``
   at import time. They never mention ``ui``. This module therefore resolves
   every target through ``_SHIM_MAP``, parsed out of the package source with
   ``ast.literal_eval`` so it can never drift from the real one.

2. **"11 module-level import cycles" is not what is there.** Measured on this
   tree, module-level edges alone form **zero** cycles and zero mutually
   importing pairs -- nothing cyclic runs at import time, today or at the audit
   commit. The cycles appeared only when ``logic.py``'s function-level deferred
   imports were counted as edges, and then it was not 11 discrete cycles but a
   single **15-module strongly connected component** covering nearly all of
   ``core``. That distinction mattered: you cannot fix a 15-node SCC one cycle
   at a time, which is why the register's remedy is a seam (split ``logic``)
   and not a list.

   **Phase 7 cut that seam and the component is gone.** ``logic.py`` now holds
   only the scheduling primitives; the eight ``optimized_*`` / scoring bridges
   live in ``scheduler_app/core/facade.py``, which nothing inside the engine
   imports, so it can import the engine normally. That took the component from
   15 modules to 2 and mutually importing pairs from 7 to 1. The residual pair
   was ``schedule_optimizer <-> solver_worker``, which had nothing to do with
   ``logic`` and carried two integer constants; those moved to the leaf
   ``core/constants.py``. Measured end state: **no strongly connected
   component of size > 1 anywhere in the package, no mutually importing pair,
   no deferred import in ``logic.py``.**

   The three ceilings below are therefore ``== 0`` and are contracts, not
   ratchets. The two ways someone will try to give that ground back --
   re-exporting the bridges from ``logic.py``, or letting an engine module
   import the facade -- each have their own contract as well, because both
   were built and measured and neither is caught by the SCC number alone.

3. **The contract must not go blind when the move lands.** Moving the leaf
   modules under ``scheduler_app/i18n/`` and adding shim aliases for the old
   names would let a shim-aware resolver rewrite ``ui.translations`` to
   ``i18n.translations`` -- and the upward-import count would drop to zero
   without a single import changing. ``test_the_shim_map_cannot_launder_a_violation``
   below pins that the resolver is not doing the laundering.

This module parses source with ``ast`` and never imports ``scheduler_app`` into
the test process. The one contract that needs a real import --
``test_logic_does_not_re_export_the_optimization_bridge``, which has to catch a
lazy ``__getattr__`` an AST walker cannot see -- runs it in a **subprocess**,
for the reason recorded there.
"""
import ast
import os
import subprocess
import sys
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

MAX_UPWARD_IMPORT_PAIRS = 0
"""Module-level imports from a lower layer into `ui`. 22 -> 0 in Phase 6.

Was 22, distributed core 13 / data_io 7 / learning 1 / storage 1, and by
imported module translations 16, day_keys 4, badge_formatter 1,
cell_formatter 1. The register says 19; it was 19 at the audit commit and three
were added by the remediation itself.

Now **zero**, and this ceiling is therefore a hard contract: `translations`,
`day_keys`, `badge_formatter` and `tier_translations` moved to
`scheduler_app/i18n/`, and `plain_cell_text` moved to its only caller in
`data_io/exporter.py`. A new upward import is now a regression, not a
pre-existing debt, so this must never be raised again.
"""

MAX_DEFERRED_IMPORTS_IN_LOGIC = 0
"""Function-level imports inside `core/logic.py`. 21 -> 13 -> 0.

These existed to keep `core` importable at all: `logic` is imported at module
scope by its partners, so its own side of each edge had to be deferred. The
register says 20 and cites lines 1129-1455; both were stale.

Phase 6 took 21 to 13. Phase 7 took 13 to 0 by moving the eight functions that
held every one of them into `core/facade.py`. Promoting them where they stood
was measured and does not work -- 0 of 13 -- and the reason is not the cycle:
`scheduler_app/__init__.py`'s `_ShimLoader` puts an **empty** alias module into
`sys.modules` before `exec_module` runs, so a re-entrant
`from scheduler_app.logic import X` cannot see names the real module has
already bound, and 15 of `logic`'s 16 importers use that flat name. Note that
`python -c "import scheduler_app.core.logic"` is NOT the check -- it succeeded
for all 13 while `workflow`, `ui.app` and the CI smoke list were all broken.

Now zero, and therefore a hard contract: with the facade in place there is no
legitimate reason for `logic.py` to defer anything, and a new deferral means
somebody has started re-attaching the engine to the primitives.
"""

MAX_CORE_SCC_SIZE = 0
"""Largest strongly connected component once deferred edges are counted.

Was 15 -- nearly all of `core`. Splitting `logic.py` into primitives plus
`core/facade.py`, which holds the `optimized_*` bridges, took it to 2; moving
two integer constants into `core/constants.py` took it to 0. There is now no
component of size > 1 anywhere in `scheduler_app`.

Zero, and therefore a hard contract: every remaining edge in the package is a
dependency a module can declare, so a component appearing here is a new
regression rather than pre-existing debt. Do not raise this again.
"""

MAX_MUTUAL_IMPORT_PAIRS = 0
"""Module pairs that import each other (deferred edges counted). 9 -> 7 -> 0.

`core.logic` was in six of the seven, and all six went with the facade split.
The seventh was the one the register never named: `solver_worker` deferred an
import of `DEFAULT_MULTI_START_RUNS` / `DEFAULT_LNS_ITERATIONS` out of
`schedule_optimizer`, which imports `solver_worker` back for `SolveCancelled`.
Two integers cannot justify a cycle; they live in the leaf `core/constants.py`
now, and `schedule_optimizer` re-exports them so its own attribute access is
unchanged. `test_progress_scale_matches_the_optimizers_own_default_budget` in
`test_solver_worker.py` is what stops the two copies drifting, and it still
passes -- both sides read the same definition.
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

    The register calls this "11 module-level cycles". Measured, it was one
    strongly connected component of 15 modules, visible only once `logic.py`'s
    deferred imports were counted as the dependencies they are. Phase 7's
    `logic` / `facade` split took it to 2. A failure means a module was pulled
    back into the knot; the fix is to move the offending import above the
    seam, not a bigger number here.
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
    """ST-ARCH-010 — `logic.py` must not defer any import. HARD contract.

    Each deferral was a dependency the module had but could not declare, and
    all 13 of them belonged to the eight bridge functions that now live in
    `core/facade.py`. With the seam cut there is no legitimate reason for the
    primitives to defer anything: a new one means somebody has started
    re-attaching the engine to `logic`, and the SCC will follow.

    This is the contract to read first when the split gets undone by accident,
    because it names the exact line.
    """
    deferred = [e for e in _edges()
                if e[0] == "scheduler_app.core.logic" and e[2]]
    assert len(deferred) <= MAX_DEFERRED_IMPORTS_IN_LOGIC, (
        "core/logic.py now defers %d imports (ceiling %d):\n  %s\n"
        "A function-level import here means the module needs something it "
        "cannot import at module scope, i.e. something that imports `logic` "
        "back. Put the caller in core/facade.py instead."
        % (len(deferred), MAX_DEFERRED_IMPORTS_IN_LOGIC,
           "\n  ".join(sorted("logic.py:%d -> %s" % (e[3], e[1])
                              for e in deferred))))


def test_the_engine_does_not_import_the_facade():
    """ST-ARCH-010 — the seam only holds while the arrow points one way.

    `core/facade.py` imports the engine (`schedule_optimizer`,
    `constraint_validator`, `placement_scorer`, ...) at module scope, which it
    can only do because nothing in the engine imports it back. `core/workflow`
    is the sole permitted consumer inside the package; the UI reaches it from
    above, which is downward and fine.

    This is not covered by the SCC count alone: measured, adding
    `from scheduler_app.core.facade import score_placement` to
    `core/placement_scorer.py` makes `import scheduler_app.core.workflow`
    raise ImportError immediately -- a red suite either way, but from here it
    says *why*.
    """
    ALLOWED = {"scheduler_app.core.workflow", "scheduler_app.core.facade"}
    offenders = [
        "scheduler_app/%s.py:%d imports the facade"
        % (a.split("scheduler_app.", 1)[-1].replace(".", "/"), ln)
        for a, b, _deferred, ln, _shim in _edges()
        if b == "scheduler_app.core.facade"
        and _layer(a) == "core" and a not in ALLOWED
    ]
    assert not offenders, (
        "the facade is the top of `core`, not part of it — these imports put "
        "`logic` back inside the dependency knot and stop "
        "`import scheduler_app.core.workflow` from working:\n  "
        + "\n  ".join(sorted(offenders)))


def test_logic_does_not_re_export_the_optimization_bridge():
    """ST-ARCH-010 — the evasion the AST walker cannot see.

    The split only pays if `logic.py` stops handing out the eight bridge
    names. Keeping them "for compatibility" was built both ways and measured:
    a `from ...facade import *` turns the cycle into a *module-level* one and
    `import scheduler_app.core.workflow` raises ImportError; a lazy PEP 562
    `__getattr__` runs perfectly and puts the component at **16 modules —
    larger than the 15 the split removed**. The lazy form imports inside a
    function body of a module the walker sees, so nothing above catches it.

    So this one asks the real interpreter. It runs in a **subprocess** for two
    reasons: this module must not drag `scheduler_app` into the test process,
    and a fresh process is the only honest check of an import contract —
    `sys.modules` in a suite that has already imported half the package can
    make a broken graph look fine.
    """
    BRIDGE = ("optimized_auto_place", "optimized_reschedule_all",
              "optimized_batch_schedule", "score_placement",
              "score_placement_explained", "analyze_schedule",
              "negotiate_after_optimization", "apply_negotiation_suggestion")
    code = (
        "import scheduler_app.core.workflow as W\n"
        "import scheduler_app.core.logic as L\n"
        "print(','.join(n for n in %r if hasattr(L, n)))\n"
        "assert callable(W.optimized_batch_schedule)\n" % (BRIDGE,))
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=REPO))
    assert proc.returncode == 0, (
        "`import scheduler_app.core.workflow` failed in a fresh process — the "
        "layering below is not merely untidy, the app does not start:\n%s"
        % proc.stderr)
    leaked = [n for n in proc.stdout.strip().split(",") if n]
    assert not leaked, (
        "core/logic.py hands out %s again. Re-exporting the bridge is the one "
        "change that makes this finding worse rather than better: measured, "
        "eagerly it breaks every entry path, lazily it takes the component to "
        "16. Repoint the caller at scheduler_app.core.facade instead."
        % ", ".join(leaked))


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

    through_shim = [e for e in _edges() if e[4]]
    assert len(through_shim) >= 20, (
        "only %d imports resolved through the shim; the flat-name resolution "
        "has stopped working, and every contract in this file is now counting "
        "a fraction of the real graph" % len(through_shim))

    # The specific alias that carried three quarters of ST-ARCH-009 must still
    # be resolved, and must land inside the leaf package now.
    resolved = {e[1] for e in through_shim
                if e[1].endswith(".translations")}
    assert resolved == {"scheduler_app.i18n.translations"}, (
        "the flat `scheduler_app.translations` import no longer resolves into "
        "the i18n leaf: %r" % (resolved,))


def test_the_i18n_leaf_stays_a_leaf():
    """ST-ARCH-009 — the fix only holds while `i18n` depends on nothing.

    Every layer imports this package, so one import out of it re-creates the
    inversion in a new direction. `cell_formatter` is the concrete temptation:
    it looks like it belongs here and its `tooltip_text` needs
    `core.logic.classroom_of`, which would make `i18n -> core -> i18n` a cycle
    and put the leaf inside the knot `test_the_core_knot_does_not_grow` tracks.
    """
    leaks = [
        "scheduler_app/%s.py:%d imports %s"
        % (a.split("scheduler_app.", 1)[-1].replace(".", "/"), ln, b)
        for a, b, _deferred, ln, _shim in _edges()
        if _layer(a) == "i18n" and _layer(b) != "i18n"
    ]
    assert not leaks, (
        "scheduler_app/i18n must import nothing else from scheduler_app:\n  "
        + "\n  ".join(sorted(leaks)))


def test_the_engine_imports_no_qt():
    """ST-ARCH-009's real payload: the engine must run without an interface.

    The layering count can reach zero while a Qt import hides one module
    deeper. This walks the actual module graph of the packages the CP-SAT
    subprocess and the Linux CI oracle import, and fails on any PyQt6 reachable
    from them -- which is the failure those two consumers would hit, and which
    a headless machine reports as an ImportError with no obvious cause.
    """
    qt_importers = []
    for path in _python_files():
        module = _module_name(path)
        if _layer(module) not in LOWER_LAYERS + ("i18n",):
            continue
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.split(".")[0] in ("PyQt6", "PyQt5", "PySide6"):
                    qt_importers.append("%s:%d imports %s"
                                        % (module, node.lineno, name))
    assert not qt_importers, (
        "these engine-layer modules import Qt, so the CP-SAT subprocess and "
        "the headless CI oracle cannot load them:\n  "
        + "\n  ".join(sorted(qt_importers)))
