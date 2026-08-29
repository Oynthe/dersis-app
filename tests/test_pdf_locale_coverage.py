"""What the PDF can spell in each of the 22 shipped languages — ST-FUNC-004.

ST-FUNC-004 was closed on twelve Turkish letters. The product ships 22
languages and bundles one face, Vera: 283 glyphs, Latin-1 and no more. Measured
against the weekday names plus the UI strings ``_pdf_document_text`` collects,
Vera cannot draw a single character of **9 of the 22** locales — ar, az, fa,
hi, ja, ko, pl, ru, zh. Before ``_resolve_pdf_fonts`` existed, those nine
printed a row of empty boxes and nothing said so.

**These tests assert the property, never the host outcome.** On this Windows
machine six of the nine recover from host faces (az/pl/ru → arial, ja →
msgothic, zh → msyh, ko → malgun) and only ar, fa and hi reach the note page.
On ``ubuntu-latest``, with none of those faces installed, the same code
correctly falls back to the note page for far more of them. Both are right. A
test that pinned "az resolves to arial" or "exactly 3 locales fail" would pass
here and fail in CI while the code was working perfectly in both places, so
what is pinned instead is the invariant that holds everywhere:

    we never silently drop a character we could have drawn.

The one test below that *does* assert a fixed number reads glyph tables out of
the Vera file inside the reportlab wheel, which is the same bytes on every
platform.

Pure measurement against ``scheduler_app.data_io.exporter``. No PDF is
rendered and nothing is written to disk.
"""
import pytest

pytest.importorskip(
    "reportlab",
    reason="reportlab is the PDF backend; without it there is no font to "
           "resolve and nothing here has a subject",
)

from scheduler_app.data_io import exporter as ex  # noqa: E402
from scheduler_app.i18n import translations as ex_i18n  # noqa: E402
from scheduler_app.translations import TRANSLATIONS  # noqa: E402

pytestmark = pytest.mark.pdf

# The nine locales the bundled face cannot spell. This is the ST-FUNC-004
# finding itself, restated as data.
VERA_CANNOT_SPELL = {"ar", "az", "fa", "hi", "ja", "ko", "pl", "ru", "zh"}

# The UI keys `_pdf_document_text` pulls, mirrored here so this file measures
# the same corpus the exporter actually resolves fonts against.
UI_KEYS = (
    "labels.time", "labels.type", "labels.class_code", "labels.class_name",
    "labels.lecturer", "labels.day", "export.appendix_title",
    "export.appendix_offgrid", "export.appendix_conflict",
    "warnings.no_schedule_data",
)

# Faces registered by `_register_covering_font` all carry this prefix.
SUBSTITUTE_PREFIX = "DersisSys-"


def _locale_text(locale):
    """Weekday names + the UI strings, for one locale."""
    table = TRANSLATIONS[locale]
    keys = [k for k in TRANSLATIONS["en"] if k.startswith("weekdays.")]
    keys += list(UI_KEYS)
    return "".join(table.get(k, TRANSLATIONS["en"][k]) for k in keys)


def _locales():
    return sorted(TRANSLATIONS)


def test_the_shipped_locale_set_is_still_the_one_these_tests_measure():
    """A language added without a PDF font story is a silent regression.

    If someone ships a 23rd locale, the tests below start covering it
    automatically — but the ST-FUNC-004 characterization list would silently
    describe a set that no longer matches the product. This is the tripwire.
    """
    assert len(TRANSLATIONS) == 22, (
        "the product now ships %d locales, not 22. That is fine, but re-measure "
        "VERA_CANNOT_SPELL in this file: a new language whose script the bundled "
        "face cannot draw needs to be in that set, or the characterization test "
        "below is describing a product that no longer exists." % len(TRANSLATIONS)
    )
    assert VERA_CANNOT_SPELL <= set(TRANSLATIONS), (
        "VERA_CANNOT_SPELL names %s, which the product no longer ships"
        % sorted(VERA_CANNOT_SPELL - set(TRANSLATIONS))
    )


