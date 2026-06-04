"""Centralized tier/plan configuration for DERSIS scheduling application.

This module is the single source of truth for all tier limits, feature flags,
and pricing information. All enforcement logic should reference this module
rather than hardcoding tier-specific values.
"""

# ---------------------------------------------------------------------------
# Tier slug constants
# ---------------------------------------------------------------------------

TIER_FREE = "free"
TIER_STARTER = "starter"
TIER_PROFESSIONAL = "professional"
TIER_MAX = "max"
TIER_INSTITUTIONAL = "institutional"

# Ordered from lowest to highest tier
TIER_ORDER: list[str] = [
    TIER_FREE,
    TIER_STARTER,
    TIER_PROFESSIONAL,
    TIER_MAX,
    TIER_INSTITUTIONAL,
]

UNLIMITED = -1

# ---------------------------------------------------------------------------
# Entity type constants (used as keys in limits dicts)
# ---------------------------------------------------------------------------

ENTITY_SCHEDULES = "max_schedules"
ENTITY_CLASSES = "max_classes"
ENTITY_CLASSROOMS = "max_classrooms"
ENTITY_LECTURERS = "max_lecturers"
ENTITY_YEARS = "max_years"
ENTITY_DEVICES = "max_devices"

# ---------------------------------------------------------------------------
# Feature flag constants
# ---------------------------------------------------------------------------

FEATURE_EXPORT_PDF = "export_pdf"
FEATURE_EXPORT_EXCEL = "export_excel"
FEATURE_EXPORT_CSV = "export_csv"
FEATURE_BULK_SCHEDULING = "bulk_scheduling"
FEATURE_AUTO_SCHEDULING = "auto_scheduling"
FEATURE_OPTIMIZATION = "optimization"
FEATURE_AI_EXPLANATIONS = "ai_explanations"
FEATURE_AUTO_NEGOTIATION = "auto_negotiation"
FEATURE_REVISION_HISTORY = "revision_history"
FEATURE_ADVANCED_ANALYTICS = "advanced_analytics"
FEATURE_MULTI_DEPARTMENT = "multi_department"
FEATURE_API_ACCESS = "api_access"
FEATURE_SSO = "sso"
FEATURE_CUSTOM_BRANDING = "custom_branding"

# ---------------------------------------------------------------------------
# Plan definitions
# ---------------------------------------------------------------------------

