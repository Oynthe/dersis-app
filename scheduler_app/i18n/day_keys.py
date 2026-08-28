"""Weekday key helpers for UI translations and state normalization."""

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
    """Convert a stored/imported day value into a stable day key."""
    text = str(value or "").strip()
    if not text:
        return None
    key = text.casefold()
    if key in DAY_KEYS:
        return key

    for day_key in DAY_KEYS:
        if key == day_label(day_key).casefold():
            return day_key

    for lang_dict in TRANSLATIONS.values():
        for day_key in DAY_KEYS:
            if key == str(lang_dict.get(f"weekdays.{day_key}", "")).strip().casefold():
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

    for _, av in state.get("lecturer_availability", {}).items():
        av["allowed_days"] = [d for d in normalize_day_list(av.get("allowed_days", [])) if d in allowed]
        av["excluded_days"] = [d for d in normalize_day_list(av.get("excluded_days", [])) if d in allowed]

    return state