def test_vera_alone_cannot_spell_nine_of_the_twenty_two_locales():
    """ST-FUNC-004 — the finding itself, pinned so it cannot be talked away.

    A failure means the bundled-font coverage changed underneath us: either
    reportlab shipped a different Vera, or a locale's strings changed script.
    Either way the "9 of 22" figure quoted throughout ``exporter.py`` and the
    handoff notes has stopped being true and every conclusion drawn from it
    needs re-deriving.

    This reads glyph tables from the font file inside the reportlab wheel, so
    it is the same answer on Windows, macOS and the CI runner.
    """
    regular, bold = ex._register_unicode_fonts()
    unspellable = set()
    for locale in _locales():
        text = _locale_text(locale)
        if (ex._chars_without_glyphs(regular, text)
                or ex._chars_without_glyphs(bold, text)):
            unspellable.add(locale)

    assert unspellable == VERA_CANNOT_SPELL, (
        "the bundled face now fails %s, not the recorded %s. The '9 of 22' "
        "measurement in exporter.py's font section is derived from this exact "
        "set; if this changed, that comment block is now wrong too."
        % (sorted(unspellable), sorted(VERA_CANNOT_SPELL))
    )


def test_no_locale_loses_a_character_the_pdf_could_have_drawn():
    """ST-FUNC-004 — a character dropped from the page with nothing said.

    For a user this is the original bug in its purest form: a weekday header
    that is a row of empty boxes, in a document that reports no problem.

    The contract is that ``_resolve_pdf_fonts`` returns two faces and a list of
    what it gave up on, and that the list is *complete* — everything NOT on it
    must be drawable by the faces it returned. Host-independent by
    construction: it re-derives the answer from whatever faces this machine
    actually produced rather than assuming which ones those are.
    """
    for locale in _locales():
        text = _locale_text(locale)
        regular, bold, unprintable = ex._resolve_pdf_fonts(text)
        drawable = "".join(ch for ch in text if ch not in unprintable)

        for face, role in ((regular, "regular"), (bold, "bold")):
            silent = ex._chars_without_glyphs(face, drawable)
            assert not silent, (
                "locale %r: _resolve_pdf_fonts chose %s face %r and reported "
                "%d unprintable characters, but %s of the characters it did "
                "NOT report have no glyph in that face either (%s). Those are "
                "drawn as empty boxes with nothing on the page explaining why "
                "— which is ST-FUNC-004 exactly."
                % (locale, role, face, len(unprintable), len(silent),
                   " ".join("U+%04X" % ord(c) for c in sorted(silent)))
            )


def test_a_shaped_script_is_never_silently_given_a_substitute_face():
    """ST-FUNC-004 — confidently wrong output in place of an honest box.

    Arabic, Persian and Hindi need a layout engine, not just a font. Measured:
    registering arial.ttf and drawing "العربية" emits the seven codepoints in
    LOGICAL order, each in its isolated form — a word spelled backwards in
    disconnected letters. For a user that is worse than a box, because it looks
    like text and cannot be recognised as broken.

    So ``_resolve_pdf_fonts`` deliberately short-circuits: if any missing
    character needs shaping, no covering face is tried at all and the note page
    explains what could not be spelled. A failure here means that guard was
    removed or inverted, and the PDF has started quietly printing scrambled
    Arabic.
    """
    regular_bundled, bold_bundled = ex._register_unicode_fonts()
    checked = []
    for locale in _locales():
        text = _locale_text(locale)
        shaped = {ch for ch in text if ex._needs_text_shaping(ch)}
        if not shaped:
            continue
        checked.append(locale)

        regular, bold, unprintable = ex._resolve_pdf_fonts(text)

        for face, role in ((regular, "regular"), (bold, "bold")):
            assert not face.startswith(SUBSTITUTE_PREFIX), (
                "locale %r contains %d characters from a script reportlab "
                "cannot lay out, yet _resolve_pdf_fonts substituted host %s "
                "face %r. A covering font does not buy correct output here — "
                "it buys text in logical order with isolated letterforms, "
                "which reads as gibberish to anyone who speaks the language."
                % (locale, len(shaped), role, face)
            )

        undrawable = shaped & (ex._chars_without_glyphs(regular_bundled, text)
                               | ex._chars_without_glyphs(bold_bundled, text))
        assert undrawable <= unprintable, (
            "locale %r: %s are shaped-script characters the bundled face "
            "cannot draw, and _resolve_pdf_fonts did not report them as "
            "unprintable. They will be blank on the page and the note that "
            "explains the blanks will not mention them."
            % (locale, " ".join("U+%04X" % ord(c)
                                for c in sorted(undrawable - unprintable)))
        )

    assert checked, (
        "no shipped locale contains a shaped script any more. If that is real "
        "the short-circuit in _resolve_pdf_fonts is now dead code; if it is "
        "not, _needs_text_shaping has stopped matching and this test is "
        "passing vacuously."
    )


