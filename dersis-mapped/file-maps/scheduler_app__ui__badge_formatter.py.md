# File: `scheduler_app/ui/badge_formatter.py`

## 1. File Role
Single source of truth for protection-level + pinned badge display.

## 2. Why this file matters
Supporting. Without consistent badges, users couldn't see which classes are protected.

## 3. Imports and Dependencies
- Internal: `translations.tr`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `_BADGE_MAP` | `protection_level → (emoji, tr_key, color)`. Levels: `soft` (🛡️ amber), `same_day` (↔ blue), `improve_only` (↑ purple), `locked` (🔒 red). |
| `_PINNED_EMOJI = "📌"`, `_PINNED_COLOR = "#DC2626"` | Pinned overrides protection visually. |
| `get_badge(cls)` → `(emoji, label, color)` | Returns `(None, None, None)` if no badge applies. |
| `badge_text(cls)` → str | `"emoji label"` or `""`. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1 | docstring | |
| 3 | import | tr. |
| 5–13 | `_BADGE_MAP` | with hex colours and translation keys (`badges.protected`, `badges.same_day`, `badges.improve_only`, `badges.locked`). |
| 15–16 | pinned constants | |
| 19–29 | `get_badge` | pinned overrides; else lookup by `protection`. |
| 32–38 | `badge_text` | formats for display. |

## 6. Runtime Behavior
Pure helpers. Hot during cell painting.

## 7. Data Flow
- In: class dict.
- Out: tuple or string.

## 8. UI Flow
Renderer paints these; tooltips include badge text via `cell_formatter.tooltip_text`.

## 9. Error Handling and Edge Cases
- Unknown protection level → `(None, None, None)`.
- Pinned → `(_PINNED_EMOJI, tr("badges.pinned"), _PINNED_COLOR)` overrides protection.

## 10. Integration Points
`ui/renderer.py`, `ui/cell_formatter.py`, `data_io/exporter.py`.

## 11. Risks and Maintenance Notes
- New protection level: add to `_BADGE_MAP` + add translation key.
- Colour changes affect both the UI and Excel export.

## 12. Mini Summary
Source of truth for protection/pinned badge appearance. Tiny but central.
