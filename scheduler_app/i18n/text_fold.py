"""One case-folding rule for every place the app compares user-typed text.

ST-ARCH-001 item 9 / ST-FUNC-012. ``str.casefold()`` is locale-independent and
implements neither half of the Turkish I, so on the shipped default language it
splits strings a human reads as identical::

    'PAZARTESİ'.casefold()  # -> 'pazartesi' + U+0307 COMBINING DOT ABOVE
    'SALI'.casefold()       # -> 'sali', ASCII i, never the dotless U+0131

Measured on this tree that drops three of the seven Turkish weekday labels
(PAZARTESİ, SALI, CUMARTESİ) and two Azerbaijani ones (ÇƏRŞƏNBƏ AXŞAMI, CÜMƏ
AXŞAMI, which break under plain ASCII ``.upper()`` with no Turkish keyboard
involved), splits one lecturer into two, and makes four Turkish lecturer-name
headers and ten Turkish/Azerbaijani column headers unrecognisable.

The obvious remedy -- a *Turkish* fold, 'İ'->'i' and 'I'->'ı' applied before the
ordinary fold -- was built and measured and is worse: 43 locale/weekday pairs
break, among them plain ASCII 'FRIDAY', 'DIENSTAG', 'LUNDI', 'DOMINGO' and every
Portuguese '-FEIRA' form, and 'PAZARTESI'/'CUMARTESI', which the suite pins as
working today. It also splits 'WILLIAM SMITH' from 'William Smith'. A
locale-*dependent* fold is worse still: the same workbook would merge or split
according to a UI setting.

So the rule here is neither. Every dotted and dotless I -- 'I', 'i', the dotted
capital 'İ' and the dotless small 'ı' -- folds onto plain ASCII 'i', and nothing
else changes. It is locale-free, idempotent, and a strict superset of what
``casefold()`` already merged, so no pair that is one thing today becomes two.
Nothing else is stripped: an NFKD strip-all-diacritics fold was measured too and
it makes 'Öz' and 'Oz' one teacher, while still failing SALI because U+0131 has
no canonical decomposition.

This module imports nothing so that ``i18n``, ``core`` and ``data_io`` can all
call the same rule -- see ``tests/test_import_layering.py``
``test_the_i18n_leaf_stays_a_leaf``.
"""

_DOTTED_CAPITAL_I = "İ"       # İ  LATIN CAPITAL LETTER I WITH DOT ABOVE
_DOTLESS_SMALL_I = "ı"        # ı  LATIN SMALL LETTER DOTLESS I
_COMBINING_DOT_ABOVE = "̇"    # the mark casefold() actually emits for İ


def fold_text(value):
    """Case-fold *value* so that every dotted and dotless I compares equal.

    Returns ``""`` for None and for the empty string; every caller must read
    that as "no match", never as a key.
    """
    folded = str(value or "").casefold()
    if not folded:
        return ""
    # ORDER IS LOAD-BEARING: 'i' + U+0307 is the two-codepoint sequence
    # casefold() emits for İ, so it must be collapsed BEFORE the bare dotted
    # capital -- otherwise the replace below never sees an İ to fix and the
    # stray combining mark survives into the comparison.
    return (folded
            .replace("i" + _COMBINING_DOT_ABOVE, "i")
            .replace(_DOTTED_CAPITAL_I, "i")
            .replace(_DOTLESS_SMALL_I, "i"))
