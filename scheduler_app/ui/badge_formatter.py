"""Single source of truth for protection-level badge display."""

from scheduler_app.translations import tr

# Badge definitions: (emoji, tr_key, hex_color)
# Colors align with existing usage across renderer.py and exporter.py.
#
# ST-UI-005. This module is the single source for badge colour \u2014 renderer.py,
# exporter.py's XLSX ``_rich_cell`` and its PDF ``_pdf_rich_markup`` all reach
# it through ``get_badge`` \u2014 so darkening here fixes the screen and both exports
# at once. Every value clears WCAG AA (4.5:1) against the darkest cell tint
# ``#f69898``; the old ones ranged 1.50:1 (soft) to 2.68:1 (improve_only), the
# worst contrast anywhere in the product.
#
# Two of these are chosen for *separation* as well as contrast. The badge shares
# a cell with body text of the same hue family, and the pairs were already below
# the just-noticeable difference for small text before this change:
#
#   same_day     vs class code    (constants.CELL_FG_CODE)    dE76 9.2 -> 21.8
#   improve_only vs branch letter (constants.CELL_FG_BRANCH)  dE76 6.2 -> 20.6
#
# Both pairs really do co-occur: any class with ``same_day`` shows the badge
# beside its code, and a sequential class with ``improve_only`` shows the badge
# beside its branch letter. Taking the darkest *passing* member of each ramp
# (#1E3A8A / #4C1D95) would have made them literally identical, so the badges
# step one further to the 950 shade instead.
_BADGE_MAP = {
    "soft":         ("\U0001F6E1", "badges.protected",    "#451A03"),
    "same_day":     ("\u2194",     "badges.same_day",     "#172554"),
    "improve_only": ("\u2191",     "badges.improve_only", "#2E1065"),
    "locked":       ("\U0001F512", "badges.locked",       "#7F1D1D"),
}

_PINNED_EMOJI = "\U0001F4CC"
_PINNED_COLOR = "#7F1D1D"


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