def test_the_note_page_never_hides_a_character_behind_a_missing_glyph():
    """ST-FUNC-004 — the explanation is itself unreadable.

    ``_unprintable_note`` names codepoints rather than characters precisely
    because the characters are the ones the document cannot draw. If the note
    were written in a face that cannot spell the note, the page would carry a
    second row of boxes where the explanation should be — the failure
    explaining itself in the language of the failure.
    """
    original = ex_i18n.get_language()
    try:
        for locale in _locales():
            _assert_note_is_legible(locale)
    finally:
        ex_i18n.set_language(original)


def test_the_note_falls_back_to_english_when_its_own_language_is_unspellable():
    """ST-FUNC-004 — the fallback that is currently load-bearing for nobody.

    ``_unprintable_note`` re-renders in English when the chosen face cannot
    draw the localized wording. On the shipped tables that branch is
    UNREACHABLE, and the test above cannot pin it: ``export.unprintable_note``
    is translated only for ``tr`` and ``en``, so the three locales that ever
    reach the note page (ar, fa, hi) already receive English from ``tr()``.
    Measured — deleting the fallback entirely left every other test in this
    file green.

    So this test supplies the input that makes the branch reachable: a
    translator filling in the Arabic wording, which is an ordinary thing for
    someone to do and would, without the fallback, turn the note explaining
    23 boxes into a note made of 16 more. Measured both ways — fallback
    removed: 16 undrawable characters in the note; fallback present: 0.

    A failure here means a Persian or Arabic user gets a PDF whose only
    explanation of the blank cells is itself blank.
    """
    from scheduler_app.translations import TRANSLATIONS as TABLES

    locale = "ar"
    key = "export.unprintable_note"
    original_lang = ex_i18n.get_language()
    had_key = key in TABLES[locale]
    previous = TABLES[locale].get(key)
    try:
        TABLES[locale][key] = "لا يوجد خط يمكنه رسم {count} حرف"
        ex_i18n.set_language(locale)
        text = _locale_text(locale)
        regular, _bold, unprintable = ex._resolve_pdf_fonts(text)
        assert unprintable, (
            "locale %r no longer reaches the note page at all, so this test has "
            "lost its subject. If a covering face now handles Arabic, the "
            "shaping short-circuit changed and that is the bigger news."
            % locale
        )
        note = ex._unprintable_note(regular, unprintable)
        blind = ex._chars_without_glyphs(regular, note)
        assert not blind, (
            "with %r translated into Arabic, the note explaining %d unprintable "
            "characters is itself unprintable (%d characters have no glyph in "
            "%r). The English fallback in _unprintable_note is what prevents "
            "this; it is unreachable on today's translation tables, so nothing "
            "else in the suite would notice it being removed."
            % (key, len(unprintable), len(blind), regular)
        )
    finally:
        if had_key:
            TABLES[locale][key] = previous
        else:
            TABLES[locale].pop(key, None)
        ex_i18n.set_language(original_lang)


def _assert_note_is_legible(locale):
    """One locale's note page, with the UI language already switched to it."""
    ex_i18n.set_language(locale)
    text = _locale_text(locale)
    regular, _bold, unprintable = ex._resolve_pdf_fonts(text)
    if not unprintable:
        return
    note = ex._unprintable_note(regular, unprintable)
    assert note.strip(), (
        "locale %r reports %d unprintable characters and produced an empty "
        "note. The blanks would then appear with no explanation at all."
        % (locale, len(unprintable))
    )
    blind = ex._chars_without_glyphs(regular, note)
    assert not blind, (
        "locale %r: the unprintable-characters note cannot itself be drawn "
        "by the face the document uses (%s missing from %r). The note "
        "exists to explain a row of boxes; rendering it as a second row of "
        "boxes explains nothing."
        % (locale, " ".join("U+%04X" % ord(c) for c in sorted(blind)), regular)
    )
