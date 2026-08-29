"""The one case-folding rule the app compares user-typed text with.

ST-ARCH-001 item 9 / ST-FUNC-012. ``scheduler_app.i18n.text_fold.fold_text`` is
the single definition of "these two strings are the same string as far as a
human is concerned". Three call sites share it — ``i18n/day_keys.py``
(weekday labels), ``core/workflow.py::register_lecturer`` (the class form's
lecturer combo) and ``data_io/importer.py::_process_teachers`` (the workbook's
duplicate-teacher check) — and ``data_io/schema.py`` uses it for column and
sheet headers.

**Why this module exists, and why it is mostly a falsification harness.** The
Phase 7 handoff prescribed a *Turkish* fold ('İ'->'i', 'I'->'ı' before the
ordinary fold) as the fix. It was built and measured and it is a net
regression: it breaks 43 locale/weekday pairs, plain ASCII ``FRIDAY``,
``DIENSTAG``, ``LUNDI``, ``DOMINGO`` and the Portuguese ``-FEIRA`` days among
them, plus ``PAZARTESI`` and ``CUMARTESI``, which the suite already pins green.
The next agent to read the handoff will reach for that fix. The sweep below is
what stops them: it is parametrised over every shipped locale precisely so that
a fold which is right for Turkish and wrong for German cannot pass it.

Two rules from ``tests/README.md`` govern what is written here:

1. **No tautology.** The sweep's expected value is the day key itself, taken
   from a hand-written tuple in this file. Nothing below calls ``fold_text`` to
   work out what the answer should be. Phase 5 shipped a pill-overlap assertion
   that reduced to ``f(x) == f(x)``; the two tests here that do call
   ``fold_text`` on both sides assert *inequality* or a *collision count*,
   which a degenerate fold cannot satisfy.
2. **Anti-vacuity.** ``test_the_fold_never_merges_two_different_days`` exists
   because a fold that returned ``""`` for everything would sail through a
   sweep that only checks resolution.

Cost: pure functions, no Qt, no optimizer, no I/O — the whole module runs in
milliseconds and is in the fast lane.
"""
import ast
import io

import pytest

from scheduler_app.core.models import (
    LOCATION_LECTURER_OFFICE,
    LOCATION_ONLINE,
    parse_location_type_label,
)
from scheduler_app.i18n.day_keys import DAY_KEYS, normalize_day_value
from scheduler_app.i18n.text_fold import fold_text
from scheduler_app.translations import TRANSLATIONS


#: The seven keys, written out here rather than imported, so that the sweep's
#: expectations do not come from the same place the code under test reads them
#: from. Pinned against the real list below.
SEVEN_KEYS = ("monday", "tuesday", "wednesday", "thursday",
              "friday", "saturday", "sunday")


def turkish_upper(text):
    """Uppercase the way a Turkish keyboard or Excel's UPPER() in tr does.

    ``str.upper()`` sends 'i' to ASCII 'I'; a Turkish locale sends it to the
    dotted 'İ' and sends the dotless 'ı' to ASCII 'I'. This models the second
    behaviour so the sweep covers the casing a real Turkish user produces, not
    only the one a Python default produces. Defined here, not imported, because
    it is the *adversary* in this test, not part of the contract.
    """
    return text.replace("i", "İ").replace("ı", "I").upper()


def _label(lang, day_key):
    return str(TRANSLATIONS[lang].get(f"weekdays.{day_key}", "")).strip()


def test_the_seven_keys_this_module_sweeps_are_the_real_seven():
    """Guards this module's own fixture (no finding ID).

    Every expectation below is one of these strings. If the engine's key list
    and this tuple ever drift, the sweep would be asserting against days the
    app does not have and would pass while proving nothing.
    """
    assert tuple(DAY_KEYS) == SEVEN_KEYS


@pytest.mark.parametrize("lang", sorted(TRANSLATIONS))
def test_every_shipped_weekday_label_survives_every_casing(lang):
    """Pins ST-ARCH-001 item 9 across all 22 shipped locales.

    A failure means a user whose file, workbook or paste spells a weekday in a
    casing the app does not recognise loses that day: ``normalize_day_value``
    returns ``None``, ``normalize_state_day_keys`` filters it out of the week
    with no warning, and ``_auto_save`` writes the shortened week back to disk
    on the next debounce tick. Measured before the fix: a Turkish week typed in
    capitals opened as three days instead of six, with every affected lesson
    un-placed and its allow-list emptied.

    Parametrised per locale rather than looped so that the failure names the
    language. This is the test that falsifies the Phase 7 handoff's proposed
    Turkish fold, which resolves Turkish and breaks German, French, Spanish,
    Portuguese, Polish and plain English.
    """
    for day_key in SEVEN_KEYS:
        label = _label(lang, day_key)
        assert label, f"{lang} is missing a label for {day_key}"
        for probe in (label,
                      label.upper(),
                      label.lower(),
                      turkish_upper(label),
                      "  " + label.upper() + "  "):
            assert normalize_day_value(probe) == day_key, (
                f"{lang}/{day_key}: {probe!r} did not resolve to {day_key!r}; "
                f"this day would be silently dropped from the user's week")


