# File: `scheduler_app/ui/tier_translations.py`

## 1. File Role
Translation keys specific to the tier system (`upgrade.dialog.*`, `upgrade.feature.*`, `upgrade.entity.*`, `upgrade.tooltip.*`). Merges into the global `TRANSLATIONS` dict on import.

## 2. Why this file matters
Supporting. Without it, the upgrade dialog and gated-button tooltips wouldn't have text.

## 3. Imports and Dependencies
- Internal: `translations.TRANSLATIONS`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `_TIER_TRANSLATIONS` | Per-language nested dict. |
| Side-effect merge into `TRANSLATIONS[lang][key] = value` at import time. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–6 | docstring | Explains key convention. |
| 8 | import | `TRANSLATIONS`. |
| 10–~270 | `_TIER_TRANSLATIONS` | English block + per-language blocks. |
| ~270–341 | merge loop | `for lang, kv in _TIER_TRANSLATIONS.items(): TRANSLATIONS.setdefault(lang, {}).update(kv)`. |

## 6. Runtime Behavior
Side-effect on import. Triggered by `from scheduler_app.ui.tier_translations import ...` (or `# noqa: F401` import in `tier_enforcement.py`).

## 7. Data Flow
- In: nothing.
- Out: mutated `TRANSLATIONS`.

## 8. UI Flow
Not applicable directly; consumed by `UpgradeDialog` and `gate_menu_action`.

## 9. Error Handling and Edge Cases
- Languages not in the main `TRANSLATIONS` dict: `setdefault` creates the entry.
- Existing keys are overwritten by tier translations — by design (single source for `upgrade.*`).

## 10. Integration Points
- Consumed by `ui/tier_enforcement.py`.

## 11. Risks and Maintenance Notes
- Adding a feature/entity: add an `upgrade.feature.X` / `upgrade.entity.X` / `upgrade.tooltip.X` key per language.
- The merge happens at import time — late imports of this module work, but cycles should be avoided.

## 12. Mini Summary
Side-effect merge of tier-related translation keys. Imported by `tier_enforcement` to ensure keys are loaded.
