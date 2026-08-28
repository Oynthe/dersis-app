"""Language and presentation vocabulary — the bottom of the stack.

ST-ARCH-009. ``core``, ``data_io``, ``storage`` and ``learning`` all need to say
things to a user: a rejection reason, a day name, a protection badge, a sheet
title. Before Phase 6 they got those by importing ``ui``, which inverted the
layering 22 times and meant the scheduling engine could not be used, tested or
extracted without the interface package coming with it.

These four modules were always leaves in fact — ``translations`` imports nothing
at all, and ``day_keys`` / ``badge_formatter`` / ``tier_translations`` import
only ``translations``. Living under ``ui/`` was an accident of where they were
first written. Moving them here is the whole fix; no call site changed meaning.

**This package must import nothing else from ``scheduler_app``.** That is what
makes it safe for every layer to depend on, and
``tests/test_import_layering.py`` enforces it. In particular ``cell_formatter``
is deliberately *not* here: its ``tooltip_text`` needs ``core.logic.classroom_of``,
so moving it would have turned a ``core -> ui`` violation into an
``i18n -> core`` one and made this package part of a cycle rather than a leaf.
Its one genuinely dependency-free function, ``plain_cell_text``, moved to its
single caller in ``data_io/exporter.py`` instead.

No module here may import Qt, now or later. The CP-SAT solver runs in a spawned
subprocess and the Linux CI job runs the invariant oracle with no Qt installed;
both re-import this package from scratch.
"""
