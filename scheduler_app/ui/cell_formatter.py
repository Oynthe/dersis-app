"""Unified cell content assembly for timetable display and export."""

from scheduler_app.logic import classroom_of
from scheduler_app.translations import tr
from scheduler_app.ui.badge_formatter import badge_text


def tooltip_text(cls, include_groups=True, include_duration=True):
    """Return full tooltip string for a timetable cell."""
    parts = []
    code = cls.get("class_code", "")
    if code:
        parts.append(f"{tr('dialogs.add_class.code')}: {code}")
    parts.extend([cls["name"], f"{tr('labels.lecturer')}: {cls['lecturer']}"])
    if include_groups:
        groups = ", ".join(
            f"{t['year']}/{t['branch']}" for t in cls.get("targets", []))
        if groups:
            parts.append(
                f"{tr('dialogs.add_class.target_groups').rstrip(':')} {groups}")
    if include_duration and cls.get("duration", 1) > 1:
        parts.append(
            f"{tr('labels.duration')}: {cls['duration']} {tr('labels.slots')}")
    room = classroom_of(cls)
    if room:
        parts.append(f"{tr('labels.classroom')}: {room}")
    bt = badge_text(cls)
    if bt:
        parts.append(f"[{bt}]")
    return "\n".join(parts)


def plain_cell_text(entry):
    """Return plain single-line text for a schedule entry (CSV/clipboard).

    Expects an entry dict with keys: name, lecturer, room, class_code.
    """
    parts = []
    code = entry.get("class_code", "")
    if code:
        parts.append(code)
    parts.append(entry["name"])
    if entry["lecturer"]:
        parts.append(entry["lecturer"])
    if entry["room"]:
        parts.append(f"[{entry['room']}]")
    return "\n".join(parts)
