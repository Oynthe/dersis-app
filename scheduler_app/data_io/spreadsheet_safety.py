"""Stop a spreadsheet evaluating a class name as a formula.

ST-UI-008. Excel and LibreOffice execute a cell whose text begins with ``=``,
and DERSİS workbooks are made to be emailed to colleagues — that is the whole
point of the export. A school that names a class ``=cmd|'/c calc'!A1`` (or, far
more likely, receives a file from someone who did) ships an executable cell.

Why this is not the same fix as the CSV one
-------------------------------------------
The conventional neutralisation is to prefix the value with an apostrophe, and
:func:`scheduler_app.core.text_safety.csv_safe` does exactly that. Applied to
XLSX it is a **data-corruption bug**, because DERSİS re-imports its own
workbooks — the Setup days / time slots / rooms / lecturers / years sheets and
the class list all have symmetric export/import pairs. Measured across one
export/re-import round trip with the value prefixed:

    '=1+1'            -> "'=1+1"            RENAMED
    '+1+1'            -> "'+1+1"            RENAMED
    '-9A Matematik'   -> "'-9A Matematik"   RENAMED   <- a real class name
    '@SUM(A1)'        -> "'@SUM(A1)"        RENAMED
    '=cmd'            -> "'=cmd"            RENAMED
                                            5 of 8 values renamed

The security fix would have silently renamed the user's data, which is the class
of defect Phases 0–4 exist to prevent.

Excel stores the same protection as a *cell attribute* instead —
``quotePrefix`` — which suppresses formula evaluation without touching the
stored string. Same round trip through this function:

    all 8 values returned unchanged, 0 ``<f>`` elements in the saved file

What actually needs neutralising, measured
------------------------------------------
Only a **leading ``=``** makes openpyxl emit a formula. ``+1+1``,
``-9A Matematik``, ``@SUM(A1)`` and a tab-prefixed ``\\t=cmd`` all stay
``data_type='s'``. So the wider OWASP trigger list is over-broad *for this
writer*, and the honest scope here is "whatever openpyxl decided was a formula"
rather than a character blacklist of our own — which also means the sweep tracks
openpyxl's behaviour if it changes, instead of drifting from it.
"""


def neutralize_formula_cells(workbook):
    """Turn every formula cell in *workbook* into quote-prefixed text.

    Returns the number of cells changed, so a caller can assert on it. Operates
    on the in-memory workbook immediately before ``save``; re-opening the saved
    file to fix it afterwards costs roughly as much as the export itself
    (measured 0.53 / 1.83 / 4.04 s at 25 / 80 / 250 classes, against an
    in-memory sweep of 0.3 / 3.2 / 7.0 **ms**).
    """
    changed = 0
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if cell.data_type == "f" and isinstance(cell.value, str):
                    cell.data_type = "s"
                    cell.quotePrefix = True
                    changed += 1
    return changed
