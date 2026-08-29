"""The second copy of a formatter, and why grep cannot police it.

ST-ARCH-003 removed one of `data_io/exporter.py`'s two Excel engines and left
three of its formatters behind: `_cell_text`, `_rich_cell` and `_entry_bg_color`
had no caller in the module, while the live `_export_excel` carried its own
nested `_build_rich_cell` / `_append_rich_cell_blocks`. Anyone fixing a colour
bug would naturally have edited `_entry_bg_color` and watched nothing change --
Phase 6's headline failure, one file along.

The residue survived four phases because a grep answers the question wrong in
both directions. `data_io/importer.py` defines its own `_cell_text` with 15 live
call sites, so:

* "is `_cell_text` used?" answers **yes, 15 sites** -- and the dead one stays;
* a grep-driven deletion of "`_cell_text`" takes the **importer's** with it and
  breaks all 15.

This test therefore asserts both halves: the exporter must define none of the
three, and the importer must still define the one that is real.

No Qt: `data_io` imports no PyQt6, and this must run in the headless job that
proves it (ST-ARCH-009).
"""
import ast
import io
import os

EXPORTER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "scheduler_app", "data_io", "exporter.py")
IMPORTER = os.path.join(os.path.dirname(EXPORTER), "importer.py")

REMOVED = ("_cell_text", "_rich_cell", "_entry_bg_color")


def _module_level_functions(path):
    tree = ast.parse(io.open(path, encoding="utf-8").read(), path)
    return {node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_the_exporter_has_one_cell_formatter():
    """ST-ARCH-011 -- the removed engine's formatters must not come back.

    A failure means `data_io/exporter.py` again defines a cell formatter that
    the live `_export_excel` does not call, so a fix applied to it changes
    nothing in the workbook a user opens.
    """
    defined = _module_level_functions(EXPORTER)
    back = sorted(defined & set(REMOVED))
    assert not back, (
        "data_io/exporter.py defines %r again. The live Excel path formats "
        "cells through the nested _build_rich_cell / _append_rich_cell_blocks "
        "inside _export_excel; plain text goes through plain_cell_text." % back)


def test_the_importer_still_has_the_cell_formatter_that_is_real():
    """The anti-vacuity half: `_cell_text` must survive where it is called.

    Without this, the test above is satisfied by deleting the wrong twin --
    which is exactly what a grep for "_cell_text" invites, because every one of
    its 15 call sites is in `importer.py`.
    """
    assert "_cell_text" in _module_level_functions(IMPORTER), (
        "data_io/importer.py no longer defines _cell_text; the deletion took "
        "the live twin instead of the dead one")

    src = io.open(IMPORTER, encoding="utf-8").read()
    calls = src.count("_cell_text(")
    assert calls > 10, (
        "importer._cell_text is down to %d call sites; if it has become dead "
        "too, say so deliberately rather than leaving this guard pointing at "
        "nothing" % calls)
