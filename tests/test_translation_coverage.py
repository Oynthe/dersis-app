"""No user may be shown a translation key where a sentence belongs.

ST-UI-011 (Medium) · `ui/translations.py`, `ui/dialogs.py`, `core/models.py`
    The Edit Classes dialog rendered the literal string ``labels.targets`` as a
    column header in **all 22 languages**, because the key existed in none of
    them and ``tr()`` returns the key itself as a last resort.

Why a key-parity test is the wrong check, measured
--------------------------------------------------
The obvious assertion — *every locale has every key* — is red on day one
against **130** keys and **2408** (locale, key) pairs, and its failure mode is
backwards: it goes red when someone adds an English string, which is the
correct and expected thing to do. A test that punishes normal work gets deleted
or weakened, and then it guards nothing.

So this module separates three genuinely different properties, and only makes
the two that are *always* defects into hard failures:

===========================  ==========  =====================================
property                     today       treatment
===========================  ==========  =====================================
key used by code, absent      0 (was 1)  HARD FAIL — always a bug, and this is
from ``en``                              exactly ST-UI-011's shape
locale string uses a          0          HARD FAIL — this is the one that
placeholder ``en`` lacks                 *crashes*; see below
locale string that cannot     0          HARD FAIL — a stray ``}`` renders
be parsed as a format string             literally
locale missing a key ``en``   0 (was 1)  RATCHET — the last one was fixed in
has (subset placeholders)                Phase 10 and the ceiling went with it
locale missing a key          2408       RATCHET — needs a translator, not a
entirely                                 build failure
===========================  ==========  =====================================

The placeholder assertion is the load-bearing one
-------------------------------------------------
``tr()``'s ``try/except`` protects ``tr(key, **kwargs)`` and **nothing else**.
There are 174 sites using ``tr(key).format(...)`` instead — 26 of them in
``ConstraintValidator``, the hottest path in the app — and none is guarded.
Driven through the real validator, a translated string whose placeholders have
drifted from English does not degrade, it **raises**:

    adds a 4th ``{}``      -> IndexError: Replacement index 3 out of range
    renames ``{}``         -> KeyError: 'room'
    stray ``}``            -> ValueError: Single '}' encountered
    drops two ``{}``       -> no exception; the room name silently disappears

That last one is why "just machine-translate the backlog" is not available: it
passes a key-count check and a format check, and loses the user's data anyway.

**No mismatch exists today.** Exactly one did until Phase 10:
``hi``/``errors.unsupported_egu_version`` carried ``{version}`` where ``en``
carries ``{version}`` and ``{supported}``, so a Hindi user opening a ``.egu``
from another build was told the file's version but not which version their copy
can read. It is a *subset*, which ``str.format`` tolerates because it ignores
extra kwargs — so it was caught by the ratchet, not by the hard assertion, and
it sat there for nine phases. It was the ratchet's sole occupant, so fixing the
string let ``MAX_PLACEHOLDER_SUBSETS`` go to **0** in the same commit: the
banked-headroom rule working as designed, and the ceiling now forbids the class
of defect outright rather than tolerating one instance of it.
"""
import ast
import os
import re
import string

import pytest

# The tier catalogue merges 52 keys into TRANSLATIONS on import. A checker that
# does not import it reports 11 false failures for keys that resolve fine at
# runtime -- measured. Import it before reading the catalogue.
import scheduler_app.i18n.tier_translations  # noqa: F401
from scheduler_app.i18n.translations import TRANSLATIONS

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(REPO_ROOT, "scheduler_app")

EN = TRANSLATIONS["en"]

