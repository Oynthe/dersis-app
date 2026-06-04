# File: `scheduler_app/ui/tier_enforcement.py`

## 1. File Role
Tier feature gating + (now-dormant) upgrade UI. Provides `TierEnforcement` (singleton), `FeatureGateWidget`, `UpgradeDialog`, `gate_menu_action`. In the offline build the default tier is **institutional**, so every feature is unlocked and the gates always allow — the upgrade dialog/button never appear.

## 2. Why this file matters
It is still the single runtime source of truth for "is this feature available?". Offline, the answer is always yes, but the call sites (`ui/app.py`, `ui/dialogs.py`) still route through it, so it must keep returning "allowed".

## 3. Imports and Dependencies
- stdlib: `webbrowser`, `typing.Callable`.
- Third-party: PyQt6 widgets/gui.
- Internal: `translations.tr`, `ui.tier_translations` (side-effect import that registers `upgrade.*` keys), `plans.*` (helpers + `FeatureState`, `TIER_INSTITUTIONAL`).

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `PRICING_PAGE_URL` | Now an empty string (the remote pricing URL was removed). Any open is guarded, so it is a no-op. |
| `TierEnforcement` (singleton) | `.instance()`, `.set_tier(slug)`, `.tier_slug`, signal `tier_changed(str)`. Defaults to `TIER_INSTITUTIONAL`. `can_use_feature` / `require_feature` / `require_entity_limit` therefore always allow. |
| `FeatureGateWidget` | Wraps a QWidget; reacts to `tier_changed`. At the institutional tier it never disables anything. |
| `UpgradeDialog` | The upgrade prompt. Never shown offline; its CTA's `webbrowser.open` is guarded by `if PRICING_PAGE_URL:`. |
| `gate_menu_action(action, feature)` | Still wraps gated actions; at the institutional tier they stay enabled. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–~55 | docstring + imports + `PRICING_PAGE_URL = ""` | |
| ~75–~260 | `TierEnforcement` singleton | default tier `TIER_INSTITUTIONAL`; stores tier; emits signal; query/require helpers. |
| ~310–~395 | gate helpers (`gate_menu_action`, `gate_export_submenu`, tooltip builder) | |
| ~400–~610 | `UpgradeDialog` | layout + guarded `_open_pricing`. |
| ~620–730 | `FeatureGateWidget` + helpers | |

## 6. Runtime Behavior
The singleton is created on first `.instance()` call and already defaults to institutional. `scheduler_gui.main()` additionally calls `set_tier(TIER_INSTITUTIONAL)` once at startup. There is no heartbeat or server tier refresh.

## 7. Data Flow
- In: tier slug (always institutional offline).
- Out: `tier_changed` signal; gated widgets refresh (and stay enabled).

## 8. UI Flow
- Offline: nothing is gated; the toolbar upgrade button and `_upgrade_banner` stay hidden; `UpgradeDialog` never opens.

## 9. Error Handling and Edge Cases
- Unknown feature key → treated as available (no-op).
- `_open_pricing` is guarded against an empty `PRICING_PAGE_URL`.
- Tier slug not in `PLANS` → falls back to Free, but offline it is always set to institutional.

## 10. Integration Points
- `scheduler_gui.main` calls `TierEnforcement.instance().set_tier(TIER_INSTITUTIONAL)` at startup.
- `ui/app.py` and `ui/dialogs.py` route feature/limit checks through `require_feature` / `require_entity_limit` / `gate_menu_action` (all always-allow offline).

## 11. Risks and Maintenance Notes
- The gating machinery is retained (not deleted) so the many call sites keep working; if you ever re-introduce paid tiers, this is where to wire it.
- A new feature flag still needs a tooltip key in `plans.py` and a translation in `ui/tier_translations.py`.

## 12. Mini Summary
Tier gating singleton + dormant upgrade UI. Offline it defaults to the institutional tier, so every feature is unlocked and the upgrade dialog never shows; the API is kept intact for the existing call sites.
