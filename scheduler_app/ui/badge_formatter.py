"""Single source of truth for protection-level badge display."""

from scheduler_app.translations import tr

# Badge definitions: (emoji, tr_key, hex_color)
# Colors align with existing usage across renderer.py and exporter.py.
_BADGE_MAP = {
    "soft":         ("\U0001F6E1", "badges.protected",    "#D97706"),
    "same_day":     ("\u2194",     "badges.same_day",     "#2563EB"),
    "improve_only": ("\u2191",     "badges.improve_only", "#7C3AED"),
    "locked":       ("\U0001F512", "badges.locked",       "#DC2626"),
}

_PINNED_EMOJI = "\U0001F4CC"
_PINNED_COLOR = "#DC2626"


def get_badge(cls):
    """Return (emoji, label, color_hex) for a class's protection/pinned state.

    Returns (None, None, None) for unprotected classes.
    """
    if cls.get("pinned"):
        return _PINNED_EMOJI, tr("badges.pinned"), _PINNED_COLOR
    prot = cls.get("protection", "none")
    entry = _BADGE_MAP.get(prot)
    if entry:
        emoji, tr_key, color = entry
        return emoji, tr(tr_key), color
    return None, None, None


def badge_text(cls):
    """Return 'emoji label' string for tooltip/display, or ''."""
    emoji, label, _ = get_badge(cls)
    if emoji and label:
        return f"{emoji} {label}"
    return ""