# ── the backlog, as it stood when this module was written ───────────────
# These are ratchets: the numbers may go DOWN freely. They may not go up
# without a deliberate edit here, which is the review point.
#
# 2408 was the figure before this phase. Phase 5 adds five keys to en+tr —
# `labels.targets`, `errors.duration_required`, the two
# `explanation.component.stability.*`, and `a11y.lane_position` for the
# ST-UI-004 cursor announcement — each of which is then absent from the other
# 20 locales, so the bound moves by exactly 5 x 20 = 100.
#
# The ratchet caught both additions on the very commits that introduced them,
# which is the behaviour wanted: adding an English string is normal work, and
# the test asks for an explicit acknowledgement rather than failing the build.
#
# Phase 7 adds **two** keys to en+tr, so the bound moves by exactly 2 x 20 = 40,
# from 2508 to 2548:
#
#   * `export.unprintable_note` -- the one string that says which characters the
#     printed timetable could not draw (ST-FUNC-004, `data_io/exporter.py`). No
#     existing key means this; the appendix-reuse trick used a few lines away in
#     that file does not apply, because "Not on the timetable" is about lessons,
#     not glyphs.
#   * `bug_report.no_mail_client` -- the last hardcoded English message box in
#     `scheduler_app/ui`.
#
# In both cases the alternative to minting a key was leaving English on screen
# in a Turkish-first app.
#
# **Measured, and it corrects a figure that circulated through this phase's own
# briefings.** The backlog is *not* ~1660 with ~848 pairs of headroom. That
# count comes from reading `TRANSLATIONS` without importing
# `scheduler_app.i18n.tier_translations`, which merges 52 further `en` keys into
# the catalogue on import. This module imports it (see above) precisely so the
# check cannot be taken against a half-built catalogue. Counted the way this
# test counts, the backlog stood at **2508 against a 2508 ceiling -- zero
# slack**.
#
# Two independent agents hit this within the same hour, each moving the ceiling
# by 20 for its own key and each arriving at 2528; the merge is what revealed
# that two keys had landed. Anyone planning a batch of new English strings
# should measure **with the tier catalogue imported** before assuming room.
#
# The student-overlap feature adds 16 English/Turkish UI strings: four course
# type strings (heading plus three values), eight overlap form/policy/help
# strings and four context-menu strings.  The other 20 locales deliberately
# fall back to English until translated, so this reviewed feature moves the
# ceiling by 16 x 20 = 320.  Complete-schedule controls and the same-classroom
# series rule add another 12 reviewed English/Turkish strings (12 x 20 = 240).
MAX_MISSING_LOCALE_KEY_PAIRS = 3108
MAX_PLACEHOLDER_SUBSETS = 0


def _iter_source_files():
    for root, dirs, files in os.walk(PACKAGE):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f.endswith(".py") and f not in ("translations.py",
                                               "tier_translations.py"):
                yield os.path.join(root, f)


def literal_tr_keys():
    """Every ``tr("literal")`` key in the package, via AST rather than regex.

    A regex over the source also matches keys inside comments and docstrings
    and misses ``tr(\n "key")``. The AST sees what Python sees.
    """
    found = {}
    for path in _iter_source_files():
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        try:
            tree = ast.parse(src, filename=path)
        except SyntaxError:                      # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name != "tr" or not node.args:
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.setdefault(arg.value, set()).add(
                    os.path.relpath(path, REPO_ROOT))
    return found


def dynamic_key_domains():
    """Keys built at runtime, whose domains are bounded and enumerable.

    A checker that only sees ``tr("literal")`` is blind to exactly the category
    ST-UI-011 lives in: a lookup table with a missing row degrades to a raw key
    with no literal anywhere to grep for. That is how ``stability`` reached the
    explanation panel as a bare Python identifier.

    **``labels.targets`` — the finding's own key — is in this category too**,
    and discovering that is what stopped this module from certifying the bug.
    It is held in the ``_CLASS_IO_FIELDS`` tuple table, never written inside a
    ``tr(...)`` call, so the AST scan above cannot see it: removing it from the
    catalogue again left every assertion green. Any table that pairs a field
    with a translation key belongs here.
    """
    from scheduler_app.i18n.badge_formatter import _BADGE_MAP
    from scheduler_app.core.explanation_engine import _COMPONENT_INFO
    from scheduler_app.ui.dialogs import _CLASS_IO_FIELDS
    from scheduler_app.data_io.schema import WORKBOOK_SHEETS

    keys = {}
    for prot, (_emoji, key, _colour) in _BADGE_MAP.items():
        keys[key] = "badge_formatter._BADGE_MAP[%r]" % prot
    for comp, info in _COMPONENT_INFO.items():
        for field in ("label_key", "positive_key", "negative_key"):
            if info.get(field):
                keys[info[field]] = (
                    "explanation_engine._COMPONENT_INFO[%r][%r]" % (comp, field))
    for field, label_key, _fallback in _CLASS_IO_FIELDS:
        keys[label_key] = "dialogs._CLASS_IO_FIELDS[%r]" % field
    for sheet_id, spec in WORKBOOK_SHEETS.items():
        keys[spec["title_key"]] = "schema.WORKBOOK_SHEETS[%r].title" % sheet_id
        for field, label_key, desc_key in spec["columns"]:
            keys[label_key] = (
                "schema.WORKBOOK_SHEETS[%r].columns[%r]" % (sheet_id, field))
            if desc_key:
                keys[desc_key] = (
                    "schema.WORKBOOK_SHEETS[%r].descriptions[%r]"
                    % (sheet_id, field))
    return keys


