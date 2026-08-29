"""Day-key normalization on open / import (ST-ARCH-001, top-10 item 9).

``scheduler_app.i18n.day_keys.normalize_state_day_keys`` is the single point
where a schedule's weekday fields are converted from whatever the user's file,
locale or Excel workbook happened to contain into the seven stable keys the
engine indexes on. It runs on every load (``ui/app.py`` ``_auto_load``), every
save (``_auto_save``, Save As), the fingerprint, and Open.

**Why this module exists.** The Phase 7 measurement round stubbed the whole
function to ``return state`` and ran the entire CI lane: ``EXIT=0``, zero
failures. Five distinct behaviours — label to key, case-fold de-duplication,
pruning an allow-list down to the days that exist on this grid, un-placing a
lesson whose day left the grid, and un-pinning a pin whose day left the grid —
were carried by nothing at all.

Two reasons the suite could not see it, both worth knowing before adding a test
here:

1. **Tautology.** ``tests/test_settings_recovery.py`` builds its *expected*
   value by calling ``normalize_state_day_keys`` on a deep copy. Break the
   function and the expectation breaks with it, in the same direction. Every
   expectation in this module is therefore a hand-written literal. Nothing below
   may call the function under test to work out what the answer should be.
2. **Deliberate abstention.** ``tests/test_import_ui_flow.py:269-275`` compares
   imported lecturer availability by lecturer *name* only, on purpose, so as not
   to canonize the pre-normalization shape. Reasonable in isolation; the
   consequence was that nothing asserted the post-normalization shape either.

Cost: pure functions, no Qt, no optimizer, no I/O — the whole module runs in
milliseconds and is in the fast lane.
"""
import copy

import pytest

from scheduler_app.i18n.day_keys import (
    DAY_KEYS,
    normalize_day_list,
    normalize_day_value,
    normalize_state_day_keys,
)


def _state(days, classes=(), availability=None):
    """A bare state carrying only the fields the normalizer touches."""
    return {
        "days": list(days),
        "classes": [dict(c) for c in classes],
        "lecturer_availability": copy.deepcopy(availability or {}),
    }


def _cls(**fields):
    """A class dict with the day-bearing fields the normalizer reads."""
    base = {
        "name": "C",
        "allowed_days": [],
        "excluded_days": [],
        "pinned": False,
        "pinned_day": None,
        "placed": False,
        "placed_day": None,
    }
    base.update(fields)
    return base


