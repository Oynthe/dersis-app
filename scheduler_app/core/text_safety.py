"""Neutralising user text for renderers that interpret markup.

ST-UI-007 / ST-UI-008. A class name, a room code, a branch letter and a time-slot
label are all **free text the user typed**. Three of the surfaces DERSİS hands
them to parse markup, and each parses a *different* dialect, so there is no
single "escape" that serves all three — a helper per context, named for the
context, so every call site says which renderer it is protecting.

The three contexts, and why they cannot share one function
----------------------------------------------------------
**Qt rich text** decides *per string* whether to parse markup at all:
``Qt.mightBeRichText`` returns True only for ``<`` followed by a tag name Qt
knows. So ``html.escape`` on the way into a ``QLabel`` is wrong — for the common
case, where Qt would have rendered the string as plain text, it produces visible
``&amp;`` garbage. The right move for a plain label is to remove Qt's choice
with ``setTextFormat(PlainText)``; escaping is only for strings interpolated
into a template that really is rich text.

**reportlab** parses every ``Paragraph`` as markup, with no auto-detection, and
fails in two different ways. A tag it knows raises ``ValueError`` out of the
whole export — measured, **10 of 24** (field × mode) combinations produced no
file at all. A tag it does not know is dropped silently, and a bare ``&``
corrupts: ``"R&D Lab"`` renders as ``"R&D; Lab"``. It does decode HTML entities
correctly, so ``html.escape`` is the right tool here even though the dialect is
not HTML — verified to round-trip every payload through ``getPlainText()``.

**A spreadsheet** treats a leading ``=`` as a formula. Entities would be visible
garbage, so neither of the above applies. CSV is export-only in this app, so a
quote prefix is safe there; XLSX is **not** — DERSİS re-imports its own
workbooks, and a prefix written into the string renames the user's data on the
way back in. That case needs openpyxl's cell-level ``quotePrefix`` flag instead
and therefore does not live here; see ``data_io/spreadsheet_safety.py``.

Layering: ``core`` imports nothing from ``ui`` or ``data_io``, so every consumer
of this module imports downward and no new ST-ARCH-009 violation is added.
"""
import html


def escape_qt_rich(value):
    """Escape *value* for interpolation into a Qt **rich-text** template.

    Use this only where the surrounding string genuinely is rich text — a
    ``<span style=…>`` or a ``<b>…</b>`` the code itself wrote. For a label that
    merely displays a message, call ``setTextFormat(Qt.TextFormat.PlainText)``
    instead: escaping a string Qt was going to render literally is how ``&amp;``
    reaches the screen.
    """
    return html.escape(str(value))


def qt_tooltip(text):
    """Wrap *text* as an explicit Qt rich-text tooltip, newlines preserved.

    Tooltips carry class and room names, so whether Qt parses one as markup
    currently depends on whether the *user's own text* happens to start with a
    tag Qt recognises — the same string renders differently on two different
    lessons. Forcing the format makes it deterministic.
    """
    return "<qt>" + html.escape(str(text)).replace("\n", "<br>") + "</qt>"


def escape_pdf_markup(value):
    """Escape *value* for a reportlab ``Paragraph``.

    ``quote=False`` because ``'`` and ``"`` are only special inside a reportlab
    tag *attribute*, and no user text reaches one — every ``<font color=…>`` in
    the exporter interpolates a literal constant from
    ``scheduler_app.core.constants``. Escaping them anyway would put ``&#x27;``
    in front of every apostrophe in a printed lecturer name.

    Kept separate from :func:`escape_qt_rich` despite the near-identical body:
    the two have different failure modes and different owners, and a future
    reportlab or Qt change should have exactly one place to move.
    """
    return html.escape(str(value), quote=False)


def csv_safe(value):
    """Neutralise a spreadsheet formula trigger for an **export-only** format.

    Excel and LibreOffice evaluate a cell whose text begins with ``=``; the
    leading ``+``, ``-``, ``@``, tab and carriage-return forms are the rest of
    the conventional list. A leading apostrophe is the standard neutralisation.

    **Only for the .csv file.** The safety of a literal prefix depends entirely
    on nothing reading it back, and that is true of the exported CSV — the only
    import filters in the app are ``*.xlsx`` and ``*.xlsx *.xls``.

    It is **not** true of the clipboard, and it is not true of XLSX:

    * ``EditClassesDialog._copy_rows`` writes the class list as TSV and
      ``_paste_rows`` parses that same text back, so a prefix applied on copy is
      read as part of the name on paste.
    * DERSİS re-imports its own workbooks through six symmetric export/import
      pairs. Measured on that round trip, a literal prefix renamed **7 of 8**
      values, including a perfectly innocent ``-9A Matematik``.

    For XLSX use openpyxl's cell-level ``quotePrefix`` flag instead, which
    neutralises the formula without touching the stored string.
    """
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s