_FIELD = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)?[^{}]*\}")


def placeholders(text):
    """The set of field names a format string references ('' for positional)."""
    return frozenset(m.group(1) or "" for m in _FIELD.finditer(text))


# ══ hard failures ═══════════════════════════════════════════════════════

def test_every_key_the_code_asks_for_exists_in_english():
    """ST-UI-011 — the exact shape of the finding: a key with no string.

    ``tr()`` falls back to English and, when English does not have it either,
    to the key itself. So a key missing from ``en`` is shown raw to **every**
    user in **every** language. A failure here means someone shipped a literal
    like ``labels.targets`` into the UI.
    """
    missing = {k: sorted(v) for k, v in literal_tr_keys().items()
               if k not in EN}
    assert not missing, (
        "these keys are passed to tr() but have no English string, so every "
        "user sees the key itself:\n  "
        + "\n  ".join("%s  <- %s" % (k, ", ".join(v))
                      for k, v in sorted(missing.items()))
    )


def test_every_runtime_built_key_exists_in_english():
    """ST-UI-011 — the blind spot a grep for tr("literal") cannot see.

    ``stability`` was exactly this: a scorer component with no row in
    ``_COMPONENT_INFO``, so the label fell through to ``tr("stability")`` and
    rendered a bare Python identifier as a user-facing string.
    """
    missing = {k: where for k, where in dynamic_key_domains().items()
               if k not in EN}
    assert not missing, (
        "these keys are built at runtime and have no English string:\n  "
        + "\n  ".join("%s  <- %s" % (k, w) for k, w in sorted(missing.items()))
    )


def test_every_runtime_built_key_exists_in_turkish():
    """ST-UI-011 — English is a fallback, not the product's default language.

    ``tr`` is what this app ships in and what the suite pins, so a key present
    only in ``en`` still shows a Turkish user an English word. That is the
    weaker half of the finding and it is invisible to the English assertion
    above: with ``labels.targets`` in ``en`` alone, the Edit Classes header
    reads ``Target Groups`` for a Turkish school.
    """
    missing = {k: where for k, where in dynamic_key_domains().items()
               if k not in TRANSLATIONS["tr"]}
    assert not missing, (
        "these keys have no Turkish string, so the default language falls "
        "back to English:\n  "
        + "\n  ".join("%s  <- %s" % (k, w) for k, w in sorted(missing.items()))
    )


def test_no_translation_references_a_placeholder_english_does_not_have():
    """ST-UI-011 — the drift that raises rather than degrades.

    174 sites call ``tr(key).format(...)``, which ``tr()``'s own try/except does
    not cover, and 26 of those are in ``ConstraintValidator``. A locale string
    that renames or adds a field raises ``KeyError`` / ``IndexError`` straight
    out of the validation path.
    """
    offenders = []
    for lang, catalogue in sorted(TRANSLATIONS.items()):
        if lang == "en":
            continue
        for key, text in sorted(catalogue.items()):
            if key not in EN:
                continue
            extra = placeholders(text) - placeholders(EN[key])
            if extra:
                offenders.append(
                    "%s/%s references %s, absent from en (%r)"
                    % (lang, key, sorted(extra), EN[key]))
    assert not offenders, (
        "a translated string uses a placeholder English does not supply; "
        "str.format raises on these:\n  " + "\n  ".join(offenders))


