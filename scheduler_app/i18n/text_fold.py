"""One case-folding rule for every place the app compares user-typed text.

ST-ARCH-001 item 9 / ST-FUNC-012. ``str.casefold()`` is locale-independent and
implements neither half of the Turkish I, so on the shipped default language it
splits strings a human reads as identical::

    'PAZARTESİ'.casefold()  # -> 'pazartesi' + U+0307 COMBINING DOT ABOVE
    'SALI'.casefold()       # -> 'sali', ASCII i, never the dotless U+0131

Measured on this tree that drops three of the seven Turkish weekday labels
(PAZARTESİ, SALI, CUMARTESİ) and two Azerbaijani ones (ÇƏRŞƏNBƏ AXŞAMI, CÜMƏ
AXŞAMI, which break under plain ASCII ``.upper()`` with no Turkish keyboard
involved), splits one lecturer into two, and makes four Turkish/Azerbaijani
lecturer-name headers -- three tr ('Öğretim Elemanı', 'Öğretmen Adı',
'Öğretim Elemanları') and one az ('Müəllim Adı') -- and ten
Turkish/Azerbaijani column headers unrecognisable. The four are the ones
``tests/test_import_roundtrip.py`` parametrises
``test_a_turkish_roster_header_typed_in_capitals_still_names_the_roster`` over.

The obvious remedy -- a *Turkish* fold, 'İ'->'i' and 'I'->'ı' applied before the
ordinary fold -- was built and measured and is worse: it breaks 42 locale/
weekday pairs, which is 84 of the sweep's 770 probes. Both figures are stated
because "43" stood here for a phase and matched neither unit. Among the 42 are
plain ASCII 'FRIDAY', 'DIENSTAG', 'LUNDI', 'DOMINGO' and every
Portuguese '-FEIRA' form, and 'PAZARTESI'/'CUMARTESI', which the suite pins as
working today. It also splits 'WILLIAM SMITH' from 'William Smith'. A
locale-*dependent* fold is worse still: the same workbook would merge or split
according to a UI setting.

So the rule here is neither. Every dotted and dotless I -- 'I', 'i', the dotted
capital 'İ' and the dotless small 'ı' -- folds onto plain ASCII 'i', and nothing
else changes. It is locale-free and a strict superset of what ``casefold()``
already merged, so no pair that is one thing today becomes two. It is
idempotent on every string a caller can produce, which is not the same as
idempotent: swept exhaustively over 0..0x30000 there are exactly EIGHT inputs
on which a second pass changes anything, and all eight are an i-family letter
or an fi/ffi ligature followed by a stray U+0307 COMBINING DOT ABOVE. No
catalogue entry, workbook header or saved day key is one -- see
``tests/test_text_fold.py`` ``test_the_fold_is_idempotent_and_blank_safe``,
whose docstring carries the list.

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
    # ORDER: measured over the whole codespace on 2026-08-29, not asserted --
    # it is NOT load-bearing, and the comment that used to stand here said the
    # opposite of what is true. All six permutations of these three replaces
    # agree on every key and every value of every shipped catalogue, in both
    # casings including a Turkish-keyboard upper(). The corpus is the whole of
    # TRANSLATIONS at whatever size it currently is -- stated that way on
    # purpose, because the size is the one part of this that rots: it is four
    # times the (locale, key) pair count, so it moves every time an English key
    # is added across the 22 locales. Re-derive it, do not trust a number here.
    # Snapshot, in the manner Dersis-mac.spec dates its own count: 83 664 on
    # 2026-08-29 at bd12e58, from 20 916 pairs, zero disagreements. This line
    # read "83 488" until that re-measurement -- exact when written at 1098671
    # and stale two commits later, when 898da14 added two keys to all 22
    # locales and moved it by 176, inside the very phase convened to make stale
    # measurements true.
    #
    # Exhaustively over 0..0x30000 the only probes that tell any two orders
    # apart are U+0131
    # followed by one or two U+0307 COMBINING DOT ABOVE, which no caller
    # produces. In particular the old claim that collapsing 'i' + U+0307 first
    # is what stops "the stray combining mark surviving into the comparison"
    # is backwards: this order is the one that leaves 'i' + U+0307 standing for
    # fold_text('İ' + U+0307), and putting the dotless-i replace first would
    # remove one such input, not add one.
    #
    # `.replace(_DOTTED_CAPITAL_I, "i")` below is UNREACHABLE -- in every
    # ordering, because `.casefold()` has already run above and no codepoint in
    # Unicode casefolds to anything containing U+0130:
    #
    #     [c for c in range(0x110000) if "İ" in chr(c).casefold()]  # -> []
    #
    # It is kept rather than deleted as the fail-safe for anyone who later
    # moves this fold ahead of that casefold, where a bare İ WOULD arrive
    # intact. Deleting it leaves the whole suite green, so nothing but this
    # comment records why it is here.
    return (folded
            .replace("i" + _COMBINING_DOT_ABOVE, "i")
            .replace(_DOTTED_CAPITAL_I, "i")
            .replace(_DOTLESS_SMALL_I, "i"))