# ===========================================================================
# 1. THE SCALAR AND LIST HELPERS
# ===========================================================================
@pytest.mark.parametrize("raw,expected", [
    # already a key, in every casing a hand-edited file might carry
    ("monday", "monday"),
    ("MONDAY", "monday"),
    ("  Monday  ", "monday"),
    # English labels
    ("Saturday", "saturday"),
    # Turkish labels — the shipped default language, and the case that reaches
    # a real user through an Excel workbook written in Turkish
    ("Pazartesi", "monday"),
    ("PAZARTESI", "monday"),  # ASCII-I uppercase; the dotted-İ spelling is
                              # covered by the dotted/dotless-I test below
    ("Salı", "tuesday"),
    ("Çarşamba", "wednesday"),
    ("Perşembe", "thursday"),
    ("Cuma", "friday"),
    ("Cumartesi", "saturday"),
    ("Pazar", "sunday"),
    # other shipped locales, because the normalizer scans all of TRANSLATIONS
    ("Montag", "monday"),
    ("Lunes", "monday"),
    # not a weekday in any locale
    ("Blursday", None),
    ("", None),
    (None, None),
])
def test_a_day_written_any_way_a_user_might_write_it_becomes_one_key(raw, expected):
    """Pins ST-ARCH-001 item 9 — the label-to-key half.

    A failure means a lesson the user placed on ``Pazartesi`` and a lesson the
    engine placed on ``monday`` are two different days as far as every occupancy
    map in the app is concerned: the grid shows an empty Monday, the validator
    sees no clash, and the timetable double-books.
    """
    assert normalize_day_value(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    # Turkish, uppercased on a Turkish keyboard or by Excel's UPPER() in a tr
    # locale: 'i' becomes the dotted 'İ' and 'ı' becomes a plain ASCII 'I'.
    ("PAZARTESİ", "monday"),
    ("SALI", "tuesday"),
    ("CUMARTESİ", "saturday"),
    # Azerbaijani, the other shipped Turkic locale. These two need NO Turkish
    # keyboard: ordinary ASCII str.upper() turns the dotless 'ı' of 'axşamı'
    # into 'I', which casefold() then sends to a dotted ASCII 'i'.
    ("ÇƏRŞƏNBƏ AXŞAMI", "tuesday"),
    ("CÜMƏ AXŞAMI", "thursday"),
])
def test_a_turkish_day_typed_in_capitals_is_still_that_day(raw, expected):
    """Pins ST-ARCH-001 item 9 — the dotted/dotless-I half.

    Turkish uppercases ``ı`` to ``I`` and ``i`` to ``İ``. A user (or an Excel
    workbook with capitalised cells) writing ``SALI`` or ``PAZARTESİ`` means
    Tuesday and Monday; before the fix the app resolved both to ``None``, which
    the normalizer treats exactly like a day that is not on the grid — the
    allow-list entry is dropped, the placement un-placed, the pin released. And
    because ``normalize_state_day_keys`` runs from ``_auto_save``, the shrunken
    week is written back to disk on the next debounce tick: a Turkish week
    typed in capitals came back from open as three days instead of six.

    It is not "specifically the two letters" and not only Turkish, which is
    what this docstring used to claim. Measured on this tree: THREE Turkish
    labels break, not the two that were pinned, and two Azerbaijani ones break
    under plain ASCII ``.upper()`` on an English-locale machine with no Turkish
    keyboard anywhere in the story. Ordinary ASCII uppercase does already work
    (``PAZARTESI`` and ``SATURDAY`` are in the table above), so the axis is the
    dotted/dotless I specifically — which is exactly what
    ``scheduler_app.i18n.text_fold.fold_text`` folds, and nothing else.
    """
    assert normalize_day_value(raw) == expected


def test_the_same_day_spelled_two_ways_collapses_to_one_entry():
    """Pins ST-ARCH-001 item 9 — the de-duplication half.

    ``['Pazartesi', 'monday', 'MONDAY']`` is one day. Left as three, an
    allow-list looks three times larger than it is and the search space the
    optimizer reports to the user is a fiction.
    """
    assert normalize_day_list(["Pazartesi", "monday", "MONDAY", "Cuma"]) == [
        "monday", "friday"]


def test_the_first_spelling_wins_so_the_order_is_stable():
    """Guards the helper itself (no finding ID).

    Order is what the day columns of the grid are drawn from. If de-duplication
    reordered days, opening and re-saving a file would silently shuffle the
    week.
    """
    assert normalize_day_list(["Cuma", "monday", "Pazartesi"]) == [
        "friday", "monday"]
    assert normalize_day_list([]) == []
    assert normalize_day_list(None) == []


def test_the_seven_keys_are_the_seven_keys():
    """Guards the helper itself (no finding ID).

    Every assertion in this module, and every occupancy map in the engine, is
    indexed by these strings. A rename here is a silent data migration.
    """
    assert DAY_KEYS == ["monday", "tuesday", "wednesday", "thursday",
                        "friday", "saturday", "sunday"]


# ===========================================================================
# 2. THE STATE-WIDE NORMALIZER
# ===========================================================================
def test_opening_a_turkish_file_gives_the_engine_day_keys_not_day_labels():
    """Pins ST-ARCH-001 item 9 — the whole-state conversion.

    The measured no-op mutation (``return state`` at the top of the function)
    leaves ``days == ['Pazartesi', 'Salı', 'MONDAY']`` and every dependent field
    in its file-shaped form. Expectations below are literals, never computed by
    calling the function under test.
    """
    state = _state(
        ["Pazartesi", "Salı", "MONDAY"],
        [_cls(name="OnGrid", allowed_days=["Pazartesi", "pazartesi", "Cuma"],
              excluded_days=["Salı"], placed=True, placed_day="Pazartesi")],
        {"Ada": {"allowed_days": ["Pazartesi", "pazartesi", "Cuma"],
                 "excluded_days": ["Salı"]}},
    )

    normalize_state_day_keys(state)

    # 'MONDAY' is the same day as 'Pazartesi' — the grid has two days, not three.
    assert state["days"] == ["monday", "tuesday"]

    cls = state["classes"][0]
    # 'Cuma' is friday, which is not on this grid, so it is pruned; the two
    # spellings of monday collapse to one.
    assert cls["allowed_days"] == ["monday"]
    assert cls["excluded_days"] == ["tuesday"]
    assert cls["placed"] is True
    assert cls["placed_day"] == "monday"

    av = state["lecturer_availability"]["Ada"]
    assert av["allowed_days"] == ["monday"]
    assert av["excluded_days"] == ["tuesday"]


