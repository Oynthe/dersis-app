"""Unified cell content assembly for timetable display.

ST-ARCH-009: this module is deliberately NOT in `scheduler_app/i18n/`.
`tooltip_text` needs `core.logic.classroom_of`, so moving it there would
turn a core->ui violation into an i18n->core one and make the leaf package
part of a cycle. Its one dependency-free function, `plain_cell_text`, went
to its single caller in `data_io/exporter.py` instead.
"""

from scheduler_app.logic import classroom_of
from scheduler_app.translations import tr
from scheduler_app.i18n.badge_formatter import badge_text


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
