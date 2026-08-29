"""Phase 10 · Item 9 — the Hindi "unsupported file version" message loses half
its information, and the placeholder ratchet has been carrying it since it was
written.

``errors.unsupported_egu_version`` is the one sentence DERSİS shows a user whose
``.egu`` was written by a different build. English says::

    Unsupported file version: {version} (expected {supported}).

Twenty-one of the twenty-two catalogues say both halves. ``hi`` says::

    असमर्थित फ़ाइल संस्करण: {version}

``storage._parse_container`` renders it with ``tr(key).format(version=...,
supported=...)`` — one of the 174 unguarded ``.format`` sites
``tests/test_translation_coverage.py`` documents — and ``str.format`` silently
ignores an argument the template does not reference. So nothing raises: the
Hindi user is told which version the *file* is and is never told which version
their own copy can read, which is the only half of the sentence that tells them
what to do about it.

This module measures the two things that matter and nothing else:

``test_the_hindi_message_names_the_version_this_build_can_read``
    RED today. Drives the real loader over a real container header, with the
    real ``tr()`` and the real language switch. The supported number is chosen
    by monkeypatching ``storage._FORMAT_VERSION`` to 7 against a file stamped 3,
    so "7" cannot appear in the message by coincidence.

``test_no_locale_drops_a_placeholder_that_english_supplies``
    RED today, with exactly one offender. This is the catalogue-level statement
    of the same defect, taken with the *same* detector
    ``test_translation_coverage.test_placeholder_subsets_do_not_grow`` uses —
    imported from that module rather than re-implemented, so the two cannot
    drift. Its failure text is the proof that ``hi``/``errors.unsupported_egu_version``
    is the SOLE occupant of ``MAX_PLACEHOLDER_SUBSETS = 1``, i.e. that the
    ratchet may be lowered to 0 in the same commit as the fix.

``test_every_other_locale_already_names_both_versions``
    GREEN today, all 21 non-``hi`` locales. The control: without it, the test
    above is satisfiable by a detector that reports everything.

``test_fixing_the_hindi_string_cannot_move_the_missing_key_backlog``
    GREEN today and after. ``hi`` already HAS the key; only its text is wrong.
    So ``MAX_MISSING_LOCALE_KEY_PAIRS`` (2548, and measured at exactly 2548 —
    zero headroom) is untouched by this fix and must not be edited for it.

**The tier catalogue is imported first, on purpose.**
``scheduler_app.i18n.tier_translations`` merges 52 further ``en`` keys into
``TRANSLATIONS`` at import time. Reading the catalogue without it reports a
backlog of ~1700 and invents ~800 pairs of headroom that do not exist. Three
phases in a row have made that mistake.
"""
import struct

import pytest

# MUST come before the TRANSLATIONS import is *used*: the tier catalogue merges
# 52 en keys in on import, and every count below is wrong without it.
import scheduler_app.i18n.tier_translations  # noqa: F401
from scheduler_app.i18n.translations import TRANSLATIONS, get_language, set_language

# tests/ is on sys.path (tests/conftest.py). Reuse the ratchet's OWN detector so
# this probe cannot measure something subtly different from the thing pinned.
from test_translation_coverage import (  # noqa: E402
    MAX_MISSING_LOCALE_KEY_PAIRS, MAX_PLACEHOLDER_SUBSETS, placeholders)

KEY = "errors.unsupported_egu_version"
EN = TRANSLATIONS["en"]


@pytest.fixture
def language():
    """Set the UI language and put it back, whatever the test does.

    ``set_language`` writes a module global that the whole session shares, and
    ``tests/conftest.py`` pins it to ``tr`` for the Excel importer's sake.
    """
    previous = get_language()

    def _set(lang):
        set_language(lang)
        assert get_language() == lang, (
            "set_language(%r) did not take — the locale is not in TRANSLATIONS "
            "and this test would silently measure English" % (lang,))
    try:
        yield _set
    finally:
        set_language(previous)


# ── the defect, driven through the real read path ───────────────────────────

def test_the_hindi_message_names_the_version_this_build_can_read(
        dersis_home, language, monkeypatch, tmp_path):
    """Item 9 — a Hindi user is told the file's version and not their own.

    Driven through ``storage.load_encrypted``, which is what the Open handler
    calls, over a byte-exact EGU1 header. Nothing here plants a message: the
    string comes out of the catalogue through ``tr()`` and is rendered by
    ``storage._parse_container``'s own ``.format(version=..., supported=...)``.

    ``_FORMAT_VERSION`` is moved to 7 for the duration so the number the
    sentence is missing is one that cannot appear anywhere else in it — the
    file is stamped 3. On the shipped tree the message reads::

        असमर्थित फ़ाइल संस्करण: 3

    with no 7 in it, while the same file in any other language names both.
    """
    from scheduler_app.storage import storage

    monkeypatch.setattr(storage, "_FORMAT_VERSION", 7)
    file_version = 3

    # Version is checked before any size/checksum work, so a header plus the
    # minimum tail is all that is needed to reach it. Built from the module's
    # own constants rather than hard-coded offsets.
    blob = struct.pack(storage._HEADER_FMT, storage._MAGIC, file_version)
    blob += b"\x00" * (storage._SALT_LEN + storage._IV_LEN
                       + struct.calcsize(storage._PAYLOAD_LEN_FMT)
                       + storage._CHECKSUM_LEN)
    path = tmp_path / "from_a_newer_build.egu"
    path.write_bytes(blob)

    language("hi")
    with pytest.raises(storage.EguFileError) as caught:
        storage.load_encrypted(str(path))
    message = str(caught.value)

    assert str(file_version) in message, (
        "the message does not even name the file's version, so this run "
        "reached some other error and measures nothing: %r" % (message,))
    assert str(7) in message, (
        "the Hindi message tells the user the file is version %s and never "
        "tells them which version their copy of DERSİS can read, so there is "
        "nothing in the sentence to act on.\n"
        "  shown to a Hindi user : %r\n"
        "  hi template           : %r\n"
        "  en template           : %r\n"
        "  placeholders lost     : %r\n"
        "str.format ignores the extra keyword, so this never raises — half the "
        "sentence just is not there."
        % (file_version, message, TRANSLATIONS["hi"][KEY], EN[KEY],
           sorted(placeholders(EN[KEY]) - placeholders(TRANSLATIONS["hi"][KEY]))))