def test_a_lesson_left_on_a_day_the_grid_no_longer_has_is_unplaced():
    """Pins ST-ARCH-001 item 9 — the stale-placement half.

    A user drops Friday from the week in Setup. The Friday lesson's
    ``placed_day`` still says ``friday``. Left alone it is a lesson the grid
    cannot draw and the validator cannot audit: invisible to the user, present
    in the file, and counted as scheduled. The normalizer must unplace it, and
    must clear ``placed`` too — a ``placed=True`` class with ``placed_day=None``
    is the shape that raises deeper in the engine.
    """
    state = _state(
        ["monday", "tuesday"],
        [_cls(name="Stale", placed=True, placed_day="Cuma"),
         _cls(name="Fine", placed=True, placed_day="Salı")],
    )

    normalize_state_day_keys(state)

    stale, fine = state["classes"]
    assert stale["placed_day"] is None
    assert stale["placed"] is False, (
        "a lesson whose day left the grid is still flagged as placed; it will "
        "be counted as scheduled while appearing nowhere on the timetable")
    assert fine["placed_day"] == "tuesday"
    assert fine["placed"] is True


def test_a_pin_to_a_day_the_grid_no_longer_has_is_released():
    """Pins ST-ARCH-001 item 9 — the stale-pin half.

    A pin is a hard constraint: the optimizer refuses to move it and the oracle
    audits it. Pinned to a day that no longer exists, it is a constraint no
    placement can satisfy — the class simply never gets scheduled again, with no
    message saying why.
    """
    state = _state(
        ["monday", "tuesday"],
        [_cls(name="StalePin", pinned=True, pinned_day="Cumartesi"),
         _cls(name="LivePin", pinned=True, pinned_day="Pazartesi")],
    )

    normalize_state_day_keys(state)

    stale, live = state["classes"]
    assert stale["pinned_day"] is None
    assert stale["pinned"] is False, (
        "a pin to a day that is not on the grid was kept; the class it pins "
        "can never be placed and nothing tells the user")
    assert live["pinned_day"] == "monday"
    assert live["pinned"] is True


def test_normalization_is_idempotent():
    """Guards the function itself (no finding ID).

    ``_auto_save`` normalizes on the way out and ``_auto_load`` on the way back
    in, so every open/save cycle applies this twice. A second pass that changed
    anything would make a file drift a little further every time it was opened.
    """
    state = _state(
        ["Pazartesi", "Cuma"],
        [_cls(name="C", allowed_days=["Pazartesi"], placed=True,
              placed_day="Cuma")],
        {"Ada": {"allowed_days": ["Cuma"], "excluded_days": []}},
    )

    normalize_state_day_keys(state)
    once = copy.deepcopy(state)
    normalize_state_day_keys(state)

    assert state == once


def test_the_normalizer_edits_the_state_it_was_given():
    """Guards the function itself (no finding ID).

    Every production caller relies on the in-place contract: ``_auto_load`` and
    ``_auto_save`` discard the return value and carry on using the dict they
    passed in. A version that normalized a copy would be a no-op at every call
    site while passing any test that read the return value.
    """
    state = _state(["Pazartesi"], [_cls(allowed_days=["Pazartesi"])])

    returned = normalize_state_day_keys(state)

    assert returned is state
    assert state["days"] == ["monday"]
    assert state["classes"][0]["allowed_days"] == ["monday"]


def test_a_state_missing_every_optional_field_does_not_raise():
    """Guards the function itself (no finding ID).

    ``normalize_state_day_keys`` runs on files written by older versions and on
    the empty state a first run creates. A KeyError here is a crash on open.
    """
    state = {}
    normalize_state_day_keys(state)
    assert state["days"] == []
