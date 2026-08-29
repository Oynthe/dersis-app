"""Weekday key helpers for UI translations and state normalization."""

from scheduler_app.i18n.text_fold import fold_text
from scheduler_app.translations import TRANSLATIONS, tr

DAY_KEYS = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]


def day_label(day_key):
    """Return translated label for a day key."""
    return tr(f"weekdays.{day_key}")


def display_day(value):
    """Return a translated display label for a stored day value."""
    day_key = normalize_day_value(value)
    if day_key:
        return day_label(day_key)
    return str(value or "")


def format_day_time(day_value, slot=None):
    """Return a localized day/time label such as 'Monday 09:00'."""
    day_text = display_day(day_value)
    if slot in (None, ""):
        return day_text
    if not day_text:
        return str(slot)
    return f"{day_text} {slot}"


def normalize_day_value(value):
    """Convert a stored/imported day value into a stable day key.

    Folded with `fold_text`, not `str.casefold`: casefold does not know the
    Turkish dotted/dotless I, so PAZARTESİ, SALI and CUMARTESİ -- and the
    Azerbaijani ÇƏRŞƏNBƏ AXŞAMI and CÜMƏ AXŞAMI, which break under plain ASCII
    .upper() -- all resolved to None and were then dropped by
    `normalize_state_day_keys` with no warning, on the autosave path that then
    writes the shrunken week back to disk. The seven keys are ASCII and fold to
    themselves, which `test_the_seven_day_keys_are_fold_stable` pins, so the
    fast path below stays correct.
    """
    text = str(value or "").strip()
    if not text:
        return None
    key = fold_text(text)
    if key in DAY_KEYS:
        return key

    for day_key in DAY_KEYS:
        if key == fold_text(day_label(day_key)):
            return day_key

    for lang_dict in TRANSLATIONS.values():
        for day_key in DAY_KEYS:
            if key == fold_text(str(lang_dict.get(f"weekdays.{day_key}", "")).strip()):
                return day_key
    return None


def normalize_day_list(values):
    """Normalize and de-duplicate day values while preserving first-seen order."""
    normalized = []
    seen = set()
    for value in values or []:
        day_key = normalize_day_value(value)
        if day_key and day_key not in seen:
            seen.add(day_key)
            normalized.append(day_key)
    return normalized


def normalize_state_day_keys(state):
    """Normalize day keys in state and dependent class/lecturer day fields in-place."""
    day_keys = normalize_day_list(state.get("days", []))
    state["days"] = day_keys
    allowed = set(day_keys)

    for cls in state.get("classes", []):
        cls["allowed_days"] = [d for d in normalize_day_list(cls.get("allowed_days", [])) if d in allowed]
        cls["excluded_days"] = [d for d in normalize_day_list(cls.get("excluded_days", [])) if d in allowed]

        if cls.get("pinned_day") is not None:
            pd = normalize_day_value(cls.get("pinned_day"))
            cls["pinned_day"] = pd if pd in allowed else None
            if cls.get("pinned") and cls["pinned_day"] is None:
                cls["pinned"] = False
        if cls.get("placed_day") is not None:
            pl = normalize_day_value(cls.get("placed_day"))
            cls["placed_day"] = pl if pl in allowed else None
            if cls.get("placed") and cls["placed_day"] is None:
                cls["placed"] = False

    # C1 -- an UNCONFIGURED week means "there is no grid to prune against", not
    # "no day is allowed". With `state["days"] == []`, `allowed` is the empty
    # set and this loop used to strip every day out of every availability
    # record; `_auto_save` (ui/app.py:2406) then wrote the emptied roster to
    # disk. Measured on a fresh schedule imported from the app's own template:
    # immediately after the import 'Prof.Emile Laurent' had
    # allowed_days=['Pazartesi', 'Çarşamba', 'Cuma'] and after 2.5 s of event
    # loop -- one autosave debounce, AUTOSAVE_DEBOUNCE_MS=1500 -- it had
    # allowed_days=[]. The hours survived, which is what made it quiet: the
    # Setup lecturer table still showed an availability record, just one that
    # no longer restricted a single day. The workbook has no days sheet
    # (data_io/schema.WORKBOOK_SHEETS), so importing before laying out the week
    # in Setup is a normal order of work, not a misuse.
    #
    # Scoped to AVAILABILITY on purpose. Leaving an off-grid key in
    # `allowed_days` is harmless because every reader intersects it with the
    # live day list (`apply_lecturer_availability_filters`,
    # core/models.py:529-548); an off-grid `placed_day` is the ST-DATA-004
    # orphan the class/pin half above exists to kill, and that half must keep
    # firing on an empty week.
    #
    # The guard is on the GRID being absent, not on the field. Written instead
    # as "never prune availability" it would break
    # `test_opening_a_turkish_file_gives_the_engine_day_keys_not_day_labels`
    # (tests/test_day_key_normalization.py), which pins the pruning against a
    # non-empty week -- and that pruning is wanted: with a real grid, an
    # off-grid day in `allowed_days` is a day the user deleted from the week.
    keep_off_grid = not day_keys
    for _, av in state.get("lecturer_availability", {}).items():
        av["allowed_days"] = [d for d in normalize_day_list(av.get("allowed_days", []))
                              if keep_off_grid or d in allowed]
        av["excluded_days"] = [d for d in normalize_day_list(av.get("excluded_days", []))
                               if keep_off_grid or d in allowed]

    return state