@pytest.mark.parametrize("lang", sorted(TRANSLATIONS))
def test_every_string_in_every_locale_is_a_parseable_format_string(lang):
    """ST-UI-011 — a stray brace renders literally or raises.

    ``'{n 件'`` and ``'... } ...'`` both survive a key-count check and both
    reach the user.
    """
    bad = []
    for key, text in sorted(TRANSLATIONS[lang].items()):
        try:
            list(string.Formatter().parse(text))
        except ValueError as exc:
            bad.append("%s: %r (%s)" % (key, text, exc))
    assert not bad, "unparseable format strings in %r:\n  %s" % (
        lang, "\n  ".join(bad))


# ══ ratchets — a backlog, not a defect ══════════════════════════════════

def test_the_translation_backlog_does_not_grow():
    """ST-UI-011 — locale coverage is a translator's job, not a build failure.

    Asserting parity would be red on day one against 2408 pairs and would go
    red again for every English string anyone adds. This bound may be lowered
    freely; raising it is the deliberate act that should draw a reviewer's eye.
    """
    missing = sum(1 for lang, cat in TRANSLATIONS.items() if lang != "en"
                  for key in EN if key not in cat)
    assert missing <= MAX_MISSING_LOCALE_KEY_PAIRS, (
        "the (locale, key) gap grew from %d to %d. New user-facing strings are "
        "expected to land in en+tr first, but if this jumped, check whether a "
        "whole locale was dropped."
        % (MAX_MISSING_LOCALE_KEY_PAIRS, missing))


def test_placeholder_subsets_do_not_grow():
    """ST-UI-011 — the silent half: a translation that drops a placeholder.

    ``str.format`` ignores extra kwargs, so this never raises — the number just
    vanishes from the sentence. The ceiling is **0**: the one string that used
    to sit under it (``hi``/``errors.unsupported_egu_version``) was fixed in
    Phase 10 and the ceiling was lowered in the same commit, so this is now an
    absolute prohibition rather than a tolerance of one.
    """
    subsets = []
    for lang, catalogue in sorted(TRANSLATIONS.items()):
        if lang == "en":
            continue
        for key, text in sorted(catalogue.items()):
            if key not in EN:
                continue
            if placeholders(EN[key]) - placeholders(text):
                subsets.append("%s/%s" % (lang, key))
    assert len(subsets) <= MAX_PLACEHOLDER_SUBSETS, (
        "a translation dropped a placeholder English supplies, so the value it "
        "carried disappears from the sentence with no error:\n  %s"
        % "\n  ".join(subsets))


# ══ the specific regressions ST-UI-011 named ════════════════════════════

def test_the_edit_classes_targets_header_is_a_word_not_a_key():
    """ST-UI-011 — the headline defect, asserted where the user sees it."""
    from scheduler_app.translations import set_language
    from scheduler_app.ui.dialogs import _class_io_headers

    try:
        for lang in sorted(TRANSLATIONS):
            set_language(lang)
            header = _class_io_headers()[6]
            assert header != "labels.targets", (
                "the Targets column header is the raw key in %r" % lang)
            assert not header.startswith("labels."), (
                "header %r in %r still looks like a key" % (header, lang))
    finally:
        set_language("tr")


def test_a_workbook_exported_before_the_fix_still_imports_its_targets():
    """ST-UI-011 — the fix must not orphan the files it already wrote.

    While the key was missing, ``tr()`` returned it, so every workbook DERSİS
    exported carried the literal ``labels.targets`` as its Targets header — and
    re-imported it, because the reverse map registered whatever ``tr()``
    returned. Once the key resolves, that literal would fall out of the map and
    every previously exported file would import with no target groups: a silent
    data loss caused by the fix itself.
    """
    from scheduler_app.ui.dialogs import _canonicalize_class_io_row

    row = _canonicalize_class_io_row(
        {"labels.targets": "Year-1/A", "Class Name": "Fizik"})
    assert row.get("targets") == "Year-1/A", (
        "a legacy workbook header no longer maps to the targets field: %r" % row)