def test_the_seven_day_keys_are_fold_stable():
    """Guards the fast path at ``day_keys.py`` ``if key in DAY_KEYS``.

    ``normalize_day_value`` folds first and then checks membership in
    ``DAY_KEYS`` before consulting any catalogue. A fold that rewrote 'friday'
    to something else would make that branch unreachable, and the function
    would silently start depending on English being present in
    ``TRANSLATIONS`` — working today, breaking on the day someone trims the
    shipped catalogues.

    Measured scope, so the next reader does not over-trust this one: the
    handoff's Turkish fold does NOT trip it. That fold only rewrites a capital
    ``I``, and the seven keys are already lowercase, so they survive it intact.
    (The recon plan predicted a failure here and was wrong; the sweep above is
    what actually catches that fold, on 84 of its 770 probes.) What this does
    catch is the mirror-image mistake — a fold whose output alphabet is the
    *dotless* i, ``casefold().replace('i', 'ı')`` — which is just as reachable
    for someone "making the app Turkish" and which the sweep alone would let
    through for any locale whose day names contain no i at all.
    """
    for day_key in SEVEN_KEYS:
        assert fold_text(day_key) == day_key


def test_the_fold_never_merges_two_different_days():
    """Anti-vacuity guard for the sweep above (no finding ID).

    A fold that collapsed every string to one value would resolve nothing, but
    a fold that collapsed too much could resolve every probe to the *wrong*
    day and still satisfy a test that only asks "did it resolve?". This asserts
    the other direction: across every locale, every day and three casings, no
    two different days ever fold to the same string. A failure means the app
    would confidently place a lesson on the wrong day of the week.
    """
    seen = {}
    collisions = []
    for lang in sorted(TRANSLATIONS):
        for day_key in SEVEN_KEYS:
            label = _label(lang, day_key)
            for probe in (label, label.upper(), turkish_upper(label)):
                folded = fold_text(probe)
                if seen.setdefault(folded, day_key) != day_key:
                    collisions.append(
                        f"{lang}: {probe!r} folds to {folded!r}, which already "
                        f"means {seen[folded]!r}, not {day_key!r}")
    assert not collisions, "\n  ".join([""] + collisions)


def test_the_fold_keeps_letters_that_are_not_an_i_apart():
    """Pins the boundary of the rule (ST-FUNC-012).

    The fold merges the dotted and dotless I and nothing else. The tempting
    wider rule — NFKD-normalize and strip every combining mark, "just remove
    the accents" — was measured and it makes ``Öz`` and ``Oz`` the same
    teacher, and ``Çarşamba`` the same word as ``Carsamba``. A failure here
    means two different members of staff have been merged into one: the second
    one's availability record is never applied and their name disappears from
    the roster, which is silent data loss the user cannot see.

    (It also does not even fix the reported bug: U+0131 DOTLESS I has no
    canonical decomposition, so NFKD leaves ``SALI`` broken.)
    """
    assert fold_text("Çarşamba") != fold_text("Carsamba")
    assert fold_text("Öz") != fold_text("Oz")
    assert fold_text("Ayşe") != fold_text("Ayse")


@pytest.mark.parametrize("raw,expected", [
    # Hand-written, not computed: this is the fold's actual contract.
    ("PAZARTESİ", "pazartesi"),
    ("SALI", "sali"),
    ("Salı", "sali"),
    ("İlhan Demir", "ilhan demir"),
    ("ILHAN DEMIR", "ilhan demir"),
    ("friday", "friday"),
    ("FRIDAY", "friday"),
])
def test_the_fold_sends_every_dotted_and_dotless_i_to_a_plain_ascii_i(
        raw, expected):
    """Pins ST-FUNC-012 — what the rule actually is.

    Four characters mean "i" to a reader: ASCII ``I`` and ``i``, the Turkish
    dotted capital ``İ`` and the dotless ``ı``. They all land on ASCII ``i``.
    A failure means the app has gone back to treating two spellings of one
    teacher, or one weekday, as two different things.
    """
    assert fold_text(raw) == expected