PLANS: dict[str, dict] = {
    TIER_FREE: {
        "name": "Free",
        "tier_slug": TIER_FREE,
        "limits": {
            ENTITY_SCHEDULES: 1,
            ENTITY_CLASSES: 10,
            ENTITY_CLASSROOMS: 1,
            ENTITY_LECTURERS: 1,
            ENTITY_YEARS: 1,
            ENTITY_DEVICES: 1,
        },
        "features": {
            FEATURE_EXPORT_PDF: False,
            FEATURE_EXPORT_EXCEL: False,
            FEATURE_EXPORT_CSV: False,
            FEATURE_BULK_SCHEDULING: False,
            FEATURE_AUTO_SCHEDULING: False,
            FEATURE_OPTIMIZATION: False,
            FEATURE_AI_EXPLANATIONS: False,
            FEATURE_AUTO_NEGOTIATION: False,
            FEATURE_REVISION_HISTORY: False,
            FEATURE_ADVANCED_ANALYTICS: False,
            FEATURE_MULTI_DEPARTMENT: False,
            FEATURE_API_ACCESS: False,
            FEATURE_SSO: False,
            FEATURE_CUSTOM_BRANDING: False,
        },
        "price_monthly": 0,
        "price_yearly": 0,
    },
    TIER_STARTER: {
        "name": "Starter",
        "tier_slug": TIER_STARTER,
        "limits": {
            ENTITY_SCHEDULES: 3,
            ENTITY_CLASSES: 40,
            ENTITY_CLASSROOMS: 8,
            ENTITY_LECTURERS: 8,
            ENTITY_YEARS: 3,
            ENTITY_DEVICES: 1,
        },
        "features": {
            FEATURE_EXPORT_PDF: True,
            FEATURE_EXPORT_EXCEL: False,
            FEATURE_EXPORT_CSV: True,
            FEATURE_BULK_SCHEDULING: True,
            FEATURE_AUTO_SCHEDULING: True,
            FEATURE_OPTIMIZATION: False,
            FEATURE_AI_EXPLANATIONS: False,
            FEATURE_AUTO_NEGOTIATION: False,
            FEATURE_REVISION_HISTORY: False,
            FEATURE_ADVANCED_ANALYTICS: False,
            FEATURE_MULTI_DEPARTMENT: False,
            FEATURE_API_ACCESS: False,
            FEATURE_SSO: False,
            FEATURE_CUSTOM_BRANDING: False,
        },
        "price_monthly": 9,
        "price_yearly": 86,
    },
    TIER_PROFESSIONAL: {
        "name": "Professional",
        "tier_slug": TIER_PROFESSIONAL,
        "limits": {
            ENTITY_SCHEDULES: 15,
            ENTITY_CLASSES: 200,
            ENTITY_CLASSROOMS: 50,
            ENTITY_LECTURERS: 50,
            ENTITY_YEARS: 15,
            ENTITY_DEVICES: 2,
        },
        "features": {
            FEATURE_EXPORT_PDF: True,
            FEATURE_EXPORT_EXCEL: True,
            FEATURE_EXPORT_CSV: True,
            FEATURE_BULK_SCHEDULING: True,
            FEATURE_AUTO_SCHEDULING: True,
            FEATURE_OPTIMIZATION: True,
            FEATURE_AI_EXPLANATIONS: True,
            FEATURE_AUTO_NEGOTIATION: True,
            FEATURE_REVISION_HISTORY: True,
            FEATURE_ADVANCED_ANALYTICS: True,
            FEATURE_MULTI_DEPARTMENT: False,
            FEATURE_API_ACCESS: False,
            FEATURE_SSO: False,
            FEATURE_CUSTOM_BRANDING: False,
        },
        "price_monthly": 19,
        "price_yearly": 182,
    },
    TIER_MAX: {
        "name": "Max",
        "tier_slug": TIER_MAX,
        "limits": {
            ENTITY_SCHEDULES: 50,
            ENTITY_CLASSES: 500,
            ENTITY_CLASSROOMS: 150,
            ENTITY_LECTURERS: 150,
            ENTITY_YEARS: 40,
            ENTITY_DEVICES: 3,
        },
        "features": {
            FEATURE_EXPORT_PDF: True,
            FEATURE_EXPORT_EXCEL: True,
            FEATURE_EXPORT_CSV: True,
            FEATURE_BULK_SCHEDULING: True,
            FEATURE_AUTO_SCHEDULING: True,
            FEATURE_OPTIMIZATION: True,
            FEATURE_AI_EXPLANATIONS: True,
            FEATURE_AUTO_NEGOTIATION: True,
            FEATURE_REVISION_HISTORY: True,
            FEATURE_ADVANCED_ANALYTICS: True,
            FEATURE_MULTI_DEPARTMENT: True,
            FEATURE_API_ACCESS: False,
            FEATURE_SSO: False,
            FEATURE_CUSTOM_BRANDING: False,
        },
        "price_monthly": 39,
        "price_yearly": 374,
    },
    TIER_INSTITUTIONAL: {
        "name": "Institutional",
        "tier_slug": TIER_INSTITUTIONAL,
        "limits": {
            ENTITY_SCHEDULES: UNLIMITED,
            ENTITY_CLASSES: UNLIMITED,
            ENTITY_CLASSROOMS: UNLIMITED,
            ENTITY_LECTURERS: UNLIMITED,
            ENTITY_YEARS: UNLIMITED,
            ENTITY_DEVICES: 5,
        },
        "features": {
            FEATURE_EXPORT_PDF: True,
            FEATURE_EXPORT_EXCEL: True,
            FEATURE_EXPORT_CSV: True,
            FEATURE_BULK_SCHEDULING: True,
            FEATURE_AUTO_SCHEDULING: True,
            FEATURE_OPTIMIZATION: True,
            FEATURE_AI_EXPLANATIONS: True,
            FEATURE_AUTO_NEGOTIATION: True,
            FEATURE_REVISION_HISTORY: True,
            FEATURE_ADVANCED_ANALYTICS: True,
            FEATURE_MULTI_DEPARTMENT: True,
            FEATURE_API_ACCESS: True,
            FEATURE_SSO: True,
            FEATURE_CUSTOM_BRANDING: True,
        },
        "price_monthly": 0,  # Custom pricing — contact sales
        "price_yearly": 0,
    },
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def get_plan(tier_slug: str) -> dict:
    """Return the plan configuration for *tier_slug*.

    Falls back to the Free plan when the slug is unrecognised.
    """
    return PLANS.get(tier_slug, PLANS[TIER_FREE])


def get_limit(tier_slug: str, limit_name: str) -> int:
    """Return the numeric limit for *limit_name* on the given tier.

    Returns ``-1`` (unlimited) if the limit name is not found in the plan.
    Defaults to the Free tier when *tier_slug* is unrecognised.
    """
    plan = get_plan(tier_slug)
    return plan["limits"].get(limit_name, UNLIMITED)


def has_feature(tier_slug: str, feature_name: str) -> bool:
    """Return whether *feature_name* is enabled for *tier_slug*.

    Returns ``False`` when the feature or tier is unrecognised.
    """
    plan = get_plan(tier_slug)
    return plan["features"].get(feature_name, False)


def check_entity_limit(
    tier_slug: str,
    entity_type: str,
    current_count: int,
) -> tuple[bool, int]:
    """Check whether adding one more entity of *entity_type* is allowed.

    Parameters
    ----------
    tier_slug:
        The user's current tier slug.
    entity_type:
        One of the ``ENTITY_*`` constants (e.g. ``"max_classes"``).
    current_count:
        How many entities of this type already exist.

    Returns
    -------
    tuple[bool, int]
        ``(allowed, limit)`` where *allowed* is ``True`` when the user
        can add another entity, and *limit* is the tier's cap (``-1``
        means unlimited).
    """
    limit = get_limit(tier_slug, entity_type)
    if limit == UNLIMITED:
        return True, limit
    return current_count < limit, limit


def get_upgrade_tier(tier_slug: str, feature_name: str) -> str | None:
    """Return the slug of the lowest tier that unlocks *feature_name*.

    If the feature is already available on the current tier, returns
    ``None``.  If no tier provides the feature, also returns ``None``.
    """
    current_index = (
        TIER_ORDER.index(tier_slug) if tier_slug in TIER_ORDER else 0
    )

    # If the user already has the feature, nothing to upgrade to.
    if has_feature(tier_slug, feature_name):
        return None

    for slug in TIER_ORDER[current_index + 1 :]:
        if has_feature(slug, feature_name):
            return slug

    return None


def get_required_tier_for_limit(
    entity_type: str,
    needed_count: int,
) -> str | None:
    """Return the lowest tier that supports at least *needed_count* of *entity_type*.

    Returns ``None`` if no tier can accommodate the requested count (should
    not happen in practice since Institutional is unlimited for most
    entity types).
    """
    for slug in TIER_ORDER:
        limit = get_limit(slug, entity_type)
        if limit == UNLIMITED or limit >= needed_count:
            return slug
    return None


# ---------------------------------------------------------------------------
# Feature state resolver — single source of truth for UI gating
# ---------------------------------------------------------------------------

# Tooltip templates: benefit-focused, max ~12 words, positive tone.
# Keys map feature flags to a short upgrade-benefit phrase.
_FEATURE_TOOLTIPS: dict[str, str] = {
    FEATURE_EXPORT_PDF: "upgrade.tooltip.export_pdf",
    FEATURE_EXPORT_EXCEL: "upgrade.tooltip.export_excel",
    FEATURE_EXPORT_CSV: "upgrade.tooltip.export_csv",
    FEATURE_BULK_SCHEDULING: "upgrade.tooltip.bulk_scheduling",
    FEATURE_AUTO_SCHEDULING: "upgrade.tooltip.auto_scheduling",
    FEATURE_OPTIMIZATION: "upgrade.tooltip.optimization",
    FEATURE_AI_EXPLANATIONS: "upgrade.tooltip.ai_explanations",
    FEATURE_AUTO_NEGOTIATION: "upgrade.tooltip.auto_negotiation",
    FEATURE_REVISION_HISTORY: "upgrade.tooltip.revision_history",
    FEATURE_ADVANCED_ANALYTICS: "upgrade.tooltip.advanced_analytics",
    FEATURE_MULTI_DEPARTMENT: "upgrade.tooltip.multi_department",
    FEATURE_API_ACCESS: "upgrade.tooltip.api_access",
    FEATURE_SSO: "upgrade.tooltip.sso",
    FEATURE_CUSTOM_BRANDING: "upgrade.tooltip.custom_branding",
}

# Limit tooltip key (used with .format(limit=N))
_ENTITY_TOOLTIPS: dict[str, str] = {
    ENTITY_SCHEDULES: "upgrade.tooltip.limit_schedules",
    ENTITY_CLASSES: "upgrade.tooltip.limit_classes",
    ENTITY_CLASSROOMS: "upgrade.tooltip.limit_classrooms",
    ENTITY_LECTURERS: "upgrade.tooltip.limit_lecturers",
    ENTITY_YEARS: "upgrade.tooltip.limit_years",
    ENTITY_DEVICES: "upgrade.tooltip.limit_devices",
}


class FeatureState:
    """Result of :func:`get_feature_state` — describes a feature's
    availability and upgrade messaging for the current tier."""

    __slots__ = ("enabled", "required_plan", "reason", "tooltip_message")

    def __init__(
        self,
        enabled: bool,
        required_plan: str,
        reason: str,
        tooltip_message: str,
    ) -> None:
        self.enabled = enabled
        self.required_plan = required_plan
        self.reason = reason
        self.tooltip_message = tooltip_message


def get_feature_state(tier_slug: str, feature_name: str) -> FeatureState:
    """Return the full UI state for *feature_name* on the user's tier.

    This is the **single entry point** that every UI element should use to
    decide enabled/disabled, tooltip text, and upgrade messaging.
    """
    if has_feature(tier_slug, feature_name):
        return FeatureState(
            enabled=True,
            required_plan="",
            reason="",
            tooltip_message="",
        )

    upgrade_slug = get_upgrade_tier(tier_slug, feature_name)
    upgrade_name = get_plan(upgrade_slug)["name"] if upgrade_slug else "?"

    # Build tooltip via translation key; caller must call tr() on it.
    tooltip_key = _FEATURE_TOOLTIPS.get(feature_name, "")

    return FeatureState(
        enabled=False,
        required_plan=upgrade_name,
        reason="feature_locked",
        tooltip_message=tooltip_key,
    )


def get_limit_state(
    tier_slug: str, entity_type: str, current_count: int
) -> FeatureState:
    """Return the UI state for an entity-limit check.

    Similar to :func:`get_feature_state` but for numeric limits.
    """
    allowed, limit = check_entity_limit(tier_slug, entity_type, current_count)
    if allowed:
        return FeatureState(
            enabled=True, required_plan="", reason="", tooltip_message=""
        )

    needed = current_count + 1
    upgrade_slug = get_required_tier_for_limit(entity_type, needed)
    upgrade_name = get_plan(upgrade_slug)["name"] if upgrade_slug else "?"

    tooltip_key = _ENTITY_TOOLTIPS.get(entity_type, "")

    return FeatureState(
        enabled=False,
        required_plan=upgrade_name,
        reason="limit_reached",
        tooltip_message=tooltip_key,
    )