@pytest.mark.parametrize(
    "lang", [l for l in sorted(TRANSLATIONS) if l != "hi"])
def test_every_other_locale_already_names_both_versions(
        dersis_home, language, monkeypatch, tmp_path, lang):
    """The control — 21 of 22 locales pass the assertion above unchanged.

    Without this, the test above is satisfied by a broken probe (a detector
    that flags everything, a header that never reaches the version check).
    """
    from scheduler_app.storage import storage

    monkeypatch.setattr(storage, "_FORMAT_VERSION", 7)
    blob = struct.pack(storage._HEADER_FMT, storage._MAGIC, 3)
    blob += b"\x00" * (storage._SALT_LEN + storage._IV_LEN
                       + struct.calcsize(storage._PAYLOAD_LEN_FMT)
                       + storage._CHECKSUM_LEN)
    path = tmp_path / "from_a_newer_build.egu"
    path.write_bytes(blob)

    language(lang)
    with pytest.raises(storage.EguFileError) as caught:
        storage.load_encrypted(str(path))
    message = str(caught.value)

    assert "3" in message and "7" in message, (
        "%r does not name both versions either, so the defect is wider than "
        "item 9 says: %r (template %r)"
        % (lang, message, TRANSLATIONS[lang].get(KEY)))


# ── the catalogue-level statement, with the ratchet's own detector ──────────

def test_no_locale_drops_a_placeholder_that_english_supplies():
    """Item 9 — zero, not one, and the failure text names every occupant.

    ``tests/test_translation_coverage.py`` allows ``MAX_PLACEHOLDER_SUBSETS =
    1`` and its docstring names this exact pair as the one. This asserts the
    correct end state instead: a translation that drops a placeholder loses the
    value it carried with no error anywhere, so the right number is 0.

    The listing this prints IS the sole-occupant check the item asks for: if it
    ever shows two lines, the ratchet cannot be lowered to 0 by fixing ``hi``.
    """
    offenders = []
    for lang, catalogue in sorted(TRANSLATIONS.items()):
        if lang == "en":
            continue
        for key, text in sorted(catalogue.items()):
            if key not in EN:
                continue
            lost = placeholders(EN[key]) - placeholders(text)
            if lost:
                offenders.append(
                    "%s/%s drops %s\n      en: %r\n      %s: %r"
                    % (lang, key, sorted(lost), EN[key], lang, text))

    assert offenders == [], (
        "%d translation(s) drop a placeholder English supplies. str.format "
        "ignores the surplus keyword, so the value simply vanishes from the "
        "sentence and nothing raises:\n    %s\n"
        "MAX_PLACEHOLDER_SUBSETS in tests/test_translation_coverage.py is "
        "currently %d; it may be lowered to %d once these are fixed."
        % (len(offenders), "\n    ".join(offenders),
           MAX_PLACEHOLDER_SUBSETS, 0))


def test_fixing_the_hindi_string_cannot_move_the_missing_key_backlog():
    """The other ratchet must NOT be touched for this item.

    ``hi`` already holds ``errors.unsupported_egu_version``; only its text is
    wrong. Rewriting a string that is already present changes no (locale, key)
    pair, so ``MAX_MISSING_LOCALE_KEY_PAIRS`` stays where it is. Measured with
    the tier catalogue imported — the count is **2548 against a ceiling of
    2548**, i.e. zero headroom, and any agent who reads it as ~1700 has
    forgotten the import at the top of this file.
    """
    assert KEY in TRANSLATIONS["hi"], (
        "the Hindi catalogue no longer has %r at all; this item is now a "
        "missing-key case and DOES move MAX_MISSING_LOCALE_KEY_PAIRS" % KEY)

    missing = sum(1 for lang, cat in TRANSLATIONS.items() if lang != "en"
                  for key in EN if key not in cat)
    assert missing == MAX_MISSING_LOCALE_KEY_PAIRS, (
        "the (locale, key) backlog is %d against a ceiling of %d. This probe "
        "records the figure so a later phase cannot claim headroom that is not "
        "there; if this fails, someone added or translated English strings and "
        "the number in this docstring is stale."
        % (missing, MAX_MISSING_LOCALE_KEY_PAIRS))