def test_the_fold_is_idempotent_and_blank_safe():
    """Guards the function's own contract (no finding ID).

    Idempotence matters because ``_auto_save`` normalizes on the way out and
    ``_auto_load`` on the way back in, so every open/save cycle applies the
    rule twice; a second pass that changed anything would make a file drift a
    little further every time it was opened. Blank-safety matters because
    ``fold_text("")`` must never be usable as a dictionary key — it is what
    ``schema.py`` keys its header map on, and a blank header that folded to a
    real value would claim a column it does not name.
    """
    for raw in ("PAZARTESİ", "SALI", "Çarşamba", "İlhan Demir",
                "  CUMARTESİ  ", "FRIDAY", "Bazar ertəsi"):
        once = fold_text(raw)
        assert fold_text(once) == once

    assert fold_text(None) == ""
    assert fold_text("") == ""


# ===========================================================================
# ANTI-DIVERGENCE: one definition, three call sites
# ===========================================================================
_SHARED_FOLD_SITES = [
    ("scheduler_app/i18n/day_keys.py", None),
    ("scheduler_app/core/workflow.py", "register_lecturer"),
    ("scheduler_app/data_io/importer.py", "_process_teachers"),
]


def _node_for(path, func_name):
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    if func_name is None:
        return tree, tree
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == func_name:
            return tree, node
    raise AssertionError(f"{path} no longer defines {func_name}")


@pytest.mark.parametrize("path,func_name", _SHARED_FOLD_SITES,
                         ids=[p for p, _ in _SHARED_FOLD_SITES])
def test_one_fold_serves_the_day_keys_the_class_form_and_the_importer(
        path, func_name):
    """Pins ST-FUNC-012 — the divergence Phase 7 refused to create.

    A Phase 7 agent was asked to make only the importer Turkish-aware and
    correctly declined: the importer and the class form would then disagree
    about whether two teachers are the same person, so one roster would hold
    one teacher and the other two. This asserts the structural version of that
    refusal — all three sites import the one ``fold_text`` and none of them
    rolls its own ``.casefold()`` or ``.lower()`` on a name or a day value.

    It names the file and the line the moment someone reintroduces one, which
    is cheaper than a behavioural test having to reproduce a workbook to say
    the same thing.
    """
    tree, node = _node_for(path, func_name)

    imports_fold = any(
        isinstance(n, ast.ImportFrom)
        and n.module == "scheduler_app.i18n.text_fold"
        and any(a.name == "fold_text" for a in n.names)
        for n in ast.walk(tree))
    assert imports_fold, (
        f"{path} no longer imports fold_text from scheduler_app.i18n.text_fold")

    rogue = [f"{path}:{n.lineno} calls .{n.attr}()"
             for n in ast.walk(node)
             if isinstance(n, ast.Attribute) and n.attr in ("casefold", "lower")]
    assert not rogue, (
        "a second case-folding rule has appeared beside fold_text:\n  "
        + "\n  ".join(rogue))


# ── the fold must reach every column of one imported row ────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("ÇEVRİMİÇİ", LOCATION_ONLINE),
    ("OFİS (ÖĞR. ELEM.)", LOCATION_LECTURER_OFFICE),
    ("Çevrimiçi", LOCATION_ONLINE),
    ("ONLINE", LOCATION_ONLINE),
])
def test_a_shouted_location_type_is_still_that_location_type(raw, expected):
    """ST-ARCH-001 item 9 — the column the fold migration first missed.

    Phase 8 routed four comparison sites through ``fold_text`` and left
    ``core/models.py::parse_location_type_label`` on ``str.casefold()``, which
    made the importer inconsistent inside a single row: two lines apart,
    ``required_room_type`` matched a shouted Turkish cell and this did not.
    Measured before the fix: ``'ÇEVRİMİÇİ'`` and ``'OFİS (ÖĞR. ELEM.)'`` both
    resolved to ``face_to_face``.

    That miss is silent, because the function's fallback is
    ``LOCATION_FACE_TO_FACE`` and a blank cell means the same thing. A failure
    means a school whose workbook is upper-cased — what a Turkish-locale
    Excel ``=UPPER()`` writes — imports every online and office lesson as
    face-to-face, and the solver then reserves a physical classroom for every
    remote lecture, inflating room demand and printing a remote lecture into a
    room on the exported timetable.

    The last two rows are the control: they worked before the fix and must
    keep working, so this cannot pass by folding everything to one string.
    """
    assert parse_location_type_label(raw) == expected
