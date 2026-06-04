# File: `scheduler_app/plans.py`

## 1. File Role
Single source of truth for the **tier system**: tier slugs, entity limits, feature flags, prices, tier-comparison helpers, and the `FeatureState` resolver that drives UI gating tooltips.

## 2. Why this file matters
**Critical.** Every UI gate consults this module. Changing limits or adding features means editing this file (and its translations).

## 3. Imports and Dependencies
No stdlib or third-party imports — pure constants + helpers. Consumed by `ui/tier_enforcement.py`. Translations referenced via key strings only (looked up by callers via `tr()`).

## 4. Main Symbols
| Symbol | Lines | Purpose |
|--------|-------|---------|
| Tier slug constants (`TIER_FREE`, `TIER_STARTER`, `TIER_PROFESSIONAL`, `TIER_MAX`, `TIER_INSTITUTIONAL`) | 12–16 | Stable identifiers. |
| `TIER_ORDER` | 19–25 | Ordered list low → high. |
| `UNLIMITED = -1` | 27 | Sentinel for unbounded limits. |
| `ENTITY_*` constants | 33–38 | Limit-name keys (`max_schedules`, `max_classes`, etc.). |
| `FEATURE_*` constants | 44–57 | Feature flag keys. |
| `PLANS` | 63–214 | Master dict: tier_slug → {name, tier_slug, limits, features, price_monthly, price_yearly}. |
| `get_plan(tier_slug)` | 222–227 | Returns plan dict; falls back to Free. |
| `get_limit(tier_slug, limit_name)` | 230–237 | Returns numeric limit; UNLIMITED if missing. |
| `has_feature(tier_slug, feature_name)` | 240–246 | Bool. |
| `check_entity_limit(tier_slug, entity_type, current_count)` | 249–275 | (allowed, limit). |
| `get_upgrade_tier(tier_slug, feature_name)` | 278–296 | Lowest tier above current that unlocks feature. |
| `get_required_tier_for_limit(entity_type, needed_count)` | 299–313 | Lowest tier supporting the needed count. |
| `_FEATURE_TOOLTIPS` / `_ENTITY_TOOLTIPS` | 322–347 | Translation-key maps for tooltips. |
| `FeatureState` (dataclass-like with `__slots__`) | 350–366 | Result struct: `enabled`, `required_plan`, `reason`, `tooltip_message`. |
| `get_feature_state(tier_slug, feature_name)` | 369–394 | Resolves the UI state for a feature on a tier. |
| `get_limit_state(tier_slug, entity_type, current_count)` | 397–421 | Resolves UI state for a limit check. |

## 5. Block-by-block code map
| Lines | Block | What | Why |
|-------|-------|------|-----|
| 1–7 | docstring | Explains responsibility. | Onboarding. |
| 9–27 | tier slugs, order, UNLIMITED | Stable constants. | Used as keys throughout. |
| 29–57 | entity + feature constants | More stable keys. | Avoids stringly-typed bugs. |
| 60–214 | `PLANS` dict | Authoritative tier configuration. | The data the helpers read. |
| 217–313 | helper functions | Read-only queries. | The public API consumed by UI gating. |
| 318–347 | tooltip maps | Translation-key registries. | Single source for upgrade tooltips. |
| 350–394 | `FeatureState` + `get_feature_state` | UI gating resolver. | Drives `FeatureGateWidget`. |
| 397–421 | `get_limit_state` | UI gating resolver for limits. | Drives entity-count gates. |

## 6. Runtime Behavior
Stateless. Read at any point. `FeatureState` is constructed fresh on each call.

## 7. Data Flow
Inputs: tier_slug (current user), feature/entity key, current_count. Outputs: bool/integer/`FeatureState`.

## 8. UI Flow
Not applicable directly, but `FeatureState` is consumed by `ui/tier_enforcement.py`. Tooltip messages are translation keys requiring `tr(...)` at the call site.

## 9. Error Handling and Edge Cases
- Unknown tier_slug → falls back to Free.
- Unknown feature_name → `has_feature` returns False.
- Unknown entity_type → `check_entity_limit` returns `UNLIMITED`.
- `get_upgrade_tier` returns None if already enabled or no tier offers the feature.
- Institutional pricing is 0 because of "contact sales" handling — UI distinguishes via `upgrade.dialog.plan_contact` key.

## 10. Integration Points
Consumed by `ui/tier_enforcement.py` (singleton + dialog), `scheduler_gui.py` (applies the institutional tier at startup), and the call sites that gate a feature or entity addition (`ui/app.py`, `ui/dialogs.py`). Offline, every plan check resolves to "allowed".

## 11. Risks and Maintenance Notes
- Changing the data shape of `PLANS` would require updating every helper. Use the helpers, never reach inside the dict directly.
- Adding a new feature flag: add the `FEATURE_*` constant, set its bool on every tier, add a tooltip key, add the upgrade dialog text in `ui/tier_translations.py`.
- Prices in this file are advisory only — the live price is what the server says at checkout time.

## 12. Mini Summary
The catalog of tiers, limits, features, and prices for the licensing system. Helpers (`has_feature`, `check_entity_limit`, `get_feature_state`, `get_limit_state`) drive UI gating. The file is the **only place** to edit tier behaviour.
